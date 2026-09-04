# DESIGN_PHASE3.md - Agent Reasoning / Explanation Layer

Scope: Explain the 28 non-trivial cases identified by the Phase 2 matcher.
Does NOT re-classify, override, or re-decide any match status.
Does NOT touch the 474 plain-matched orders or 89 batch_credited settlements.

---

## 1. Scope: Exactly 28 Cases

| # | entity_type | case_id | confidence | exception_code | count |
|---|-------------|---------|------------|----------------|-------|
| 1-3 | order | REFUND_SPLIT cases (3) | matched_with_note | REFUND_SPLIT | 3 |
| 4-5 | order | CURRENCY_MISMATCH cases (2) | matched_with_note | CURRENCY_MISMATCH | 2 |
| 6 | order | ord_EnDJiS9HvlxNgbb1 | needs_review | DUPLICATE_ORDER | 1 |
| 7-18 | order | 12 UNRECORDED_REFUND cases | needs_review | UNRECORDED_REFUND | 12 |
| 19-26 | order | 8 UNMATCHED_ORDER cases (5 failed + 2 authorized + 1 missing from settlement) | hard_exception | UNMATCHED_ORDER | 8 |
| 27 | settlement | set_1E8lJ4dKfU21o9Is | needs_review | GHOST_TRANSACTION | 1 |
| 28 | settlement | set_7oqQnmBR7evr0ci5 | hard_exception | NEFT_FAILED | 1 |

Agent involvement: ZERO for the other 474 + 89 = 563 cases.

**Scope note:** the original Phase 3 design covered 17 cases (this table plus NO_CREDIT_EXPECTED). Post-build fixes to Phase 2 changed the scope: Fix 4 reclassified the negative-net NO_CREDIT_EXPECTED batch to `matched` (no longer narrated), and Fix 3 added the 12 UNRECORDED_REFUND exceptions (now narrated) -- see the addendum in DESIGN_PHASE2.md and audit_trail.md. Net result: 16 + 12 = **28 narrated cases**.

---

## 2. Core Principle: Narrate, Do NOT Re-Decide

For all 28 cases, the Phase 2 classification is **final and correct** against ground truth.
The agent job is to:

1. **Explain WHY** in plain English, using actual computed facts from the case
2. **Never re-classify** or override the status
3. **Express uncertainty** where the data is genuinely ambiguous -- a formal `confidence_note` is attached only to DUPLICATE_ORDER (the one case where two conflicting amounts make the correct value unknowable)
4. **Suggest actions** (advisory only, never auto-executed)

The agent is a narrator with a reference card, not a decision-maker.

---

## 3. Grounding Strategy: Domain Facts Reference Block

To prevent hallucination of domain facts, every prompt includes a short curated
domain facts reference block extracted from DESIGN_PHASE1.md. The agent must
base its explanation ONLY on the facts in this block + case-specific data.

### Domain Facts Block (fixed, injected into every prompt)

```
DOMAIN FACTS - Razorpay Settlement Mechanics (for explanation context only):

1. FEE STRUCTURE:
   - Domestic payments (UPI, cards): 2% platform fee + 18% GST on fee
   - International cards: 3% platform fee + 18% GST on fee
   - Formula: net = gross - fee - (fee x 0.18)

2. SETTLEMENT CYCLE:
   - Standard: T+2 working days from capture date
   - Excludes weekends (Sat/Sun) and bank holidays
   - Daily cut-off ~5:00 PM IST

3. BATCHING:
   - Razorpay does NOT settle 1:1 with orders
   - One settlement_id = one batch = one NEFT credit (one UTR)
   - Refund deductions appear as negative line items in future batches

4. REFUND MECHANICS:
   - Refunds deducted from FUTURE settlement batches
   - MDR is NON-REFUNDABLE even on full refunds
   - One partial refund can land in a different batch than original

5. CURRENCY:
   - Razorpay settles everything in INR
   - For this dataset: fixed rate 1 USD = 83.00 INR

6. IDENTIFIERS:
   - order_id: ord_XXXXXXXXXXXXXX
   - settlement_id: set_XXXXXXXXXXXXXX
   - bank_utr: 16-digit NEFT reference from bank
```
---

## 4. Per-Case Prompt Structure

Each case gets ONE API call with a self-contained prompt. The prompt has
three sections: (A) system instructions, (B) domain facts block, (C) case data.

### Prompt Template

```
SYSTEM: You are a fintech reconciliation analyst. Your job is to EXPLAIN
a specific reconciliation case in plain English. You must:
- Base your explanation ONLY on the data provided below
- Reference specific amounts, dates, and IDs from the case data
- Do NOT invent facts not present in the data or the domain reference
- Do NOT re-classify the case - the status is already determined
- Keep your explanation to 2-4 sentences
- If asked about a needs_review case, express genuine uncertainty

DOMAIN REFERENCE:
{domain_facts_block}

CASE DATA:
{case_specific_data}

TASK: Explain why this case has status "{status}" with exception code
"{exception_code}". Use the actual numbers and IDs from the case data.
```

### Case-Specific Data by Exception Type

#### 4a. REFUND_SPLIT (3 cases)

Data passed to LLM:
- Order ledger row: order_id, gross_amount, currency, payment_method, created_at
- All settlement rows for this order_id (may span 2+ settlement_ids):
  settlement_id, gross_amount, transaction_fee, gst_on_fee, refund_deduction, net_amount
- Order residual (sum of net_amounts across all settlement rows)
- Cross-batch linkage: which settlement_ids contain this order rows
- Bank UTR(s) linked to each settlement batch

Example for ord_ohZLWumvMi5brH8l:
```
Order: ord_ohZLWumvMi5brH8l
- Ledger: gross=14802.45 INR, payment_method=upi, created=2025-08-07
- Settlement row 1 (set_V4wg4sqjavfNXiFk): gross=14802.45, fee=296.05, gst=53.29, net=14453.11
- Settlement row 2 (set_ZxZYr1ol75jKh16f): refund=-2558.65, net=-2558.65
- Order residual: 14453.11 + (-2558.65) = 11894.46
- Bank UTR: 5977216880053789
- Exception: REFUND_SPLIT (partial refund in different batch than original)
```

#### 4b. CURRENCY_MISMATCH (2 cases)

Data passed to LLM:
- Order ledger row: order_id, gross_amount (in USD), currency, created_at
- Settlement row: gross_amount (in INR), transaction_fee, gst_on_fee, net_amount
- Conversion applied: FX_RATE = 83.00, converted INR amount
- Match result: converted ledger amount vs settlement amount

Example for ord_0EE1Z6jjCFojTQpT:
```
Order: ord_0EE1Z6jjCFojTQpT
- Ledger: gross=424.24 USD, currency=USD, payment_method=international_card
- Settlement (set_Ug9C5dqtELc0MalO): gross=35211.92 INR, fee=1056.36, gst=190.14, net=33965.42
- Conversion: 424.24 USD x 83.00 = 35211.92 INR (matches settlement gross exactly)
- Bank UTR: 9136817203136955
- Exception: CURRENCY_MISMATCH (ledger stores USD, settlement/bank store INR)
```

#### 4c. DUPLICATE_ORDER (1 case - needs_review)

Data passed to LLM:
- Both conflicting ledger rows (order_id, gross_amount, quantity, customer_id, created_at)
- Same order_id, same customer, same timestamp, same quantity, DIFFERENT amounts
- No settlement rows exist (matcher could not resolve which amount is correct)
- Explicit instruction: express uncertainty, do not pick a winner

Example for ord_EnDJiS9HvlxNgbb1:
```
Order: ord_EnDJiS9HvlxNgbb1
- Row 1: gross=1130.56 INR, quantity=5, customer=cust_7GvmQFOiJZ, created=2025-08-10
- Row 2: gross=1202.36 INR, quantity=5, customer=cust_7GvmQFOiJZ, created=2025-08-10
- Same customer, same SKU, same timestamp, same quantity - different amounts
- No settlement rows found for this order_id
- Exception: DUPLICATE_ORDER (requires human judgment to determine correct amount)
```

#### 4d. UNMATCHED_ORDER (8 cases - hard_exception)

Three sub-types:

**Failed payment (5 cases):**
```
Order: {order_id}
- Ledger: gross={amount}, payment_status=failed, created_at={timestamp}
- Settlement: NO rows found
- Bank: NO credits linked
- Explanation: Payment was attempted but failed. No settlement or bank credit expected.
```

**Authorized not captured (2 cases):**
```
Order: {order_id}
- Ledger: gross={amount}, payment_status=authorized, created_at={timestamp}
- Settlement: NO rows found
- Bank: NO credits linked
- Explanation: Payment authorized by bank but never captured. May settle in future cycle.
```

**Missing settlement (1 case):**
```
Order: ord_u6qLtRkvl8zSWSrH
- Ledger: gross={amount}, payment_status=captured
- Settlement: NO rows found
- Bank: NO credits linked
- Explanation: Captured order missing from settlement report. Data integrity issue.
```

#### 4e. GHOST_TRANSACTION (1 case - needs_review)

```
Settlement batch: set_1E8lJ4dKfU21o9Is
- Bank UTR: 7200283948232204
- Batch net: 14420.72 INR, Bank credit: 14420.72 INR (matched)
- 6 orders in batch:
  - ord_HPM8Q5WQdYvGi5l7 (GHOST - not in order_ledger.csv)
  - ord_B2isTsjSspJxHl1P (legitimate)
  - ord_Is9f9lgvbISm6Csq (legitimate)
  - ord_KXth3FkmdB6K3JGM (legitimate)
  - ord_BNaQNpUcpvjV21Jv (legitimate)
  - ord_w3zeQSzElIc0Jwfe (legitimate)
- Exception: GHOST_TRANSACTION (settlement references order not in ledger)
```

#### 4f. NEFT_FAILED (1 case - hard_exception)

```
Settlement batch: set_7oqQnmBR7evr0ci5
- Bank UTR: 9503100649340391
- Batch net: 62386.14 INR (positive - credit expected)
- 5 orders in batch
- Bank statement: UTR 9503100649340391 NOT FOUND
- Exception: NEFT_FAILED (positive net but bank never credited)
```

#### 4g. NO_CREDIT_EXPECTED (1 case - no longer narrated)

```
Settlement batch: set_vlVzIbTfj7VNQanv
- Bank UTR: 4299074729669417
- Batch net: -446.18 INR (NEGATIVE - refund deductions exceeded gross)
- 2 orders in batch
- Bank statement: UTR 4299074729669417 NOT FOUND
- Status: NO_CREDIT_EXPECTED (negative net correctly produces no bank credit)
```

**Note:** post-Fix 4 (DESIGN_PHASE2.md addendum) the negative-net batch is classified `matched`, so NO_CREDIT_EXPECTED is excluded from the 28 narrated cases. The builder is retained in explainer.py for safety.

#### 4h. UNRECORDED_REFUND (12 cases - needs_review)

Added by Fix 3 (see DESIGN_PHASE2.md addendum): the ledger claims a partial
refund but no refund_deduction row exists in the settlement report. The engine
flags UNRECORDED_REFUND and asks a human to reconcile the gap.

Data passed to LLM:
- The full match_log entry (settlement_ids, bank_utr, order_residual, refund_type, detail)
- There is no dedicated builder: build_case_data falls back to the raw case JSON

Example for ord_2ozm6RqNbOW8W3nD: the ledger shows a partial refund of Rs 134.57,
the settlement batch contains no refund-deduction entry, and the order residual
is Rs 363.96 -- the explanation states the discrepancy and the uncertainty.

---

## 5. Output Schema

Each case produces one entry in agent/output/explanations.json.

```json
{
  "case_id": "ord_ohZLWumvMi5brH8l",
  "case_type": "order",
  "status": "matched_with_note",
  "exception_code": "REFUND_SPLIT",
  "explanation": "This order of Rs 14,802.45 was originally settled in batch set_V4wg4sqjavfNXiFk (net Rs 14,453.11 after 2% UPI fee and GST). A partial refund of Rs 2,558.65 was later deducted from a different batch (set_ZxZYr1ol75jKh16f). This cross-batch split is normal Razorpay behavior when a refund is initiated after the original batch has already settled.",
  "suggested_action": "No action required - expected behavior for partial refunds near batch boundaries.",
  "confidence_note": null,
  "hallucination_check": {
    "stated_figures": ["14802.45", "14453.11", "2558.65"],
    "verified": true,
    "mismatches": []
  }
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| case_id | string | order_id or settlement_id |
| case_type | string | "order" or "settlement" |
| status | string | Copied read-only from Phase 2 output - NEVER altered |
| exception_code | string | Copied read-only from Phase 2 output |
| explanation | string | 2-4 sentence plain English, grounded in case data |
| suggested_action | string | Advisory only, clearly labeled as suggestion |
| confidence_note | string or null | ONLY non-null for needs_review cases |
| hallucination_check | object | Automated cross-verification of stated figures |

### Confidence Note Rules

- matched_with_note: null
- hard_exception: null
- needs_review (DUPLICATE_ORDER): REQUIRED - must express genuine uncertainty
- needs_review (GHOST_TRANSACTION): null (classification certain, resolution needs human)
- needs_review (UNRECORDED_REFUND): null (flagged for manual review -- no single figure is in dispute, the refund row is simply absent from settlement)

---

## 6. Hallucination Safeguard

After the LLM produces an explanation, a lightweight automated check
extracts any currency figures/dates the agent states and cross-verifies
them against the actual source data for that case.

### How It Works

1. **Extract figures:** Regex scan of explanation text for patterns like
   Rs X,XXX.XX, $XXX.XX, XXX.XX INR, XXX.XX USD, date patterns like 2025-XX-XX

2. **Cross-verify:** For each extracted figure, check if it appears in
   EITHER (a) the case source data (match_log.json entry + relevant CSV rows)
   OR (b) the domain facts reference block (fee percentages like 2%/3%/18%,
   FX rate 83.00, effective rates like 2.36%/3.54%, etc.). A figure is only
   a true hallucination if it matches NEITHER source. Tolerance: exact string
   match on the number (after stripping commas and currency symbols).

3. **Flag mismatches:** If a stated figure does not match any source data,
   mark hallucination_check.verified = false and list mismatches.

4. **Do NOT auto-fix:** Mismatches are flagged for human review. The
   explanation is still included in the output - just flagged.

### Why This Is Sufficient

- The agent is narrating pre-computed facts, not generating new analysis
- Temperature=0 reduces randomness
- The domain facts block prevents invention of domain rules
- The cross-verify catches any creative rounding or figure invention
- 28 cases is small enough for human spot-checking anyway

### Known Limitation: Relational/Logical Claims

The hallucination checker extracts numeric figures from the explanation and cross-verifies them against source data. However, it **cannot catch relational or logical claims** that misrepresent HOW numbers relate to each other.

Example: An explanation could correctly state figures "1130.56" and "1202.36" (both present in source data) but incorrectly claim "both amounts appear in the same settlement batch" — a relational claim that is factually wrong but passes the figure-level check.

This was observed in the DUPLICATE_ORDER case for ord_EnDJiS9HvlxNgbb1, where the LLM initially stated "both rows were included in a single batch" when only one settlement row existed. The fix was to make the case data builder explicitly state the relationship between ledger rows and settlement rows, rather than relying on the LLM to infer it correctly.

**Mitigation:** For cases where relational accuracy matters (DUPLICATE_ORDER, UNRECORDED_REFUND), the case data builder should explicitly describe the relationship between entities, not just list the raw figures. This shifts the burden from LLM inference to deterministic data preparation.

### Known Limitation: Formatting Sensitivity

Exact-string-match can false-flag a correct explanation due to formatting
differences (e.g. LLM writes "Rs 14,802" without trailing ".45" while source
has "14802.45", or uses "14802.45" without comma). When reviewing the 28
outputs, treat hallucination_check.mismatches as a filter for manual review,
not as proof of actual hallucination. A mismatch means "verify this number
manually" -- not "the LLM made this up".

---

## 7. API Call Strategy

### Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | openai/gpt-oss-120b (Groq) | Free tier, no cost; sufficient for narration of pre-computed facts |
| Temperature | 0 | Deterministic output for reproducibility |
| Max tokens | 800 | 2-4 sentence explanations with margin for model output format |
| Calls per case | 1 | One call, one explanation - no chaining needed |
| Total API calls | 28 | One per case, sequential |

### Call Pattern

```python
for case in cases_to_explain:
    prompt = build_prompt(case, domain_facts_block)
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_tokens=300
    )
    explanation = response.choices[0].message.content
    hc = run_hallucination_check(explanation, case)
    save_entry(case, explanation, hc)
```

### Error Handling

| Error Type | Retry Strategy |
|------------|---------------|
| Rate limit (429) | Exponential backoff: 2s, 4s, 8s, 16s - max 3 retries |
| Server error (500/502/503) | Retry once after 5s |
| Timeout | Retry once after 5s |
| Auth error (401) | STOP - check API key, do not retry |
| All retries exhausted | Log error, save case with explanation="ERROR: API call failed", flag for manual retry |

### Why Groq (not OpenAI)

Switched to Groq free tier for the buildathon submission:
- **Zero cost**: 28 calls at $0.00 on the Groq free tier
- **No quality tradeoff**: the task is pure narration of pre-computed facts - openai/gpt-oss-120b handles this identically to GPT-4o-mini
- **No setup overhead**: avoids local model deployment given deadline constraints
- Groq free tier limits (30 req/min, 14,400/day) far exceed our 28-call budget
- Uses the openai Python SDK pointed at Groq endpoint (base_url) - no new dependencies

### No LangChain

Per locked stack: direct openai SDK calls only. The task is 28 independent
API calls with no chaining, retrieval, or tool use - LangChain would add
complexity with zero benefit.

---

## 8. API Key Handling

- API key stored in .env file at project root: GROQ_API_KEY (get a free key at https://console.groq.com)
- Loaded via os.environ.get("GROQ_API_KEY") in the agent module; the openai SDK is pointed at Groq's endpoint (base_url=https://api.groq.com/openai/v1)
- .env is in .gitignore (confirmed)
- .env is NOT committed to the repository
- If key is missing at runtime: clear error message, exit gracefully

---

## 9. Storage

- Output path: agent/output/explanations.json
- Format: JSON array of objects (one per case), sorted by case_type then case_id
- match_log.json is READ-ONLY - Phase 3 reads from it, never writes to it
- explanations.json is WRITE-ONCE - generated by Phase 3, read by Phase 4/5
- If re-run is needed: delete explanations.json and re-run (idempotent at temperature=0)

---

## 10. Implementation Scope (for Phase 3 build)

Files to create:
- agent/explainer.py - main script: loads match_log, builds prompts, calls API, runs hallucination check, saves explanations
- agent/output/explanations.json - generated output (not hand-written)

Files to modify:
- None (Phase 3 reads from engine/output/match_log.json, never modifies it)

Dependencies:
- openai Python SDK (approved per locked stack)
- python-dotenv for loading .env (or use manual os.environ load)

Runtime:
- ~28 API calls, ~8-15 seconds total at temperature=0
- Cost: $0.00 (Groq free tier)

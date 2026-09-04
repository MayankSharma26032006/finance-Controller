# DESIGN_PHASE7.md - Settlement Q&A Agent

## 1. Purpose

Allow a user to ask a natural-language question about ANY of the 591 cases (orders
and settlements) and get a grounded, factual answer. The agent answers using ONLY
data retrieved from existing pipeline outputs. It never invents facts, never
re-classifies or re-decides, and says "I dont have enough information" for
out-of-scope questions.

## 2. Architecture Overview

The QA agent is a pure read-retrieve-respond pipeline with no state mutation:

1. **Load** -- On first call, load reconciliation_report.json (591 entries),
   match_log.json (591 entries), explanations.json (29 entries), and
   metrics_report.json into memory dicts keyed by case_id. Never reloaded.

2. **Classify** -- Parse the free-text question to determine category:
   - Contains ord_/set_ pattern -> single_case
   - Asks about counts/rates/breakdowns -> aggregate
   - Neither / unresolvable -> out_of_scope

3. **Retrieve** -- Based on classification:
   - single_case: look up case_id in RR (primary) + ML (enrichment) + EXP
   - aggregate: extract pre-computed stats from RR.summary + MR metrics
   - out_of_scope: skip to step 6 with structured fallback

4. **Ground** -- Build prompt with: (A) domain facts, (B) case/summary data, (C) question

5. **Generate** -- Groq API call (llama-3.3-70b, temperature=0), retry on 429/5xx

6. **Verify** -- Extract figures from answer, cross-check against source data

7. **Return** -- Structured dict: {answer, source_case_ids, verified, category, fallback_reason}

## 3. Data Sources (read-only)

| File | What it provides | Used for |
|------|-----------------|----------|
| reconciliation_report.json | 591 entries with simplified_status, exception_code, key_figures, explanation (for 29 cases) | Single-case lookup, aggregate counting |
| match_log.json | 591 entries with raw matching data: settlement_ids, bank_utr, order_residual, refund_type, confidence, detail, soft_flags | Enriched single-case data when RR lacks detail |
| explanations.json | 29 narrated cases with full explanation text, suggested_action, hallucination_check | Reuse existing explanations, dont regenerate |
| metrics_report.json | overall accuracy, per-exception-code precision/recall, FPR/FNR | Aggregate/statistical questions |
| DESIGN_PHASE1.md domain facts | Fee structure (2%/3%, 18% GST), settlement batching, FX rate 83.00, refund deduction timing | Grounding every prompt |

All files loaded once at startup, held in memory as dicts keyed by case_id.

---
## 4. Question Classification

Every incoming question is classified into one of three categories:

### 4a. Single-Case Lookup

Triggers when the question contains a recognizable case identifier:
- Order: contains "ord_" followed by alphanumeric characters
- Settlement: contains "set_" followed by alphanumeric characters
- Or when the question clearly refers to one entity: "this order",
  "the failed settlement", "order X"

Extraction logic:
1. Regex scan for ord_[A-Za-z0-9]+ or set_[A-Za-z0-9]+ patterns
2. If found, look up in reconciliation_report.json (primary) + match_log.json (enrichment)
3. If explanation exists in explanations.json, include it verbatim (do not regenerate)

### 4b. Aggregate / Summary Question

Triggers when the question asks about counts, rates, breakdowns, or comparisons
across multiple cases:
- "How many orders needed human review?"
- "What is the match rate?"
- "How many settlements failed NEFT?"
- "What exception types exist and how many of each?"

Handling: extract the relevant pre-computed statistic from:
- reconciliation_report.json summary block (status counts, match rates)
- metrics_report.json overall/order_level/settlement_level (accuracy, per-code precision/recall)
- Reconstruct the answer from these numbers. The LLM is NOT asked to count from raw data.

### 4c. Out-of-Scope

Triggers when the question:
- References a case_id not found in the data
- Asks about future predictions ("what will next month match rate be?")
- Asks about external data not in the dataset ("what is Razorpays actual fee?")
- Is ambiguous and cannot be resolved to a specific case or statistic

Response: return a structured fallback explaining what information is available
and what is not, without guessing.

---
## 5. Grounding Strategy

### 5a. Domain Facts Block (included in EVERY prompt)

Same curated block from DESIGN_PHASE3.md Section 3:

DOMAIN FACTS - Razorpay Settlement Mechanics:
- Platform fee: 2% for domestic instruments, 3% for international cards
- GST on fee: 18% of the platform fee amount
- Net formula: net = gross - fee - (fee x 0.18)
- Settlement batching: multiple orders batched into one settlement_id per settlement cycle
- Settlement cycle: T+1 to T+3 business days after capture, depending on time of capture
- Refund deductions: partial refunds appear as negative net_amount rows in settlement
- Full refund: refund_deduction magnitude equals original gross_amount; residual = -(fee + gst)
- FX rate for USD orders: 1 USD = 83.00 INR (fixed in synthetic data)
- Bank credit: each settlement_id maps to one NEFT UTR on the bank statement
- Negative net settlement: correctly produces no bank credit (expected, not an error)

### 5b. Single-Case Grounding

For a single-case question, inject the FULL entry from reconciliation_report.json
for that case_id, including all 9 fields:
  case_id, case_type, simplified_status, exception_code, explanation,
  suggested_action, confidence_note, key_figures, soft_flags

If the case has an existing explanation in explanations.json, include it
PRIMARY CONTEXT. The agent should use it as the foundation for its answer,
but is permitted to supplement it with additional fields from the case data
when the user asks about something the explanation does not cover.
The agent must not contradict the existing explanation.

If the case has NO existing explanation (the 562 plain-matched cases), inject
the enriched data from match_log.json instead:
  confidence, match_status, detail, settlement_ids, bank_utr, order_residual,
  refund_type, soft_flags (for orders)
  result_type, bank_utr, status, confidence, batch_net, row_count, order_ids,
  ghost_order_ids, bank_amount, diff, detail, soft_flags (for settlements)

### 5c. Aggregate Grounding

For aggregate questions, inject the pre-computed summary blocks:
- From reconciliation_report.json: summary.orders, summary.settlements, summary.overall
- From metrics_report.json: overall (accuracy, FPR, FNR), order_level.per_exception_code,
  settlement_level.per_exception_code

The agent phrases these numbers in natural language. It NEVER attempts to
count or aggregate raw data itself.

---
## 6. Out-of-Scope Handling

The agent returns a structured fallback when it cannot answer.
The response shape is identical to a normal answer -- the caller never
needs special handling for out-of-scope cases.

### Fallback response schema

All out-of-scope responses follow this exact JSON structure:

```json
{
  "answer": "<human-readable explanation of why the question cannot be answered>",
  "source_case_ids": [],
  "verified": true,
  "category": "out_of_scope",
  "fallback_reason": "<one of the reason codes below>"
}
```

Fields:
- answer: Polite, specific explanation. Must state what the agent CAN do
  (e.g. "Try asking about a specific order or settlement ID") not just
  what it cannot.
- source_case_ids: Always empty [] for out-of-scope (no cases were looked up).
- verified: Always true (there is no LLM-generated claim to verify --
  the fallback is constructed deterministically from rules, not generated).
- category: Always "out_of_scope".
- fallback_reason: One of the codes below. Used by Phase 8 (dashboard)
  for analytics and by the hallucination checker to skip verification.

### Fallback reason codes

| fallback_reason | Trigger condition | Answer template |
|----------------|-------------------|-----------------|
| case_id_not_found | Question contains ord_/set_ pattern but the ID does not exist in reconciliation_report.json | "Order/settlement {id} does not exist in the reconciliation dataset (500 orders, 91 settlements)." |
| future_prediction | Question asks about future behavior ("will match rate improve?", "what about next month?") | "I can only answer questions about the current dataset. I cannot predict future match rates or settlement behavior." |
| external_data | Question asks about data outside the 3-source dataset (Razorpay docs, real bank policies, etc.) | "I can only answer using the 3-source reconciliation dataset. I cannot look up external Razorpay documentation." |
| ambiguous | Question is too vague to classify OR could refer to multiple cases without a specific ID | "Your question could refer to multiple cases. Please specify an order_id (ord_...) or settlement_id (set_...)." |
| ambiguous | Question has no retrievable context even after attempted classification | "I need more specific information. Try asking about a specific order or settlement ID, or ask for summary statistics." |
| api_error | Groq API call failed after all retries (added by answer_question() exception handler, never returned by the classifier) | "I apologize -- the QA service is temporarily unavailable. Please try again in a moment." |

### Example: case_id_not_found

```json
{
  "answer": "Order ord_fake123 does not exist in the reconciliation dataset (500 orders, 91 settlements).",
  "source_case_ids": [],
  "verified": true,
  "category": "out_of_scope",
  "fallback_reason": "case_id_not_found"
}
```

### Example: aggregate out-of-scope

```json
{
  "answer": "I can only answer questions about the current dataset. I cannot predict future match rates or settlement behavior.",
  "source_case_ids": [],
  "verified": true,
  "category": "out_of_scope",
  "fallback_reason": "future_prediction"
}
```

## 7. Hallucination Safeguard

Same two-source verification pattern from Phase 3 (DESIGN_PHASE3.md Section 6):

### For single-case answers:
1. Extract every currency figure and date the LLM states in its answer
2. Verify each figure appears in EITHER the case data block OR the domain facts block
3. A figure is only flagged as hallucinated if it matches NEITHER source
4. Log verification result but do NOT block the answer -- flag for human review

### For aggregate answers:
1. Extract any stated count or percentage
2. Verify against the pre-computed summary stats that were injected into the prompt
3. If the LLM states a number that doesnt match the injected summary, flag it
4. This catches the specific failure mode where the LLM approximates or rounds
   a number instead of using the exact value from the injected data

### Known limitation (same as Phase 3):
The checker verifies figures EXIST in source data but cannot catch incorrect
RELATIONAL claims (e.g. "order X settled in batch Y" when the data says batch Z).
Mitigation: case data blocks explicitly describe relationships, not just raw values.

## 8. Public API

def answer_question(question: str) -> dict:
    """Answer a natural-language question about the reconciliation dataset.

    Args:
        question: Free-text question from the user.

    Returns:
        {
            "answer": str,           # Plain English response
            "source_case_ids": list,  # case_ids referenced (empty if aggregate/out-of-scope)
            "verified": bool,         # True if hallucination check passed
            "category": str,          # "single_case" | "aggregate" | "out_of_scope"
            "fallback_reason": str|None  # populated only for out_of_scope
        }
    """
    ...

This function is the sole entry point for Phase 8 (Streamlit dashboard).
It loads all data at startup (lazy init on first call) and holds it in memory.

## 9. Prompt Template

### Single-case prompt:

You are a financial reconciliation assistant. Answer the user question using
ONLY the data provided below. Do not invent facts or make predictions.

DOMAIN FACTS:
{domain_facts_block}

CASE DATA:
{case_json}  # full reconciliation_report entry, or match_log entry if no RR explanation

{if existing explanation exists:}
EXISTING EXPLANATION (use as primary context, supplement with other fields if needed):
{explanation_text}

OTHER AVAILABLE FIELDS:
{other_fields_json}  # key_figures, soft_flags, suggested_action, etc.

USER QUESTION: {question}
Note: Prioritize answering what was actually asked. The existing explanation
is your primary reference, but if the user asks about a specific field not
covered by the explanation, use the OTHER AVAILABLE FIELDS to answer.

### Aggregate prompt:

You are a financial reconciliation assistant. Answer the user question using
ONLY the pre-computed statistics below. Do not count raw data yourself.

SUMMARY STATISTICS:
{summary_json}  # reconciliation_report.json summary block

ACCURACY METRICS:
{metrics_json}  # metrics_report.json overall + per-code tables

USER QUESTION: {question}

## 10. API Call Strategy

Same locked stack as Phase 3 (DESIGN_PHASE3.md Section 7):

| Parameter | Value |
|-----------|-------|
| Provider | Groq (via openai SDK) |
| Base URL | https://api.groq.com/openai/v1 |
| Model | openai/gpt-oss-120b |
| Temperature | 0 |
| Max tokens | 500 (longer than Phase 3's 300 since QA answers may reference multiple fields) |
| API key | GROQ_API_KEY from .env |

### Retry/Backoff Table

| HTTP Status | Action | Max Retries |
|-------------|--------|-------------|
| 429 (rate limit) | Exponential backoff: 2s, 4s, 8s | 3 |
| 500/502/503/timeout | Single retry after 2s | 1 |
| 401 (auth) | Hard stop, return error to user | 0 |

### Dashboard Failure Behavior (critical for live demo)

If the API call fails after all retries, the QA agent must NOT crash the
Streamlit dashboard. Instead:

1. Catch the exception in answer_question()
2. Return a graceful fallback response with answer = user-friendly message,
   source_case_ids = [], verified = false, category = out_of_scope,
   fallback_reason = api_error
3. Log the error details to stderr for debugging
4. The Streamlit UI displays the answer field as-is (not a Python traceback)

This ensures the demo never shows an error page -- worst case, the user gets
a polite retry message while the rest of the dashboard remains functional.

## 11. Scope Boundary

This module:
- Reads reconciliation_report.json, match_log.json, explanations.json, metrics_report.json
- Writes nothing to disk (response is returned in-memory to the caller)
- Does NOT re-run the matcher, reconciler, or any prior phase
- Does NOT re-explain the 29 Phase 3 cases (reuses existing explanations verbatim)
- Does NOT call any API other than Groq for answer generation
- Does NOT modify any file from Phases 1-6

---

*DESIGN_PHASE7.md - to be implemented as agent/qa_agent.py*
# Audit Trail

Full traceability chain for the Razorpay AI Buildathon reconciliation system.

Generated: 2026-08-27T10:16:28Z

---

## 1. Pipeline Overview

```
Raw Data Sources          Deterministic Matcher       Agent Narration       Final Report         Accuracy Score
    |                          |                          |                    |                     |
 CSVs: ledger,            match_log.json:            explanations.json:  reconciliation_     metrics_report.json:
 settlement,              confidence, exc_code,      explanation text,   report.json:         accuracy per code,
 bank statement           settlement_ids,            suggested_action,   simplified_status,   TP/FP/FN/TN
                          soft_flags                 halluc_check        exception_code
```

Phases 1-2: Synthetic data generation + deterministic matching (no LLM)
Phase 3: Agent narrates 28 non-trivial cases (Groq API, llama-3.3-70b)
Phase 4: Simplified status mapping for human-readable reporting
Phase 5: Accuracy scoring against ground truth (100% verified)
Phase 6: This audit trail (documentation only, no computation)

---

## 2. Chain of Custody

All hashes computed live from files at generation time.

| Phase | File | SHA-256 | Produced By |
|-------|------|---------|-------------|
| Frozen | data/raw/order_ledger.csv | e26d9f8cd70852bad6fa554c35340f11be8c5cefdd24366b3fd9564f8f456d5c | data generator |
| Frozen | data/raw/settlement_report.csv | 8e0953886750e1c7055c635e9118e461c3ce3ac0c64454cd02bd64091de183f2 | data generator |
| Frozen | data/raw/bank_statement.csv | 8f437d051e611216f54aa9cc705327f6c9217c15edf3e8ec8d8201d7869590bf | data generator |
| Frozen | data/raw/ground_truth.json | f2df67996c3a50cc84205829112d421e362a7a1e4254cec9301c95daf5e1ba22 | updated once |
| Frozen | data/raw/ground_truth_settlements.json | 853d8bfbb22bb73098433fe03fc7e708e8e03cd0fb97da9c2fd634c789352690 | updated once |
| Phase 2 | engine/output/match_log.json | 7584a9a94f6afeb47d1b02237449197a533dffc1053c30acc44608ffda5fcdec | matcher_exact.py |
| Phase 3 | agent/output/explanations.json | 363966335a488230c94294a44954c5aa15c868e8374765fea2f41588c0ba0f63 | explainer.py |
| Phase 4 | engine/output/reconciliation_report.json | 29eb46aa26788fefededdb0c712cba00c182408f01a395dea03ca3026d710bec | reconciler.py |
| Phase 5 | engine/output/metrics_report.json | 3df882d3b01a40013a7e5755cff7c2d5f0d753ff39164f1f4cdfb886a6692e44 | metrics_scorer.py |

**Note:** match_log.json is fully deterministic (same hash every run). explanations.json
is NOT deterministic -- Groq temperature=0 does not guarantee identical wording across
runs (same facts, different phrasing). reconciliation_report.json and metrics_report.json
contain a generated_at timestamp, so their hash changes on re-run.

---

## 3. What Broke and How It Was Fixed

Four real issues were found during development via external audit (human review of
generated data against DESIGN.md). Each was independently verified before fixing.

### Bug 1: Duplicate Order Settlement Suppression

**What was wrong:** For order ord_EnDJiS9HvlxNgbb1, which has two ledger rows with
conflicting amounts (1130.56 and 1202.36), the matcher DUPLICATE_ORDER classification
short-circuited before order-level matching ever ran. This meant the real settlement
row (set_NvO7qBhqH6y5IHWi, gross=1130.56) was never linked to the match_log entry --
settlement_ids was set to empty list.

**How it was caught:** Human review of match_log.json noticed settlement_ids=[] for
this order, then independently verified a real settlement row exists in
settlement_report.csv matching the 1130.56 amount exactly.

**How it was fixed:** Added settlement lookup logic for DUPLICATE_ORDER cases in
order_matcher.py. The matcher now attaches real settlement_ids/bank_utr/settlement
data even when confidence stays needs_review. The explainer case-data builder was
also updated to reference the real settlement data.

**Independent verification:** Re-ran matcher_exact.py, confirmed
settlement_ids=[set_NvO7qBhqH6y5IHWi] now appears in match_log.json. Re-ran
explainer.py, confirmed explanation now correctly states settlement supports 1130.56
but 1202.36 is unverified.

### Bug 2: 12 Unrecorded Refunds

**What was wrong:** 12 orders had refund_status=partial in order_ledger.csv but no
corresponding refund_deduction row in settlement_report.csv. The matcher classified
all 12 as matched with refund_type=none, completely blind to the discrepancy. This
is a genuine ledger-vs-settlement mismatch that should be flagged for human review.

**How it was caught:** Cross-referencing ledger refund_status against settlement
refund_deduction rows using an independent script. The original design
(DESIGN_PHASE1.md) mentioned ~15 partial refund orders, 3 of which are REFUND_SPLIT
(correctly handled). The other 12 were a gap between the design intent and the
generator output.

**How it was fixed:** Added new exception_code UNRECORDED_REFUND detection in
order_matcher.py. After order matching, the matcher cross-references ledger
refund_status against settlement refund_deduction rows and flags orders where the
ledger claims a refund but no settlement refund row exists. The 12 orders were
reclassified from matched to needs_review.

**Independent verification:** Independently re-derived the 12 order_ids directly
from raw CSVs using the objective rule (refund_status=partial/full AND no matching
settlement refund row). Confirmed all 12 match exactly between the independent
derivation and the matcher output.

### Bug 3: Negative-Net Batch Mislabeling

**What was wrong:** Settlement batch set_vlVzIbTfj7VNQanv (net=-446.18, UTR
4299074729669417) was assigned confidence=hard_exception in batch_matcher.py, the
same label as genuine failures like NEFT_FAILED. But a negative-net batch correctly
produces no bank credit -- this is expected behavior, not an error.

**How it was caught:** Human review of batch_matcher.py noticed the condition
confidence = hard_exception if batch_net < 0 else matched
treats all negative-net batches as exceptions.

**How it was fixed:** Changed confidence from hard_exception to matched for
negative-net batches. The batch still carries exception_code=NO_CREDIT_EXPECTED
for traceability in reporting.

**Independent verification:** Re-ran matcher_exact.py, confirmed set_vlVzIbTfj7VNQanv
now has confidence=matched. Re-ran metrics_scorer.py, confirmed it scores correctly
as Reconciled (no credit due) against ground_truth_settlements.json.

### Claim 2 (Refuted): Expected Residual Formula

**What was claimed:** The expected_residual for REFUND_SPLIT cases does not match
order_residual, and this was suspected to be a formula bug.

**What was found:** The field is computed-but-unused in matching logic.
expected_residual measures gross-based retained value (orig_gross - refund_amount),
while order_residual measures net-of-fees sum. They measure different things by
design. The code never compares them. No bug -- confirmed not a fix.

---

## 4. Failure Handled Gracefully: NEFT_FAILED

This is exactly the kind of exception the system is designed to catch: a real missing bank credit.

**Settlement batch:** set_7oqQnmBR7evr0ci5

| Field | Value |
|-------|-------|
| Orders in batch | 5 |
| Net amount | 62386.14 INR |
| Expected bank UTR | 9503100649340391 |
| Bank statement | NOT FOUND -- credit never arrived |
| Phase 2 confidence | hard_exception |
| Phase 2 status | batch_neft_failed |
| Phase 4 simplified_status | Unresolved |
| Ground truth | NEFT_FAILED |

**Phase 3 explanation:** The settlement batch set_7oqQnmBR7evr0ci5 was scheduled to credit ₹62,386.14 for five orders via NEFT with UTR 9503100649340391. However, the bank statement shows no entry for that UTR, and the batch is flagged with the NEFT_FAILED exception. This means the expected credit was never received in the bank account.

**Why this matters:** Without automated reconciliation, a missing 62K INR credit could
go unnoticed for days. The system flagged it immediately, provided a human-readable
explanation, and correctly escalated it for bank ops investigation.

---

## 5. Fully-Traced Example Cases

### Example 1: Clean Match -- ord_0Avn4Yk3gLazPS7o

| Phase | Data |
|-------|------|
| Raw (order_ledger) | gross_amount=172458.41, payment_status=captured |
| Phase 2 (match_log) | confidence=matched, settlement_ids=['set_XSbG5A1ROwkHLHyD'], bank_utr=4893326145430796 |
| Phase 3 (explanation) | Not narrated (plain match, below threshold) |
| Phase 4 (reconciliation) | simplified_status=Reconciled, exception_code=None |
| Phase 5 (metrics) | Scored as correct. none-class TP += 1 |
| Ground truth | expected_match_status=matched, exception_code=None |

**Trace:** Order captured, settled in batch set_XSbG5A1ROwkHLHyD, credited to bank with UTR 4893326145430796. All three sources agree.

### Example 2: DUPLICATE_ORDER -- ord_EnDJiS9HvlxNgbb1

| Phase | Data |
|-------|------|
| Raw (order_ledger) | 2 rows: amounts=1130.56, 1202.36 |
| Phase 2 (match_log) | confidence=needs_review, exception_code=DUPLICATE_ORDER, settlement_ids=['set_NvO7qBhqH6y5IHWi'] |
| Phase 3 (explanation) | On 2025‑08‑10 the order ord_EnDJiS9HvlxNgbb1 generated two identical ledger rows for cust_7GvmQFOiJZ (quantity 5) but with different gross amounts – ₹1,130.56 and ₹1,202.36. The settlement report shows only the ₹1,130.56 entry in batch set_NvO7qBhqH6y5IHWi with bank UTR 1845235426874470, while the ₹1,202.36 row has no matching settlement record. Because the duplicate‑order exception requires human judgment, we cannot determine which amount is correct. |
| Phase 4 (reconciliation) | simplified_status=Needs Human Review, exception_code=DUPLICATE_ORDER |
| Phase 5 (metrics) | Scored as correct. DUPLICATE_ORDER TP += 1 |
| Ground truth | 2 entries: matched/None; exception/DUPLICATE_ORDER |

**Trace:** Two conflicting amounts for one order. Settlement supports 1130.56 only. System correctly refuses to guess, flags for human review.

### Example 3: NEFT_FAILED -- set_7oqQnmBR7evr0ci5

| Phase | Data |
|-------|------|
| Raw (settlement_report) | 5 orders, net=62386.14, UTR=9503100649340391 |
| Raw (bank_statement) | No entry for UTR 9503100649340391 |
| Phase 2 (match_log) | confidence=hard_exception, status=batch_neft_failed |
| Phase 3 (explanation) | The settlement batch set_7oqQnmBR7evr0ci5 was scheduled to credit ₹62,386.14 for five orders via NEFT with UTR 9503100649340391. However, the bank statement shows no entry for that UTR, and the batch is flagged with the NEFT_FAILED exception. This means the expected credit was never received in the bank account. |
| Phase 4 (reconciliation) | simplified_status=Unresolved, exception_code=NEFT_FAILED |
| Phase 5 (metrics) | Scored as correct. NEFT_FAILED TP += 1 |
| Ground truth | expected_bank_credit_status=NEFT_FAILED, net_amount_expected=62386.14 |

**Trace:** NEFT credit never arrived. System correctly identified the missing bank credit, flagged as unresolved, recommended escalation to bank ops.

---

## 6. Summary Statistics

| Metric | Value |
|--------|-------|
| Total entries | 591 |
| Reconciled | 562 |
| Reconciled (with note) | 5 |
| Reconciled (no credit due) | 1 |
| Needs Human Review | 14 |
| Unresolved | 9 |
| Overall match rate | 96.1% |
| Phase 5 accuracy | 100% (591/591) |
| Mismatches | 0 |

| Exception Code | Count |
|---------------|-------|
| CURRENCY_MISMATCH | 2 |
| DUPLICATE_ORDER | 1 |
| GHOST_TRANSACTION | 1 |
| NEFT_FAILED | 1 |
| NO_CREDIT_EXPECTED | 1 |
| REFUND_SPLIT | 3 |
| UNMATCHED_ORDER | 8 |
| UNRECORDED_REFUND | 12 |

---

*Generated by engine/generate_audit.py. All hashes computed live.*
# DESIGN_PHASE5.md — Metrics Scoring Engine

## 1. Purpose

Score the reconciliation system's accuracy against known ground truth. Pure measurement layer
- reads reconciliation_report.json + ground_truth.json + ground_truth_settlements.json
- does NOT re-match, re-narrate, or re-categorize
- writes metrics_report.json + metrics_report.md

## 2. Inputs (read-only)

| File | Source | Count | Keys |
|------|--------|-------|------|
| reconciliation_report.json | Phase 4 output | 500 orders + 91 settlements = 591 entries | case_id, case_type, simplified_status, exception_code, key_figures |
| ground_truth.json | data/raw/ | 501 entries (500 unique order_ids, 1 duplicate) | order_id, expected_match_status, expected_settlement_ids, expected_bank_utr, exception_code |
| ground_truth_settlements.json | data/raw/ | 91 entries | settlement_id, expected_bank_utr, expected_bank_credit_status, net_amount_expected |

**Critical structural asymmetry:** ground_truth.json has 501 entries because `ord_EnDJiS9HvlxNgbb1` (DUPLICATE_ORDER) appears twice — once with `expected_match_status: matched` (first ledger row) and once with `expected_match_status: exception, exception_code: DUPLICATE_ORDER` (second ledger row). The reconciliation report has 1 entry for this order. See Section 3 for handling rules.

## 3. Comparison Logic

### 3a. Status Vocabulary Mapping

The report and ground truth use different vocabularies. The scorer must bridge them:

| Report simplified_status | Ground truth expected_match_status + exception_code | Verdict |
|--------------------------|---------------------------------------------------|---------|
| Reconciled | matched + null | CORRECT |
| Reconciled (with note) | exception + CURRENCY_MISMATCH | CORRECT |
| Reconciled (with note) | exception + REFUND_SPLIT | CORRECT |
| Reconciled (no credit due) | N/A (settlement-level only) | See 3c |
| Needs Human Review | exception + DUPLICATE_ORDER | CORRECT |
| Needs Human Review | exception + UNRECORDED_REFUND | CORRECT |
| Needs Human Review | exception + GHOST_TRANSACTION | CORRECT (settlement) |
| Unresolved | exception + UNMATCHED_ORDER | CORRECT |
| Unresolved | exception + NEFT_FAILED | CORRECT (settlement) |
| Reconciled | exception + any | FALSE NEGATIVE |
| Unresolved / Needs Human Review | matched + null | FALSE POSITIVE |

**Decision rule:** Compare exception_code, not simplified_status. A case is CORRECT if:
- report.exception_code == gt.exception_code (both null, or both same non-null code)
- report.simplified_status maps correctly per the table above (secondary check)

### 3b. DUPLICATE_ORDER Resolution Rule

For `ord_EnDJiS9HvlxNgbb1` specifically:
- ground_truth.json has 2 entries: one `matched` (null exception) + one `exception/DUPLICATE_ORDER`
- reconciliation_report.json has 1 entry: `Needs Human Review/DUPLICATE_ORDER`
- **Rule:** When an order_id has multiple ground truth entries, use the EXCEPTION entry as authoritative. The `matched` entry for the same order_id is a ledger-row artifact, not an independent classification target.
- Implementation: build a GT lookup dict. If an order_id has both `matched` and `exception` entries, keep only the `exception` entry for scoring. This reduces GT from 501 to 500 unique scored entries.

### 3c. Settlement Comparison

Compare report exception_code against ground_truth_settlements.json's expected_bank_credit_status:

| Report exception_code | GT expected_bank_credit_status | Verdict |
|-----------------------|-------------------------------|---------|
| null (Reconciled) | credited | CORRECT |
| NO_CREDIT_EXPECTED | no_credit_expected | CORRECT |
| NEFT_FAILED | NEFT_FAILED | CORRECT |
| GHOST_TRANSACTION | credited | CORRECT (GHOST is about order presence, not credit) |
| null | NEFT_FAILED | FALSE NEGATIVE |
| NEFT_FAILED | credited | FALSE POSITIVE |
| NO_CREDIT_EXPECTED | credited | FALSE POSITIVE |

### 3d. Matched Order Count Verification

The report shows 474 Reconciled orders. Ground truth has 475 unique order_ids with `expected_match_status: matched`. The discrepancy of 1 is `ord_EnDJiS9HvlxNgbb1` — GT has both a `matched` entry (first row) and an `exception` entry (second row). After applying the 3b resolution rule, GT has 474 truly-matched unique orders, matching the report exactly.

### 3e. False Negative Detection Rule

A false negative is: ground truth says `expected_match_status: exception` but report says `simplified_status: Reconciled` with `exception_code: null`.

This is the most dangerous failure mode — the system silently matched something that should have been flagged. The scorer must list every false negative individually, not just count them.

### 3f. False Positive Detection Rule

A false positive is: ground truth says `expected_match_status: matched` (exception_code: null) but report says `simplified_status` is NOT `Reconciled` (or has a non-null exception_code).

## 4. Metrics to Compute

### 4a. Overall Accuracy

```
correct = count of cases where report.exception_code == gt.exception_code
total = 500 orders + 91 settlements = 591
accuracy = correct / total
```

### 4b. Order-Level Accuracy

```
order_correct = orders where report.exception_code matches GT (after 3b resolution)
order_accuracy = order_correct / 500
```

### 4c. Settlement-Level Accuracy

```
settlement_correct = settlements where report.exception_code maps correctly to GT credit status (per 3c table)
settlement_accuracy = settlement_correct / 91
```

### 4d. Per-Exception-Code Precision and Recall

For each exception_code C -- including `None` (matched orders) -- use the SAME generic formula:

```
true_positives  = cases where GT.exception_code == C AND report.exception_code == C
false_positives = cases where GT.exception_code != C AND report.exception_code == C
false_negatives = cases where GT.exception_code == C AND report.exception_code != C
true_negatives  = cases where GT.exception_code != C AND report.exception_code != C

precision_C = TP / (TP + FP)   # of those flagged as C, how many are correct
recall_C    = TP / (TP + FN)   # of those that ARE C, how many were caught
f1_C        = 2 * precision * recall / (precision + recall)
```

This applies identically to C = `None` (matched class). For example, with TP=474, FP=0, FN=0, TN=26:
- precision_None = 474 / (474 + 0) = 1.0
- recall_None = 474 / (474 + 0) = 1.0

No special-casing. The multiclass formula handles the matched class naturally.

### 4e. False Positive Rate (FPR)

FPR and FNR are the binary (matched-vs-exception) views of the same multiclass confusion matrix. They are NOT computed from a separate "total FP" / "total FN" -- they derive from the per-class counts above.

```
FPR = none-class FN / (none-class FN + none-class TP)
# i.e. cases where GT said matched (exception_code=None) but report flagged as exception
# Equivalently: count of ground_truth "matched" orders that the system incorrectly classified as exceptions
```

For the expected 0-mismatch case: none-class FN = 0, none-class TP = 474, so FPR = 0.

### 4f. False Negative Rate (FNR)

```
FNR = sum(exception-class FN) / (sum(exception-class FN) + sum(exception-class TP))
# i.e. cases where GT said exception but report said matched (exception_code=None)
# Equivalently: count of ground_truth "exception" orders the system silently matched
```

For the expected 0-mismatch case: all exception-class FN = 0, all exception-class TP = 26 (8+2+3+1+12 across orders) + 2 (settlements) = 28, so FNR = 0.

**Cross-check:** FPR + FNR must be consistent with overall accuracy. Specifically:
- false_positives_total = none-class FN (same set of cases)
- false_negatives_total = sum of all exception-class FN (same set of cases)
- overall_accuracy = 1 - (false_positives_total + false_negatives_total) / 591

### 4g. Match Rate Verification

Phase 4 reports match_rate_pct = 96.1%. Phase 5 verifies this independently:
```
reconciled = report entries with simplified_status in ('Reconciled', 'Reconciled (with note)', 'Reconciled (no credit due)')
phase5_match_rate = len(reconciled) / 591
# Should equal 568/591 = 96.1% -- flag if different
```

## 5. Output Schema

### 5a. metrics_report.json

```json
{
  "report_metadata": {
    "generated_at": "ISO-8601 timestamp",
    "reconciliation_report_hash": "SHA-256 of input",
    "ground_truth_hash": "SHA-256 of input",
    "ground_truth_settlements_hash": "SHA-256 of input"
  },
  "completeness": {
    "orders_scored": 500,
    "settlements_scored": 91,
    "total_scored": 591,
    "assertion_passed": true
  },
  "overall": {
    "accuracy": 1.000,
    "match_rate_from_report": 0.961,
    "match_rate_phase5_verified": 0.961,
    "false_positive_rate": 0.0,
    "false_negative_rate": 0.0
  },
  "order_level": {
    "total": 500,
    "correct": 500,
    "accuracy": 1.0,
    "per_exception_code": {
      "none": { "TP": 474, "FP": 0, "FN": 0, "TN": 26, "precision": 1.0, "recall": 1.0, "f1": 1.0 },
      "UNMATCHED_ORDER": { "TP": 8, "FP": 0, "FN": 0, "TN": 492, "precision": 1.0, "recall": 1.0, "f1": 1.0 },
      "...remaining codes follow same pattern..."
    }
  },
  "settlement_level": {
    "total": 91,
    "correct": 91,
    "accuracy": 1.0,
    "per_exception_code": { "...same TP/FP/FN/TN structure..." }
  },
  "mismatches": [],
  "expected_counts": {
    "orders": 500,
    "settlements": 91,
    "total": 591
  }
}
```

### 5b. mismatches array

Each mismatch entry (empty if system is fully correct):
```json
{
  "case_id": "ord_xxx",
  "case_type": "order",
  "expected_status": "exception",
  "expected_exception_code": "UNMATCHED_ORDER",
  "actual_status": "Reconciled",
  "actual_exception_code": null,
  "mismatch_type": "false_negative",
  "ground_truth_detail": "...",
  "report_key_figures": { "..." }
}
```

## 6. Processing Logic (Pseudocode)

```
FUNCTION main():
    # Step 0: Load all 3 inputs (read-only)
    report = load_json('engine/output/reconciliation_report.json')
    gt = load_json('data/raw/ground_truth.json')
    gt_set = load_json('data/raw/ground_truth_settlements.json')

    # Step 0.5: Compute and store hashes of all 3 inputs
    hashes = {
        reconciliation_report_hash: sha256('engine/output/reconciliation_report.json'),
        ground_truth_hash: sha256('data/raw/ground_truth.json'),
        ground_truth_settlements_hash: sha256('data/raw/ground_truth_settlements.json')
    }

    # Step 1: Build GT lookup dicts
    # For orders: key by order_id. If duplicate exists, keep exception entry (Section 3b).
    gt_order_map = {}
    FOR each entry IN gt:
        oid = entry.order_id
        IF oid NOT IN gt_order_map:
            gt_order_map[oid] = entry
        ELIF entry.expected_match_status == 'exception':
            gt_order_map[oid] = entry  # exception overrides matched
    ASSERT len(gt_order_map) == 500  # completeness

    # For settlements: key by settlement_id
    gt_set_map = {e.settlement_id: e FOR e IN gt_set}
    ASSERT len(gt_set_map) == 91  # completeness

    # Step 2: Score each order
    order_results = []
    mismatches = []
    FOR each rr_entry IN report.orders:
        oid = rr_entry.case_id
        gt_entry = gt_order_map[oid]
        gt_exc = gt_entry.exception_code OR null
        rr_exc = rr_entry.exception_code OR null
        correct = (gt_exc == rr_exc)
        IF NOT correct:
            mismatch_type = classify(gt_exc, rr_exc)
            mismatches.append({...})
        order_results.append({case_id, expected, actual, correct})

    # Step 3: Score each settlement (per Section 3c)
    settlement_results = []
    credit_map = {
        null: 'credited',
        'NO_CREDIT_EXPECTED': 'no_credit_expected',
        'NEFT_FAILED': 'NEFT_FAILED',
        'GHOST_TRANSACTION': 'credited'
    }
    FOR each rr_entry IN report.settlements:
        sid = rr_entry.case_id
        gt_credit = gt_set_map[sid].expected_bank_credit_status
        rr_exc = rr_entry.exception_code OR null
        predicted_credit = credit_map[rr_exc]
        correct = (predicted_credit == gt_credit)
        IF NOT correct:
            mismatches.append({...})
        settlement_results.append({...})

    # Step 4: Compute per-exception-code precision/recall (Section 4d)
    all_results = order_results + settlement_results
    FOR each code IN all_exception_codes + [null]:
        TP, FP, FN, TN = compute_confusion(all_results, code)
        precision = TP / (TP + FP)
        recall = TP / (TP + FN)
        f1 = 2 * precision * recall / (precision + recall)
        per_code[code] = {TP, FP, FN, TN, precision, recall, f1}

    # Step 5: Compute overall metrics (Section 4a-4f)
    accuracy = count_correct / 591
    FPR = FP_total / (FP_total + true_matched)
    FNR = FN_total / (FN_total + true_exceptions)

    # Step 6: Verify match rate (Section 4g)
    report_match_rate = report_reconciled / 591
    phase5_match_rate = gt_matched / 591
    ASSERT abs(report_match_rate - phase5_match_rate) < 0.001

    # Step 7: Assert completeness
    ASSERT len(order_results) == 500
    ASSERT len(settlement_results) == 91

    # Step 8: Write output
    write_json('engine/output/metrics_report.json', metrics)
```

## 7. Edge Cases and Handling

| Edge case | How scorer handles it |
|-----------|----------------------|
| DUPLICATE_ORDER has 2 GT entries | Use exception entry as authoritative (Section 3b). GT reduces from 501 to 500 scored entries. |
| GHOST_TRANSACTION is order-level in GT but settlement-level in report | GT does not have GHOST_TRANSACTION as an order exception. GHOST is scored at settlement level only, against ground_truth_settlements.json (which says 'credited'). Report's GHOSTTransaction = correctly identifies ghost order while batch itself is credited. Verdict: CORRECT. |
| NO_CREDIT_EXPECTED in report, 'no_credit_expected' in GT settlements | Direct match after vocabulary normalization. Verdict: CORRECT. |
| NEFT_FAILED in both report and GT | Direct match. Verdict: CORRECT. |
| All 474 plain-matched orders | GT.exception_code = null, report.exception_code = null. Verdict: CORRECT. |
| 5 CURRENCY_MISMATCH + REFUND_SPLIT orders | GT.exception_code matches report.exception_code. Verdict: CORRECT. |
| 12 UNRECORDED_REFUND orders | GT.exception_code matches report.exception_code. Verdict: CORRECT. |

## 8. Expected Output

If the system is fully correct (0 mismatches), metrics_report.json should show:
```
order_level.accuracy:       1.000 (500/500)
settlement_level.accuracy:  1.000 (91/91)
overall.accuracy:           1.000 (591/591)
false_positive_rate:        0.000
false_negative_rate:        0.000
mismatches:                 [] (empty)
```

If ANY mismatch exists, the scorer lists it individually in the mismatches array with full traceability. No silent aggregation.

## 9. File Locations

| File | Direction |
|------|-----------|
| engine/metrics_scorer.py | Script |
| engine/output/metrics_report.json | Output |
| engine/output/metrics_report.md | Output (human-readable) |
| engine/output/reconciliation_report.json | Input (read-only) |
| data/raw/ground_truth.json | Input (read-only) |
| data/raw/ground_truth_settlements.json | Input (read-only) |

## 10. What Phase 5 Does NOT Do

- Does NOT re-match or re-classify any case
- Does NOT call any LLM or external API
- Does NOT modify any input file
- Does NOT introduce new exception codes or status labels
- Does NOT aggregate mismatches into anonymous counts -- every mismatch is individually listed with case_id, expected, actual, and raw data

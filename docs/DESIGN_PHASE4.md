# DESIGN_PHASE4.md -- Final Exception Categorization and Consolidated Report



## Status: Design Only -- not yet implemented

## Depends on: Phase 2 (match_log.json), Phase 3 (explanations.json)

## Produces: reconciliation_report.json



---



## 1. Purpose



Phase 4 collapses the Phase 2 confidence model and Phase 3 narration into a single,

judge-readable reconciliation report. It does NOT re-classify, re-narrate, or override

anything from Phases 2 or 3. It reads two files, maps statuses to simplified labels,

merges the data, and writes one output file.



Input files (read-only):

- engine/output/match_log.json -- 591 entries (500 orders + 91 settlements)

- agent/output/explanations.json -- 29 entries (all non-trivial cases)



Output file (write-once):

- engine/output/reconciliation_report.json

---



## 2. Simplified Status Mapping



The Phase 2 confidence model has 4 statuses. Phase 4 maps them to judge-readable labels:



Phase 4 maps the Phase 2 confidence model to judge-readable labels. The mapping is **exception_code-aware**, not a pure confidence lookup -- a "matched" order/settlement normally maps to "Reconciled", UNLESS exception_code == "NO_CREDIT_EXPECTED", in which case it maps to "Reconciled (no credit due)".

| Phase 2 confidence | exception_code | simplified_status | Orders | Settlements | Description |
|---|---|---|---|---|---|
| matched | (none or not NO_CREDIT_EXPECTED) | Reconciled | 474 | 88 | Exact match, no special handling |
| matched_with_note | REFUND_SPLIT, CURRENCY_MISMATCH | Reconciled (with note) | 5 | 0 | Special-case logic (currency conversion, cross-batch refund) |
| matched | NO_CREDIT_EXPECTED | Reconciled (no credit due) | 0 | 1 | Negative net batch, bank correctly did not credit |
| needs_review | DUPLICATE_ORDER | Needs Human Review | 1 | 0 | Conflicting ledger amounts, human must decide correct value |
| needs_review | UNRECORDED_REFUND | Needs Human Review | 12 | 0 | Ledger claims refund but no settlement refund row exists |
| needs_review | GHOST_TRANSACTION | Needs Human Review | 0 | 1 | Settlement row with no matching order in ledger |
| hard_exception | UNMATCHED_ORDER | Unresolved | 8 | 0 | Failed/authorized/captured order with no settlement |
| hard_exception | NEFT_FAILED | Unresolved | 0 | 1 | Positive net batch, bank never credited |

Ordering: Reconciled > Reconciled (with note) > Reconciled (no credit due) > Needs Human Review > Unresolved

### Mapping pseudocode (build-time reference):

    if confidence == "matched":
        if exception_code == "NO_CREDIT_EXPECTED":
            simplified_status = "Reconciled (no credit due)"
        else:
            simplified_status = "Reconciled"
    elif confidence == "matched_with_note":
        simplified_status = "Reconciled (with note)"
    elif confidence == "needs_review":
        simplified_status = "Needs Human Review"
    elif confidence == "hard_exception":
        simplified_status = "Unresolved"

Why these labels:
- Reconciled instead of Matched: emphasizes work was done and verified
- (with note) parenthetical: signals transparency, not uncertainty
- Needs Human Review instead of Pending: explicit that someone must look
- Reconciled (no credit due) is semantically opposite to Unresolved: the system correctly determined no credit should arrive
- Unresolved instead of Error: honest without implying the system broke
- UNRECORDED_REFUND and DUPLICATE_ORDER are both Needs Human Review but semantically distinct: one is a missing refund record, the other is conflicting amounts for the same order

---



## 3. Output Schema: reconciliation_report.json



Top-level structure:

    report_metadata: { ... }

    summary: { ... }

    orders: [ ... ]

    settlements: [ ... ]



### 3a. report_metadata



Fields: generated_at (ISO-8601), data_frozen_at, match_log_hash (SHA-256),

explanations_hash (SHA-256), total_orders: 500, total_settlements: 91,

phases_consumed: [Phase 2 (matcher_exact), Phase 3 (explainer)]



Hashes provide tamper-evidence: if either input file changes after Phase 4 runs,

the hashes in the report will not match the current files.



### 3b. summary



orders: { total: 500, reconciled: 474, reconciled_with_note: 5,

  needs_human_review: 1, unresolved: 8, match_rate_pct: 95.8 }

settlements: { total: 91, reconciled: 88, reconciled_no_credit_due: 1,

  needs_human_review: 1, unresolved: 1, match_rate_pct: 97.8 }

overall: { total_cases: 591, reconciled_total: 568,

  needs_human_review_total: 14, unresolved_total: 9, match_rate_pct: 96.1 }



match_rate_pct = (reconciled + reconciled_with_note + reconciled_no_credit_due) / total * 100

reconciled_with_note and reconciled_no_credit_due both count toward match rate because they

ARE successfully resolved outcomes -- the note explains special handling, and no_credit_due

is a correct system determination.

### 3c. orders[] -- one entry per order_id (500 entries)



Every order gets an entry, even the 474 plain-matched ones. Structure varies by status.



**Plain matched (474 entries) -- minimal, no explanation:**

  case_id: ord_0Avn4Yk3gLazPS7o

  case_type: order

  simplified_status: Reconciled

  exception_code: null

  explanation: null

  suggested_action: null

  key_figures: { settlement_ids, bank_utr, order_residual, refund_type }

  soft_flags: []



**Matched with note (5 entries) -- includes Phase 3 explanation:**

  case_id: ord_ohZLWumvMi5brH8l

  case_type: order

  simplified_status: Reconciled (with note)

  exception_code: REFUND_SPLIT

  explanation: [from explanations.json, verbatim]

  suggested_action: [from explanations.json]

  confidence_note: null

  key_figures: { settlement_ids, bank_utr, order_residual, refund_type }

  soft_flags: []



**Duplicate order (1 entry) -- includes conflicting rows:**

  case_id: ord_EnDJiS9HvlxNgbb1

  simplified_status: Needs Human Review

  exception_code: DUPLICATE_ORDER

  explanation: [from explanations.json]

  suggested_action: [from explanations.json]

  confidence_note: Cannot determine which amount is correct. Requires manual verification.

  key_figures: { conflicting_amounts: [1130.56, 1202.36], settlement_ids: ["set_NvO7qBhqH6y5IHWi"], bank_utr: "1845235426874470", settlement_gross_matched: 1130.56 }

  soft_flags: []



**Unrecorded refund orders (12 entries) -- includes Phase 3 explanation:**
  case_id: ord_2ozm6RqNbOW8W3nD
  simplified_status: Needs Human Review
  exception_code: UNRECORDED_REFUND
  explanation: [from explanations.json]
  suggested_action: [from explanations.json]
  confidence_note: null
  key_figures: { settlement_ids: ["set_Ug9C5dqtELc0MalO"], bank_utr: "9136817203136955",
    ledger_refund_status: "partial", ledger_refund_amount: 134.57,
    settlement_refund_deduction: 0.00 }
  soft_flags: []

**Hard exception orders (8 entries) -- includes detail string:**

  case_id: ord_u6qLtRkvl8zSWSrH

  simplified_status: Unresolved

  exception_code: UNMATCHED_ORDER

  explanation: [from explanations.json]

  suggested_action: [from explanations.json]

  confidence_note: null

  key_figures: { settlement_ids: [], bank_utr: null, order_residual: null, refund_type: null, detail: [from match_log] }

  soft_flags: []

### 3d. settlements[] -- one entry per settlement_id (91 entries)



**Plain batch_credited (88 entries) -- minimal:**

  case_id: set_72COYJjxWDkwwSC7

  case_type: settlement

  simplified_status: Reconciled

  exception_code: null

  explanation: null

  suggested_action: null

  key_figures: { bank_utr, batch_net, bank_amount, row_count, diff }

  soft_flags: []



**GHOST_TRANSACTION settlement (1 entry) -- includes ghost order list:**

  case_id: set_1E8lJ4dKfU21o9Is

  simplified_status: Needs Human Review

  exception_code: GHOST_TRANSACTION

  explanation: [from explanations.json]

  suggested_action: [from explanations.json]

  confidence_note: null

  key_figures: { bank_utr, batch_net, bank_amount, row_count, diff, ghost_order_ids }

  soft_flags: []



**NEFT_FAILED settlement (1 entry):**

  case_id: set_7oqQnmBR7evr0ci5

  simplified_status: Unresolved

  exception_code: NEFT_FAILED

  explanation: [from explanations.json]

  suggested_action: [from explanations.json]

  confidence_note: null

  key_figures: { bank_utr, batch_net: 62386.14, bank_amount: null, row_count: 5, diff: null }

  soft_flags: []



**NO_CREDIT_EXPECTED settlement (1 entry):**

  case_id: set_vlVzIbTfj7VNQanv

  simplified_status: Reconciled (no credit due)

  exception_code: NO_CREDIT_EXPECTED

  explanation: [from explanations.json]

  suggested_action: [from explanations.json]

  confidence_note: null

  key_figures: { bank_utr, batch_net: -446.18, bank_amount: null, row_count: 2, diff: null }

  soft_flags: []

---



## 4. Processing Logic



Phase 4 is a pure data-merge script. No matching, no classification, no LLM calls.



### Step 1: Load inputs

- Read match_log.json (591 entries: 500 orders + 91 settlements)

- Read explanations.json (29 entries)

- Compute SHA-256 hashes of both files for report_metadata



### Step 2: Build explanation lookup

- Index explanations.json by case_id into a dict

- This gives O(1) lookup when merging



### Step 3: Process each match_log entry

For each of the 591 entries:

1. Map confidence to simplified_status using the table in Section 2

2. Look up case_id in the explanation dict

3. If found: copy explanation, suggested_action, confidence_note from explanations.json

4. If not found (562 plain-matched): set all three to null

5. Build key_figures from match_log entry fields

6. Copy soft_flags from match_log as-is

7. Set exception_code from match_log if present, else null



### Step 4: Compute summary statistics

- Count per simplified_status, per case_type

- Compute match_rate_pct = (reconciled + reconciled_with_note + reconciled_no_credit_due) / total * 100



### Step 5: Write output

- Write reconciliation_report.json to engine/output/

- Sort orders[] by: simplified_status (Reconciled first), then case_id

- Sort settlements[] by: simplified_status, then case_id

- Clean cases first, exceptions at bottom -- natural reading order for judges

---



## 5. Integrity Rules



1. Read-only inputs: match_log.json and explanations.json are never modified.

   Phase 4 writes only to reconciliation_report.json.

2. No re-classification: simplified_status is a direct mapping from confidence.

   Phase 4 never decides whether something is reconciled or not.

3. Explanation preservation: the exact explanation text from explanations.json

   is copied verbatim -- never truncated, paraphrased, or summarized.

4. Hash provenance: report_metadata includes SHA-256 hashes of both input files.

   Any downstream consumer can verify the report was generated from specific inputs.

5. Completeness: reconciliation_report.json must have exactly 500 order entries

   and 91 settlement entries. The script should assert this and abort if mismatched.



---



## 6. Edge Cases



Case: Order with match_status=exception but confidence=matched_with_note

  Handling: simplified_status = Reconciled (with note). These ARE reconciled.



Case: DUPLICATE_ORDER conflicting_amounts

  Handling: Stored in key_figures for the human reviewer. Report does not resolve.



Case: GHOST_TRANSACTION ghost_order_ids

  Handling: Stored in key_figures. The batch IS credited -- issue is missing order, not missing payment.



Case: NO_CREDIT_EXPECTED with negative batch_net

  Handling: simplified_status = Reconciled (no credit due).

  This IS a correct, fully-explained outcome. Negative net means refund deductions

  exceeded gross, so no NEFT credit was expected. Counts toward match_rate_pct.



Case: soft_flags on plain-matched orders

  Handling: Copied through. Informational only, do not affect simplified_status.

---



## 7. File Locations



engine/

  output/

    match_log.json              # Input (read-only)

    reconciliation_report.json  # Output (Phase 4)

  reconciler.py                 # Phase 4 script (to be built)

agent/

  output/

    explanations.json           # Input (read-only)



One script: engine/reconciler.py. Reads two files, writes one. ~100-150 lines.



---



## 8. What Phase 4 Does NOT Do



- Does NOT re-match anything (Phase 2 already did that)

- Does NOT re-narrate anything (Phase 3 already did that)

- Does NOT score accuracy (Phase 5 does that against ground_truth.json)

- Does NOT produce a human-readable report (that is a separate deliverable)

- Does NOT modify match_log.json or explanations.json

- Does NOT introduce any new exception codes or status values
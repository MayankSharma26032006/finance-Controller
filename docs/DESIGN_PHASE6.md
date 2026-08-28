# DESIGN_PHASE6.md - Audit Trail

## 1. Purpose

Consolidate traceability from Phases 1-5 into one human-readable audit trail document (audit_trail.md). No new computation, no re-scoring, no modification of any prior phase's files. This is documentation for judges, not another processing step.

## 2. What an Audit Entry Looks Like

For any given case_id, the audit trail lets someone trace one case through the entire pipeline:

```
Raw Data Source  -->  Deterministic Matcher  -->  Agent Narration  -->  Simplified Status  -->  Ground Truth Score
     |                      |                         |                      |                       |
  CSVs: ledger,        match_log.json:            explanations.json:    reconciliation_       metrics_report.json:
  settlement,          confidence, exc_code,       explanation text,     report.json:           accuracy per code,
  bank statement       settlement_ids,             suggested_action,     simplified_status,     TP/FP/FN/TN
                       soft_flags                  hallucination_check   exception_code
```

Each traced example in audit_trail.md shows all 5 columns for a specific case_id, with the actual data from each file.

## 3. System-Level Audit Trail: Chain of Custody

A single table proving the entire pipeline is traceable and nothing was silently altered:

| Phase | Output File | SHA-256 Hash | Produced By |
|-------|------------|-------------|-------------|
| Frozen | data/raw/order_ledger.csv | e26d9f8cd70852bad6fa554c35340f11be8c5cefdd24366b3fd9564f8f456d5c | data generator |
| Frozen | data/raw/settlement_report.csv | 8e0953886750e1c7055c635e9118e461c3ce3ac0c64454cd02bd64091de183f2 | data generator |
| Frozen | data/raw/bank_statement.csv | 8f437d051e611216f54aa9cc705327f6c9217c15edf3e8ec8d8201d7869590bf | data generator |
| Frozen | data/raw/ground_truth.json | f2df67996c3a50cc84205829112d421e362a7a1e4254cec9301c95daf5e1ba22 | updated once (audit fix) |
| Frozen | data/raw/ground_truth_settlements.json | 853d8bfbb22bb73098433fe03fc7e708e8e03cd0fb97da9c2fd634c789352690 | updated once (audit fix) |
| Phase 2 | engine/output/match_log.json | 7584a9a94f6afeb47d1b02237449197a533dffc1053c30acc44608ffda5fcdec | matcher_exact.py |
| Phase 3 | agent/output/explanations.json | 50e3bd022f40432930fc62954cf7ab93cbb13a686a53caf355770d3ed1d4cff4 | explainer.py |
| Phase 4 | engine/output/reconciliation_report.json | a7727f1bf36bc054c6537a5a86132c42a8a07927c2182bdd0a79d42c93b06137 | reconciler.py |
| Phase 5 | engine/output/metrics_report.json | 2fd8dba9676b73c26e5338eef89a3ded4d47ff548ae39ec3fff82e3f4d57348c | metrics_scorer.py |

Each phase's report_metadata contains the SHA-256 hashes of its inputs, creating a verifiable chain. Phase 6 just concatenates them into one readable table.

**Note on hash stability:** reconciliation_report.json and metrics_report.json contain a generated_at timestamp in report_metadata, so their hash will differ slightly on every re-run even with identical underlying data -- this is expected and does not indicate a reproducibility issue. match_log.json contains no timestamps and is fully stable across runs (same hash every run). explanations.json is NOT deterministic -- Groq temperature=0 does not guarantee identical wording across runs (same facts, different phrasing). reconciliation_report.json and metrics_report.json contain timestamps. All JSON outputs use LF line endings (via open(..., newline="")) and all list fields (settlement_ids, order_ids, amounts) are explicitly sorted to ensure byte-level determinism across platforms and consecutive runs.

## 4. What Broke and How It Was Fixed

Four real bugs were found during development via external audit (human review of generated data against DESIGN.md). Each was independently verified before fixing.

### Bug 1: Duplicate Order Settlement Suppression (Claim 1)

**What was wrong:** For order ord_EnDJiS9HvlxNgbb1, which has two ledger rows with conflicting amounts (1130.56 and 1202.36), the matcher's DUPLICATE_ORDER classification short-circuited before order-level matching ever ran. This meant the real settlement row (set_NvO7qBhqH6y5IHWi, gross=1130.56) was never linked to the match_log entry -- settlement_ids was set to empty list.

**How it was caught:** Human review of match_log.json noticed settlement_ids=[] for this order, then independently verified a real settlement row exists in settlement_report.csv matching the 1130.56 amount exactly.

**How it was fixed:** Added settlement lookup logic for DUPLICATE_ORDER cases in order_matcher.py (lines 44-50). The matcher now attaches real settlement_ids/bank_utr/settlement data even when confidence stays needs_review. The explainer's case-data builder was also updated to reference the real settlement data instead of hardcoding "No settlement rows found."

**Independent verification:** Re-ran matcher_exact.py, confirmed settlement_ids=['set_NvO7qBhqH6y5IHWi'] now appears in match_log.json. Re-ran explainer.py, confirmed explanation now correctly states "settlement supports 1130.56 but 1202.36 is unverified."

### Bug 2: 12 Unrecorded Refunds (Claim 3)

**What was wrong:** 12 orders had refund_status=partial in order_ledger.csv but no corresponding refund_deduction row in settlement_report.csv. The matcher classified all 12 as "matched" with refund_type=none, completely blind to the discrepancy. This is a genuine ledger-vs-settlement mismatch that should be flagged for human investigation.

**How it was caught:** Cross-referencing ledger refund_status against settlement refund_deduction rows using an independent script. The original design (DESIGN_PHASE1.md) mentioned ~15 partial refund orders, 3 of which are REFUND_SPLIT (correctly handled). The other 12 were a gap between the design intent and the generator output.

**How it was fixed:** Added new exception_code UNRECORDED_REFUND detection in order_matcher.py (lines 206-210). After order matching, the matcher cross-references ledger refund_status against settlement refund_deduction rows and flags orders where the ledger claims a refund but no settlement refund row exists. These 12 orders were reclassified from "matched" to "needs_review."

**Independent verification:** Re-derived the 12 order_ids independently from raw CSVs using the objective rule (refund_status=partial/full AND no settlement refund_deduction row). Confirmed all 12 match exactly between the independent derivation and the matcher output.

### Bug 3: Negative-Net Batch Mislabeling (Claim 4)

**What was wrong:** Settlement batch set_vlVzIbTfj7VNQanv (net=-446.18, UTR 4299074729669417) was assigned confidence=hard_exception in batch_matcher.py, the same label as genuine failures like NEFT_FAILED. But a negative-net batch correctly produces no bank credit -- this is expected behavior, not an error.

**How it was caught:** Human review of batch_matcher.py noticed the line `confidence = "hard_exception" if batch_net < 0 else "matched"` treats all negative-net batches as exceptions.

**How it was fixed:** Changed line 75 in batch_matcher.py from `hard_exception` to `matched` for negative-net batches. The batch still carries exception_code=NO_CREDIT_EXPECTED for traceability, but its confidence is now correctly "matched."

**Independent verification:** Re-ran matcher_exact.py, confirmed set_vlVzIbTfj7VNQanv now has confidence=matched. Re-ran metrics_scorer.py, confirmed it scores correctly as "Reconciled (no credit due)" against ground_truth_settlements.json.

### Claim 2 (Refuted): Expected Residual Formula

**What was claimed:** The expected_residual for REFUND_SPLIT cases does not match order_residual, and this was suspected to be a formula bug.

**What was found:** The field is computed-but-unused in matching logic. expected_residual measures gross-based retained value (orig_gross - refund_amount), while order_residual measures net-of-fees sum. They measure different things by design. The code never compares them. No bug -- confirmed not a fix.

## 5. One Failure Handled Gracefully: NEFT_FAILED

This is a real-world-relevant failure case: a Razorpay settlement batch was supposed to credit the merchant via NEFT, but the bank never received the funds.

**The case:** set_7oqQnmBR7evr0ci5

**What happened:**
- 5 orders totalling 62,386.14 INR were settled in this batch
- The expected bank UTR was 9503100649340391
- The bank statement shows NO entry for this UTR -- the credit never arrived
- Phase 2 correctly identified this as a hard_exception (NEFT_FAILED)
- Phase 3 narrated: "The settlement batch was supposed to credit 62,386.14 via NEFT... the bank statement shows no entry for that UTR, and the batch is flagged with the exception NEFT_FAILED"
- Phase 4 classified as "Unresolved" (correct -- this needs human escalation to bank ops)
- Phase 5 scored as correct against ground truth (ground_truth_settlements.json has expected_bank_credit_status=NEFT_FAILED)

**Why this matters:** This is exactly the kind of exception the system is designed to catch. Without automated reconciliation, a missing 62K INR credit could go unnoticed for days. The system flagged it immediately, provided a human-readable explanation, and correctly escalated it for bank ops investigation.

## 6. Fully-Traced Example Cases

### Example 1: Clean Match (ord_0Avn4Yk3gLazPS7o)

| Phase | Data |
|-------|------|
| Raw data | order_ledger: 168,388.39 INR, payment_status=captured |
| Phase 2 (match_log) | confidence=matched, settlement_ids=[set_XSbG5A1ROwkHLHyD], bank_utr=4893326145430796 |
| Phase 3 (explanation) | Not narrated (plain match, below threshold) |
| Phase 4 (reconciliation) | simplified_status=Reconciled, exception_code=null |
| Phase 5 (metrics) | Scored as correct. none-class TP += 1 |
| Ground truth | expected_match_status=matched, exception_code=null |

Trace: The order was captured, settled in batch set_XSbG5A1ROwkHLHyD, credited to bank with UTR 4893326145430796. All three sources agree. System confidence: matched. Human review needed: none.

### Example 2: Exception - DUPLICATE_ORDER (ord_EnDJiS9HvlxNgbb1)

| Phase | Data |
|-------|------|
| Raw data | Two ledger rows: 1130.56 and 1202.36 for same order_id, same timestamp |
| Phase 2 (match_log) | confidence=needs_review, exception_code=DUPLICATE_ORDER, settlement_ids=[set_NvO7qBhqH6y5IHWi] |
| Phase 3 (explanation) | Two identical ledger rows except for gross amounts 1130.56 and 1202.36. Settlement shows only the 1130.56 entry... 1202.36 has no corresponding settlement. Cannot determine which is correct. |
| Phase 4 (reconciliation) | simplified_status=Needs Human Review, exception_code=DUPLICATE_ORDER |
| Phase 5 (metrics) | Scored as correct. DUPLICATE_ORDER TP += 1 |
| Ground truth | expected_match_status=exception, exception_code=DUPLICATE_ORDER |

Trace: Two conflicting amounts for one order. Settlement supports 1130.56 only. System correctly refuses to guess, flags for human review. Agent explains the ambiguity with specific amounts.

### Example 3: Failure - NEFT_FAILED (set_7oqQnmBR7evr0ci5)

| Phase | Data |
|-------|------|
| Raw data | settlement_report: 5 orders, net=62,386.14, UTR=9503100649340391. bank_statement: no entry for this UTR |
| Phase 2 (match_log) | confidence=hard_exception, exception_code=NEFT_FAILED |
| Phase 3 (explanation) | Settlement batch was supposed to credit 62,386.14 via NEFT... bank statement shows no entry for that UTR |
| Phase 4 (reconciliation) | simplified_status=Unresolved, exception_code=NEFT_FAILED |
| Phase 5 (metrics) | Scored as correct. NEFT_FAILED TP += 1 |
| Ground truth | expected_bank_credit_status=NEFT_FAILED, net_amount_expected=62386.14 |

Trace: NEFT credit never arrived. System correctly identified the missing bank credit, flagged as unresolved, recommended escalation to bank ops. This is a genuine money-action failure the system was built to catch.

## 7. Output

A single file: **audit_trail.md** at project root. Human-readable markdown. Contains:
- Section 2: Pipeline flow diagram
- Section 3: Chain-of-custody hash table (all 9 files)
- Section 4: Bug-fix narrative (4 items, with before/after evidence)
- Section 5: NEFT_FAILED graceful-failure example
- Section 6: Three fully-traced cases

## 8. File Locations

| File | Direction |
|------|-----------|
| audit_trail.md | Output (at project root) |

All other files are read-only inputs from prior phases. No engine/ script is needed -- this is a documentation-only phase, written by hand (or by a simple write script) from verified data already on disk.

## 9. Hash Generation Rule

The chain-of-custody table in Section 3 MUST be generated by a script that reads each file and computes its SHA-256 live at generation time -- never hand-transcribed or copied from prior conversation output. This prevents hash mismatches from stale intermediate values.

Implementation: audit_trail.md should include a short Python snippet at the top (or a separate generate_audit.py script) that iterates over all 9 files, computes SHA-256, and writes the table. The hashes in the design doc are reference values for verification, not the source of truth.

## 10. What Phase 6 Does NOT Do

- Does NOT re-match, re-score, or re-classify any case
- Does NOT call any LLM or external API
- Does NOT modify any file from Phases 1-5
- Does NOT introduce new exception codes, status labels, or metrics
- Does NOT hardcode hashes -- they are computed live from the files

# DESIGN_PHASE2.md - Deterministic Matching Engine

Scope: Exact and tolerance-based matching across 3 data sources.
Does NOT cover agent reasoning (Phase 3) or final exception categorization (Phase 4).

---

## 1. Input Data (verified from data/raw/)

| File | Rows | Role |
|------|------|------|
| order_ledger.csv | 501 | Merchant internal record: orders, amounts, payment methods |
| settlement_report.csv | 504 | Razorpay view: fees, GST, batching, refund deductions |
| bank_statement.csv | 100 | Bank view: NEFT credits with UTRs, no order details |
| ground_truth.json | 501 | Per order_id: match status, expected settlement_ids, exception_code |
| ground_truth_settlements.json | 91 | Per settlement_id: expected bank credit status |

Key structural facts:
- 91 unique settlement_ids, each with one bank_utr
- 91 unique bank_utrs in settlement_report; 99 unique utrs in bank_statement (includes noise)
- 504 settlement rows for 493 distinct orders (some orders have 2 rows: original + refund deduction)
- 11 refund deduction rows across 10 batches
- 11 orders span multiple settlement_ids (cross-batch refund splits)
- 1 ghost transaction (settlement row with order_id not in ledger)
- 189 of 492 shared orders have payment_method label mismatch
- 12/91 batches have rounding drift (max +/-0.02)
- 2 USD orders converted at 1 USD = Rs 83.00

---

## 2. Matching Philosophy: Batch-First, Then Order

The bank statement never sees individual orders. It only sees NEFT credits
identified by UTR, where each UTR corresponds to one settlement batch
(aggregating multiple orders). Therefore the matching MUST proceed top-down:

    Layer 1: settlement_id <-> bank UTR   (batch-level)
    Layer 2: order_id <-> settlement_id   (order-level within matched batches)
    Layer 3: order_id <-> ledger          (confirm order exists and is consistent)

Attempting order-by-order matching directly against the bank statement would
fail because a bank credit of Rs 88,022.53 (UTR 1003076205459671) corresponds
to 6 orders in batch set_RTP6kuwRwviITZ8T, not one.

---

## 3. Matching Algorithm: Step by Step

### Step 0: Preprocessing (before any matching)

#### 0a. Label Normalization

Payment method labels differ between ledger and settlement. Apply this
alias map to settlement_report.payment_method BEFORE any comparison:

    LEDGER_TO_SETTLEMENT_LABEL:
      visa_mc_domestic  ->  card
      amex_diners       ->  amex
      international_card -> intl_card
      upi               ->  upi  (already matches)

Real examples from the data:
  - ord_ZKgJypW5wBMZX0mB: ledger=visa_mc_domestic, settlement=card
  - ord_5s0IgzAZJE8TLrhU: ledger=international_card, settlement=intl_card
  - ord_LnvuE9Oo4evYnMCc: ledger=amex_diners, settlement=amex

After normalization, all 492 shared orders should have matching payment
methods. This is NOT an exception - it is a known preprocessing step.
Any remaining label differences after normalization WOULD be an exception.

#### 0b. Currency Normalization

2 orders use USD in the ledger but INR in settlement:
  - ord_0EE1Z6jjCFojTQpT: ledger=424.24 USD, settlement=35,211.92 INR
  - ord_bhS8ygpi5G2Q3uJR: ledger=341.91 USD, settlement=28,378.53 INR

Rule: When ledger.currency == "USD", convert ledger.gross_amount to INR
using FX_RATE = 83.00 before comparing to settlement.gross_amount.
The settlement and bank always store INR.

#### 0c. Amount Field Definitions

Different files use gross_amount differently:
  - ledger.gross_amount: what the customer paid (original currency)
  - settlement.gross_amount: what Razorpay received (INR, post-conversion if USD)
  - settlement.net_amount: gross - fee - gst + refund_deduction (per row)
  - bank.amount: the aggregated NEFT credit for the whole batch

The matcher should compare:
  - Ledger vs Settlement: compare ledger.gross_amount (converted) to settlement.gross_amount
  - Settlement vs Bank: compare sum(settlement.net_amount) per batch to bank.amount

### Step 1: Batch-Level Matching (settlement <-> bank)

For each settlement_id in settlement_report.csv:

1. Collect all rows with that settlement_id
2. Compute batch_net = sum(float(row["net_amount"]) for all rows in batch)
3. Extract bank_utr = rows[0]["bank_utr"] (all rows in a batch share the same UTR)
4. Look up bank_utr in bank_statement.csv (filter to txn_type == "credit")
5. Match if: |bank.amount - batch_net| <= 0.05 (rounding tolerance)

Real example:
  Batch set_RTP6kuwRwviITZ8T:
    6 rows, gross_sum=90,568.69, fee_sum=2,157.76, gst_sum=388.39
    net_sum = 88,022.53
    Bank credit: UTR 1003076205459671, amount=88,022.53
    Diff = 0.00 -> MATCHED

  Batch set_7oqQnmBR7evr0ci5:
    5 rows, net_sum = 62,386.14
    UTR 9503100649340391 NOT in bank_statement
    -> NEFT_FAILED (per ground_truth_settlements.json)

  Batch set_vlVzIbTfj7VNQanv:
    2 rows, net_sum = -446.18 (negative, refund deductions exceed gross)
    UTR 4299074729669417 NOT in bank_statement
    -> no_credit_expected (negative net, credit correctly skipped)

Classification after Step 1:
  - "batch_credited": UTR found in bank, amount within tolerance
  - "batch_neft_failed": UTR not in bank, but net_amount > 0 (should have been credited)
  - "batch_no_credit": UTR not in bank, net_amount <= 0 (credit correctly skipped)

### Step 2: Order-Level Matching (ledger <-> settlement)

For each order_id in order_ledger.csv:

2a. Find all settlement rows where settlement.order_id matches.
    Use the RAW order_id (before any normalization) for the join.

    Example: ord_4dzvdm4BNJ6lawOH has 2 settlement rows:
      - set_V4wg4sqjavfNXiFk: gross=734.57, fee=14.69, gst=2.64, refund=0.00, net=717.24
      - set_C7Op2x2IWgEccBkA: gross=0.00, fee=0.00, gst=0.00, refund=-734.57, net=-734.57

2b. Classify the order based on its settlement rows:

    RULE: payment_status check first
      - If ledger.payment_status == "failed": NO settlement rows expected
        -> Exception: UNMATCHED_ORDER
        Real example: ord_nBRNtp3FaBNfBMoh (failed, 0 settlement rows)

      - If ledger.payment_status == "authorized": NO settlement rows expected
        -> Exception: UNMATCHED_ORDER (settles next cycle)
        Real example: orders at indices 5-6 in the ledger

    RULE: settlement presence check
      - If captured but 0 settlement rows -> Exception: UNMATCHED_ORDER
        Real example: ord_u6qLtRkvl8zSWSrH (captured, missing from settlement)

      - If 1+ settlement rows exist -> continue to amount validation

2c. Amount validation for matched orders:

    For each settlement row with gross_amount > 0 (the original credit):
      - If ledger.currency == "USD": compare ledger.gross_amount * 83.00 to settlement.gross_amount
      - Else: compare ledger.gross_amount to settlement.gross_amount
      - Tolerance: exact match expected (both are 2dp values)
      - If mismatch > 0.01: flag as AMOUNT_MISMATCH (not expected in current data)

    For each settlement row with gross_amount == 0 and refund_deduction < 0:
      - This is a refund row, not a new charge
      - See Section 4 for refund classification

### Step 3: Refund Classification (within matched orders)

#### 3a. Full Refund Detection

When an order has exactly 2 settlement rows where:
  - Row 1: gross_amount > 0 (original charge)
  - Row 2: gross_amount == 0, refund_deduction < 0 (refund deduction)

And |refund_deduction| == Row1.gross_amount (the FULL gross, not net):

  -> Classification: FULL_REFUND

  Real example: ord_4dzvdm4BNJ6lawOH
    Row 1 (set_V4wg4sqjavfNXiFk): gross=734.57, fee=14.69, gst=2.64, net=717.24
    Row 2 (set_C7Op2x2IWgEccBkA): gross=0.00, refund=-734.57, net=-734.57
    |refund| = 734.57 == Row1.gross = 734.57 -> FULL_REFUND

  Residual after summing both rows:
    Row1.net + Row2.net = 717.24 + (-734.57) = -17.33
    This equals -(fee + gst) = -(14.69 + 2.64) = -17.33
    The merchant loses the fees (non-refundable MDR). This is NOT an error.

  IMPORTANT: Do not flag the -17.33 residual as unexplained variance.
  The matcher must compute: order_residual = sum(all settlement nets for this order).
  For full refunds: order_residual == -(original_fee + original_gst).
  This is expected behavior, not a discrepancy.

#### 3b. Partial Refund Split Detection

When an order has 2+ settlement rows spanning different settlement_ids,
and the refund deduction magnitude is LESS than the original gross:

  -> Classification: REFUND_SPLIT

  Real example: ord_ohZLWumvMi5brH8l
    Row 1 (set_V4wg4sqjavfNXiFk): gross=14,802.45, net=14,453.11
    Row 2 (set_ZxZYr1ol75jKh16f): gross=0.00, refund=-2,558.65, net=-2,558.65
    |refund| = 2,558.65 < gross = 14,802.45 -> PARTIAL_REFUND_SPLIT

  The matcher must link both settlement_ids to the same order_id.
  Use settlement.captured_date to establish temporal ordering:
    Row1.captured_date should be <= Row2.captured_date.

#### 3c. Normal Full Refund (not an exception)

Full refunds (indices 36-43 in the generator) are NOT exceptions.
They span 2 batches (normal Razorpay behavior) and are classified
as "matched" in ground_truth.json. The matcher should confirm:
  - Both rows exist in settlement_report
  - |refund_deduction| == original gross_amount
  - Residual == -(fee + gst) from original row

### Step 4: Ledger-Only Exception Detection

Orders that are in the ledger but have NO settlement rows and are NOT
failed/authorized:

  - Check: is this the known missing settlement order?
    Real example: ord_u6qLtRkvl8zSWSrH
    -> Exception: UNMATCHED_ORDER (captured but missing from settlement)

  - Check: is this a duplicate order_id?
    Real example: ord_EnDJiS9HvlxNgbb1 appears twice with different amounts
    -> Exception: DUPLICATE_ORDER (both rows should be flagged)

### Step 5: Settlement-Only Exception Detection

Settlement rows whose order_id does NOT appear in order_ledger.csv:

  Real example: ord_HPM8Q5WQdYvGi5l7 in batch set_1E8lJ4dKfU21o9Is
  -> Exception: GHOST_TRANSACTION

  The matcher should flag these for Phase 3 agent review, since they
  represent Razorpay settlement activity with no matching internal order.

### Step 6: Cross-Source Consistency Check

For orders that are matched across all 3 sources, verify:
  - ledger.payment_method (normalized) == settlement.payment_method
  - settlement.settlement_date is within 3 working days of ledger.order_date
  - settlement.captured_date == ledger.order_date
  - bank.txn_date is within 1 working day of settlement.settlement_date (T+1 rule)

Any violation here is a data quality flag for Phase 3, not a hard exception.

---

## 4. Two-Layer Exception Model

### Layer 1: Order-Level Exceptions (scored against ground_truth.json)

| Exception Code | Meaning | Expected Count | Detection Method |
|---------------|---------|----------------|------------------|
| UNMATCHED_ORDER | Order not in settlement (failed/auth/missing) | 8 | ledger exists, 0 settlement rows, payment_status != captured OR captured but missing |
| REFUND_SPLIT | Refund deduction in different batch than original | 3 | 2+ settlement_ids, refund_deduction < 0, |refund| < gross |
| CURRENCY_MISMATCH | USD order, requires conversion before matching | 2 | ledger.currency == USD, amount differs until converted |
| DUPLICATE_ORDER | Same order_id appears twice in ledger | 1 | order_id_count > 1 in ledger |

Orders that ARE matched (no exception code):
  - Normal captured orders: 1 row in settlement, amounts match
  - Full refund orders: 2 rows across 2 batches, residual = -(fee+gst)
  - Near-cutoff orders: normal match, just ambiguous batch boundary
  - Partial refund (same batch): 2 rows in same batch, amounts correct

### Layer 2: Settlement-Level Exceptions (scored against ground_truth_settlements.json)

| Status Code | Meaning | Expected Count | Detection Method |
|------------|---------|----------------|------------------|
| batch_credited | UTR found in bank, amount within tolerance | 89 | bank.utr match, |bank.amount - batch_net| <= 0.05 |
| batch_neft_failed | UTR not in bank, positive net (should have been credited) | 1 | UTR missing from bank, batch_net > 0 |
| batch_no_credit | UTR not in bank, negative net (credit correctly skipped) | 1 | UTR missing from bank, batch_net <= 0 |

---

## 5. Double-Counting Prevention

The biggest risk in batch-first matching is attributing the same settlement
row to multiple orders or counting refund deductions as new charges.

Rules to prevent double-counting:

1. ORDER_ID IS THE JOIN KEY for settlement rows. Each settlement row has
   exactly one order_id. When grouping by settlement_id for batch net
   calculation, each row is counted exactly once.

2. REFUND DEDUCTION ROWS have gross_amount == 0 and refund_deduction < 0.
   They must NOT be counted as new charges. When computing batch_net:
     batch_net = sum(float(row["net_amount"]) for row in batch_rows)
   This naturally handles refunds because their net_amount is negative.

3. GHOST TRANSACTIONS have an order_id not in the ledger. They are
   counted in batch_net (they affect the bank credit amount) but are
   flagged as settlement-level exceptions, not attributed to any order.

4. CROSS-BATCH REFUND SPLITS: when an order appears in 2 batches
   (original + refund deduction), the refund row contributes negative
   net to its batch. The batch_net still sums correctly because:
     - Batch A (original): includes positive net for the order
     - Batch B (refund): includes negative net for the refund deduction
   Both batches are independently matched to their respective bank credits.

5. ROUNDING DRIFT: 12/91 batches have drift up to +/-0.02. The tolerance
   of +/-0.05 in Step 1 ensures these are still matched. The drift is
   recorded but NOT flagged as an exception.

---

## 6. Matching Order of Operations (Summary)

    PREPROCESS
      0a. Normalize payment_method labels (ledger -> settlement alias map)
      0b. Convert USD amounts to INR (FX_RATE = 83.00)
      0c. Parse all amounts to float with 2dp precision

    LAYER 1: BATCH MATCHING
      1. Group settlement_report rows by settlement_id
      2. For each batch: compute bank_utr, batch_net (sum of net_amount)
      3. For each batch: look up bank_utr in bank_statement (credit rows only)
      4. Classify: batch_credited / batch_neft_failed / batch_no_credit
      5. Record batch_match_result per settlement_id

    LAYER 2: ORDER MATCHING
      6. For each order_id in ledger:
         a. Check payment_status -> if failed/authorized, expect 0 settlement rows
         b. Find all settlement rows by order_id
         c. If 0 rows and captured -> UNMATCHED_ORDER
         d. If 1+ rows -> validate amounts (with currency conversion)
         e. Classify refund type (full/partial/none)
         f. Compute order_residual = sum(all settlement nets for this order)
         g. For full refunds: confirm residual == -(fee + gst)
         h. Record order_match_result per order_id

    LAYER 3: CROSS-VALIDATION
      9. For matched orders: verify settlement batch was also matched in Layer 1
      10. For unmatched orders: confirm they are NOT in any matched batch
      11. Flag ghost transactions (settlement rows with unknown order_ids)
      12. Check date tolerances (settlement_date vs order_date, bank txn_date vs settlement_date)

    OUTPUT
      13. Compile match results into structured output:
          - Per-order: match_status, settlement_ids, exception_code (if any)
          - Per-settlement: match_status, bank_utr, exception_code (if any)
          - Summary: total matched, total exceptions, match rate

---

## 7. Confidence Levels (4-Status Model)

Each order receives one of four confidence statuses. These are independent of `match_status` ("matched"/"exception") and `exception_code`.

### "matched" — plain reconciliation, no special logic
  - Order has 1 settlement row, amounts match exactly (after currency conversion)
  - Batch has bank credit, amount within +/-0.05
  - Label normalization resolves all payment_method differences
  - Full refund orders where residual == -(fee + gst)
  - No exception_code set, no special-case logic needed
  - Expected count: ~486 orders

### "matched_with_note" — reconciliation succeeded, but required special-case logic
  - REFUND_SPLIT: refund deduction spans 2 batches, amounts match after cross-batch linking
  - CURRENCY_MISMATCH: USD order converts to matching INR amount at FX_RATE=83.00
  - exception_code IS set (REFUND_SPLIT, CURRENCY_MISMATCH) to distinguish from plain matched
  - The reconciliation math is correct and resolved — no human/agent intervention neededn  - But it required special-case logic, so it stays distinguishable from a plain 1:1 exact match
  - Expected count: 5 orders (3 REFUND_SPLIT + 2 CURRENCY_MISMATCH)

### "needs_review" — requires human or agent judgment
  - DUPLICATE_ORDER: order_id appears multiple times with conflicting amounts
  - GHOST_TRANSACTION: settlement row with unknown order_id
  - Orders with AMOUNT_MISMATCH > 0.01 (not expected in current data)
  - Date tolerance violations (settlement_date > order_date + 5 working days)
  - Batch net differs from bank credit by > 0.05 but < 1.00 (ambiguous rounding)
  - Expected count: 1 order + 1 batch (ghost transaction)

### "hard_exception" — no match possible
  - UNMATCHED_ORDER: captured order with no settlement rows (8 orders: 5 failed + 2 authorized + 1 missing)
  - batch_neft_failed: settlement batch with no bank credit despite positive net
  - batch_no_credit: negative net batch (correctly not credited, but still an edge case)
  - Expected count: 8 orders + 2 batches

### Two-axis model

| Axis | Values | Meaning | Phase 5 comparison target |
|------|--------|---------|---------------------------|
| `match_status` | "matched" / "exception" | Category tag | Compare to `expected_match_status` in ground_truth.json |
| `confidence` | "matched" / "matched_with_note" / "needs_review" / "hard_exception" | Operational outcome | NOT compared to ground_truth — this is the matcher's own classification |

An order can have match_status="exception" + confidence="matched_with_note" when special-case logic succeeds (REFUND_SPLIT, CURRENCY_MISMATCH). Phase 5 scoring should compare match_status to expected_match_status, NOT confidence to expected_match_status.

---

## 8. Expected Output Metrics (against ground truth)

After matching, compare results to ground_truth.json:
  - Order-level precision/recall for each exception code
  - Settlement-level accuracy for credit_status classification
  - Overall match rate (expected: ~98.2% orders matched or matched_with_note, ~98% batches credited)

The matcher should produce a machine-readable match_log (list of dicts)
that Phase 4 (metrics engine) can compare against both ground truth files.


---

## Addendum: Post-Build Fixes (4 Issues Investigated and Resolved)

**Date:** Post-Phase 2 build, after investigation of 4 claimed issues.

### Fix 1: DUPLICATE_ORDER - Attach Real Settlement Data (order_matcher.py)

**Problem:** The DUPLICATE_ORDER classification short-circuited before order-level matching, producing `settlement_ids: []` and `bank_utr: None` even though a real settlement row existed for `ord_EnDJiS9HvlxNgbb1`.

**Fix:** Added settlement lookup for duplicate orders. The entry now includes real `settlement_ids` and `bank_utr` when settlement rows exist, while keeping `confidence: needs_review` (human must still decide which conflicting amount is correct).

**Before:** `settlement_ids: [], bank_utr: None`
**After:** `settlement_ids: ['set_NvO7qBhqH6y5IHWi'], bank_utr: 1845235426874470`

### Fix 2: expected_residual - No Fix Needed (CONFIRMED NOT A BUG)

**Investigation result:** `expected_residual` is computed but never compared against `order_residual` in any conditional branch, assertion, or confidence assignment. It's used only in a cosmetic detail string (FULL_REFUND cases) and as LLM context data. For REFUND_SPLIT cases, the two numbers measure different things (net-of-fees vs gross-based) and the code never asserts they should match.

### Fix 3: UNRECORDED_REFUND - New Exception Code (order_matcher.py)

**Problem:** 12 orders with `refund_status=partial` in the ledger have no corresponding `refund_deduction` row in the settlement report. The matcher classified all 12 as plain `matched` with `refund_type=none`, blind to the discrepancy.

**Fix:** Added `UNRECORDED_REFUND` exception code. After refund classification, if `refund_status` is `partial` or `full` AND `refund_type == "none"` AND `refund_amount > 0`, the order gets `confidence: needs_review` with a descriptive detail.

**Count:** 12 orders reclassified from `matched` to `needs_review`.

### Fix 4: Negative-Net Batch Confidence (batch_matcher.py)

**Problem:** `set_vlVzIbTfj7VNQanv` (batch_net=-446.18) was assigned `confidence: hard_exception` even though the detail said "credit correctly skipped" -- the behavior was correct but the label contradicted it.

**Fix:** Changed confidence from `hard_exception` to `matched` for all negative-net batches. The `status: batch_no_credit` and derived exception code `NO_CREDIT_EXPECTED` remain for traceability, but the batch is no longer flagged as a problem.

### Updated Status Breakdown

**Order-Level (500 orders):**

| Status | Before | After | Delta |
|--------|--------|-------|-------|
| matched | 486 | 474 | -12 |
| matched_with_note | 5 | 5 | 0 |
| needs_review | 1 | 13 | +12 |
| hard_exception | 8 | 8 | 0 |
| **TOTAL** | **500** | **500** | **0** |

Exception codes: CURRENCY_MISMATCH(2), DUPLICATE_ORDER(1), REFUND_SPLIT(3), UNMATCHED_ORDER(8), UNRECORDED_REFUND(12)

**Settlement-Level (91 batches):**

| Status | Before | After | Delta |
|--------|--------|-------|-------|
| batch_credited | 89 | 89 | 0 |
| batch_neft_failed | 1 | 1 | 0 |
| batch_no_credit | 1 | 1 | 0 |

Confidence: matched(88->89), hard_exception(2->1), needs_review(1->1)

**Match Rate Recomputation:**
- Orders: (474 + 5) / 500 = 479/500 = **95.8%** (was 98.2%)
- Settlements: 89/91 = **97.8%** (unchanged)
- Overall: (479 + 89) / 591 = 568/591 = **96.1%** (was 98.1%)

Note: The 12-point drop in order match rate is correct -- those 12 orders genuinely have an unresolved ledger-vs-settlement discrepancy that was previously being silently ignored.

### Phase 3 Impact

**Affected existing cases:**
- `ord_EnDJiS9HvlxNgbb1` (DUPLICATE_ORDER): Now has real settlement_ids/bank_utr. Existing explanation premise ("no settlement rows found") is now inaccurate -- needs re-explanation.
- `set_vlVzIbTfj7VNQanv` (NO_CREDIT_EXPECTED): Confidence changed from hard_exception to matched, but explanation content is still correct (the reasoning about why no credit is expected has not changed).

**New cases needing explanations (12):**
All `UNRECORDED_REFUND` orders are new needs_review cases requiring LLM explanation. These did not exist in the previous 17-case set.

**Total Phase 3 cases after fix:** 17 (existing) + 12 (new UNRECORDED_REFUND) - 0 (none removed) = **29 cases**

match_log.json hash (post-fix): computed before Phase 3 regeneration.

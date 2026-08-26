# DESIGN.md - Razorpay Reconciliation Buildathon (Phase 0 + Phase 1)

> **Track:** AI Finance Controller - Razorpay AI Buildathon
> **Scope:** Phase 0 (research) + Phase 1 (schema design). No matching logic, agent reasoning, or UI yet.

---

## Phase 0 - How Razorpay Settlement *Actually* Works

### 0.1 Fee Structure (MDR by Payment Method)

Razorpay publishes a **2% + 18% GST** blended platform fee. The actual MDR breakdown varies by instrument:

| Payment Method | Platform / MDR Fee | GST (18% on fee) | Effective Cost on Rs 10,000 |
|---|---|---|---|
| **UPI (bank-to-bank)** | 0% MDR (RBI mandate) but **2% platform fee** | 18% on Rs 200 = Rs 36 | Rs 236 |
| **RuPay Debit Card** | 0% MDR (Zero MDR mandate) but **2% platform fee** | 18% on Rs 200 = Rs 36 | Rs 236 |
| **Domestic Visa/MC Debit** | 2% platform fee | 18% on Rs 200 = Rs 36 | Rs 236 |
| **Domestic Visa/MC Credit** | 2% platform fee | 18% on Rs 200 = Rs 36 | Rs 236 |
| **International Cards** | 3% + FX markup (0.5-1%) | 18% on combined fee | Higher |
| **Amex / Diners** | 2.95-3% | 18% on combined fee | Higher |
| **UPI on RuPay Credit Card** | 1.6-2% | 18% on fee | Variable |
| **Wallets / Pay Later** | 2% platform fee | 18% on fee | Rs 236 on Rs 10K |

**Key insight for synthetic data:** We simplify to three tiers for realism without combinatorial explosion:

- **Tier 1 - UPI / RuPay Debit:** 0% MDR + 2% platform fee + 18% GST on platform fee
- **Tier 2 - Domestic Cards:** 2% MDR/platform fee + 18% GST on fee
- **Tier 3 - International Cards:** 3% MDR + 18% GST on fee

The formula is always: `net = gross - fee - (fee x 0.18)`

### 0.2 Settlement Cycle Timing

| Parameter | Value |
|---|---|
| **Standard cycle** | T+2 (two *working* days from capture date) |
| **T+1 available** | Yes, for merchants with strong history |
| **Instant (T+0)** | Opt-in; charges 0.25-0.50% extra per transaction |
| **Cut-off time** | Fixed daily cut-off (~5:00 PM IST). Payments captured after cut-off roll into next day batch |
| **Working days** | Excludes weekends (Sat/Sun) and bank holidays |
| **NEFT payout** | Settlements arrive as NEFT credits to the registered bank account |
| **Partial settlements** | If live balance < scheduled amount, Razorpay settles what is available and defers the rest |

**Timing messiness to model:**
- Friday 5:30 PM capture leads to Tuesday settlement (weekend + T+2)
- Holiday on Wednesday leads to settlement delay
- Different capture times in a single day lead to split batching

### 0.3 Batching Behavior

This is the core reconciliation challenge. Razorpay does **not** settle 1:1 with orders.



**Batching rules:**
1. All captured payments within a settlement window are batched together
2. Refunds processed against those orders are **deducted from the batch** (negative line items)
3. The batch produces a **single NEFT credit** with one UTR to the merchant bank
4. The settlement report lists individual transaction rows that sum to the batch total
5. The **settlement_id** is the canonical match key - *not* the bank UTR


### 0.4 Refund Mechanics

| Refund Property | Behavior |
|---|---|
| **When deducted** | Refunds are deducted from *future* settlement batches (5-7 working days after refund initiation) |
| **MDR on refunds** | **Non-refundable.** Merchant absorbs the original MDR + GST even after full refund |
| **Partial refund** | Only a portion returned to customer; MDR stays on the *original full amount*, so effective fee rate on retained revenue is higher |
| **Settlement report appearance** | Refund appears as a **negative amount** against the original Order ID |
| **Refund splitting** | One partial refund on one order can land in a different settlement batch than the original transaction |

**Example - partial refund split across settlements:**

    Day 0:  Order E 20,000 captured -> settled in Batch 1 (20,000 gross)
    Day 3:  Partial refund 8,000 initiated on Order E
    Day 5:  Batch 1 already settled. Refund deduction appears in Batch 3:
            Order E: -8,000 as a line item in Batch 3

Now the order gross appears in Batch 1 and its refund deduction appears in Batch 3. **One order spans two settlement batches.** This is the single hardest reconciliation edge case.

### 0.5 Key IDs and Identifiers

| ID | Scope | Format (synthetic) | Notes |
|---|---|---|---|
| order_id | Merchant system + Razorpay | ord_XXXXXXXXXXXXXX | Primary order identifier |
| payment_id | Razorpay | pay_XXXXXXXXXXXXXX | One order can have multiple payments (retries) |
| settlement_id | Razorpay | set_XXXXXXXXXXXXXX | Batch identifier; appears in settlement report |
| bank_utr | Bank statement | 16-digit NEFT ref | Issued by correspondent bank, *not* Razorpay |
| refund_id | Razorpay | rfn_XXXXXXXXXXXXXX | Links refund to original payment_id |

### 0.6 GST Compliance Detail

- GST at **18%** is applied on the MDR/platform fee component only
- Razorpay issues a **monthly GST tax invoice** for total MDR charged
- Merchants can claim **Input Tax Credit (ITC)** on the GST-on-MDR component
- The monthly GST invoice total must match the sum of GST deductions across all settlement reports for that month
- **E-commerce TCS** (1% under Section 52 CGST) applies only to marketplace/platform models - not relevant for direct merchants

### 0.7 International Currency Conversion

Razorpay settles **all** payments in INR, regardless of the customer payment currency. For international card payments:

1. The customer pays in their card currency (e.g., USD)
2. Razorpay converts to INR at the processing bank exchange rate on the date of payment capture
3. A **0.5-1% FX markup** is added over the interbank rate
4. The INR-converted amount (called `base_amount` in the Razorpay API) is used for fee calculation and settlement
5. Settlement arrives in INR to the merchant bank account

**For synthetic data, we fix the conversion rate to eliminate floating-point ambiguity:**

| Parameter | Value |
|---|---|
| **Fixed FX rate** | 1 USD = Rs 83.00 |
| **FX markup** | 0.5% (baked into the Rs 83 rate) |
| **Fee calculation base** | INR base_amount, not original USD |
| **Example** | Customer pays $249.99 -> base_amount = Rs 20,749.17 (249.99 x 83) -> fee at 3% = Rs 622.47 -> GST = Rs 112.04 -> net = Rs 20,014.66 |

**Cross-dataset visibility of USD orders:**

| Dataset | Stores | Shows |
|---|---|---|
| Dataset A (Order Ledger) | USD amount in gross_amount, USD in currency | The customer-facing amount the merchant recorded |
| Dataset B (Settlement Report) | INR base_amount in gross_amount, INR in all amounts | The converted amount Razorpay used for settlement |
| Dataset C (Bank Statement) | INR only | The NEFT credit in INR |

This means the same order shows **different numeric values** in Dataset A vs Dataset B/C. The reconciliation engine must detect the currency field, look up the conversion rate, and compute the INR equivalent before matching.


---

## Phase 1 - Synthetic Data Schema Design

### Design Principles

1. **Realistic volume:** ~500 orders over a 2-week window (manageable for a buildathon demo)
2. **Deliberate messiness:** Every messy edge case has a named, traceable origin
3. **Deterministic generation:** Seed-based so the demo is reproducible
4. **Three files, three perspectives:** Each source sees the same transactions through its own lens

### 1.1 Dataset A - Internal Order Ledger

The merchant own order management system. Knows about orders, amounts, and customer details. Does **not** know about fees, settlement batching, or bank credits.

CSV columns:
order_id, order_date, customer_id, product_sku, quantity, gross_amount, currency, payment_method, payment_status, refund_status, refund_amount, created_at, notes

| Column | Type | Messiness Notes |
|---|---|---|
| order_id | string | Canonical ord_ prefixed |
| order_date | date | **IST (Asia/Kolkata)** - source of truth for order timing |
| customer_id | string | Anonymized customer reference |
| product_sku | string | Product reference |
| quantity | int | 1-5 |
| gross_amount | decimal(10,2) | Pre-fee amount in INR |
| currency | string | Always INR for domestic orders. 2 international orders use **USD** (customer payment currency). See section 0.7 for conversion rules. |
| payment_method | enum | upi, visa_mc_domestic, international_card, amex_diners |
| payment_status | enum | captured, failed, authorized (not yet captured) |
| refund_status | enum | none, full, partial |
| refund_amount | decimal(10,2) | 0 if no refund; partial amount if partial |
| created_at | timestamp | **IST timezone, with milliseconds** - might differ from order_date by hours |
| notes | string | Free-text; occasionally has internal memos |

**Messiness baked in:**
- 5 orders with payment_status = failed (will not appear in settlement report, unmatchable from bank side)
- 2 orders with payment_status = authorized (captured after cut-off, settles next cycle)
- 3 orders with refund_status = partial (refund splits across batches)
- 2 orders in USD (international card). Dataset A stores gross_amount in USD (e.g., $249.99). Datasets B and C store the INR-converted equivalent (1 USD = Rs 83 fixed rate for synthetic data). This tests currency-aware matching and exposes the conversion gap that naive matchers will miss.
- 10 orders where created_at is 23:30-23:59 IST (near cut-off, causes batch ambiguity)
- 1 duplicate order_id entry with slightly different amounts (human data-entry error)


### 1.2 Dataset B - Razorpay Settlement Report

Razorpay view. Knows about fees, GST, settlements, and refunds - but in batches, not as individual orders.

CSV columns:
settlement_id, settlement_date, bank_utr, payment_id, order_id, gross_amount, fee, gst_on_fee, refund_deduction, net_amount, payment_method, captured_date, settlement_status

| Column | Type | Messiness Notes |
|---|---|---|
| settlement_id | string | set_ prefixed - groups transactions into batches |
| settlement_date | date | **T+N from captured_date**; skips weekends/holidays |
| bank_utr | string | 16-digit NEFT reference - **same UTR for all rows in a settlement_id** |
| payment_id | string | pay_ prefixed - Razorpay transaction ID |
| order_id | string | Links back to order ledger |
| gross_amount | decimal(10,2) | Transaction amount before deductions |
| fee | decimal(10,2) | Platform fee / MDR |
| gst_on_fee | decimal(10,2) | 18% of fee |
| refund_deduction | decimal(10,2) | **Negative** if this is a refund line; 0 for normal transactions |
| net_amount | decimal(10,2) | gross_amount - fee - gst_on_fee + refund_deduction |
| payment_method | enum | May have slightly different labels than order ledger (card, upi, netbanking) |
| captured_date | date | When payment was captured (may differ from order_date) |
| settlement_status | enum | settled, pending, on_hold |

**Fee calculation rules (for generator):**

    if payment_method in (upi, ruPay_debit):
        fee = gross_amount * 0.02          # 2% platform fee
    elif payment_method in (visa_mc_domestic, amex_diners):
        fee = gross_amount * 0.02          # 2%
    elif payment_method == international_card:
        fee = gross_amount * 0.03          # 3%
    gst_on_fee = round(fee * 0.18, 2)     # Always 18% of fee
    net = gross - fee - gst_on_fee

**Messiness baked in:**
- **Batched settlements:** 3-6 orders share the same settlement_id and bank_utr
- **Partial refund splits:** 3 refund deductions appear in a *different* settlement batch than the original order
- **Rounding variance:** Use round() at transaction level, so sum of net_amount may differ from gross_batch_sum - total_fees - total_gst by 0.01-0.05 per batch (sub-rupee rounding)
- **Payment method label mismatch:** Order ledger says visa_mc_domestic, settlement report says card - forces fuzzy matching
- **USD conversion:** For the 2 international orders, gross_amount in Dataset B is the INR-converted value (USD amount x Rs 83), not the original USD amount. The fee and GST are calculated on the INR base_amount. This means Dataset A (USD) and Dataset B (INR) show different numeric values for the same order, requiring currency conversion awareness.
- **Timezone gap:** Settlement dates are in IST but some fields might be reported in UTC
- **1 ghost transaction:** A payment_id in settlement report with no matching order_id in the order ledger (settlement of a payment created outside the system)
- **1 missing settlement row:** An order that appears in the ledger as captured but is missing from settlement (could be delayed, held, or an error)
- **Weekend/holiday clustering:** 2 settlement batches land on Tuesday because Friday payments skip the weekend


### 1.3 Dataset C - Bank Statement

The bank view. Only sees NEFT credits/debits. Does **not** know about individual orders, fees, or Razorpay internals.

CSV columns:
txn_date, txn_type, narration, utr, amount, balance_after, branch_code

| Column | Type | Messiness Notes |
|---|---|---|
| txn_date | datetime | **Bank recording time** - may be 1 day after Razorpay settlement_date (NEFT processing lag) |
| txn_type | enum | credit, debit |
| narration | string | Free-text bank narration, format varies |
| utr | string | NEFT UTR - matches bank_utr in settlement report |
| amount | decimal(10,2) | Always positive; txn_type indicates direction |
| balance_after | decimal(12,2) | Running account balance |
| branch_code | string | Bank branch code (irrelevant but present in real statements) |

**Messiness baked in:**
- **Narration format variance** (4+ formats to test regex/NLP extraction):
  - NEFT CR: Razorpay Solutions Pvt Ltd REF:1234567890123456
  - NEFT-CR RAZORPAY SETTLEMENT UTR 1234567890123456
  - Razorpay Settlement - NEFT Credit - Ref No.1234567890123456
  - NEFT/CR/RAZORPAY/1234567890123456
- **Date offset:** Bank txn_date is typically T+1 from settlement_date (NEFT processing), but sometimes same-day
- **Non-settlement entries:** Include 8-10 regular bank transactions (salary debits, vendor payments, other credits) as noise - these are unmatchable to Razorpay
- **Weekend gap:** No credits on Saturday/Sunday even if Razorpay settled on Friday (NEFT does not process on weekends)
- **1 failed NEFT credit:** A Razorpay settlement that failed at bank level (amount debited from Razorpay nodal but not credited to merchant)
- **1 duplicate UTR:** Same UTR appears twice (bank system glitch) - test deduplication


### 1.4 Cross-Dataset Messiness Matrix

This table maps every deliberate messiness artifact and which datasets it touches:

| Edge Case | Dataset A (Orders) | Dataset B (Settlement) | Dataset C (Bank) | Intended Test |
|---|---|---|---|---|
| Failed payment (not captured) | 5 rows | absent | absent | Detection of unmatched orders |
| Near-cut-off capture | 10 rows | Split across 2 batches | 2 separate credits | Batch boundary detection |
| Batched settlement | individual orders | grouped by settlement_id | single UTR credit | 1:N matching (settlement to orders) |
| Partial refund split | refund_amount shown | refund in different batch | no separate bank entry | Cross-batch refund tracking |
| Rounding variance | - | +/-0.01 per batch | exact credit amount | Tolerance-based matching |
| Payment method label mismatch | visa_mc_domestic | card | - | Fuzzy / alias matching |
| Timezone date offset | IST order_date | IST/UTC mixed | Bank recording time | Date normalization |
| Ghost transaction (no order) | absent | 1 row | credit present | Unmatchable exception |
| Missing settlement row | captured order | absent | - | Delayed/held detection |
| International currency (USD) | 2 rows with USD gross_amount | present with INR-converted gross_amount (x83) | INR only | Currency conversion handling: A stores USD, B stores INR post-conversion |
| Narration format variance | - | - | 4+ formats | NLP / regex extraction |
| Bank processing delay | - | settlement_date | txn_date = +1d | Tolerance window |
| Non-settlement bank noise | - | - | 8-10 rows | Noise filtering |
| Failed NEFT credit | - | settlement shows settled | no credit | Discrepancy detection |
| Duplicate UTR | - | - | 1 duplicate | Deduplication |
| Data entry duplicate order | 1 duplicate | - | - | Deduplication |
| Weekend / holiday gap | - | delayed batches | no weekend credits | Calendar-aware matching |

### 1.5 Dataset Size and Distribution

| Metric | Value |
|---|---|
| **Date range** | 2 weeks (14 calendar days), Mon Aug 4 - Sun Aug 17, 2025 |
| **Total orders** | ~500 |
| **Captured successfully** | ~480 |
| **Failed** | 5 |
| **Authorized (not captured)** | 2 |
| **Settlement batches** | ~80-100 (depending on batching) |
| **Partial refunds** | ~15 orders (3 split across batches) |
| **Full refunds** | ~8 orders |
| **International orders** | 2 |
| **Bank statement rows** | ~120 (settlement credits + noise transactions) |
| **Overall expected match rate** | ~93-96% (after reconciliation + normalization preprocessing), with ~4-7% genuine exceptions |


### 1.6 Reconciliation Relationships (for downstream phases)

These are the join/match paths the reconciliation engine will need:

    +------------------+     order_id / payment_id     +--------------------+
    |  Dataset A       |<----------------------------->|  Dataset B         |
    |  Order Ledger    |                                |  Settlement Report |
    +------------------+                                +--------------------+
                                                               |
                                                            bank_utr
                                                               |
                                                               v
                                                       +--------------------+
                                                       |  Dataset C         |
                                                       |  Bank Statement    |
                                                       +--------------------+

**Primary match path:** A.order_id -> B.order_id -> B.bank_utr -> C.utr

**Settlement batch aggregation:** Sum of B.net_amount per settlement_id should approximately equal C.amount where C.utr matches

**Amount tolerance:** Allow +/-1.00 per batch to handle rounding

### 1.7 Expected Exception Categories

Based on the messiness we are baking in, the reconciliation output should eventually classify exceptions into:

| Code | Description | Count (expected) |
|---|---|---|
| UNMATCHED_ORDER | Order captured but no settlement or bank credit | ~5 |
| GHOST_SETTLEMENT | Settlement/bank entry with no matching order | ~1 |
| REFUND_SPLIT | Order spans multiple settlement batches | ~3 |
| DUPLICATE_ORDER | Same order_id appears twice in Dataset A (data entry error) | ~1 |
| ROUNDING_VARIANCE | Amount diff within tolerance (+/-1) | ~5-10 |
| NARRATION_PARSE_FAIL | Bank narration could not be parsed for UTR | ~0 (design to succeed) |
| DATE_OFFSET | Match exists but dates differ by >1 day | ~3-5 |
| CURRENCY_MISMATCH | USD order (Dataset A) vs INR settlement (Dataset B). Requires conversion at fixed rate Rs 83/USD to match. | ~2 |
| DUPLICATE_UTR | Bank duplicate to be deduplicated | ~1 |
| NEFT_FAILED | Settlement exists but bank credit absent | ~1 |
| ~~LABEL_MISMATCH~~ | **Removed from exceptions.** See note below. | N/A |

---





**Note on LABEL_MISMATCH (removed from exception list):**

Payment method labels differ between Dataset A (order ledger) and Dataset B (settlement report):
- Dataset A: visa_mc_domestic, international_card, amex_diners, upi
- Dataset B: card, intl_card, amex, upi

This is a systemic difference affecting ~100+ rows. It is not an exception -- it is a normalization rule the matcher should handle as a preprocessing step. The alias mapping table is:

| Dataset A label | Dataset B label |
|---|---|
| visa_mc_domestic | card |
| international_card | intl_card |
| amex_diners | amex |
| upi | upi |

This normalization is applied before matching and does not count against the exception list or match rate. It belongs in Phase 3 preprocessing, not in exception classification.

**Corrected expected match rate:** ~93-96% (slightly higher now that LABEL_MISMATCH is handled as preprocessing, not counted as exceptions).

### 1.8 Ground Truth Manifest (ground_truth.json)

**Why this exists:** Without a ground truth file, there is no way to validate the reconciliation engine output against known-correct answers. The match rate reported by Phase 5 would be unverifiable. This file is the answer key, generated alongside the data -- not inferred after.

**Schema (one entry per order_id):**

    {
      "order_id": "ord_AbCdEfGhIjKlMn",
      "expected_match_status": "matched | exception",
      "expected_settlement_ids": ["set_XyZ123"],
      "expected_bank_utr": "1234567890123456",
      "exception_code": null,
      "exception_detail": null,
      "notes": null
    }

**Field definitions:**

| Field | Type | Values | Notes |
|---|---|---|---|
| order_id | string | ord_... | One entry per order_id in Dataset A. Duplicate order_id gets two entries. |
| expected_match_status | enum | matched, exception | Every order is either cleanly matchable or is a known exception. |
| expected_settlement_ids | list[string] | set_... | Which settlement batches this order appears in. Empty list if unmatched. Most orders have 1 entry; refund-split orders have 2+. |
| expected_bank_utr | string or null | 16-digit ref | The UTR of the bank credit that corresponds to the settlement(s). Null if no bank credit exists (failed NEFT, unmatched order). |
| exception_code | string or null | See table 1.7 | Null if matched. Otherwise the classification code. |
| exception_detail | string or null | Free text | Human-readable explanation of why this is an exception. Null if matched. |
| notes | string or null | Free text | Generator metadata (e.g., "near-cut-off batch boundary", "ghost transaction origin"). Null for clean matches. |

**Generation rules:**

1. Every order in Dataset A gets exactly one entry in ground_truth.json
2. The file is written by the data generator in the same pass that produces the three CSVs -- never post-hoc
3. For orders with refund splits, expected_settlement_ids lists BOTH settlement batches
4. For the duplicate order_id, there are TWO entries with different expected_match_status values (one matched, one exception DUPLICATE_ORDER)
5. For the ghost transaction (present in Dataset B but not Dataset A), there is NO entry in ground_truth -- ground_truth is keyed on order_id, and the ghost has none
6. For the failed NEFT credit, expected_match_status is exception and expected_bank_utr is null

**Example entries:**

    [
      {
        "order_id": "ord_AbCdEfGhIjKlMn",
        "expected_match_status": "matched",
        "expected_settlement_ids": ["set_XyZ123"],
        "expected_bank_utr": "1234567890123456",
        "exception_code": null,
        "exception_detail": null,
        "notes": null
      },
      {
        "order_id": "ord_PqRsTuVwXyZaBc",
        "expected_match_status": "exception",
        "expected_settlement_ids": ["set_Aaa111", "set_Bbb222"],
        "expected_bank_utr": "9998887776665554",
        "exception_code": "REFUND_SPLIT",
        "exception_detail": "Order gross settled in set_Aaa111, partial refund -8000 deducted in set_Bbb222. Two bank credits involved.",
        "notes": "Partial refund initiated Day 3, landed in Day 5 batch"
      },
      {
        "order_id": "ord_FaIlEd0000000",
        "expected_match_status": "exception",
        "expected_settlement_ids": [],
        "expected_bank_utr": null,
        "exception_code": "UNMATCHED_ORDER",
        "exception_detail": "Payment status is failed. Never captured, never settled.",
        "notes": "Deliberate failed payment for testing"
      }
    ]

## Appendix A - Razorpay Settlement Report (Real) Field Reference

For reference, a real Razorpay settlement report CSV typically includes these columns (field names may vary by export version):

    settlement_id, settlement_date, settlement_utr, payment_id, order_id,
    amount, customer_email, customer_phone, payment_method, settlement_status,
    fee, tax, refund, net, captured_at

Our synthetic schema adds gst_on_fee as an explicit column (some real exports bundle it into tax) and uses a clearer refund_deduction column to distinguish from fee.

## Appendix B - Settlement Calculation Worked Example

**Order:** Rs 15,000 via Visa debit card (domestic)

    Gross amount:           15,000.00
    Platform fee (2%):       -300.00
    GST on fee (18%):         -54.00
                            ---------
    Net settlement:         14,646.00

**If later partially refunded Rs 5,000:**

    Original settlement:    14,646.00 (already in bank)
    Refund deduction:       -5,000.00 (in a future batch)
    MDR on original:          300.00 (NOT refunded, merchant absorbs)
    GST on MDR:                54.00 (NOT refunded)
    Effective fee on          354.00 on 10,000 retained
    retained revenue:         3.54% effective rate (not 2%)

**Batch of 4 orders:**

| Order | Gross | Fee (2%) | GST (18%) | Refund Deduct | Net |
|---|---|---|---|---|---|
| A | 15,000 | 300 | 54 | 0 | 14,646 |
| B | 8,000 | 160 | 28.80 | 0 | 7,811.20 |
| C | 22,000 | 440 | 79.20 | -3,000 | 18,480.80 |
| D | 5,000 | 100 | 18 | 0 | 4,882 |
| **Batch Total** | **50,000** | **1,000** | **180** | **-3,000** | **45,820** |

The bank receives **one NEFT credit of Rs 45,820** with one UTR.

---

*This document is the foundation for Phase 2 (data generator), Phase 3 (reconciliation engine), and Phase 4 (agent + UI). Each downstream phase will reference the schemas and edge cases defined here.*

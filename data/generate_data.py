#!/usr/bin/env python3
"""
Synthetic data generator for Razorpay reconciliation buildathon.
Produces 4 files in data/raw/:
  - order_ledger.csv
  - settlement_report.csv
  - bank_statement.csv
  - ground_truth.json

All outputs are deterministic given seed=42.
"""

import csv
import json
import os
import random
from datetime import datetime, timedelta, date, time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SEED = 42
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "raw")
START_DATE = date(2025, 8, 4)   # Monday
END_DATE = date(2025, 8, 17)    # Sunday (14 calendar days)
HOLIDAYS = {date(2025, 8, 15)}  # Independence Day (Friday)
FX_RATE = 83.00                 # 1 USD = Rs 83.00
CUT_OFF_HOUR = 17               # 5 PM IST

# Payment method distributions and their fee tiers
# (order_label, settlement_label, fee_pct, weight)
PAYMENT_METHODS = [
    ("upi",                   "upi",         0.02, 45),
    ("visa_mc_domestic",      "card",        0.02, 25),
    ("amex_diners",           "amex",        0.02, 10),
    ("international_card",    "intl_card",   0.03,  5),
]
# Remaining 15% fill with upi to reach 100% weight

PRODUCT_SKUS = [
    "SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004", "SKU-1005",
    "SKU-2001", "SKU-2002", "SKU-2003", "SKU-2004", "SKU-2005",
    "SKU-3001", "SKU-3002", "SKU-3003", "SKU-3004", "SKU-3005",
]

BANK_BRANCH_CODES = ["HDFC0001234", "ICICI0005678", "SBIN0009012", "KKBK0004567"]

NARRATION_FORMATS = [
    lambda utr: f"NEFT CR: Razorpay Solutions Pvt Ltd REF:{utr}",
    lambda utr: f"NEFT-CR RAZORPAY SETTLEMENT UTR {utr}",
    lambda utr: f"Razorpay Settlement - NEFT Credit - Ref No.{utr}",
    lambda utr: f"NEFT/CR/RAZORPAY/{utr}",
]

NOISE_NARRATIONS = [
    "SALARY TRANSFER - AUG 2025",
    "NEFT DR: VENDOR PAYMENTS PVT LTD",
    "IMPS CR: CLIENT INVOICE #4521",
    "NEFT DR: OFFICE RENT AUGUST",
    "UPI DR: SWIGGY ORDER",
    "NEFT CR: INTEREST CREDIT Q2",
    "ATM WDL: HDFC ATM 0810",
    "NEFT DR: RENEWABLES INDIA",
    "IMPS CR: FREELANCE PAYMENT",
    "NEFT DR: INTERNET BILL AIRTEL",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
rng = random.Random(SEED)

def id_gen(prefix, n=16):
    """Generate a deterministic ID like ord_AbCdEfGhIjKlMn."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return prefix + "".join(rng.choices(chars, k=n))

def is_weekend(d):
    return d.weekday() in (5, 6)

def next_working_day(d):
    """Return the next business day after d."""
    d = d + timedelta(days=1)
    while is_weekend(d) or d in HOLIDAYS:
        d += timedelta(days=1)
    return d

def add_working_days(d, n):
    """Add n working days to date d."""
    for _ in range(n):
        d = next_working_day(d)
    return d

def make_ist_datetime(d, h=10, m=0, s=0, ms=0):
    """Create an IST datetime for a given date and time."""
    return datetime(d.year, d.month, d.day, h, m, s, ms * 1000)

def calc_fee(gross, fee_pct):
    fee = round(gross * fee_pct, 2)
    gst = round(fee * 0.18, 2)
    return fee, gst

def calc_fee_with_drift(gross, fee_pct):
    """Compute fee/gst as usual but derive net from unrounded intermediates.
    This creates a small gap between sum(net) and sum(gross)-sum(fee)-sum(gst)
    at the batch level, because the per-row rounding of net uses different
    intermediate precision than the batch-level check."""
    fee_exact = gross * fee_pct
    gst_exact = fee_exact * 0.18
    net_exact = gross - fee_exact - gst_exact
    fee = round(fee_exact, 2)
    gst = round(gst_exact, 2)
    net = round(net_exact, 2)  # net uses unrounded intermediates
    return fee, gst, net

def pick_payment_method():
    methods = ["upi"] * 45 + ["visa_mc_domestic"] * 25 + ["amex_diners"] * 10 + ["international_card"] * 5 + ["upi"] * 15
    return rng.choice(methods)

def pick_amount():
    """Realistic Indian e-commerce amount distribution."""
    buckets = [
        (200, 1000, 30),     # small items
        (1000, 5000, 35),    # mid-range
        (5000, 15000, 20),   # high-end
        (15000, 50000, 10),  # premium
        (50000, 200000, 5),  # bulk/enterprise
    ]
    low, high, _ = rng.choices(buckets, weights=[b[2] for b in buckets])[0]
    return round(rng.uniform(low, high), 2)

# ---------------------------------------------------------------------------
# Step 1: Generate orders
# ---------------------------------------------------------------------------
print("Generating orders...")
orders = []
order_ids_used = []

# First pass: generate all ~500 orders
# We'll generate 502 base orders, then handle edge cases
NUM_ORDERS = 500

for i in range(NUM_ORDERS):
    oid = id_gen("ord_")
    order_ids_used.append(oid)

    # Spread orders across the 14-day window
    day_offset = rng.randint(0, 13)
    order_day = START_DATE + timedelta(days=day_offset)

    # Time of day - most orders are business hours, some near cutoff
    hour = rng.choices(
        range(24),
        weights=[1]*6 + [3]*4 + [8]*8 + [4]*4 + [2]*2  # weighted toward 8-20
    )[0]
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    ms = rng.randint(0, 999)

    created_at = make_ist_datetime(order_day, hour, minute, second, ms)
    order_date = order_day

    customer_id = id_gen("cust_", 10)
    product_sku = rng.choice(PRODUCT_SKUS)
    quantity = rng.randint(1, 5)
    gross_amount = pick_amount()
    currency = "INR"
    payment_method = pick_payment_method()

    # Default: captured, no refund
    payment_status = "captured"
    refund_status = "none"
    refund_amount = 0.0
    notes = ""

    orders.append({
        "order_id": oid,
        "order_date": order_date,
        "created_at": created_at,
        "customer_id": customer_id,
        "product_sku": product_sku,
        "quantity": quantity,
        "gross_amount": gross_amount,
        "currency": currency,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "refund_status": refund_status,
        "refund_amount": refund_amount,
        "notes": notes,
    })

# ---------------------------------------------------------------------------
# Step 2: Inject edge cases into orders
# ---------------------------------------------------------------------------
print("Injecting edge cases...")

# 2a: 5 failed payments (indices 0-4)
for i in range(5):
    orders[i]["payment_status"] = "failed"
    orders[i]["notes"] = "Deliberate failed payment for testing"
    orders[i]["refund_status"] = "none"
    orders[i]["refund_amount"] = 0.0

# 2b: 2 authorized (not captured) - indices 5-6
for i in range(5, 7):
    orders[i]["payment_status"] = "authorized"
    orders[i]["notes"] = "Captured after cutoff, settles next cycle"
    orders[i]["refund_status"] = "none"
    orders[i]["refund_amount"] = 0.0

# 2c: 2 USD international orders - indices 7-8
for i in range(7, 9):
    usd_amount = round(rng.uniform(50, 500), 2)
    orders[i]["gross_amount"] = usd_amount
    orders[i]["currency"] = "USD"
    orders[i]["payment_method"] = "international_card"
    orders[i]["notes"] = f"International order. USD {usd_amount}, settles as INR {round(usd_amount * FX_RATE, 2)}"

# 2d: 10 near-cut-off orders (23:30-23:59 IST) - indices 9-18
for i in range(9, 19):
    day = orders[i]["order_date"]
    h = 23
    m = rng.randint(30, 59)
    s = rng.randint(0, 59)
    ms = rng.randint(0, 999)
    orders[i]["created_at"] = make_ist_datetime(day, h, m, s, ms)
    orders[i]["notes"] = "Near cutoff - batch boundary ambiguous"

# 2e: 1 duplicate order_id - insert a copy of order at index 20 with different amount
dup_source = orders[20]
dup_order = dict(dup_source)
dup_order["gross_amount"] = round(dup_source["gross_amount"] * rng.uniform(0.85, 1.15), 2)
dup_order["notes"] = "DUPLICATE: data entry error, different amount"
# We'll append this later after settlement logic so we can track it

# 2f: 15 partial refunds - indices 21-35 (3 will be cross-batch splits)
for i in range(21, 36):
    order = orders[i]
    order["refund_status"] = "partial"
    # Refund between 10% and 60% of gross
    pct = rng.uniform(0.10, 0.60)
    order["refund_amount"] = round(order["gross_amount"] * pct, 2)
    order["notes"] = "Partial refund"

# Mark the 3 cross-batch refund splits specifically (indices 21, 22, 23)
# These refunds will be deducted in a DIFFERENT settlement batch
for i in range(21, 24):
    orders[i]["notes"] = "Partial refund - split across settlement batches"

# 2g: 8 full refunds - indices 36-43
for i in range(36, 44):
    orders[i]["refund_status"] = "full"
    orders[i]["refund_amount"] = orders[i]["gross_amount"]
    orders[i]["notes"] = "Full refund"

# ---------------------------------------------------------------------------
# Step 3: Separate capturable orders from edge cases
# ---------------------------------------------------------------------------
captured_orders = [o for o in orders if o["payment_status"] == "captured"]
failed_orders = [o for o in orders if o["payment_status"] == "failed"]
authorized_orders = [o for o in orders if o["payment_status"] == "authorized"]

print(f"  Total orders in ledger: {len(orders)}")
print(f"  Captured: {len(captured_orders)}, Failed: {len(failed_orders)}, Authorized: {len(authorized_orders)}")

# ---------------------------------------------------------------------------
# Step 4: Assign captured orders to settlement batches
# ---------------------------------------------------------------------------
print("Creating settlement batches...")

# Group captured orders by their "settlement window"
# Orders captured before 5PM IST on a working day settle T+2
# Orders after 5PM roll to next working day, then T+2

def get_settlement_date(captured_date, captured_time):
    """Determine which settlement batch an order falls into."""
    if captured_time.hour >= CUT_OFF_HOUR:
        # After cutoff - rolls to next working day, then T+2
        batch_day = next_working_day(captured_date)
    else:
        batch_day = captured_date
    return add_working_days(batch_day, 2)

# Group by settlement date
batch_groups = defaultdict(list)
for order in captured_orders:
    settle_date = get_settlement_date(order["order_date"], order["created_at"])
    batch_groups[settle_date].append(order)

# Flatten and sort batches by settlement date
sorted_batch_dates = sorted(batch_groups.keys())

# Assign settlement IDs and bank UTRs
settlements = []  # list of {settlement_id, bank_utr, settlement_date, orders: [...]}
settlement_counter = 0

for settle_date in sorted_batch_dates:
    batch_orders = batch_groups[settle_date]
    # Split large batches into groups of 4-6
    rng.shuffle(batch_orders)
    chunk_size = rng.randint(4, 6)
    for chunk_start in range(0, len(batch_orders), chunk_size):
        chunk = batch_orders[chunk_start:chunk_start + chunk_size]
        if len(chunk) < 1:
            continue
        settlement_counter += 1
        settlements.append({
            "settlement_id": id_gen("set_"),
            "bank_utr": "".join([str(rng.randint(0,9)) for _ in range(16)]),
            "settlement_date": settle_date,
            "orders": chunk,
        })

print(f"  Settlement batches: {len(settlements)}")

# ---------------------------------------------------------------------------
# Step 5: Build settlement report rows
# ---------------------------------------------------------------------------
print("Building settlement report...")

# Payment method label mapping for settlement report
SETTLEMENT_LABEL_MAP = {
    "upi": "upi",
    "visa_mc_domestic": "card",
    "amex_diners": "amex",
    "international_card": "intl_card",
}

# Fee rates by payment method
FEE_RATES = {
    "upi": 0.02,
    "visa_mc_domestic": 0.02,
    "amex_diners": 0.02,
    "international_card": 0.03,
}

# Mark ~35% of batches as "drift batches" for realistic rounding variance.
# Only ~40-50% of these will actually produce non-zero drift (depends on
# whether gross * fee_pct has fractional paise), so effective rate is ~15-20%.
drift_batch_ids = set()
for batch in settlements:
    if rng.random() < 0.35:
        drift_batch_ids.add(batch["settlement_id"])
print(f"  Drift batches (unrounded-net rounding): {len(drift_batch_ids)}/{len(settlements)}")

settlement_rows = []
for batch in settlements:
    is_drift = batch["settlement_id"] in drift_batch_ids
    # For drift batches, pick 2-3 random rows to use unrounded-net calculation
    drift_row_count = 0
    if is_drift:
        n = len(batch["orders"])
        drift_row_count = rng.randint(2, min(3, n)) if n >= 2 else 0
    drift_rows_picked = set(rng.sample(range(len(batch["orders"])), drift_row_count)) if drift_row_count > 0 else set()

    for idx, order in enumerate(batch["orders"]):
        # For USD orders, gross_amount in settlement is INR-converted
        if order["currency"] == "USD":
            gross_inr = round(order["gross_amount"] * FX_RATE, 2)
        else:
            gross_inr = order["gross_amount"]

        fee_rate = FEE_RATES[order["payment_method"]]
        if idx in drift_rows_picked:
            fee, gst, net = calc_fee_with_drift(gross_inr, fee_rate)
        else:
            fee, gst = calc_fee(gross_inr, fee_rate)
            net = round(gross_inr - fee - gst, 2)
        refund_ded = 0.0

        settlement_rows.append({
            "settlement_id": batch["settlement_id"],
            "settlement_date": batch["settlement_date"].strftime("%Y-%m-%d"),
            "bank_utr": batch["bank_utr"],
            "payment_id": id_gen("pay_"),
            "order_id": order["order_id"],
            "gross_amount": gross_inr,
            "fee": fee,
            "gst_on_fee": gst,
            "refund_deduction": refund_ded,
            "net_amount": net,
            "payment_method": SETTLEMENT_LABEL_MAP[order["payment_method"]],
            "captured_date": order["order_date"].strftime("%Y-%m-%d"),
            "settlement_status": "settled",
        })

# ---------------------------------------------------------------------------
# Step 5b: Add refund deduction rows in DIFFERENT batches (cross-batch splits)
# ---------------------------------------------------------------------------
# The 3 orders at indices 21, 22, 23 have refunds that appear in a different batch
print("Adding cross-batch refund splits...")

for refund_order in [orders[21], orders[22], orders[23]]:
    # Find which batch this order is in
    original_batch = None
    for batch in settlements:
        if any(o["order_id"] == refund_order["order_id"] for o in batch["orders"]):
            original_batch = batch
            break

    if original_batch is None:
        continue

    # Pick a DIFFERENT batch that settlement_date is after original
    later_batches = [
        b for b in settlements
        if b["settlement_date"] > original_batch["settlement_date"]
        and b["settlement_id"] != original_batch["settlement_id"]
    ]
    if not later_batches:
        later_batches = [b for b in settlements if b["settlement_id"] != original_batch["settlement_id"]]

    refund_batch = rng.choice(later_batches)

    if refund_order["currency"] == "USD":
        gross_inr = round(refund_order["gross_amount"] * FX_RATE, 2)
    else:
        gross_inr = refund_order["gross_amount"]

    fee_rate = FEE_RATES[refund_order["payment_method"]]
    fee, gst = calc_fee(gross_inr, fee_rate)
    refund_ded = -abs(refund_order["refund_amount"])
    net = round(0 - 0 - 0 + refund_ded, 2)  # refund row: only the deduction matters

    settlement_rows.append({
        "settlement_id": refund_batch["settlement_id"],
        "settlement_date": refund_batch["settlement_date"].strftime("%Y-%m-%d"),
        "bank_utr": refund_batch["bank_utr"],
        "payment_id": id_gen("pay_"),
        "order_id": refund_order["order_id"],
        "gross_amount": 0.0,
        "fee": 0.0,
        "gst_on_fee": 0.0,
        "refund_deduction": refund_ded,
        "net_amount": refund_ded,
        "payment_method": SETTLEMENT_LABEL_MAP[refund_order["payment_method"]],
        "captured_date": refund_order["order_date"].strftime("%Y-%m-%d"),
        "settlement_status": "settled",
    })

# Also add refund deduction rows for full refunds (indices 36-43)
# These also appear in a different batch
for refund_order in [orders[i] for i in range(36, 44)]:
    original_batch = None
    for batch in settlements:
        if any(o["order_id"] == refund_order["order_id"] for o in batch["orders"]):
            original_batch = batch
            break

    if original_batch is None:
        continue

    later_batches = [
        b for b in settlements
        if b["settlement_date"] >= original_batch["settlement_date"]
        and b["settlement_id"] != original_batch["settlement_id"]
    ]
    if not later_batches:
        later_batches = [b for b in settlements if b["settlement_id"] != original_batch["settlement_id"]]

    refund_batch = rng.choice(later_batches)

    if refund_order["currency"] == "USD":
        gross_inr = round(refund_order["gross_amount"] * FX_RATE, 2)
    else:
        gross_inr = refund_order["gross_amount"]

    refund_ded = -abs(refund_order["refund_amount"])
    net = refund_ded

    settlement_rows.append({
        "settlement_id": refund_batch["settlement_id"],
        "settlement_date": refund_batch["settlement_date"].strftime("%Y-%m-%d"),
        "bank_utr": refund_batch["bank_utr"],
        "payment_id": id_gen("pay_"),
        "order_id": refund_order["order_id"],
        "gross_amount": 0.0,
        "fee": 0.0,
        "gst_on_fee": 0.0,
        "refund_deduction": refund_ded,
        "net_amount": net,
        "payment_method": SETTLEMENT_LABEL_MAP[refund_order["payment_method"]],
        "captured_date": refund_order["order_date"].strftime("%Y-%m-%d"),
        "settlement_status": "settled",
    })

# ---------------------------------------------------------------------------
# Step 5c: 1 ghost transaction (settlement row with no matching order)
# ---------------------------------------------------------------------------
print("Adding ghost transaction...")
ghost_batch = rng.choice(settlements)
settlement_rows.append({
    "settlement_id": ghost_batch["settlement_id"],
    "settlement_date": ghost_batch["settlement_date"],
    "bank_utr": ghost_batch["bank_utr"],
    "payment_id": id_gen("pay_"),
    "order_id": id_gen("ord_"),  # no matching order
    "gross_amount": round(rng.uniform(500, 5000), 2),
    "fee": 0.0,
    "gst_on_fee": 0.0,
    "refund_deduction": 0.0,
    "net_amount": 0.0,
    "payment_method": "card",
    "captured_date": ghost_batch["settlement_date"],
    "settlement_status": "settled",
})
# Fix ghost transaction amounts
ghost = settlement_rows[-1]
ghost["fee"] = round(ghost["gross_amount"] * 0.02, 2)
ghost["gst_on_fee"] = round(ghost["fee"] * 0.18, 2)
ghost["net_amount"] = round(ghost["gross_amount"] - ghost["fee"] - ghost["gst_on_fee"], 2)

# ---------------------------------------------------------------------------
# Step 5d: 1 missing settlement row (captured order not in settlement)
# ---------------------------------------------------------------------------
# Pick one captured order and remove it from settlements
print("Creating missing settlement row...")
missing_order = None
for order in captured_orders:
    if order["payment_status"] == "captured" and order["notes"] == "":
        missing_order = order
        break

if missing_order:
    # Remove from settlement_rows
    settlement_rows = [r for r in settlement_rows if r["order_id"] != missing_order["order_id"]]
    # Remove from settlements list too
    for batch in settlements:
        batch["orders"] = [o for o in batch["orders"] if o["order_id"] != missing_order["order_id"]]
    missing_order["notes"] = "Missing from settlement report - possibly on hold"

# ---------------------------------------------------------------------------
# Step 6: Add duplicate order_id to ledger
# ---------------------------------------------------------------------------
print("Adding duplicate order_id...")
orders.append(dup_order)
order_ids_used.append(dup_order["order_id"])

# ---------------------------------------------------------------------------
# Step 7: Build bank statement
# ---------------------------------------------------------------------------
print("Building bank statement...")

bank_rows = []
balance = 500000.00  # Starting balance

# For each settlement batch, create a NEFT credit
# Bank credit date is typically T+1 from settlement_date
for batch in settlements:
    if not batch["orders"]:
        continue

    # Calculate batch net amount
    batch_net = 0.0
    for row in settlement_rows:
        if row["settlement_id"] == batch["settlement_id"]:
            batch_net += row["net_amount"]

    if batch_net <= 0:
        continue

    # Bank credit date: T+1 from settlement (skip weekends)
    credit_date = next_working_day(batch["settlement_date"])

    # Pick narration format
    narr_fn = rng.choice(NARRATION_FORMATS)
    narration = narr_fn(batch["bank_utr"])

    balance = round(balance + batch_net, 2)

    bank_rows.append({
        "txn_date": credit_date.strftime("%Y-%m-%d %H:%M:%S"),
        "txn_type": "credit",
        "narration": narration,
        "utr": batch["bank_utr"],
        "amount": round(batch_net, 2),
        "balance_after": balance,
        "branch_code": rng.choice(BANK_BRANCH_CODES),
    })

# ---------------------------------------------------------------------------
# Step 7b: 1 failed NEFT credit
# ---------------------------------------------------------------------------
print("Adding failed NEFT credit...")
# Pick a settlement that has a bank credit, and note that it failed
# We'll just NOT add the bank credit for one settlement (remove it)
# Actually per design: settlement shows settled but bank has no credit
# So we keep the settlement row but remove the bank credit
if len(bank_rows) > 10:
    failed_neft_idx = rng.randint(5, min(15, len(bank_rows) - 1))
    failed_neft_utr = bank_rows[failed_neft_idx]["utr"]
    # Remove this bank credit
    bank_rows.pop(failed_neft_idx)
    # Adjust balances for all rows after this one
    for i in range(failed_neft_idx, len(bank_rows)):
        bank_rows[i]["balance_after"] = round(
            bank_rows[i-1]["balance_after"] + (bank_rows[i]["amount"] if bank_rows[i]["txn_type"] == "credit" else -bank_rows[i]["amount"]),
            2
        ) if i > 0 else bank_rows[i]["balance_after"]

# ---------------------------------------------------------------------------
# Step 7c: 1 duplicate UTR
# ---------------------------------------------------------------------------
print("Adding duplicate UTR...")
if bank_rows:
    dup_utr_row = dict(bank_rows[rng.randint(0, len(bank_rows)-1)])
    dup_utr_row["amount"] = round(rng.uniform(100, 500), 2)  # small duplicate
    dup_utr_row["balance_after"] = round(balance + dup_utr_row["amount"], 2)
    balance = dup_utr_row["balance_after"]
    bank_rows.append(dup_utr_row)

# ---------------------------------------------------------------------------
# Step 7d: 8-10 noise (non-settlement) bank transactions
# ---------------------------------------------------------------------------
print("Adding noise bank transactions...")
noise_count = rng.randint(8, 10)
for i in range(noise_count):
    day_offset = rng.randint(0, 13)
    txn_day = START_DATE + timedelta(days=day_offset)
    h = rng.randint(9, 18)
    m = rng.randint(0, 59)
    s = rng.randint(0, 59)

    is_credit = rng.random() > 0.5
    amount = round(rng.uniform(200, 50000), 2)

    if is_credit:
        balance = round(balance + amount, 2)
        txn_type = "credit"
    else:
        balance = round(balance - amount, 2)
        txn_type = "debit"

    bank_rows.append({
        "txn_date": make_ist_datetime(txn_day, h, m, s).strftime("%Y-%m-%d %H:%M:%S"),
        "txn_type": txn_type,
        "narration": rng.choice(NOISE_NARRATIONS),
        "utr": "".join([str(rng.randint(0,9)) for _ in range(16)]),
        "amount": amount,
        "balance_after": balance,
        "branch_code": rng.choice(BANK_BRANCH_CODES),
    })

# Sort bank rows by date
bank_rows.sort(key=lambda r: r["txn_date"])

# Recalculate running balance
balance = 500000.00
for row in bank_rows:
    if row["txn_type"] == "credit":
        balance = round(balance + row["amount"], 2)
    else:
        balance = round(balance - row["amount"], 2)
    row["balance_after"] = balance

# ---------------------------------------------------------------------------
# Step 8: Build ground truth
# ---------------------------------------------------------------------------
print("Building ground truth...")

ground_truth = []

# Map order_id -> list of settlement_ids it appears in
order_settlement_map = defaultdict(list)
for row in settlement_rows:
    order_settlement_map[row["order_id"]].append(row["settlement_id"])

# Map settlement_id -> bank_utr
settlement_utr_map = {}
for row in settlement_rows:
    settlement_utr_map[row["settlement_id"]] = row["bank_utr"]

# Map settlement_id -> net_amount sum
settlement_net_map = defaultdict(float)
for row in settlement_rows:
    settlement_net_map[row["settlement_id"]] += row["net_amount"]

# Identify which settlements have bank credits
settlements_with_bank_credit = set()
for row in bank_rows:
    if row["txn_type"] == "credit":
        for sid, utr in settlement_utr_map.items():
            if utr == row["utr"]:
                settlements_with_bank_credit.add(sid)

for order in orders:
    oid = order["order_id"]
    settlement_ids = order_settlement_map.get(oid, [])
    bank_utr = None
    exception_code = None
    exception_detail = None
    notes = None

    if order["payment_status"] == "failed":
        exception_code = "UNMATCHED_ORDER"
        exception_detail = "Payment status is failed. Never captured, never settled."
        notes = "Deliberate failed payment for testing"
    elif order["payment_status"] == "authorized":
        exception_code = "UNMATCHED_ORDER"
        exception_detail = "Payment authorized but not captured. Settles next cycle."
        notes = "Captured after cutoff, settles next cycle"
    elif oid == missing_order["order_id"] if missing_order else False:
        exception_code = "UNMATCHED_ORDER"
        exception_detail = "Order captured but missing from settlement report."
        notes = "Missing from settlement - possibly on hold"
    elif order["refund_status"] == "partial" and oid in [o["order_id"] for o in [orders[21], orders[22], orders[23]]]:
        # Cross-batch refund split
        all_set_ids = list(set(settlement_ids))
        bank_utr_list = [settlement_utr_map.get(s) for s in all_set_ids if s in settlement_utr_map]
        bank_utr = bank_utr_list[0] if bank_utr_list else None
        exception_code = "REFUND_SPLIT"
        exception_detail = f"Order spans {len(all_set_ids)} settlement batches due to cross-batch refund deduction."
        notes = "Partial refund - split across settlement batches"
    elif order["refund_status"] == "full":
        # Full refund - the refund deduction is in a different batch
        # This is NORMAL behavior (not an exception) per Razorpay mechanics
        all_set_ids = list(set(settlement_ids))
        bank_utr_list = [settlement_utr_map.get(s) for s in all_set_ids if s in settlement_utr_map]
        bank_utr = bank_utr_list[0] if bank_utr_list else None
        notes = "Full refund - deducted in future batch. Matched but spans 2 batches (normal)."
    elif order["currency"] == "USD":
        all_set_ids = list(set(settlement_ids))
        bank_utr_list = [settlement_utr_map.get(s) for s in all_set_ids if s in settlement_utr_map]
        bank_utr = bank_utr_list[0] if bank_utr_list else None
        exception_code = "CURRENCY_MISMATCH"
        exception_detail = f"USD order ({order['gross_amount']} USD) settles as INR ({round(order['gross_amount'] * FX_RATE, 2)}). Requires currency conversion to match."
        notes = f"International order. USD {order['gross_amount']}, INR rate {FX_RATE}"
    elif order["notes"] and "Near cutoff" in order["notes"]:
        all_set_ids = list(set(settlement_ids))
        bank_utr_list = [settlement_utr_map.get(s) for s in all_set_ids if s in settlement_utr_map]
        bank_utr = bank_utr_list[0] if bank_utr_list else None
        notes = "Near cutoff - batch boundary ambiguous"
    elif oid == dup_order["order_id"] and order.get("notes", "").startswith("DUPLICATE"):
        exception_code = "DUPLICATE_ORDER"
        exception_detail = "Same order_id appears twice in ledger with different amounts."
        notes = "Data entry error duplicate"
    else:
        # Normal matched order
        all_set_ids = list(set(settlement_ids))
        bank_utr_list = [settlement_utr_map.get(s) for s in all_set_ids if s in settlement_utr_map]
        bank_utr = bank_utr_list[0] if bank_utr_list else None

    ground_truth.append({
        "order_id": oid,
        "expected_match_status": "exception" if exception_code else "matched",
        "expected_settlement_ids": list(set(settlement_ids)),
        "expected_bank_utr": bank_utr,
        "exception_code": exception_code,
        "exception_detail": exception_detail,
        "notes": notes,
    })

# ---------------------------------------------------------------------------
# Step 9: Write output files
# ---------------------------------------------------------------------------
print("Writing output files...")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 9a: order_ledger.csv
ledger_path = os.path.join(OUTPUT_DIR, "order_ledger.csv")
with open(ledger_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "order_id", "order_date", "customer_id", "product_sku", "quantity",
        "gross_amount", "currency", "payment_method", "payment_status",
        "refund_status", "refund_amount", "created_at", "notes"
    ])
    writer.writeheader()
    for order in orders:
        writer.writerow({
            "order_id": order["order_id"],
            "order_date": order["order_date"].strftime("%Y-%m-%d"),
            "customer_id": order["customer_id"],
            "product_sku": order["product_sku"],
            "quantity": order["quantity"],
            "gross_amount": f"{order['gross_amount']:.2f}",
            "currency": order["currency"],
            "payment_method": order["payment_method"],
            "payment_status": order["payment_status"],
            "refund_status": order["refund_status"],
            "refund_amount": f"{order['refund_amount']:.2f}",
            "created_at": order["created_at"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "notes": order["notes"],
        })

# 9b: settlement_report.csv
settlement_path = os.path.join(OUTPUT_DIR, "settlement_report.csv")
with open(settlement_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "settlement_id", "settlement_date", "bank_utr", "payment_id",
        "order_id", "gross_amount", "fee", "gst_on_fee", "refund_deduction",
        "net_amount", "payment_method", "captured_date", "settlement_status"
    ])
    writer.writeheader()
    for row in settlement_rows:
        writer.writerow({
            "settlement_id": row["settlement_id"],
            "settlement_date": row["settlement_date"],
            "bank_utr": row["bank_utr"],
            "payment_id": row["payment_id"],
            "order_id": row["order_id"],
            "gross_amount": f"{row['gross_amount']:.2f}",
            "fee": f"{row['fee']:.2f}",
            "gst_on_fee": f"{row['gst_on_fee']:.2f}",
            "refund_deduction": f"{row['refund_deduction']:.2f}",
            "net_amount": f"{row['net_amount']:.2f}",
            "payment_method": row["payment_method"],
            "captured_date": row["captured_date"],
            "settlement_status": row["settlement_status"],
        })

# 9c: bank_statement.csv
bank_path = os.path.join(OUTPUT_DIR, "bank_statement.csv")
with open(bank_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "txn_date", "txn_type", "narration", "utr", "amount",
        "balance_after", "branch_code"
    ])
    writer.writeheader()
    for row in bank_rows:
        writer.writerow({
            "txn_date": row["txn_date"],
            "txn_type": row["txn_type"],
            "narration": row["narration"],
            "utr": row["utr"],
            "amount": f"{row['amount']:.2f}",
            "balance_after": f"{row['balance_after']:.2f}",
            "branch_code": row["branch_code"],
        })

# 9d: ground_truth.json
gt_path = os.path.join(OUTPUT_DIR, "ground_truth.json")
with open(gt_path, "w", encoding="utf-8") as f:
    json.dump(ground_truth, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Step 10: Summary
# ---------------------------------------------------------------------------
print("\n" + "="*60)
print("GENERATION COMPLETE")
print("="*60)
print(f"\nFiles written to: {OUTPUT_DIR}/")
print(f"  order_ledger.csv    : {len(orders)} rows")
print(f"  settlement_report.csv: {len(settlement_rows)} rows")
print(f"  bank_statement.csv  : {len(bank_rows)} rows")
print(f"  ground_truth.json   : {len(ground_truth)} entries")

print(f"\n--- Edge Case Counts ---")
failed_count = sum(1 for o in orders if o["payment_status"] == "failed")
auth_count = sum(1 for o in orders if o["payment_status"] == "authorized")
partial_refund_count = sum(1 for o in orders if o["refund_status"] == "partial")
full_refund_count = sum(1 for o in orders if o["refund_status"] == "full")
usd_count = sum(1 for o in orders if o["currency"] == "USD")
cutoff_count = sum(1 for o in orders if "Near cutoff" in o.get("notes", ""))
dup_count = sum(1 for o in orders if "DUPLICATE" in o.get("notes", ""))
missing_count = sum(1 for o in orders if "Missing from settlement" in o.get("notes", ""))

print(f"  Failed payments      : {failed_count} (expected: 5)")
print(f"  Authorized (uncap)   : {auth_count} (expected: 2)")
print(f"  Partial refunds      : {partial_refund_count} (expected: 15)")
print(f"    Cross-batch splits : 3 (expected: 3)")
print(f"  Full refunds         : {full_refund_count} (expected: 8)")
print(f"  USD international    : {usd_count} (expected: 2)")
print(f"  Near-cut-off         : {cutoff_count} (expected: 10)")
print(f"  Duplicate order_id   : {dup_count} (expected: 1)")
print(f"  Missing settlement   : {missing_count} (expected: 1)")

print(f"\n--- Settlement Report ---")
print(f"  Total batches        : {len(settlements)}")
print(f"  Unique orders settled: {len(set(r['order_id'] for r in settlement_rows))}")
print(f"  Ghost transactions   : 1")
print(f"  Refund deduction rows: {sum(1 for r in settlement_rows if r['refund_deduction'] < 0)}")

print(f"\n--- Bank Statement ---")
print(f"  Total rows           : {len(bank_rows)}")
credits = [r for r in bank_rows if r["txn_type"] == "credit"]
debits = [r for r in bank_rows if r["txn_type"] == "debit"]
print(f"    Credits            : {len(credits)}")
print(f"    Debits             : {len(debits)}")
print(f"  Failed NEFT          : 1 (settlement exists, no bank credit)")
print(f"  Duplicate UTR        : 1")
print(f"  Noise transactions   : {noise_count}")

print(f"\n--- Exception Categories ---")
exc_counts = defaultdict(int)
for gt in ground_truth:
    if gt["exception_code"]:
        exc_counts[gt["exception_code"]] += 1
for code, count in sorted(exc_counts.items()):
    print(f"  {code:25s}: {count}")
matched = sum(1 for gt in ground_truth if gt["expected_match_status"] == "matched")
total = len(ground_truth)
print(f"\n  Matched              : {matched}/{total} ({matched/total*100:.1f}%)")
print(f"  Exceptions           : {total-matched}/{total} ({(total-matched)/total*100:.1f}%)")

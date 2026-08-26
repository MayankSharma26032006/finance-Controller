#!/usr/bin/env python3
"""Quick two-check validation: label mismatch + rounding variance."""
import csv
from collections import defaultdict

RAW = "raw"

def load_csv(name):
    with open(f"{RAW}/{name}", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

ledger = load_csv("order_ledger.csv")
settlement = load_csv("settlement_report.csv")

# ── CHECK 1: Label mismatch side-by-side ──────────────────────────────
print("=" * 70)
print("CHECK 1: Label Mismatch Confirmation")
print("=" * 70)

# Build settlement lookup: order_id -> list of (payment_method, gross_amount)
setl_lookup = defaultdict(list)
for r in settlement:
    setl_lookup[r["order_id"]].append((r["payment_method"], float(r["gross_amount"])))

ledger_methods = {r["order_id"]: r["payment_method"] for r in ledger}

# Pick order_ids in both, preferring rows with gross > 0 (not refund deductions)
shared_ids = sorted(set(ledger_methods.keys()) & set(setl_lookup.keys()))
picked = []
for oid in shared_ids:
    # Get the payment_method from the row with gross > 0
    for method, gross in setl_lookup[oid]:
        if gross > 0:
            picked.append((oid, ledger_methods[oid], method))
            break
    if len(picked) >= 5:
        break

print(f"  {'order_id':35s}  {'ledger method':22s}  {'settlement method':22s}  differ?")
print("  " + "-" * 88)
for oid, lm, sm in picked:
    differs = "YES" if lm != sm else "NO"
    print(f"  {oid:35s}  {lm:22s}  {sm:22s}  {differs}")

# Full summary
mismatch = 0
identical = 0
for oid in shared_ids:
    ledger_m = ledger_methods[oid]
    for method, gross in setl_lookup[oid]:
        if gross > 0:
            if ledger_m != method:
                mismatch += 1
            else:
                identical += 1
            break

print()
print(f"  Total shared orders with gross > 0: {mismatch + identical}")
print(f"  Mismatched (correct):  {mismatch}")
print(f"  Identical (unexpected): {identical}")
print(f"  Status: {'LABEL MISMATCH IS PRESENT (correct)' if mismatch > 0 else 'NO MISMATCH (bug)'}")
print()

# ── CHECK 2: Batch arithmetic rounding variance ───────────────────────
print("=" * 70)
print("CHECK 2: Batch Arithmetic Rounding Variance")
print("=" * 70)

batch_groups = defaultdict(list)
for r in settlement:
    batch_groups[r["settlement_id"]].append(r)

exactly_zero = 0
nonzero = 0
max_abs_diff = 0.0
worst_sid = None

for sid, rows in batch_groups.items():
    gross_sum = sum(float(r["gross_amount"]) for r in rows)
    fee_sum = sum(float(r["fee"]) for r in rows)
    gst_sum = sum(float(r["gst_on_fee"]) for r in rows)
    refund_sum = sum(float(r["refund_deduction"]) for r in rows)
    net_sum = sum(float(r["net_amount"]) for r in rows)
    expected = round(gross_sum - fee_sum - gst_sum + refund_sum, 2)
    diff = round(expected - net_sum, 6)
    abs_diff = abs(diff)

    if abs_diff < 0.0001:
        exactly_zero += 1
    else:
        nonzero += 1
        if abs_diff > max_abs_diff:
            max_abs_diff = abs_diff
            worst_sid = sid
        if nonzero <= 10:
            print(f"  NON-ZERO: {sid}")
            print(f"           gross={gross_sum:.2f}  fee={fee_sum:.2f}  gst={gst_sum:.2f}  refund={refund_sum:.2f}")
            print(f"           expected_net={expected:.2f}  actual_net_sum={net_sum:.2f}  diff={diff:+.6f}")

total = len(batch_groups)
print()
print(f"  Total batches:     {total}")
print(f"  Exactly zero diff: {exactly_zero}")
print(f"  Non-zero diff:     {nonzero}")
print(f"  Max |diff|:        {max_abs_diff:.6f}")
if worst_sid:
    print(f"  Worst batch:       {worst_sid}")
if nonzero == 0:
    print("  Result: ALL 91 batches have perfect arithmetic (no rounding drift)")
else:
    print(f"  Result: {nonzero}/{total} batches have rounding drift (max {max_abs_diff:.4f})")

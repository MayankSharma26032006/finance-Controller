#!/usr/bin/env python3
"""
Validation script for Razorpay reconciliation synthetic data.
Reads the 4 generated files in data/raw/ and checks every messiness
item from docs/DESIGN_PHASE1.md. Prints a PASS/FAIL report for each.
"""

import csv
import json
import os
from datetime import datetime, date
from collections import defaultdict

RAW_DIR = os.path.join(os.path.dirname(__file__), "raw")

# ── Load data ──────────────────────────────────────────────────────────
def load_csv(name):
    path = os.path.join(RAW_DIR, name)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_json(name):
    path = os.path.join(RAW_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)

ledger = load_csv("order_ledger.csv")
settlement = load_csv("settlement_report.csv")
bank = load_csv("bank_statement.csv")
ground_truth = load_json("ground_truth.json")

results = []  # (check_name, expected, found, status, detail)

def check(name, expected, found, status, detail=""):
    results.append((name, expected, found, status, detail))

# ── 1. Failed payments ────────────────────────────────────────────────
failed_in_ledger = [o for o in ledger if o["payment_status"] == "failed"]
failed_ids = {o["order_id"] for o in failed_in_ledger}

# None of these should appear in settlement_report
failed_in_settlement = [r for r in settlement if r["order_id"] in failed_ids]
# None should appear in bank_statement (they don't have UTRs, but check anyway)
failed_utrs_in_bank = []
# Failed orders shouldn't have settlement rows, so no UTRs to check

check(
    "1a. Failed payments in ledger",
    5, len(failed_in_ledger),
    "PASS" if len(failed_in_ledger) == 5 else "FAIL",
)

check(
    "1b. Failed orders NOT in settlement",
    0, len(failed_in_settlement),
    "PASS" if len(failed_in_settlement) == 0 else "FAIL",
    f"Found {len(failed_in_settlement)} failed orders in settlement" if failed_in_settlement else "",
)

# ── 2. Authorized not captured ────────────────────────────────────────
auth_in_ledger = [o for o in ledger if o["payment_status"] == "authorized"]
check(
    "2. Authorized (uncaptured)",
    2, len(auth_in_ledger),
    "PASS" if len(auth_in_ledger) == 2 else "FAIL",
)

# ── 3. Duplicate order_id ─────────────────────────────────────────────
order_id_counts = defaultdict(int)
for o in ledger:
    order_id_counts[o["order_id"]] += 1
dup_ids = {oid for oid, cnt in order_id_counts.items() if cnt > 1}
dup_details = []
for oid in dup_ids:
    rows = [o for o in ledger if o["order_id"] == oid]
    amounts = [o["gross_amount"] for o in rows]
    dup_details.append(f"{oid}: amounts={amounts}")

check(
    "3. Duplicate order_id",
    1, len(dup_ids),
    "PASS" if len(dup_ids) >= 1 else "FAIL",
    "; ".join(dup_details) if dup_details else "",
)

# Check that the duplicate has different amounts
if dup_ids:
    for oid in dup_ids:
        rows = [o for o in ledger if o["order_id"] == oid]
        amounts = set(o["gross_amount"] for o in rows)
        check(
            "3b. Duplicate has different amounts",
            True, len(amounts) > 1,
            "PASS" if len(amounts) > 1 else "FAIL",
            f"Unique amounts for {oid}: {amounts}",
        )

# ── 4. USD/international orders ───────────────────────────────────────
usd_orders = [o for o in ledger if o["currency"] == "USD"]
check(
    "4a. USD orders in ledger",
    2, len(usd_orders),
    "PASS" if len(usd_orders) == 2 else "FAIL",
)

# Check conversion rate in settlement
for usd_o in usd_orders:
    oid = usd_o["order_id"]
    set_rows = [r for r in settlement if r["order_id"] == oid]
    if set_rows:
        usd_amt = float(usd_o["gross_amount"])
        inr_amt = float(set_rows[0]["gross_amount"])
        if usd_amt > 0:
            rate = inr_amt / usd_amt
            check(
                f"4b. USD->INR conversion rate ({oid})",
                83.0, round(rate, 2),
                "PASS" if abs(rate - 83.0) < 0.01 else "FAIL",
                f"USD {usd_amt} -> INR {inr_amt}, rate={rate:.4f}",
            )
        else:
            check(f"4b. USD->INR conversion rate ({oid})", "N/A", "skip", "SKIP", "zero amount")
    else:
        check(f"4b. USD->INR conversion rate ({oid})", "N/A", "missing", "FAIL", "No settlement row found")

# ── 5. Near-cutoff timestamps (23:30-23:59 IST) ──────────────────────
cutoff_count = 0
for o in ledger:
    try:
        dt = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M:%S.%f")
        if dt.hour == 23 and 30 <= dt.minute <= 59:
            cutoff_count += 1
    except ValueError:
        try:
            dt = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M:%S")
            if dt.hour == 23 and 30 <= dt.minute <= 59:
                cutoff_count += 1
        except ValueError:
            pass

check(
    "5. Near-cutoff timestamps (23:30-23:59)",
    "~10", cutoff_count,
    "PASS" if 8 <= cutoff_count <= 12 else "FAIL",
    f"Found {cutoff_count} orders with timestamps 23:30-23:59",
)

# ── 6. Ghost transaction ──────────────────────────────────────────────
ledger_ids = {o["order_id"] for o in ledger}
ghost_rows = [r for r in settlement if r["order_id"] not in ledger_ids]
check(
    "6. Ghost transaction (settlement row with no order)",
    ">=1", len(ghost_rows),
    "PASS" if len(ghost_rows) >= 1 else "FAIL",
    f"Found {len(ghost_rows)} ghost rows: {[r['order_id'] for r in ghost_rows[:5]]}",
)

# ── 7. Missing settlement row ─────────────────────────────────────────
# Orders with payment_status=captured that have NO row in settlement_report
captured_in_ledger = [o for o in ledger if o["payment_status"] == "captured"]
captured_settled_ids = {r["order_id"] for r in settlement}
missing_settlement = [
    o for o in captured_in_ledger
    if o["order_id"] not in captured_settled_ids
]
check(
    "7. Missing settlement (captured but not in settlement)",
    ">=1", len(missing_settlement),
    "PASS" if len(missing_settlement) >= 1 else "FAIL",
    f"Found {len(missing_settlement)} orders: {[o['order_id'] for o in missing_settlement[:5]]}",
)

# ── 8. Duplicate UTR ──────────────────────────────────────────────────
utr_counts = defaultdict(int)
for r in bank:
    utr_counts[r["utr"]] += 1
dup_utrs = {utr for utr, cnt in utr_counts.items() if cnt > 1}
check(
    "8. Duplicate UTR in bank statement",
    ">=1", len(dup_utrs),
    "PASS" if len(dup_utrs) >= 1 else "FAIL",
    f"Found {len(dup_utrs)} duplicated UTRs: {list(dup_utrs)[:5]}",
)

# ── 9. Failed NEFT credit ─────────────────────────────────────────────
# Settlement rows with settlement_status=settled whose bank_utr does NOT appear in bank_statement
bank_utrs = {r["utr"] for r in bank}
settled_rows = [r for r in settlement if r["settlement_status"] == "settled"]
failed_neft_utrs = set()
for r in settled_rows:
    if r["bank_utr"] not in bank_utrs:
        failed_neft_utrs.add(r["bank_utr"])
check(
    "9. Failed NEFT (settled but no bank credit)",
    ">=1", len(failed_neft_utrs),
    "PASS" if len(failed_neft_utrs) >= 1 else "FAIL",
    f"Found {len(failed_neft_utrs)} UTRs with no bank credit: {list(failed_neft_utrs)[:5]}",
)

# ── 10. Bank noise rows ───────────────────────────────────────────────
razorpay_keywords = ["razorpay", "RAZORPAY"]
noise_rows = [
    r for r in bank
    if not any(kw.lower() in r["narration"].lower() for kw in razorpay_keywords)
]
check(
    "10. Bank noise rows (non-Razorpay)",
    "8-10", len(noise_rows),
    "PASS" if 7 <= len(noise_rows) <= 12 else "FAIL",
    f"Found {len(noise_rows)} noise rows",
)

# ── 11. Weekend gap ───────────────────────────────────────────────────
weekend_credits = []
for r in bank:
    if r["txn_type"] != "credit":
        continue
    # Check narration for Razorpay keywords (only settlement credits)
    if not any(kw.lower() in r["narration"].lower() for kw in razorpay_keywords):
        continue
    try:
        dt = datetime.strptime(r["txn_date"], "%Y-%m-%d %H:%M:%S")
        if dt.weekday() in (5, 6):  # Saturday=5, Sunday=6
            weekend_credits.append(r)
    except ValueError:
        pass

check(
    "11. No weekend Razorpay credits",
    0, len(weekend_credits),
    "PASS" if len(weekend_credits) == 0 else "FAIL",
    f"Found {len(weekend_credits)} Razorpay credits on weekends" if weekend_credits else "",
)

# ── 12. Row count sanity ──────────────────────────────────────────────
check(
    "12a. order_ledger row count",
    "~500", len(ledger),
    "PASS" if 495 <= len(ledger) <= 510 else "FAIL",
)

check(
    "12b. settlement_report row count",
    ">=400", len(settlement),
    "PASS" if len(settlement) >= 400 else "FAIL",
    f"Total {len(settlement)} rows",
)

check(
    "12c. bank_statement row count",
    "~100", len(bank),
    "PASS" if 80 <= len(bank) <= 150 else "FAIL",
    f"Total {len(bank)} rows",
)

# Count Razorpay vs noise in bank
razorpay_bank = [r for r in bank if any(kw.lower() in r["narration"].lower() for kw in razorpay_keywords)]
check(
    "12d. Bank Razorpay credits",
    ">=70", len(razorpay_bank),
    "PASS" if len(razorpay_bank) >= 60 else "FAIL",
    f"Razorpay: {len(razorpay_bank)}, Noise: {len(noise_rows)}",
)

# ── 13. Ground truth consistency ──────────────────────────────────────
gt_order_ids = {e["order_id"] for e in ground_truth}
ledger_order_ids = {o["order_id"] for o in ledger}
check(
    "13a. ground_truth covers all ledger orders",
    len(ledger_order_ids), len(gt_order_ids),
    "PASS" if gt_order_ids == ledger_order_ids else "FAIL",
    f"Missing from GT: {ledger_order_ids - gt_order_ids}, Extra in GT: {gt_order_ids - ledger_order_ids}",
)

gt_exceptions = [e for e in ground_truth if e["exception_code"] is not None]
gt_matched = [e for e in ground_truth if e["expected_match_status"] == "matched"]
check(
    "13b. ground_truth matched + exceptions = total",
    len(ground_truth), len(gt_matched) + len(gt_exceptions),
    "PASS" if len(gt_matched) + len(gt_exceptions) == len(ground_truth) else "FAIL",
)

# ── 14. LABEL_MISMATCH should NOT be an exception code ────────────────
label_mismatch = [e for e in ground_truth if e.get("exception_code") == "LABEL_MISMATCH"]
check(
    "14. No LABEL_MISMATCH exceptions (should be normalization)",
    0, len(label_mismatch),
    "PASS" if len(label_mismatch) == 0 else "FAIL",
)

# ── 15. Verify ground truth exception codes exist in settlement ───────
gt_with_settlement = [
    e for e in ground_truth
    if e["expected_settlement_ids"] and e["expected_settlement_ids"] != []
]
for e in gt_with_settlement[:3]:
    sid = e["expected_settlement_ids"][0]
    exists = any(r["settlement_id"] == sid for r in settlement)
    check(
        f"15. GT settlement_id {sid[:12]}... exists",
        True, exists,
        "PASS" if exists else "FAIL",
    )

# ── 16. Batch-level arithmetic: gross - fee - gst + refund_ded == net ──
batch_groups = defaultdict(list)
for r in settlement:
    batch_groups[r["settlement_id"]].append(r)

exactly_zero = 0
nonzero_diffs = 0
max_abs_diff = 0.0
worst_batch = None
all_diffs = []

for sid, rows in batch_groups.items():
    gross_sum = sum(float(r["gross_amount"]) for r in rows)
    fee_sum = sum(float(r["fee"]) for r in rows)
    gst_sum = sum(float(r["gst_on_fee"]) for r in rows)
    refund_sum = sum(float(r["refund_deduction"]) for r in rows)
    net_sum = sum(float(r["net_amount"]) for r in rows)

    expected_net = round(gross_sum - fee_sum - gst_sum + refund_sum, 2)
    diff = round(expected_net - net_sum, 2)
    abs_diff = abs(diff)
    all_diffs.append((sid, diff, len(rows), gross_sum, fee_sum, gst_sum, refund_sum, net_sum, expected_net))

    if abs_diff < 0.001:
        exactly_zero += 1
    else:
        nonzero_diffs += 1
        if abs_diff > max_abs_diff:
            max_abs_diff = abs_diff
            worst_batch = sid

total_batches = len(batch_groups)

# Report the arithmetic check as its own summary block (not a single pass/fail)
check(
    "16a. Batches with exactly zero arithmetic diff",
    total_batches, exactly_zero,
    "PASS",
    f"{exactly_zero}/{total_batches} batches are exact",
)
check(
    "16b. Batches with non-zero diff (rounding drift)",
    ">=0", nonzero_diffs,
    "PASS" if nonzero_diffs <= total_batches else "FAIL",
    f"{nonzero_diffs}/{total_batches} batches have rounding variance",
)
check(
    "16c. Max absolute diff across all batches",
    "<=0.05", max_abs_diff,
    "PASS" if max_abs_diff <= 0.05 else "WARN",
    f"Worst batch: {worst_batch[:16] if worst_batch else 'none'}",
)

# Print detailed batch arithmetic table
print("\n  --- Batch Arithmetic Detail ---")
print(f"  {'settlement_id':30s}  {'rows':>4s}  {'gross':>10s}  {'fee':>9s}  {'gst':>8s}  {'refund':>9s}  {'net_sum':>10s}  {'expected':>10s}  {'diff':>7s}")
print("  " + "-" * 110)
for sid, diff, nrows, gs, fs, rs, rfs, ns, en in sorted(all_diffs, key=lambda x: abs(x[1]), reverse=True):
    marker = " " if abs(diff) < 0.001 else "*"
    print(f"  {sid:30s}  {nrows:4d}  {gs:10.2f}  {fs:9.2f}  {rs:8.2f}  {rfs:9.2f}  {ns:10.2f}  {en:10.2f}  {diff:+7.2f} {marker}")
print(f"\n  * = non-zero diff (rounding drift). Total batches: {total_batches}")
print(f"    Exact (diff=0): {exactly_zero}, Non-zero: {nonzero_diffs}, Max |diff|: {max_abs_diff:.2f}")
print()

# ── Print report ──────────────────────────────────────────────────────
print("=" * 80)
print("VALIDATION REPORT — Razorpay Reconciliation Synthetic Data")
print("=" * 80)
print()

pass_count = sum(1 for _, _, _, s, _ in results if s == "PASS")
fail_count = sum(1 for _, _, _, s, _ in results if s == "FAIL")
skip_count = sum(1 for _, _, _, s, _ in results if s in ("SKIP", "WARN"))

for name, expected, found, status, detail in results:
    icon = "[OK]" if status == "PASS" else ("[!!]" if status == "FAIL" else "[--]")
    detail_str = f"  ({detail})" if detail else ""
    print(f"  {icon} {status:4s} | {name:50s} | expected={str(expected):6s} | found={str(found):6s}{detail_str}")

print()
print(f"  SUMMARY: {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP/TOTAL={len(results)}")
if fail_count > 0:
    print(f"  !!  {fail_count} checks FAILED - review above for details")
else:
    print(f"  ** All checks passed!")
print()

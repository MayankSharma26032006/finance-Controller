#!/usr/bin/env python3
"""
Steps 4/5/6: Exception Detection and Cross-Source Consistency Checks.
Ghost transactions, consistency soft flags, and match_log compilation.
"""

import json
import os
from datetime import datetime
from collections import defaultdict
from preprocessor import (
    to_float, normalize_label, working_days_between, OUTPUT_DIR
)


def detect_ghost_transactions(batch_results):
    """
    Step 5: Mark batches containing ghost transactions (settlement rows
    with unknown order_ids) as needs_review.
    """
    for sid, result in batch_results.items():
        if result["ghost_order_ids"]:
            result["confidence"] = "needs_review"
            result["soft_flags"].append(
                f"Ghost transaction(s): {result['ghost_order_ids']}"
            )


def check_consistency(order_results, order_data):
    """
    Step 6: Cross-source consistency checks (soft flags only, never blocking).
    Checks date tolerance, label normalization, and bank txn timing.
    """
    ledger_by_id = order_data["ledger_by_id"]
    settlement_by_id = order_data["settlement_by_id"]
    bank_credits_by_utr = order_data["bank_credits_by_utr"]

    for oid, result in order_results.items():
        if result["match_status"] != "matched":
            continue

        order = ledger_by_id[oid][0]
        setl_rows = settlement_by_id.get(oid, [])
        if not setl_rows:
            continue

        # 6a. Label normalization check
        ledger_method = normalize_label(order["payment_method"])
        setl_method = setl_rows[0]["payment_method"]
        if ledger_method != setl_method:
            result["soft_flags"].append(
                f"Label mismatch after normalization: ledger={ledger_method}, settlement={setl_method}"
            )

        # 6b. Date tolerance: settlement_date within 5 working days of order_date
        order_date = datetime.strptime(order["order_date"], "%Y-%m-%d").date()
        for sr in setl_rows:
            setl_date = datetime.strptime(sr["settlement_date"], "%Y-%m-%d").date()
            wd = working_days_between(order_date, setl_date)
            if wd > 5:
                result["soft_flags"].append(
                    f"Settlement date {sr['settlement_date']} is {wd} working days after order date (expected <=3)"
                )

        # 6c. captured_date == order_date
        for sr in setl_rows:
            if sr["captured_date"] != order["order_date"]:
                result["soft_flags"].append(
                    f"captured_date {sr['captured_date']} != order_date {order['order_date']}"
                )

        # 6d. Bank txn_date within 1 working day of settlement_date (T+1 rule)
        bank_utr = setl_rows[0]["bank_utr"]
        bank_candidates = bank_credits_by_utr.get(bank_utr, [])
        if bank_candidates:
            bank_row = min(
                bank_candidates,
                key=lambda r: abs(to_float(r["amount"]) - float(setl_rows[0]["net_amount"]))
            )
            bank_date = datetime.strptime(
                bank_row["txn_date"], "%Y-%m-%d %H:%M:%S"
            ).date()
            setl_date = datetime.strptime(
                setl_rows[0]["settlement_date"], "%Y-%m-%d"
            ).date()
            wd = working_days_between(setl_date, bank_date)
            if wd > 2:
                result["soft_flags"].append(
                    f"Bank txn date {bank_row['txn_date'][:10]} is {wd} working days after settlement date (expected <=1)"
                )


def compile_match_log(order_results, batch_results):
    """
    Step 13: Compile and write match_log.json.
    Returns the match_log list.
    """
    match_log = []

    for oid in sorted(order_results.keys()):
        match_log.append(order_results[oid])

    for sid in sorted(batch_results.keys()):
        match_log.append(batch_results[sid])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, "match_log.json")
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        json.dump(match_log, f, indent=2, ensure_ascii=False)

    return match_log, output_path


def print_summary(order_results, batch_results, match_log, output_path):
    """Print the final matching summary."""
    print()
    print("=" * 60)
    print("MATCHING COMPLETE")
    print("=" * 60)

    # Order summary
    order_confidence = defaultdict(int)
    order_exception = defaultdict(int)
    for oid, result in order_results.items():
        order_confidence[result["confidence"]] += 1
        if result["exception_code"]:
            order_exception[result["exception_code"]] += 1

    order_count = len(order_results)
    print(f"\nOrder-Level Results ({order_count} orders):")
    print(f"  matched:             {order_confidence.get('matched', 0)}")
    print(f"  matched_with_note:   {order_confidence.get('matched_with_note', 0)}")
    print(f"  needs_review:        {order_confidence.get('needs_review', 0)}")
    print(f"  hard_exception:      {order_confidence.get('hard_exception', 0)}")
    print(f"  TOTAL:               {sum(order_confidence.values())}")
    print(f"\n  Exception codes:")
    for code, count in sorted(order_exception.items()):
        print(f"    {code}: {count}")

    # Settlement summary
    settle_status = defaultdict(int)
    settle_confidence = defaultdict(int)
    for sid, result in batch_results.items():
        settle_status[result["status"]] += 1
        settle_confidence[result["confidence"]] += 1

    settle_count = len(batch_results)
    print(f"\nSettlement-Level Results ({settle_count} batches):")
    print(f"  batch_credited:   {settle_status.get('batch_credited', 0)}")
    print(f"  batch_neft_failed: {settle_status.get('batch_neft_failed', 0)}")
    print(f"  batch_no_credit:  {settle_status.get('batch_no_credit', 0)}")
    print(f"\n  Confidence:")
    for conf, count in sorted(settle_confidence.items()):
        print(f"    {conf}: {count}")

    # Soft flags
    print(f"\nUnexpected/Soft Flags:")
    flagged_orders = [(oid, r) for oid, r in order_results.items() if r["soft_flags"]]
    flagged_batches = [(sid, r) for sid, r in batch_results.items() if r["soft_flags"]]
    for oid, r in flagged_orders:
        for flag in r["soft_flags"]:
            print(f"  ORDER {oid}: {flag}")
    for sid, r in flagged_batches:
        for flag in r["soft_flags"]:
            print(f"  BATCH {sid}: {flag}")
    if not flagged_orders and not flagged_batches:
        print("  (none)")

    print(f"\nOutput written to: {output_path}")
    print(f"Total entries in match_log: {len(match_log)}")

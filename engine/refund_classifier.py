#!/usr/bin/env python3
"""
Step 3: Refund Classification.
Classifies refund type and computes expected residual for matched orders.
"""

from preprocessor import to_float


def classify_refund(setl_rows, ledger_gross):
    """
    Classify refund type for an order based on its settlement rows.

    Args:
        setl_rows: list of settlement row dicts for this order
        ledger_gross: order's gross amount in INR (after currency conversion)

    Returns:
        dict with keys: refund_type, order_residual, expected_residual,
                        original_rows, refund_rows
    """
    original_rows = [r for r in setl_rows if to_float(r["gross_amount"]) > 0]
    refund_rows = [r for r in setl_rows if to_float(r["refund_deduction"]) < 0]

    refund_type = "none"
    order_residual = round(sum(to_float(r["net_amount"]) for r in setl_rows), 2)
    expected_residual = None

    if len(original_rows) == 1 and len(refund_rows) >= 1:
        orig = original_rows[0]
        orig_gross = to_float(orig["gross_amount"])
        orig_fee = to_float(orig["fee"])
        orig_gst = to_float(orig["gst_on_fee"])

        for ref in refund_rows:
            ref_amount = abs(to_float(ref["refund_deduction"]))

            if ref_amount == orig_gross:
                refund_type = "FULL_REFUND"
                expected_residual = round(-(orig_fee + orig_gst), 2)
            elif ref_amount < orig_gross:
                if ref["settlement_id"] != orig["settlement_id"]:
                    refund_type = "REFUND_SPLIT"
                else:
                    refund_type = "PARTIAL_REFUND"
                expected_residual = round(orig_gross - ref_amount, 2)

    elif len(original_rows) == 0 and len(refund_rows) > 0:
        refund_type = "REFUND_ONLY"
        expected_residual = order_residual

    return {
        "refund_type": refund_type,
        "order_residual": order_residual,
        "expected_residual": expected_residual,
        "original_rows": original_rows,
        "refund_rows": refund_rows,
    }

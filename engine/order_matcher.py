#!/usr/bin/env python3
"""
Layer 2: Order-Level Matching (order_id <-> settlement rows).
Includes duplicate detection, payment_status pre-checks, and refund classification.
"""

from collections import defaultdict
from preprocessor import to_float, ledger_gross_inr, FX_RATE, ORDER_TOLERANCE
from refund_classifier import classify_refund


def match_orders(ledger_by_id, settlement_by_id):
    """
    Match orders to settlement rows.

    Args:
        ledger_by_id: dict[order_id] -> list[ledger rows]
        settlement_by_id: dict[order_id] -> list[settlement rows]

    Returns:
        dict[order_id] -> order_result dict
    """
    order_results = {}

    # Track duplicate order_ids
    order_id_counts = defaultdict(int)
    for oid, rows in ledger_by_id.items():
        order_id_counts[oid] += len(rows)

    for oid, rows in ledger_by_id.items():
        # Handle duplicates
        if order_id_counts[oid] > 1:
            conflicting_rows = []
            for r in rows:
                conflicting_rows.append({
                    "gross_amount": r["gross_amount"],
                    "quantity": r["quantity"],
                    "customer_id": r["customer_id"],
                    "created_at": r["created_at"],
                    "payment_status": r["payment_status"],
                })
            amounts = list(set(r["gross_amount"] for r in rows))
            # CLAIM 1 FIX: Look up real settlement data even for duplicates
            dup_setl_rows = settlement_by_id.get(oid, [])
            dup_settlement_ids = list(set(r["settlement_id"] for r in dup_setl_rows)) if dup_setl_rows else []
            dup_bank_utr = dup_setl_rows[0]["bank_utr"] if dup_setl_rows else None
            order_results[oid] = {
                "order_id": oid,
                "result_type": "order",
                "match_status": "exception",
                "exception_code": "DUPLICATE_ORDER",
                "settlement_ids": dup_settlement_ids,
                "bank_utr": dup_bank_utr,
                "refund_type": None,
                "order_residual": None,
                "expected_residual": None,
                "confidence": "needs_review",
                "detail": f"Order appears {order_id_counts[oid]} times in ledger with conflicting amounts: {amounts}" + (f"; settlement rows found: {len(dup_setl_rows)} (settlement_ids={dup_settlement_ids})" if dup_setl_rows else "; no settlement rows found"),
                "conflicting_ledger_rows": conflicting_rows,
                "soft_flags": [],
            }
            continue

        order = rows[0]
        payment_status = order["payment_status"]
        currency = order["currency"]
        ledger_gross = ledger_gross_inr(order)

        # Find settlement rows for this order
        setl_rows = settlement_by_id.get(oid, [])

        # Step 2b: payment_status pre-check
        if payment_status == "failed":
            if len(setl_rows) > 0:
                order_results[oid] = {
                    "order_id": oid,
                    "result_type": "order",
                    "match_status": "exception",
                    "exception_code": "UNEXPECTED_SETTLEMENT",
                    "settlement_ids": list(set(r["settlement_id"] for r in setl_rows)),
                    "bank_utr": setl_rows[0]["bank_utr"] if setl_rows else None,
                    "refund_type": None,
                    "order_residual": None,
                    "expected_residual": None,
                    "confidence": "needs_review",
                    "detail": f"Failed order has {len(setl_rows)} settlement rows (unexpected)",
                    "soft_flags": [],
                }
            else:
                order_results[oid] = {
                    "order_id": oid,
                    "result_type": "order",
                    "match_status": "exception",
                    "exception_code": "UNMATCHED_ORDER",
                    "settlement_ids": [],
                    "bank_utr": None,
                    "refund_type": None,
                    "order_residual": None,
                    "expected_residual": None,
                    "confidence": "hard_exception",
                    "detail": "Payment failed, no settlement expected",
                    "soft_flags": [],
                }
            continue

        if payment_status == "authorized":
            if len(setl_rows) > 0:
                order_results[oid] = {
                    "order_id": oid,
                    "result_type": "order",
                    "match_status": "exception",
                    "exception_code": "UNEXPECTED_SETTLEMENT",
                    "settlement_ids": list(set(r["settlement_id"] for r in setl_rows)),
                    "bank_utr": setl_rows[0]["bank_utr"] if setl_rows else None,
                    "refund_type": None,
                    "order_residual": None,
                    "expected_residual": None,
                    "confidence": "needs_review",
                    "detail": f"Authorized order has {len(setl_rows)} settlement rows (unexpected)",
                    "soft_flags": [],
                }
            else:
                order_results[oid] = {
                    "order_id": oid,
                    "result_type": "order",
                    "match_status": "exception",
                    "exception_code": "UNMATCHED_ORDER",
                    "settlement_ids": [],
                    "bank_utr": None,
                    "refund_type": None,
                    "order_residual": None,
                    "expected_residual": None,
                    "confidence": "hard_exception",
                    "detail": "Payment authorized but not captured, settles next cycle",
                    "soft_flags": [],
                }
            continue

        # payment_status == "captured"
        if len(setl_rows) == 0:
            order_results[oid] = {
                "order_id": oid,
                "result_type": "order",
                "match_status": "exception",
                "exception_code": "UNMATCHED_ORDER",
                "settlement_ids": [],
                "bank_utr": None,
                "refund_type": None,
                "order_residual": None,
                "expected_residual": None,
                "confidence": "hard_exception",
                "detail": "Captured order missing from settlement report",
                "soft_flags": [],
            }
            continue

        # Step 2c: Amount validation
        original_rows = [r for r in setl_rows if to_float(r["gross_amount"]) > 0]
        amount_mismatch = False
        for orig in original_rows:
            setl_gross = to_float(orig["gross_amount"])
            if abs(ledger_gross - setl_gross) > ORDER_TOLERANCE:
                amount_mismatch = True

        # Step 3: Refund classification
        refund = classify_refund(setl_rows, ledger_gross)
        refund_type = refund["refund_type"]
        order_residual = refund["order_residual"]
        expected_residual = refund["expected_residual"]
        settlement_ids = list(set(r["settlement_id"] for r in setl_rows))

        # Determine exception code and confidence
        exception_code = None
        detail = None
        confidence = "matched"

        if amount_mismatch:
            exception_code = "AMOUNT_MISMATCH"
            confidence = "needs_review"
            detail = f"Ledger gross {ledger_gross:.2f} != settlement gross (after conversion)"
        elif refund_type == "REFUND_SPLIT":
            exception_code = "REFUND_SPLIT"
            confidence = "matched_with_note"
            detail = f"Partial refund split across {len(settlement_ids)} batches"
        elif refund_type == "FULL_REFUND":
            exception_code = None
            confidence = "matched"
            detail = f"Full refund: residual {order_residual:.2f} == expected {expected_residual:.2f}" if expected_residual is not None else "Full refund"
        elif refund_type == "REFUND_ONLY":
            exception_code = "REFUND_ONLY"
            confidence = "needs_review"
            detail = "Settlement has refund rows but no original charge row"

        # CURRENCY_MISMATCH is informational - the amounts match after conversion
        if currency == "USD" and not amount_mismatch:
            exception_code = "CURRENCY_MISMATCH"
            confidence = "matched_with_note"
            detail = f"USD order converted at {FX_RATE}: {order['gross_amount']} USD -> {ledger_gross:.2f} INR"

        # CLAIM 3 FIX: UNRECORDED_REFUND - ledger claims refund but no settlement refund evidence
        ledger_refund_status = order.get("refund_status", "none")
        ledger_refund_amount = to_float(order.get("refund_amount", "0"))
        if ledger_refund_status in ("partial", "full") and refund_type == "none" and ledger_refund_amount > 0:
            exception_code = "UNRECORDED_REFUND"
            confidence = "needs_review"
            detail = f"Ledger claims {ledger_refund_status} refund of {ledger_refund_amount:.2f} but no refund_deduction row in settlement report"

        match_status = "exception" if exception_code else "matched"
        bank_utr = setl_rows[0]["bank_utr"] if setl_rows else None

        order_results[oid] = {
            "order_id": oid,
            "result_type": "order",
            "match_status": match_status,
            "exception_code": exception_code,
            "settlement_ids": settlement_ids,
            "bank_utr": bank_utr,
            "refund_type": refund_type,
            "order_residual": order_residual,
            "expected_residual": expected_residual,
            "confidence": confidence,
            "detail": detail,
            "soft_flags": [],
        }

    return order_results

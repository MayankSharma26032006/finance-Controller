#!/usr/bin/env python3
"""
Layer 1: Batch-Level Matching (settlement_id <-> bank_utr).
Matches settlement batches to bank credits within tolerance.
"""

from collections import defaultdict
from preprocessor import to_float, BATCH_TOLERANCE


def match_batches(settlement_by_sid, bank_credits_by_utr, ledger_ids):
    """
    Match settlement batches to bank credits.

    Args:
        settlement_by_sid: dict[settlement_id] -> list[settlement_rows]
        bank_credits_by_utr: dict[utr] -> list[bank_credit_rows]
        ledger_ids: set of known order_ids from the ledger

    Returns:
        dict[settlement_id] -> batch_result dict
    """
    batch_results = {}

    for sid, rows in settlement_by_sid.items():
        batch_net = sum(to_float(r["net_amount"]) for r in rows)
        batch_net = round(batch_net, 2)
        bank_utr = rows[0]["bank_utr"]

        bank_credit_candidates = bank_credits_by_utr.get(bank_utr, [])
        row_count = len(rows)
        order_ids = list(set(r["order_id"] for r in rows))

        # Check for ghost transaction in this batch
        ghost_order_ids = [oid for oid in order_ids if oid not in ledger_ids]

        # Pick the bank credit row closest to batch_net (handles duplicate UTRs)
        bank_credit = None
        if bank_credit_candidates:
            if len(bank_credit_candidates) == 1:
                bank_credit = bank_credit_candidates[0]
            else:
                bank_credit = min(
                    bank_credit_candidates,
                    key=lambda r: abs(to_float(r["amount"]) - batch_net)
                )

        if bank_credit is not None:
            bank_amount = to_float(bank_credit["amount"])
            diff = round(bank_amount - batch_net, 2)
            abs_diff = abs(diff)

            if abs_diff <= BATCH_TOLERANCE:
                status = "batch_credited"
                confidence = "matched"
                detail = None
            elif abs_diff < 1.00:
                status = "batch_credited"
                confidence = "needs_review"
                detail = f"Amount diff {diff:.2f} within ambiguous range"
            else:
                status = "batch_neft_failed"
                confidence = "hard_exception"
                detail = f"Bank amount {bank_amount:.2f} differs from batch net {batch_net:.2f} by {abs_diff:.2f}"
        else:
            bank_amount = None
            diff = None
            if batch_net > 0:
                status = "batch_neft_failed"
                confidence = "hard_exception"
                detail = f"UTR {bank_utr} not in bank, batch net {batch_net:.2f} > 0"
            else:
                # CLAIM 4 FIX: Negative-net batches are correctly resolved, not exceptions
                status = "batch_no_credit"
                confidence = "matched"
                detail = f"UTR {bank_utr} not in bank, batch net {batch_net:.2f} <= 0 (credit correctly skipped)"

        batch_results[sid] = {
            "settlement_id": sid,
            "result_type": "settlement",
            "bank_utr": bank_utr,
            "batch_net": batch_net,
            "row_count": row_count,
            "order_ids": order_ids,
            "ghost_order_ids": ghost_order_ids,
            "bank_amount": bank_amount,
            "diff": diff,
            "status": status,
            "confidence": confidence,
            "detail": detail,
            "soft_flags": [],
        }

    return batch_results

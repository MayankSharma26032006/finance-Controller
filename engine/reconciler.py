#!/usr/bin/env python3
"""Phase 4: Final Exception Categorization and Consolidated Report.
Reads match_log.json + explanations.json (read-only), writes reconciliation_report.json.
"""

import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MATCH_LOG = PROJECT_ROOT / "engine" / "output" / "match_log.json"
EXPLANATIONS = PROJECT_ROOT / "agent" / "output" / "explanations.json"
OUTPUT = PROJECT_ROOT / "engine" / "output" / "reconciliation_report.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def map_status(confidence, exception_code):
    """Section 2: exception_code-aware mapping."""
    if confidence == "matched":
        if exception_code == "NO_CREDIT_EXPECTED":
            return "Reconciled (no credit due)"
        return "Reconciled"
    if confidence == "matched_with_note":
        return "Reconciled (with note)"
    if confidence == "needs_review":
        return "Needs Human Review"
    if confidence == "hard_exception":
        return "Unresolved"
    return "Unknown"


def get_exception_code(entry):
    """Derive exception_code from match_log entry."""
    ec = entry.get("exception_code")
    if ec:
        return ec
    if entry.get("ghost_order_ids"):
        return "GHOST_TRANSACTION"
    status = entry.get("status", "")
    if status == "batch_neft_failed":
        return "NEFT_FAILED"
    if status == "batch_no_credit":
        return "NO_CREDIT_EXPECTED"
    return None


def build_key_figures(entry, case_type, exception_code):
    """Build key_figures dict based on case type."""
    if case_type == "order":
        kf = {
            "settlement_ids": entry.get("settlement_ids", []),
            "bank_utr": entry.get("bank_utr"),
            "order_residual": entry.get("order_residual"),
            "refund_type": entry.get("refund_type"),
        }
        if exception_code == "DUPLICATE_ORDER":
            kf["conflicting_amounts"] = [
                float(r["gross_amount"])
                for r in entry.get("conflicting_ledger_rows", [])
            ]
            if kf["settlement_ids"]:
                kf["settlement_gross_matched"] = kf["conflicting_amounts"][0]
        if exception_code == "UNRECORDED_REFUND":
            detail = entry.get("detail", "")
            kf["ledger_refund_status"] = (
                detail.split("claims ")[1].split(" refund")[0]
                if "claims " in detail else None
            )
            kf["settlement_refund_deduction"] = 0.0
        if exception_code == "UNMATCHED_ORDER":
            kf["detail"] = entry.get("detail")
        return kf
    # settlement
    kf = {
        "bank_utr": entry.get("bank_utr"),
        "batch_net": entry.get("batch_net"),
        "bank_amount": entry.get("bank_amount"),
        "row_count": entry.get("row_count"),
        "diff": entry.get("diff"),
    }
    if exception_code == "GHOST_TRANSACTION":
        kf["ghost_order_ids"] = entry.get("ghost_order_ids", [])
    return kf


STATUS_ORDER = {
    "Reconciled": 0,
    "Reconciled (with note)": 1,
    "Reconciled (no credit due)": 2,
    "Needs Human Review": 3,
    "Unresolved": 4,
}


def main():
    # Step 1: Load inputs
    print("Loading inputs...")
    with open(MATCH_LOG, encoding="utf-8") as f:
        match_log = json.load(f)
    with open(EXPLANATIONS, encoding="utf-8") as f:
        explanations = json.load(f)

    ml_hash = sha256(MATCH_LOG)
    ex_hash = sha256(EXPLANATIONS)
    print("  match_log.json: %d entries, hash=%s..." % (len(match_log), ml_hash[:16]))
    print("  explanations.json: %d entries, hash=%s..." % (len(explanations), ex_hash[:16]))

    # Step 2: Build explanation lookup
    expl_by_id = {e["case_id"]: e for e in explanations}

    # Step 3: Process each match_log entry
    orders = []
    settlements = []
    status_counts = {"order": {}, "settlement": {}}

    for entry in match_log:
        case_type = entry.get("result_type", "unknown")
        case_id = entry.get("order_id") or entry.get("settlement_id")
        confidence = entry.get("confidence", "")
        exc = get_exception_code(entry)
        status = map_status(confidence, exc)

        expl_entry = expl_by_id.get(case_id)

        out = {
            "case_id": case_id,
            "case_type": case_type,
            "simplified_status": status,
            "exception_code": exc,
            "explanation": expl_entry["explanation"] if expl_entry else None,
            "suggested_action": expl_entry["suggested_action"] if expl_entry else None,
            "confidence_note": expl_entry.get("confidence_note") if expl_entry else None,
            "key_figures": build_key_figures(entry, case_type, exc),
            "soft_flags": entry.get("soft_flags", []),
        }

        if case_type == "order":
            orders.append(out)
        else:
            settlements.append(out)

        sc = status_counts[case_type]
        sc[status] = sc.get(status, 0) + 1

    # Completeness assertion
    print("")
    print("Completeness check: %d orders, %d settlements" % (len(orders), len(settlements)))
    if len(orders) != 500:
        print("ERROR: Expected 500 orders, got %d. Aborting." % len(orders))
        sys.exit(1)
    if len(settlements) != 91:
        print("ERROR: Expected 91 settlements, got %d. Aborting." % len(settlements))
        sys.exit(1)
    print("  PASSED: 500 orders + 91 settlements = 591 entries")

    # Step 4: Compute summary statistics
    oc = status_counts["order"]
    sc = status_counts["settlement"]

    o_rec = oc.get("Reconciled", 0)
    o_wn = oc.get("Reconciled (with note)", 0)
    o_rev = oc.get("Needs Human Review", 0)
    o_unr = oc.get("Unresolved", 0)
    o_tot = o_rec + o_wn + o_rev + o_unr

    s_rec = sc.get("Reconciled", 0)
    s_nc = sc.get("Reconciled (no credit due)", 0)
    s_rev = sc.get("Needs Human Review", 0)
    s_unr = sc.get("Unresolved", 0)
    s_tot = s_rec + s_nc + s_rev + s_unr

    o_rate = round((o_rec + o_wn) / o_tot * 100, 1) if o_tot else 0
    s_rate = round((s_rec + s_nc) / s_tot * 100, 1) if s_tot else 0

    rec_total = o_rec + o_wn + s_rec + s_nc
    rev_total = o_rev + s_rev
    unr_total = o_unr + s_unr
    grand = o_tot + s_tot
    overall = round(rec_total / grand * 100, 1) if grand else 0

    print("")
    print("Summary:")
    print("  Orders:     %d reconciled + %d with_note + %d review + %d unresolved = %d" % (o_rec, o_wn, o_rev, o_unr, o_tot))
    print("  Settlements: %d reconciled + %d no_credit + %d review + %d unresolved = %d" % (s_rec, s_nc, s_rev, s_unr, s_tot))
    print("  Overall:    %d/%d = %.1f%%" % (rec_total, grand, overall))

    # Step 5: Sort and write output
    orders.sort(key=lambda x: (STATUS_ORDER.get(x["simplified_status"], 9), x["case_id"]))
    settlements.sort(key=lambda x: (STATUS_ORDER.get(x["simplified_status"], 9), x["case_id"]))

    report = {
        "report_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_frozen_at": "2025-08-25T17:40:46+05:30",
            "match_log_hash": ml_hash,
            "explanations_hash": ex_hash,
            "total_orders": 500,
            "total_settlements": 91,
            "phases_consumed": [
                "Phase 2 (matcher_exact)",
                "Phase 3 (explainer)"
            ],
        },
        "summary": {
            "orders": {
                "total": o_tot, "reconciled": o_rec,
                "reconciled_with_note": o_wn,
                "needs_human_review": o_rev, "unresolved": o_unr,
                "match_rate_pct": o_rate,
            },
            "settlements": {
                "total": s_tot, "reconciled": s_rec,
                "reconciled_no_credit_due": s_nc,
                "needs_human_review": s_rev, "unresolved": s_unr,
                "match_rate_pct": s_rate,
            },
            "overall": {
                "total_cases": grand, "reconciled_total": rec_total,
                "needs_human_review_total": rev_total,
                "unresolved_total": unr_total,
                "match_rate_pct": overall,
            },
        },
        "orders": orders,
        "settlements": settlements,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("")
    print("Output written to: %s" % OUTPUT)
    print("File size: %d bytes" % OUTPUT.stat().st_size)

    # Verify input files unchanged
    ml_hash2 = sha256(MATCH_LOG)
    ex_hash2 = sha256(EXPLANATIONS)
    if ml_hash2 != ml_hash or ex_hash2 != ex_hash:
        print("WARNING: Input files changed during execution!")
    else:
        print("Input files verified unchanged.")


if __name__ == "__main__":
    main()

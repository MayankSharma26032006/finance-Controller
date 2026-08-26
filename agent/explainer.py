#!/usr/bin/env python3
"""Phase 3: Agent Reasoning / Explanation Layer."""

import csv, json, os, re, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MATCH_LOG = PROJECT_ROOT / "engine" / "output" / "match_log.json"
ORDER_LEDGER = PROJECT_ROOT / "data" / "raw" / "order_ledger.csv"
SETTLEMENT_REPORT = PROJECT_ROOT / "data" / "raw" / "settlement_report.csv"
OUTPUT_PATH = PROJECT_ROOT / "agent" / "output" / "explanations.json"
FX_RATE = 83.0

DOMAIN_FACTS = """DOMAIN FACTS - Razorpay Settlement Mechanics:
1. FEE STRUCTURE: Domestic 2% + 18% GST; International 3% + 18% GST; net = gross - fee - (fee x 0.18)
2. SETTLEMENT: T+2 working days, excludes weekends/holidays, cut-off ~5PM IST
3. BATCHING: Not 1:1; one settlement_id = one batch = one NEFT credit (one UTR)
4. REFUNDS: Deducted from future batches; MDR non-refundable; partial refund can land in different batch
5. CURRENCY: Everything settled in INR; fixed rate 1 USD = 83.00 INR
6. IDENTIFIERS: order_id=ord_*, settlement_id=set_*, bank_utr=16-digit NEFT ref"""

SYSTEM_INSTRUCTIONS = """You are a fintech reconciliation analyst. Your job is to EXPLAIN
a specific reconciliation case in plain English. You must:
- Base your explanation ONLY on the data provided below
- Reference specific amounts, dates, and IDs from the case data
- Do NOT invent facts not present in the data or the domain reference
- Do NOT re-classify the case - the status is already determined
- Keep your explanation to 2-4 sentences
- If asked about a needs_review case, express genuine uncertainty
- Output ONLY the final answer. Do NOT show thinking, reasoning, or analysis steps."""
def load_match_log():
    with open(MATCH_LOG, "r") as f:
        log = json.load(f)
    return [e for e in log if e.get("confidence", "") in ("matched_with_note", "needs_review", "hard_exception")]

def load_csv(path):
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))

def get_ledger_rows(oid, ledger):
    return [r for r in ledger if r["order_id"] == oid]

def get_settlement_rows(oid, settlements):
    return [r for r in settlements if r["order_id"] == oid]

def get_exception_code(case):
    exc = case.get("exception_code", "")
    if exc: return exc
    if case.get("ghost_order_ids"): return "GHOST_TRANSACTION"
    s = case.get("status", "")
    if s == "batch_neft_failed": return "NEFT_FAILED"
    if s == "batch_no_credit": return "NO_CREDIT_EXPECTED"
    return ""

def build_refund_split_data(case, ledger, settlements):
    oid = case["order_id"]; lr = get_ledger_rows(oid, ledger); sr = get_settlement_rows(oid, settlements)
    L = ["Order: " + oid]
    if lr:
        r = lr[0]
        L.append("- Ledger: gross=" + r["gross_amount"] + " " + r["currency"] + ", payment_method=" + r["payment_method"] + ", created=" + r["created_at"])
    for i, s in enumerate(sr, 1):
        L.append("- Settlement row " + str(i) + " (" + s["settlement_id"] + "): gross=" + s["gross_amount"] + ", fee=" + s["fee"] + ", gst=" + s["gst_on_fee"] + ", refund=" + s["refund_deduction"] + ", net=" + s["net_amount"])
    L.append("- Order residual (net): " + str(case.get("order_residual")))
    L.append("- Bank UTR: " + str(case.get("bank_utr", "N/A")))
    L.append("- Exception: REFUND_SPLIT (partial refund in different batch)")
    return chr(10).join(L)
def build_currency_mismatch_data(case, ledger, settlements):
    oid = case["order_id"]; lr = get_ledger_rows(oid, ledger); sr = get_settlement_rows(oid, settlements)
    L = ["Order: " + oid]
    if lr:
        r = lr[0]
        L.append("- Ledger: gross=" + r["gross_amount"] + " " + r["currency"] + ", payment_method=" + r["payment_method"])
        if r["currency"] == "USD":
            usd = float(r["gross_amount"]); inr = round(usd * FX_RATE, 2)
            L.append("- Conversion: " + str(usd) + " USD x " + str(FX_RATE) + " = " + str(inr) + " INR")
    for s in sr:
        L.append("- Settlement (" + s["settlement_id"] + "): gross=" + s["gross_amount"] + " INR, fee=" + s["fee"] + ", gst=" + s["gst_on_fee"] + ", net=" + s["net_amount"])
    L.append("- Bank UTR: " + str(case.get("bank_utr", "N/A")))
    L.append("- Exception: CURRENCY_MISMATCH (ledger stores USD, settlement/bank store INR)")
    return chr(10).join(L)

def build_duplicate_order_data(case, ledger):
    oid = case["order_id"]; rows = case.get("conflicting_ledger_rows", [])
    L = ["Order: " + oid]
    for i, row in enumerate(rows, 1):
        L.append("- Row " + str(i) + ": gross=" + row["gross_amount"] + " INR, quantity=" + row["quantity"] + ", customer=" + row["customer_id"] + ", created=" + row["created_at"])
    L.append("- Same customer, same SKU, same timestamp, same quantity - different amounts")
    L.append("- No settlement rows found for this order_id")
    L.append("- Exception: DUPLICATE_ORDER (requires human judgment)")
    return chr(10).join(L)

def build_unmatched_order_data(case, ledger):
    oid = case["order_id"]; lr = get_ledger_rows(oid, ledger)
    L = ["Order: " + oid]
    if lr:
        r = lr[0]
        L.append("- Ledger: gross=" + r["gross_amount"] + " " + r["currency"] + ", payment_status=" + r["payment_status"] + ", created_at=" + r["created_at"])
    else:
        L.append("- Ledger: NO rows found")
    L.append("- Settlement: NO rows found for this order_id")
    L.append("- Bank: NO credits linked to this order")
    L.append("- Detail: " + str(case.get("detail", "")))
    return chr(10).join(L)

def build_ghost_transaction_data(case, settlements):
    sid = case["settlement_id"]; ghosts = case.get("ghost_order_ids", []); all_ids = case.get("order_ids", [])
    L = ["Settlement batch: " + sid]
    L.append("- Bank UTR: " + str(case.get("bank_utr", "N/A")))
    L.append("- Batch net: " + str(case.get("batch_net")) + " INR")
    L.append("- Bank credit: " + str(case.get("bank_amount")) + " INR")
    L.append("- " + str(len(all_ids)) + " orders in batch:")
    for oid in all_ids:
        tag = "GHOST - not in order_ledger.csv" if oid in ghosts else "legitimate"
        L.append("  - " + oid + " (" + tag + ")")
    L.append("- Exception: GHOST_TRANSACTION (settlement references order not in ledger)")
    return chr(10).join(L)

def build_neft_failed_data(case):
    sid = case["settlement_id"]
    L = ["Settlement batch: " + sid]
    L.append("- Bank UTR: " + str(case.get("bank_utr", "N/A")))
    L.append("- Batch net: " + str(case.get("batch_net")) + " INR (positive - credit expected)")
    L.append("- " + str(case.get("row_count", "?")) + " orders in batch")
    L.append("- Bank statement: UTR " + str(case.get("bank_utr")) + " NOT FOUND")
    L.append("- Exception: NEFT_FAILED (positive net but bank never credited)")
    return chr(10).join(L)

def build_no_credit_expected_data(case):
    sid = case["settlement_id"]
    L = ["Settlement batch: " + sid]
    L.append("- Bank UTR: " + str(case.get("bank_utr", "N/A")))
    L.append("- Batch net: " + str(case.get("batch_net")) + " INR (NEGATIVE - refund deductions exceeded gross)")
    L.append("- " + str(case.get("row_count", "?")) + " orders in batch")
    L.append("- Bank statement: UTR " + str(case.get("bank_utr")) + " NOT FOUND")
    L.append("- Status: NO_CREDIT_EXPECTED (negative net correctly produces no bank credit)")
    return chr(10).join(L)

def build_case_data(case, ledger, settlements):
    exc = case.get("exception_code", ""); rt = case.get("result_type", ""); st = case.get("status", case.get("confidence", ""))
    if exc == "REFUND_SPLIT": return build_refund_split_data(case, ledger, settlements)
    if exc == "CURRENCY_MISMATCH": return build_currency_mismatch_data(case, ledger, settlements)
    if exc == "DUPLICATE_ORDER": return build_duplicate_order_data(case, ledger)
    if exc == "UNMATCHED_ORDER": return build_unmatched_order_data(case, ledger)
    if rt == "settlement" and case.get("ghost_order_ids"): return build_ghost_transaction_data(case, settlements)
    if st == "batch_neft_failed": return build_neft_failed_data(case)
    if st == "batch_no_credit": return build_no_credit_expected_data(case)
    return json.dumps(case, indent=2)

def build_prompt(case, case_data):
    return """DOMAIN REFERENCE:
{domain}

TASK: Explain the following reconciliation case in 2-4 sentences. Reference specific amounts, dates, and IDs from the case data. Do NOT invent facts. Do NOT re-classify the case.

CASE DATA:
{data}

Output ONLY the explanation. No preamble, no analysis, no reasoning steps.""".format(domain=DOMAIN_FACTS, data=case_data)

def extract_figures(text):
    normalized = text
    for old, new in [(chr(0x202f), " "), (chr(0x2009), " "), (chr(0x00a0), " "), (chr(0x20b9), "Rs")]:
        normalized = normalized.replace(old, new)
    patterns = [r"Rs\s*([\d,]+\.?\d*)", r"\$\s*([\d,]+\.?\d*)",
                r"([\d,]+\.?\d*)\s*INR", r"([\d,]+\.?\d*)\s*USD",
                r"INR\s*([\d,]+\.?\d*)", r"USD\s*([\d,]+\.?\d*)"]
    figs = []
    for p in patterns:
        for m in re.findall(p, normalized):
            cleaned = m.replace(",", "").strip()
            if cleaned: figs.append(cleaned)
            if cleaned.startswith('-') and cleaned[1:].replace('.','',1).isdigit():
                figs.append(cleaned[1:])
    for m in re.findall(r"2025-\d{2}-\d{2}", normalized):
        figs.append(m)
    figs = [f for f in figs if len(f) > 1 or "." in f]
    seen = set(); result = []
    for f in figs:
        if f not in seen: seen.add(f); result.append(f)
    return result
def collect_source_figures(case, ledger, settlements):
    figs = set()
    for k in ("order_residual", "expected_residual", "batch_net", "bank_amount", "diff"):
        v = case.get(k)
        if v is not None: figs.add(str(v))
    oid = case.get("order_id")
    if oid:
        for r in get_ledger_rows(oid, ledger):
            for f in ("gross_amount", "refund_amount", "quantity"):
                figs.add(r.get(f, ""))
            figs.add(r.get("created_at", "")[:10])
        for s in get_settlement_rows(oid, settlements):
            for f in ("gross_amount", "fee", "gst_on_fee", "refund_deduction", "net_amount"):
                figs.add(s.get(f, ""))
            figs.add(s.get("settlement_date", ""))
    sid = case.get("settlement_id")
    if sid:
        for s in settlements:
            if s["settlement_id"] == sid:
                for f in ("gross_amount", "fee", "gst_on_fee", "refund_deduction", "net_amount"):
                    figs.add(s.get(f, ""))
                figs.add(s.get("settlement_date", ""))
                figs.add(s.get("bank_utr", ""))
    figs.discard("")
    # Add absolute values so negative numbers match unsigned mentions
    for f in list(figs):
        if f.startswith('-') and f[1:].replace('.','',1).isdigit():
            figs.add(f[1:])
    domain_figures = {"2", "3", "18", "83.00", "83", "0.18", "0.02", "0.03", "2.36", "3.54"}
    figs.update(domain_figures)
    return figs

def run_hallucination_check(explanation, case, ledger, settlements):
    stated = extract_figures(explanation)
    source = collect_source_figures(case, ledger, settlements)
    mismatches = [f for f in stated if f not in source]
    return {"stated_figures": stated, "verified": len(mismatches) == 0, "mismatches": mismatches}

def call_openai(client, prompt, case_id):
    msgs = [{"role": "system", "content": SYSTEM_INSTRUCTIONS}, {"role": "user", "content": prompt}]
    max429 = 3; delay = 2
    for attempt in range(max429 + 1):
        try:
            resp = client.chat.completions.create(model="openai/gpt-oss-120b", messages=msgs, temperature=0, max_tokens=800)
            expl = resp.choices[0].message.content.strip()
            expl = re.sub(r"(?s)<think>.*?</think>", "", expl).strip()
            expl = re.sub(r"(?s)^.*?Here.s a thinking process:.*?(?:Draft(?: Explanation)?|Output|Final Answer|Revised draft).*?:\s*", "", expl).strip()
            expl = expl.replace("(Mental Refinement - aiming for 2-4 sentences):", "")
            expl = expl.replace("(Mental Refinement):", "")
            expl = expl.lstrip("*: ").strip()
            expl = expl.replace(chr(0x202f), " ")
            usage = {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens, "total_tokens": resp.usage.total_tokens}
            return expl, usage, None
        except Exception as e:
            es = str(e)
            if "401" in es or "auth" in es.lower():
                print("  FATAL: Auth error. Check GROQ_API_KEY.", file=sys.stderr); sys.exit(1)
            if "429" in es or "rate" in es.lower():
                if attempt < max429:
                    print("  Rate limited, retry " + str(attempt+1) + "/" + str(max429) + " in " + str(delay) + "s...")
                    time.sleep(delay); delay *= 2; continue
                return None, None, "Rate limit exceeded after retries"
            if any(c in es for c in ["500", "502", "503", "timeout", "Timeout"]):
                if attempt == 0: time.sleep(5); continue
                return None, None, "Server error: " + es[:200]
            if attempt == 0: time.sleep(5); continue
            return None, None, "Error: " + es[:200]
    return None, None, "All retries exhausted"

def get_suggested_action(exc, case):
    if exc == "REFUND_SPLIT": return "No action required - expected behavior for partial refunds near batch boundaries."
    if exc == "CURRENCY_MISMATCH": return "No action required - amounts match after applying FX rate of 83.00 INR/USD."
    if exc == "DUPLICATE_ORDER": return "Escalate to payment ops to verify correct gross_amount against source system."
    if exc == "GHOST_TRANSACTION": return "Investigate ghost order_id in settlement report. Could be data entry error or order not yet in ledger."
    if exc == "NEFT_FAILED": return "Escalate to bank ops to trace NEFT credit for UTR " + str(case.get("bank_utr", "N/A")) + "."
    if exc == "NO_CREDIT_EXPECTED": return "No action required - negative net correctly produces no bank credit."
    if exc == "UNMATCHED_ORDER":
        d = case.get("detail", "")
        if "failed" in d.lower(): return "No action required - payment failed. Order should be voided or retried."
        if "authorized" in d.lower(): return "Check if authorization will be captured in future cycle. If stale (>7 days), consider voiding."
        if "captured" in d.lower(): return "Escalate to payment ops - data integrity issue. Captured order missing from settlement."
        return "Review this unmatched order manually."
    return "Review this case manually."
def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError: pass

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or api_key == "your-key-here":
        print("ERROR: GROQ_API_KEY not set.")
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    print("Loading match_log.json...")
    cases = load_match_log()
    print("Found " + str(len(cases)) + " cases (expected: 17)")
    if len(cases) != 17:
        print("ERROR: Expected 17 cases, got " + str(len(cases)) + ". Aborting.")
        sys.exit(1)

    print("Loading CSV data...")
    ledger = load_csv(ORDER_LEDGER)
    settlements = load_csv(SETTLEMENT_REPORT)
    print("  order_ledger.csv: " + str(len(ledger)) + " rows")
    print("  settlement_report.csv: " + str(len(settlements)) + " rows")

    explanations = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    failed_cases = []

    for i, case in enumerate(cases, 1):
        case_id = case.get("order_id") or case.get("settlement_id")
        case_type = case.get("result_type", "unknown")
        status = case.get("confidence", case.get("status", ""))
        exc = get_exception_code(case)

        print("")
        print("[" + str(i).rjust(2) + "/17] " + case_type + ": " + case_id + " (" + (exc or status) + ")")
        case_data = build_case_data(case, ledger, settlements)
        prompt = build_prompt(case, case_data)
        explanation, usage, error = call_openai(client, prompt, case_id)

        if error:
            print("  FAILED: " + error)
            failed_cases.append({"case_id": case_id, "error": error})
            explanation = "ERROR: API call failed - " + error
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if usage:
            for k in total_usage: total_usage[k] += usage.get(k, 0)

        hc = run_hallucination_check(explanation, case, ledger, settlements)

        confidence_note = None
        if status == "needs_review" and exc == "DUPLICATE_ORDER":
            confidence_note = "Cannot determine which amount is correct. Requires manual verification."

        suggested_action = get_suggested_action(exc, case)

        entry = {
            "case_id": case_id, "case_type": case_type, "status": status,
            "exception_code": exc, "explanation": explanation,
            "suggested_action": suggested_action, "confidence_note": confidence_note,
            "hallucination_check": hc,
        }
        explanations.append(entry)

        vs = "VERIFIED" if hc["verified"] else "FLAGGED(" + str(len(hc["mismatches"])) + ")"
        print("  HC: " + vs)
        print("  Expl: " + explanation[:120].encode("ascii", "replace").decode() + "...")

    explanations.sort(key=lambda x: (x["case_type"], x["case_id"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(explanations, f, indent=2)

    print("")
    print("=" * 60)
    print("PHASE 3 COMPLETE")
    print("=" * 60)
    print("Cases processed: " + str(len(explanations)))
    print("Total tokens: " + str(total_usage["total_tokens"]))
    print("  Prompt: " + str(total_usage["prompt_tokens"]))
    print("  Completion: " + str(total_usage["completion_tokens"]))
    print("Failed: " + str(len(failed_cases)))
    for fc in failed_cases: print("  - " + fc["case_id"] + ": " + fc["error"])
    print("")
    print("Output: " + str(OUTPUT_PATH))

    print("")
    print("=" * 60)
    print("FULL EXPLANATIONS")
    print("=" * 60)
    for e in explanations:
        print("")
        print("--- " + e["case_type"] + ": " + e["case_id"] + " ---")
        print("Status: " + e["status"])
        print("Exception: " + e["exception_code"])
        print("Explanation: " + e["explanation"].encode("ascii", "replace").decode())
        print("Action: " + e["suggested_action"])
        if e["confidence_note"]: print("Note: " + e["confidence_note"])
        hc2 = e["hallucination_check"]
        print("HC: verified=" + str(hc2["verified"]) + ", mismatches=" + str(hc2["mismatches"]))

if __name__ == "__main__":
    main()

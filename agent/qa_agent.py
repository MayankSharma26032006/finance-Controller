#!/usr/bin/env python3
"""
Phase 7: Settlement Q&A Agent.

Reads reconciliation_report.json, match_log.json, explanations.json,
and metrics_report.json (all read-only). Writes nothing to disk.

Public API: answer_question(question: str) -> dict
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RR_PATH = ROOT / "engine" / "output" / "reconciliation_report.json"
ML_PATH = ROOT / "engine" / "output" / "match_log.json"
EXP_PATH = ROOT / "agent" / "output" / "explanations.json"
MR_PATH = ROOT / "engine" / "output" / "metrics_report.json"

DOMAIN_FACTS = (
    "DOMAIN FACTS - Razorpay Settlement Mechanics:\n"
    "- Platform fee: 2% for domestic instruments, 3% for international cards\n"
    "- GST on fee: 18% of the platform fee amount\n"
    "- Net formula: net = gross - fee - (fee x 0.18)\n"
    "- Settlement batching: multiple orders batched into one settlement_id per cycle\n"
    "- Settlement cycle: T+1 to T+3 business days after capture\n"
    "- Refund deductions: partial refunds appear as negative net_amount rows\n"
    "- Full refund: refund_deduction magnitude equals original gross_amount; "
    "residual = -(fee + gst)\n"
    "- FX rate for USD orders: 1 USD = 83.00 INR (fixed in synthetic data)\n"
    "- Bank credit: each settlement_id maps to one NEFT UTR on bank statement\n"
    "- Negative net settlement: correctly produces no bank credit (expected)"
)

SYSTEM_PROMPT = (
    "You are a financial reconciliation assistant. Answer the user question "
    "using ONLY the data provided below. Do not invent facts or make predictions. "
    "If the data does not contain enough information to answer, say so clearly."
)

# ── Singleton state ──────────────────────────────────────────────────
_state = None


def _load_data():
    """Load all 4 data sources once, hold in memory."""
    global _state
    if _state is not None:
        return _state

    with open(RR_PATH, "r", encoding="utf-8") as f:
        rr = json.load(f)
    with open(ML_PATH, "r", encoding="utf-8") as f:
        ml = json.load(f)
    with open(EXP_PATH, "r", encoding="utf-8") as f:
        exp_raw = json.load(f)
    with open(MR_PATH, "r", encoding="utf-8") as f:
        mr = json.load(f)

    # Index reconciliation_report by case_id
    rr_by_id = {}
    for entry in rr.get("orders", []) + rr.get("settlements", []):
        rr_by_id[entry["case_id"]] = entry

    # Index match_log by case_id (order_id or settlement_id)
    ml_by_id = {}
    for entry in ml:
        cid = entry.get("order_id") or entry.get("settlement_id")
        if cid:
            ml_by_id[cid] = entry

    # Index explanations by case_id
    exp_by_id = {}
    for entry in exp_raw:
        exp_by_id[entry["case_id"]] = entry

    _state = {
        "rr": rr,
        "ml": ml,
        "rr_by_id": rr_by_id,
        "ml_by_id": ml_by_id,
        "exp_by_id": exp_by_id,
        "mr": mr,
    }
    return _state


# ── Classification ───────────────────────────────────────────────────

def classify_question(question):
    """Classify question into single_case / aggregate / out_of_scope."""
    q = question.strip()

    # Check for case_id patterns
    order_match = re.search(r"ord_[A-Za-z0-9]+", q)
    settle_match = re.search(r"set_[A-Za-z0-9]+", q)
    if order_match or settle_match:
        return "single_case", order_match, settle_match

    # Check for future/out-of-scope keywords
    future_kw = ["next month", "next week", "tomorrow", "will improve",
                  "will the", "predict", "forecast", "future"]
    if any(kw in q.lower() for kw in future_kw):
        return "out_of_scope", None, None

    external_kw = ["razorpay fee", "actual fee", "real razorpay",
                    "production", "live account", "real bank"]
    if any(kw in q.lower() for kw in external_kw):
        return "out_of_scope", None, None

    # Default to aggregate for any other question
    return "aggregate", None, None


# ── Fallback responses ───────────────────────────────────────────────

def _fallback(answer, reason):
    """Return a structured out-of-scope fallback."""
    return {
        "answer": answer,
        "source_case_ids": [],
        "verified": True,
        "category": "out_of_scope",
        "fallback_reason": reason,
    }


def _handle_out_of_scope(question, order_match, settle_match, rr_by_id):
    """Build fallback for out-of-scope or failed lookups."""
    q = question.strip().lower()

    # If a case_id was found but doesn't exist
    if order_match:
        cid = order_match.group(0)
        if cid not in rr_by_id:
            return _fallback(
                f"Order {cid} does not exist in the reconciliation dataset "
                f"(500 orders, 91 settlements). Try asking about an existing "
                f"order or settlement ID.",
                "case_id_not_found",
            )
    if settle_match:
        cid = settle_match.group(0)
        if cid not in rr_by_id:
            return _fallback(
                f"Settlement {cid} does not exist in the reconciliation dataset "
                f"(500 orders, 91 settlements). Try asking about an existing "
                f"order or settlement ID.",
                "case_id_not_found",
            )

    # Future prediction
    future_kw = ["next month", "next week", "tomorrow", "will improve",
                  "will the", "predict", "forecast", "future"]
    if any(kw in q for kw in future_kw):
        return _fallback(
            "I can only answer questions about the current dataset. "
            "I cannot predict future match rates or settlement behavior.",
            "future_prediction",
        )

    # External data
    external_kw = ["razorpay fee", "actual fee", "real razorpay",
                    "production", "live account", "real bank"]
    if any(kw in q for kw in external_kw):
        return _fallback(
            "I can only answer using the 3-source reconciliation dataset. "
            "I cannot look up external Razorpay documentation.",
            "external_data",
        )

    # Ambiguous
    return _fallback(
        "Your question could refer to multiple cases. Please specify an "
        "order_id (ord_...) or settlement_id (set_...), or ask for "
        "summary statistics.",
        "ambiguous",
    )


# ── Grounding ────────────────────────────────────────────────────────

def _build_single_case_prompt(case_id, question, rr_by_id, ml_by_id, exp_by_id):
    """Build grounded prompt for a single-case question."""
    case = rr_by_id.get(case_id)
    ml_entry = ml_by_id.get(case_id)
    exp_entry = exp_by_id.get(case_id)

    parts = [SYSTEM_PROMPT, "", "DOMAIN FACTS:", DOMAIN_FACTS, ""]

    if case:
        parts.append("CASE DATA (from reconciliation report):")
        parts.append(json.dumps(case, indent=2, default=str))
        parts.append("")

    if ml_entry:
        parts.append("ENRICHMENT DATA (from match log):")
        parts.append(json.dumps(ml_entry, indent=2, default=str))
        parts.append("")

    if exp_entry:
        parts.append("EXISTING EXPLANATION (use as primary context, "
                      "supplement with other fields if needed):")
        parts.append(exp_entry.get("explanation", ""))
        parts.append("")
        if exp_entry.get("suggested_action"):
            parts.append("SUGGESTED ACTION: " + exp_entry["suggested_action"])
            parts.append("")
        if exp_entry.get("confidence_note"):
            parts.append("CONFIDENCE NOTE: " + exp_entry["confidence_note"])
            parts.append("")

    parts.append("USER QUESTION: " + question)
    parts.append("")
    parts.append("Note: Prioritize answering what was actually asked. "
                 "The existing explanation is your primary reference, but "
                 "if the user asks about a specific field not covered by "
                 "the explanation, use the CASE DATA and ENRICHMENT DATA "
                 "to answer.")
    return "\n".join(parts)


def _build_aggregate_prompt(question, rr, mr):
    """Build grounded prompt for an aggregate question."""
    summary = rr.get("summary", {})
    overall = mr.get("overall", {})
    order_level = mr.get("order_level", {})
    settle_level = mr.get("settlement_level", {})

    parts = [SYSTEM_PROMPT, "", "You are a financial reconciliation assistant. "
             "Answer the user question using ONLY the pre-computed statistics "
             "below. Do not count raw data yourself.", ""]

    parts.append("SUMMARY STATISTICS:")
    parts.append(json.dumps(summary, indent=2))
    parts.append("")

    parts.append("ACCURACY METRICS:")
    parts.append(json.dumps(overall, indent=2))
    parts.append("")

    if order_level:
        parts.append("ORDER-LEVEL BREAKDOWN:")
        parts.append(json.dumps(order_level, indent=2))
        parts.append("")

    if settle_level:
        parts.append("SETTLEMENT-LEVEL BREAKDOWN:")
        parts.append(json.dumps(settle_level, indent=2))
        parts.append("")

    parts.append("USER QUESTION: " + question)
    return "\n".join(parts)


# ── Hallucination check ──────────────────────────────────────────────

_CURRENCY_RE = re.compile(r"[\d,]+\.?\d*")
_DATE_RE = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}")

DOMAIN_FIGURES = {"2%", "3%", "18%", "83.00", "83", "0.18", "T+1", "T+2", "T+3"}


def _extract_figures(text):
    """Extract currency figures and dates from text."""
    figures = set()
    for m in _CURRENCY_RE.finditer(text):
        val = m.group(0).replace(",", "")
        if len(val) >= 2:  # skip single-digit contextual words
            figures.add(val)
    for m in _DATE_RE.finditer(text):
        figures.add(m.group(0))
    return figures


def _verify_figures(answer_text, case_data_str):
    """Check stated figures against source data + domain facts."""
    stated = _extract_figures(answer_text)
    source = _extract_figures(case_data_str)
    # Normalize: add variants for comparison
    source_normalized = set()
    for s in source:
        source_normalized.add(s)
        source_normalized.add(s.replace(".00", ""))
        # Add percentage equivalent: 1.0 -> 100, 0.96 -> 96
        try:
            val = float(s)
            if 0.0 <= val <= 1.0:
                source_normalized.add(str(int(val * 100)))
                source_normalized.add(f"{val * 100:.2f}")
        except ValueError:
            pass
    for df in DOMAIN_FIGURES:
        source_normalized.add(df)
        source_normalized.add(df.replace("%", ""))
    # Check each stated figure
    mismatches = []
    for fig in stated:
        found = False
        for src in source_normalized:
            if fig in src or src in fig:
                found = True
                break
        if not found:
            try:
                as_float = float(fig)
                formatted = f"{as_float:,.2f}"
                if formatted in case_data_str:
                    found = True
            except ValueError:
                pass
        if not found:
            mismatches.append(fig)
    return {"verified": len(mismatches) == 0, "mismatches": mismatches}


# ── API call ─────────────────────────────────────────────────────────

def _call_llm(prompt):
    """Call Groq API with retry/backoff. Returns (text, usage) or raises."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or api_key == "your-key-here":
        raise RuntimeError("GROQ_API_KEY not set in .env")

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}]

    max_429 = 3
    delay = 2

    for attempt in range(max_429 + 1):
        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=msgs,
                temperature=0,
                max_tokens=500,
            )
            text = resp.choices[0].message.content.strip()
            # Strip thinking tags if present
            text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
            text = text.replace("\u202f", " ")
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
            return text, usage

        except Exception as e:
            es = str(e)
            # 401 auth error: hard stop
            if "401" in es or "auth" in es.lower():
                raise RuntimeError(f"Auth error: {e}")
            # 429 rate limit: exponential backoff
            if "429" in es or "rate" in es.lower():
                if attempt < max_429:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            # 500/502/503/timeout: single retry
            if any(code in es for code in ["500", "502", "503", "timeout", "Timeout"]):
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise
            raise


# ── Public API ───────────────────────────────────────────────────────

def answer_question(question):
    """Answer a natural-language question about the reconciliation dataset.

    Args:
        question: Free-text question from the user.

    Returns:
        {
            "answer": str,
            "source_case_ids": list,
            "verified": bool,
            "category": str,
            "fallback_reason": str | None
        }
    """
    try:
        state = _load_data()
        rr = state["rr"]
        rr_by_id = state["rr_by_id"]
        ml_by_id = state["ml_by_id"]
        exp_by_id = state["exp_by_id"]
        mr = state["mr"]

        category, order_match, settle_match = classify_question(question)

        # ── Single-case ──
        if category == "single_case":
            case_id = (order_match.group(0) if order_match
                       else settle_match.group(0))
            if case_id not in rr_by_id:
                return _handle_out_of_scope(question, order_match,
                                            settle_match, rr_by_id)

            prompt = _build_single_case_prompt(
                case_id, question, rr_by_id, ml_by_id, exp_by_id
            )
            answer_text, usage = _call_llm(prompt)

            # Hallucination check
            case_data = json.dumps(rr_by_id[case_id], default=str)
            vr = _verify_figures(answer_text, case_data)

            return {
                "answer": answer_text,
                "source_case_ids": [case_id],
                "verified": vr["verified"],
                "category": "single_case",
                "fallback_reason": None,
            }

        # ── Aggregate ──
        if category == "aggregate":
            prompt = _build_aggregate_prompt(question, rr, mr)
            answer_text, usage = _call_llm(prompt)

            # Verify against summary stats
            verify_source = json.dumps(
                {"summary": rr.get("summary", {}), "metrics": mr},
                default=str,
            )
            vr = _verify_figures(answer_text, verify_source)

            return {
                "answer": answer_text,
                "source_case_ids": [],
                "verified": vr["verified"],
                "category": "aggregate",
                "fallback_reason": None,
            }

        # ── Out-of-scope ──
        return _handle_out_of_scope(question, order_match, settle_match,
                                    rr_by_id)

    except Exception as e:
        print(f"QA agent error: {e}", file=sys.stderr)
        return {
            "answer": ("I apologize -- the QA service is temporarily "
                       "unavailable. Please try again in a moment."),
            "source_case_ids": [],
            "verified": False,
            "category": "out_of_scope",
            "fallback_reason": "api_error",
        }


# ── CLI test harness ─────────────────────────────────────────────────

def main():
    """Run test questions against answer_question()."""
    test_questions = [
        # Single-case (order)
        "Why does order ord_EnDJiS9HvlxNgbb1 need human review?",
        # Single-case (settlement)
        "What happened with settlement set_7oqQnmBR7evr0ci5?",
        # Aggregate
        "How many orders needed human review?",
        # Aggregate
        "What is the overall accuracy of the reconciliation?",
        # Out-of-scope (fake case_id)
        "What is the status of order ord_FAKE12345?",
        # Out-of-scope (future prediction)
        "What will the match rate be next month?",
    ]

    for i, q in enumerate(test_questions, 1):
        print(f"\n{'='*60}")
        print(f"TEST {i}: {q}")
        print(f"{'='*60}")
        result = answer_question(q)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

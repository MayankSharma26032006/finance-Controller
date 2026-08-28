#!/usr/bin/env python3
"""
Phase 8: Streamlit Dashboard for the AI Finance Controller.

Reads reconciliation_report.json, metrics_report.json, and audit_trail.md
(all cached via @st.cache_data). Calls qa_agent.answer_question() only
on user question submission (the one live/dynamic part).

Run: streamlit run dashboard.py
"""

import json
import hashlib
import sys
import os
from pathlib import Path
from collections import Counter

import streamlit as st

# ── Paths ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
RR_PATH = ROOT / "engine" / "output" / "reconciliation_report.json"
MR_PATH = ROOT / "engine" / "output" / "metrics_report.json"
AUDIT_PATH = ROOT / "audit_trail.md"

# Ensure agent/ is importable for qa_agent
sys.path.insert(0, str(ROOT / "agent"))


# ── Data loading (cached) ───────────────────────────────────────────
@st.cache_data
def load_data():
    """Load all static data once, cached across Streamlit reruns."""
    with open(RR_PATH, "r", encoding="utf-8") as f:
        rr = json.load(f)
    with open(MR_PATH, "r", encoding="utf-8") as f:
        mr = json.load(f)
    with open(AUDIT_PATH, "r", encoding="utf-8") as f:
        audit_text = f.read()
    return rr, mr, audit_text


@st.cache_data
def compute_hashes():
    """Compute SHA-256 hashes of key files for the footer."""
    files = {
        "match_log": ROOT / "engine" / "output" / "match_log.json",
        "reconciliation_report": RR_PATH,
        "metrics_report": MR_PATH,
        "explanations": ROOT / "agent" / "output" / "explanations.json",
        "audit_trail": AUDIT_PATH,
    }
    hashes = {}
    for name, path in files.items():
        if path.exists():
            with open(path, "rb") as _f: h = hashlib.sha256(_f.read()).hexdigest()[:16]
            hashes[name] = h
    return hashes


# ── Page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Finance Controller",
    page_icon="🏦",
    layout="wide",
)

# ── Load data ────────────────────────────────────────────────────────
rr, mr, audit_text = load_data()
hashes = compute_hashes()

# Merge all cases into one list
all_cases = rr.get("orders", []) + rr.get("settlements", [])

# ── Header ───────────────────────────────────────────────────────────
st.title("AI Finance Controller")
st.caption(
    "591 cases | 100% classification accuracy | "
    "96.1% reconciled | 3.9% flagged for review"
)
st.divider()


# ══════════════════════════════════════════════════════════════════════
# SECTION A: Headline Metrics
# ══════════════════════════════════════════════════════════════════════
st.subheader("Reconciliation Summary")

summary = rr.get("summary", {})
overall = summary.get("overall", {})
accuracy = mr.get("overall", {}).get("accuracy", 0)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Accuracy",
        value=f"{accuracy:.0%}",
        help="Percentage of cases correctly classified against verified ground truth",
    )

with col2:
    st.metric(
        label="Matched",
        value=f"{overall.get('reconciled_total', 0)}/{overall.get('total_cases', 0)}",
        delta=f"{overall.get('match_rate_pct', 0)}%",
        delta_color="normal",
        help="Cases fully reconciled, including special-case handling",
    )

with col3:
    st.metric(
        label="Needs Review",
        value=overall.get("needs_human_review_total", 0),
        delta=f"{overall.get('needs_human_review_total', 0) / overall.get('total_cases', 1) * 100:.1f}%",
        delta_color="off",
        help="Cases requiring human judgment - genuine ambiguity",
    )

with col4:
    st.metric(
        label="Unresolved",
        value=overall.get("unresolved_total", 0),
        delta=f"{overall.get('unresolved_total', 0) / overall.get('total_cases', 1) * 100:.1f}%",
        delta_color="off",
        help="Cases where no match was possible - needs escalation",
    )

# Chart: Exception cases only (plain 562 dwarfs the others, so show meaningful variation)
status_counts = Counter(e["simplified_status"] for e in all_cases)

# Short labels for chart readability (fixed logical order, horizontal bars)
import pandas as pd
chart_labels = ["With Note", "No Credit Due", "Review", "Unresolved"]
chart_keys = [
    "Reconciled (with note)",
    "Reconciled (no credit due)",
    "Needs Human Review",
    "Unresolved",
]
chart_values = [status_counts.get(k, 0) for k in chart_keys]
chart_df = pd.DataFrame({"Category": chart_labels, "Count": chart_values})
chart_df["Category"] = pd.Categorical(
    chart_df["Category"], categories=chart_labels, ordered=True
)

total_chart = sum(status_counts.values())

# Render horizontal bar chart via raw Vega-Lite spec for reliable orientation.
# st.altair_chart(use_container_width=True) overrides Altair's width settings,
# which breaks horizontal bar charts. A raw Vega-Lite spec avoids this.
vlt = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "width": "container",
    "height": 140,
    "data": {
        "values": [
            {"Category": chart_labels[i], "Count": chart_values[i]}
            for i in range(len(chart_labels))
        ]
    },
    "mark": {"type": "bar", "cornerRadiusEnd": 4},
    "encoding": {
        "y": {
            "field": "Category",
            "type": "nominal",
            "sort": chart_labels,
            "axis": {"labelAngle": 0, "title": None}
        },
        "x": {
            "field": "Count",
            "type": "quantitative",
            "axis": {"tickMinStep": 1, "title": None}
        },
        "tooltip": [
            {"field": "Category", "type": "nominal"},
            {"field": "Count", "type": "quantitative"}
        ]
    },
    "config": {"view": {"stroke": None}, "axis": {"grid": False}}
}
st.vega_lite_chart(vlt, use_container_width=True)
st.caption(
    f"Exception breakdown: {dict(zip(chart_labels, chart_values))} | "
    f"Reconciled (plain): {status_counts.get('Reconciled', 0)} | "
    f"Total: {total_chart}"
)

st.divider()


# ══════════════════════════════════════════════════════════════════════
# SECTION B: Case Explorer
# ══════════════════════════════════════════════════════════════════════
st.subheader("Case Explorer")

# Filter bar
fc1, fc2, fc3, fc4 = st.columns([1, 2, 2, 2])

with fc1:
    case_type_filter = st.selectbox(
        "Case type", ["All", "Orders", "Settlements"], index=0
    )

with fc2:
    all_statuses = sorted(set(e["simplified_status"] for e in all_cases))
    status_filter = st.multiselect("Status", all_statuses, default=[])

with fc3:
    all_exceptions = sorted(
        set(e.get("exception_code") for e in all_cases if e.get("exception_code"))
    )
    exc_filter = st.multiselect("Exception code", all_exceptions, default=[])

with fc4:
    search_text = st.text_input("Search case_id", placeholder="ord_... or set_...")

# Apply filters
filtered = all_cases[:]

if case_type_filter == "Orders":
    filtered = [e for e in filtered if e["case_type"] == "order"]
elif case_type_filter == "Settlements":
    filtered = [e for e in filtered if e["case_type"] == "settlement"]

if status_filter:
    filtered = [e for e in filtered if e["simplified_status"] in status_filter]

if exc_filter:
    filtered = [
        e
        for e in filtered
        if e.get("exception_code") and e["exception_code"] in exc_filter
    ]

if search_text.strip():
    q = search_text.strip().lower()
    filtered = [e for e in filtered if q in e["case_id"].lower()]

# Pagination
PAGE_SIZE = 25
total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)

page_col1, page_col2, page_col3 = st.columns([1, 1, 4])
with page_col1:
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1)
with page_col2:
    st.caption(f"of {total_pages} ({len(filtered)} cases)")

start = (page - 1) * PAGE_SIZE
page_cases = filtered[start : start + PAGE_SIZE]

# Table
if page_cases:
    # Color mapping for status
    STATUS_COLORS = {
        "Reconciled": "green",
        "Reconciled (with note)": "green",
        "Reconciled (no credit due)": "green",
        "Needs Human Review": "orange",
        "Unresolved": "red",
    }
    table_data = []
    for e in page_cases:
        table_data.append(
            {
                "case_id": e["case_id"],
                "Type": e["case_type"],
                "Status": e["simplified_status"],
                "Exception": e.get("exception_code") or "—",
                "Has explanation": "Yes" if e.get("explanation") else "—",
            }
        )
    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.TextColumn(
                "Status",
                help="Reconciliation status",
            ),
        },
    )
    # Apply row-level color via CSS injection
    status_col_idx = 2  # Status is the 3rd column (0-indexed)
    css_rows = []
    for i, e in enumerate(page_cases):
        color = STATUS_COLORS.get(e["simplified_status"], "white")
        css_rows.append(
            f'var(--row-{i}) = [{{"background-color": "{color}20", "color": "{color}"}}];'
        )
    if css_rows:
        st.markdown(
            '<style>' + '\n'.join(css_rows) + '</style>',
            unsafe_allow_html=True,
        )
else:
    st.info("No cases match the current filters.")

# Detail panel
st.markdown("---")
st.markdown("**Case Detail**")

detail_ids = [e["case_id"] for e in filtered]
if detail_ids:
    selected_id = st.selectbox("Select a case to inspect", detail_ids, index=0)
    selected = next((e for e in filtered if e["case_id"] == selected_id), None)

    if selected:
        with st.expander(f"{selected['case_id']} — {selected['simplified_status']}", expanded=True):
            # Header
            st.markdown(
                f"**{selected['case_id']}** | "
                f"Type: `{selected['case_type']}` | "
                f"Status: **{selected['simplified_status']}** | "
                f"Exception: `{selected.get('exception_code') or 'none'}`"
            )

            # Key Figures
            kf = selected.get("key_figures", {})
            if kf:
                st.markdown("**Key Figures:**")
                st.json(kf)

            # Explanation
            if selected.get("explanation"):
                st.markdown("**Explanation (Phase 3):**")
                st.markdown(selected["explanation"])

            # Suggested Action
            if selected.get("suggested_action"):
                st.markdown(f"**Suggested Action:** {selected['suggested_action']}")

            # Confidence Note
            if selected.get("confidence_note"):
                st.warning(f"**Confidence Note:** {selected['confidence_note']}")

            # Soft Flags
            flags = selected.get("soft_flags", [])
            if flags:
                st.markdown("**Soft Flags:**")
                for flag in flags:
                    st.caption(f"⚠ {flag}")
else:
    st.info("No cases to display.")

st.divider()


# ══════════════════════════════════════════════════════════════════════
# SECTION C: Live Q&A
# ══════════════════════════════════════════════════════════════════════
st.subheader("Ask a Question")

# Session state for conversation history
if "qa_history" not in st.session_state:
    st.session_state.qa_history = []

# ── Clear input BEFORE the widget renders (Streamlit rule: cannot
#    modify a widget's session_state key after the widget is created) ──
if st.session_state.get("_qa_clear_pending"):
    st.session_state["qa_input"] = ""
    st.session_state["_qa_clear_pending"] = False

# Clear history button (for demo reset)
if st.session_state.qa_history:
    if st.button("\U0001f5d1 Clear history", key="clear_history"):
        st.session_state.qa_history = []
        st.session_state["_qa_clear_pending"] = True
        st.rerun()

# Fixed-height scrollable container for conversation history
_qa_container = st.container(height=380)
with _qa_container:
    if st.session_state.qa_history:
        # Reverse chronological: most recent first
        for q_text, a_text, a_verified, a_fallback in reversed(
            st.session_state.qa_history
        ):
            with st.chat_message("user"):
                st.markdown(q_text)
            with st.chat_message("assistant"):
                if a_fallback == "api_error":
                    st.warning(a_text)
                else:
                    st.markdown(a_text)
                if a_verified:
                    st.caption("\u2713 Verified against source data")
                else:
                    st.caption("\u26a0 Figures not fully verified")
            # Tight gap within pair, wider gap between exchanges
            st.markdown("---")
    else:
        st.info("Ask a question below to get started.")

# Input: st.text_input + st.button (NOT st.chat_input)
# Root cause confirmed: st.chat_input causes auto-scroll on every load/rerun.
_q_col1, _q_col2 = st.columns([5, 1])
with _q_col1:
    question = st.text_input(
        "Ask about any order or settlement...",
        key="qa_input",
        label_visibility="collapsed",
    )
with _q_col2:
    ask_pressed = st.button("Ask", key="qa_submit")

if question and ask_pressed:
    # Call Q&A agent
    with st.spinner("Thinking..."):
        try:
            from qa_agent import answer_question

            result = answer_question(question)
        except Exception as e:
            result = {
                "answer": "I apologize -- the QA service encountered an error. Please try again.",
                "source_case_ids": [],
                "verified": False,
                "category": "out_of_scope",
                "fallback_reason": "api_error",
            }

    # Save to history, set flag to clear input on next run, then rerun
    st.session_state.qa_history.append(
        (
            question,
            result["answer"],
            result.get("verified", False),
            result.get("fallback_reason"),
        )
    )
    st.session_state["_qa_clear_pending"] = True
    st.rerun()

st.divider()


# ══════════════════════════════════════════════════════════════════════
# SECTION D: Engineering Story
# ══════════════════════════════════════════════════════════════════════
st.subheader("Engineering Story")

# Extract sections 3 and 4 from audit_trail.md
story_text = ""
try:
    # Find section 3 start
    s3_start = audit_text.find("## 3.")
    # Find section 5 start (sections 3 and 4 are between ## 3. and ## 5.)
    s5_start = audit_text.find("## 5.")
    if s3_start >= 0 and s5_start > s3_start:
        story_text = audit_text[s3_start:s5_start].rstrip()
    elif s3_start >= 0:
        # Fallback: take from section 3 to end
        story_text = audit_text[s3_start:].rstrip()
except Exception:
    story_text = "*Could not load engineering story from audit_trail.md*"

with st.expander("What Broke and How We Fixed It", expanded=False):
    if story_text:
        st.markdown(story_text)
    else:
        st.info("Engineering story not available.")

st.divider()


# ══════════════════════════════════════════════════════════════════════
# SECTION E: Footer
# ══════════════════════════════════════════════════════════════════════
st.markdown("---")
st.caption("Built for Razorpay AI Buildathon | Track: AI Finance Controller")
st.caption(
    "Pipeline: 8 phases | 591 cases | 100% accuracy | "
    "96.1% reconciled | 3.9% transparently flagged"
)

# Hash provenance
hash_display = " | ".join(f"{k}: {v}" for k, v in hashes.items())
st.caption(f"Hash provenance: {hash_display}")
st.caption("Run: `streamlit run dashboard.py`")

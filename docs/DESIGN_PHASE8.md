# DESIGN_PHASE8.md - Streamlit Dashboard

## 1. Purpose

Build the primary judge-facing artifact: an interactive Streamlit dashboard
that makes the reconciliation system strengths land visually within a
5-minute pitch window. The dashboard shows headline accuracy numbers, lets
judges browse every one of the 591 cases, explains what broke and how it
was fixed, and offers a live Q&A agent -- all from a single streamlit run
command.

## 2. Page Structure and Layout

Single-page app with 5 vertically-stacked sections. No multi-page navigation
-- everything visible on one scroll, keeping the pitch linear and controlled.

Section ordering is deliberate:
- Metrics first (hook the judge with 100% accuracy)
- Explorer second (proof of depth: 591 browsable cases)
- Q&A third (live demo moment)
- Engineering story fourth (differentiator: what broke and how we fixed it)
- Footer fifth (hash provenance, run instructions)

## 3. Data Loading

All data is static/frozen. Loaded ONCE at app startup via @st.cache_data.

- reconciliation_report.json: 591 entries + summary block
- metrics_report.json: accuracy, per-code precision/recall, FPR/FNR
- audit_trail.md: raw markdown text for the engineering story section

The Q&A agent (qa_agent.answer_question()) is the ONE live/dynamic part.
It is NOT cached -- called on every user question submission. The agent
itself holds its own cached data in memory (lazy init on first call).

No other files are read at runtime. No prior phase outputs are modified.

## 4. Section A: Headline Metrics

### Layout: 4 metric cards in a single row + 1 chart below

Metric cards (use st.metric):

| Card | Value source | Display |
|------|-------------|---------|
| Accuracy | mr[overall][accuracy] = 1.0 | 100% in large green text |
| Matched | rr[summary][overall][reconciled_total] = 568 | 568/591 with 96.1% subtitle |
| Review | rr[summary][overall][needs_human_review_total] = 14 | 14 with 2.4% subtitle |
| Unresolved | rr[summary][overall][unresolved_total] = 9 | 9 with 1.5% subtitle |

Chart: Horizontal bar showing status breakdown (5 mutually-exclusive categories,
computed by counting simplified_status values across all 591 entries -- NOT by
re-using the aggregate summary field, which double-counts):
- Reconciled (plain): 562 (green)
- Reconciled (with note): 5 (light green)
- Reconciled (no credit due): 1 (light blue)
- Needs Human Review: 14 (yellow)
- Unresolved: 9 (red)

Total: 562 + 5 + 1 + 14 + 9 = 591

Data source: Counter(e["simplified_status"] for e in rr["orders"] + rr["settlements"])
Use st.bar_chart (native Streamlit, no extra deps needed).

## 5. Section B: Case Explorer

### 5a. Filter bar

Horizontal row of filter controls:

| Filter | Type | Options | Default |
|--------|------|---------|----------|
| Case type | selectbox | All, Orders, Settlements | All |
| Status | multiselect | Reconciled, Reconciled (with note), Reconciled (no credit due), Needs Human Review, Unresolved | All |
| Exception code | multiselect | None, UNMATCHED_ORDER, REFUND_SPLIT, CURRENCY_MISMATCH, DUPLICATE_ORDER, UNRECORDED_REFUND, GHOST_TRANSACTION, NEFT_FAILED, NO_CREDIT_EXPECTED | All |
| Search | text_input | Free text, searches case_id | empty |

Filtering is done in-memory on the combined list. With 591 entries this is instant.

### 5b. Case table

Display filtered results in st.dataframe (interactive, sortable, searchable).

| Column | Source field | Width | Notes |
|--------|-------------|-------|-------|
| case_id | case_id | Wide | Clickable -- selecting opens detail view |
| Type | case_type | Narrow | order or settlement |
| Status | simplified_status | Medium | Color-coded (green/yellow/red) |
| Exception | exception_code | Medium | Blank if null |
| Has explanation | Derived | Narrow | Yes if explanation is not null |

Table paginated: 25 rows per page via manual slicing.

### 5c. Detail panel

When a row is selected, show full detail in an st.expander:

- Header: case_id, type, status, exception_code
- Key Figures: all fields from key_figures dict
- Explanation: full Phase 3 text if present (29 cases), otherwise omitted
- Suggested Action: advisory text if present
- Confidence Note: only for needs_review cases
- Soft Flags: list of any flags

This generalizes the fully-traced example structure from audit_trail.md
Section 6 to work for ANY case.

## 6. Section C: Live Q&A

### Interface

- st.text_input: label=Ask a question about any order or settlement
- st.button: Ask
- On submit: call qa_agent.answer_question(question)
- Display response in st.markdown for the answer text
- Show source_case_ids as clickable reference text
- Show verified status: green checkmark if true, yellow warning if false
- Show fallback_reason if present (small grey text)

### api_error fallback handling

If fallback_reason == api_error, the answer text is already the polite
message from qa_agent.py. Display it in st.warning() -- no Python traceback,
no broken UI. The rest of the dashboard remains fully functional.

### Session state

Use st.session_state to persist conversation history within a session:
- st.session_state.qa_history: list of (question, answer) tuples
- Display previous Q&A pairs above the input so the judge can scroll back

## 7. Section D: Engineering Story

Render audit_trail.md Sections 3 and 4 (What Broke + Failure Handled
Gracefully) as a Streamlit expander.

Implementation: extract sections 3 and 4 from the raw markdown using
string splitting on markers (section 3 starts at ## 3., section 5
starts at ## 5. -- section 4 is between them). Render via st.markdown().

Why this section matters: directly answers the buildathon submission
requirement to explain what broke and how it was fixed. Most submissions
wont have a concrete, verified bug-fix narrative. Surfacing it prominently
makes the engineering story a differentiator.

## 8. Section E: Footer

Small footer:
- Built for Razorpay AI Buildathon | Track: AI Finance Controller
- Pipeline: 6 phases | 591 cases | 100% accuracy against ground truth
- Hash provenance (first 16 chars of each key files SHA-256)
- Run: streamlit run dashboard.py

## 9. Performance and Responsiveness

### Case table
591 rows: in-memory filtering is instant. st.dataframe handles this natively.

### Pagination
25 rows per page via st.number_input(Page, 1, total_pages) = 24 pages.

### Filtering
Python list comprehensions on 591-entry list. Intersection on each rerun.
With 591 entries this takes less than 10ms.

### Q&A latency
Groq API call takes 1-3 seconds. Show st.spinner(Thinking...) during call.

### Caching
@st.cache_data on load_data() ensures JSON files are read once and cached
across Streamlit reruns. Cache invalidated only on app restart.

## 10. File Locations and Dependencies

### New file

| File | Purpose |
|------|---------|
| dashboard.py (project root) | Streamlit app entry point |

### Read-only inputs (at runtime)

| File | Loaded by |
|------|-----------|
| engine/output/reconciliation_report.json | dashboard.py via @st.cache_data |
| engine/output/metrics_report.json | dashboard.py via @st.cache_data |
| audit_trail.md | dashboard.py via @st.cache_data |
| agent/qa_agent.py | dashboard.py (imported, called on question submit) |

### Dependencies

| Package | Already approved? |
|---------|-------------------|
| streamlit | Yes (requirements.txt) |
| openai | Yes (requirements.txt) |

No new dependencies needed.

## 11. Run Instructions

  pip install -r requirements.txt
  # Ensure .env has GROQ_API_KEY
  # Ensure data/raw/ and engine/output/ exist
  streamlit run dashboard.py

Opens at http://localhost:8501 by default.

## 12. What Phase 8 Does NOT Do

- Does NOT re-run the matcher, reconciler, metrics scorer, or any prior phase
- Does NOT modify any file from Phases 1-7
- Does NOT add new matching/scoring/classification logic
- Does NOT cache Q&A responses (each question gets a fresh API call)
- Does NOT use any library beyond Streamlit + openai SDK (locked stack)

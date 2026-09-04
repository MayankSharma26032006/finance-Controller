# Finance Controller — Razorpay AI Buildathon

**Track:** AI Finance Controller

## What This Is

An agent-assisted reconciliation system that matches orders across three noisy data sources — an internal order ledger, a Razorpay settlement report, and a bank statement — and reports match rate plus an honest exception list. Razorpay settlement isn't 1:1: it deducts transaction fees + GST, batches multiple orders into one settlement UTR, and partial refunds split one order across two settlement events. The engine handles all of this with batch-first matching, currency conversion, rounding tolerance, and label normalization — then an AI layer *narrates* each exception (it never decides), and every narration is automatically fact-checked against source data before it's trusted.

Built across 8 design-and-build phases, from synthetic data generation through a live Q&A agent and Streamlit dashboard. The full pipeline — data → matcher → AI narration → reconciliation → ground-truth scoring → audit trail — is reproducible end to end from committed, frozen data.

## Headline Results

| Metric | Value |
|---|---|
| Cases scored | **591** (500 orders + 91 settlements) |
| Classification accuracy | **100%** — all 591 cases classified consistently with the project's synthetic ground truth (0 mismatches) |
| Clean reconciliation rate | **96.1%** — 568 cases fully reconciled with no human involvement |
| Honest exception rate | **3.9%** — 23 cases correctly and transparently flagged for human review or escalation, not hidden |
| AI-narrated exceptions | **28** exception cases explained by the LLM, all verified against source data |
| Automated tests | **64** regression tests, all passing |

**What the numbers mean:** the system is 100% accurate at *classification*; the 3.9% flagged as needing review are genuine ambiguities the system correctly identified rather than guessed on. Zero false negatives — no exception was ever silently missed — and the only false positive is a known near-cutoff edge case that the exception list transparently discloses.

**Scope of the 100%:** it is measured against *synthetic* ground truth — labels generated from the same rule engine that produced the data, then corrected once by manual audit. Generator and matcher share assumptions, so the score proves the engine is internally consistent with that reference set and was externally audited, not that it would score 100% on real, unlabeled data.

**How AI output is guarded:** every AI-generated explanation and Q&A answer is automatically fact-checked against its source data before display — the check extracts *every* stated figure (including amounts written without a currency marker) and compares them numerically (not by substring) against the case data and a fixed domain-facts block. Verification fails closed: an explanation that carries decimal amounts it cannot extract for checking is recorded as unverified, never as a silent pass.

## The 8 Phases

| Phase | What it built | Status |
|---|---|---|
| 0 | Razorpay settlement research (fees, GST, batching, refunds) | ✅ Complete |
| 1 | Schema design + synthetic data generator with ground truth | ✅ Complete |
| 2 | Deterministic matching engine (batch → order → refund layers) | ✅ Complete |
| 3 | Agent reasoning layer — narrates exceptions, never decides | ✅ Complete |
| 4 | Exception categorization + reconciliation report | ✅ Complete |
| 5 | Metrics scoring against ground truth (accuracy, FPR/FNR) | ✅ Complete |
| 6 | Audit trail with chain-of-custody hashes | ✅ Complete |
| 7 | Settlement Q&A agent — ask anything about any of the 591 cases | ✅ Complete |
| 8 | Streamlit dashboard for demo and review | ✅ Complete |

The deterministic reconciliation result is always the source of truth. AI is only an explanatory layer — an AI failure can never change a correct reconciliation result.

## Run It

### Prerequisites
- Python 3.8+
- A free Groq API key (only needed for Phase 3 narration and the live Q&A agent; everything else runs offline)

### Quick start (dashboard)

```bash
pip install -r requirements.txt
copy .env.example .env        # Windows — then add your GROQ_API_KEY
# cp .env.example .env        # macOS / Linux
streamlit run dashboard.py
```

Opens at `http://localhost:8501`. The dashboard includes headline metrics, a browsable case table for all 591 cases, a live Q&A panel, and the engineering story from the audit trail. The full app works without a Groq key except the Q&A chatbot, which falls back to a clean message instead of crashing.

### Run the whole pipeline

```bash
python run_pipeline.py --all             # Phases 2-6 (matcher → audit trail)
python run_pipeline.py --with-ai-eval    # Phases 2-5 (matcher → metrics)
python run_pipeline.py --reconcile-only  # Phase 2 (deterministic matcher only)
python run_pipeline.py --regenerate-data # Phase 1 + 2-6 (requires typing "yes" — data is frozen for a reason)
```

Data is **frozen**. `--regenerate-data` exists only to prove the generator itself is correct; the frozen dataset is what every result, hash, and audit entry is anchored to, so regeneration is off by default and guarded by an explicit confirmation prompt.

### Run individual phases

```bash
# Phase 1: Generate synthetic data (frozen — do not re-run unless needed)
python3 data/generate_data.py

# Phase 2: Deterministic matcher
python3 engine/matcher_exact.py

# Phase 3: AI explanations for the 28 exception cases (needs GROQ_API_KEY)
python3 agent/explainer.py

# Phase 4: Reconciliation report
python3 engine/reconciler.py

# Phase 5: Score against ground truth
python3 engine/metrics_scorer.py

# Phase 6: Generate the audit trail
python3 engine/generate_audit.py

# Phase 7: Test the Q&A agent (6 built-in test questions)
python3 agent/qa_agent.py
```

### Run the tests

```bash
python -m unittest tests.test_reconciliation -v   # 64 tests, ~0.1s, no API calls
```

## How It Works

```
data/raw/*.csv ──────────► engine/matcher_exact.py ──► engine/output/match_log.json
     (frozen)                    │ deterministic               │
                                 ▼                            ▼
                      agent/explainer.py ─────► agent/output/explanations.json
                     (LLM narration)                  (28 cases, verified)
                                 │                            │
                                 ▼                            ▼
                      engine/reconciler.py ──► engine/output/reconciliation_report.json
                                 │
                                 ▼
                      engine/metrics_scorer.py ──► engine/output/metrics_report.json + .md
                                 │       scores against data/raw/ground_truth*.json
                                 ▼
                      engine/generate_audit.py ──► audit_trail.md
                                 (live-computed SHA-256 chain of custody)

dashboard.py + agent/qa_agent.py read the committed outputs only —
they never re-run the matcher or modify any prior phase's files.
```

### The matching engine (deterministic, testable)

Three layers, in order:

1. **Batch matcher** — matches each settlement batch to its bank credit by UTR with a ±₹0.50 rounding tolerance; flags missing credits as `NEFT_FAILED`.
2. **Order matcher** — distributes a settled batch across its order rows; handles currency conversion (USD→INR at FX 83.00, minus fees + GST), duplicate orders, and unrecorded refunds.
3. **Refund classifier** — full refunds (residual = −(fee + GST)), refund splits across batches, and partial refunds within a batch.

Exceptions surface through a single structured path (`exceptions.py`) with a code, an explanation, and identifiers — never silently swallowed.

### The AI layer (narrate, don't decide)

Phase 3 generates a plain-language explanation for each exception case, and Phase 7 lets you ask natural-language questions about any of the 591 cases. Both follow the same discipline:

- **Grounded** — the prompt receives the case's real data plus a fixed domain-facts block (fee %, GST %, FX rate). The model is told to quote, not invent.
- **Temperature 0** — `openai/gpt-oss-120b` via Groq for reproducible output.
- **Fact-checked** — every numeric figure in an explanation is verified against both the case data and the domain facts, including amounts written without a currency marker (e.g. "residual of 2196.99"); verification is numeric (not substring), so a short figure like "2" can never falsely match an unrelated amount like 47,500.00. Results are logged per case.
- **Fails closed** — an explanation that carries decimal figures but yields none for cross-checking is recorded as unverified (`amounts_present_but_none_extracted`), never as a silent pass.
- **Fail-safe** — empty, malformed, or overlong LLM responses are rejected and recorded as failure states; a failed explanation never changes the deterministic reconciliation result.

### Real bugs found — and fixed

Because every pipeline output was audited against ground truth during review, four real defects were caught and corrected rather than papered over:

1. **Duplicate-order suppression** — the matcher skipped a genuinely ambiguous second order; now surfaced as a review case.
2. **12 unrecorded refunds** — refund rows absent from settlements were silently dropped; now detected and classified.
3. **Negative-net mislabeling** — a negative-net batch was tagged as a failure instead of a legitimate credit; classification corrected.
4. **A refuted claim** — one suspected bug turned out to be correct behavior, confirmed by the audit trail (worth saying — not every finding was a bug).

The full narrative — what was wrong, how it was caught, how it was fixed, how it was independently verified — lives in `audit_trail.md`.

## Data Integrity & Audit

- **Frozen inputs** — the raw CSVs and ground-truth files under `data/raw/` are committed and never regenerated in normal use.
- **Chain of custody** — `metrics_report.json` and `audit_trail.md` record SHA-256 hashes of every pipeline stage, recomputed live from the files at generation time (never hand-copied).
- **Cross-platform stable hashes** — a `.gitattributes` enforces LF line endings, and the hash functions normalize `\r\n` → `\n` as a safety net, so the audit trail verifies identically on Windows, Linux, and CI.
- **What the audit proves** — that the reported 100% accuracy was computed against exactly the committed ground truth, from exactly the committed inputs, by the committed code.

## AI Layer Evaluation

Every AI-generated explanation is automatically fact-checked against source data before being shown to a user; verification results are logged per case. The hallucination safeguard uses two-source verification: each numeric figure the LLM states is cross-checked against both the case-specific data (amounts, IDs, dates from the raw CSVs) and a curated domain facts block (fee percentages, GST rates, FX conversion rate).

- All **28/28 explanations pass verification** (`verified: true`) — every stated figure in each, including marker-less amounts, cross-checks against its source data.
- The verifier compares figures numerically with a small tolerance (`math.isclose`, abs_tol 0.01) — never by substring, so "2" cannot falsely match 47,500.00, and 83.00 matches 83. Regression tests lock this in.
- The check **fails closed**: bare amounts are extracted and verified like any other figure, and an explanation whose decimal figures cannot be extracted is recorded as unverified rather than vacantly passing.
- During development this mechanism caught Unicode normalization issues (narrow no-break spaces causing false-positive mismatches) and percentage-to-decimal equivalences ("100%" vs "1.0") that were fixed in the verifier.
- One relational-claim error — where the LLM correctly quoted numbers but misrepresented how they related to each other — slipped past the automated check and was caught by manual review, confirming that fact-verification is a necessary but not sufficient safeguard.

## Testing

**64 automated regression tests** in `tests/test_reconciliation.py`, run with the standard library only (no pytest, no external framework, no live API calls):

```bash
python -m unittest tests.test_reconciliation -v
```

| Area | Tests |
|---|---|
| Batch matching success / mismatch paths | `TestBatchMatchSuccess`, `TestBatchMatchMismatch` |
| Currency & FX (USD→INR at 83.00, CURRENCY_MISMATCH) | `TestCurrencyFx` |
| Refund classification (full / split / partial) | `TestRefundFull`, `TestRefundSplit` |
| Order-level exceptions (unmatched, duplicate, unrecorded refund) | `TestOrderMatchingExceptions` |
| Status mapping, ghost transactions, metrics-scorer arithmetic | `TestStatusMapping`, `TestGhostTransaction`, `TestMetricsScorer` |
| QA-agent parsing + hallucination-check numerics (no API) | `TestQAAgentParsing` |
| Explanation validation (empty / malformed / overlong rejection) | `TestExplanationValidation` |
| Figure verification, fail-closed on unextractable amounts | `TestFigureVerificationFailClosed` |
| Full-dataset integration (500 orders, 91 settlements) | `TestEndToEndPipeline` |
| Output reproducibility (fresh matcher == committed match_log hash) | `TestMatcherOutputReproducibility` |

The full-dataset integration class runs the real matcher against the frozen CSVs and verifies record counts, field completeness, determinism, and a well-formed reconciliation report — no LLM involved. See `TESTING.md` for the full breakdown.

## Project Structure

```
├── dashboard.py               # Streamlit dashboard (Phase 8)
├── audit_trail.md             # Audit trail with chain-of-custody (Phase 6)
├── run_pipeline.py            # Unified pipeline runner (all phase flags)
├── requirements.txt           # Python dependencies
├── .env.example               # Template — copy to .env and add GROQ_API_KEY
├── .gitattributes             # LF line endings for cross-platform hash stability
├── TESTING.md                 # Test documentation
│
├── docs/                      # Design documents for Phases 0-8
│   ├── DESIGN_PHASE1.md ... DESIGN_PHASE8.md
│
├── data/
│   ├── generate_data.py       # Synthetic data generator (Phase 1)
│   ├── validate_data.py       # Data validator — 28 checks, all PASS
│   ├── check_two.py           # Label mismatch + rounding checks
│   └── raw/                   # FROZEN generated data (committed)
│       ├── order_ledger.csv             # 500 orders with edge cases
│       ├── settlement_report.csv        # 91 settlement batches
│       ├── bank_statement.csv           # 100 bank credits (90 Razorpay + 10 noise)
│       ├── ground_truth.json            # Order-level ground truth
│       └── ground_truth_settlements.json # Settlement-level ground truth
│
├── engine/
│   ├── preprocessor.py        # Constants, data loading, label normalization
│   ├── batch_matcher.py       # Layer 1: settlement <-> bank matching
│   ├── order_matcher.py       # Layer 2: order <-> settlement matching
│   ├── refund_classifier.py   # Full/partial/split refund classification
│   ├── exceptions.py          # Exception detection + output generation
│   ├── matcher_exact.py       # Phase 2 orchestrator (runs all layers)
│   ├── reconciler.py          # Phase 4: reconciliation report builder
│   ├── metrics_scorer.py      # Phase 5: accuracy scoring vs ground truth
│   ├── generate_audit.py      # Phase 6: audit trail generator
│   └── output/                # Pipeline outputs (committed)
│       ├── match_log.json
│       ├── reconciliation_report.json
│       ├── metrics_report.json
│       └── metrics_report.md
│
├── agent/
│   ├── explainer.py           # Phase 3: LLM narration of exceptions
│   ├── qa_agent.py            # Phase 7: natural-language Q&A agent
│   └── output/
│       └── explanations.json  # 28 narrated exception cases (verified)
│
├── tests/
│   ├── __init__.py│       └── test_reconciliation.py # 64 regression tests
│
└── DATA_ARCHITECTURE_REPORT.md  # Data-flow + production-database analysis
```

## Tech Stack

- **Python 3.8+** — stdlib-first data processing (csv, json, random, datetime, unittest, hashlib)
- **Groq + openai SDK** — LLM narration via `openai/gpt-oss-120b` (free tier), temperature 0
- **Streamlit** — dashboard UI
- **python-dotenv** — API-key loading
- **pandas** — used only in the dashboard for the chart and table styling
- **Deliberately minimal** — no vector DB, no LangChain, no external orchestration frameworks

## Repository Hygiene

- The real `.env` (with your API key) is gitignored and never committed.
- `.freebuff/` and preview log files are gitignored.
- Frozen data, pipeline outputs, and the audit trail are all committed, so a fresh clone is fully runnable — no undocumented setup steps.

## License

Built for the Razorpay AI Buildathon.

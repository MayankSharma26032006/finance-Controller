# Finance Controller — Razorpay AI Buildathon

**Track:** AI Finance Controller

## What This Is

An agent-assisted reconciliation system that matches orders across three
noisy data sources — an internal order ledger, a Razorpay settlement
report, and a bank statement — and reports match rate plus an honest
exception list. Razorpay settlement isn't 1:1: it deducts transaction
fees + GST, batches multiple orders into one settlement UTR, and partial
refunds split one order across two settlement events. The engine handles
all of this with batch-first matching, currency conversion, rounding
tolerance, and label normalization.

## Results

- **591 cases** scored (500 orders + 91 settlements)
- **100% classification accuracy** — every case correctly classified against independently verified ground truth (0 mismatches)
- **96.1% clean reconciliation rate** — cases fully reconciled with no human involvement needed
- **3.9% honest exception rate** — cases correctly and transparently flagged for human review or escalation, not hidden

The system is 100% accurate at classification; the 3.9% flagged as needing review are genuine ambiguities the system correctly identified rather than guessed on.

- **28 AI-narrated** exception cases via Groq LLM
- **Full audit trail** with chain-of-custody hashes across every pipeline stage

## AI Layer Evaluation

Every AI-generated explanation is automatically fact-checked against source data before being shown to a user; verification results are logged per case. The hallucination safeguard uses two-source verification: each numeric figure the LLM states is cross-checked against both the case-specific data (amounts, IDs, dates from the raw CSVs) and a curated domain facts block (fee percentages, GST rates, FX conversion rate). During development, this mechanism caught Unicode normalization issues (narrow no-break spaces causing false-positive mismatches) and percentage-to-decimal equivalences ("100%" vs "1.0") that were fixed in the verifier. One relational-claim error — where the LLM correctly quoted numbers but misrepresented how they related to each other — slipped past the automated check and was caught by manual review, confirming that fact-verification is a necessary but not sufficient safeguard.

All 28 explanations currently pass verification (28/28 verified: true).

## Running the Dashboard

```bash
pip install -r requirements.txt
# Set your Groq API key in .env:
#   echo "GROQ_API_KEY=gsk_..." > .env
streamlit run dashboard.py
```

Opens at `http://localhost:8501`. The dashboard includes:
- Headline metrics (accuracy, match rate, exception breakdown)
- Browsable/filterable case table for all 591 cases
- Live Q&A — ask any natural-language question about orders or settlements
- Engineering story — the bug-fix narrative from the audit trail

## Running Individual Phases

```bash
# Phase 1: Generate synthetic data (data is frozen — do not re-run unless needed)
python3 data/generate_data.py

# Phase 2: Run the deterministic matcher
python3 engine/matcher_exact.py

# Phase 3: Generate AI explanations for the 29 exception cases
python3 agent/explainer.py

# Phase 4: Build the reconciliation report
python3 engine/reconciler.py

# Phase 5: Score against ground truth
python3 engine/metrics_scorer.py

# Phase 6: Generate the audit trail
python3 engine/generate_audit.py
```

Full pipeline output: `match_log.json` → `explanations.json` → `reconciliation_report.json` → `metrics_report.json` → `audit_trail.md`

## Current Status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Razorpay settlement research | ✅ Complete |
| Phase 1 | Schema design + synthetic data generator | ✅ Complete |
| Phase 2 | Deterministic matching engine | ✅ Complete |
| Phase 3 | Agent reasoning layer (Groq LLM) | ✅ Complete |
| Phase 4 | Exception categorization + reconciliation report | ✅ Complete |
| Phase 5 | Metrics scoring against ground truth | ✅ Complete |
| Phase 6 | Audit trail with chain-of-custody | ✅ Complete |
| Phase 7 | Settlement Q&A agent | ✅ Complete |
| Phase 8 | Streamlit dashboard | ✅ Complete |

## Project Structure

```
├── dashboard.py               # Streamlit dashboard (Phase 8)
├── audit_trail.md             # Audit trail document (Phase 6)
├── requirements.txt           # Python dependencies
├── README.md
│
├── docs/                      # Design documents
│   ├── DESIGN_PHASE1.md       #   Schema + synthetic data
│   ├── DESIGN_PHASE2.md       #   Matching engine
│   ├── DESIGN_PHASE3.md       #   Agent reasoning layer
│   ├── DESIGN_PHASE4.md       #   Exception categorization
│   ├── DESIGN_PHASE5.md       #   Metrics scoring
│   ├── DESIGN_PHASE6.md       #   Audit trail
│   ├── DESIGN_PHASE7.md       #   Q&A agent
│   └── DESIGN_PHASE8.md       #   Dashboard
│
├── data/
│   ├── generate_data.py       # Synthetic data generator
│   ├── validate_data.py       # Validation suite (28 checks)
│   ├── check_two.py           # Label mismatch + rounding checks
│   └── raw/                   # Frozen generated data
│       ├── order_ledger.csv       # 500 orders (10+ edge cases)
│       ├── settlement_report.csv  # 91 settlement batches
│       ├── bank_statement.csv     # 100 bank credits (90 Razorpay + 10 noise)
│       ├── ground_truth.json      # Order-level ground truth
│       └── ground_truth_settlements.json  # Settlement-level ground truth
│
├── engine/
│   ├── preprocessor.py        # Constants, data loading, label normalization
│   ├── batch_matcher.py       # Layer 1: settlement <-> bank matching
│   ├── order_matcher.py       # Layer 2: order <-> settlement matching
│   ├── refund_classifier.py   # Full/partial refund classification
│   ├── exceptions.py          # Exception detection + output generation
│   ├── matcher_exact.py       # Orchestrator (runs all layers)
│   ├── reconciler.py          # Phase 4: reconciliation report builder
│   ├── metrics_scorer.py      # Phase 5: accuracy scoring against ground truth
│   ├── generate_audit.py      # Phase 6: audit trail generator
│   └── output/                # Pipeline outputs
│       ├── match_log.json
│       ├── reconciliation_report.json
│       ├── metrics_report.json
│       └── metrics_report.md
│
├── agent/
│   ├── explainer.py           # Phase 3: LLM-powered case narration
│   ├── qa_agent.py            # Phase 7: natural-language Q&A agent
│   └── output/
│       └── explanations.json  # 29 narrated exception cases
│
└── .gitignore
```

## Tech Stack

- **Python 3.8+** — stdlib only for data processing (csv, json, random, datetime)
- **Groq + openai SDK** — LLM narration via `llama-3.3-70b-versatile` (free tier)
- **Streamlit** — dashboard UI
- **No pandas, no vector DB, no LangChain** — minimal dependencies by design

## License

Built for the Razorpay AI Buildathon.

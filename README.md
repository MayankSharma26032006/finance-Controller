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

## Current Status

| Phase | Status |
|---|---|
| Phase 0 — Razorpay settlement research | ✅ Complete |
| Phase 1 — Schema design + synthetic data | ✅ Complete |
| Phase 2 — Deterministic matching engine | ✅ Complete |
| Phase 3 — Agent reasoning layer | ⏳ Not started |
| Phase 4 — Exception categorization | ⏳ Not started |
| Phase 5 — Metrics scoring | ⏳ Not started |
| Phase 6 — Audit trail | ⏳ Not started |

## Running the Matcher

```bash
cd engine
python3 matcher_exact.py
```

Reads frozen data from `data/raw/`, outputs `match_log.json` to `engine/output/`.

Expected output: 486 matched, 5 matched_with_note, 1 needs_review, 8 hard_exception (500 orders total).

## Validating the Generated Data

```bash
cd data
python3 validate_data.py
```

Runs 28 checks against the frozen synthetic data (edge cases, arithmetic, ground truth coverage).

## Project Structure

```
├── docs/                      # Design documents
│   ├── DESIGN_PHASE1.md       #   Phase 0 research + Phase 1 schema
│   └── DESIGN_PHASE2.md       #   Phase 2 matching engine design
├── data/
│   ├── generate_data.py       #   Synthetic data generator
│   ├── validate_data.py       #   Validation suite
│   ├── check_two.py           #   Label mismatch + rounding checks
│   └── raw/                   #   Frozen generated data (3 CSVs + 2 ground truths)
├── engine/
│   ├── preprocessor.py        #   Constants, data loading, helpers
│   ├── batch_matcher.py       #   Layer 1: settlement <-> bank
│   ├── order_matcher.py       #   Layer 2: order <-> settlement
│   ├── refund_classifier.py   #   Refund type classification
│   ├── exceptions.py          #   Ghost detection, consistency checks, output
│   ├── matcher_exact.py       #   Orchestrator (run this)
│   └── output/                #   match_log.json output
├── agent/                     #   Phase 3 placeholder (agent reasoning layer)
├── README.md
├── requirements.txt
└── .gitignore
```

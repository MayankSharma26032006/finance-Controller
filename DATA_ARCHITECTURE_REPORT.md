# Data Architecture Analysis

## 1. Persistent Data Files

### Source-of-Truth Financial Data (Frozen)

| File | Format | Rows | Description |
|------|--------|------|-------------|
| data/raw/order_ledger.csv | CSV | 501 (+ header) | Internal order records. One row per order. |
| data/raw/settlement_report.csv | CSV | 504 (+ header) | Razorpay settlement rows. Multiple rows per settlement_id (batched). |
| data/raw/bank_statement.csv | CSV | 100 (+ header) | Bank NEFT credit/debit statements. |
| data/raw/ground_truth.json | JSON | 501 entries | Expected classification per order_id. |
| data/raw/ground_truth_settlements.json | JSON | 91 entries | Expected bank credit status per settlement_id. |

### Derived Pipeline Outputs

| File | Format | Entries | Description |
|------|--------|---------|-------------|
| engine/output/match_log.json | JSON | 591 | Deterministic matcher output: 500 order results + 91 batch results. |
| agent/output/explanations.json | JSON | 28 | LLM-narrated explanations for non-trivial cases only. |
| engine/output/reconciliation_report.json | JSON | 591 | Final classified report: simplified_status + merged explanation. |
| engine/output/metrics_report.json | JSON | 1 file | Accuracy scores, per-code precision/recall, mismatch list. |
| engine/output/metrics_report.md | Markdown | 1 file | Human-readable version of the above. |
| audit_trail.md | Markdown | 1 file | Chain-of-custody hashes, bug narrative, traced examples. |

### Schema: Source Files

**order_ledger.csv** (13 columns):

    order_id, order_date, customer_id, product_sku, quantity, gross_amount,
    currency, payment_method, payment_status, refund_status, refund_amount,
    created_at, notes

**settlement_report.csv** (13 columns):

    settlement_id, settlement_date, bank_utr, payment_id, order_id,
    gross_amount, fee, gst_on_fee, refund_deduction, net_amount,
    payment_method, captured_date, settlement_status

**bank_statement.csv** (7 columns):

    txn_date, txn_type, narration, utr, amount, balance_after, branch_code

---

## 2. Entity Trace

### Orders

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | data/raw/order_ledger.csv | generate_data.py |
| Loaded | engine/matcher_exact.py | preprocessor.load_csv -> ledger_by_id dict |
| Read by | order_matcher.py | match_orders() compares order to settlement rows |
| Read by | exceptions.py | check_consistency() cross-source date/label checks |
| Written to | engine/output/match_log.json | exceptions.compile_match_log() |
| Read by | engine/reconciler.py | main() adds simplified_status |
| Read by | agent/qa_agent.py | _load_data() for Q&A lookups |
| Read by | engine/metrics_scorer.py | main() compares against ground truth |

### Payments (payment_id)

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | data/raw/settlement_report.csv | generate_data.py |
| Used | order_matcher.py | Part of settlement row dict, not independently queried |

### Razorpay Settlements

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | data/raw/settlement_report.csv | generate_data.py |
| Loaded | engine/matcher_exact.py | preprocessor.load_csv -> settlement_by_id + settlement_by_sid |
| Read by | batch_matcher.py | match_batches() Layer 1: matches settlement_id to bank UTR |
| Read by | order_matcher.py | match_orders() Layer 2: matches order_id to settlement rows |
| Written to | engine/output/match_log.json | 91 batch result entries |

### Bank Transactions

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | data/raw/bank_statement.csv | generate_data.py |
| Loaded | engine/matcher_exact.py | bank_credits_by_utr dict (filtered to txn_type==credit) |
| Read by | batch_matcher.py | match_batches() compares bank amount to batch net |
| Read by | exceptions.py | check_consistency() T+1 timing check |
| Never written | -- | Read-only throughout |

### Refunds

| Step | Where | Module/Function |
|------|-------|-----------------|
| Represented as | refund_deduction column in settlement_report.csv (negative values) | -- |
| Also in | refund_status + refund_amount in order_ledger.csv | -- |
| Classified by | refund_classifier.py classify_refund() | FULL_REFUND / REFUND_SPLIT / PARTIAL_REFUND / REFUND_ONLY / none |
| Cross-checked by | order_matcher.py | UNRECORDED_REFUND: ledger claims refund but no settlement evidence |
| Output | match_log.json entries | refund_type, order_residual, expected_residual fields |

### Reconciliation Results

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | engine/output/reconciliation_report.json | engine/reconciler.py main() |
| Input | match_log.json (591) + explanations.json (28) | Read-only |
| Adds | simplified_status, key_figures, explanation passthrough | Pure read-merge-write |
| Read by | agent/qa_agent.py | Q&A grounding |
| Read by | engine/metrics_scorer.py | Comparison against ground truth |
| Read by | dashboard.py | UI display |

### Exceptions

| Step | Where | Module/Function |
|------|-------|-----------------|
| Generated by | order_matcher.py match_orders() | UNMATCHED_ORDER, DUPLICATE_ORDER, REFUND_SPLIT, CURRENCY_MISMATCH, UNRECORDED_REFUND |
| Generated by | batch_matcher.py match_batches() | NEFT_FAILED, NO_CREDIT_EXPECTED |
| Generated by | exceptions.py detect_ghost_transactions() | GHOST_TRANSACTION |
| Stored as | exception_code field in match_log.json entries | -- |
| Mapped to | simplified_status in reconciler.py map_status() | Reconciled / Needs Human Review / Unresolved |

### AI Explanations

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | agent/output/explanations.json | agent/explainer.py |
| Input | match_log.json (filtered to 28 cases) | Read-only |
| Process | Per-case Groq API call (openai/gpt-oss-120b) | Temperature=0, max_tokens=300 |
| Verified | _verify_figures() in explainer.py | Two-source hallucination check |
| Output | explanations.json | case_id, explanation, suggested_action, confidence_note, hallucination_check |
| Read by | reconciler.py | Passthrough to reconciliation_report.json |
| Read by | qa_agent.py | Injected into Q&A prompts as existing context |

### Audit Trail

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | audit_trail.md | engine/generate_audit.py |
| Input | ALL 9 pipeline files | Reads each, computes live SHA-256 |
| Output | Markdown with chain-of-custody table, bug narrative, 3 traced examples |
| Never read by code | -- | Pure documentation artifact |

### Metrics/Evaluation

| Step | Where | Module/Function |
|------|-------|-----------------|
| Created | engine/output/metrics_report.json + .md | engine/metrics_scorer.py |
| Input | reconciliation_report.json + ground_truth.json + ground_truth_settlements.json | Read-only |
| Computes | Per-code TP/FP/FN/TN, precision, recall, F1, FPR, FNR, 100% accuracy |
| Read by | dashboard.py | Metrics cards display |
| Read by | qa_agent.py | Aggregate Q&A grounding |

---

## 3. Data Flow Diagram

    data/raw/order_ledger.csv --------+
    data/raw/settlement_report.csv ---+
    data/raw/bank_statement.csv ------+
                                      |
                                      v
              +-------------------------------------------+
              |       engine/matcher_exact.py             |
              |       (thin orchestrator)                 |
              +----------+------------------+-------------+
                         |                  |
            +------------+                  +--------------+
            v                                             v
    preprocessor.py                            order_matcher.py
    (load CSVs,                                (Layer 2:
     FX conversion,    batch_matcher.py         order <-> settlement,
     label norm)       (Layer 1:                refund classification,
                        settlement <-> bank     duplicate/unrecorded
                        batch_net match)        detect)
            |                |                        |
            |                v                        |
            |        exceptions.py                    |
            |        (ghost detection,                |
            |         consistency soft flags)         |
            +----------------+------------------------+
                             |
                             v
              engine/output/match_log.json  (591 entries)
                             |
            +----------------+----------------------------+
            v                v                            v
    agent/explainer.py    engine/reconciler.py       dashboard.py
    (28 API calls         (add simplified_status,    (Case Explorer
     via Groq,             merge explanations,        reads directly
     hallucination         compute summary)            from match_log)
     check)                     |
            |                   |
            v                   v
    agent/output/         engine/output/
    explanations.json     reconciliation_report.json
    (28 entries)          (591 entries)
            |                   |
            +--------+----------+
                     |
            +--------+-------------------+
            v        v                   v
    engine/        engine/          dashboard.py
    metrics_       generate_        (Q&A section)
    scorer.py      audit.py         calls qa_agent.py
    (compare       (read all 9      answer_question()
     against GT,    files, live      -> Groq API call
     compute        SHA-256,         -> response
     accuracy)      write trail)     -> display
            |              |
            v              v
    engine/output/    audit_trail.md
    metrics_report.
    json + .md

---

## 4. Data Classification

| Category | Files | Mutability |
|----------|-------|------------|
| Source-of-truth financial data | order_ledger.csv, settlement_report.csv, bank_statement.csv | Frozen. Never modified after generation. |
| Ground truth | ground_truth.json, ground_truth_settlements.json | Updated once (audit fix), then frozen. |
| Derived data | match_log.json, reconciliation_report.json | Regenerated from frozen inputs. Stateless. |
| AI-generated data | explanations.json | Generated by LLM. Semantically non-deterministic. |
| Evaluation data | metrics_report.json, metrics_report.md | Derived from reconciliation + ground truth. Stateless. |
| Audit data | audit_trail.md | Documentation. Generated from all other files. |

---

## 5. Production Risks in Current Architecture

### Risk 1: Duplicate Records

The file-based system has 1 confirmed duplicate: ord_EnDJiS9HvlxNgbb1 appears twice in order_ledger.csv with different amounts. The code handles it (DUPLICATE_ORDER exception), but there is no structural prevention. In production, nothing stops a second ingestion of the same order.

What a database gives: UNIQUE(order_id) constraint or an idempotency key.

### Risk 2: Accidental Overwrites

Every pipeline phase rewrites its output file via a single json.dump, so a crash mid-write would lose the entire file. There is no write-ahead log or temp-file-then-rename pattern.

What a database gives: Transactional writes. BEGIN...COMMIT with rollback on failure.

### Risk 3: No Audit History of Derived Data

match_log.json, reconciliation_report.json, and metrics_report.json are overwritten on every re-run. If a bug is introduced and the pipeline is re-run, the previous correct output is silently destroyed.

What a database gives: Append-only audit log with run_id tracking which execution produced each row.

### Risk 4: Hash Instability Undermines Chain of Custody

reconciliation_report.json and metrics_report.json contain generated_at timestamps, so their SHA-256 changes on every re-run even with identical logic.

What a database gives: Content-addressed storage or deterministic snapshots (hash of data, not metadata).

### Risk 5: Concurrent Write Hazard

Nothing prevents running matcher_exact.py and reconciler.py simultaneously. The reconciler could see a half-written match_log.json.

What a database gives: Row-level locking. Readers never block writers.

### Risk 6: Loss of Explained Cases on Re-run

explanations.json is regenerated by calling the Groq API for all 28 cases. If the API is down mid-run, partially written output could overwrite the previous complete output.

What a database gives: Upsert with case-level granularity. Re-running only fills gaps.

---

## 6. What a Production Database Improves

| Current Problem | File-Based | PostgreSQL |
|----------------|-----------|------------|
| No referential integrity | order_id could reference nothing | Foreign keys enforce relationships |
| No concurrent safety | file corruption risk | ACID transactions |
| No idempotent ingestion | duplicate order silently inserted | UNIQUE constraint + upsert |
| No audit trail | overwrite = lose history | Append-only tables with run_id |
| No query capability | load entire JSON into memory | Indexed queries, aggregations |
| No access control | anyone with file access sees everything | Row-level security, roles |

---

## 7. Proposed PostgreSQL Schema

    -- Frozen source data (ingested once, immutable)

    CREATE TABLE orders (
        order_id        TEXT PRIMARY KEY,
        order_date      DATE NOT NULL,
        customer_id     TEXT NOT NULL,
        product_sku     TEXT,
        quantity        INT,
        gross_amount    NUMERIC(12,2),
        currency        VARCHAR(3),
        payment_method  TEXT,
        payment_status  TEXT,
        refund_status   TEXT,
        refund_amount   NUMERIC(12,2),
        created_at      TIMESTAMP,
        ingested_at     TIMESTAMP DEFAULT now()
    );

    CREATE TABLE settlements (
        id              SERIAL PRIMARY KEY,
        settlement_id   TEXT NOT NULL,
        settlement_date DATE,
        bank_utr        TEXT,
        payment_id      TEXT,
        order_id        TEXT REFERENCES orders(order_id),
        gross_amount    NUMERIC(12,2),
        fee             NUMERIC(12,2),
        gst_on_fee      NUMERIC(12,2),
        refund_deduction NUMERIC(12,2),
        net_amount      NUMERIC(12,2),
        payment_method  TEXT,
        captured_date   DATE,
        settlement_status TEXT
    );
    CREATE INDEX idx_settlements_order ON settlements(order_id);
    CREATE INDEX idx_settlements_sid ON settlements(settlement_id);
    CREATE INDEX idx_settlements_utr ON settlements(bank_utr);

    CREATE TABLE bank_transactions (
        txn_date    TIMESTAMP,
        txn_type    TEXT,
        narration   TEXT,
        utr         TEXT,
        amount      NUMERIC(12,2),
        balance_after NUMERIC(12,2),
        branch_code TEXT
    );
    CREATE INDEX idx_bank_utr ON bank_transactions(utr);

    -- Derived data (append-only with run tracking)

    CREATE TABLE reconciliation_runs (
        run_id          SERIAL PRIMARY KEY,
        run_at          TIMESTAMP DEFAULT now(),
        match_log_hash  TEXT,
        explanations_hash TEXT,
        report_hash     TEXT,
        metrics_hash    TEXT,
        total_orders    INT,
        total_settlements INT,
        overall_accuracy NUMERIC(6,4)
    );

    CREATE TABLE match_results (
        id              SERIAL PRIMARY KEY,
        run_id          INT REFERENCES reconciliation_runs(run_id),
        case_type       TEXT,
        case_id         TEXT NOT NULL,
        confidence      TEXT,
        exception_code  TEXT,
        detail          TEXT,
        soft_flags      JSONB,
        UNIQUE (run_id, case_id)
    );
    CREATE INDEX idx_match_run ON match_results(run_id);
    CREATE INDEX idx_match_exc ON match_results(exception_code);

    CREATE TABLE explanations (
        id              SERIAL PRIMARY KEY,
        run_id          INT REFERENCES reconciliation_runs(run_id),
        case_id         TEXT NOT NULL,
        case_type       TEXT,
        status          TEXT,
        exception_code  TEXT,
        explanation     TEXT,
        suggested_action TEXT,
        confidence_note TEXT,
        hallucination_verified BOOLEAN,
        hallucination_mismatches JSONB,
        model_used      TEXT,
        UNIQUE (run_id, case_id)
    );

    CREATE TABLE reconciliation_reports (
        id              SERIAL PRIMARY KEY,
        run_id          INT REFERENCES reconciliation_runs(run_id),
        case_type       TEXT,
        case_id         TEXT NOT NULL,
        simplified_status TEXT,
        exception_code  TEXT,
        explanation     TEXT,
        key_figures     JSONB,
        UNIQUE (run_id, case_id)
    );

    CREATE TABLE metrics (
        id              SERIAL PRIMARY KEY,
        run_id          INT REFERENCES reconciliation_runs(run_id) UNIQUE,
        overall_accuracy NUMERIC(6,4),
        order_accuracy  NUMERIC(6,4),
        settlement_accuracy NUMERIC(6,4),
        fpr             NUMERIC(6,4),
        fnr             NUMERIC(6,4),
        per_code_metrics JSONB,
        mismatches      JSONB
    );

    -- Ground truth (versioned, not overwritten)

    CREATE TABLE ground_truth (
        id              SERIAL PRIMARY KEY,
        version         INT NOT NULL,
        order_id        TEXT NOT NULL,
        expected_status TEXT,
        exception_code  TEXT,
        exception_detail TEXT,
        created_at      TIMESTAMP DEFAULT now(),
        UNIQUE (version, order_id)
    );

---

## 8. Five Most Important Observations

1. **The data model is already relational but stored as flat files.** Orders, settlements, and bank transactions have clear foreign keys (order_id, bank_utr, settlement_id) that are enforced in code but not structurally. The system is one step away from a proper schema.

2. **The pipeline is strictly sequential and stateless.** Each phase reads frozen inputs and overwrites a single output file. Easy to reason about, but impossible to audit which run produced which output without external tracking.

3. **Only 28 of 591 cases ever touch an LLM.** The architecture correctly isolates AI to narration, not classification. 95% of the pipeline is purely deterministic and reproducible -- a strong production property.

4. **The refund_deduction column does double duty.** It encodes both no refund (0.00) and actual refund amounts (negative values) in the same column. A production schema would separate refund events into their own table.

5. **The hallucination checker is now type-aware** (after the numeric fix), but the two-source verification is still fundamentally a figure-spot-check, not a semantic audit. It catches wrong numbers but not wrong relationships between numbers.

---

## 9. Three Biggest Production Risks

1. **No idempotent ingestion.** Running generate_data.py twice could double the dataset. A production system needs deduplication at the point of entry (natural key on order_id + payment_id), not downstream detection.

2. **File-based outputs are not crash-safe.** A crash during json.dump to match_log.json destroys the previous output. Production needs transactional writes or at least atomic rename (tmp -> final).

3. **No separation between what happened and what we decided.** match_log.json conflates raw financial facts (amounts, dates, IDs) with matcher decisions (confidence, exception_code). In production, these should be separate tables.

---

## 10. Recommended Production Architecture

    +----------------------------+
    |     Ingestion Layer        |
    |  (API/polling/file drop)   |
    +-----------+----------------+
                |
    +-----------v----------------+
    |   PostgreSQL + WAL         |
    |                            |
    |  orders (raw, immutable)   |
    |  settlements (raw)         |
    |  bank_transactions (raw)   |
    |  refunds (separate table)  |
    +-----------+----------------+
                |
    +-----------+---------------+-----------------+
    v                           v                 v
Deterministic              Audit Log         Ground Truth
Matcher                    (append-only)     (versioned)
(same logic as                |                 |
 current code)                |                 |
    |                         |                 |
    v                         |                 |
match_results ----------------+                 |
(per-run, idempotent)        |                  |
    |                         |                 |
    v                         |                 |
AI Narration Layer            |                 |
(only 28 cases)               |                 |
    |                         |                 |
    v                         |                 |
explanations -----------------+                 |
    |                                           |
    v                                           |
Metrics Scorer --------------------------------+
    |                         |
    v                         v
metrics              reconciliation_runs
(per-run)            (hash provenance)
    |
    v
Streamlit Dashboard
(reads views, not tables)

The core insight: **the current code logic is production-quality; the storage layer is the gap.** The matcher, refund classifier, and exception detector do not need rewriting -- they need a database underneath them instead of JSON files.

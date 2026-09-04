# Testing

## How to Run

```bash
# Run all tests (stdlib unittest, no extra dependencies)
python -m unittest tests.test_reconciliation -v

# Or run a single test class
python -m unittest tests.test_reconciliation.TestBatchMatchSuccess -v
```

No external test framework (pytest, etc.) is required — the suite uses Python's built-in `unittest`.

## What the Tests Cover

**64 tests** across 14 test classes, covering the critical financial-reconciliation paths:

| # | Test Class | Type | Tests | What It Tests |
|---|-----------|------|-------|---------------|
| 1 | `TestBatchMatchSuccess` | Unit | 3 | `match_batches()` matches settlement to bank credit (exact, tolerance, mismatch) |
| 2 | `TestBatchMatchMismatch` | Unit | 2 | Missing bank credit → `batch_neft_failed`; negative net → matched (not mislabeled as exception) |
| 3 | `TestCurrencyFx` | Unit | 4 | USD→INR conversion at FX_RATE=83.00, label normalization, `CURRENCY_MISMATCH` classification |
| 4 | `TestRefundFull` | Unit | 2 | `classify_refund()` detects FULL_REFUND with correct `expected_residual = -(fee+gst)` |
| 5 | `TestRefundSplit` | Unit | 2 | `classify_refund()` detects REFUND_SPLIT (cross-batch) and PARTIAL_REFUND (same-batch) |
| 6 | `TestOrderMatchingExceptions` | Unit | 4 | `match_orders()` produces UNMATCHED_ORDER, DUPLICATE_ORDER, UNRECORDED_REFUND for known edge cases |
| 7 | `TestStatusMapping` | Unit | 1 | Phase 4 `map_status()` maps confidence + exception_code to correct simplified labels |
| 8 | `TestQAAgentParsing` | Unit | 13 | QA agent question classification, numeric-aware hallucination checker, structured fallback output (no live API call) |
| 9 | `TestExplanationValidation` | Unit | 11 | `validate_explanation()` — empty/None/whitespace rejection, valid 2-4 sentence acceptance, overlong rejection, error handling |
| 10 | `TestEndToEndPipeline` | Integration | 7 | Full matcher on frozen dataset: 500 orders + 91 settlements, field completeness, determinism, reconciliation report validation |
| 11 | `TestGhostTransaction` | Unit | 1 | `detect_ghost_transactions()` flags unknown order_ids as `needs_review` |
| 12 | `TestMetricsScorer` | Unit | 7 | `safe_div()`, `compute_per_code()` precision/recall/F1, completeness assertions |
| 13 | `TestFigureVerificationFailClosed` | Unit | 5 | Bare (marker-less) amounts are extracted and verified; verification fails closed when decimal figures cannot be extracted |
| 14 | `TestMatcherOutputReproducibility` | Integration | 2 | Fresh deterministic matcher run hash-matches the committed `match_log.json` (reproducibility pin); committed entry counts |

## Unit vs Integration

- **Unit tests (Classes 1-9, 11-13):** Test individual functions with small controlled fixtures. Fast, deterministic, no file I/O against real data.
- **Integration tests (Classes 10, 14):** Run the full deterministic matcher against the frozen `data/raw/` CSV files. Verifies the complete pipeline produces correct counts without errors, and (Class 14) pins the fresh output to the committed `match_log.json` hash.

## External API Calls

**Intentionally excluded.** The QA agent tests (`TestQAAgentParsing`) and explanation tests (`TestExplanationValidation`) verify parsing, classification, validation, and hallucination-check logic only — they do NOT call the Groq API. This keeps the test suite:

- Fast (~0.1s total)
- Deterministic (no LLM variance)
- Offline (no network dependency)
- Free (no API cost per run)

## Full Dataset Regression

The `TestEndToEndPipeline` class (7 tests) exercises the real matcher against the full synthetic dataset (500 orders, 91 settlements):

- **Record counts** are correct (no loss, no duplication)
- **Field completeness** — every result has all required fields
- **Determinism** — running twice on the same input produces identical confidence for every order
- **Reconciliation report** — existing `reconciliation_report.json` loads and has correct structure
- **Reproducibility pin** — `TestMatcherOutputReproducibility` serializes a fresh in-memory matcher run exactly as `compile_match_log` does and asserts its SHA-256 equals the committed `match_log.json` hash. Any engine change that alters results now fails the suite instead of silently drifting from the audit trail.

## Regression Fixes Locked In By This Suite

1. **Hallucination checker numeric comparison** — `_verify_figures()` originally used bidirectional substring matching, so a stated figure like "2" could falsely verify against an unrelated amount like 47500.00 (the "2" inside it). This was fixed to compare numeric values with `math.isclose(..., abs_tol=0.01)` for numeric strings, falling back to exact string equality for non-numeric tokens (IDs, dates). Regression tests assert: `2` must NOT match `47500.00`, `18` must NOT match `1180.00`, `83` must NOT match `8383.00` — while `83` correctly matches `83.00`. The fix is applied in both `agent/explainer.py` and `agent/qa_agent.py`.

2. **Explanation validation / fail-safe failure states** — empty, whitespace-only, or malformed LLM responses are rejected with an explicit failure state instead of being stored as valid explanations; overlong responses beyond the 2-4 sentence spec are flagged. Covered by `TestExplanationValidation`.

3. **Fail-closed figure verification** — bare amounts without a currency marker (e.g. "residual of 2196.99") are now extracted and checked like any other figure; an explanation whose decimal figures cannot be extracted is recorded as unverified, never as a silent pass. Covered by `TestFigureVerificationFailClosed`.

## Files

| File | Purpose |
|------|---------|
| `tests/__init__.py` | Makes `tests/` a Python package |
| `tests/test_reconciliation.py` | The 57-test regression suite |
| `TESTING.md` | This file |

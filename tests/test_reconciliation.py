#!/usr/bin/env python3
"""
Automated regression tests for the deterministic reconciliation engine.

Covers:
  1. Batch matching — success and mismatch paths
  2. Currency/FX conversion
  3. Refund classification (FULL_REFUND, REFUND_SPLIT, none)
  4. Order-level matching (success, UNMATCHED_ORDER, DUPLICATE_ORDER, UNRECORDED_REFUND)
  5. Status mapping (Phase 4)
  6. QA agent question classification and hallucination check
  7. End-to-end pipeline on frozen full dataset
  8. Data-integrity regression (no record loss/duplication)

Run:  python -m pytest tests/test_reconciliation.py -v
      or:  python tests/test_reconciliation.py
"""

import json
import os
import sys
import hashlib
import tempfile
import unittest
from pathlib import Path

# ── Make engine/ and agent/ importable ──────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = str(PROJECT_ROOT / "engine")
AGENT_DIR = str(PROJECT_ROOT / "agent")
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, AGENT_DIR)

from preprocessor import (
    FX_RATE, to_float, normalize_label, ledger_gross_inr, LABEL_ALIAS,
)
from batch_matcher import match_batches
from order_matcher import match_orders
from refund_classifier import classify_refund
from exceptions import (
    detect_ghost_transactions,
    check_consistency,
    compile_match_log,
)


# ═══════════════════════════════════════════════════════════════════════
#  FIXTURE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _settlement_row(**overrides):
    """Build a minimal settlement row dict with sensible defaults."""
    base = {
        "payment_method": "card",
        "gross_amount": "1000.00",
        "fee": "20.00",
        "gst_on_fee": "3.60",
        "refund_deduction": "0.00",
        "net_amount": "976.40",
        "settlement_id": "set_test001",
        "settlement_date": "2025-08-20",
        "bank_utr": "UTR001",
        "order_id": "ord_test001",
        "captured_date": "2025-08-19",
    }
    base.update(overrides)
    return base


def _bank_row(**overrides):
    """Build a minimal bank credit row dict."""
    base = {
        "utr": "UTR001",
        "amount": "976.40",
        "txn_type": "credit",
        "txn_date": "2025-08-21 10:00:00",
        "narration": "Razorpay settlement",
    }
    base.update(overrides)
    return base


def _ledger_row(**overrides):
    """Build a minimal ledger row dict."""
    base = {
        "order_id": "ord_test001",
        "gross_amount": "1000.00",
        "currency": "INR",
        "payment_method": "visa_mc_domestic",
        "payment_status": "captured",
        "refund_status": "none",
        "refund_amount": "0",
        "customer_id": "cust_001",
        "quantity": "1",
        "created_at": "2025-08-19 12:00:00",
        "order_date": "2025-08-19",
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════
#  TEST 1: BATCH MATCHING — SUCCESS
# ═══════════════════════════════════════════════════════════════════════

class TestBatchMatchSuccess(unittest.TestCase):
    """A settlement batch should match its bank credit when amounts agree."""

    def test_exact_match_produces_batch_credited(self):
        settlement_row = _settlement_row(net_amount="976.40")
        bank_row = _bank_row(amount="976.40")

        settlement_by_sid = {"set_test001": [settlement_row]}
        bank_credits_by_utr = {"UTR001": [bank_row]}
        ledger_ids = {"ord_test001"}

        results = match_batches(settlement_by_sid, bank_credits_by_utr, ledger_ids)
        r = results["set_test001"]

        self.assertEqual(r["status"], "batch_credited")
        self.assertEqual(r["confidence"], "matched")
        self.assertEqual(r["bank_amount"], 976.40)
        self.assertAlmostEqual(r["diff"], 0.0, places=2)

    def test_within_tolerance_match(self):
        """Amount diff of 0.04 (below BATCH_TOLERANCE=0.05) should still match."""
        settlement_row = _settlement_row(net_amount="976.40")
        bank_row = _bank_row(amount="976.44")  # diff = 0.04

        results = match_batches(
            {"set_t": [settlement_row]},
            {"UTR001": [bank_row]},
            {"ord_test001"},
        )
        r = results["set_t"]
        self.assertEqual(r["status"], "batch_credited")
        self.assertEqual(r["confidence"], "matched")

    def test_large_mismatch_produces_neft_failed(self):
        """Amount diff >= 1.00 should produce batch_neft_failed."""
        settlement_row = _settlement_row(net_amount="976.40")
        bank_row = _bank_row(amount="900.00")  # diff = 76.40

        results = match_batches(
            {"set_t": [settlement_row]},
            {"UTR001": [bank_row]},
            {"ord_test001"},
        )
        r = results["set_t"]
        self.assertEqual(r["status"], "batch_neft_failed")
        self.assertEqual(r["confidence"], "hard_exception")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 2: BATCH MATCHING — MISMATCH
# ═══════════════════════════════════════════════════════════════════════

class TestBatchMatchMismatch(unittest.TestCase):
    """A settlement with no matching bank credit should fail, not match cleanly."""

    def test_no_bank_credit_positive_net_produces_neft_failed(self):
        settlement_row = _settlement_row(net_amount="976.40")

        results = match_batches(
            {"set_t": [settlement_row]},
            {},   # no bank credits at all
            {"ord_test001"},
        )
        r = results["set_t"]
        self.assertEqual(r["status"], "batch_neft_failed")
        self.assertEqual(r["confidence"], "hard_exception")
        self.assertIsNone(r["bank_amount"])

    def test_negative_net_batch_is_matched_no_credit(self):
        """Negative net (refund-heavy batch) correctly marked as matched, not exception."""
        settlement_row = _settlement_row(net_amount="-500.00")

        results = match_batches(
            {"set_t": [settlement_row]},
            {},
            {"ord_test001"},
        )
        r = results["set_t"]
        self.assertEqual(r["status"], "batch_no_credit")
        self.assertEqual(r["confidence"], "matched")  # CLAIM 4 FIX


# ═══════════════════════════════════════════════════════════════════════
#  TEST 3: CURRENCY / FX CONVERSION
# ═══════════════════════════════════════════════════════════════════════

class TestCurrencyFx(unittest.TestCase):
    """USD orders should convert at FX_RATE=83.00 for settlement comparison."""

    def test_usd_to_inr_conversion(self):
        row = _ledger_row(currency="USD", gross_amount="120.50")
        inr = ledger_gross_inr(row)
        expected = round(120.50 * FX_RATE, 2)
        self.assertEqual(inr, expected)
        self.assertEqual(FX_RATE, 83.00)

    def test_inr_order_unchanged(self):
        row = _ledger_row(currency="INR", gross_amount="1000.00")
        self.assertEqual(ledger_gross_inr(row), 1000.00)

    def test_label_normalization(self):
        self.assertEqual(normalize_label("visa_mc_domestic"), "card")
        self.assertEqual(normalize_label("international_card"), "intl_card")
        self.assertEqual(normalize_label("amex_diners"), "amex")
        self.assertEqual(normalize_label("upi"), "upi")
        # Unknown label passes through unchanged
        self.assertEqual(normalize_label("netbanking"), "netbanking")

    def test_usd_order_classified_as_currency_mismatch(self):
        """USD orders matched against INR settlement should produce CURRENCY_MISMATCH."""
        ledger = [_ledger_row(
            order_id="ord_usd001",
            currency="USD",
            gross_amount="12.00",  # 12 * 83 = 996.00 INR
            payment_method="international_card",
        )]
        settlement = [_settlement_row(
            order_id="ord_usd001",
            gross_amount="996.00",  # matches converted amount
            payment_method="intl_card",
        )]
        ledger_by_id = {"ord_usd001": ledger}
        settlement_by_id = {"ord_usd001": settlement}

        results = match_orders(ledger_by_id, settlement_by_id)
        r = results["ord_usd001"]
        self.assertEqual(r["confidence"], "matched_with_note")
        self.assertEqual(r["exception_code"], "CURRENCY_MISMATCH")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 4: REFUND CLASSIFICATION — FULL REFUND
# ═══════════════════════════════════════════════════════════════════════

class TestRefundFull(unittest.TestCase):
    """A full refund should classify as FULL_REFUND with expected_residual = -(fee+gst)."""

    def test_full_refund_detected(self):
        rows = [
            _settlement_row(gross_amount="1000.00", refund_deduction="0.00",
                             net_amount="976.40", settlement_id="set_A"),
            _settlement_row(gross_amount="0.00", refund_deduction="-1000.00",
                             net_amount="-1000.00", settlement_id="set_B"),
        ]
        result = classify_refund(rows, ledger_gross=1000.00)
        self.assertEqual(result["refund_type"], "FULL_REFUND")
        expected_res = round(-(20.00 + 3.60), 2)  # -(fee + gst)
        self.assertEqual(result["expected_residual"], expected_res)

    def test_no_refund_rows_gives_none(self):
        rows = [_settlement_row(refund_deduction="0.00")]
        result = classify_refund(rows, ledger_gross=1000.00)
        self.assertEqual(result["refund_type"], "none")
        self.assertIsNone(result["expected_residual"])


# ═══════════════════════════════════════════════════════════════════════
#  TEST 5: REFUND CLASSIFICATION — REFUND SPLIT
# ═══════════════════════════════════════════════════════════════════════

class TestRefundSplit(unittest.TestCase):
    """A partial refund in a different settlement batch = REFUND_SPLIT."""

    def test_split_refund_cross_batch(self):
        rows = [
            _settlement_row(gross_amount="1000.00", refund_deduction="0.00",
                             net_amount="976.40", settlement_id="set_A"),
            _settlement_row(gross_amount="0.00", refund_deduction="-500.00",
                             net_amount="-500.00", settlement_id="set_B"),
        ]
        result = classify_refund(rows, ledger_gross=1000.00)
        self.assertEqual(result["refund_type"], "REFUND_SPLIT")
        # expected_residual = orig_gross - refund_amount = 1000 - 500 = 500
        self.assertEqual(result["expected_residual"], 500.00)

    def test_partial_refund_same_batch(self):
        """Partial refund in same settlement = PARTIAL_REFUND."""
        rows = [
            _settlement_row(gross_amount="1000.00", refund_deduction="0.00",
                             net_amount="976.40", settlement_id="set_A"),
            _settlement_row(gross_amount="0.00", refund_deduction="-400.00",
                             net_amount="-400.00", settlement_id="set_A"),
        ]
        result = classify_refund(rows, ledger_gross=1000.00)
        self.assertEqual(result["refund_type"], "PARTIAL_REFUND")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 6: ORDER MATCHING — UNMATCHED & DUPLICATE
# ═══════════════════════════════════════════════════════════════════════

class TestOrderMatchingExceptions(unittest.TestCase):
    """Verify that order-level exceptions are correctly produced."""

    def test_captured_order_no_settlement_is_unmatched(self):
        ledger = [_ledger_row(payment_status="captured")]
        results = match_orders({"ord_t": ledger}, {})
        r = results["ord_t"]
        self.assertEqual(r["exception_code"], "UNMATCHED_ORDER")
        self.assertEqual(r["confidence"], "hard_exception")

    def test_failed_order_no_settlement_is_unmatched(self):
        ledger = [_ledger_row(payment_status="failed")]
        results = match_orders({"ord_t": ledger}, {})
        r = results["ord_t"]
        self.assertEqual(r["exception_code"], "UNMATCHED_ORDER")
        self.assertEqual(r["confidence"], "hard_exception")

    def test_duplicate_order_detected(self):
        ledger = [
            _ledger_row(order_id="ord_dup", gross_amount="1000.00"),
            _ledger_row(order_id="ord_dup", gross_amount="2000.00"),
        ]
        settlement = [_settlement_row(order_id="ord_dup")]
        results = match_orders({"ord_dup": ledger}, {"ord_dup": settlement})
        r = results["ord_dup"]
        self.assertEqual(r["exception_code"], "DUPLICATE_ORDER")
        self.assertEqual(r["confidence"], "needs_review")
        self.assertIn("conflicting_ledger_rows", r)
        self.assertEqual(len(r["conflicting_ledger_rows"]), 2)

    def test_unrecorded_refund_detected(self):
        """Ledger claims refund but settlement has no refund_deduction row."""
        ledger = [_ledger_row(
            refund_status="partial",
            refund_amount="500",
            gross_amount="1000.00",
        )]
        settlement = [_settlement_row(
            refund_deduction="0.00",  # no refund evidence
        )]
        results = match_orders({"ord_t": ledger}, {"ord_t": settlement})
        r = results["ord_t"]
        self.assertEqual(r["exception_code"], "UNRECORDED_REFUND")
        self.assertEqual(r["confidence"], "needs_review")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 7: PHASE 4 STATUS MAPPING
# ═══════════════════════════════════════════════════════════════════════

class TestStatusMapping(unittest.TestCase):
    """Verify map_status() produces correct simplified labels."""

    def test_import_map_status(self):
        from reconciler import map_status

        self.assertEqual(map_status("matched", None), "Reconciled")
        self.assertEqual(map_status("matched", "NO_CREDIT_EXPECTED"),
                         "Reconciled (no credit due)")
        self.assertEqual(map_status("matched_with_note", "CURRENCY_MISMATCH"),
                         "Reconciled (with note)")
        self.assertEqual(map_status("needs_review", "DUPLICATE_ORDER"),
                         "Needs Human Review")
        self.assertEqual(map_status("hard_exception", "UNMATCHED_ORDER"),
                         "Unresolved")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 8: QA AGENT — CLASSIFY QUESTION + HALLUCINATION CHECK
# ═══════════════════════════════════════════════════════════════════════

class TestQAAgentParsing(unittest.TestCase):
    """Test classification and verification logic WITHOUT live API calls."""

    def test_classify_single_case_order(self):
        from qa_agent import classify_question
        cat, om, sm = classify_question("Why does ord_ABC123 need review?")
        self.assertEqual(cat, "single_case")
        self.assertIsNotNone(om)
        self.assertIsNone(sm)

    def test_classify_single_case_settlement(self):
        from qa_agent import classify_question
        cat, om, sm = classify_question("What is set_XYZ789 status?")
        self.assertEqual(cat, "single_case")
        self.assertIsNone(om)
        self.assertIsNotNone(sm)

    def test_classify_aggregate(self):
        from qa_agent import classify_question
        cat, om, sm = classify_question("How many orders need review?")
        self.assertEqual(cat, "aggregate")

    def test_classify_out_of_scope_future(self):
        from qa_agent import classify_question
        cat, om, sm = classify_question("What will next month's rate be?")
        self.assertEqual(cat, "out_of_scope")

    def test_verify_figures_passes_correct_numbers(self):
        from qa_agent import _verify_figures
        text = "The order settled for 976.40 on 2025-08-20."
        source = json.dumps({"net_amount": "976.40", "settlement_date": "2025-08-20"})
        result = _verify_figures(text, source)
        self.assertTrue(result["verified"])
        self.assertEqual(result["mismatches"], [])

    def test_verify_figures_flags_wrong_number(self):
        from qa_agent import _verify_figures
        text = "The order settled for 47500.00."
        source = json.dumps({"net_amount": "976.40"})
        result = _verify_figures(text, source)
        self.assertFalse(result["verified"])
        self.assertIn("47500.00", result["mismatches"])

    def test_numeric_2_does_not_match_47500(self):
        """Domain figure '2' must not false-positive match 47500.00."""
        from qa_agent import _verify_figures
        text = "The batch contained 47500.00."
        source = json.dumps({"amount": "2.00"})  # source has '2'
        result = _verify_figures(text, source)
        self.assertFalse(result["verified"])
        self.assertIn("47500.00", result["mismatches"])

    def test_numeric_18_does_not_match_1180(self):
        """Domain figure '18' must not false-positive match 1180.00."""
        from qa_agent import _verify_figures
        text = "The fee was 1180.00."
        source = json.dumps({"gst": "18.00"})
        result = _verify_figures(text, source)
        self.assertFalse(result["verified"])
        self.assertIn("1180.00", result["mismatches"])

    def test_numeric_83_does_not_match_8383(self):
        """Domain figure '83' must not false-positive match 8383.00."""
        from qa_agent import _verify_figures
        text = "The amount was 8383.00."
        source = json.dumps({"fx_rate": "83.00"})
        result = _verify_figures(text, source)
        self.assertFalse(result["verified"])
        self.assertIn("8383.00", result["mismatches"])

    def test_numeric_83_matches_83_00(self):
        """83 and 83.00 are financially equivalent within tolerance."""
        from qa_agent import _verify_figures
        text = "The FX rate is 83."
        source = json.dumps({"fx_rate": "83.00"})
        result = _verify_figures(text, source)
        self.assertTrue(result["verified"])
        self.assertEqual(result["mismatches"], [])

    def test_exact_match_47500(self):
        """47500.00 in source should pass when AI states 47500.00."""
        from qa_agent import _verify_figures
        text = "The total was 47500.00."
        source = json.dumps({"total": "47500.00"})
        result = _verify_figures(text, source)
        self.assertTrue(result["verified"])
        self.assertEqual(result["mismatches"], [])

    def test_answer_question_returns_structured_output(self):
        """Out-of-scope questions return structured fallback without API call."""
        from qa_agent import answer_question
        result = answer_question("What will the match rate be next month?")
        self.assertIn("answer", result)
        self.assertIn("source_case_ids", result)
        self.assertIn("verified", result)
        self.assertIn("category", result)
        self.assertIn("fallback_reason", result)
        self.assertEqual(result["category"], "out_of_scope")
        self.assertEqual(result["fallback_reason"], "future_prediction")
        self.assertTrue(result["verified"])

    def test_answer_question_fake_case_id(self):
        from qa_agent import answer_question
        result = answer_question("What is the status of ord_FAKE12345?")
        self.assertEqual(result["category"], "out_of_scope")
        self.assertEqual(result["fallback_reason"], "case_id_not_found")


# ═══════════════════════════════════════════════════════════════════════
#  TEST 9: END-TO-END PIPELINE — FULL DATASET
# ═══════════════════════════════════════════════════════════════════════

class TestExplanationValidation(unittest.TestCase):
    """Tests for validate_explanation() — output validation on LLM responses."""

    def test_empty_string_rejected(self):
        from explainer import validate_explanation
        r = validate_explanation("")
        self.assertFalse(r["is_valid"])
        self.assertEqual(r["reason"], "empty_response")
        self.assertEqual(r["sentence_count"], 0)

    def test_whitespace_only_rejected(self):
        from explainer import validate_explanation
        r = validate_explanation("   \n  \t  ")
        self.assertFalse(r["is_valid"])
        self.assertEqual(r["reason"], "empty_response")
        self.assertEqual(r["sentence_count"], 0)

    def test_none_rejected(self):
        from explainer import validate_explanation
        r = validate_explanation(None)
        self.assertFalse(r["is_valid"])
        self.assertEqual(r["reason"], "empty_response")

    def test_valid_two_sentences(self):
        from explainer import validate_explanation
        text = "Order ord_ABC123 was captured for 1000.00 INR. The settlement set_XYZ matched with a net of 976.40 INR."
        r = validate_explanation(text)
        self.assertTrue(r["is_valid"])
        self.assertIsNone(r["reason"])
        self.assertEqual(r["sentence_count"], 2)

    def test_valid_three_sentences(self):
        from explainer import validate_explanation
        text = "This is sentence one. This is sentence two. This is sentence three."
        r = validate_explanation(text)
        self.assertTrue(r["is_valid"])
        self.assertEqual(r["sentence_count"], 3)

    def test_valid_four_sentences(self):
        from explainer import validate_explanation
        text = "First sentence here. Second sentence. Third sentence. Fourth sentence."
        r = validate_explanation(text)
        self.assertTrue(r["is_valid"])
        self.assertEqual(r["sentence_count"], 4)

    def test_single_sentence_rejected(self):
        from explainer import validate_explanation
        text = "This is just one sentence without a second."
        r = validate_explanation(text)
        self.assertFalse(r["is_valid"])
        self.assertEqual(r["reason"], "too_short")
        self.assertEqual(r["sentence_count"], 1)

    def test_overlong_response_flagged(self):
        from explainer import validate_explanation
        text = ". ".join(["Sentence " + str(i) + "." for i in range(8)])
        r = validate_explanation(text)
        self.assertFalse(r["is_valid"])
        self.assertEqual(r["reason"], "too_long")
        self.assertGreater(r["sentence_count"], 6)

    def test_financial_abbreviations_not_rejected(self):
        """Decimal numbers and IDs should not break sentence counting."""
        from explainer import validate_explanation
        text = ("Order ord_0EE1Z6jjCFojTQpT was an international card payment of 424.24 USD. "
                "The settlement set_Ug9C5dqtELc0MalO settled for 35,211.92 INR with a net of 33,965.42 INR.")
        r = validate_explanation(text)
        self.assertTrue(r["is_valid"])
        self.assertEqual(r["sentence_count"], 2)

    def test_error_string_not_validated(self):
        """An API error string should be treated as invalid by the pipeline."""
        from explainer import validate_explanation
        # API error strings are typically short single phrases
        text = "ERROR: Rate limit exceeded after retries"
        r = validate_explanation(text)
        # Single sentence -> too_short, which is correct for pipeline handling
        self.assertFalse(r["is_valid"])

    def test_validation_result_has_expected_fields(self):
        from explainer import validate_explanation
        r = validate_explanation("Sentence one. Sentence two.")
        self.assertIn("is_valid", r)
        self.assertIn("reason", r)
        self.assertIn("sentence_count", r)
        self.assertIsInstance(r["is_valid"], bool)
        self.assertIsInstance(r["sentence_count"], int)


class TestEndToEndPipeline(unittest.TestCase):
    """
    Integration test: run the full deterministic matcher on frozen
    data/raw/ and verify output artifact counts and content.
    Does NOT call the LLM (Phase 3) or write new outputs.
    """

    @classmethod
    def setUpClass(cls):
        """Run the matcher once; reuse results across all test methods."""
        # Import the matcher orchestrator's core logic by re-running it
        # We import the components directly to avoid side effects
        from preprocessor import load_csv
        from collections import defaultdict

        ledger = load_csv("order_ledger.csv")
        settlement = load_csv("settlement_report.csv")
        bank = load_csv("bank_statement.csv")

        # Build indices (mirrors matcher_exact.py)
        ledger_by_id = {}
        for row in ledger:
            oid = row["order_id"]
            if oid not in ledger_by_id:
                ledger_by_id[oid] = []
            ledger_by_id[oid].append(row)

        settlement_by_id = defaultdict(list)
        for row in settlement:
            settlement_by_id[row["order_id"]].append(row)

        settlement_by_sid = defaultdict(list)
        for row in settlement:
            settlement_by_sid[row["settlement_id"]].append(row)

        bank_credits_by_utr = defaultdict(list)
        for row in bank:
            if row["txn_type"] == "credit":
                bank_credits_by_utr[row["utr"]].append(row)

        ledger_ids = set(ledger_by_id.keys())

        # Run Layer 1
        batch_results = match_batches(settlement_by_sid, bank_credits_by_utr, ledger_ids)

        # Run Layer 2
        order_results = match_orders(ledger_by_id, settlement_by_id)

        # Ghost detection
        detect_ghost_transactions(batch_results)

        cls.ledger = ledger
        cls.settlement = settlement
        cls.bank = bank
        cls.order_results = order_results
        cls.batch_results = batch_results
        cls.ledger_by_id = ledger_by_id
        cls.settlement_by_id = settlement_by_id

    def test_order_count(self):
        self.assertEqual(len(self.order_results), 500,
                         "Expected 500 unique order_ids in results")

    def test_settlement_count(self):
        self.assertEqual(len(self.batch_results), 91,
                         "Expected 91 settlement batches in results")

    def test_all_orders_have_required_fields(self):
        required = {"order_id", "confidence", "exception_code", "match_status",
                     "settlement_ids", "soft_flags"}
        for oid, r in self.order_results.items():
            missing = required - set(r.keys())
            self.assertEqual(missing, set(), f"Order {oid} missing fields: {missing}")

    def test_all_settlements_have_required_fields(self):
        required = {"settlement_id", "confidence", "status", "batch_net",
                     "row_count", "ghost_order_ids", "soft_flags"}
        for sid, r in self.batch_results.items():
            missing = required - set(r.keys())
            self.assertEqual(missing, set(), f"Settlement {sid} missing fields: {missing}")

    def test_confidence_distribution_plausible(self):
        """Sanity check: all 500 orders classified, no null confidence."""
        from collections import Counter
        conf_counts = Counter(r["confidence"] for r in self.order_results.values())
        total = sum(conf_counts.values())
        self.assertEqual(total, 500)
        # All 4 statuses should be present
        for key in ("matched", "matched_with_note", "needs_review", "hard_exception"):
            self.assertIn(key, conf_counts, f"Missing confidence key: {key}")

    def test_no_record_loss_or_duplication(self):
        """Processing the same input twice produces identical counts."""
        from collections import defaultdict
        from preprocessor import load_csv

        ledger2 = load_csv("order_ledger.csv")
        settlement2 = load_csv("settlement_report.csv")
        bank2 = load_csv("bank_statement.csv")

        ledger_by_id2 = {}
        for row in ledger2:
            oid = row["order_id"]
            if oid not in ledger_by_id2:
                ledger_by_id2[oid] = []
            ledger_by_id2[oid].append(row)

        settlement_by_id2 = defaultdict(list)
        for row in settlement2:
            settlement_by_id2[row["order_id"]].append(row)

        settlement_by_sid2 = defaultdict(list)
        for row in settlement2:
            settlement_by_sid2[row["settlement_id"]].append(row)

        bank_credits_by_utr2 = defaultdict(list)
        for row in bank2:
            if row["txn_type"] == "credit":
                bank_credits_by_utr2[row["utr"]].append(row)

        ledger_ids2 = set(ledger_by_id2.keys())

        batch2 = match_batches(settlement_by_sid2, bank_credits_by_utr2, ledger_ids2)
        order2 = match_orders(ledger_by_id2, settlement_by_id2)

        self.assertEqual(len(order2), len(self.order_results))
        self.assertEqual(len(batch2), len(self.batch_results))

        # Every order_id should get same confidence both times
        for oid in self.order_results:
            self.assertEqual(
                self.order_results[oid]["confidence"],
                order2[oid]["confidence"],
                f"Non-deterministic result for {oid}",
            )

    def test_reconciled_output_loads_correctly(self):
        """Verify the existing reconciliation_report.json is well-formed."""
        rr_path = PROJECT_ROOT / "engine" / "output" / "reconciliation_report.json"
        if not rr_path.exists():
            self.skipTest("reconciliation_report.json not found")
        with open(rr_path, encoding="utf-8") as f:
            rr = json.load(f)
        self.assertIn("orders", rr)
        self.assertIn("settlements", rr)
        self.assertIn("summary", rr)
        self.assertEqual(len(rr["orders"]), 500)
        self.assertEqual(len(rr["settlements"]), 91)


# ═══════════════════════════════════════════════════════════════════════
#  TEST 10: GHOST TRANSACTION DETECTION
# ═══════════════════════════════════════════════════════════════════════

class TestGhostTransaction(unittest.TestCase):
    """Settlement rows with order_ids not in the ledger should be flagged."""

    def test_ghost_order_marks_batch_needs_review(self):
        settlement_row = _settlement_row(order_id="ord_ghost999",
                                          net_amount="500.00")
        batch_results = {
            "set_t": {
                "settlement_id": "set_t",
                "ghost_order_ids": ["ord_ghost999"],
                "confidence": "matched",
                "soft_flags": [],
            }
        }
        detect_ghost_transactions(batch_results)
        r = batch_results["set_t"]
        self.assertEqual(r["confidence"], "needs_review")
        self.assertTrue(any("Ghost" in f for f in r["soft_flags"]))



# ==============================================================
#  TEST 11: METRICS SCORER (safe_div, compute_per_code, assertions)
# ==============================================================

from metrics_scorer import safe_div, compute_per_code

class TestMetricsScorer(unittest.TestCase):
    """Unit tests for engine/metrics_scorer.py helper functions."""

    def test_safe_div_normal(self):
        """safe_div returns correct quotient for positive integers."""
        self.assertAlmostEqual(safe_div(10, 2), 5.0)

    def test_safe_div_zero_denominator(self):
        """safe_div returns 0.0 when denominator is zero, no ZeroDivisionError."""
        self.assertEqual(safe_div(10, 0), 0.0)

    def test_safe_div_zero_numerator(self):
        """safe_div returns 0.0 when numerator is zero."""
        self.assertEqual(safe_div(0, 5), 0.0)

    def test_compute_per_code_perfect_classification(self):
        """When gt and rr always agree, precision/recall/F1 all equal 1.0 for each class."""
        results = [
            {"gt": "A", "rr": "A"},
            {"gt": "A", "rr": "A"},
            {"gt": "B", "rr": "B"},
            {"gt": "B", "rr": "B"},
        ]
        pc = compute_per_code(results, ["A", "B"])
        for code in ["A", "B"]:
            self.assertEqual(pc[code]["precision"], 1.0, f"{code} precision should be 1.0")
            self.assertEqual(pc[code]["recall"], 1.0, f"{code} recall should be 1.0")
            self.assertEqual(pc[code]["f1"], 1.0, f"{code} F1 should be 1.0")

    def test_compute_per_code_known_mismatch(self):
        """One false positive for code A: gt says B but rr says A."""
        results = [
            {"gt": "A", "rr": "A"},  # TP for A
            {"gt": "B", "rr": "A"},  # FP for A, FN for B
            {"gt": "B", "rr": "B"},  # TP for B
        ]
        pc = compute_per_code(results, ["A", "B"])
        # A: TP=1, FP=1, FN=0 -> precision=0.5, recall=1.0
        self.assertEqual(pc["A"]["TP"], 1)
        self.assertEqual(pc["A"]["FP"], 1)
        self.assertEqual(pc["A"]["FN"], 0)
        self.assertAlmostEqual(pc["A"]["precision"], 0.5)
        self.assertAlmostEqual(pc["A"]["recall"], 1.0)
        # B: TP=1, FP=0, FN=1 -> precision=1.0, recall=0.5
        self.assertEqual(pc["B"]["TP"], 1)
        self.assertEqual(pc["B"]["FP"], 0)
        self.assertEqual(pc["B"]["FN"], 1)
        self.assertAlmostEqual(pc["B"]["precision"], 1.0)
        self.assertAlmostEqual(pc["B"]["recall"], 0.5)

    def test_compute_per_code_none_class(self):
        """The 'none' class (empty string key) uses 'none' as dict key."""
        results = [
            {"gt": None, "rr": None},
            {"gt": None, "rr": None},
            {"gt": "X", "rr": None},
        ]
        pc = compute_per_code(results, [None, "X"])
        self.assertIn("none", pc)
        # none: TP=2 (gt=None,rr=None), FP=1 (gt=X,rr=None), FN=0
        self.assertEqual(pc["none"]["TP"], 2)
        self.assertEqual(pc["none"]["FP"], 1)
        self.assertEqual(pc["none"]["FN"], 0)
        self.assertAlmostEqual(pc["none"]["precision"], 2/3, places=3)

    def test_completeness_assertion_in_main(self):
        """Verify metrics_scorer.py source contains the 500/91 assertion."""
        scorer_path = PROJECT_ROOT / "engine" / "metrics_scorer.py"
        src = scorer_path.read_text(encoding="utf-8")
        self.assertIn("assert len(order_results) == 500", src)
        self.assertIn("assert len(settlement_results) == 91", src)


if __name__ == "__main__":
    unittest.main()

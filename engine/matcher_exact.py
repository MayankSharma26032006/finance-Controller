#!/usr/bin/env python3
"""
Phase 2: Deterministic Matching Engine
Implements docs/DESIGN_PHASE2.md exactly.

Thin orchestrator that imports and calls modules in sequence:
  1. preprocessor.py   - Constants, data loading, helpers
  2. batch_matcher.py  - Layer 1: settlement <-> bank
  3. order_matcher.py  - Layer 2: order <-> settlement
  4. refund_classifier.py - Step 3: refund classification
  5. exceptions.py     - Steps 4/5/6: ghost detection, consistency, output

Reads frozen data from data/raw/.
Outputs match_log.json to engine/output/.
"""

import sys
import os
from collections import defaultdict

# Add engine/ to path so relative imports work
sys.path.insert(0, os.path.dirname(__file__))

from preprocessor import load_csv, load_json
from batch_matcher import match_batches
from order_matcher import match_orders
from exceptions import (
    detect_ghost_transactions,
    check_consistency,
    compile_match_log,
    print_summary,
)

# ---------------------------------------------------------------------------
# Load frozen data
# ---------------------------------------------------------------------------
print("Loading frozen data...")
ledger = load_csv("order_ledger.csv")
settlement = load_csv("settlement_report.csv")
bank = load_csv("bank_statement.csv")

# Build indices
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

# ---------------------------------------------------------------------------
# Step 0: Preprocessing (label normalization & currency conversion are
#          applied during comparison in order_matcher, not here)
# ---------------------------------------------------------------------------
print("Step 0: Preprocessing...")

# ---------------------------------------------------------------------------
# Layer 1: Batch-Level Matching
# ---------------------------------------------------------------------------
print("Layer 1: Batch-level matching...")
batch_results = match_batches(settlement_by_sid, bank_credits_by_utr, ledger_ids)

batch_status_counts = defaultdict(int)
for sid, result in batch_results.items():
    batch_status_counts[result["status"]] += 1
print(f"  Batch results: {dict(batch_status_counts)}")

# ---------------------------------------------------------------------------
# Layer 2: Order-Level Matching
# ---------------------------------------------------------------------------
print("Layer 2: Order-level matching...")
order_results = match_orders(ledger_by_id, settlement_by_id)

# ---------------------------------------------------------------------------
# Step 5: Ghost Transaction Detection
# ---------------------------------------------------------------------------
print("Step 5: Ghost transaction detection...")
detect_ghost_transactions(batch_results)

# ---------------------------------------------------------------------------
# Step 6: Cross-Source Consistency Checks (soft flags only)
# ---------------------------------------------------------------------------
print("Step 6: Cross-source consistency checks...")
order_data = {
    "ledger_by_id": ledger_by_id,
    "settlement_by_id": settlement_by_id,
    "bank_credits_by_utr": bank_credits_by_utr,
}
check_consistency(order_results, order_data)

# ---------------------------------------------------------------------------
# Step 13: Compile Output
# ---------------------------------------------------------------------------
print("Compiling match_log.json...")
match_log, output_path = compile_match_log(order_results, batch_results)
print_summary(order_results, batch_results, match_log, output_path)

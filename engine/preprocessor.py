#!/usr/bin/env python3
"""
Shared constants, data loading, and helper functions for the matching engine.
"""

import csv
import json
import os
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
FX_RATE = 83.00
BATCH_TOLERANCE = 0.05
ORDER_TOLERANCE = 0.01

LABEL_ALIAS = {
    "visa_mc_domestic": "card",
    "amex_diners": "amex",
    "international_card": "intl_card",
    "upi": "upi",
}

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------
def load_csv(name):
    path = os.path.join(DATA_DIR, name)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(name):
    path = os.path.join(DATA_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def to_float(val):
    return round(float(val), 2)


def normalize_label(method):
    return LABEL_ALIAS.get(method, method)


def ledger_gross_inr(order):
    """Convert ledger gross to INR for comparison with settlement."""
    gross = to_float(order["gross_amount"])
    if order["currency"] == "USD":
        return round(gross * FX_RATE, 2)
    return gross


def is_weekend(d):
    return d.weekday() in (5, 6)


def next_working_day(d):
    d = d + timedelta(days=1)
    while is_weekend(d):
        d += timedelta(days=1)
    return d


def working_days_between(d1, d2):
    """Count working days from d1 to d2 (exclusive of d1)."""
    count = 0
    current = d1
    while current < d2:
        current = next_working_day(current)
        if current <= d2:
            count += 1
    return count

#!/usr/bin/env python3
"""
Unified pipeline runner for the AI Finance Controller.

Runs the deterministic reconciliation pipeline in the correct dependency
order. All phases read from frozen data/raw/ and write to their designated
output directories.

Usage:
    python run_pipeline.py --all              # Phases 2-6 in order
    python run_pipeline.py --reconcile-only   # Phase 2 only (matcher)
    python run_pipeline.py --with-ai-eval     # Phases 2,3,4,5 (with AI narration)
    python run_pipeline.py --regenerate-data  # Phase 1 first, then phases 2-6
                                                (requires confirmation)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Phase scripts in dependency order
PHASES = {
    "generate_data": {
        "script": PROJECT_ROOT / "data" / "generate_data.py",
        "label": "Phase 1: Generate synthetic data",
        "requires_api": False,
    },
    "matcher_exact": {
        "script": PROJECT_ROOT / "engine" / "matcher_exact.py",
        "label": "Phase 2: Deterministic matcher",
        "requires_api": False,
    },
    "explainer": {
        "script": PROJECT_ROOT / "agent" / "explainer.py",
        "label": "Phase 3: AI explanation layer",
        "requires_api": True,
    },
    "reconciler": {
        "script": PROJECT_ROOT / "engine" / "reconciler.py",
        "label": "Phase 4: Reconciliation report",
        "requires_api": False,
    },
    "metrics_scorer": {
        "script": PROJECT_ROOT / "engine" / "metrics_scorer.py",
        "label": "Phase 5: Metrics scoring",
        "requires_api": False,
    },
    "generate_audit": {
        "script": PROJECT_ROOT / "engine" / "generate_audit.py",
        "label": "Phase 6: Audit trail generation",
        "requires_api": False,
    },
}


def run_phase(phase_key):
    """Run a single phase script as a subprocess."""
    phase = PHASES[phase_key]
    script = phase["script"]
    label = phase["label"]

    if not script.exists():
        print(f"  ERROR: {script} not found. Skipping {label}.")
        return False

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(PROJECT_ROOT),
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"\n  FAILED: {label} exited with code {result.returncode}")
        return False

    print(f"\n  COMPLETED: {label}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="AI Finance Controller — unified pipeline runner"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Run all phases 2-6 in order (data stays frozen)",
    )
    group.add_argument(
        "--reconcile-only",
        action="store_true",
        help="Run Phase 2 only (deterministic matcher)",
    )
    group.add_argument(
        "--with-ai-eval",
        action="store_true",
        help="Run Phases 2,3,4,5 (matcher + AI narration + reconciler + metrics)",
    )
    group.add_argument(
        "--regenerate-data",
        action="store_true",
        help="Regenerate data (Phase 1) then run phases 2-6. Requires confirmation.",
    )
    args = parser.parse_args()

    # ── Determine which phases to run ──
    if args.reconcile_only:
        phase_keys = ["matcher_exact"]
    elif args.with_ai_eval:
        phase_keys = ["matcher_exact", "explainer", "reconciler", "metrics_scorer"]
    elif args.all:
        phase_keys = ["matcher_exact", "explainer", "reconciler", "metrics_scorer", "generate_audit"]
    elif args.regenerate_data:
        # Safety confirmation for data regeneration
        print("=" * 60)
        print("  WARNING: Data Regeneration")
        print("=" * 60)
        print()
        print("  This will DELETE and REGENERATE all files in data/raw/:")
        print("    - order_ledger.csv")
        print("    - settlement_report.csv")
        print("    - bank_statement.csv")
        print("    - ground_truth.json")
        print()
        print("  All downstream pipeline outputs will need to be re-generated.")
        print("  The current frozen dataset has been extensively validated.")
        print()
        confirm = input("  Type 'yes' to confirm regeneration: ").strip()
        if confirm != "yes":
            print("  Aborted. No files were modified.")
            sys.exit(0)
        phase_keys = [
            "generate_data",
            "matcher_exact",
            "explainer",
            "reconciler",
            "metrics_scorer",
            "generate_audit",
        ]
    else:
        parser.print_help()
        sys.exit(1)

    # ── Run phases ──
    print(f"\nRunning {len(phase_keys)} phase(s)...")
    print(f"Working directory: {PROJECT_ROOT}")
    print()

    failed = []
    for key in phase_keys:
        phase = PHASES[key]
        if phase["requires_api"]:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key or api_key == "your-key-here":
                # Try loading from .env
                try:
                    from dotenv import load_dotenv
                    load_dotenv(PROJECT_ROOT / ".env")
                    api_key = os.environ.get("GROQ_API_KEY")
                except ImportError:
                    pass
            if not api_key or api_key == "your-key-here":
                print(f"\n  WARNING: GROQ_API_KEY not set. Skipping {phase['label']}.")
                print(f"  Set GROQ_API_KEY in .env to enable AI narration.")
                failed.append(key)
                continue

        success = run_phase(key)
        if not success:
            failed.append(key)
            # Stop on failure for phases 2-4 (required for downstream)
            if key in ("matcher_exact", "reconciler", "metrics_scorer"):
                print(f"\n  Pipeline stopped: {phase['label']} failed.")
                break

    # ── Summary ──
    print(f"\n{'='*60}")
    print("  PIPELINE SUMMARY")
    print(f"{'='*60}")
    print(f"  Phases attempted: {len(phase_keys)}")
    print(f"  Phases failed:    {len(failed)}")
    if failed:
        print(f"  Failed phases:    {', '.join(failed)}")
    else:
        print(f"  Status:           All phases completed successfully.")
    print()


if __name__ == "__main__":
    main()

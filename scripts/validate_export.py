"""
validate_export.py — Export a stratified validation sample for hand-coding.

Creates a CSV with ~100 events stratified across event types and event sources
for Arthur to hand-code and compare against LLM classifications via Cohen's kappa.
Target kappa ≥ 0.80 ("good" agreement).

Usage:
    python scripts/validate_export.py --n 100 --output data/validation_sample.csv
    python scripts/validate_export.py --n 25 --batch 1  # first small refinement batch

Output columns:
    event_id, company, ticker, filing_date, event_type, event_subtype,
    headline, raw_text_url, source_type,
    arthur_event_type (blank — Arthur fills this in),
    arthur_event_subtype (blank),
    arthur_notes (blank),
    llm_event_type (from pipeline),
    llm_event_subtype (from pipeline)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from src.storage import load_events
from src.config import EVENT_TYPES, NOISY_EVENT_TYPES

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger("validate_export")


def export_validation_sample(
    n: int = 100,
    output_path: str = "data/validation_sample.csv",
    batch: int | None = None,
    seed: int = 42,
) -> None:
    """
    Export a stratified random sample for validation.

    Stratification:
      - Equal representation across the 5 event types (20 per type if n=100)
      - Within foreign events: split equally between entry and exit
      - Within source types: EDGAR and NewsAPI proportionally

    Args:
        n: total number of events to sample
        output_path: output CSV path
        batch: if set, export a smaller first batch (n events from batch 1, 2, ...)
               to refine the rubric before the full validation sample
        seed: random seed for reproducibility
    """
    events_df = load_events()

    if events_df.empty:
        logger.error("No events in data store. Run backfill.py first.")
        return

    n_per_type = n // len(EVENT_TYPES)
    samples = []

    for evt_type_key in EVENT_TYPES.keys():
        type_events = events_df[events_df["event_type"] == evt_type_key]
        if type_events.empty:
            logger.warning("No events for type: %s", evt_type_key)
            continue
        k = min(n_per_type, len(type_events))
        sampled = type_events.sample(n=k, random_state=seed)
        samples.append(sampled)
        logger.info("  %s: %d events sampled", evt_type_key, k)

    if not samples:
        logger.error("No events to sample.")
        return

    combined = pd.concat(samples, ignore_index=True)

    # If batch requested, take a subset
    if batch is not None:
        batch_size = max(5, n // 5)
        start = (batch - 1) * batch_size
        combined = combined.iloc[start: start + batch_size]
        logger.info("Batch %d: %d events (rows %d–%d)", batch, len(combined), start, start + batch_size)

    # Build output with validation columns
    output_cols = [
        "event_id", "company", "ticker", "filing_date",
        "event_type", "event_subtype",
        "headline", "raw_text_url", "source_type",
        "sector", "ai_flag",
    ]
    out = combined[[c for c in output_cols if c in combined.columns]].copy()

    # Rename LLM columns to make it clear
    out = out.rename(columns={
        "event_type":    "llm_event_type",
        "event_subtype": "llm_event_subtype",
    })

    # Add blank columns for Arthur's codes
    out["arthur_event_type"]    = ""
    out["arthur_event_subtype"] = ""
    out["arthur_notes"]         = ""

    # Reorder: Arthur's columns first for ease of coding
    final_cols = [
        "event_id", "company", "ticker", "filing_date",
        "arthur_event_type", "arthur_event_subtype", "arthur_notes",
        "llm_event_type", "llm_event_subtype",
        "headline", "raw_text_url", "source_type", "sector", "ai_flag",
    ]
    out = out[[c for c in final_cols if c in out.columns]]

    out.to_csv(output_path, index=False)
    logger.info("Saved %d events to %s", len(out), output_path)

    # Print kappa computation instructions
    print(f"""
Validation sample exported: {output_path}
Events: {len(out)}

NEXT STEPS:
1. Open {output_path} in Excel or Numbers
2. Fill in 'arthur_event_type' and 'arthur_event_subtype' for each row
   Coding guide: see docs/coding_scheme.md
3. Run the kappa computation:
   python scripts/compute_kappa.py --file {output_path}
4. Target kappa ≥ 0.80 for event_type (primary classification)
""")


def compute_kappa(file_path: str) -> None:
    """Compute Cohen's kappa between Arthur's codes and LLM codes."""
    try:
        from sklearn.metrics import cohen_kappa_score
    except ImportError:
        logger.error("scikit-learn required: pip install scikit-learn --break-system-packages")
        return

    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)

    # Filter to coded rows
    coded = df[df["arthur_event_type"].str.strip() != ""].copy()
    if coded.empty:
        logger.error("No coded rows found. Fill in arthur_event_type first.")
        return

    kappa_type = cohen_kappa_score(
        coded["arthur_event_type"].str.strip(),
        coded["llm_event_type"].str.strip(),
    )

    kappa_subtype = None
    if "arthur_event_subtype" in coded.columns and "llm_event_subtype" in coded.columns:
        sub_coded = coded[coded["arthur_event_subtype"].str.strip() != ""]
        if not sub_coded.empty:
            kappa_subtype = cohen_kappa_score(
                sub_coded["arthur_event_subtype"].str.strip(),
                sub_coded["llm_event_subtype"].str.strip(),
            )

    print(f"""
Cohen's Kappa Results
=====================
N coded:          {len(coded)}
Event type kappa: {kappa_type:.3f}  {'✅ PASS (≥0.80)' if kappa_type >= 0.80 else '❌ Below target — revise coding scheme'}
{'Subtype kappa:    ' + f'{kappa_subtype:.3f}' if kappa_subtype is not None else ''}

Confusion matrix (event type):
""")
    from collections import Counter
    pairs = list(zip(coded["arthur_event_type"], coded["llm_event_type"]))
    for pair, count in Counter(pairs).most_common():
        match = "✓" if pair[0] == pair[1] else "✗"
        print(f"  {match} Arthur: {pair[0]:<25} LLM: {pair[1]:<25} n={count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validation sample export and kappa computation")
    sub = parser.add_subparsers(dest="command")

    exp = sub.add_parser("export", help="Export validation sample")
    exp.add_argument("--n", type=int, default=100, help="Sample size")
    exp.add_argument("--output", default="data/validation_sample.csv")
    exp.add_argument("--batch", type=int, help="Export only batch N (for iterative refinement)")
    exp.add_argument("--seed", type=int, default=42)

    kap = sub.add_parser("kappa", help="Compute Cohen's kappa")
    kap.add_argument("--file", required=True, help="Path to coded validation CSV")

    args = parser.parse_args()

    if args.command == "export" or args.command is None:
        export_validation_sample(
            n=getattr(args, "n", 100),
            output_path=getattr(args, "output", "data/validation_sample.csv"),
            batch=getattr(args, "batch", None),
            seed=getattr(args, "seed", 42),
        )
    elif args.command == "kappa":
        compute_kappa(args.file)

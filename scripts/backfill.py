"""
backfill.py — 24-month historical backfill.

Fetches and processes all events from the past 24 months (configurable).
Runs the full predict + score pipeline in sequence.

Usage:
    python scripts/backfill.py [--months 24] [--event-type ceo_turnover]
    python scripts/backfill.py --months 24 --chunk-size 30

Notes:
  - Runs in monthly chunks to respect EDGAR rate limits and avoid timeouts
  - Already-stored events are skipped (idempotent)
  - Score job runs for each chunk after prediction to avoid very long gaps
  - Expect 4–8 hours for a full 24-month backfill across all event types
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_pipeline import run_predict
from scripts.run_scorer import run_score
from src.config import EVENT_TYPES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("backfill")


def date_chunks(start: date, end: date, chunk_days: int = 30):
    """Yield (start_str, end_str) tuples covering [start, end] in chunks."""
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def run_backfill(
    months: int = 24,
    event_type_filter: list[str] | None = None,
    chunk_days: int = 30,
    skip_scoring: bool = False,
) -> None:
    """
    Run the full predict + score pipeline for the past N months.
    """
    end_date   = date.today()
    start_date = date(end_date.year - months // 12, end_date.month - months % 12 + 12 if end_date.month - months % 12 <= 0 else end_date.month - months % 12, 1)

    # Simpler: just subtract days
    start_date = end_date - timedelta(days=months * 30)

    logger.info("=" * 70)
    logger.info("BACKFILL START: %s → %s  (%d months, %d-day chunks)",
                start_date, end_date, months, chunk_days)
    logger.info("=" * 70)

    event_types = event_type_filter or list(EVENT_TYPES.keys())
    logger.info("Event types: %s", event_types)

    total_events = 0
    total_predictions = 0

    for chunk_start, chunk_end in date_chunks(start_date, end_date, chunk_days):
        logger.info("\n--- Chunk: %s → %s ---", chunk_start, chunk_end)
        days = (date.fromisoformat(chunk_end) - date.fromisoformat(chunk_start)).days + 1

        summary = run_predict(
            days_back=days,
            event_type_filter=event_types,
        )
        total_events      += summary.get("events_new", 0)
        total_predictions += summary.get("predictions_generated", 0)

        # Score after each chunk (only past-window events will actually score)
        if not skip_scoring:
            run_score()

        # Brief pause between chunks to avoid hitting rate limits
        logger.info("Chunk complete. Pausing 5s before next chunk…")
        time.sleep(5)

    logger.info("\n" + "=" * 70)
    logger.info("BACKFILL COMPLETE: %d new events, %d predictions generated",
                total_events, total_predictions)
    logger.info("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Historical backfill")
    parser.add_argument("--months", type=int, default=24, help="Months to backfill (default: 24)")
    parser.add_argument(
        "--event-type",
        choices=list(EVENT_TYPES.keys()) + ["all"],
        default="all",
        help="Limit to one event type (default: all)",
    )
    parser.add_argument("--chunk-size", type=int, default=30, help="Days per chunk (default: 30)")
    parser.add_argument("--skip-scoring", action="store_true", help="Skip scoring pass (predict only)")
    args = parser.parse_args()

    event_filter = None if args.event_type == "all" else [args.event_type]
    run_backfill(
        months=args.months,
        event_type_filter=event_filter,
        chunk_days=args.chunk_size,
        skip_scoring=args.skip_scoring,
    )

"""
run_pipeline.py — Manual pipeline runner (prediction phase).

Fetches new events from EDGAR/NewsAPI, classifies them, generates locked
predictions via Claude, and writes all records to the data store.

Usage:
    python scripts/run_pipeline.py [--days 7] [--event-type ceo_turnover]

This is the same logic executed by the GitHub Actions predict.yml cron job.
Run manually for testing or to backfill a gap.

IMPORTANT: Always run this BEFORE run_scorer.py. The predict job must complete
before the scoring job can safely run.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import EVENT_TYPES
from src.edgar import fetch_events_for_type, get_company_metadata, load_sp500_ciks
from src.events import build_event_record, filter_new_events
from src.moderators import fetch_moderator_data
from src.news import fetch_foreign_events_newsapi, deduplicate_against_stored
from src.predictor import assert_no_outcome_data, generate_all_predictions, _get_client
from src.storage import (
    append_events,
    append_predictions,
    get_existing_event_ids,
    get_existing_prediction_pairs,
    load_events,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("predict")


def run_predict(
    days_back: int = 7,
    event_type_filter: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Run the prediction pipeline for the last N days.

    Returns summary dict with counts.
    """
    end_date   = date.today().isoformat()
    start_date = (date.today() - timedelta(days=days_back)).isoformat()

    logger.info("=" * 60)
    logger.info("PREDICT JOB  %s → %s", start_date, end_date)
    logger.info("=" * 60)

    # Load S&P 500 CIK list
    sp500_ciks = load_sp500_ciks()
    logger.info("Loaded %d S&P 500 CIKs", len(sp500_ciks))

    # Load existing state for deduplication
    existing_event_ids = get_existing_event_ids()
    existing_pred_pairs = get_existing_prediction_pairs()
    existing_headlines = set(load_events()["headline"].tolist()) if not load_events().empty else set()

    claude_client = _get_client()

    summary = {
        "events_found": 0,
        "events_new": 0,
        "predictions_generated": 0,
        "errors": 0,
    }

    event_types_to_run = event_type_filter or list(EVENT_TYPES.keys())

    for evt_type_key in event_types_to_run:
        logger.info("--- Processing event type: %s ---", evt_type_key)
        evt_type = EVENT_TYPES[evt_type_key]

        # Fetch from EDGAR
        raw_events = fetch_events_for_type(
            event_type_key=evt_type_key,
            start_date=start_date,
            end_date=end_date,
            sp500_ciks=sp500_ciks,
        )

        # Fetch from NewsAPI (supplemental, for foreign events)
        if evt_type.uses_newsapi:
            company_names = [e["entity_name"] for e in raw_events]
            # Also add all S&P 500 company names for broader coverage
            news_events = fetch_foreign_events_newsapi(
                company_names=company_names[:50],  # limit to top hits to conserve API quota
                start_date=start_date,
                end_date=end_date,
                event_type_key=evt_type_key,
            )
            news_events = deduplicate_against_stored(news_events, existing_headlines)
            raw_events.extend(news_events)
            logger.info("  NewsAPI added %d candidates", len(news_events))

        summary["events_found"] += len(raw_events)

        for raw in raw_events:
            try:
                cik   = raw.get("cik", "")
                ticker = raw.get("ticker", "")

                # Fetch company metadata
                company_meta = get_company_metadata(cik) if cik else {}

                # Fetch moderator data
                mod_data = fetch_moderator_data(ticker) if ticker else {}

                # Build structured event record
                event_record = build_event_record(raw, company_meta, mod_data)
                item_text = raw.get("item_text", "")

                # Deduplicate
                new_events = filter_new_events([event_record], existing_event_ids)
                if not new_events:
                    logger.debug("  Skip duplicate event: %s", event_record.get("event_id"))
                    continue

                event_record = new_events[0]
                event_id = event_record["event_id"]

                # LEAKAGE CHECK: assert no outcome data before prediction
                assert_no_outcome_data(event_record)

                if dry_run:
                    logger.info("  [DRY RUN] Would process: %s — %s",
                                event_record["company"], event_record["event_type"])
                    continue

                # Write event record (before predictions — ensures event exists if job interrupted)
                # Remove internal key before writing
                clean_record = {k: v for k, v in event_record.items() if not k.startswith("_")}
                append_events([clean_record])
                existing_event_ids.add(event_id)
                existing_headlines.add(event_record.get("headline", ""))
                summary["events_new"] += 1

                # Generate predictions (incremental write via callback)
                pending_theory_keys = {
                    theory_key
                    for theory_key in EVENT_TYPES[evt_type_key].theories
                    if (event_id, theory_key) not in existing_pred_pairs
                }

                if not pending_theory_keys:
                    logger.info("  All theories already predicted for %s", event_id)
                    continue

                def on_prediction(pred: dict) -> None:
                    """Write each prediction immediately after generation."""
                    append_predictions([pred])
                    existing_pred_pairs.add((pred["event_id"], pred["theory_key"]))
                    summary["predictions_generated"] += 1
                    logger.info(
                        "    [LOCKED] %s → %s (conf %s)",
                        pred["theory_name"],
                        pred["predicted_direction"].upper(),
                        pred["confidence"],
                    )

                generate_all_predictions(
                    event_record=event_record,
                    item_text=item_text,
                    client=claude_client,
                    on_prediction=on_prediction,
                    theory_key_filter=pending_theory_keys,
                )

            except Exception as e:
                logger.error("Error processing event: %s", e, exc_info=True)
                summary["errors"] += 1

    logger.info("=" * 60)
    logger.info("PREDICT JOB COMPLETE: %s", summary)
    logger.info("=" * 60)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the prediction pipeline")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument(
        "--event-type",
        choices=list(EVENT_TYPES.keys()) + ["all"],
        default="all",
        help="Event type to process (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print events without writing")
    args = parser.parse_args()

    event_filter = None if args.event_type == "all" else [args.event_type]
    run_predict(days_back=args.days, event_type_filter=event_filter, dry_run=args.dry_run)

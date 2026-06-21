"""
run_scorer.py — Manual scoring runner (outcome phase).

Scores completed events where the outcome window (3-day or 10-day) has passed,
sends notifications to crowd voters, and flags residuals.

Usage:
    python scripts/run_scorer.py [--horizon 3] [--horizon 10]

This is the same logic executed by the GitHub Actions score.yml cron job.
Run manually to score events outside the schedule.

IMPORTANT: Only run AFTER run_pipeline.py has completed for the relevant period.
The predict job must run before the scoring job.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import SCORING_HORIZONS
from src.notifier import notify_voters_for_event
from src.scorer import detect_residual, score_event
from src.storage import (
    append_outcomes,
    append_residuals,
    append_scores,
    get_events_pending_scoring,
    load_crowd_votes,
    load_outcomes,
    load_predictions,
    mark_crowd_votes_notified,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("scorer")


def run_score(horizons: list[int] | None = None) -> dict:
    """
    Score all events whose outcome windows have closed.

    Returns summary dict.
    """
    horizons = horizons or SCORING_HORIZONS

    logger.info("=" * 60)
    logger.info("SCORE JOB  horizons=%s", horizons)
    logger.info("=" * 60)

    predictions_df  = load_predictions()
    crowd_votes_df  = load_crowd_votes()
    existing_outcomes = set(load_outcomes()["event_id"].tolist()) if not load_outcomes().empty else set()

    summary = {
        "events_scored": 0,
        "scores_written": 0,
        "residuals_flagged": 0,
        "notifications_sent": 0,
        "errors": 0,
    }

    for horizon in horizons:
        logger.info("--- Horizon: %d trading days ---", horizon)
        pending = get_events_pending_scoring(horizon)

        if pending.empty:
            logger.info("  No events pending scoring at %d-day horizon", horizon)
            continue

        logger.info("  %d events to score", len(pending))

        for _, ev_row in pending.iterrows():
            event_id = ev_row["event_id"]
            try:
                # Get predictions for this event
                ev_preds = predictions_df[predictions_df["event_id"] == event_id].to_dict("records") \
                    if not predictions_df.empty else []

                if not ev_preds:
                    logger.warning("  No predictions found for event %s — skipping", event_id)
                    continue

                # Score
                outcome_record, score_records = score_event(
                    event_record=ev_row.to_dict(),
                    predictions=ev_preds,
                )

                if outcome_record is None:
                    logger.warning("  Could not score event %s (no price data)", event_id)
                    continue

                # Write outcome and scores
                if event_id not in existing_outcomes:
                    append_outcomes([outcome_record])
                    existing_outcomes.add(event_id)

                if score_records:
                    append_scores(score_records)
                    summary["scores_written"] += len(score_records)

                summary["events_scored"] += 1

                # Check for residual (only after all horizons scored)
                all_horizon_scores = score_records
                residual = detect_residual(ev_row.to_dict(), all_horizon_scores)
                if residual:
                    append_residuals([residual])
                    summary["residuals_flagged"] += 1
                    logger.info("  ⚠️ Residual flagged: %s", event_id)

                # Notify crowd voters (only when 3-day horizon resolves)
                if horizon == 3 and not crowd_votes_df.empty:
                    ev_votes = crowd_votes_df[
                        (crowd_votes_df["event_id"] == event_id) &
                        (crowd_votes_df["notified"] != "True")
                    ].to_dict("records")

                    if ev_votes:
                        sent = notify_voters_for_event(
                            event_record=ev_row.to_dict(),
                            score_records=score_records,
                            crowd_votes=ev_votes,
                        )
                        if sent > 0:
                            mark_crowd_votes_notified(event_id)
                            summary["notifications_sent"] += sent

                logger.info(
                    "  ✅ Scored event %s: %d scores",
                    event_id, len(score_records)
                )

            except Exception as e:
                logger.error("  Error scoring event %s: %s", event_id, e, exc_info=True)
                summary["errors"] += 1

    logger.info("=" * 60)
    logger.info("SCORE JOB COMPLETE: %s", summary)
    logger.info("=" * 60)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the scoring pipeline")
    parser.add_argument(
        "--horizon",
        type=int,
        choices=[3, 10],
        action="append",
        dest="horizons",
        help="Horizon(s) to score (3 and/or 10; default: both)",
    )
    args = parser.parse_args()
    run_score(horizons=args.horizons)

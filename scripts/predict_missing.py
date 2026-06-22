"""
predict_missing.py — Generate predictions for events that already exist in
events.csv but have no predictions yet.

Useful for recovering from backfill API failures without re-fetching from EDGAR.

Usage:
    python scripts/predict_missing.py
    python scripts/predict_missing.py --event-type ma
    python scripts/predict_missing.py --dry-run

Requires GROQ_API_KEY or ANTHROPIC_API_KEY in the environment (or a .pat is not
an API key — set the LLM key separately).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import EVENT_TYPES
from src.predictor import assert_no_outcome_data, generate_all_predictions, _get_client
from src.storage import (
    append_predictions,
    get_existing_prediction_pairs,
    load_events,
    load_predictions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("predict_missing")


def run(event_type_filter: list[str] | None = None, dry_run: bool = False) -> None:
    events_df = load_events()
    preds_df  = load_predictions()

    existing_pred_pairs = get_existing_prediction_pairs()

    # Filter to events that are missing at least one theory prediction
    def has_missing_theories(row):
        evt_type = row.get("event_type", "")
        if evt_type not in EVENT_TYPES:
            return False
        return any(
            (row["event_id"], tk) not in existing_pred_pairs
            for tk in EVENT_TYPES[evt_type].theories
        )

    missing = events_df[events_df.apply(has_missing_theories, axis=1)]

    if event_type_filter:
        missing = missing[missing["event_type"].isin(event_type_filter)]

    logger.info("Events with incomplete predictions: %d", len(missing))
    for _, row in missing.iterrows():
        predicted_count = sum(
            1 for tk in EVENT_TYPES.get(row["event_type"], type("", (), {"theories": []})()).theories
            if (row["event_id"], tk) in existing_pred_pairs
        )
        total = len(EVENT_TYPES[row["event_type"]].theories) if row["event_type"] in EVENT_TYPES else "?"
        logger.info("  %s — %s — %s  [%s/%s predicted]", row["company"], row["event_type"], row["filing_date"], predicted_count, total)

    if dry_run or missing.empty:
        return

    client = _get_client()
    total_written = 0
    errors = 0

    for _, event_record in missing.iterrows():
        event_dict = event_record.to_dict()
        event_id   = event_dict["event_id"]
        evt_type   = event_dict.get("event_type", "")

        if evt_type not in EVENT_TYPES:
            logger.warning("Unknown event type '%s' for event %s — skipping", evt_type, event_id)
            continue

        pending_theory_keys = {
            tk for tk in EVENT_TYPES[evt_type].theories
            if (event_id, tk) not in existing_pred_pairs
        }

        if not pending_theory_keys:
            logger.info("Already predicted all theories for %s", event_id)
            continue

        logger.info(
            "Predicting %s — %s — theories: %s",
            event_dict.get("company", ""), evt_type,
            ", ".join(pending_theory_keys),
        )

        try:
            assert_no_outcome_data(event_dict)
        except Exception as e:
            logger.warning("Leakage check failed for %s: %s — skipping", event_id, e)
            continue

        def on_prediction(pred: dict) -> None:
            append_predictions([pred])
            existing_pred_pairs.add((pred["event_id"], pred["theory_key"]))
            nonlocal total_written
            total_written += 1
            logger.info(
                "  [LOCKED] %s → %s (conf %s)",
                pred["theory_name"],
                pred["predicted_direction"].upper(),
                pred.get("confidence", "?"),
            )

        # Retry loop with exponential backoff for rate limits
        for attempt in range(4):
            try:
                generate_all_predictions(
                    event_record=event_dict,
                    item_text=event_dict.get("headline", ""),
                    client=client,
                    on_prediction=on_prediction,
                    theory_key_filter=pending_theory_keys,
                )
                break  # success
            except Exception as e:
                msg = str(e).lower()
                if "rate" in msg or "429" in msg or "too many" in msg:
                    wait = 30 * (2 ** attempt)
                    logger.warning("Rate limited — waiting %ds before retry %d/3", wait, attempt + 1)
                    time.sleep(wait)
                else:
                    logger.error("Error predicting %s: %s", event_id, e, exc_info=True)
                    errors += 1
                    break

        # Small pause between events to stay within rate limits
        time.sleep(3)

    logger.info("Done. Predictions written: %d  Errors: %d", total_written, errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Predict missing events from events.csv")
    parser.add_argument(
        "--event-type",
        choices=list(EVENT_TYPES.keys()),
        nargs="+",
        help="Only run for these event types (default: all missing)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List missing events, don't call LLM")
    args = parser.parse_args()

    run(event_type_filter=args.event_type, dry_run=args.dry_run)

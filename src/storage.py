"""
storage.py — Append-only CSV data layer with provenance enforcement.

DESIGN PRINCIPLES:
  - Raw source data (events, predictions, outcomes) is NEVER overwritten.
  - All writes are append-only; only new records are added.
  - Every record has a created_at or predicted_at or scored_at timestamp.
  - File paths are resolved relative to the repo root (configurable via REPO_ROOT env var).

Public API:
  load_events()           → pd.DataFrame
  load_predictions()      → pd.DataFrame
  load_outcomes()         → pd.DataFrame
  load_scores()           → pd.DataFrame
  load_crowd_votes()      → pd.DataFrame
  load_residuals()        → pd.DataFrame
  append_events(records)
  append_predictions(records)
  append_outcomes(records)
  append_scores(records)
  append_crowd_vote(record)
  append_residuals(records)
  get_existing_event_ids() → set[str]
  get_existing_prediction_pairs() → set[tuple[str, str]]
  get_scored_event_ids() → set[str]
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .config import (
    CROWD_VOTE_COLUMNS,
    EVENT_COLUMNS,
    EVENTS_FILE,
    OUTCOMES_FILE,
    PREDICTIONS_FILE,
    RESIDUAL_COLUMNS,
    RESIDUAL_FLAG_FILE,
    SCORE_COLUMNS,
    SCORES_FILE,
    CROWD_VOTES_FILE,
    PREDICTION_COLUMNS,
    OUTCOME_COLUMNS,
)

logger = logging.getLogger(__name__)

# Resolve repo root: env var → parent of this file's src/ directory
_REPO_ROOT = Path(os.getenv("REPO_ROOT", Path(__file__).parent.parent))


def _resolve(relative_path: str) -> Path:
    return _REPO_ROOT / relative_path


def _ensure_file(path: Path, columns: list[str]) -> None:
    """Create the CSV file with headers if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
        logger.info("Created %s", path)


# ---------------------------------------------------------------------------
# Load functions
# ---------------------------------------------------------------------------

def _load_csv(relative_path: str, columns: list[str]) -> pd.DataFrame:
    path = _resolve(relative_path)
    _ensure_file(path, columns)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        # Add any missing columns (schema evolution)
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        return df[columns]
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)


def load_events() -> pd.DataFrame:
    return _load_csv(EVENTS_FILE, EVENT_COLUMNS)


def load_predictions() -> pd.DataFrame:
    return _load_csv(PREDICTIONS_FILE, PREDICTION_COLUMNS)


def load_outcomes() -> pd.DataFrame:
    return _load_csv(OUTCOMES_FILE, OUTCOME_COLUMNS)


def load_scores() -> pd.DataFrame:
    return _load_csv(SCORES_FILE, SCORE_COLUMNS)


def load_crowd_votes() -> pd.DataFrame:
    return _load_csv(CROWD_VOTES_FILE, CROWD_VOTE_COLUMNS)


def load_residuals() -> pd.DataFrame:
    return _load_csv(RESIDUAL_FLAG_FILE, RESIDUAL_COLUMNS)


# ---------------------------------------------------------------------------
# Append functions — append-only, never overwrite
# ---------------------------------------------------------------------------

def _append_records(relative_path: str, columns: list[str], records: list[dict[str, Any]]) -> int:
    """
    Append records to a CSV file. Returns number of records written.
    Only writes columns defined in the schema; extra keys are dropped.
    """
    if not records:
        return 0

    path = _resolve(relative_path)
    _ensure_file(path, columns)

    written = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        for rec in records:
            writer.writerow({col: rec.get(col, "") for col in columns})
            written += 1

    logger.info("Appended %d records to %s", written, path.name)
    return written


def append_events(records: list[dict[str, Any]]) -> int:
    return _append_records(EVENTS_FILE, EVENT_COLUMNS, records)


def append_predictions(records: list[dict[str, Any]]) -> int:
    return _append_records(PREDICTIONS_FILE, PREDICTION_COLUMNS, records)


def append_outcomes(records: list[dict[str, Any]]) -> int:
    return _append_records(OUTCOMES_FILE, OUTCOME_COLUMNS, records)


def append_scores(records: list[dict[str, Any]]) -> int:
    return _append_records(SCORES_FILE, SCORE_COLUMNS, records)


def append_crowd_vote(record: dict[str, Any]) -> int:
    return _append_records(CROWD_VOTES_FILE, CROWD_VOTE_COLUMNS, [record])


def append_residuals(records: list[dict[str, Any]]) -> int:
    return _append_records(RESIDUAL_FLAG_FILE, RESIDUAL_COLUMNS, records)


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def get_existing_event_ids() -> set[str]:
    """Return set of all event_ids already stored."""
    df = load_events()
    return set(df["event_id"].tolist())


def get_existing_prediction_pairs() -> set[tuple[str, str]]:
    """Return set of (event_id, theory_key) pairs already predicted."""
    df = load_predictions()
    return {(row["event_id"], row["theory_key"]) for _, row in df.iterrows()}


def get_scored_event_ids() -> set[str]:
    """Return set of event_ids that have already been scored."""
    df = load_scores()
    return set(df["event_id"].tolist())


def get_events_pending_scoring(horizon: int) -> pd.DataFrame:
    """
    Return events that:
      1. Have been predicted (have entries in predictions.csv)
      2. Have NOT yet been scored at this horizon
      3. Whose outcome window has closed (checked in scorer.py)
    """
    from .scorer import outcome_window_closed

    events_df     = load_events()
    predictions_df = load_predictions()
    scores_df     = load_scores()

    predicted_event_ids = set(predictions_df["event_id"].tolist())
    scored_ids_at_horizon = set(
        scores_df[scores_df["horizon"] == str(horizon)]["event_id"].tolist()
    )

    pending = events_df[
        events_df["event_id"].isin(predicted_event_ids)
        & ~events_df["event_id"].isin(scored_ids_at_horizon)
    ].copy()

    # Filter to events whose window has now closed
    mask = pending.apply(
        lambda row: outcome_window_closed(
            row.get("event_date") or row.get("filing_date", ""),
            horizon,
        ),
        axis=1,
    )
    return pending[mask].copy()


def mark_crowd_votes_notified(event_id: str) -> None:
    """Update notified=True for all crowd votes on this event."""
    path = _resolve(CROWD_VOTES_FILE)
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[df["event_id"] == event_id, "notified"] = "True"
    df.to_csv(path, index=False)

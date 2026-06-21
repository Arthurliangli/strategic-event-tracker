"""
scorer.py — Abnormal return calculation and theory scoring.

Pipeline:
  1. Identify events whose outcome windows (3-day, 10-day) have now closed
  2. Pull price data via yfinance: event firm + SPY (market benchmark)
  3. Calculate cumulative abnormal return (CAR) for each window
  4. Classify direction: positive / negative / flat (|CAR| < FLAT_THRESHOLD)
  5. Score each theory prediction: is_win = predicted_direction matches realized_direction
     and realized is not flat
  6. Flag all-miss events as residuals
  7. Write outcome and score records

SEPARATION OF CONCERNS:
  This module is called ONLY by the scoring GitHub Actions job (score.yml),
  never by the prediction job. The two jobs must not run concurrently.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import yfinance as yf
import pandas as pd

from .config import (
    FLAT_THRESHOLD,
    MARKET_TICKER,
    NOISY_EVENT_TYPES,
    SCORING_HORIZONS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trading-day helpers
# ---------------------------------------------------------------------------

def get_trading_days(reference_date: str, n: int) -> str:
    """
    Return the date that is approximately n trading days after reference_date.
    Uses a conservative approach: fetch SPY calendar and walk forward.
    Returns "YYYY-MM-DD" string.
    """
    ref = pd.Timestamp(reference_date)
    # Add buffer so yfinance has enough data
    end_search = ref + timedelta(days=n * 2 + 10)

    try:
        spy = yf.download(
            MARKET_TICKER,
            start=ref.strftime("%Y-%m-%d"),
            end=end_search.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        trading_dates = [d for d in spy.index.tolist() if not pd.isna(d)]
        if len(trading_dates) > n:
            return pd.Timestamp(trading_dates[n]).strftime("%Y-%m-%d")
        elif trading_dates:
            return pd.Timestamp(trading_dates[-1]).strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning("Trading day lookup failed: %s — using calendar days", e)

    # Fallback: approximate with calendar days (140% of n)
    approx = ref + timedelta(days=int(n * 1.4))
    return approx.strftime("%Y-%m-%d")


def outcome_window_closed(event_date: str, horizon: int) -> bool:
    """Return True if the outcome window for this horizon has passed."""
    window_end_str = get_trading_days(event_date, horizon)
    window_end = datetime.strptime(window_end_str, "%Y-%m-%d").date()
    return date.today() > window_end


# ---------------------------------------------------------------------------
# Return calculation
# ---------------------------------------------------------------------------

def fetch_returns(
    ticker: str,
    event_date: str,
    horizon: int,
) -> tuple[float | None, float | None]:
    """
    Fetch (firm_return, market_return) over the event window.

    Window: [event_date, event_date + horizon trading days]
    Returns (firm_cum_return, market_cum_return) or (None, None) on failure.
    """
    start = event_date
    end_td = get_trading_days(event_date, horizon + 2)  # buffer

    try:
        firm_data = yf.download(
            ticker,
            start=start,
            end=end_td,
            progress=False,
            auto_adjust=True,
        )
        mkt_data = yf.download(
            MARKET_TICKER,
            start=start,
            end=end_td,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        logger.error("yfinance download failed for %s: %s", ticker, e)
        return None, None

    if firm_data.empty or mkt_data.empty:
        logger.warning("Empty price data for %s / %s", ticker, event_date)
        return None, None

    # Align on common dates
    # yfinance >=0.2.x may return multi-level columns; squeeze to Series
    firm_close = firm_data["Close"].squeeze().dropna()
    mkt_close  = mkt_data["Close"].squeeze().dropna()
    common_dates = firm_close.index.intersection(mkt_close.index)

    if len(common_dates) < 2:
        logger.warning("Insufficient price data for %s", ticker)
        return None, None

    # Use first available date as event-day (t=0) and t=n as end of window
    common_sorted = sorted(common_dates)
    window_dates = common_sorted[:horizon + 1]  # t=0 to t=n

    if len(window_dates) < 2:
        return None, None

    t0 = window_dates[0]
    tn = window_dates[-1]

    firm_ret = float(firm_close[tn] / firm_close[t0] - 1)
    mkt_ret  = float(mkt_close[tn] / mkt_close[t0] - 1)

    return firm_ret, mkt_ret


def compute_car(firm_return: float, market_return: float) -> float:
    """Cumulative Abnormal Return = firm_return - market_return."""
    return firm_return - market_return


def classify_direction(car: float) -> tuple[str, bool]:
    """
    Returns (direction, is_flat).
    direction: 'positive' | 'negative' | 'neutral'
    is_flat: True if |CAR| < FLAT_THRESHOLD
    """
    if abs(car) < FLAT_THRESHOLD:
        return "neutral", True
    return ("positive" if car > 0 else "negative"), False


# ---------------------------------------------------------------------------
# Score one event
# ---------------------------------------------------------------------------

def score_event(
    event_record: dict[str, Any],
    predictions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    Score one event across both horizons.

    Returns:
        outcome_record: dict conforming to OUTCOME_COLUMNS (or None if data unavailable)
        score_records: list of dicts conforming to SCORE_COLUMNS
    """
    event_id   = event_record.get("event_id", "")
    ticker     = event_record.get("ticker", "")
    event_date = event_record.get("event_date", event_record.get("filing_date", ""))
    event_type = event_record.get("event_type", "")
    is_foreign = event_type in NOISY_EVENT_TYPES

    if not ticker or not event_date:
        logger.warning("Missing ticker or date for event %s — skipping", event_id)
        return None, []

    outcome: dict[str, Any] = {
        "outcome_id": str(uuid.uuid4())[:16],
        "event_id":   event_id,
    }
    score_records: list[dict] = []

    for horizon in SCORING_HORIZONS:
        if not outcome_window_closed(event_date, horizon):
            logger.info("  Window not yet closed for event %s at %d-day horizon", event_id, horizon)
            continue

        firm_ret, mkt_ret = fetch_returns(ticker, event_date, horizon)
        if firm_ret is None:
            logger.warning("  No return data for %s at %d-day horizon", ticker, horizon)
            outcome[f"return_{horizon}d"]        = ""
            outcome[f"market_return_{horizon}d"] = ""
            outcome[f"car_{horizon}d"]           = ""
            outcome[f"direction_{horizon}d"]     = ""
            outcome[f"is_flat_{horizon}d"]       = ""
            continue

        car = compute_car(firm_ret, mkt_ret)
        direction, is_flat = classify_direction(car)

        outcome[f"return_{horizon}d"]        = round(firm_ret, 6)
        outcome[f"market_return_{horizon}d"] = round(mkt_ret, 6)
        outcome[f"car_{horizon}d"]           = round(car, 6)
        outcome[f"direction_{horizon}d"]     = direction
        outcome[f"is_flat_{horizon}d"]       = is_flat
        outcome[f"outcome_window_end_{horizon}d"] = get_trading_days(event_date, horizon)

        # Score each theory prediction for this horizon
        for pred in predictions:
            predicted_dir = pred.get("predicted_direction", "neutral")
            is_win = (
                not is_flat
                and predicted_dir == direction
                and predicted_dir != "neutral"
            )

            score_records.append({
                "score_id":           str(uuid.uuid4())[:16],
                "prediction_id":      pred.get("prediction_id", ""),
                "event_id":           event_id,
                "theory_key":         pred.get("theory_key", ""),
                "theory_name":        pred.get("theory_name", ""),
                "horizon":            horizon,
                "predicted_direction": predicted_dir,
                "realized_direction": direction,
                "is_flat":            is_flat,
                "is_win":             is_win,
                "is_foreign_event":   is_foreign,
                "scored_at":          datetime.utcnow().isoformat() + "Z",
            })

    outcome["scored_at"] = datetime.utcnow().isoformat() + "Z"
    return outcome, score_records


# ---------------------------------------------------------------------------
# Residual detection
# ---------------------------------------------------------------------------

def detect_residual(
    event_record: dict[str, Any],
    score_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    If all theories missed (is_win=False) at BOTH horizons (non-flat),
    flag the event as a residual for the quarterly review.
    Returns a residual dict or None.
    """
    if not score_records:
        return None

    # Only flag if there are scored (non-flat) horizons
    scored = [s for s in score_records if not s.get("is_flat", True)]
    if not scored:
        return None  # all horizons were flat — not a residual

    all_missed = all(not s.get("is_win", False) for s in scored)
    if not all_missed:
        return None

    return {
        "residual_id":         str(uuid.uuid4())[:16],
        "event_id":            event_record.get("event_id", ""),
        "event_type":          event_record.get("event_type", ""),
        "theories_all_missed": True,
        "patterns_noted":      "",  # filled in during quarterly review
        "flagged_at":          datetime.utcnow().isoformat() + "Z",
    }

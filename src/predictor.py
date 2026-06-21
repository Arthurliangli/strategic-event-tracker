"""
predictor.py — Locked prediction generation via Anthropic Claude API.

NO-LEAKAGE GUARANTEE:
  Predictions are generated and written BEFORE any outcome data is retrieved.
  The scoring pipeline (scorer.py) runs in a separate GitHub Actions job.
  Once written, prediction records are never modified — only new records are appended.

Design:
  - Calls Claude API once per (event × theory) combination
  - Parses JSON response; falls back to text parsing if JSON is malformed
  - Writes predictions immediately after generation (not batched) to minimize
    risk of losing data if the job is interrupted
  - Records model_used so predictions remain reproducible if model changes
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any

import anthropic

from .config import (
    ANTHROPIC_API_KEY,
    PREDICTION_MODEL,
    THEORY_ROSTER_VERSION,
    PREDICTION_COLUMNS,
)
from .theories import SYSTEM_PROMPT, build_prediction_prompt, get_theories_for_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Single theory prediction
# ---------------------------------------------------------------------------

def generate_prediction(
    theory_key: str,
    event_record: dict[str, Any],
    item_text: str,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any] | None:
    """
    Generate a locked prediction for one (event, theory) pair.

    Returns a prediction dict conforming to PREDICTION_COLUMNS, or None on failure.
    """
    from .config import THEORIES
    from .theories import Theory

    theory = THEORIES.get(theory_key)
    if theory is None:
        logger.error("Unknown theory key: %s", theory_key)
        return None

    if client is None:
        client = _get_client()

    user_prompt = build_prediction_prompt(theory, event_record, item_text)
    predicted_at = datetime.utcnow().isoformat() + "Z"

    try:
        response = client.messages.create(
            model=PREDICTION_MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text.strip()
    except anthropic.APIError as e:
        logger.error("Anthropic API error for %s/%s: %s", event_record.get("event_id"), theory_key, e)
        return None
    except Exception as e:
        logger.error("Unexpected error generating prediction: %s", e)
        return None

    # Parse JSON response
    parsed = _parse_prediction_response(raw_text)
    if parsed is None:
        logger.warning("Could not parse prediction for %s/%s — skipping",
                       event_record.get("event_id"), theory_key)
        return None

    prediction_id = str(uuid.uuid4())[:16]

    return {
        "prediction_id":        prediction_id,
        "event_id":             event_record.get("event_id", ""),
        "theory_key":           theory_key,
        "theory_name":          theory.name,
        "predicted_direction":  parsed.get("predicted_direction", "neutral"),
        "reasoning":            parsed.get("reasoning", ""),
        "magnitude_estimate":   parsed.get("magnitude_estimate", "small"),
        "confidence":           str(parsed.get("confidence", 3)),
        "predicted_at":         predicted_at,
        "theory_roster_version": str(THEORY_ROSTER_VERSION),
        "model_used":           PREDICTION_MODEL,
        "locked":               "True",
    }


def _parse_prediction_response(text: str) -> dict | None:
    """
    Parse Claude's JSON response. Attempts multiple strategies:
      1. Direct JSON parse
      2. Extract JSON from markdown code block
      3. Regex extraction of key fields
    """
    # Strategy 1: direct parse
    try:
        data = json.loads(text)
        return _validate_prediction(data)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract from code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return _validate_prediction(data)
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract JSON object anywhere in text
    match = re.search(r"\{[^{}]*\"predicted_direction\"[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return _validate_prediction(data)
        except json.JSONDecodeError:
            pass

    # Strategy 4: regex field extraction
    direction_match = re.search(r'"predicted_direction"\s*:\s*"(\w+)"', text)
    reasoning_match = re.search(r'"reasoning"\s*:\s*"([^"]*)"', text)
    magnitude_match = re.search(r'"magnitude_estimate"\s*:\s*"(\w+)"', text)
    confidence_match = re.search(r'"confidence"\s*:\s*(\d)', text)

    if direction_match:
        return _validate_prediction({
            "predicted_direction": direction_match.group(1),
            "reasoning": reasoning_match.group(1) if reasoning_match else "",
            "magnitude_estimate": magnitude_match.group(1) if magnitude_match else "small",
            "confidence": int(confidence_match.group(1)) if confidence_match else 3,
        })

    return None


def _validate_prediction(data: dict) -> dict:
    """Normalize and validate prediction fields."""
    direction = data.get("predicted_direction", "neutral").lower().strip()
    if direction not in ("positive", "negative", "neutral"):
        direction = "neutral"

    magnitude = data.get("magnitude_estimate", "small").lower().strip()
    if magnitude not in ("small", "medium", "large"):
        magnitude = "small"

    try:
        confidence = max(1, min(5, int(data.get("confidence", 3))))
    except (ValueError, TypeError):
        confidence = 3

    reasoning = str(data.get("reasoning", "")).strip()
    # Sanitize: remove newlines from reasoning for CSV storage
    reasoning = reasoning.replace("\n", " ").replace("\r", " ")[:1000]

    return {
        "predicted_direction": direction,
        "reasoning": reasoning,
        "magnitude_estimate": magnitude,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Full event prediction (all theories)
# ---------------------------------------------------------------------------

def generate_all_predictions(
    event_record: dict[str, Any],
    item_text: str,
    client: anthropic.Anthropic | None = None,
    on_prediction: callable | None = None,
    theory_key_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate predictions for theories relevant to this event type.

    Args:
        event_record: structured event dict from events.py
        item_text: raw 8-K item text or article text
        client: reuse Anthropic client if provided
        on_prediction: callback(prediction_dict) called after each prediction
                       is generated — used by run_pipeline.py to write
                       predictions incrementally (minimize data loss on interruption)
        theory_key_filter: if provided, only generate predictions for these
                           theory keys (used to skip already-predicted theories)

    Returns list of prediction dicts.
    """
    event_type = event_record.get("event_type", "")
    theories = get_theories_for_event(event_type)

    if not theories:
        logger.warning("No theories for event type: %s", event_type)
        return []

    if client is None:
        client = _get_client()

    predictions = []
    # get_theories_for_event returns (key, Theory) tuples — use the canonical key directly
    for theory_key, theory in theories:
        if theory_key_filter is not None and theory_key not in theory_key_filter:
            logger.debug("  Skipping already-predicted theory: %s", theory_key)
            continue
        logger.info("  Generating prediction: %s / %s", event_type, theory.short_name)
        pred = generate_prediction(
            theory_key=theory_key,
            event_record=event_record,
            item_text=item_text,
            client=client,
        )

        if pred is not None:
            predictions.append(pred)
            if on_prediction:
                on_prediction(pred)
        else:
            logger.error("  Failed prediction for %s — skipping", theory.name)

        # Brief pause between API calls
        time.sleep(0.5)

    return predictions


# ---------------------------------------------------------------------------
# Leakage prevention check
# ---------------------------------------------------------------------------

def assert_no_outcome_data(event_record: dict[str, Any]) -> None:
    """
    Assert that the event record does NOT contain outcome/price fields.
    Called before prediction generation as a safeguard.
    Raises ValueError if outcome data is detected.
    """
    outcome_keys = {"car_3d", "car_10d", "return_3d", "return_10d",
                    "direction_3d", "direction_10d", "is_win"}
    found = outcome_keys & set(event_record.keys())
    if found:
        raise ValueError(
            f"LEAKAGE PREVENTION: event_record contains outcome fields: {found}. "
            "Prediction generation must run before scoring."
        )

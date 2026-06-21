"""
theories.py — Theory roster access helpers and prediction prompt assembly.

The canonical theory definitions live in config.py (THEORIES dict).
This module provides:
  - get_theories_for_event(): returns only the theories relevant to an event type
  - build_prediction_prompt(): assembles the full Claude prompt for a single theory
  - SYSTEM_PROMPT: the shared system context for all prediction calls
"""

from __future__ import annotations

from typing import Any

from .config import THEORIES, Theory, THEORY_ROSTER_VERSION, EVENT_TYPES

# ---------------------------------------------------------------------------
# System prompt — shared across all prediction calls
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a management theory expert acting as a pre-registered prediction engine.

Your role:
- You will receive a description of a real corporate strategic event.
- You must generate a locked, pre-outcome prediction according to ONE specific management theory.
- Your prediction will be compared against the actual stock market reaction AFTER the fact.
- This is a competitive tournament: theories are scored on predictive validity.

CRITICAL RULES — these are research integrity requirements:
1. Predict ONLY based on information in the event description — not on any outcome data.
2. State a clear directional prediction: POSITIVE, NEGATIVE, or NEUTRAL.
   - POSITIVE = expect a positive abnormal stock return (above market) over the outcome window.
   - NEGATIVE = expect a negative abnormal stock return.
   - NEUTRAL = expect near-zero or ambiguous abnormal return.
3. Provide brief, theory-grounded reasoning (3–6 sentences). Cite the theoretical mechanism.
4. Estimate magnitude: SMALL (<1%), MEDIUM (1–3%), or LARGE (>3%).
5. Assign a confidence level 1–5 where:
   1 = very uncertain / theory applies weakly
   3 = moderate confidence
   5 = strong theoretical prediction, clear applicability
6. Do NOT hedge by averaging across theories — you represent ONE theory only.
7. Do NOT look up or reference actual stock prices, analyst reports, or news after filing date.

Output format (JSON, no markdown):
{
  "predicted_direction": "positive" | "negative" | "neutral",
  "reasoning": "...",
  "magnitude_estimate": "small" | "medium" | "large",
  "confidence": 1-5
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_theories_for_event(event_type_key: str) -> list[tuple[str, Theory]]:
    """
    Return list of (theory_key, Theory) tuples that compete on this event type.
    The theory_key is the canonical key from config.THEORIES and must be used
    when writing prediction records — never derived from the theory name string.
    """
    et = EVENT_TYPES.get(event_type_key)
    if not et:
        return []
    return [(k, THEORIES[k]) for k in et.theories if k in THEORIES]


def build_prediction_prompt(
    theory: Theory,
    event_record: dict[str, Any],
    item_text: str,
) -> str:
    """
    Build the user-turn prompt for Claude to generate a prediction.

    Args:
        theory: Theory object from config.THEORIES
        event_record: the structured event dict (from events.py)
        item_text: raw 8-K item text or news article text

    Returns full user message string.
    """
    company   = event_record.get("company", "Unknown Company")
    ticker    = event_record.get("ticker", "N/A")
    sector    = event_record.get("sector", "N/A")
    sic_code  = event_record.get("sic_code", "N/A")
    event_type = event_record.get("event_type", "")
    event_subtype = event_record.get("event_subtype", "")
    filing_date   = event_record.get("filing_date", "")
    home_country  = event_record.get("home_country", "US")
    market_cap    = event_record.get("market_cap_usd", "N/A")
    ai_flag       = event_record.get("ai_flag", False)
    source_type   = event_record.get("source_type", "edgar")

    event_type_label = EVENT_TYPES.get(event_type, type("", (), {"label": event_type})()).label

    prompt = f"""EVENT DESCRIPTION
=================
Company: {company} ({ticker})
Sector: {sector} (SIC {sic_code})
Home country: {home_country}
Market cap: {market_cap}
Event type: {event_type_label} — subtype: {event_subtype}
Filing / publication date: {filing_date}
AI-related event: {"YES" if ai_flag else "NO"}
Source: {"SEC EDGAR 8-K" if source_type == "edgar" else "News API (supplemental — lower confidence)"}

RELEVANT FILING / ARTICLE TEXT:
{item_text[:2000]}

---

YOUR ASSIGNED THEORY: {theory.name}
=========================================
{theory.prediction_prompt_fragment}

Generate your locked prediction now. Output only valid JSON as specified.
"""
    return prompt.strip()


def build_crowd_context(event_record: dict[str, Any]) -> str:
    """
    Build a concise, plain-language event summary for the public voting UI.
    No jargon — this is shown to non-expert crowd voters.
    """
    company  = event_record.get("company", "Unknown")
    ticker   = event_record.get("ticker", "")
    evt_type = event_record.get("event_type", "")
    subtype  = event_record.get("event_subtype", "")
    date_str = event_record.get("filing_date", "")
    headline = event_record.get("headline", "")

    type_labels = {
        "ceo_turnover": "a CEO or senior executive change",
        "ma": "a merger or acquisition",
        "restructuring": "a restructuring or layoff announcement",
        "foreign_entry": "entry into a foreign market",
        "foreign_exit": "exit from a foreign market",
    }
    evt_label = type_labels.get(evt_type, "a strategic event")

    return (
        f"**{company} ({ticker})** announced {evt_label} on {date_str}.\n\n"
        f"**What happened:** {headline}\n\n"
        f"**Your prediction:** Do you think {company}'s stock will go UP or DOWN "
        f"in the days after this announcement, compared to the overall market?"
    )

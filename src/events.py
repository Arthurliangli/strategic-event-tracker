"""
events.py — Event classification, deduplication, and structured event creation.

Responsibilities:
  - Classify raw filings into specific event subtypes (e.g., CEO turnover vs.
    CFO turnover; horizontal M&A vs. vertical; voluntary vs. forced restructuring)
  - Generate a unique event_id
  - Detect AI-related events (ai_flag)
  - Assign event_date (typically filing_date; uses period_of_report when available)
  - Prevent duplicate events (same CIK + event_type + event window)
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from .config import (
    AI_KEYWORDS,
    EVENT_TYPES,
    NOISY_EVENT_TYPES,
    THEORY_ROSTER_VERSION,
    EventType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event ID generation
# ---------------------------------------------------------------------------

def make_event_id(cik: str, event_type: str, filing_date: str) -> str:
    """
    Deterministic event ID: SHA-256 of (CIK, event_type, filing_date).
    Deterministic so re-runs don't create duplicates.
    """
    raw = f"{cik}|{event_type}|{filing_date}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# AI flag detection
# ---------------------------------------------------------------------------

def detect_ai_flag(text: str) -> bool:
    """Return True if the event text contains AI-related keywords."""
    lower = text.lower()
    return any(kw in lower for kw in AI_KEYWORDS)


# ---------------------------------------------------------------------------
# Event subtype classification
# ---------------------------------------------------------------------------

# CEO/Officer turnover subtypes
_CEO_PATTERNS = [
    (r"\bCEO\b|\bchief executive\b", "ceo"),
    (r"\bCFO\b|\bchief financial\b", "cfo"),
    (r"\bCOO\b|\bchief operating\b", "coo"),
    (r"\bchair\b|\bdirector\b|\bboard\b", "director"),
    (r"\bpresident\b", "president"),
]

_CEO_ACTION_PATTERNS = [
    (r"\bresign\b|\bdeparture\b|\bstep(?:ping)? down\b|\bretir\b", "departure"),
    (r"\bappoint\b|\belect\b|\bnamed\b|\bhire\b|\bjoin\b", "appointment"),
]

# M&A subtypes
_MA_PATTERNS = [
    (r"\bacquir\b|\bacquisition\b|\bmerger\b|\bpurchas\b", "acquisition"),
    (r"\bdispos\b|\bdivestiture\b|\bsale of\b|\bsold\b", "divestiture"),
    (r"\bjoint venture\b|\bpartnership\b", "joint_venture"),
]

# Restructuring subtypes
_RESTR_PATTERNS = [
    (r"\blayoff\b|\bworkforce reduction\b|\bjob cut\b|\bredundanc\b", "layoffs"),
    (r"\bplant clos\b|\bfacility clos\b|\bshutdown\b", "facility_closure"),
    (r"\brestructur\b|\breorganiz\b", "restructuring"),
]

# Foreign entry subtypes
_ENTRY_PATTERNS = [
    (r"\bjoint venture\b|\bJV\b", "joint_venture"),
    (r"\bwholly.?owned\b|\bWOS\b|\bsubsidiary\b", "wholly_owned"),
    (r"\blicens\b", "licensing"),
    (r"\bacquir\b|\bstake\b|\bequity interest\b", "acquisition"),
    (r"\bgreenfield\b|\bnew facility\b|\bnew plant\b", "greenfield"),
]

# Foreign exit subtypes
_EXIT_PATTERNS = [
    (r"\bsell\b|\bsale\b|\bdispos\b|\bdivestiture\b", "divestiture"),
    (r"\bceas\b|\bdiscontinue\b|\bwind down\b|\bshutdown\b", "closure"),
    (r"\bexit\b|\bwithdraw\b|\bpull out\b", "withdrawal"),
]


def _classify_by_patterns(text: str, patterns: list[tuple[str, str]]) -> str:
    lower = text.lower()
    for pattern, label in patterns:
        if re.search(pattern, lower):
            return label
    return "other"


def classify_event_subtype(event_type_key: str, item_text: str) -> str:
    """
    Return a fine-grained subtype label for the event.
    Used for moderator variable logging and rubric validation.
    """
    if event_type_key == "ceo_turnover":
        role = _classify_by_patterns(item_text, _CEO_PATTERNS)
        action = _classify_by_patterns(item_text, _CEO_ACTION_PATTERNS)
        return f"{role}_{action}" if role != "other" else "officer_change"

    if event_type_key == "ma":
        return _classify_by_patterns(item_text, _MA_PATTERNS)

    if event_type_key == "restructuring":
        return _classify_by_patterns(item_text, _RESTR_PATTERNS)

    if event_type_key == "foreign_entry":
        return _classify_by_patterns(item_text, _ENTRY_PATTERNS)

    if event_type_key == "foreign_exit":
        return _classify_by_patterns(item_text, _EXIT_PATTERNS)

    return "unknown"


# ---------------------------------------------------------------------------
# Structured event creation
# ---------------------------------------------------------------------------

def build_event_record(
    raw: dict[str, Any],
    company_meta: dict[str, Any],
    moderator_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert a raw filing dict (from edgar.py or news.py) into a full event record
    conforming to EVENT_COLUMNS in config.py.

    Args:
        raw: dict from fetch_events_for_type() or fetch_foreign_events_newsapi()
        company_meta: dict from get_company_metadata()
        moderator_data: dict from moderators.py (market_cap_usd, slack_ratio)

    Returns dict ready to be written by storage.py
    """
    event_type_key = raw.get("event_type_key", "")
    filing_date    = raw.get("filing_date") or raw.get("published_date", "")
    cik            = raw.get("cik", "")
    item_text      = raw.get("item_text", "")
    headline       = raw.get("headline", "") or item_text[:120]

    event_id = make_event_id(cik or raw.get("entity_name", ""), event_type_key, filing_date)

    sic_raw     = company_meta.get("sic", "")
    sic_prefix  = sic_raw[:2] if sic_raw else ""
    from .config import SIC_SECTOR_MAP
    sector = SIC_SECTOR_MAP.get(sic_prefix, "Other")

    return {
        "event_id":            event_id,
        "company":             raw.get("entity_name", company_meta.get("name", "")),
        "ticker":              raw.get("ticker", (company_meta.get("tickers") or [""])[0]),
        "cik":                 cik,
        "sic_code":            sic_raw,
        "sector":              sector,
        "home_country":        company_meta.get("state_of_incorporation", "US"),
        "filing_date":         filing_date,
        "event_date":          filing_date,  # refined in scorer.py to next trading day
        "event_type":          event_type_key,
        "event_subtype":       classify_event_subtype(event_type_key, item_text),
        "raw_text_url":        raw.get("index_url") or raw.get("article_url", ""),
        "source":              raw.get("source_name", "SEC EDGAR"),
        "source_type":         raw.get("source_type", "edgar"),
        "headline":            headline,
        "ai_flag":             detect_ai_flag(item_text + " " + headline),
        "market_cap_usd":      moderator_data.get("market_cap_usd", ""),
        "slack_ratio":         moderator_data.get("slack_ratio", ""),
        "theory_roster_version": THEORY_ROSTER_VERSION,
        "created_at":          datetime.utcnow().isoformat() + "Z",
        # Internal — not in EVENT_COLUMNS but needed downstream
        "_item_text":          item_text,
    }


# ---------------------------------------------------------------------------
# Duplicate checking
# ---------------------------------------------------------------------------

def is_duplicate(event_id: str, existing_ids: set[str]) -> bool:
    """Return True if this event_id has already been stored."""
    return event_id in existing_ids


def filter_new_events(
    candidates: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    """Filter out events already present in the data store."""
    seen = set()
    out = []
    for ev in candidates:
        eid = ev.get("event_id", "")
        if eid and eid not in existing_ids and eid not in seen:
            out.append(ev)
            seen.add(eid)
    return out

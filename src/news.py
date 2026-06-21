"""
news.py — NewsAPI integration for foreign market entry/exit events.

This feed supplements the EDGAR Item 8.01 source. It is intentionally noisier
than the clean EDGAR triggers for the other event types. All results are flagged
as source_type="newsapi" and shown on a separate leaderboard panel.

Pipeline:
  1. Search NewsAPI for S&P 500 company name + entry/exit keywords
  2. Deduplicate against events already stored (by company + headline similarity)
  3. Return structured event candidates for classification by events.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

from .config import (
    NEWSAPI_KEY,
    NEWSAPI_URL,
    EVENT_TYPES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword sets for entry vs. exit
# ---------------------------------------------------------------------------

ENTRY_KEYWORDS = [
    "enters market", "market entry", "new subsidiary", "new facility",
    "opens operations", "expands into", "new manufacturing plant",
    "establishes joint venture", "acquires stake in", "greenfield investment",
    "new office in", "launches in", "begins operations in",
]

EXIT_KEYWORDS = [
    "exits market", "ceases operations", "withdraws from", "divests subsidiary",
    "closes facility", "sells operations", "wind down", "market exit",
    "discontinues operations", "ceasing operations in", "pulls out of",
    "exit from", "sell its operations",
]

_ALL_KEYWORDS = ENTRY_KEYWORDS + EXIT_KEYWORDS

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _newsapi_get(params: dict) -> dict:
    """Call NewsAPI with retry logic."""
    if not NEWSAPI_KEY:
        raise RuntimeError(
            "NEWSAPI_KEY not set. Foreign market events require NewsAPI. "
            "Set NEWSAPI_KEY env var or disable foreign event types."
        )
    params["apiKey"] = NEWSAPI_KEY
    url = NEWSAPI_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StrategicEventTracker/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 2)
                logger.warning("NewsAPI 429 — retrying in %ds", wait)
                time.sleep(wait)
            else:
                logger.error("NewsAPI HTTP %d", e.code)
                raise
        except Exception as e:
            logger.warning("NewsAPI error (attempt %d): %s", attempt, e)
            time.sleep(2 ** attempt)
    raise RuntimeError("NewsAPI failed after retries")


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def fetch_foreign_events_newsapi(
    company_names: list[str],
    start_date: str,
    end_date: str,
    event_type_key: str,
) -> list[dict[str, Any]]:
    """
    Fetch news articles about foreign market events for a list of company names.

    Args:
        company_names: list of company display names (from EDGAR metadata)
        start_date / end_date: "YYYY-MM-DD"
        event_type_key: "foreign_entry" or "foreign_exit"

    Returns list of article dicts with provenance fields added.
    """
    keywords = ENTRY_KEYWORDS if event_type_key == "foreign_entry" else EXIT_KEYWORDS
    results: list[dict] = []
    seen_hashes: set[str] = set()

    for company in company_names:
        for kw in keywords[:5]:  # limit to top 5 keywords to conserve API calls
            query = f'"{company}" "{kw}"'
            params = {
                "q": query,
                "from": start_date,
                "to": end_date,
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": 10,
            }
            try:
                data = _newsapi_get(params)
            except Exception as e:
                logger.warning("NewsAPI query failed for %s / %s: %s", company, kw, e)
                continue

            articles = data.get("articles", [])
            for art in articles:
                title = art.get("title", "")
                desc = art.get("description", "")
                url = art.get("url", "")
                published = art.get("publishedAt", "")[:10]

                # Dedup by title hash
                h = hashlib.md5(title.encode()).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                # Confirm relevance: company name AND at least one keyword in title/desc
                content = (title + " " + desc).lower()
                if company.lower() not in content:
                    continue
                if not any(kw2.lower() in content for kw2 in keywords):
                    continue

                # Determine if it looks more like entry or exit
                detected_type = _classify_article_type(content)
                if detected_type != event_type_key:
                    continue

                results.append({
                    "entity_name": company,
                    "headline": title,
                    "description": desc,
                    "article_url": url,
                    "published_date": published,
                    "source_name": art.get("source", {}).get("name", ""),
                    "event_type_key": detected_type,
                    "source_type": "newsapi",
                    "item_text": f"{title}. {desc}",
                    "keyword_matched": kw,
                })

            time.sleep(0.25)  # ~4 req/sec — well within free tier

    return results


def _classify_article_type(content: str) -> str:
    """Simple keyword vote to classify article as entry or exit."""
    entry_score = sum(1 for kw in ENTRY_KEYWORDS if kw.lower() in content)
    exit_score = sum(1 for kw in EXIT_KEYWORDS if kw.lower() in content)
    return "foreign_entry" if entry_score >= exit_score else "foreign_exit"


# ---------------------------------------------------------------------------
# Deduplication against existing events
# ---------------------------------------------------------------------------

def deduplicate_against_stored(
    candidates: list[dict],
    stored_headlines: set[str],
    similarity_threshold: float = 0.7,
) -> list[dict]:
    """
    Remove candidates whose headline is too similar to already-stored events.
    Uses token overlap (Jaccard) as a cheap similarity measure.
    """

    def jaccard(a: str, b: str) -> float:
        toks_a = set(a.lower().split())
        toks_b = set(b.lower().split())
        if not toks_a or not toks_b:
            return 0.0
        return len(toks_a & toks_b) / len(toks_a | toks_b)

    out = []
    for c in candidates:
        headline = c.get("headline", "")
        is_dup = any(
            jaccard(headline, stored) >= similarity_threshold
            for stored in stored_headlines
        )
        if not is_dup:
            out.append(c)
    return out

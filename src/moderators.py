"""
moderators.py — Moderator variable logging.

Fetches and computes four moderator variables logged alongside every event:
  1. market_cap_usd   — firm size proxy (from yfinance)
  2. slack_ratio      — (current_assets - current_liabilities) / total_assets
                        (unabsorbed organizational slack)
  3. home_country     — state of incorporation from EDGAR submissions API
  4. ai_flag          — detected in events.py from event text keywords

Note: ai_flag is set in events.py; only market_cap and slack are fetched here.
home_country comes from company_meta in events.py.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fetch_moderator_data(ticker: str) -> dict[str, Any]:
    """
    Fetch market cap and organizational slack for a given ticker via yfinance.

    Returns dict with keys:
      market_cap_usd: float or ""
      slack_ratio: float or ""
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as e:
        logger.warning("yfinance info fetch failed for %s: %s", ticker, e)
        return {"market_cap_usd": "", "slack_ratio": ""}

    market_cap = info.get("marketCap", "")

    # Unabsorbed slack = (currentAssets - currentLiabilities) / totalAssets
    current_assets      = info.get("totalCurrentAssets", None)
    current_liabilities = info.get("totalCurrentLiabilities", None)
    total_assets        = info.get("totalAssets", None)

    slack_ratio = ""
    if (
        current_assets is not None
        and current_liabilities is not None
        and total_assets
        and total_assets > 0
    ):
        slack_ratio = round(
            (current_assets - current_liabilities) / total_assets, 4
        )

    return {
        "market_cap_usd": market_cap,
        "slack_ratio": slack_ratio,
    }

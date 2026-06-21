"""
config.py — All constants, theory roster, and event type definitions.

DESIGN PRINCIPLE: Every tuneable value lives here. No hardcoded strings elsewhere.
Theory roster is versioned; bump THEORY_ROSTER_VERSION when adding/removing theories.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# API / endpoint constants
# ---------------------------------------------------------------------------

EDGAR_EFTS_URL      = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS   = "https://data.sec.gov/submissions"
EDGAR_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
EDGAR_ARCHIVES      = "https://www.sec.gov/Archives/edgar/data"
NEWSAPI_URL         = "https://newsapi.org/v2/everything"
NTFY_BASE           = "https://ntfy.sh"

# ---------------------------------------------------------------------------
# API keys (always from environment — never hardcoded)
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
NEWSAPI_KEY         = os.getenv("NEWSAPI_KEY", "")
RESEND_API_KEY      = os.getenv("RESEND_API_KEY", "")
NTFY_TOPIC          = os.getenv("NTFY_TOPIC", "theory-tournament")
NOTIFICATION_FROM   = os.getenv("NOTIFICATION_FROM", "tournament@yourdomain.com")

# LLM provider auto-detected from available API keys.
# Set PREDICTION_MODEL env var to override the model name.
# Groq default: llama-3.3-70b-versatile (free tier, OpenAI-compatible)
# Anthropic default: claude-opus-4-8
_default_model = (
    "llama-3.3-70b-versatile" if os.getenv("GROQ_API_KEY") else "claude-opus-4-8"
)
PREDICTION_MODEL    = os.getenv("PREDICTION_MODEL", _default_model)

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

SCORING_HORIZONS    = [3, 10]          # trading days
FLAT_THRESHOLD      = 0.005            # |CAR| < 0.5% → classified as flat / no signal
MARKET_TICKER       = "SPY"            # benchmark for abnormal return

# ---------------------------------------------------------------------------
# Theory roster — v1
# ---------------------------------------------------------------------------

THEORY_ROSTER_VERSION = 1

@dataclass
class Theory:
    name: str
    short_name: str
    event_types: list[str]           # which event types this theory competes on
    predicted_direction: str         # 'positive', 'negative', or 'conditional'
    description: str                 # one-sentence summary shown on leaderboard
    prediction_prompt_fragment: str  # injected into Claude prompt for this theory

THEORIES: dict[str, Theory] = {
    # ---- CEO / Officer Turnover ----
    "upper_echelons": Theory(
        name="Upper Echelons Theory",
        short_name="Upper Echelons",
        event_types=["ceo_turnover"],
        predicted_direction="conditional",
        description="Outsider successor following prior underperformance → positive market reaction.",
        prediction_prompt_fragment=(
            "Apply Upper Echelons Theory. Assess whether the successor appears to be an outsider "
            "(recruited from outside the firm) and whether the firm showed prior underperformance. "
            "If outsider + underperformance: predict POSITIVE. If insider or no clear underperformance: "
            "predict NEGATIVE or NEUTRAL. Explain your reasoning using executive background signals "
            "visible in the filing."
        ),
    ),
    "agency_ceo": Theory(
        name="Agency Theory (CEO Turnover)",
        short_name="Agency (CEO)",
        event_types=["ceo_turnover"],
        predicted_direction="positive",
        description="CEO removal signals governance correcting a principal–agent problem → positive.",
        prediction_prompt_fragment=(
            "Apply Agency Theory. CEO departure — especially forced — signals that the board is "
            "fulfilling its governance role and correcting a principal–agent misalignment. "
            "Predict POSITIVE. Adjust if the filing language suggests voluntary retirement with "
            "no governance concern (lower confidence). Explain the agency logic."
        ),
    ),
    "disruption_ceo": Theory(
        name="Disruption / Instability View (CEO)",
        short_name="Disruption (CEO)",
        event_types=["ceo_turnover"],
        predicted_direction="negative",
        description="Leadership change creates strategic uncertainty regardless of cause → negative.",
        prediction_prompt_fragment=(
            "Apply the Disruption/Instability view. Any CEO transition introduces uncertainty "
            "about strategic direction, key relationships, and organizational stability. "
            "Predict NEGATIVE regardless of the specific circumstances. Calibrate magnitude: "
            "higher uncertainty if no obvious successor, lower if planned succession is clear."
        ),
    ),

    # ---- M&A ----
    "synergy_tce": Theory(
        name="Synergy / Transaction Cost Economics",
        short_name="Synergy/TCE",
        event_types=["ma"],
        predicted_direction="positive",
        description="Deal creates operational or financial synergies → positive combined return.",
        prediction_prompt_fragment=(
            "Apply Synergy/TCE theory. Assess whether the deal description suggests efficiency "
            "gains, vertical integration, or scope economies. Predict POSITIVE if synergy logic "
            "is plausible. Consider deal type (horizontal vs. vertical), target description, "
            "and stated rationale. Flag if the premium appears very high (which weakens synergy credibility)."
        ),
    ),
    "hubris_ma": Theory(
        name="Hubris Hypothesis (Roll 1986)",
        short_name="Hubris",
        event_types=["ma"],
        predicted_direction="negative",
        description="Overconfident acquirers overpay; acquirer return is negative.",
        prediction_prompt_fragment=(
            "Apply Roll's Hubris Hypothesis. Acquirers systematically overpay due to managerial "
            "overconfidence. Predict NEGATIVE for the acquirer's stock return. Calibrate magnitude "
            "by premium size (if disclosed) and deal size relative to acquirer market cap. "
            "Note any signals of hubris: serial acquirer, high premium, cash deal (associated with "
            "overconfidence more than stock deals)."
        ),
    ),
    "entrenchment_ma": Theory(
        name="Managerial Entrenchment (Agency/M&A)",
        short_name="Entrenchment",
        event_types=["ma"],
        predicted_direction="negative",
        description="Empire-building acquisition serves managers, not shareholders → negative acquirer return.",
        prediction_prompt_fragment=(
            "Apply Managerial Entrenchment theory. Large acquisitions often reflect managers "
            "pursuing growth and empire-building at shareholders' expense. Predict NEGATIVE for "
            "the acquirer. Strengthen your prediction if: deal is large, diversifying (unrelated), "
            "or the firm has free cash flow (Jensen 1986). Weaken if the acquisition is small "
            "and highly focused."
        ),
    ),

    # ---- Restructuring / Layoffs ----
    "rbv_restructuring": Theory(
        name="Resource-Based View (Restructuring)",
        short_name="RBV",
        event_types=["restructuring"],
        predicted_direction="negative",
        description="Cutting human capital destroys rare/inimitable resources → negative long-run.",
        prediction_prompt_fragment=(
            "Apply Resource-Based View. Large-scale layoffs and restructuring destroy human capital "
            "and tacit knowledge that are rare, valuable, and difficult to rebuild. Predict NEGATIVE. "
            "This effect is particularly pronounced at longer horizons (10-day), as capabilities "
            "take time to erode. Calibrate: R&D-intensive or knowledge-worker-heavy firms suffer more."
        ),
    ),
    "signaling_restructuring": Theory(
        name="Signaling Theory (Restructuring)",
        short_name="Signaling",
        event_types=["restructuring"],
        predicted_direction="positive",
        description="Decisive cost-cutting signals management discipline; reassures investors → positive.",
        prediction_prompt_fragment=(
            "Apply Signaling Theory. Announcing restructuring/layoffs signals that management "
            "is disciplined, addresses over-staffing, and is committed to margin improvement. "
            "Predict POSITIVE. Calibrate: larger and cleaner restructuring announcements signal "
            "more decisiveness. Predict NEGATIVE if the restructuring is framed as a response "
            "to severe distress (which signals desperation, not discipline)."
        ),
    ),
    "stakeholder_restructuring": Theory(
        name="Stakeholder Theory (Restructuring)",
        short_name="Stakeholder",
        event_types=["restructuring"],
        predicted_direction="negative",
        description="Layoffs damage employee morale and reputation; negative reaction scales with size.",
        prediction_prompt_fragment=(
            "Apply Stakeholder Theory. Large layoffs impose costs on employees, communities, and "
            "suppliers, generating reputational damage and morale effects that impair long-run "
            "performance. Predict NEGATIVE. Scale your magnitude estimate with the number of "
            "employees affected (if disclosed) and the firm's sector exposure to consumer sentiment "
            "(B2C firms suffer reputationally more than B2B)."
        ),
    ),

    # ---- Foreign Market Entry ----
    "oli_entry": Theory(
        name="OLI Paradigm / Internalization Theory",
        short_name="OLI",
        event_types=["foreign_entry"],
        predicted_direction="conditional",
        description="Entry mode matched to ownership advantage and institutional distance → positive.",
        prediction_prompt_fragment=(
            "Apply Dunning's OLI Paradigm and Internalization Theory. Assess whether the entry "
            "mode (wholly-owned, JV, licensing) appears matched to the firm's ownership advantages "
            "and the host country's institutional environment. Predict POSITIVE if the entry mode "
            "appears appropriate (e.g., WOS in low-distance market with strong IP advantage). "
            "Predict NEGATIVE if there is a visible mismatch (e.g., WOS in high-distance, opaque "
            "institutional environment without apparent ownership advantages)."
        ),
    ),
    "institutional_entry": Theory(
        name="Institutional Theory / Liability of Foreignness",
        short_name="Inst. / LoF",
        event_types=["foreign_entry"],
        predicted_direction="conditional",
        description="High institutional distance → negative, especially for high-commitment modes.",
        prediction_prompt_fragment=(
            "Apply Institutional Theory and Liability of Foreignness. High institutional distance "
            "between home and host country imposes compliance costs and legitimacy penalties. "
            "Predict NEGATIVE for high institutional distance + high-commitment modes (WOS, majority JV). "
            "Predict NEUTRAL/POSITIVE for low-distance entries or low-commitment modes (minority JV, "
            "licensing). Use the host country as a proxy for institutional distance: "
            "OECD/developed = lower; emerging/frontier = higher."
        ),
    ),
    "real_options_entry": Theory(
        name="Real Options Theory (Entry)",
        short_name="Real Options (Entry)",
        event_types=["foreign_entry"],
        predicted_direction="conditional",
        description="Low-commitment modes in uncertain markets rewarded; WOS in uncertain markets punished.",
        prediction_prompt_fragment=(
            "Apply Real Options Theory. Low-commitment entry modes (JV, licensing, minority stake) "
            "preserve flexibility in uncertain markets — investors reward optionality. "
            "Wholly-owned entry forecloses options and commits resources irreversibly. "
            "Predict POSITIVE for JV/licensing into high-uncertainty markets. "
            "Predict NEGATIVE for WOS into high-uncertainty markets. "
            "Predict NEUTRAL for WOS into stable, low-uncertainty markets (options value is low anyway)."
        ),
    ),

    # ---- Foreign Market Exit ----
    "strategic_refocusing_exit": Theory(
        name="Strategic Refocusing / RBV (Exit)",
        short_name="Strategic Refocus",
        event_types=["foreign_exit"],
        predicted_direction="positive",
        description="Exit reallocates resources to core markets; focus improves performance → positive.",
        prediction_prompt_fragment=(
            "Apply Strategic Refocusing / Resource-Based View. Foreign market exit returns "
            "resources (capital, management attention) to core competency markets, improving "
            "resource allocation efficiency. Predict POSITIVE. Strengthen if the exiting firm "
            "is over-diversified internationally. Weaken if the exit market was a key revenue source."
        ),
    ),
    "sunk_cost_exit": Theory(
        name="Sunk Cost / Legitimacy Loss (Exit)",
        short_name="Sunk Cost / Legit.",
        event_types=["foreign_exit"],
        predicted_direction="negative",
        description="Exit signals past strategic misjudgment; legitimacy loss → negative.",
        prediction_prompt_fragment=(
            "Apply Sunk Cost and Organizational Legitimacy theories. Foreign market exit reveals "
            "that prior entry was a strategic error, imposing reputational and legitimacy costs. "
            "Predict NEGATIVE. Calibrate: more negative if the exit is abrupt, involves write-downs, "
            "or follows a high-commitment entry. Less negative if the exit was planned and the "
            "market was a small fraction of total revenue."
        ),
    ),
    "real_options_exit": Theory(
        name="Real Options Theory (Exit)",
        short_name="Real Options (Exit)",
        event_types=["foreign_exit"],
        predicted_direction="conditional",
        description="Exit = rational option abandonment → neutral-to-positive if uncertainty was priced.",
        prediction_prompt_fragment=(
            "Apply Real Options Theory. Exiting a foreign market exercises the abandonment option, "
            "stopping value destruction from continuing in an unprofitable or high-risk market. "
            "Predict NEUTRAL-TO-POSITIVE if there are signals that the market has become riskier "
            "or less attractive than anticipated (investors will see this as rational). "
            "Predict NEGATIVE if the market was previously represented as stable and promising "
            "(exit contradicts prior signals, destroying credibility). Note how much uncertainty "
            "about the market was already known/priced."
        ),
    ),
}

# ---------------------------------------------------------------------------
# Event type definitions
# ---------------------------------------------------------------------------

@dataclass
class EventType:
    key: str                    # internal identifier
    label: str                  # display label
    edgar_items: list[str]      # 8-K item numbers (e.g., "5.02")
    uses_newsapi: bool          # whether NewsAPI supplements EDGAR
    keywords: list[str]         # keywords for Item 8.01 screening (foreign events)
    theories: list[str]         # theory keys from THEORIES that compete here
    source_note: str            # brief note on data quality / coverage

EVENT_TYPES: dict[str, EventType] = {
    "ceo_turnover": EventType(
        key="ceo_turnover",
        label="CEO / Officer Turnover",
        edgar_items=["5.02"],
        uses_newsapi=False,
        keywords=[],
        theories=["upper_echelons", "agency_ceo", "disruption_ceo"],
        source_note="Clean 8-K Item 5.02 trigger; high recall for S&P 500.",
    ),
    "ma": EventType(
        key="ma",
        label="M&A",
        edgar_items=["1.01", "2.01"],
        uses_newsapi=False,
        keywords=[],
        theories=["synergy_tce", "hubris_ma", "entrenchment_ma"],
        source_note="Item 1.01 (agreement) + 2.01 (completion); filter for acquisition language.",
    ),
    "restructuring": EventType(
        key="restructuring",
        label="Restructuring / Layoffs",
        edgar_items=["2.05"],
        uses_newsapi=False,
        keywords=[],
        theories=["rbv_restructuring", "signaling_restructuring", "stakeholder_restructuring"],
        source_note="Item 2.05 dedicated to exit/disposal activities; reliable trigger.",
    ),
    "foreign_entry": EventType(
        key="foreign_entry",
        label="Foreign Market Entry",
        edgar_items=["8.01"],
        uses_newsapi=True,
        keywords=[
            "new subsidiary", "new facility", "manufacturing facility",
            "establishes operations", "enters market", "opens office",
            "new plant", "greenfield", "joint venture", "new manufacturing",
        ],
        theories=["oli_entry", "institutional_entry", "real_options_entry"],
        source_note=(
            "Noisier feed: Item 8.01 + keyword screening + NewsAPI. "
            "Expect more manual triage; results shown separately on leaderboard."
        ),
    ),
    "foreign_exit": EventType(
        key="foreign_exit",
        label="Foreign Market Exit",
        edgar_items=["8.01"],
        uses_newsapi=True,
        keywords=[
            "ceases operations", "exits market", "divests subsidiary",
            "closes facility", "withdrawal from", "discontinues operations",
            "sells subsidiary", "wind down", "ceasing operations in",
        ],
        theories=["strategic_refocusing_exit", "sunk_cost_exit", "real_options_exit"],
        source_note=(
            "Same noisier feed as foreign entry. Results flagged and separated on leaderboard."
        ),
    ),
}

# ---- Event types that use the noisy/supplemental feed ----
NOISY_EVENT_TYPES   = {"foreign_entry", "foreign_exit"}

# ---- Moderator variable config ----
AI_KEYWORDS = [
    "artificial intelligence", " ai ", "machine learning", "deep learning",
    "generative ai", "large language model", "llm", "automation", "robotics",
    "neural network", "chatbot", "autonomous", "algorithm",
]

# SIC code → sector label (abbreviated — full mapping loaded at runtime from EDGAR)
SIC_SECTOR_MAP: dict[str, str] = {
    "01": "Agriculture", "10": "Mining", "13": "Oil & Gas",
    "15": "Construction", "20": "Food & Tobacco", "22": "Textiles",
    "26": "Paper", "27": "Publishing", "28": "Chemicals",
    "29": "Petroleum Refining", "30": "Rubber/Plastics", "32": "Stone/Glass",
    "33": "Primary Metals", "34": "Fabricated Metals", "35": "Industrial Machinery",
    "36": "Electronics", "37": "Transportation Equipment", "38": "Instruments",
    "40": "Railroad", "42": "Trucking", "44": "Water Transport",
    "45": "Air Transport", "48": "Communications", "49": "Electric/Gas Utilities",
    "50": "Wholesale - Durable", "51": "Wholesale - Nondurable",
    "52": "Retail - Building", "53": "Retail - General", "54": "Retail - Food",
    "55": "Retail - Auto", "56": "Retail - Apparel", "57": "Retail - Home",
    "58": "Eating & Drinking", "59": "Retail - Misc",
    "60": "Depository Institutions", "61": "Nondepository Credit",
    "62": "Security Brokers", "63": "Insurance", "64": "Insurance Agents",
    "65": "Real Estate", "67": "Holding Companies",
    "70": "Hotels & Lodging", "72": "Personal Services",
    "73": "Business Services", "75": "Auto Repair",
    "78": "Motion Pictures", "79": "Amusement", "80": "Health Services",
    "82": "Educational Services", "83": "Social Services",
    "87": "Engineering Services", "99": "Nonclassifiable",
}

# ---------------------------------------------------------------------------
# Data file paths (relative to repo root)
# ---------------------------------------------------------------------------

DATA_DIR            = "data"
EVENTS_FILE         = f"{DATA_DIR}/events/events.csv"
PREDICTIONS_FILE    = f"{DATA_DIR}/predictions/predictions.csv"
OUTCOMES_FILE       = f"{DATA_DIR}/outcomes/outcomes.csv"
SCORES_FILE         = f"{DATA_DIR}/outcomes/scores.csv"
CROWD_VOTES_FILE    = f"{DATA_DIR}/crowd/votes.csv"
SP500_CACHE_FILE    = f"{DATA_DIR}/raw/sp500_ciks.json"
RESIDUAL_FLAG_FILE  = f"{DATA_DIR}/outcomes/residuals.csv"

# ---------------------------------------------------------------------------
# Column schemas (used by storage.py for consistent CSV headers)
# ---------------------------------------------------------------------------

EVENT_COLUMNS = [
    "event_id", "company", "ticker", "cik", "sic_code", "sector",
    "home_country", "filing_date", "event_date", "event_type",
    "event_subtype", "raw_text_url", "source", "source_type",
    "headline", "ai_flag", "market_cap_usd", "slack_ratio",
    "theory_roster_version", "created_at",
]

PREDICTION_COLUMNS = [
    "prediction_id", "event_id", "theory_key", "theory_name",
    "predicted_direction",        # 'positive' | 'negative' | 'neutral'
    "reasoning", "magnitude_estimate",  # qualitative: 'small' | 'medium' | 'large'
    "confidence",                 # 1–5 scale
    "predicted_at",               # ISO timestamp — locked at generation time
    "theory_roster_version",
    "model_used",
    "locked",                     # always True once written
]

OUTCOME_COLUMNS = [
    "outcome_id", "event_id",
    "return_3d", "market_return_3d", "car_3d", "direction_3d", "is_flat_3d",
    "return_10d", "market_return_10d", "car_10d", "direction_10d", "is_flat_10d",
    "outcome_window_end_3d", "outcome_window_end_10d",
    "scored_at",
]

SCORE_COLUMNS = [
    "score_id", "prediction_id", "event_id", "theory_key", "theory_name",
    "horizon",                    # 3 or 10
    "predicted_direction",
    "realized_direction",
    "is_flat",
    "is_win",                     # True if directions match AND not flat
    "is_foreign_event",           # True if event_type in NOISY_EVENT_TYPES
    "scored_at",
]

CROWD_VOTE_COLUMNS = [
    "vote_id", "event_id", "direction",   # 'positive' | 'negative'
    "confidence",                 # 1–5 scale (optional)
    "email",                      # optional; for notifications
    "ntfy_channel",               # optional; for push notifications
    "created_at",
    "notified",                   # whether outcome notification was sent
]

RESIDUAL_COLUMNS = [
    "residual_id", "event_id", "event_type",
    "theories_all_missed",        # True if all theories missed at both horizons
    "patterns_noted",             # free text from quarterly review
    "flagged_at",
]

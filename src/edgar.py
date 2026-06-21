"""
edgar.py — SEC EDGAR event fetching.

Covers:
  - Company tickers → CIK mapping (S&P 500 filter)
  - Full-text search API (EFTS) for 8-K filings by item number
  - Submissions API for filer metadata
  - 8-K text fetching and item extraction
  - Rate-limit-aware retry with exponential back-off
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

from .config import (
    EDGAR_ARCHIVES,
    EDGAR_COMPANY_TICKERS,
    EDGAR_EFTS_URL,
    EDGAR_SUBMISSIONS,
    EVENT_TYPES,
    SP500_CACHE_FILE,
    NOISY_EVENT_TYPES,
)

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu",
    "Accept-Encoding": "gzip, deflate",
    "Host": "efts.sec.gov",
}

_SUBMISSIONS_HEADERS = {
    "User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu",
    "Host": "data.sec.gov",
}

# Pause between EDGAR requests — SEC rate limit is 10 req/sec
_REQUEST_DELAY = 0.12   # seconds


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, headers: dict | None = None, retries: int = 5) -> dict | list:
    """GET JSON from URL with exponential back-off on 429 / 5xx."""
    hdrs = headers or _HEADERS
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                time.sleep(_REQUEST_DELAY)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = 2 ** attempt
                logger.warning("EDGAR HTTP %d — retrying in %ds", e.code, wait)
                time.sleep(wait)
            else:
                logger.error("EDGAR HTTP %d for %s", e.code, url)
                raise
        except Exception as e:
            logger.error("EDGAR request error: %s", e)
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} retries: {url}")


def _get_text(url: str, retries: int = 5) -> str:
    """GET raw text (for 8-K documents)."""
    hdrs = {
        "User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                time.sleep(_REQUEST_DELAY)
                try:
                    return raw.decode("utf-8", errors="replace")
                except Exception:
                    return raw.decode("latin-1", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = 2 ** attempt
                logger.warning("Text fetch HTTP %d — retrying in %ds", e.code, wait)
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            logger.warning("Text fetch error: %s", e)
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} retries: {url}")


# ---------------------------------------------------------------------------
# S&P 500 CIK mapping
# ---------------------------------------------------------------------------

def load_sp500_ciks(cache_path: str = SP500_CACHE_FILE) -> dict[str, str]:
    """
    Return {ticker: cik_str} for S&P 500 constituents.

    Uses a local cache (refreshed if older than 7 days). Falls back to the
    EDGAR company_tickers.json master list filtered against a hard-coded S&P 500
    ticker set (updated quarterly via backfill.py).
    """
    import os
    from pathlib import Path

    cache = Path(cache_path)
    cache.parent.mkdir(parents=True, exist_ok=True)

    # Use cache if fresh
    if cache.exists():
        age = datetime.now().timestamp() - cache.stat().st_mtime
        if age < 7 * 86400:
            with open(cache) as f:
                return json.load(f)

    logger.info("Fetching EDGAR company tickers list…")
    all_tickers: dict = _get(
        EDGAR_COMPANY_TICKERS,
        headers={"User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu"},
    )

    # Build ticker → CIK map from EDGAR master list
    ticker_to_cik: dict[str, str] = {}
    for _idx, rec in all_tickers.items():
        tk = rec.get("ticker", "").upper()
        cik = str(rec.get("cik_str", "")).zfill(10)
        if tk:
            ticker_to_cik[tk] = cik

    # Filter to S&P 500 — use the canonical list (defined below)
    sp500 = {tk: ticker_to_cik[tk] for tk in _SP500_TICKERS if tk in ticker_to_cik}

    with open(cache, "w") as f:
        json.dump(sp500, f, indent=2)

    logger.info("Cached %d S&P 500 CIKs", len(sp500))
    return sp500


# ---------------------------------------------------------------------------
# EDGAR Full-Text Search (EFTS)
# ---------------------------------------------------------------------------

def search_8k_filings(
    item_number: str,
    start_date: str,
    end_date: str,
    cik_set: set[str] | None = None,
    max_hits: int = 500,
) -> list[dict[str, Any]]:
    """
    Search for 8-K filings containing a specific item number.

    Args:
        item_number: e.g. "5.02", "2.01", "8.01"
        start_date / end_date: "YYYY-MM-DD" strings
        cik_set: if provided, only return filings from these CIKs
        max_hits: maximum results to collect (paginates)

    Returns list of filing dicts with keys:
        {entity_name, cik, accession_no, file_date, form, url}
    """
    results: list[dict] = []
    from_offset = 0
    page_size = 100

    query = f'"Item {item_number}"'

    while True:
        params = {
            "q": query,
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "forms": "8-K",
            "from": str(from_offset),
        }

        url = EDGAR_EFTS_URL + "?" + urllib.parse.urlencode(params)
        efts_headers = {
            "User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu",
            "Host": "efts.sec.gov",
        }

        try:
            data = _get(url, headers=efts_headers)
        except Exception as e:
            logger.error("EFTS search failed for item %s: %s", item_number, e)
            break

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})

            # _id format: "XXXXXXXXXX-YY-NNNNNN:filename.htm"
            # Strip filename suffix after ':' to get the accession number
            filing_id = hit.get("_id", "")
            accession_no = filing_id.split(":")[0] if ":" in filing_id else filing_id

            # CIK is the first hyphen-delimited segment (10 digits with leading zeros)
            id_parts = accession_no.split("-")
            cik_padded = id_parts[0].zfill(10) if id_parts else "0000000000"
            cik_raw = cik_padded.lstrip("0") or "0"

            # _source.ciks may also carry CIKs (use as fallback)
            src_ciks = src.get("ciks", [])
            if not cik_raw and src_ciks:
                cik_padded = str(src_ciks[0]).zfill(10)
                cik_raw = cik_padded.lstrip("0") or "0"

            if cik_set and cik_padded not in cik_set:
                continue

            # Entity name from display_names list
            display_names = src.get("display_names", [])
            if display_names and isinstance(display_names[0], dict):
                entity_name = display_names[0].get("name", "")
            elif display_names:
                entity_name = str(display_names[0])
            else:
                entity_name = src.get("entity_name", "")

            filing_date = src.get("file_date", "")
            # Use adsh (clean accession) from _source if available
            adsh = src.get("adsh", accession_no)

            acc_clean = adsh.replace("-", "")
            doc_url = (
                f"{EDGAR_ARCHIVES}/{cik_raw}/{acc_clean}/{adsh}-index.htm"
            )

            results.append({
                "entity_name": entity_name,
                "cik": cik_padded,
                "cik_raw": cik_raw,
                "accession_no": adsh,
                "filing_date": filing_date,
                "item_number": item_number,
                "index_url": doc_url,
            })

        total_val = data.get("hits", {}).get("total", {})
        total_count = total_val.get("value", 0) if isinstance(total_val, dict) else int(total_val or 0)
        from_offset += page_size
        if from_offset >= min(total_count, max_hits):
            break

    return results


# ---------------------------------------------------------------------------
# Submissions API — filer metadata
# ---------------------------------------------------------------------------

def get_company_metadata(cik: str) -> dict[str, Any]:
    """
    Fetch company metadata from EDGAR submissions API.

    Returns dict with keys: name, tickers, exchanges, sic, stateOfIncorporation,
    stateOfIncorporationDescription, category, fiscalYearEnd, addresses.
    """
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_SUBMISSIONS}/CIK{cik_padded}.json"
    try:
        data = _get(url, headers=_SUBMISSIONS_HEADERS)
        return {
            "name": data.get("name", ""),
            "tickers": data.get("tickers", []),
            "exchanges": data.get("exchanges", []),
            "sic": str(data.get("sic", "")),
            "sic_description": data.get("sicDescription", ""),
            "state_of_incorporation": data.get("stateOfIncorporation", ""),
            "category": data.get("category", ""),
            "fiscal_year_end": data.get("fiscalYearEnd", ""),
        }
    except Exception as e:
        logger.warning("Failed to fetch metadata for CIK %s: %s", cik, e)
        return {}


# ---------------------------------------------------------------------------
# 8-K document fetching and item extraction
# ---------------------------------------------------------------------------

def get_8k_filing_index(cik_raw: str, accession_no: str) -> list[dict[str, str]]:
    """
    Fetch the filing index page and return a list of documents:
    [{description, document, type, size}]
    """
    acc_clean = accession_no.replace("-", "")
    url = (
        f"{EDGAR_ARCHIVES}/{cik_raw}/{acc_clean}/"
        f"{accession_no}-index.json"
    )
    try:
        data = _get(url, headers={"User-Agent": "StrategicEventTracker/1.0 research@youruniversity.edu"})
        return data.get("directory", {}).get("item", [])
    except Exception as e:
        logger.warning("Index fetch failed for %s/%s: %s", cik_raw, accession_no, e)
        return []


def fetch_8k_text(cik_raw: str, accession_no: str) -> str:
    """
    Fetch the primary 8-K document text (the .htm or .txt primary doc).
    Returns empty string on failure.
    """
    documents = get_8k_filing_index(cik_raw, accession_no)
    primary_url = None

    for doc in documents:
        doc_type = doc.get("type", "").upper()
        name = doc.get("name", "")
        if doc_type in ("8-K", "8-K/A") or name.lower().endswith((".htm", ".html")):
            acc_clean = accession_no.replace("-", "")
            primary_url = f"{EDGAR_ARCHIVES}/{cik_raw}/{acc_clean}/{name}"
            break

    if not primary_url:
        # Fallback: use the .txt complete submission
        acc_clean = accession_no.replace("-", "")
        primary_url = f"{EDGAR_ARCHIVES}/{cik_raw}/{acc_clean}/{accession_no}.txt"

    try:
        return _get_text(primary_url)
    except Exception as e:
        logger.warning("8-K text fetch failed: %s", e)
        return ""


def extract_item_text(full_text: str, item_number: str, max_chars: int = 3000) -> str:
    """
    Extract the text section for a given Item number from 8-K full text.
    Returns up to max_chars characters of the relevant section.
    """
    # Try various patterns for "Item X.XX" in SEC filings
    escaped_item = re.escape(item_number)
    escaped_item_dots = re.escape(item_number.replace(".", r"\."))
    patterns = [
        rf"Item\s+{escaped_item}[\.\s]",
        rf"ITEM\s+{escaped_item}[\.\s]",
        rf"Item\s+{escaped_item_dots}",
    ]

    start = -1
    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            start = m.start()
            break

    if start == -1:
        # Couldn't find the item; return beginning of document
        return full_text[:max_chars].strip()

    section = full_text[start: start + max_chars * 2]

    # Find the next "Item X" to truncate
    next_item = re.search(r"Item\s+\d+\.\d+", section[50:], re.IGNORECASE)
    if next_item:
        section = section[: 50 + next_item.start()]

    # Strip HTML tags
    section = re.sub(r"<[^>]+>", " ", section)
    section = re.sub(r"\s+", " ", section).strip()

    return section[:max_chars]


# ---------------------------------------------------------------------------
# High-level event fetcher
# ---------------------------------------------------------------------------

def fetch_events_for_type(
    event_type_key: str,
    start_date: str,
    end_date: str,
    sp500_ciks: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Fetch 8-K filings for a given event type and return structured event dicts.

    Each dict includes: {entity_name, cik, ticker, filing_date, item_number,
    index_url, item_text, event_type_key}
    """
    evt_type = EVENT_TYPES[event_type_key]

    # Build reverse CIK → ticker map
    cik_to_ticker = {v: k for k, v in sp500_ciks.items()}
    cik_set = set(sp500_ciks.values())

    events: list[dict] = []

    for item_no in evt_type.edgar_items:
        logger.info("Searching EDGAR: event_type=%s, item=%s, %s→%s",
                    event_type_key, item_no, start_date, end_date)
        filings = search_8k_filings(
            item_number=item_no,
            start_date=start_date,
            end_date=end_date,
            cik_set=cik_set,
        )

        for f in filings:
            cik = f["cik"]
            if cik not in cik_set:
                continue

            ticker = cik_to_ticker.get(cik, "")
            text = fetch_8k_text(f["cik_raw"], f["accession_no"])
            item_text = extract_item_text(text, item_no)

            # For Item 8.01, apply keyword screening for foreign events
            if item_no == "8.01":
                kw_lower = item_text.lower()
                matched = any(kw in kw_lower for kw in evt_type.keywords)
                if not matched:
                    continue  # skip — doesn't look like the right type

            events.append({
                "entity_name": f["entity_name"],
                "cik": cik,
                "cik_raw": f["cik_raw"],
                "ticker": ticker,
                "filing_date": f["filing_date"],
                "item_number": item_no,
                "accession_no": f["accession_no"],
                "index_url": f["index_url"],
                "item_text": item_text,
                "event_type_key": event_type_key,
                "source_type": "edgar",
            })

        logger.info("  → %d filings collected", len(events))

    return events


# ---------------------------------------------------------------------------
# S&P 500 ticker list (canonical — update quarterly)
# ---------------------------------------------------------------------------

_SP500_TICKERS: set[str] = {
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A",
    "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL",
    "GOOG", "MO", "AMZN", "AMCR", "AEE", "AAL", "AEP", "AXP", "AIG", "AMT",
    "AWK", "AMP", "AME", "AMGN", "APH", "ADI", "ANSS", "AON", "APA", "APO",
    "AAPL", "AMAT", "APTV", "ACGL", "ADM", "ANET", "AJG", "AIZ", "T", "ATO",
    "ADSK", "ADP", "AZO", "AVB", "AVY", "AXON", "BKR", "BALL", "BAC", "BAX",
    "BDX", "BRK.B", "BBY", "TECH", "BIIB", "BLK", "BX", "BK", "BA", "BKNG",
    "BWA", "BSX", "BMY", "AVGO", "BR", "BRO", "BF.B", "BLDR", "BG", "CDNS",
    "CZR", "CPT", "CPB", "COF", "CAH", "KMX", "CCL", "CARR", "CAT", "CBOE",
    "CBRE", "CDW", "CE", "COR", "CNC", "CNP", "CF", "CRL", "SCHW", "CHTR",
    "CVX", "CMG", "CB", "CHD", "CI", "CINF", "CTAS", "CSCO", "C", "CFG",
    "CLX", "CME", "CMS", "KO", "CTSH", "CL", "CMCSA", "CAG", "COP", "ED",
    "STZ", "CEG", "COO", "CPRT", "GLW", "CPAY", "CTVA", "CSGP", "COST",
    "CTRA", "CRWD", "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DVA", "DAY",
    "DE", "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR", "DFS", "DG", "DLTR",
    "D", "DPZ", "DOV", "DOW", "DHI", "DTE", "DUK", "DD", "EMN", "ETN",
    "EBAY", "ECL", "EIX", "EW", "EA", "ELV", "EMR", "ENPH", "ETR", "EOG",
    "EPAM", "EQT", "EFX", "EQIX", "EQR", "ESS", "EL", "ETSY", "EG", "EVRST",
    "ES", "EXC", "EXPE", "EXPD", "EXR", "XOM", "FFIV", "FDS", "FICO", "FAST",
    "FRT", "FDX", "FIS", "FITB", "FSLR", "FE", "FI", "FLT", "FMC", "F",
    "FTNT", "FTV", "FOXA", "FOX", "BEN", "FCX", "GRMN", "IT", "GE", "GEHC",
    "GEV", "GEN", "GNRC", "GD", "GIS", "GM", "GPC", "GILD", "GPN", "GL",
    "GDDY", "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY", "HES",
    "HPE", "HLT", "HOLX", "HD", "HON", "HRL", "HST", "HWM", "HPQ", "HUBB",
    "HUM", "HBAN", "HII", "IBM", "IEX", "IDXX", "ITW", "INCY", "IR", "PODD",
    "INTC", "ICE", "IFF", "IP", "IPG", "INTU", "ISRG", "IVZ", "INVH", "IQV",
    "IRM", "JBHT", "JBL", "JKHY", "J", "JNJ", "JCI", "JPM", "JNPR", "K",
    "KVUE", "KDP", "KEY", "KEYS", "KMB", "KIM", "KMI", "KKR", "KLAC", "KHC",
    "KR", "LHX", "LH", "LRCX", "LW", "LVS", "LDOS", "LEN", "LLY", "LIN",
    "LYV", "LKQ", "LMT", "L", "LOW", "LULU", "LYB", "MTB", "MRO", "MPC",
    "MKTX", "MAR", "MMC", "MLM", "MAS", "MA", "MTCH", "MKC", "MCD", "MCK",
    "MDT", "MRK", "META", "MET", "MTD", "MGM", "MCHP", "MU", "MSFT", "MAA",
    "MRNA", "MHK", "MOH", "TAP", "MDLZ", "MPWR", "MNST", "MCO", "MS", "MOS",
    "MSI", "MSCI", "NDAQ", "NTAP", "NFLX", "NEM", "NWSA", "NWS", "NEE", "NKE",
    "NI", "NDSN", "NSC", "NTRS", "NOC", "NCLH", "NRG", "NUE", "NVDA", "NVR",
    "NXPI", "ORLY", "OXY", "ODFL", "OMC", "ON", "OKE", "ORCL", "OTIS", "PCAR",
    "PKG", "PLTR", "PANW", "PARA", "PH", "PAYX", "PAYC", "PYPL", "PNR", "PEP",
    "PFE", "PCG", "PM", "PSX", "PNW", "PNC", "POOL", "PPG", "PPL", "PFG",
    "PG", "PGR", "PLD", "PRU", "PEG", "PTC", "PSA", "PHM", "QRVO", "PWR",
    "QCOM", "DGX", "RL", "RJF", "RTX", "O", "REG", "REGN", "RF", "RSG",
    "RMD", "RVTY", "ROK", "ROL", "ROP", "ROST", "RCL", "SPGI", "CRM", "SBAC",
    "SLB", "STX", "SRE", "NOW", "SHW", "SPG", "SWKS", "SJM", "SW", "SNA",
    "SOLV", "SO", "LUV", "SWK", "SBUX", "STT", "STLD", "STE", "SYK", "SMCI",
    "SYF", "SNPS", "SYY", "TMUS", "TROW", "TTWO", "TPR", "TRGP", "TGT",
    "TEL", "TDY", "TFX", "TER", "TSLA", "TXN", "TXT", "TMO", "TJX", "TSCO",
    "TT", "TDG", "TRV", "TRMB", "TFC", "TYL", "TSN", "USB", "UBER", "UDR",
    "ULTA", "UNP", "UAL", "UPS", "URI", "UNH", "UHS", "VLO", "VTR", "VLTO",
    "VRSN", "VRSK", "VZ", "VRTX", "VTRS", "VICI", "V", "VST", "VMC", "WRB",
    "GWW", "WAB", "WBA", "WMT", "DIS", "WBD", "WM", "WAT", "WEC", "WFC",
    "WELL", "WST", "WDC", "WY", "WMB", "WTW", "WDAY", "WYNN", "XEL", "XYL",
    "YUM", "ZBRA", "ZBH", "ZTS",
}

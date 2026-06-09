"""SEC EDGAR filings lookup and dilution-risk flagging.

We use the official EDGAR APIs:
  * ticker → CIK mapping from www.sec.gov/files/company_tickers.json
  * recent submissions from data.sec.gov/submissions/CIK#########.json

EDGAR requires a real contact in the User-Agent. Set SEC_USER_AGENT.

Dilution heuristic: any S-1, S-3, S-3ASR, 424B*, or F-3 filing within
the lookback window flags the ticker as dilution-risk. ATM language
in 8-K filings ("at-the-market", "offering") also counts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

import requests

from src.config import SETTINGS

log = logging.getLogger(__name__)

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

DILUTIVE_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "F-3", "F-3/A"}
PROSPECTUS_FORM_PREFIXES = ("424B",)  # 424B1 .. 424B5 etc.
SHELF_DRAW_FORMS = {"FWP"}
ATM_REGEX = re.compile(r"\b(at[- ]the[- ]market|atm offering|equity distribution)\b", re.I)

# Manual cache: lru_cache would poison the cache with an empty dict on
# the first failed network call and never retry within the process.
_TICKER_MAP_CACHE: dict[str, str] | None = None


@dataclass(frozen=True)
class Filing:
    ticker: str
    form: str
    filed: date
    accession: str
    description: str
    is_dilutive: bool


def _headers() -> dict[str, str]:
    return {"User-Agent": SETTINGS.sec_user_agent, "Accept": "application/json"}


def _ticker_to_cik_map() -> dict[str, str]:
    """Return {TICKER: 10-digit zero-padded CIK}.

    Cached *only on success*. A failed fetch returns an empty dict but
    leaves the cache unset so the next call will retry.
    """
    global _TICKER_MAP_CACHE
    if _TICKER_MAP_CACHE is not None:
        return _TICKER_MAP_CACHE
    try:
        r = requests.get(EDGAR_TICKERS_URL, headers=_headers(), timeout=SETTINGS.http_timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("EDGAR ticker map fetch failed: %s", e)
        return {}
    out: dict[str, str] = {}
    for row in data.values():
        try:
            out[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
        except (KeyError, TypeError, AttributeError):
            continue
    _TICKER_MAP_CACHE = out
    return out


def _reset_ticker_map_cache() -> None:
    """Test helper — clear the manual cache."""
    global _TICKER_MAP_CACHE
    _TICKER_MAP_CACHE = None


def _is_dilutive(form: str, description: str = "") -> bool:
    form_u = form.upper()
    if form_u in DILUTIVE_FORMS:
        return True
    if any(form_u.startswith(p) for p in PROSPECTUS_FORM_PREFIXES):
        return True
    if form_u in SHELF_DRAW_FORMS and ATM_REGEX.search(description or ""):
        return True
    return False


def fetch_recent_filings(
    ticker: str, lookback_days: int = 30, max_filings: int = 25
) -> list[Filing]:
    """Recent filings for a ticker, newest first, capped at max_filings."""
    cik = _ticker_to_cik_map().get(ticker.upper())
    if not cik:
        return []
    try:
        r = requests.get(
            EDGAR_SUBMISSIONS_URL.format(cik=cik),
            headers=_headers(),
            timeout=SETTINGS.http_timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("EDGAR submissions fetch failed for %s: %s", ticker, e)
        return []

    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    descs = recent.get("primaryDocDescription", [])
    cutoff = date.today() - timedelta(days=lookback_days)

    out: list[Filing] = []
    for form, filed, acc, desc in zip(forms, dates, accs, descs):
        try:
            filed_d = datetime.strptime(filed, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if filed_d < cutoff:
            continue
        out.append(
            Filing(
                ticker=ticker.upper(),
                form=form,
                filed=filed_d,
                accession=acc,
                description=desc or "",
                is_dilutive=_is_dilutive(form, desc or ""),
            )
        )
        if len(out) >= max_filings:
            break
    return out


def fetch_many(tickers: Iterable[str], lookback_days: int = 30) -> dict[str, list[Filing]]:
    return {t.upper(): fetch_recent_filings(t, lookback_days=lookback_days) for t in tickers}


def dilution_risk_summary(filings: list[Filing]) -> dict:
    """Return a small dict the scorer + reporter can consume."""
    dilutive = [f for f in filings if f.is_dilutive]
    return {
        "any_dilutive": bool(dilutive),
        "count": len(dilutive),
        "most_recent_form": dilutive[0].form if dilutive else None,
        "most_recent_date": dilutive[0].filed.isoformat() if dilutive else None,
    }

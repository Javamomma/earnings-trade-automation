"""Reddit ticker-mention scanner.

Uses Reddit's public JSON endpoints (no auth) — sufficient for ticker
mention counting. A polite User-Agent is still required.

We compute, per ticker:
  * fresh_mentions:    count in the last `fresh_window_hours`
  * baseline_mentions: average per-`fresh_window_hours` over the
                       `baseline_window_hours` lookback
  * acceleration:      fresh / max(baseline, epsilon) — the headline
                       "is mention rate spiking" number consumed by
                       scoring.py
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests

from src.config import SETTINGS, SubredditCfg

log = logging.getLogger(__name__)

# $TICKER or bare TICKER with 1-5 uppercase letters. Avoid common false
# positives like "I", "A", "DD", "CEO", "ETF". Two letters are common
# enough to be noisy so we require either a leading $ or context.
CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
BARE = re.compile(r"\b([A-Z]{2,5})\b")
STOPLIST = {
    "I", "A", "THE", "AND", "OR", "FOR", "TO", "ON", "IN", "IS", "IT", "BE",
    "DD", "TLDR", "USD", "EUR", "GBP", "PM", "AM", "ET", "ETF", "CEO", "CFO",
    "COO", "IPO", "NYSE", "NASDAQ", "ATH", "OTM", "ITM", "FOMO", "YOLO",
    "WSB", "AI", "ML", "FY", "Q1", "Q2", "Q3", "Q4", "EPS", "PE", "PS",
    "MM", "BN", "TR", "EOD", "EOW", "EOM", "SPY", "QQQ",  # exclude indices
}


@dataclass(frozen=True)
class Mention:
    subreddit: str
    title: str
    permalink: str
    created_utc: datetime
    ticker: str


_OAUTH_TOKEN: str | None = None
_OAUTH_TRIED = False


def _get_oauth_token() -> str | None:
    """App-only OAuth token (client_credentials) for a reddit 'script' app.

    Reddit now 403s unauthenticated .json scraping, so an app credential is
    required. Cached per process. Returns None if creds are unset or the call
    fails (callers then degrade to empty mentions).
    """
    global _OAUTH_TOKEN, _OAUTH_TRIED
    if _OAUTH_TOKEN or _OAUTH_TRIED:
        return _OAUTH_TOKEN
    _OAUTH_TRIED = True
    cid, secret = SETTINGS.reddit_client_id, SETTINGS.reddit_client_secret
    if not cid or not secret:
        return None
    try:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": SETTINGS.reddit_user_agent},
            timeout=SETTINGS.http_timeout,
        )
        r.raise_for_status()
        _OAUTH_TOKEN = r.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        log.warning("reddit OAuth token fetch failed: %s", e)
        _OAUTH_TOKEN = None
    return _OAUTH_TOKEN


def _fetch_subreddit_listing(subreddit: str, listing: str = "new", limit: int = 100) -> list[dict]:
    token = _get_oauth_token()
    headers = {"User-Agent": SETTINGS.reddit_user_agent}
    if token:
        url = f"https://oauth.reddit.com/r/{subreddit}/{listing}?limit={limit}"
        headers["Authorization"] = f"bearer {token}"
    else:
        # Unauthenticated fallback — reddit usually 403s this now. Set
        # REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (free script app) to fix.
        url = f"https://www.reddit.com/r/{subreddit}/{listing}.json?limit={limit}"
    try:
        r = requests.get(url, headers=headers, timeout=SETTINGS.http_timeout)
        if r.status_code == 429:
            log.warning("Rate limited on r/%s; backing off 5s", subreddit)
            time.sleep(5)
            r = requests.get(url, headers=headers, timeout=SETTINGS.http_timeout)
        r.raise_for_status()
        return [c["data"] for c in r.json().get("data", {}).get("children", [])]
    except Exception as e:
        log.warning("reddit fetch r/%s failed: %s", subreddit, e)
        return []


def _extract_tickers(text: str, universe: set[str]) -> set[str]:
    hits: set[str] = set()
    for m in CASHTAG.finditer(text or ""):
        sym = m.group(1).upper()
        if sym in universe:
            hits.add(sym)
    for m in BARE.finditer(text or ""):
        sym = m.group(1).upper()
        if sym in STOPLIST:
            continue
        if sym in universe:
            hits.add(sym)
    return hits


def scan_mentions(
    universe: Iterable[str],
    subreddits: Iterable[SubredditCfg],
    lookback_hours: int,
) -> list[Mention]:
    universe_set = {t.upper() for t in universe}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    out: list[Mention] = []
    for sub in subreddits:
        posts = _fetch_subreddit_listing(sub.name, listing="new", limit=100)
        for p in posts:
            try:
                created = datetime.fromtimestamp(p["created_utc"], tz=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            if created < cutoff:
                continue
            body = " ".join(filter(None, [p.get("title", ""), p.get("selftext", "")]))
            for sym in _extract_tickers(body, universe_set):
                out.append(
                    Mention(
                        subreddit=sub.name,
                        title=p.get("title", "")[:280],
                        permalink="https://www.reddit.com" + p.get("permalink", ""),
                        created_utc=created,
                        ticker=sym,
                    )
                )
    return out


@dataclass(frozen=True)
class TickerMentions:
    ticker: str
    fresh: int
    baseline_per_window: float
    acceleration: float
    weighted_score: float
    subreddits: tuple[str, ...]
    sample_titles: tuple[str, ...]


def summarize_mentions(
    universe: Iterable[str],
    subreddits: Iterable[SubredditCfg],
    fresh_window_hours: int,
    baseline_window_hours: int,
) -> dict[str, TickerMentions]:
    subs = list(subreddits)
    sub_weights = {s.name: s.weight for s in subs}
    all_mentions = scan_mentions(
        universe, subs, lookback_hours=baseline_window_hours
    )
    now = datetime.now(timezone.utc)
    fresh_cutoff = now - timedelta(hours=fresh_window_hours)

    by_ticker: dict[str, list[Mention]] = defaultdict(list)
    for m in all_mentions:
        by_ticker[m.ticker].append(m)

    out: dict[str, TickerMentions] = {}
    windows_in_baseline = max(baseline_window_hours / fresh_window_hours, 1.0)
    for ticker in {t.upper() for t in universe}:
        ms = by_ticker.get(ticker, [])
        fresh_ms = [m for m in ms if m.created_utc >= fresh_cutoff]
        baseline_per_window = max((len(ms) - len(fresh_ms)) / windows_in_baseline, 0.0)
        acceleration = (
            len(fresh_ms) / baseline_per_window if baseline_per_window > 0 else float(len(fresh_ms))
        )
        weighted = sum(sub_weights.get(m.subreddit, 1.0) for m in fresh_ms)
        out[ticker] = TickerMentions(
            ticker=ticker,
            fresh=len(fresh_ms),
            baseline_per_window=baseline_per_window,
            acceleration=acceleration,
            weighted_score=weighted,
            subreddits=tuple(sorted({m.subreddit for m in fresh_ms})),
            sample_titles=tuple(m.title for m in fresh_ms[:3]),
        )
    return out

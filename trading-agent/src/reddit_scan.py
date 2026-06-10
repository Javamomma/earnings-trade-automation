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


def _fetch_subreddit_listing(
    subreddit: str,
    listing: str = "new",
    limit: int = 100,
    max_pages: int = 5,
    oldest_needed: datetime | None = None,
) -> list[dict]:
    """Fetch up to ``max_pages`` pages of a subreddit listing.

    A single page (100 posts) covers only a few hours on busy subs like
    r/wallstreetbets, which silently truncated the 72h baseline window
    and inflated every acceleration ratio. We paginate with Reddit's
    ``after`` cursor and stop early once posts are older than
    ``oldest_needed``.
    """
    headers = {"User-Agent": SETTINGS.reddit_user_agent}
    out: list[dict] = []
    after: str | None = None
    for page in range(max_pages):
        url = f"https://www.reddit.com/r/{subreddit}/{listing}.json?limit={limit}"
        if after:
            url += f"&after={after}"
        try:
            r = requests.get(url, headers=headers, timeout=SETTINGS.http_timeout)
            if r.status_code == 429:
                log.warning("Rate limited on r/%s; backing off 5s", subreddit)
                time.sleep(5)
                r = requests.get(url, headers=headers, timeout=SETTINGS.http_timeout)
            r.raise_for_status()
            data = r.json().get("data", {})
        except Exception as e:
            log.warning("reddit fetch r/%s page %d failed: %s", subreddit, page, e)
            break
        children = [c["data"] for c in data.get("children", [])]
        if not children:
            break
        out.extend(children)
        if oldest_needed is not None:
            try:
                oldest_seen = datetime.fromtimestamp(
                    children[-1]["created_utc"], tz=timezone.utc
                )
                if oldest_seen < oldest_needed:
                    break
            except (KeyError, TypeError, ValueError):
                pass
        after = data.get("after")
        if not after:
            break
        time.sleep(1)  # politeness between pages
    return out


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
        posts = _fetch_subreddit_listing(
            sub.name, listing="new", limit=100, oldest_needed=cutoff
        )
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


def compute_acceleration(
    fresh_count: int,
    older_count: int,
    fresh_window_hours: int,
    baseline_window_hours: int,
) -> tuple[float, float]:
    """Return (baseline_per_window, acceleration). Pure, for tests.

    The baseline period excludes the fresh window: with a 6h fresh
    window inside a 72h lookback, older mentions span 66h = 11 windows
    (not 12 — the old divisor systematically inflated acceleration).
    """
    non_fresh_hours = max(baseline_window_hours - fresh_window_hours, fresh_window_hours)
    windows = non_fresh_hours / fresh_window_hours
    baseline_per_window = max(older_count / windows, 0.0)
    if baseline_per_window > 0:
        acceleration = fresh_count / baseline_per_window
    else:
        acceleration = float(fresh_count)
    return baseline_per_window, acceleration


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
    for ticker in {t.upper() for t in universe}:
        ms = by_ticker.get(ticker, [])
        fresh_ms = [m for m in ms if m.created_utc >= fresh_cutoff]
        baseline_per_window, acceleration = compute_acceleration(
            fresh_count=len(fresh_ms),
            older_count=len(ms) - len(fresh_ms),
            fresh_window_hours=fresh_window_hours,
            baseline_window_hours=baseline_window_hours,
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

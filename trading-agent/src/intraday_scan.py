"""Intraday refresh: cheaper than the morning brief.

Skips SEC EDGAR (filings rarely change intraday) and only re-runs the
price snapshot + Reddit scan. Pushes a short Discord alert when any
ticker crosses ``priority_alert_threshold`` *and* its priority has
risen since the last morning brief.

Run from the ``trading-agent/`` directory:

    python -m src.intraday_scan
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date

from src.alerts import send_discord
from src.config import SETTINGS, load_watchlists
from src.prices import fetch_many as fetch_prices
from src.reddit_scan import summarize_mentions
from src.scoring import score_ticker
from src.trade_journal import _connect  # internal helper reused

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def _last_morning_scores(brief_date: date) -> dict[str, float]:
    try:
        with _connect() as conn:
            cur = conn.execute(
                "SELECT ticker, MAX(research_priority) FROM score_history "
                "WHERE brief_date = ? GROUP BY ticker",
                (brief_date.isoformat(),),
            )
            return {row[0]: float(row[1]) for row in cur.fetchall()}
    except sqlite3.Error as e:
        log.warning("score history read failed: %s", e)
        return {}


def main() -> int:
    wl = load_watchlists()
    today = date.today()
    morning = _last_morning_scores(today)

    universe = wl.all_tickers
    prices = fetch_prices(universe)
    mentions = summarize_mentions(
        universe=universe,
        subreddits=wl.subreddits,
        fresh_window_hours=wl.fresh_window_hours,
        baseline_window_hours=wl.baseline_window_hours,
    )

    alerts: list[str] = []
    for ticker in universe:
        snap = prices.get(ticker)
        ment = mentions.get(ticker)
        tags: tuple[str, ...] = ()
        for source in (wl.core, wl.radar):
            for line in source:
                if line.ticker.upper() == ticker:
                    tags = line.tags
        score = score_ticker(
            pct_change=getattr(snap, "pct_change", None),
            relative_volume=getattr(snap, "relative_volume", None),
            reddit_acceleration=ment.acceleration if ment else 0.0,
            reddit_weighted=ment.weighted_score if ment else 0.0,
            dilution_summary={},  # skip SEC intraday
            tags=tags,
        )
        prior = morning.get(ticker)
        crossed = score.research_priority >= SETTINGS.priority_alert_threshold
        rising = prior is None or score.research_priority > prior + 5
        if crossed and rising:
            alerts.append(
                f"• `{ticker}` p={score.research_priority:.0f}"
                + (f" (Δ from morning {score.research_priority - prior:+.1f})" if prior is not None else "")
            )

    if alerts:
        send_discord(
            f"**Intraday signal — {today.isoformat()}**\n" + "\n".join(alerts)
        )
        log.info("Sent %d intraday alerts", len(alerts))
    else:
        log.info("No intraday alerts to send.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("Intraday scan failed")
        sys.exit(1)

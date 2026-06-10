"""Entry point: produce the daily morning brief.

Run from the ``trading-agent/`` directory:

    python -m src.generate_brief

Steps:
  1. Load watchlists.
  2. Fetch prices, SEC filings, Reddit mentions.
  3. Score each ticker.
  4. Render Markdown, write file, journal, alert.

Nothing here ever calls a broker.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import date

from src.alerts import send_discord
from src.buy_zones import evaluate_zones, triggered_zones
from src.config import SETTINGS, Watchlists, load_watchlists
from src.portfolio import load_buy_zones
from src.prices import fetch_many as fetch_prices
from src.proposals import list_open, propose
from src.reddit_scan import summarize_mentions
from src.reporting import (
    TickerLine,
    render_brief,
    render_discord_summary,
    write_brief,
)
from src.scoring import score_ticker
from src.sec_filings import dilution_risk_summary, fetch_many as fetch_filings
from src.trade_journal import record_brief_run, record_scores

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _ticker_metadata(wl: Watchlists, ticker: str) -> tuple[bool, tuple[str, ...], str]:
    for source, is_core in ((wl.core, True), (wl.radar, False)):
        for line in source:
            if line.ticker.upper() == ticker.upper():
                return is_core, line.tags, line.notes
    return False, (), ""


def build_lines(wl: Watchlists) -> list[TickerLine]:
    universe = wl.all_tickers
    log.info("Universe: %s", universe)

    prices = fetch_prices(universe)
    filings = fetch_filings(universe, lookback_days=30)
    mentions = summarize_mentions(
        universe=universe,
        subreddits=wl.subreddits,
        fresh_window_hours=wl.fresh_window_hours,
        baseline_window_hours=wl.baseline_window_hours,
    )

    lines: list[TickerLine] = []
    for ticker in universe:
        is_core, tags, notes = _ticker_metadata(wl, ticker)
        snap = prices.get(ticker)
        mlist = filings.get(ticker, [])
        dsum = dilution_risk_summary(mlist)
        ment = mentions.get(ticker)
        score = score_ticker(
            pct_change=getattr(snap, "pct_change", None),
            relative_volume=getattr(snap, "relative_volume", None),
            reddit_acceleration=ment.acceleration if ment else 0.0,
            reddit_weighted=ment.weighted_score if ment else 0.0,
            dilution_summary=dsum,
            tags=tags,
        )
        lines.append(
            TickerLine(
                ticker=ticker,
                tags=tags,
                notes=notes,
                is_core=is_core,
                snapshot=snap,
                mentions=ment,
                filings=mlist,
                dilution_summary=dsum,
                score=score,
            )
        )
    return lines


def filter_for_render(wl: Watchlists, lines: list[TickerLine]) -> list[TickerLine]:
    """Always keep `core` tickers; for `radar`, only those above the
    render threshold."""
    threshold = SETTINGS.priority_render_threshold
    out = []
    for line in lines:
        if line.is_core or line.score.research_priority >= threshold:
            out.append(line)
    return out


def main() -> int:
    today = date.today()
    log.info("Generating brief for %s", today)

    wl = load_watchlists()
    all_lines = build_lines(wl)
    visible = filter_for_render(wl, all_lines)

    body = render_brief(today, visible)
    path = write_brief(today, body)
    log.info("Wrote %s", path)

    summary = {
        "tickers": [l.ticker for l in visible],
        "top_priority": max((l.score.research_priority for l in visible), default=0.0),
    }
    brief_id = record_brief_run(today, path, summary=summary)
    record_scores(
        today,
        [{"ticker": l.ticker, **asdict(l.score)} for l in all_lines],
    )

    # Quality-stock layer: silently check buy zones; any trigger turns
    # into a Proposal in the journal. Discord only pings when triggers
    # fired or research-priority threshold crossed.
    try:
        zones = load_buy_zones()
        if zones:
            checks = evaluate_zones(zones)
            fired = triggered_zones(checks)
            for c in fired:
                propose(
                    ticker=c.zone.ticker,
                    kind="buy_zone_hit",
                    rationale=(
                        f"Buy zone fired: {c.zone.trigger_kind} {c.zone.trigger_value} "
                        f"(observed {c.observed_value:.2f}). {c.zone.rationale}"
                    ),
                    structured={
                        "trigger_kind": c.zone.trigger_kind,
                        "trigger_value": c.zone.trigger_value,
                        "observed_value": c.observed_value,
                        "size_pct_of_portfolio": c.zone.size_pct_of_portfolio,
                    },
                    invalidation=(
                        "Re-verify thesis is intact before sizing in; "
                        "expand only if no dilutive filing in the past 30d."
                    ),
                )
            if fired:
                log.info("Buy-zone triggers fired: %s",
                         ", ".join(c.zone.ticker for c in fired))
    except Exception as e:  # noqa: BLE001
        log.warning("buy-zone check raised: %s", e)

    alert_threshold = SETTINGS.priority_alert_threshold
    alerters = [l for l in visible if l.score.research_priority >= alert_threshold]
    open_props = list_open()
    bits = []
    if alerters:
        bits.append(render_discord_summary(today, alerters))
    if open_props:
        bits.append(
            f"\n**{len(open_props)} open proposal(s)** awaiting review "
            f"— `python -m src.proposals list`"
        )
    if bits:
        send_discord("\n".join(bits))
    else:
        log.info("No alerts to send (threshold %.1f, %d open proposals).",
                 alert_threshold, len(open_props))

    log.info("Brief %d complete", brief_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("Brief generation failed")
        sys.exit(1)

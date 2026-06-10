"""Sunday weekly brief — the 10-minute portfolio review.

Sections:
  1. Portfolio summary — holdings, weights, week-over-week move
  2. Triggered buy zones (new this week)
  3. Open option-income ideas on holdings + cash-secured targets
  4. Filings deltas on holdings (new this week)
  5. Theses approaching invalidation / hit invalidation
  6. Outcome scoring: hit rate by signal combination (the feedback loop)
  7. Open proposals awaiting your decision

The output is one Markdown file plus a Discord summary. The agent
never executes; every proposal sits in the journal until you mark it.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

from src.alerts import send_discord
from src.buy_zones import evaluate_zones, triggered_zones
from src.config import SETTINGS
from src.filings_diff import digest_for
from src.options_income import find_cc_ideas, find_csp_ideas
from src.outcomes import hit_rate_summary, measure_theses, persist
from src.portfolio import load_buy_zones, load_portfolio, zone_tickers
from src.proposals import list_open, propose
from src.theses import check_invalidations, list_active
from src.trade_journal import record_brief_run

log = logging.getLogger(__name__)


def _last_close_and_week(ticker: str) -> tuple[float | None, float | None]:
    try:
        hist = yf.Ticker(ticker).history(period="10d", auto_adjust=False)
        if hist is None or hist.empty:
            return None, None
        last = float(hist["Close"].iloc[-1])
        week_ago = float(hist["Close"].iloc[max(0, len(hist) - 6)])
        return last, ((last - week_ago) / week_ago * 100.0 if week_ago else None)
    except Exception as e:
        log.warning("price/week fetch failed for %s: %s", ticker, e)
        return None, None


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _render_portfolio(portfolio, marks) -> str:
    rows = [
        "| Ticker | Shares | Last | Wk Δ | Cost | Unreal P/L | Weight |",
        "| --- | ---:| ---:| ---:| ---:| ---:| ---:|",
    ]
    total = portfolio.total_equity(marks)
    for h in portfolio.holdings:
        mark = marks.get(h.ticker.upper())
        wk = _wk.get(h.ticker.upper()) if "_wk" in globals() else None
        upnl = ((mark - h.cost_basis) * h.shares) if (mark and h.cost_basis) else None
        upnl_pct = ((mark - h.cost_basis) / h.cost_basis * 100.0) if (mark and h.cost_basis) else None
        weight = (mark * h.shares / total * 100.0) if (mark and total) else None
        rows.append(
            "| {tic} | {sh} | {mk} | {wk} | {cb} | {pl} | {w} |".format(
                tic=h.ticker,
                sh=h.shares,
                mk=f"{mark:.2f}" if mark else "—",
                wk=_fmt_pct(wk),
                cb=f"{h.cost_basis:.2f}" if h.cost_basis else "—",
                pl=(f"${upnl:,.0f} ({_fmt_pct(upnl_pct)})" if upnl is not None else "—"),
                w=f"{weight:.1f}%" if weight else "—",
            )
        )
    rows.append("")
    rows.append(f"**Cash:** ${portfolio.cash:,.2f}  •  **Total equity:** ${total:,.2f}")
    return "\n".join(rows)


def _render_buy_zone_section(checks) -> tuple[str, list]:
    triggered = triggered_zones(checks)
    if not triggered:
        return "_No buy-zone triggers fired this week._", []
    rows = ["| Ticker | Trigger | Observed | Suggested size |", "| --- | --- | --- | --- |"]
    for c in triggered:
        rows.append(
            f"| **{c.zone.ticker}** | {c.zone.trigger_kind} {c.zone.trigger_value} "
            f"| {c.observed_value:.2f} | {c.zone.size_pct_of_portfolio*100:.1f}% of book |"
        )
    return "\n".join(rows), triggered


def _render_income_section(ideas_by_kind: dict[str, list]) -> str:
    if not any(ideas_by_kind.values()):
        return "_No qualifying option-income ideas this week._"
    parts: list[str] = []
    for kind, ideas in ideas_by_kind.items():
        if not ideas:
            continue
        label = "Cash-secured puts" if kind == "csp" else "Covered calls"
        parts.append(f"**{label}**")
        rows = [
            "| Ticker | Strike | Exp | DTE | Δ | Prob asn | Mid | Ann yield | Score | Notes |",
            "| --- | ---:| --- | ---:| ---:| ---:| ---:| ---:| ---:| --- |",
        ]
        for i in ideas[:5]:
            rows.append(
                f"| {i.ticker} | {i.strike:.2f} | {i.expiry} | {i.days_to_exp} | "
                f"{i.delta:+.2f} | {i.prob_assignment:.0%} | {i.mid_price:.2f} | "
                f"{i.annual_yield*100:.1f}% | {i.score:.0f} | "
                f"{'⚠earnings' if i.spans_earnings else ''} |"
            )
        parts.append("\n".join(rows))
    return "\n\n".join(parts)


def _render_proposals(open_props: list) -> str:
    if not open_props:
        return "_No open proposals._"
    rows = ["| # | Kind | Ticker | Rationale | Invalidation |",
            "| ---:| --- | --- | --- | --- |"]
    for p in open_props:
        rows.append(
            f"| {p.id} | {p.kind} | **{p.ticker}** | {p.rationale[:80]} | "
            f"{p.invalidation[:60]} |"
        )
    rows.append("")
    rows.append("> Review with: `python -m src.proposals review --id N --action accepted|rejected|deferred --note '...'`")
    return "\n".join(rows)


def _render_hit_rate(stats: list[dict]) -> str:
    if not stats:
        return "_Outcome scorer has not yet accumulated enough data._"
    rows = ["| Kind | Measured | Invalidated | Avg days |",
            "| --- | ---:| ---:| ---:|"]
    for s in stats:
        rows.append(
            f"| {s.get('subject_kind','?')} | {s.get('measured',0)} | "
            f"{s.get('invalidated',0)} | {(s.get('avg_days') or 0):.1f} |"
        )
    return "\n".join(rows)


def main() -> int:
    today = date.today()
    portfolio = load_portfolio()
    zones = load_buy_zones()

    # Marks for everything we touch.
    universe = sorted(set(
        [h.ticker for h in portfolio.holdings]
        + zone_tickers(zones)
    ))
    marks: dict[str, float] = {}
    global _wk
    _wk = {}
    for tk in universe:
        last, wk = _last_close_and_week(tk)
        if last is not None:
            marks[tk] = last
        if wk is not None:
            _wk[tk] = wk

    # 1) Portfolio
    portfolio_md = _render_portfolio(portfolio, marks)

    # 2) Buy zones — propose any triggers
    checks = evaluate_zones(zones)
    bz_md, fired = _render_buy_zone_section(checks)
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
            invalidation="Thesis assumed intact; re-check fundamentals before scaling.",
        )

    # 3) Income ideas — CSPs on cash for zone tickers, CCs on holdings.
    ideas: dict[str, list] = {"csp": [], "cc": []}
    for tk in zone_tickers(zones):
        ideas["csp"].extend(find_csp_ideas(tk))
    for h in portfolio.holdings:
        if h.shares >= 100:
            ideas["cc"].extend(find_cc_ideas(h.ticker))
    ideas["csp"].sort(key=lambda i: i.score, reverse=True)
    ideas["cc"].sort(key=lambda i: i.score, reverse=True)
    income_md = _render_income_section(ideas)
    for kind in ("csp", "cc"):
        for i in ideas[kind][:3]:
            propose(
                ticker=i.ticker, kind=kind,
                rationale=f"{('CSP' if kind=='csp' else 'CC')} {i.strike}@{i.expiry}: {i.rationale}",
                structured={
                    "strike": i.strike, "expiry": i.expiry, "mid": i.mid_price,
                    "annual_yield": i.annual_yield, "prob_assignment": i.prob_assignment,
                    "delta": i.delta, "capital_required": i.capital_required,
                },
                invalidation=(
                    "Roll or close if underlying breaks below buy-zone (CSP) "
                    "or moves >50% of strike (CC); avoid holding through earnings."
                ),
            )

    # 4) Filings deltas — surface MD&A/Risk Factor changes for holdings.
    filings_md_blocks = []
    for h in portfolio.holdings:
        d = digest_for(h.ticker)
        if not d:
            continue
        any_change = any(s.added or s.removed for s in d.sections)
        if not any_change:
            continue
        block = [f"### {h.ticker} — {d.latest.form} filed {d.latest.filed}"]
        for sec in d.sections:
            if not (sec.added or sec.removed):
                continue
            block.append(f"**{sec.section}:** {sec.summary}")
            for p in sec.added[:3]:
                block.append(f"  - + {p[:220]}{'…' if len(p) > 220 else ''}")
        filings_md_blocks.append("\n".join(block))
        propose(
            ticker=h.ticker, kind="filings_delta",
            rationale=f"{d.latest.form} filed {d.latest.filed}: material section changes detected.",
            structured={
                "form": d.latest.form, "filed": d.latest.filed,
                "accession": d.latest.accession,
                "added_words": {s.section: s.added_word_count for s in d.sections},
            },
            invalidation="Re-read the new paragraphs; reassess thesis if risk factors expanded.",
        )
    filings_md = "\n\n".join(filings_md_blocks) if filings_md_blocks else "_No material filings deltas on holdings this week._"

    # 5) Theses — invalidation hits
    invalidation_hits = check_invalidations(marks)
    if invalidation_hits:
        rows = ["| Thesis | Ticker | Direction | Reason |", "| ---:| --- | --- | --- |"]
        for t, reason in invalidation_hits:
            rows.append(f"| #{t.id} | **{t.ticker}** | {t.direction} | {reason} |")
            propose(
                ticker=t.ticker, kind="thesis_invalidation",
                rationale=f"Thesis #{t.id} invalidation criterion fired: {reason}",
                structured={"thesis_id": t.id, "direction": t.direction},
                invalidation="Close or revise; do not let it ride past stated invalidation.",
            )
        theses_md = "\n".join(rows)
    else:
        active = list_active()
        theses_md = (
            f"_No invalidations fired. {len(active)} active theses._"
        )

    # 6) Outcome scoring
    persist(measure_theses())
    hr_md = _render_hit_rate(hit_rate_summary())

    # 7) Open proposals
    open_props = list_open()
    proposals_md = _render_proposals(open_props)

    body_parts = [
        f"# Sunday brief — {today.isoformat()}",
        "",
        "_Agent-generated. **No trades placed.** Every proposal needs your sign-off._",
        "",
        "## 1. Portfolio", portfolio_md, "",
        "## 2. Buy-zone triggers", bz_md, "",
        "## 3. Option-income ideas", income_md, "",
        "## 4. Filings deltas", filings_md, "",
        "## 5. Thesis status", theses_md, "",
        "## 6. Hit-rate summary", hr_md, "",
        "## 7. Open proposals", proposals_md,
    ]
    body = "\n".join(body_parts) + "\n"

    out_dir = SETTINGS.reports_path() / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}-sunday-brief.md"
    out_path.write_text(body)
    log.info("Wrote %s", out_path)

    record_brief_run(today, out_path, summary={"kind": "weekly", "open_proposals": len(open_props)})

    if fired or invalidation_hits or open_props:
        bits = [
            f"**Sunday brief — {today.isoformat()}**",
            f"• {len(fired)} buy-zone triggers" if fired else None,
            f"• {len(invalidation_hits)} thesis invalidations" if invalidation_hits else None,
            f"• {len(open_props)} open proposals awaiting review" if open_props else None,
        ]
        send_discord("\n".join(b for b in bits if b))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        sys.exit(main())
    except Exception:
        log.exception("Weekly brief failed")
        sys.exit(1)

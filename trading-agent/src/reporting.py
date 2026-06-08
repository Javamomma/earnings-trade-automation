"""Daily Markdown brief writer.

Output goes to ``reports/daily/YYYY-MM-DD-morning-brief.md``. When
``REPORT_STYLE=obsidian`` we emit YAML frontmatter and [[wikilinks]]
for tickers so the file is first-class inside an Obsidian vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from src.config import SETTINGS
from src.prices import PriceSnapshot
from src.reddit_scan import TickerMentions
from src.scoring import CompositeScore
from src.sec_filings import Filing


@dataclass(frozen=True)
class TickerLine:
    ticker: str
    tags: tuple[str, ...]
    notes: str
    is_core: bool
    snapshot: PriceSnapshot
    mentions: TickerMentions | None
    filings: list[Filing]
    dilution_summary: dict
    score: CompositeScore


def _ticker_token(ticker: str) -> str:
    return f"[[{ticker}]]" if SETTINGS.report_style == "obsidian" else f"`{ticker}`"


def _fmt(v, fmt: str = "{:.2f}") -> str:
    if v is None:
        return "—"
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_int(v) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}"


def _fmt_cap(v) -> str:
    if v is None:
        return "—"
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if v >= scale:
            return f"${v/scale:.2f}{unit}"
    return f"${v:.0f}"


def _frontmatter(d: date, lines: list[TickerLine]) -> str:
    tags = sorted({t for line in lines for t in line.tags})
    yaml_block = [
        "---",
        f"date: {d.isoformat()}",
        f"generated_at: {datetime.now().isoformat(timespec='seconds')}",
        "type: morning-brief",
        f"tickers: [{', '.join(sorted(l.ticker for l in lines))}]",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
    ]
    return "\n".join(yaml_block)


def _summary_table(lines: list[TickerLine]) -> str:
    rows = [
        "| Ticker | Px | Δ | RelVol | Priority | Notes |",
        "| --- | ---:| ---:| ---:| ---:| --- |",
    ]
    for line in sorted(lines, key=lambda l: l.score.research_priority, reverse=True):
        flags: list[str] = []
        if line.dilution_summary.get("any_dilutive"):
            flags.append("⚠ DIL")
        if line.score.meme >= 50:
            flags.append("🎰 MEME")
        if line.is_core:
            flags.append("★")
        rows.append(
            "| {tok} | {px} | {chg} | {rv} | {pri} | {flags} |".format(
                tok=_ticker_token(line.ticker),
                px=_fmt(line.snapshot.price),
                chg=_fmt_pct(line.snapshot.pct_change),
                rv=_fmt(line.snapshot.relative_volume, "{:.2f}x"),
                pri=f"{line.score.research_priority:.1f}",
                flags=" ".join(flags) or "—",
            )
        )
    return "\n".join(rows)


def _section_for(line: TickerLine) -> str:
    out: list[str] = []
    out.append(f"### {_ticker_token(line.ticker)}")
    if line.tags:
        out.append(f"_tags: {', '.join(line.tags)}_")
    if line.notes:
        out.append(f"> {line.notes}")
    out.append("")

    s = line.snapshot
    out.append(
        f"**Price:** {_fmt(s.price)}  •  **Δ:** {_fmt_pct(s.pct_change)}"
        f"  •  **Volume:** {_fmt_int(s.volume)}  •  **RelVol:** {_fmt(s.relative_volume, '{:.2f}x')}"
        f"  •  **MCap:** {_fmt_cap(s.market_cap)}"
    )
    sc = line.score
    out.append(
        f"**Scores:** momentum {sc.momentum} • reddit {sc.reddit} • "
        f"dilution {sc.dilution} • meme {sc.meme} • **priority {sc.research_priority}**"
    )

    if line.mentions and line.mentions.fresh > 0:
        out.append("")
        out.append(
            f"**Reddit:** {line.mentions.fresh} fresh mentions "
            f"(accel {line.mentions.acceleration:.2f}x, "
            f"weighted {line.mentions.weighted_score:.1f}) "
            f"across {', '.join(line.mentions.subreddits) or '—'}"
        )
        for title in line.mentions.sample_titles:
            out.append(f"  - {title}")

    if line.dilution_summary.get("any_dilutive"):
        out.append("")
        out.append(
            f"**⚠ Dilution flag:** {line.dilution_summary.get('count')} dilutive filings "
            f"in window; most recent {line.dilution_summary.get('most_recent_form')} on "
            f"{line.dilution_summary.get('most_recent_date')}"
        )

    if line.filings:
        out.append("")
        out.append("**Recent filings:**")
        for f in line.filings[:8]:
            marker = "⚠ " if f.is_dilutive else ""
            out.append(f"  - {marker}{f.filed.isoformat()} `{f.form}` — {f.description or '(no description)'}")

    out.append("")
    return "\n".join(out)


def render_brief(d: date, lines: list[TickerLine]) -> str:
    rendered = []
    if SETTINGS.report_style == "obsidian":
        rendered.append(_frontmatter(d, lines))

    rendered.append(f"# Morning brief — {d.isoformat()}")
    rendered.append("")
    rendered.append(
        "_Research-only signals. **No automated trades**. Sorted by research priority._"
    )
    rendered.append("")
    rendered.append("## Summary")
    rendered.append("")
    rendered.append(_summary_table(lines))
    rendered.append("")
    rendered.append("## Details")
    rendered.append("")
    for line in sorted(lines, key=lambda l: l.score.research_priority, reverse=True):
        rendered.append(_section_for(line))
    return "\n".join(rendered).rstrip() + "\n"


def write_brief(d: date, body: str) -> Path:
    out_dir = SETTINGS.reports_path() / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{d.isoformat()}-morning-brief.md"
    out_path.write_text(body)
    return out_path


def render_discord_summary(d: date, lines: Iterable[TickerLine], top_n: int = 5) -> str:
    ranked = sorted(lines, key=lambda l: l.score.research_priority, reverse=True)[:top_n]
    if not ranked:
        return f"**Brief {d.isoformat()}** — no qualifying tickers."
    out = [f"**Brief {d.isoformat()}** — top {len(ranked)} by research priority:"]
    for line in ranked:
        flags = []
        if line.dilution_summary.get("any_dilutive"):
            flags.append("⚠DIL")
        if line.score.meme >= 50:
            flags.append("🎰")
        out.append(
            f"• `{line.ticker}` p={line.score.research_priority:.0f} "
            f"({_fmt_pct(line.snapshot.pct_change)}, "
            f"{_fmt(line.snapshot.relative_volume, '{:.1f}x')} RV) "
            f"{' '.join(flags)}".rstrip()
        )
    return "\n".join(out)

"""Thesis ledger — every position or proposal carries a falsifiable
hypothesis with explicit invalidation criteria.

This is what makes the agent a partner instead of a horoscope: every
recommendation must answer "what would prove me wrong, and when?".
The outcome scorer later checks whether reality confirmed or
invalidated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from src.trade_journal import _connect


@dataclass(frozen=True)
class Thesis:
    id: int | None
    ticker: str
    direction: str             # 'long' | 'short' | 'income'
    setup: str                 # which signals fired (one line)
    thesis: str                # paragraph
    confirms: str              # evidence that would strengthen
    invalidates: str           # plain-English kill criteria
    invalidation_price: float | None
    invalidation_date: str | None      # YYYY-MM-DD
    horizon_days: int | None
    conviction: int            # 1-5
    sizing_hint: str
    status: str = "active"     # 'active' | 'invalidated' | 'realized' | 'closed_manual'
    created_at: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None


def create_thesis(
    ticker: str,
    *,
    direction: str,
    setup: str,
    thesis: str,
    confirms: str = "",
    invalidates: str = "",
    invalidation_price: float | None = None,
    invalidation_date: str | None = None,
    horizon_days: int | None = None,
    conviction: int = 3,
    sizing_hint: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO theses
            (ticker, direction, setup, thesis, confirms, invalidates,
             invalidation_price, invalidation_date, horizon_days,
             conviction, sizing_hint, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, 'active')""",
            (ticker.upper(), direction, setup, thesis, confirms, invalidates,
             invalidation_price, invalidation_date, horizon_days,
             conviction, sizing_hint),
        )
        return cur.lastrowid


def list_active(ticker: str | None = None) -> list[Thesis]:
    with _connect() as conn:
        if ticker:
            cur = conn.execute(
                "SELECT * FROM theses WHERE status = 'active' AND ticker = ? "
                "ORDER BY created_at DESC", (ticker.upper(),))
        else:
            cur = conn.execute(
                "SELECT * FROM theses WHERE status = 'active' "
                "ORDER BY created_at DESC")
        cols = [d[0] for d in cur.description]
        return [_row_to_thesis(dict(zip(cols, row))) for row in cur.fetchall()]


def close_thesis(thesis_id: int, *, reason: str, status: str = "closed_manual") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE theses SET status = ?, closed_at = ?, close_reason = ? "
            "WHERE id = ?",
            (status, datetime.now().isoformat(timespec="seconds"), reason, thesis_id),
        )


def check_invalidations(marks: dict[str, float]) -> list[tuple[Thesis, str]]:
    """Return active theses whose invalidation criteria fired today.

    Currently checks price levels and date horizons. Returns the
    thesis and a one-line reason for each hit. Does NOT mutate state —
    that's a separate explicit ``close_thesis`` call. The agent
    proposes, the human acts.
    """
    out: list[tuple[Thesis, str]] = []
    today = date.today()
    for t in list_active():
        # Date-based invalidation
        if t.invalidation_date:
            try:
                d = datetime.strptime(t.invalidation_date, "%Y-%m-%d").date()
                if today >= d:
                    out.append((t, f"invalidation date {t.invalidation_date} reached"))
                    continue
            except ValueError:
                pass
        # Horizon-based. `horizon_days=0` is legitimate ("expire on
        # creation"); only None means "no horizon set".
        if t.horizon_days is not None and t.created_at:
            try:
                created = datetime.fromisoformat(t.created_at).date()
                if (today - created).days >= t.horizon_days:
                    out.append((t, f"horizon of {t.horizon_days}d elapsed"))
                    continue
            except ValueError:
                pass
        # Price-based
        if t.invalidation_price is not None:
            mark = marks.get(t.ticker.upper())
            if mark is None:
                continue
            if t.direction == "long" and mark <= t.invalidation_price:
                out.append((t, f"price {mark:.2f} ≤ invalidation {t.invalidation_price:.2f}"))
            elif t.direction == "short" and mark >= t.invalidation_price:
                out.append((t, f"price {mark:.2f} ≥ invalidation {t.invalidation_price:.2f}"))
    return out


def _row_to_thesis(d: dict) -> Thesis:
    return Thesis(
        id=d.get("id"),
        ticker=d["ticker"],
        direction=d["direction"],
        setup=d.get("setup") or "",
        thesis=d.get("thesis") or "",
        confirms=d.get("confirms") or "",
        invalidates=d.get("invalidates") or "",
        invalidation_price=d.get("invalidation_price"),
        invalidation_date=d.get("invalidation_date"),
        horizon_days=d.get("horizon_days"),
        conviction=int(d.get("conviction") or 3),
        sizing_hint=d.get("sizing_hint") or "",
        status=d.get("status") or "active",
        created_at=d.get("created_at"),
        closed_at=d.get("closed_at"),
        close_reason=d.get("close_reason"),
    )

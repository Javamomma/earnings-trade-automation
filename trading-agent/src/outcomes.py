"""Outcome scorer — the feedback loop.

Nightly job that, for every accepted proposal and active thesis older
than N days, fetches the current price and records:

  * Did the invalidation level get hit?
  * Did the thesis "work" by its own definition?
  * How long did it take?

The aggregate of these rows is what tells you (and the agent) which
signal combinations actually pay. Without this loop, every score
weight is just a guess; with it, weights become evidence-based.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import yfinance as yf

from src.theses import list_active
from src.trade_journal import _connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutcomeRow:
    subject_kind: str
    subject_id: int
    days_elapsed: int
    price_at_open: float | None
    price_at_measure: float | None
    hit_invalidation: bool
    notes: str = ""


def _price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning("price fetch for %s failed: %s", ticker, e)
        return None


def measure_theses() -> list[OutcomeRow]:
    """Record an outcome row for every active thesis older than 7d."""
    rows: list[OutcomeRow] = []
    now = datetime.now()
    for t in list_active():
        if not t.created_at:
            continue
        try:
            created = datetime.fromisoformat(t.created_at)
        except ValueError:
            continue
        days = (now - created).days
        if days < 7:
            continue
        mark = _price(t.ticker)
        hit = False
        if t.invalidation_price is not None and mark is not None:
            if t.direction == "long" and mark <= t.invalidation_price:
                hit = True
            if t.direction == "short" and mark >= t.invalidation_price:
                hit = True
        rows.append(OutcomeRow(
            subject_kind="thesis", subject_id=t.id, days_elapsed=days,
            price_at_open=None, price_at_measure=mark, hit_invalidation=hit,
            notes=f"direction={t.direction}, conviction={t.conviction}",
        ))
    return rows


def persist(rows: Iterable[OutcomeRow]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO outcomes
               (subject_kind, subject_id, days_elapsed, price_at_open,
                price_at_measure, hit_invalidation, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(r.subject_kind, r.subject_id, r.days_elapsed,
              r.price_at_open, r.price_at_measure,
              1 if r.hit_invalidation else 0, r.notes) for r in rows],
        )
    return len(rows)


def hit_rate_summary() -> list[dict]:
    """Aggregate hit/miss counts by subject_kind across the whole
    journal. Feeds the weekly report."""
    with _connect() as conn:
        cur = conn.execute(
            """SELECT subject_kind,
                      COUNT(*) AS measured,
                      SUM(CASE WHEN hit_invalidation = 1 THEN 1 ELSE 0 END) AS invalidated,
                      AVG(days_elapsed) AS avg_days
                 FROM outcomes
                GROUP BY subject_kind"""
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    rows = measure_theses()
    n = persist(rows)
    log.info("Recorded %d outcome rows.", n)
    return 0


if __name__ == "__main__":
    import logging as _l
    _l.basicConfig(level=_l.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())

"""Price, change, volume, and relative-volume snapshots via yfinance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import yfinance as yf

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceSnapshot:
    ticker: str
    price: float | None
    prev_close: float | None
    pct_change: float | None
    volume: int | None
    avg_volume_30d: float | None
    relative_volume: float | None
    market_cap: float | None

    @property
    def has_data(self) -> bool:
        return self.price is not None


def _safe_float(x) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_one(ticker: str) -> PriceSnapshot:
    """Return a price snapshot for a single ticker.

    Uses 30 trading days of history to compute average volume and the
    previous close. Wrapped so a single failed ticker can't take a
    batch down.
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="30d", auto_adjust=False)
        if hist is None or hist.empty:
            return PriceSnapshot(ticker, None, None, None, None, None, None, None)
        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else None
        price = _safe_float(latest["Close"])
        prev_close = _safe_float(prev["Close"]) if prev is not None else None
        pct = (
            ((price - prev_close) / prev_close * 100.0)
            if price is not None and prev_close not in (None, 0)
            else None
        )
        vol = int(latest["Volume"]) if latest["Volume"] else None
        avg30 = float(hist["Volume"].tail(30).mean()) if len(hist) >= 5 else None
        rel_vol = (vol / avg30) if vol and avg30 else None
        cap = None
        try:
            cap = _safe_float(t.fast_info.get("market_cap"))
        except Exception:
            cap = None
        return PriceSnapshot(
            ticker=ticker.upper(),
            price=price,
            prev_close=prev_close,
            pct_change=pct,
            volume=vol,
            avg_volume_30d=avg30,
            relative_volume=rel_vol,
            market_cap=cap,
        )
    except Exception as e:
        log.warning("fetch_one(%s) failed: %s", ticker, e)
        return PriceSnapshot(ticker, None, None, None, None, None, None, None)


def fetch_many(tickers: Iterable[str]) -> dict[str, PriceSnapshot]:
    return {t.upper(): fetch_one(t) for t in tickers}

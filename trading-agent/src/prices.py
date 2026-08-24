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
    """Coerce to float; map None/NaN/uncoercible to None.

    NaN must become None here — downstream scoring treats None as
    "missing" but a NaN would flow through arithmetic and poison the
    composite scores.
    """
    try:
        if x is None:
            return None
        f = float(x)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _safe_int(x) -> int | None:
    f = _safe_float(x)
    return int(f) if f is not None else None


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
        price = _safe_float(latest["Close"])
        # Walk back to the most recent non-NaN close before the latest
        # row — a single halted/sparse session must not produce a NaN
        # pct_change (which previously scored as a perfect 100).
        prev_close = None
        for i in range(len(hist) - 2, -1, -1):
            prev_close = _safe_float(hist.iloc[i]["Close"])
            if prev_close is not None:
                break
        pct = (
            ((price - prev_close) / prev_close * 100.0)
            if price is not None and prev_close
            else None
        )
        vol = _safe_int(latest["Volume"])
        avg30 = (
            _safe_float(hist["Volume"].tail(30).mean()) if len(hist) >= 5 else None
        )
        rel_vol = (vol / avg30) if vol and avg30 else None
        # FastInfo's surface has shifted across yfinance versions — it's
        # been a dict-like, an object with attrs, and a MutableMapping
        # that raises KeyError instead of returning None. Try each shape
        # rather than relying on .get().
        cap = None
        try:
            fi = t.fast_info
            cap = _safe_float(getattr(fi, "market_cap", None))
            if cap is None:
                try:
                    cap = _safe_float(fi["market_cap"])
                except (KeyError, TypeError):
                    cap = None
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

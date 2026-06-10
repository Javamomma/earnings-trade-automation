"""Buy-zone sentinel: silent until a trigger fires.

The agent reads ``config/buy_zones.yaml``, evaluates each zone against
today's snapshot, and emits a Proposal row for every trigger that just
flipped. Critically: nothing fires unless the zone trigger is *met*.
Quiet by design — when the agent pings, it matters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import yfinance as yf

from src.portfolio import BuyZone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZoneCheck:
    zone: BuyZone
    triggered: bool
    observed_value: float | None
    note: str = ""


def _pct_off_high(t: "yf.Ticker") -> float | None:
    try:
        hist = t.history(period="1y", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        high = float(hist["High"].max())
        last = float(hist["Close"].iloc[-1])
        if high <= 0:
            return None
        return (high - last) / high * 100.0
    except Exception as e:
        log.warning("pct_off_high failed: %s", e)
        return None


def _pe(t: "yf.Ticker") -> float | None:
    try:
        info = t.info or {}
        pe = info.get("trailingPE")
        return float(pe) if pe is not None and pe > 0 else None
    except Exception as e:
        log.warning("pe lookup failed: %s", e)
        return None


def _fcf_yield(t: "yf.Ticker") -> float | None:
    try:
        info = t.info or {}
        fcf = info.get("freeCashflow")
        mcap = info.get("marketCap")
        if fcf and mcap and mcap > 0:
            return float(fcf) / float(mcap)
        return None
    except Exception as e:
        log.warning("fcf yield lookup failed: %s", e)
        return None


def _price(t: "yf.Ticker") -> float | None:
    try:
        hist = t.history(period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning("price lookup failed: %s", e)
        return None


def check_zone(zone: BuyZone) -> ZoneCheck:
    """Evaluate a single zone against live data."""
    t = yf.Ticker(zone.ticker)
    kind = zone.trigger_kind
    if kind == "price":
        v = _price(t)
        return ZoneCheck(zone, v is not None and v <= zone.trigger_value, v,
                         f"last={v}, trigger≤{zone.trigger_value}")
    if kind == "pct_off_high":
        v = _pct_off_high(t)
        return ZoneCheck(zone, v is not None and v >= zone.trigger_value, v,
                         f"off-high={v:.1f}%, trigger≥{zone.trigger_value}%"
                         if v is not None else "no data")
    if kind == "pe_below":
        v = _pe(t)
        return ZoneCheck(zone, v is not None and v <= zone.trigger_value, v,
                         f"trailing P/E={v}, trigger≤{zone.trigger_value}"
                         if v is not None else "no PE data")
    if kind == "fcf_yield_above":
        v = _fcf_yield(t)
        return ZoneCheck(zone, v is not None and v >= zone.trigger_value, v,
                         f"FCF yield={v:.3f}, trigger≥{zone.trigger_value}"
                         if v is not None else "no FCF data")
    log.warning("Unknown trigger_kind %r on %s", kind, zone.ticker)
    return ZoneCheck(zone, False, None, f"unknown trigger_kind: {kind}")


def evaluate_zones(zones: Iterable[BuyZone]) -> list[ZoneCheck]:
    return [check_zone(z) for z in zones]


def triggered_zones(checks: Iterable[ZoneCheck]) -> list[ZoneCheck]:
    return [c for c in checks if c.triggered]

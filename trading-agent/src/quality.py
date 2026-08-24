"""Quality screen — the inverse of the speculative flags.

Where the radar-watchlist scorer rewards momentum and Reddit chatter
and penalizes dilution, this module rewards persistence: stable
return-on-invested-capital, positive free-cash-flow yield, *shrinking*
share count (buybacks — the anti-dilution flag), and steady operating
margins. Designed for the buy-and-hold core of the book.

All scoring is pure: feed it the financial inputs you have, get back
a 0-100 quality score plus a per-axis breakdown. The data fetcher
(``fetch_quality_inputs``) lives at the boundary and is wrapped so a
single yfinance failure can't take a screen down.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import yfinance as yf

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityInputs:
    ticker: str
    roic: float | None = None              # return on invested capital, fraction
    fcf_yield: float | None = None         # FCF / market cap, fraction
    shares_outstanding_delta_pct: float | None = None  # negative = buyback
    operating_margin: float | None = None  # fraction
    debt_to_equity: float | None = None
    revenue_cagr_3y: float | None = None   # fraction
    price: float | None = None
    market_cap: float | None = None


@dataclass(frozen=True)
class QualityScore:
    ticker: str
    profitability: float
    capital_return: float
    growth: float
    leverage: float
    composite: float
    flags: tuple[str, ...]


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if x != x:  # NaN
        return lo
    return max(lo, min(hi, x))


def _none_safe(x: float | None) -> float | None:
    if x is None or x != x:
        return None
    return x


def profitability_score(roic: float | None, operating_margin: float | None) -> float:
    """High ROIC + stable operating margin = profitable franchise."""
    roic = _none_safe(roic)
    om = _none_safe(operating_margin)
    if roic is None and om is None:
        return 0.0
    # ROIC ~10% = 50 score, 20%+ saturates, negative kills it.
    r = _clip((roic or 0.0) * 500.0) if roic is not None else 30.0
    # Operating margin 15% = 50, 30%+ saturates.
    m = _clip((om or 0.0) * 333.0) if om is not None else 30.0
    return _clip(0.6 * r + 0.4 * m)


def capital_return_score(
    fcf_yield: float | None, shares_delta_pct: float | None
) -> float:
    """FCF yield + buybacks. Negative shares delta = company is buying
    back stock, the anti-dilution we always want on a quality name."""
    fy = _none_safe(fcf_yield)
    sd = _none_safe(shares_delta_pct)
    # 5% FCF yield = 50 score; 10%+ saturates.
    y = _clip((fy or 0.0) * 1000.0) if fy is not None else 25.0
    # 2% annual buyback (sd = -2) -> 60; flat -> 30; dilution -> 0.
    b = 30.0
    if sd is not None:
        b = _clip(30.0 - sd * 15.0)
    return _clip(0.65 * y + 0.35 * b)


def growth_score(revenue_cagr_3y: float | None) -> float:
    rg = _none_safe(revenue_cagr_3y)
    if rg is None:
        return 35.0  # default to "average"
    # 10% CAGR -> 60, 20%+ saturates, negative penalized.
    return _clip(35.0 + rg * 250.0)


def leverage_score(debt_to_equity: float | None) -> float:
    """Lower is better; massive leverage on a quality compounder is
    almost always a forced sale waiting to happen."""
    d = _none_safe(debt_to_equity)
    if d is None:
        return 50.0
    if d < 0:  # negative equity
        return 0.0
    # D/E 0.5 -> 75, 1.0 -> 50, 2.0 -> 10.
    return _clip(100.0 - d * 50.0)


def composite_quality(p: float, c: float, g: float, l: float) -> float:
    return _clip(0.35 * p + 0.30 * c + 0.20 * g + 0.15 * l)


def score_quality(inputs: QualityInputs) -> QualityScore:
    p = profitability_score(inputs.roic, inputs.operating_margin)
    c = capital_return_score(inputs.fcf_yield, inputs.shares_outstanding_delta_pct)
    g = growth_score(inputs.revenue_cagr_3y)
    l = leverage_score(inputs.debt_to_equity)
    composite = composite_quality(p, c, g, l)

    flags: list[str] = []
    if inputs.shares_outstanding_delta_pct is not None and inputs.shares_outstanding_delta_pct < -1.0:
        flags.append("BUYBACK")
    if inputs.shares_outstanding_delta_pct is not None and inputs.shares_outstanding_delta_pct > 2.0:
        flags.append("DILUTING")
    if inputs.debt_to_equity is not None and inputs.debt_to_equity > 2.0:
        flags.append("HIGH_DEBT")
    if inputs.fcf_yield is not None and inputs.fcf_yield > 0.06:
        flags.append("CHEAP_FCF")
    if inputs.roic is not None and inputs.roic > 0.20:
        flags.append("HIGH_ROIC")
    return QualityScore(
        ticker=inputs.ticker,
        profitability=round(p, 1),
        capital_return=round(c, 1),
        growth=round(g, 1),
        leverage=round(l, 1),
        composite=round(composite, 1),
        flags=tuple(flags),
    )


def _safe(x, fn=float):
    try:
        if x is None:
            return None
        v = fn(x)
        if isinstance(v, float) and v != v:
            return None
        return v
    except (TypeError, ValueError):
        return None


def fetch_quality_inputs(ticker: str) -> QualityInputs:
    """Best-effort fundamentals fetch via yfinance.

    yfinance is unofficial and frequently changes shape; every field is
    independently wrapped so a missing one doesn't take the others
    down. Missing fields propagate as None into the scorer.
    """
    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception as e:
            log.warning("yfinance info() failed for %s: %s", ticker, e)

        # ROIC isn't always exposed; approximate from
        # returnOnAssets * (1 + debt/equity) when both present.
        roic = _safe(info.get("returnOnAssets"))
        de = _safe(info.get("debtToEquity"))
        if de is not None:
            de = de / 100.0 if de > 5 else de  # yfinance sometimes returns %
        if roic is not None and de is not None and de >= 0:
            roic = roic * (1.0 + de)

        fcf = _safe(info.get("freeCashflow"))
        mcap = _safe(info.get("marketCap"))
        fcf_yield = (fcf / mcap) if fcf and mcap and mcap > 0 else None

        op_margin = _safe(info.get("operatingMargins"))
        revenue_growth = _safe(info.get("revenueGrowth"))
        price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))

        # Shares-outstanding delta: yfinance exposes a one-quarter look
        # via implied; we don't have history without extra calls, so
        # leave None unless float-shares / shares-outstanding both
        # present and consistent.
        sd_pct = None
        return QualityInputs(
            ticker=ticker.upper(),
            roic=roic,
            fcf_yield=fcf_yield,
            shares_outstanding_delta_pct=sd_pct,
            operating_margin=op_margin,
            debt_to_equity=de,
            revenue_cagr_3y=revenue_growth,  # 1y is what's available; better than nothing
            price=price,
            market_cap=mcap,
        )
    except Exception as e:
        log.warning("fetch_quality_inputs(%s) failed: %s", ticker, e)
        return QualityInputs(ticker=ticker.upper())


def screen_many(tickers: Iterable[str]) -> dict[str, QualityScore]:
    out: dict[str, QualityScore] = {}
    for t in tickers:
        out[t.upper()] = score_quality(fetch_quality_inputs(t))
    return out

"""Option-income scanner: cash-secured puts and covered calls.

This is the module that produces the monthly cash cadence. We rank
candidate strikes by:

  * Premium per day, annualized
  * Probability of assignment (Black-Scholes, OTM-only by default)
  * IV richness (current IV vs. realized vol)
  * Liquidity (open interest + volume)
  * Earnings calendar — never propose a short-vol position spanning an
    earnings date by default

The output is a list of ``IncomeIdea`` objects with explicit rationale.
None of this places trades; the brief renders the ideas as proposals.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

import yfinance as yf

from src.volatility import (
    annualized_yield,
    call_delta,
    iv_rank,
    prob_assignment_short_call,
    prob_assignment_short_put,
    put_delta,
    yang_zhang,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomeIdea:
    ticker: str
    kind: str          # 'csp' (cash-secured put) | 'cc' (covered call)
    expiry: str        # YYYY-MM-DD
    strike: float
    mid_price: float
    bid: float
    ask: float
    spot: float
    days_to_exp: int
    capital_required: float
    annual_yield: float
    period_yield: float
    delta: float
    prob_assignment: float
    iv: float
    iv_rv_ratio: float | None
    iv_rank_value: float | None
    open_interest: int
    volume: int
    spans_earnings: bool
    score: float       # 0-100 composite for ranking
    rationale: str


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


def _next_earnings(t: "yf.Ticker") -> date | None:
    try:
        cal = t.calendar
        if hasattr(cal, "loc") and "Earnings Date" in cal.index:
            d = cal.loc["Earnings Date"]
            if hasattr(d, "__iter__"):
                d = next(iter(d), None)
            if d is None:
                return None
            return d.date() if hasattr(d, "date") else d
        if isinstance(cal, dict) and "Earnings Date" in cal:
            d = cal["Earnings Date"]
            if isinstance(d, list) and d:
                d = d[0]
            return d.date() if hasattr(d, "date") else d
    except Exception as e:
        log.debug("calendar lookup failed for %s: %s", t.ticker, e)
    return None


def _eligible_expiries(t: "yf.Ticker", min_days: int, max_days: int) -> list[str]:
    today = date.today()
    out = []
    for e in t.options or ():
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (d - today).days
        if min_days <= days <= max_days:
            out.append(e)
    return out


def _score_idea(
    annual: float,
    prob_assign: float,
    iv_rv: float | None,
    open_interest: int,
    volume: int,
    spans_earnings: bool,
    target_delta: tuple[float, float],
    delta: float,
) -> float:
    """0-100 composite ranking. Reward yield, penalize crowded delta zones,
    illiquidity, and earnings exposure."""
    # Yield: 30% annualized -> 60 points; saturates around 60%.
    y = max(0.0, min(60.0, annual * 200.0))
    # IV richness: IV/RV 1.5 = 20 points; above 2 saturates.
    iv_pts = 0.0
    if iv_rv is not None:
        iv_pts = max(0.0, min(20.0, (iv_rv - 1.0) * 30.0))
    # Liquidity: log of (OI + volume).
    liq_pts = 0.0
    if open_interest + volume > 0:
        liq_pts = max(0.0, min(15.0, math.log10(open_interest + volume + 1) * 6.0))
    # Delta in target window earns 10 points; outside, 0.
    d_abs = abs(delta)
    target_pts = 10.0 if target_delta[0] <= d_abs <= target_delta[1] else 0.0
    # Penalties.
    earn_penalty = 25.0 if spans_earnings else 0.0
    return max(0.0, y + iv_pts + liq_pts + target_pts - earn_penalty)


def find_csp_ideas(
    ticker: str,
    *,
    min_days: int = 14,
    max_days: int = 45,
    target_delta: tuple[float, float] = (0.15, 0.30),
    avoid_earnings: bool = True,
    min_open_interest: int = 100,
) -> list[IncomeIdea]:
    """Cash-secured-put ideas. Restricted to OTM by default.

    target_delta tightens the strike to the income sweet spot (~70-85%
    chance of expiring worthless). Set wider if you actually want the
    shares — e.g. ``(0.30, 0.50)`` to chase ownership."""
    return _find_ideas(ticker, "csp", min_days, max_days, target_delta,
                       avoid_earnings, min_open_interest)


def find_cc_ideas(
    ticker: str,
    *,
    min_days: int = 14,
    max_days: int = 45,
    target_delta: tuple[float, float] = (0.15, 0.30),
    avoid_earnings: bool = True,
    min_open_interest: int = 100,
) -> list[IncomeIdea]:
    """Covered-call ideas — only OTM. Caller is expected to own ≥100
    shares per contract; we don't enforce that here."""
    return _find_ideas(ticker, "cc", min_days, max_days, target_delta,
                       avoid_earnings, min_open_interest)


def _find_ideas(
    ticker: str,
    kind: str,
    min_days: int,
    max_days: int,
    target_delta: tuple[float, float],
    avoid_earnings: bool,
    min_open_interest: int,
) -> list[IncomeIdea]:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="90d", auto_adjust=False)
        if hist is None or hist.empty:
            return []
        spot = _safe(hist["Close"].iloc[-1])
        if not spot:
            return []
        try:
            rv30 = float(yang_zhang(hist, window=30))
        except Exception:
            rv30 = None
        next_earn = _next_earnings(t) if avoid_earnings else None

        ideas: list[IncomeIdea] = []
        for expiry in _eligible_expiries(t, min_days, max_days):
            try:
                chain = t.option_chain(expiry)
            except Exception as e:
                log.debug("option_chain(%s) failed: %s", expiry, e)
                continue
            exp_d = datetime.strptime(expiry, "%Y-%m-%d").date()
            dte = (exp_d - date.today()).days
            spans_earn = bool(next_earn and date.today() <= next_earn <= exp_d)

            df = chain.puts if kind == "csp" else chain.calls
            for _, row in df.iterrows():
                strike = _safe(row.get("strike"))
                bid = _safe(row.get("bid")) or 0.0
                ask = _safe(row.get("ask")) or 0.0
                oi = int(_safe(row.get("openInterest"), int) or 0)
                vol = int(_safe(row.get("volume"), int) or 0)
                iv = _safe(row.get("impliedVolatility"))
                if not strike or not iv or iv <= 0:
                    continue
                # OTM only.
                if kind == "csp" and strike >= spot:
                    continue
                if kind == "cc" and strike <= spot:
                    continue
                if oi < min_open_interest:
                    continue
                mid = (bid + ask) / 2.0 if (bid and ask) else max(bid, ask)
                if mid <= 0:
                    continue
                t_years = dte / 365.0
                if kind == "csp":
                    delta = put_delta(spot, strike, t_years, iv)
                    prob_assign = prob_assignment_short_put(spot, strike, t_years, iv)
                    capital = strike * 100.0
                else:
                    delta = call_delta(spot, strike, t_years, iv)
                    prob_assign = prob_assignment_short_call(spot, strike, t_years, iv)
                    capital = spot * 100.0
                premium = mid * 100.0
                annual = annualized_yield(premium, capital, dte)
                iv_rv = (iv / rv30) if rv30 and rv30 > 0 else None
                ivr = None  # IV rank requires historical IV series — defer

                score = _score_idea(
                    annual=annual, prob_assign=prob_assign, iv_rv=iv_rv,
                    open_interest=oi, volume=vol, spans_earnings=spans_earn,
                    target_delta=target_delta, delta=delta,
                )
                if score <= 0:
                    continue
                rationale_bits = [
                    f"{annual*100:.1f}% annualized at {abs(delta):.2f}Δ",
                    f"IV/RV {iv_rv:.2f}" if iv_rv else "IV/RV n/a",
                    f"OI {oi}, vol {vol}",
                ]
                if spans_earn:
                    rationale_bits.append("⚠ spans earnings")
                ideas.append(IncomeIdea(
                    ticker=ticker.upper(), kind=kind, expiry=expiry, strike=strike,
                    mid_price=mid, bid=bid, ask=ask, spot=spot, days_to_exp=dte,
                    capital_required=capital, annual_yield=annual, period_yield=premium / capital,
                    delta=delta, prob_assignment=prob_assign, iv=iv,
                    iv_rv_ratio=iv_rv, iv_rank_value=ivr, open_interest=oi, volume=vol,
                    spans_earnings=spans_earn, score=score, rationale=" • ".join(rationale_bits),
                ))
        ideas.sort(key=lambda i: i.score, reverse=True)
        return ideas
    except Exception as e:
        log.warning("find_ideas(%s, %s) failed: %s", ticker, kind, e)
        return []


def top_ideas_for(
    ticker: str, *, n: int = 3, kind: str = "csp", **kwargs
) -> list[IncomeIdea]:
    if kind == "csp":
        return find_csp_ideas(ticker, **kwargs)[:n]
    if kind == "cc":
        return find_cc_ideas(ticker, **kwargs)[:n]
    raise ValueError(f"unknown kind {kind!r}")

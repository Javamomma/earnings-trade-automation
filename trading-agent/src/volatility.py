"""Yang-Zhang realized volatility (ported from earnings bot) and a
small Black-Scholes kit for delta / probability estimation.

All functions are pure — given the same inputs they return the same
outputs. No I/O, no env lookups.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd


def yang_zhang(
    price_data: pd.DataFrame,
    window: int = 30,
    trading_periods: int = 252,
    return_last_only: bool = True,
):
    """Annualized Yang-Zhang realized volatility.

    Expects an OHLC DataFrame indexed by date with at least
    ``window + 1`` non-NaN closes.
    """
    log_ho = np.log(price_data["High"] / price_data["Open"])
    log_lo = np.log(price_data["Low"] / price_data["Open"])
    log_co = np.log(price_data["Close"] / price_data["Open"])
    log_oc = np.log(price_data["Open"] / price_data["Close"].shift(1))
    log_cc = np.log(price_data["Close"] / price_data["Close"].shift(1))

    rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
    close_vol = (log_cc ** 2).rolling(window=window).sum() * (1.0 / (window - 1.0))
    open_vol = (log_oc ** 2).rolling(window=window).sum() * (1.0 / (window - 1.0))
    window_rs = rs.rolling(window=window).sum() * (1.0 / (window - 1.0))

    k = 0.34 / (1.34 + ((window + 1) / (window - 1)))
    result = np.sqrt(open_vol + k * close_vol + (1 - k) * window_rs) * np.sqrt(trading_periods)
    return result.iloc[-1] if return_last_only else result.dropna()


# ---------------------------------------------------------------------
# Black-Scholes utilities. Used to estimate delta and probability of
# assignment for option-income scoring when the broker chain doesn't
# include greeks. American early-exercise effects are ignored — fine
# for short-dated OTM income strikes; flagged separately when ITM.
# ---------------------------------------------------------------------


def _phi(x: float) -> float:
    """Standard normal CDF via math.erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_d1(spot: float, strike: float, t_years: float, vol: float, r: float = 0.04) -> float:
    if t_years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    return (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))


def call_delta(spot: float, strike: float, t_years: float, vol: float, r: float = 0.04) -> float:
    return _phi(bs_d1(spot, strike, t_years, vol, r))


def put_delta(spot: float, strike: float, t_years: float, vol: float, r: float = 0.04) -> float:
    return call_delta(spot, strike, t_years, vol, r) - 1.0


def prob_assignment_short_put(
    spot: float, strike: float, t_years: float, vol: float, r: float = 0.04
) -> float:
    """Risk-neutral probability the short put finishes in the money
    (i.e. assigned at expiry). Approximate, ignores American exercise."""
    if t_years <= 0 or vol <= 0:
        return 1.0 if spot < strike else 0.0
    d2 = bs_d1(spot, strike, t_years, vol, r) - vol * math.sqrt(t_years)
    return _phi(-d2)


def prob_assignment_short_call(
    spot: float, strike: float, t_years: float, vol: float, r: float = 0.04
) -> float:
    if t_years <= 0 or vol <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = bs_d1(spot, strike, t_years, vol, r) - vol * math.sqrt(t_years)
    return _phi(d2)


def iv_rank(current_iv: float, history: Sequence[float]) -> float:
    """IV rank in [0, 100]: where current IV sits between the historical
    min and max. Standard CBOE-style definition."""
    if not history:
        return 50.0
    lo, hi = min(history), max(history)
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100.0))


def annualized_yield(premium: float, capital_required: float, days_to_exp: int) -> float:
    """Annualize a single-trade return.

    capital_required is the cash tied up: for a CSP that's strike*100,
    for a CC that's the share value of the underlying."""
    if capital_required <= 0 or days_to_exp <= 0:
        return 0.0
    period = premium / capital_required
    return (1.0 + period) ** (365.0 / days_to_exp) - 1.0

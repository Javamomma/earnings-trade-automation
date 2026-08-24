"""Tests for the Yang-Zhang + Black-Scholes layer."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.volatility import (
    annualized_yield,
    bs_d1,
    call_delta,
    iv_rank,
    prob_assignment_short_call,
    prob_assignment_short_put,
    put_delta,
    yang_zhang,
)


class TestYangZhang:
    def _sample(self, n: int = 60, seed: int = 0) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        steps = rng.normal(0.0, 0.01, n)
        close = 100 * np.exp(np.cumsum(steps))
        return pd.DataFrame({
            "Open":  close * (1 + rng.normal(0, 0.001, n)),
            "High":  close * (1 + np.abs(rng.normal(0, 0.005, n))),
            "Low":   close * (1 - np.abs(rng.normal(0, 0.005, n))),
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, n),
        })

    def test_returns_finite_positive(self):
        rv = float(yang_zhang(self._sample()))
        assert math.isfinite(rv)
        assert rv > 0

    def test_monotonic_in_input_vol(self):
        rng = np.random.default_rng(1)
        def make(vol):
            steps = rng.normal(0, vol, 60)
            close = 100 * np.exp(np.cumsum(steps))
            return pd.DataFrame({
                "Open": close, "High": close * 1.005, "Low": close * 0.995,
                "Close": close, "Volume": [1_000_000]*60,
            })
        lo = float(yang_zhang(make(0.005)))
        hi = float(yang_zhang(make(0.03)))
        assert hi > lo


class TestBlackScholes:
    def test_atm_call_delta_around_half(self):
        d = call_delta(100, 100, 30/365, 0.25, r=0.04)
        assert 0.45 < d < 0.65

    def test_atm_put_delta_around_minus_half(self):
        d = put_delta(100, 100, 30/365, 0.25, r=0.04)
        assert -0.55 < d < -0.35

    def test_otm_short_put_prob_assignment_low(self):
        # 25-delta short put: assignment probability near 25%.
        p = prob_assignment_short_put(100, 90, 30/365, 0.25, r=0.04)
        assert 0.05 < p < 0.35

    def test_deep_otm_short_call_prob_assignment_tiny(self):
        p = prob_assignment_short_call(100, 200, 30/365, 0.25, r=0.04)
        assert p < 0.01

    def test_expired_options_resolve(self):
        # t=0: call assignment iff in the money.
        assert prob_assignment_short_call(100, 90, 0, 0.25) == 1.0
        assert prob_assignment_short_call(100, 110, 0, 0.25) == 0.0
        assert prob_assignment_short_put(100, 110, 0, 0.25) == 1.0
        assert prob_assignment_short_put(100, 90, 0, 0.25) == 0.0


class TestIvRank:
    def test_empty_history_neutral(self):
        assert iv_rank(0.3, []) == 50.0

    def test_at_min(self):
        assert iv_rank(0.1, [0.1, 0.2, 0.3, 0.4]) == 0.0

    def test_at_max(self):
        assert iv_rank(0.4, [0.1, 0.2, 0.3, 0.4]) == 100.0

    def test_middle(self):
        assert iv_rank(0.25, [0.1, 0.4]) == pytest.approx(50.0)

    def test_flat_history(self):
        assert iv_rank(0.2, [0.2, 0.2]) == 50.0


class TestAnnualizedYield:
    def test_simple(self):
        # $200 on $10,000 over 30 days ≈ 27.4% annualized.
        y = annualized_yield(200, 10_000, 30)
        assert 0.25 < y < 0.30

    def test_zero_capital_returns_zero(self):
        assert annualized_yield(100, 0, 30) == 0.0

    def test_zero_days_returns_zero(self):
        assert annualized_yield(100, 10_000, 0) == 0.0

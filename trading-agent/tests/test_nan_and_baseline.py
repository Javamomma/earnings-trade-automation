"""Regression tests for the NaN-poisoning and Reddit-baseline bugs
found in the audit.

The NaN chain was: a halted/sparse session gives yfinance a NaN
prev-close -> pct_change=NaN -> momentum_score(NaN) -> _clip(NaN)
returned 100 (because min(100, nan) keeps 100) -> a dead ticker tops
the brief with priority ~64 and can fire a Discord alert.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.reddit_scan import compute_acceleration
from src.scoring import _clip, momentum_score, score_ticker

NAN = float("nan")


class TestClipNaN:
    def test_clip_nan_returns_floor(self):
        assert _clip(NAN) == 0.0

    def test_clip_nan_custom_floor(self):
        assert _clip(NAN, lo=5.0) == 5.0

    def test_clip_normal_unaffected(self):
        assert _clip(50.0) == 50.0
        assert _clip(-1.0) == 0.0
        assert _clip(101.0) == 100.0


class TestMomentumNaN:
    def test_nan_pct_treated_as_missing(self):
        # NaN pct + real relvol should equal None pct + real relvol.
        assert momentum_score(NAN, 2.0) == momentum_score(None, 2.0)

    def test_nan_relvol_treated_as_missing(self):
        assert momentum_score(5.0, NAN) == momentum_score(5.0, None)

    def test_both_nan_is_zero(self):
        assert momentum_score(NAN, NAN) == 0.0

    def test_nan_never_scores_perfect(self):
        s = score_ticker(
            pct_change=NAN, relative_volume=NAN,
            reddit_acceleration=0.0, reddit_weighted=0.0,
            dilution_summary={}, tags=(),
        )
        assert s.momentum == 0.0
        assert s.research_priority < 25  # the no-data baseline, not 64


class TestPricesNaN:
    """fetch_one must degrade per-field, not nuke the snapshot, and
    must never emit a NaN pct_change."""

    def _snap_for(self, df, monkeypatch):
        import yfinance

        class FakeTicker:
            fast_info = {}
            def history(self, period=None, auto_adjust=False):
                return df

        monkeypatch.setattr(yfinance, "Ticker", lambda t: FakeTicker())
        from src.prices import fetch_one
        return fetch_one("TEST")

    def test_nan_volume_keeps_price(self, monkeypatch):
        df = pd.DataFrame({
            "Open": [10, 11], "High": [11, 12], "Low": [9, 10],
            "Close": [10.5, 11.0], "Volume": [1e6, np.nan],
        })
        snap = self._snap_for(df, monkeypatch)
        assert snap.price == 11.0, "NaN volume must not destroy the price"
        assert snap.volume is None
        assert snap.pct_change is not None and not math.isnan(snap.pct_change)

    def test_nan_prev_close_skips_to_earlier_close(self, monkeypatch):
        df = pd.DataFrame({
            "Open": [10, 10, 11], "High": [11, 11, 12], "Low": [9, 9, 10],
            "Close": [10.0, np.nan, 11.0], "Volume": [1e6, 1e6, 2e6],
        })
        snap = self._snap_for(df, monkeypatch)
        assert snap.pct_change is not None
        assert not math.isnan(snap.pct_change)
        assert snap.pct_change == pytest.approx(10.0)  # 11 vs 10, skipping NaN

    def test_all_nan_history_yields_none_fields(self, monkeypatch):
        df = pd.DataFrame({
            "Open": [np.nan], "High": [np.nan], "Low": [np.nan],
            "Close": [np.nan], "Volume": [np.nan],
        })
        snap = self._snap_for(df, monkeypatch)
        assert snap.price is None
        assert snap.pct_change is None
        assert snap.volume is None


class TestRedditAccelerationMath:
    def test_divisor_excludes_fresh_window(self):
        # 72h lookback, 6h fresh window: older mentions span 66h = 11
        # windows. 22 older mentions -> baseline 2.0/window exactly.
        baseline, accel = compute_acceleration(
            fresh_count=4, older_count=22,
            fresh_window_hours=6, baseline_window_hours=72,
        )
        assert baseline == pytest.approx(2.0)
        assert accel == pytest.approx(2.0)

    def test_zero_baseline_uses_fresh_count(self):
        baseline, accel = compute_acceleration(0, 0, 6, 72)
        assert baseline == 0.0
        assert accel == 0.0
        baseline, accel = compute_acceleration(7, 0, 6, 72)
        assert accel == 7.0

    def test_degenerate_windows_dont_divide_by_zero(self):
        # baseline window == fresh window: non-fresh span clamps to one
        # window instead of zero.
        baseline, accel = compute_acceleration(3, 6, 6, 6)
        assert baseline == pytest.approx(6.0)
        assert accel == pytest.approx(0.5)


class TestAtmIn8K:
    def test_8k_with_atm_language_flags(self):
        from src.sec_filings import _is_dilutive
        assert _is_dilutive("8-K", "Entry into at-the-market offering agreement") is True

    def test_8k_without_atm_language_clean(self):
        from src.sec_filings import _is_dilutive
        assert _is_dilutive("8-K", "Results of operations") is False

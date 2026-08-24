"""Unit tests for the pure scoring functions.

Run from the ``trading-agent/`` directory:

    pytest -q
"""

from __future__ import annotations

import math

import pytest

from src.scoring import (
    CompositeScore,
    _clip,
    dilution_risk_score,
    meme_risk_score,
    momentum_score,
    reddit_acceleration_score,
    research_priority_score,
    score_ticker,
)


class TestClip:
    def test_inside_range(self):
        assert _clip(50) == 50
    def test_below(self):
        assert _clip(-10) == 0
    def test_above(self):
        assert _clip(150) == 100


class TestMomentum:
    def test_both_none(self):
        assert momentum_score(None, None) == 0.0

    def test_positive_move_high_rv(self):
        s = momentum_score(5.0, 3.0)
        assert 50 < s <= 100

    def test_negative_move_low_rv(self):
        # Negative moves still count (volatility is volatility); the
        # absolute pct change drives momentum so abs(-5) == abs(5).
        s_pos = momentum_score(5.0, 1.0)
        s_neg = momentum_score(-5.0, 1.0)
        assert s_pos == s_neg

    def test_rel_vol_one_is_neutral(self):
        # rel_vol=1 should give exactly 50 on the rv-component.
        assert math.isclose(momentum_score(0.0, 1.0), _clip(0.4 * 50.0), rel_tol=1e-6)

    def test_saturation(self):
        assert momentum_score(100.0, 100.0) == 100.0


class TestRedditAcceleration:
    def test_zero(self):
        assert reddit_acceleration_score(0.0, 0.0) >= 0

    def test_monotonic_in_acceleration(self):
        a = reddit_acceleration_score(1.0, 5.0)
        b = reddit_acceleration_score(5.0, 5.0)
        assert b >= a

    def test_monotonic_in_volume(self):
        a = reddit_acceleration_score(2.0, 1.0)
        b = reddit_acceleration_score(2.0, 50.0)
        assert b >= a

    def test_bounded(self):
        s = reddit_acceleration_score(1e6, 1e6)
        assert 0 <= s <= 100


class TestDilutionRisk:
    def test_empty(self):
        assert dilution_risk_score({}) == 0.0

    def test_none(self):
        assert dilution_risk_score(None) == 0.0  # type: ignore[arg-type]

    def test_single_dilutive(self):
        s = dilution_risk_score({"any_dilutive": True, "count": 1})
        assert s == 50.0

    def test_multiple_dilutive_increases(self):
        s1 = dilution_risk_score({"any_dilutive": True, "count": 1})
        s3 = dilution_risk_score({"any_dilutive": True, "count": 3})
        s10 = dilution_risk_score({"any_dilutive": True, "count": 10})
        assert s1 < s3 < s10 == 100.0  # saturates at +5 over base


class TestMemeRisk:
    def test_no_signal(self):
        assert meme_risk_score([], 0.0) == 0.0

    def test_meme_tag_alone_contributes(self):
        s = meme_risk_score(["meme"], 0.0)
        assert s > 0

    def test_volume_contributes(self):
        s_lo = meme_risk_score([], 1.0)
        s_hi = meme_risk_score([], 100.0)
        assert s_hi > s_lo

    def test_bounded(self):
        assert 0 <= meme_risk_score(["meme"], 1e6) <= 100


class TestResearchPriority:
    def test_all_zero(self):
        s = research_priority_score(0, 0, 0, 0)
        assert math.isclose(s, 20.0, rel_tol=1e-3)  # (-25..100) maps; 0 -> 20

    def test_high_momentum_dominates(self):
        good = research_priority_score(100, 0, 0, 0)
        bad = research_priority_score(0, 0, 100, 100)
        assert good > bad

    def test_dilution_reduces(self):
        without = research_priority_score(80, 50, 0, 0)
        withd = research_priority_score(80, 50, 80, 0)
        assert withd < without


class TestScoreTicker:
    def test_returns_composite(self):
        s = score_ticker(
            pct_change=3.0,
            relative_volume=2.0,
            reddit_acceleration=2.5,
            reddit_weighted=8.0,
            dilution_summary={"any_dilutive": False, "count": 0},
            tags=("ai",),
        )
        assert isinstance(s, CompositeScore)
        assert 0 <= s.momentum <= 100
        assert 0 <= s.reddit <= 100
        assert s.dilution == 0
        assert 0 <= s.research_priority <= 100

    def test_dilution_lowers_priority(self):
        baseline_inputs = dict(
            pct_change=3.0, relative_volume=2.0,
            reddit_acceleration=1.0, reddit_weighted=2.0,
            tags=(),
        )
        clean = score_ticker(
            **baseline_inputs, dilution_summary={"any_dilutive": False, "count": 0}
        )
        dirty = score_ticker(
            **baseline_inputs, dilution_summary={"any_dilutive": True, "count": 2}
        )
        assert dirty.research_priority < clean.research_priority

    def test_meme_tag_raises_meme_score(self):
        no_tag = score_ticker(
            pct_change=0.0, relative_volume=1.0,
            reddit_acceleration=1.0, reddit_weighted=10.0,
            dilution_summary={}, tags=(),
        )
        meme = score_ticker(
            pct_change=0.0, relative_volume=1.0,
            reddit_acceleration=1.0, reddit_weighted=10.0,
            dilution_summary={}, tags=("meme",),
        )
        assert meme.meme > no_tag.meme


@pytest.mark.parametrize(
    "pct,rv",
    [(None, None), (0.0, None), (None, 1.0), (None, 0.0)],
)
def test_momentum_handles_partial_inputs(pct, rv):
    s = momentum_score(pct, rv)
    assert 0 <= s <= 100


class TestSecTickerMapCache:
    """Regression: previously @lru_cache poisoned the cache on a failed
    network fetch so subsequent runs in the same process returned an
    empty map forever."""

    def test_failure_does_not_poison_cache(self, monkeypatch):
        import requests
        from src import sec_filings

        sec_filings._reset_ticker_map_cache()

        def boom(*a, **kw):
            raise requests.RequestException("simulated outage")

        monkeypatch.setattr(requests, "get", boom)
        assert sec_filings._ticker_to_cik_map() == {}

        class FakeResp:
            def raise_for_status(self): pass
            def json(self):
                return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}

        monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResp())
        out = sec_filings._ticker_to_cik_map()
        assert out == {"AAPL": "0000320193"}, "cache was poisoned by earlier failure"

        sec_filings._reset_ticker_map_cache()

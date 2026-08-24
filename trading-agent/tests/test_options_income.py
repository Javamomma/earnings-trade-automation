"""Options-income scorer tests. The chain-fetching path is network-bound
so it isn't exercised here; the scoring math is."""

from __future__ import annotations

from src.options_income import _score_idea


class TestScoreIdea:
    def test_higher_yield_scores_higher(self):
        kwargs = dict(prob_assign=0.20, iv_rv=1.4, open_interest=500,
                      volume=50, spans_earnings=False,
                      target_delta=(0.15, 0.30), delta=-0.22)
        lo = _score_idea(annual=0.10, **kwargs)
        hi = _score_idea(annual=0.30, **kwargs)
        assert hi > lo

    def test_earnings_penalty(self):
        base = dict(annual=0.30, prob_assign=0.20, iv_rv=1.4, open_interest=500,
                    volume=50, target_delta=(0.15, 0.30), delta=-0.22)
        clean = _score_idea(spans_earnings=False, **base)
        earn = _score_idea(spans_earnings=True, **base)
        assert earn < clean

    def test_delta_outside_band_zero_target_bonus(self):
        base = dict(annual=0.30, prob_assign=0.20, iv_rv=1.4, open_interest=500,
                    volume=50, spans_earnings=False, target_delta=(0.15, 0.30))
        inside = _score_idea(delta=-0.20, **base)
        outside = _score_idea(delta=-0.05, **base)
        assert inside > outside

    def test_iv_richness_helps(self):
        base = dict(annual=0.20, prob_assign=0.20, open_interest=500,
                    volume=50, spans_earnings=False,
                    target_delta=(0.15, 0.30), delta=-0.22)
        cheap = _score_idea(iv_rv=1.0, **base)
        rich = _score_idea(iv_rv=2.0, **base)
        assert rich > cheap

    def test_illiquid_loses_to_liquid(self):
        base = dict(annual=0.20, prob_assign=0.20, iv_rv=1.4, spans_earnings=False,
                    target_delta=(0.15, 0.30), delta=-0.22)
        illiq = _score_idea(open_interest=10, volume=0, **base)
        liq = _score_idea(open_interest=5000, volume=1000, **base)
        assert liq > illiq

    def test_never_negative(self):
        s = _score_idea(annual=0.0, prob_assign=0.99, iv_rv=0.5,
                        open_interest=0, volume=0, spans_earnings=True,
                        target_delta=(0.15, 0.30), delta=-0.05)
        assert s >= 0

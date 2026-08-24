"""Quality screen — pure scoring tests."""

from __future__ import annotations

from src.quality import (
    QualityInputs,
    capital_return_score,
    composite_quality,
    growth_score,
    leverage_score,
    profitability_score,
    score_quality,
)


class TestProfitability:
    def test_zero_inputs(self):
        assert profitability_score(0.0, 0.0) == 0.0

    def test_all_missing(self):
        assert profitability_score(None, None) == 0.0

    def test_high_roic_and_margin_scores_high(self):
        s = profitability_score(roic=0.25, operating_margin=0.30)
        assert s >= 80

    def test_negative_roic_floors(self):
        assert profitability_score(roic=-0.05, operating_margin=0.10) < 50


class TestCapitalReturn:
    def test_buyback_beats_flat(self):
        s_buyback = capital_return_score(fcf_yield=0.05, shares_delta_pct=-3.0)
        s_flat = capital_return_score(fcf_yield=0.05, shares_delta_pct=0.0)
        assert s_buyback > s_flat

    def test_dilution_kills(self):
        s = capital_return_score(fcf_yield=0.05, shares_delta_pct=5.0)
        assert s < capital_return_score(fcf_yield=0.05, shares_delta_pct=0.0)


class TestLeverage:
    def test_low_debt_high(self):
        assert leverage_score(0.3) > 70

    def test_high_debt_low(self):
        assert leverage_score(2.5) < 20

    def test_negative_equity_floors(self):
        assert leverage_score(-0.5) == 0.0

    def test_missing_neutral(self):
        assert leverage_score(None) == 50.0


class TestGrowth:
    def test_strong_growth(self):
        assert growth_score(0.20) >= 80

    def test_negative_growth(self):
        assert growth_score(-0.05) < growth_score(0.05)


class TestComposite:
    def test_msft_like(self):
        # Strong franchise: high ROIC, FCF yield ~3%, modest buybacks,
        # margin steady, growth ~12%, low debt.
        s = score_quality(QualityInputs(
            ticker="MSFT", roic=0.25, fcf_yield=0.03,
            shares_outstanding_delta_pct=-1.0,
            operating_margin=0.42, debt_to_equity=0.4,
            revenue_cagr_3y=0.12,
        ))
        assert s.composite >= 60
        assert "HIGH_ROIC" in s.flags
        assert "BUYBACK" not in s.flags  # -1.0% is below the -1.0 cutoff

    def test_struggling_smallcap(self):
        s = score_quality(QualityInputs(
            ticker="WEAK", roic=-0.05, fcf_yield=-0.02,
            shares_outstanding_delta_pct=8.0,
            operating_margin=-0.05, debt_to_equity=2.5,
            revenue_cagr_3y=-0.05,
        ))
        assert s.composite < 35
        assert "DILUTING" in s.flags
        assert "HIGH_DEBT" in s.flags

    def test_nan_inputs_dont_explode(self):
        nan = float("nan")
        s = score_quality(QualityInputs(
            ticker="X", roic=nan, fcf_yield=nan,
            shares_outstanding_delta_pct=nan,
            operating_margin=nan, debt_to_equity=nan,
            revenue_cagr_3y=nan,
        ))
        assert 0 <= s.composite <= 100

"""Portfolio + buy-zone config loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.portfolio import BuyZone, Holding, Portfolio, load_buy_zones, load_portfolio


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


class TestLoadPortfolio:
    def test_basic(self, tmp_path):
        p = _write(tmp_path, "h.yaml",
                   "holdings:\n"
                   "  - ticker: msft\n    shares: 10\n    cost_basis: 380\n"
                   "  - ticker: cost\n    shares: 5\n    cost_basis: 740\n"
                   "cash:\n  amount: 5000\n"
                   "targets:\n  MSFT: 0.10\n")
        port = load_portfolio(p)
        assert port.cash == 5000
        bk = port.by_ticker()
        assert bk["MSFT"].shares == 10
        assert bk["COST"].cost_basis == 740
        assert port.targets["MSFT"] == 0.10

    def test_missing_file_returns_empty(self, tmp_path):
        port = load_portfolio(tmp_path / "missing.yaml")
        assert port.holdings == ()
        assert port.cash == 0.0

    def test_total_equity(self):
        port = Portfolio(
            holdings=(
                Holding("MSFT", 10, 380.0),
                Holding("COST", 5, 740.0),
            ),
            cash=1000.0, targets={},
        )
        marks = {"MSFT": 400.0, "COST": 800.0}
        # 1000 + 4000 + 4000
        assert port.total_equity(marks) == 9000.0

    def test_position_weight(self):
        port = Portfolio(
            holdings=(Holding("MSFT", 10, 380.0),),
            cash=0.0, targets={},
        )
        marks = {"MSFT": 400.0}
        # 4000 / 4000 = 1.0
        assert port.position_weight("MSFT", marks) == pytest.approx(1.0)

    def test_missing_mark_excluded_from_equity(self):
        port = Portfolio(
            holdings=(Holding("X", 10, 50.0), Holding("Y", 10, 50.0)),
            cash=100.0, targets={},
        )
        marks = {"X": 60.0}  # Y missing
        assert port.total_equity(marks) == 100.0 + 600.0


class TestLoadBuyZones:
    def test_parses_all_kinds(self, tmp_path):
        p = _write(tmp_path, "z.yaml",
                   "zones:\n"
                   "  - ticker: MSFT\n    trigger_kind: pct_off_high\n    trigger_value: 15\n"
                   "  - ticker: GOOGL\n    trigger_kind: pe_below\n    trigger_value: 20\n")
        zones = load_buy_zones(p)
        assert len(zones) == 2
        assert zones[0].ticker == "MSFT"
        assert zones[1].trigger_kind == "pe_below"

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_buy_zones(tmp_path / "nope.yaml") == ()

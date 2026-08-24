"""Holdings and buy-zone config loaders + portfolio math.

Strict YAML schema. Anything malformed raises at load time so it
surfaces before the morning brief tries to render with bad data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from src.config import REPO_ROOT


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: int
    cost_basis: float | None = None
    opened_at: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class BuyZone:
    ticker: str
    trigger_kind: str
    trigger_value: float
    size_pct_of_portfolio: float = 0.0
    rationale: str = ""


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    cash: float
    targets: dict[str, float] = field(default_factory=dict)

    def by_ticker(self) -> dict[str, Holding]:
        return {h.ticker.upper(): h for h in self.holdings}

    def total_equity(self, marks: dict[str, float]) -> float:
        """Value the book given a {ticker: last_price} dict."""
        equity = float(self.cash)
        for h in self.holdings:
            mark = marks.get(h.ticker.upper())
            if mark is None:
                continue
            equity += float(mark) * h.shares
        return equity

    def position_weight(self, ticker: str, marks: dict[str, float]) -> float:
        ticker = ticker.upper()
        h = self.by_ticker().get(ticker)
        if not h:
            return 0.0
        mark = marks.get(ticker)
        if mark is None:
            return 0.0
        total = self.total_equity(marks)
        return (mark * h.shares) / total if total > 0 else 0.0


def load_portfolio(path: Path | None = None) -> Portfolio:
    path = path or (REPO_ROOT / "config" / "holdings.yaml")
    if not path.exists():
        return Portfolio(holdings=(), cash=0.0, targets={})
    raw = yaml.safe_load(path.read_text()) or {}
    holdings = tuple(
        Holding(
            ticker=str(h["ticker"]).upper(),
            shares=int(h["shares"]),
            cost_basis=float(h["cost_basis"]) if h.get("cost_basis") is not None else None,
            opened_at=str(h["opened_at"]) if h.get("opened_at") else None,
            notes=str(h.get("notes", "")),
        )
        for h in (raw.get("holdings") or [])
    )
    cash = float((raw.get("cash") or {}).get("amount", 0.0))
    targets = {str(k).upper(): float(v) for k, v in (raw.get("targets") or {}).items()}
    return Portfolio(holdings=holdings, cash=cash, targets=targets)


def load_buy_zones(path: Path | None = None) -> tuple[BuyZone, ...]:
    path = path or (REPO_ROOT / "config" / "buy_zones.yaml")
    if not path.exists():
        return ()
    raw = yaml.safe_load(path.read_text()) or {}
    return tuple(
        BuyZone(
            ticker=str(z["ticker"]).upper(),
            trigger_kind=str(z["trigger_kind"]),
            trigger_value=float(z["trigger_value"]),
            size_pct_of_portfolio=float(z.get("size_pct_of_portfolio", 0.0)),
            rationale=str(z.get("rationale", "")),
        )
        for z in (raw.get("zones") or [])
    )


def zone_tickers(zones: Iterable[BuyZone]) -> list[str]:
    seen: dict[str, None] = {}
    for z in zones:
        seen.setdefault(z.ticker, None)
    return list(seen.keys())

"""Composite scoring.

All functions here are deliberately pure (no I/O, no globals beyond
their inputs) so they're trivially testable. ``tests/test_scoring.py``
exercises the boundary conditions.

Each component returns a 0-100 number. The final ``research_priority``
is a weighted blend, also 0-100, intended as a sortable "look at this
first" signal — not a trading signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def momentum_score(pct_change: float | None, relative_volume: float | None) -> float:
    """Combines today's % change and rel-vol.

    The intent is "is this both moving and being watched?". A 5% move
    on 3x rel-vol scores higher than a 5% move on 0.5x rel-vol.
    """
    if pct_change is None and relative_volume is None:
        return 0.0
    move = abs(pct_change or 0.0)
    move_score = _clip(move * 5.0)  # 1% move -> 5; 20%+ saturates
    rv = relative_volume if relative_volume is not None else 1.0
    rv_score = _clip(50.0 * math.log2(max(rv, 0.01)) + 50.0)  # rv=1 -> 50
    return _clip(0.6 * move_score + 0.4 * rv_score)


def reddit_acceleration_score(acceleration: float, fresh_weighted: float) -> float:
    """Reddit "is mention rate spiking" score.

    ``acceleration`` is fresh/baseline. ``fresh_weighted`` is a
    subreddit-weighted count of fresh mentions. We saturate both so a
    single absurd day on a small subreddit doesn't dominate. A pair of
    zeros maps to exactly 0 so absent data is never noise.
    """
    accel_score = 40.0 * math.log2(acceleration + 1) if acceleration > 0 else 0.0
    volume_score = 8.0 * math.log2(fresh_weighted + 1) * 4.0 if fresh_weighted > 0 else 0.0
    return _clip(0.55 * _clip(accel_score) + 0.45 * _clip(volume_score))


def dilution_risk_score(summary: dict) -> float:
    """Higher = more risk. Render in the report; subtract in priority."""
    if not summary or not summary.get("any_dilutive"):
        return 0.0
    base = 50.0
    count = int(summary.get("count") or 0)
    # Each additional dilutive filing adds risk, saturates at 100.
    return _clip(base + min(count - 1, 5) * 10.0)


def meme_risk_score(tags: Iterable[str], reddit_weighted: float) -> float:
    """0-100. A "meme-tagged" ticker with high recent Reddit volume
    scores hot. Used as a *flag*, not a buy signal. Zero tags + zero
    Reddit traffic returns exactly 0."""
    tag_set = {t.lower() for t in tags or ()}
    tag_boost = 30.0 if "meme" in tag_set else 0.0
    rd = _clip(12.0 * math.log2(reddit_weighted + 1) * 4.0) if reddit_weighted > 0 else 0.0
    return _clip(tag_boost + 0.7 * rd)


@dataclass(frozen=True)
class CompositeScore:
    momentum: float
    reddit: float
    dilution: float
    meme: float
    research_priority: float


def research_priority_score(
    momentum: float,
    reddit: float,
    dilution: float,
    meme: float,
) -> float:
    """Final 0-100 "which of these should I read about first?".

    Momentum dominates because it's the most actionable for a
    research-only review. Reddit adds context. Dilution and meme risk
    *subtract* — we deprioritize tickers where the most likely outcome
    is a print-the-shares headline or a hype trap."""
    raw = (
        0.55 * momentum
        + 0.30 * reddit
        - 0.15 * dilution
        - 0.10 * meme
    )
    # Map [-25, 100] roughly to [0, 100].
    return _clip((raw + 25.0) / 1.25)


def score_ticker(
    pct_change: float | None,
    relative_volume: float | None,
    reddit_acceleration: float,
    reddit_weighted: float,
    dilution_summary: dict,
    tags: Iterable[str],
) -> CompositeScore:
    momentum = momentum_score(pct_change, relative_volume)
    reddit = reddit_acceleration_score(reddit_acceleration, reddit_weighted)
    dilution = dilution_risk_score(dilution_summary)
    meme = meme_risk_score(tags, reddit_weighted)
    priority = research_priority_score(momentum, reddit, dilution, meme)
    return CompositeScore(
        momentum=round(momentum, 1),
        reddit=round(reddit, 1),
        dilution=round(dilution, 1),
        meme=round(meme, 1),
        research_priority=round(priority, 1),
    )

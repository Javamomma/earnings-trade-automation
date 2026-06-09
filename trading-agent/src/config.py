"""Configuration: env vars and YAML watchlists."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Tickerline:
    ticker: str
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SubredditCfg:
    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class Watchlists:
    core: tuple[Tickerline, ...]
    radar: tuple[Tickerline, ...]
    subreddits: tuple[SubredditCfg, ...]
    fresh_window_hours: int
    baseline_window_hours: int

    @property
    def all_tickers(self) -> list[str]:
        seen: dict[str, None] = {}
        for t in (*self.core, *self.radar):
            seen.setdefault(t.ticker.upper(), None)
        return list(seen.keys())

    def is_core(self, ticker: str) -> bool:
        return any(t.ticker.upper() == ticker.upper() for t in self.core)


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str = os.environ.get("SEC_USER_AGENT", "trading-agent contact@example.com")
    reddit_user_agent: str = os.environ.get("REDDIT_USER_AGENT", "trading-agent/0.1")
    discord_webhook_url: str | None = os.environ.get("DISCORD_WEBHOOK_URL") or None
    journal_db_path: str = os.environ.get("JOURNAL_DB_PATH", "data/journal.db")
    reports_dir: str = os.environ.get("REPORTS_DIR", "reports")
    priority_render_threshold: float = float(os.environ.get("PRIORITY_RENDER_THRESHOLD", "40"))
    priority_alert_threshold: float = float(os.environ.get("PRIORITY_ALERT_THRESHOLD", "70"))
    report_style: str = os.environ.get("REPORT_STYLE", "obsidian").lower()
    http_timeout: float = float(os.environ.get("HTTP_TIMEOUT", "20"))
    # Reddit OAuth (app-only). Reddit now 403s unauthenticated .json scraping;
    # set these (a free "script" app at reddit.com/prefs/apps) to use the API.
    reddit_client_id: str | None = os.environ.get("REDDIT_CLIENT_ID") or None
    reddit_client_secret: str | None = os.environ.get("REDDIT_CLIENT_SECRET") or None

    def reports_path(self) -> Path:
        p = REPO_ROOT / self.reports_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    def journal_path(self) -> Path:
        p = REPO_ROOT / self.journal_db_path
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


SETTINGS = Settings()


def _coerce_tickerline(item: Any) -> Tickerline:
    if isinstance(item, str):
        return Tickerline(ticker=item.upper())
    return Tickerline(
        ticker=str(item["ticker"]).upper(),
        notes=str(item.get("notes", "")),
        tags=tuple(item.get("tags") or ()),
    )


def load_watchlists(path: Path | None = None) -> Watchlists:
    path = path or (REPO_ROOT / "config" / "watchlists.yaml")
    raw = yaml.safe_load(path.read_text())
    reddit_cfg = raw.get("reddit", {}) or {}
    subreddits = tuple(
        SubredditCfg(name=str(s["name"]), weight=float(s.get("weight", 1.0)))
        for s in (reddit_cfg.get("subreddits") or [])
    )
    return Watchlists(
        core=tuple(_coerce_tickerline(i) for i in raw.get("core") or []),
        radar=tuple(_coerce_tickerline(i) for i in raw.get("radar") or []),
        subreddits=subreddits,
        fresh_window_hours=int(reddit_cfg.get("fresh_window_hours", 6)),
        baseline_window_hours=int(reddit_cfg.get("baseline_window_hours", 72)),
    )

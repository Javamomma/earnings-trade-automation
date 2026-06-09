"""Research/observation journal (SQLite).

This is **not** a brokerage ledger. The schema stores:
  * brief_runs:  one row per generated brief (provenance)
  * observations: hypothetical setups, manual notes, post-mortems
  * scores:      a flat history of per-ticker scores so you can chart
                 priority over time

No trades are ever placed by this codebase.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

from src.config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS brief_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_date   TEXT    NOT NULL,
    generated_at TEXT    NOT NULL,
    report_path  TEXT    NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    ticker          TEXT    NOT NULL,
    kind            TEXT    NOT NULL,        -- 'hypothesis' | 'manual_note' | 'post_mortem'
    direction       TEXT,                    -- 'long' | 'short' | NULL
    conviction      INTEGER,                 -- 0-5, manually rated
    thesis          TEXT,
    invalidation    TEXT,
    catalyst        TEXT,
    tags            TEXT,                    -- comma-separated
    linked_brief_id INTEGER REFERENCES brief_runs(id)
);
CREATE INDEX IF NOT EXISTS ix_observations_ticker
    ON observations(ticker);

CREATE TABLE IF NOT EXISTS score_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at        TEXT NOT NULL DEFAULT (datetime('now')),
    brief_date         TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    momentum           REAL NOT NULL,
    reddit             REAL NOT NULL,
    dilution           REAL NOT NULL,
    meme               REAL NOT NULL,
    research_priority  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_score_history_ticker
    ON score_history(ticker, brief_date);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path: Path = SETTINGS.journal_path()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_brief_run(
    brief_date: date, report_path: Path, summary: dict | None = None
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO brief_runs (brief_date, generated_at, report_path, summary_json) "
            "VALUES (?, ?, ?, ?)",
            (
                brief_date.isoformat(),
                datetime.now().isoformat(timespec="seconds"),
                str(report_path),
                json.dumps(summary or {}),
            ),
        )
        return cur.lastrowid


def record_scores(brief_date: date, scores: Iterable[dict]) -> int:
    with _connect() as conn:
        rows = [
            (
                brief_date.isoformat(),
                s["ticker"],
                float(s["momentum"]),
                float(s["reddit"]),
                float(s["dilution"]),
                float(s["meme"]),
                float(s["research_priority"]),
            )
            for s in scores
        ]
        conn.executemany(
            "INSERT INTO score_history "
            "(brief_date, ticker, momentum, reddit, dilution, meme, research_priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)


def record_observation(
    ticker: str,
    kind: str,
    *,
    direction: str | None = None,
    conviction: int | None = None,
    thesis: str | None = None,
    invalidation: str | None = None,
    catalyst: str | None = None,
    tags: Iterable[str] = (),
    linked_brief_id: int | None = None,
) -> int:
    """Insert a manual observation row.

    Intended to be called interactively from a notebook or a small CLI;
    not by ``generate_brief.py``. We never auto-create hypotheses on
    behalf of the user.
    """
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO observations "
            "(ticker, kind, direction, conviction, thesis, invalidation, catalyst, tags, linked_brief_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker.upper(),
                kind,
                direction,
                conviction,
                thesis,
                invalidation,
                catalyst,
                ",".join(tags),
                linked_brief_id,
            ),
        )
        return cur.lastrowid

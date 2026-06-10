"""Proposals: the agent's actionable output.

Every recommendation flows through this table. The agent never just
"shows" something — it proposes a specific action with a rationale,
an invalidation, and a status. The human reviews and marks it
``accepted`` / ``rejected`` / ``deferred``. Outcomes are then measured
against the proposal's invalidation level.

This is the file that enforces the "agent proposes, together we act"
contract.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.trade_journal import _connect


@dataclass(frozen=True)
class Proposal:
    id: int | None
    ticker: str
    kind: str             # 'buy_zone_hit' | 'csp' | 'cc' | 'exit' | 'thesis_invalidation' | 'filings_delta'
    rationale: str
    structured: dict      # serialized to structured_json
    invalidation: str
    status: str           # 'open' | 'accepted' | 'rejected' | 'expired' | 'deferred'
    proposed_at: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


def propose(
    ticker: str,
    kind: str,
    rationale: str,
    *,
    structured: dict | None = None,
    invalidation: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO proposals (ticker, kind, rationale, structured_json,
                                     invalidation, status)
               VALUES (?, ?, ?, ?, ?, 'open')""",
            (ticker.upper(), kind, rationale,
             json.dumps(structured or {}), invalidation),
        )
        return cur.lastrowid


def list_open(ticker: str | None = None) -> list[Proposal]:
    with _connect() as conn:
        if ticker:
            cur = conn.execute(
                "SELECT * FROM proposals WHERE status = 'open' AND ticker = ? "
                "ORDER BY proposed_at DESC", (ticker.upper(),))
        else:
            cur = conn.execute(
                "SELECT * FROM proposals WHERE status = 'open' "
                "ORDER BY proposed_at DESC")
        cols = [d[0] for d in cur.description]
        return [_row(dict(zip(cols, row))) for row in cur.fetchall()]


def list_recent(days: int = 30) -> list[Proposal]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM proposals WHERE "
            "  datetime(proposed_at) >= datetime('now', ?) "
            "ORDER BY proposed_at DESC", (f"-{int(days)} days",))
        cols = [d[0] for d in cur.description]
        return [_row(dict(zip(cols, row))) for row in cur.fetchall()]


def review(proposal_id: int, action: str, note: str = "") -> None:
    if action not in ("accepted", "rejected", "deferred", "expired"):
        raise ValueError(f"unknown action {action!r}")
    with _connect() as conn:
        conn.execute(
            "UPDATE proposals SET status = ?, reviewed_at = ?, review_note = ? "
            "WHERE id = ?",
            (action, datetime.now().isoformat(timespec="seconds"), note, proposal_id),
        )


def _row(d: dict[str, Any]) -> Proposal:
    structured = {}
    if d.get("structured_json"):
        try:
            structured = json.loads(d["structured_json"])
        except (json.JSONDecodeError, TypeError):
            structured = {}
    return Proposal(
        id=d.get("id"),
        ticker=d.get("ticker") or "",
        kind=d.get("kind") or "",
        rationale=d.get("rationale") or "",
        structured=structured,
        invalidation=d.get("invalidation") or "",
        status=d.get("status") or "open",
        proposed_at=d.get("proposed_at"),
        reviewed_at=d.get("reviewed_at"),
        review_note=d.get("review_note"),
    )


# ----- CLI: list / review proposals -----
def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Review agent proposals. Agent proposes, you decide.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List open proposals")
    p_list.add_argument("--ticker", help="Filter by ticker")
    p_list.add_argument("--recent", type=int, default=0,
                        help="Show last N days (any status)")

    p_review = sub.add_parser("review", help="Mark a proposal")
    p_review.add_argument("--id", type=int, required=True)
    p_review.add_argument("--action", required=True,
                          choices=["accepted", "rejected", "deferred", "expired"])
    p_review.add_argument("--note", default="")

    args = parser.parse_args()
    if args.cmd == "list":
        items = list_recent(args.recent) if args.recent else list_open(args.ticker)
        if not items:
            print("(none)")
            return 0
        for p in items:
            print(f"#{p.id:<4} {p.proposed_at[:19] if p.proposed_at else '?':<19} "
                  f"{p.status:<10} {p.kind:<22} {p.ticker:<6} {p.rationale[:80]}")
            if p.invalidation:
                print(f"      INVALIDATES: {p.invalidation}")
            if p.structured:
                print(f"      DETAILS:     {json.dumps(p.structured)[:120]}")
        return 0
    if args.cmd == "review":
        review(args.id, args.action, args.note)
        print(f"proposal #{args.id} -> {args.action}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())

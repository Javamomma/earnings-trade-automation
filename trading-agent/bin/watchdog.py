#!/usr/bin/env python3
"""Watchdog: alert when the pipeline stops producing briefs.

Designed for the Raspberry Pi (or any second machine): it needs only
Python 3.9+ stdlib plus network access to Discord. Two modes:

  Local  — run on the Mac mini itself as a cron self-check:
             python3 bin/watchdog.py
  Remote — run on the Pi against the Mac over SSH:
             python3 watchdog.py --ssh user@mac-mini --path '~/trading-agent'

Logic: on a weekday, today's morning brief should exist by
ALERT_AFTER_HOUR local time. If it doesn't, post to Discord (webhook
from the DISCORD_WEBHOOK_URL env var) and exit 1. Silent (exit 0)
when everything is fine — the watchdog only speaks when something is
wrong, same philosophy as the rest of the system.

This file is intentionally dependency-free so it can be copied to the
Pi standalone: `scp bin/watchdog.py pi@raspberrypi:` and cron it there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request

ALERT_AFTER_HOUR = 10  # local time; brief is expected by 08:30


def _expected_brief_name(today: dt.date) -> str:
    return f"{today.isoformat()}-morning-brief.md"


def _exists_local(base: str, name: str) -> bool:
    return os.path.exists(os.path.join(os.path.expanduser(base), "reports", "daily", name))


def _exists_ssh(host: str, base: str, name: str) -> bool:
    path = f"{base}/reports/daily/{name}"
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             host, f"test -f {path}"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"ssh check failed: {e}", file=sys.stderr)
        return False


def _discord(msg: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL unset; printing instead:", file=sys.stderr)
        print(msg, file=sys.stderr)
        return
    data = json.dumps({"content": msg}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"Discord post failed: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Brief-freshness watchdog.")
    parser.add_argument("--ssh", help="user@host of the machine running the agent")
    parser.add_argument("--path", default="~/trading-agent",
                        help="trading-agent directory on the target machine")
    parser.add_argument("--force", action="store_true",
                        help="Check regardless of weekday/hour gates")
    args = parser.parse_args()

    now = dt.datetime.now()
    today = now.date()
    if not args.force:
        if today.weekday() >= 5:  # weekend
            return 0
        if now.hour < ALERT_AFTER_HOUR:
            return 0

    name = _expected_brief_name(today)
    ok = (
        _exists_ssh(args.ssh, args.path, name)
        if args.ssh
        else _exists_local(args.path, name)
    )
    if ok:
        return 0

    where = args.ssh or "local machine"
    _discord(
        f"🚨 **trading-agent watchdog**: no morning brief for {today.isoformat()} "
        f"on {where} as of {now:%H:%M}. Pipeline may be down — check "
        f"`data/cron.log`."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

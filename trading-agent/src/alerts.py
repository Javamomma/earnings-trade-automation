"""Discord webhook alerts."""

from __future__ import annotations

import logging

import requests

from src.config import SETTINGS

log = logging.getLogger(__name__)


def send_discord(content: str) -> bool:
    """POST a plain-text message to the configured Discord webhook.

    Returns True on a 2xx, False if the webhook is unset or the request
    failed. Discord truncates over 2000 chars, so we pre-trim.
    """
    url = SETTINGS.discord_webhook_url
    if not url:
        log.info("Discord webhook unset; skipping alert.")
        return False
    if len(content) > 1900:
        content = content[:1900] + "\n…(truncated)"
    try:
        r = requests.post(
            url, json={"content": content}, timeout=SETTINGS.http_timeout
        )
        if 200 <= r.status_code < 300:
            return True
        log.warning("Discord webhook returned %s: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        log.warning("Discord webhook post failed: %s", e)
        return False

"""Project-wide logger setup.

Use ``from log import get_logger; log = get_logger(__name__)`` instead of
``print``. Levels can be tuned via the ``LOG_LEVEL`` env var (default INFO).
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    # Replace any inherited handlers so we don't double-log under pytest/CI.
    root.handlers[:] = [handler]
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name)

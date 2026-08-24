"""Centralized, env-overridable configuration.

All tuning knobs live here so strategy parameters, time windows, and
I/O endpoints are visible in one place and adjustable without editing
trade logic. Any value may be overridden via an environment variable of
the same name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Screening thresholds ---
    min_avg_volume: float = _env_float("MIN_AVG_VOLUME", 1_500_000)
    min_iv30_rv30: float = _env_float("MIN_IV30_RV30", 1.25)
    max_ts_slope_0_45: float = _env_float("MAX_TS_SLOPE_0_45", -0.00406)

    # --- Position sizing ---
    kelly_fraction: float = _env_float("KELLY_FRACTION", 0.06)
    # When True, Kelly uses raw equity. When False, equity is reduced by
    # PROFIT_ADJUSTMENT_FACTOR * cumulative_profit (the legacy behavior,
    # which is anti-compounding but preserves principal).
    kelly_use_raw_equity: bool = _env_bool("KELLY_USE_RAW_EQUITY", False)
    profit_adjustment_factor: float = _env_float("PROFIT_ADJUSTMENT_FACTOR", 0.5)

    # --- Commissions ---
    # Alpaca's Order model doesn't return per-fill commissions, so we estimate
    # them from a flat per-contract fee. Default reflects typical OCC/exchange
    # fees on option orders.
    commission_per_contract: float = _env_float("COMMISSION_PER_CONTRACT", 0.0)

    # --- Trade timing windows (ET) ---
    open_window_minutes_before_close: int = _env_int("OPEN_WINDOW_MIN_BEFORE_CLOSE", 25)
    open_window_length_minutes: int = _env_int("OPEN_WINDOW_LENGTH_MIN", 40)
    close_window_minutes_after_open: int = _env_int("CLOSE_WINDOW_MIN_AFTER_OPEN", 15)

    # --- Calendar spread construction ---
    back_month_target_days: int = _env_int("BACK_MONTH_TARGET_DAYS", 30)

    # --- Safety caps on order chase ---
    # Hard ceiling on how much we're willing to pay (as a fraction of
    # original open debit) to escape a losing position when closing.
    # 1.0 = never pay more than we paid to open; 2.0 = up to 2x.
    max_close_debit_multiple: float = _env_float("MAX_CLOSE_DEBIT_MULTIPLE", 1.0)
    # Absolute per-share cap on close-order debit, applied in addition to
    # the multiple above.
    max_close_debit_abs: float = _env_float("MAX_CLOSE_DEBIT_ABS", 10.0)

    # --- Network ---
    http_timeout_sec: float = _env_float("HTTP_TIMEOUT_SEC", 15.0)
    http_retries: int = _env_int("HTTP_RETRIES", 3)

    # --- Endpoints ---
    google_script_url: str | None = os.environ.get("GOOGLE_SCRIPT_URL")
    # Optional shared secret posted as the `auth` field. When unset, no
    # auth field is sent (matching legacy behavior).
    google_script_token: str | None = os.environ.get("GOOGLE_SCRIPT_TOKEN")

    # --- Storage ---
    db_path: str = os.environ.get("TRADES_DB_PATH", "trades.db")


SETTINGS = Settings()

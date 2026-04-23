"""Orchestrates the daily open/close workflow.

Behavioral changes versus the legacy version:

* A single ``_open_trade_for`` helper replaces the copy-pasted BMO/AMC
  loops.
* All ``print`` calls are replaced with the project logger.
* ``trades`` table gets a unique index on (Ticker, Open Date, Short
  Symbol) so retried runs can't double-record the same fill.
* HTTP calls to the Dolthub earnings endpoint use a timeout and a small
  retry loop instead of blocking indefinitely on network stalls.
* ``close_calendar_spread_order`` is called with the original open
  debit so the creeping close can't chase unbounded losses.
"""

from __future__ import annotations

import os
import queue
import sqlite3
import sys
import threading
import time as time_mod
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from alpaca.trading.enums import PositionIntent
from dotenv import load_dotenv

from alpaca_integration import (
    close_calendar_spread_order,
    close_single_option_leg_order,
    get_alpaca_option_chain,
    get_option_spread_mid_price,
    get_portfolio_value,
    get_single_option_quotes,
    init_alpaca_client,
    monitor_fill_async,
    place_calendar_spread_order,
    select_expiries_and_strike_alpaca,
)
from automation import (
    compute_recommendation,
    get_todays_earnings,
    get_tomorrows_earnings,
)
from config import SETTINGS
from log import get_logger

load_dotenv()
log = get_logger(__name__)

trade_fill_queue: queue.Queue = queue.Queue()
trade_monitor_threads: list[threading.Thread] = []


# ---------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------

def init_db() -> None:
    """Create the trades table and a uniqueness index if missing."""
    conn = sqlite3.connect(SETTINGS.db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
           "Ticker" TEXT,
           "Implied Move" TEXT,
           "Structure" TEXT,
           "Side" TEXT,
           "When" TEXT,
           "Size" INTEGER,
           "Short Symbol" TEXT,
           "Long Symbol" TEXT,
           "Open Date" TEXT,
           "Open Price" REAL,
           "Open Comm." REAL,
           "Close Date" TEXT,
           "Close Price" REAL,
           "Close Comm." REAL
        )
        """
    )
    cur.execute("PRAGMA table_info(trades)")
    cols = [row[1] for row in cur.fetchall()]
    if "When" not in cols:
        cur.execute('ALTER TABLE trades ADD COLUMN "When" TEXT')
    cur.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_open '
        'ON trades("Ticker", "Open Date", "Short Symbol")'
    )
    conn.commit()
    conn.close()


init_db()


def get_total_profit() -> float:
    """Cumulative realized profit across closed trades.

    If ``KELLY_USE_RAW_EQUITY`` is true, this is informational only and
    no longer feeds Kelly sizing. Otherwise returns the legacy
    PROFIT_ADJUSTMENT_FACTOR-scaled value used to keep bets sized from
    original principal.
    """
    try:
        conn = sqlite3.connect(SETTINGS.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT SUM(("Close Price" - "Open Price") * "Size" * 100
                       - "Open Comm." - "Close Comm.") AS profit
            FROM trades
            WHERE "Close Date" IS NOT NULL AND "Close Date" != ''
            """
        )
        result = cur.fetchone()[0]
        conn.close()
        if result is None or result <= 0:
            return 0.0
        adjusted = result * SETTINGS.profit_adjustment_factor
        log.info("Realized profit: $%.2f (scaled: $%.2f)", result, adjusted)
        return adjusted
    except Exception as e:  # noqa: BLE001
        log.exception("Error calculating profit: %s", e)
        return 0.0


def _post_to_google(trade_data: dict) -> Optional[str]:
    """POST a payload to the Google Apps Script, with timeout + optional auth."""
    url = SETTINGS.google_script_url
    if not url:
        log.debug("GOOGLE_SCRIPT_URL unset; skipping remote post.")
        return None
    if SETTINGS.google_script_token:
        trade_data = {**trade_data, "auth": SETTINGS.google_script_token}
    try:
        r = requests.post(url, json=trade_data, timeout=SETTINGS.http_timeout_sec)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        log.warning("Google Script post failed: %s", e)
        return None


def post_trade(trade_data: dict):
    """Record a newly-opened trade locally and push to Google Sheets."""
    trade_data = {**trade_data, "action": "create"}
    trade_data.setdefault("Open Comm.", 0)
    trade_data.setdefault("Close Comm.", 0)

    try:
        conn = sqlite3.connect(SETTINGS.db_path)
        cur = conn.cursor()
        # INSERT OR IGNORE relies on the uq_trades_open unique index so
        # re-runs on the same day don't double-post the same position.
        cur.execute(
            """INSERT OR IGNORE INTO trades
               ("Ticker","Implied Move","Structure","Side","When","Size",
                "Short Symbol","Long Symbol","Open Date","Open Price",
                "Open Comm.","Close Date","Close Price","Close Comm.")
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade_data.get("Ticker"),
                trade_data.get("Implied Move"),
                trade_data.get("Structure"),
                trade_data.get("Side"),
                trade_data.get("When"),
                trade_data.get("Size"),
                trade_data.get("Short Symbol"),
                trade_data.get("Long Symbol"),
                trade_data.get("Open Date"),
                trade_data.get("Open Price"),
                trade_data.get("Open Comm.", 0),
                trade_data.get("Close Date"),
                trade_data.get("Close Price"),
                trade_data.get("Close Comm.", 0),
            ),
        )
        was_new = cur.rowcount > 0
        conn.commit()
        conn.close()
        if not was_new:
            log.info(
                "Trade %s@%s already recorded; skipping duplicate insert.",
                trade_data.get("Ticker"), trade_data.get("Open Date"),
            )
            return None
    except Exception as e:  # noqa: BLE001
        log.exception("SQLite insert error: %s", e)

    return _post_to_google(trade_data)


def get_open_trades() -> list[dict]:
    try:
        conn = sqlite3.connect(SETTINGS.db_path)
        cur = conn.cursor()
        cur.execute(
            'SELECT * FROM trades WHERE "Close Date" IS NULL OR "Close Date" = \'\''
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:  # noqa: BLE001
        log.exception("Error fetching open trades: %s", e)
        return []


def update_trade(trade_data: dict):
    try:
        conn = sqlite3.connect(SETTINGS.db_path)
        cur = conn.cursor()
        cur.execute(
            """UPDATE trades
                  SET "Close Date"  = ?,
                      "Close Price" = ?,
                      "Close Comm." = ?
                WHERE "Ticker"     = ?
                  AND "Open Date"  = ?""",
            (
                trade_data.get("Close Date"),
                trade_data.get("Close Price"),
                trade_data.get("Close Comm.", 0),
                trade_data.get("Ticker"),
                trade_data.get("Open Date"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        log.exception("SQLite update error: %s", e)

    return _post_to_google({**trade_data, "action": "update"})


# ---------------------------------------------------------------------
# Time window helpers
# ---------------------------------------------------------------------

def is_time_to_open(earnings_date, when: str) -> bool:
    eastern = ZoneInfo("America/New_York")
    now = datetime.now(tz=eastern)
    market_close = time(16, 0)
    anchor_date = (
        earnings_date - timedelta(days=1) if when == "BMO" else earnings_date
    )
    open_dt = datetime.combine(anchor_date, market_close, tzinfo=eastern) - timedelta(
        minutes=SETTINGS.open_window_minutes_before_close
    )
    return open_dt <= now < open_dt + timedelta(
        minutes=SETTINGS.open_window_length_minutes
    )


def is_time_to_close(earnings_date, when: str) -> bool:
    eastern = ZoneInfo("America/New_York")
    now = datetime.now(tz=eastern)
    open_time = time(9, 30)
    anchor_date = (
        earnings_date if when == "BMO" else earnings_date + timedelta(days=1)
    )
    close_dt = datetime.combine(anchor_date, open_time, tzinfo=eastern) + timedelta(
        minutes=SETTINGS.close_window_minutes_after_open
    )
    return now >= close_dt


# ---------------------------------------------------------------------
# Yahoo fallbacks
# ---------------------------------------------------------------------

def select_expiries_and_strike_yahoo(stock, earnings_date):
    try:
        exp_dates = sorted(
            datetime.strptime(d, "%Y-%m-%d").date() for d in stock.options
        )
        expiry_short = next((d for d in exp_dates if d > earnings_date), None)
        if not expiry_short:
            return None, None, None
        target_back = expiry_short + timedelta(days=SETTINGS.back_month_target_days)
        expiry_long = min(
            (d for d in exp_dates if d > expiry_short),
            key=lambda d: abs((d - target_back).days),
            default=None,
        )
        if not expiry_long:
            return None, None, None
        underlying_price = stock.history(period="1d")["Close"].iloc[0]
        chain = stock.option_chain(expiry_short.strftime("%Y-%m-%d"))
        strikes = chain.calls["strike"].tolist()
        strike = min(strikes, key=lambda x: abs(x - underlying_price))
        return (
            expiry_short.strftime("%Y-%m-%d"),
            expiry_long.strftime("%Y-%m-%d"),
            strike,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Yahoo expiry/strike error: %s", e)
        return None, None, None


def calculate_calendar_spread_cost_yahoo(stock, expiry_short, expiry_long, strike):
    try:
        chain_short = stock.option_chain(expiry_short)
        chain_long = stock.option_chain(expiry_long)
        call_short = chain_short.calls.loc[chain_short.calls["strike"] == strike]
        call_long = chain_long.calls.loc[chain_long.calls["strike"] == strike]
        if call_short.empty or call_long.empty:
            return None
        short_mid = (call_short["bid"].iloc[0] + call_short["ask"].iloc[0]) / 2
        long_mid = (call_long["bid"].iloc[0] + call_long["ask"].iloc[0]) / 2
        return float(long_mid - short_mid)
    except Exception as e:  # noqa: BLE001
        log.exception("Yahoo spread-cost error: %s", e)
        return None


# ---------------------------------------------------------------------
# Consolidated open path
# ---------------------------------------------------------------------

def _open_trade_for(
    ticker_info: dict,
    when_norm: str,
    earnings_date,
    sizing_equity: float,
):
    """Screen, size, and submit one calendar spread.

    Replaces the copy-pasted BMO and AMC blocks in the old workflow.
    Returns None; fill callbacks push rows onto ``trade_fill_queue``.
    """
    ticker = ticker_info["act_symbol"]
    if not is_time_to_open(earnings_date, when_norm):
        log.debug("%s: not in open window for %s", ticker, when_norm)
        return

    try:
        rec = compute_recommendation(ticker)
    except Exception as e:  # noqa: BLE001
        log.warning("compute_recommendation(%s) raised: %s", ticker, e)
        return
    if not (
        isinstance(rec, dict)
        and rec.get("avg_volume")
        and rec.get("iv30_rv30")
        and rec.get("ts_slope_0_45")
    ):
        log.info("%s failed screening: %s", ticker, rec)
        return

    # BMO allows same-day expiry; filter from one day earlier.
    filter_date = (
        earnings_date - timedelta(days=1) if when_norm == "BMO" else earnings_date
    )

    expiry_short, expiry_long, strike = select_expiries_and_strike_alpaca(
        ticker, filter_date
    )
    if not (expiry_short and expiry_long and strike):
        stock = yf.Ticker(ticker)
        expiry_short, expiry_long, strike = select_expiries_and_strike_yahoo(
            stock, filter_date
        )
    if not (expiry_short and expiry_long and strike):
        log.info("%s: could not resolve expiries/strike; skipping.", ticker)
        return

    spread_cost = get_option_spread_mid_price(ticker, expiry_short, expiry_long, strike)
    if spread_cost is None:
        stock = yf.Ticker(ticker)
        spread_cost = calculate_calendar_spread_cost_yahoo(
            stock, expiry_short, expiry_long, strike
        )
    if spread_cost is None or spread_cost <= 0:
        log.info("%s: invalid spread cost (%s); skipping.", ticker, spread_cost)
        return

    chain = get_alpaca_option_chain(ticker) or {}
    short_contract = chain.get(expiry_short, {}).get(strike, {}).get("call")
    long_contract = chain.get(expiry_long, {}).get(strike, {}).get("call")
    short_symbol = getattr(short_contract, "symbol", None)
    long_symbol = getattr(long_contract, "symbol", None)
    if not (short_symbol and long_symbol):
        log.info("%s: missing OCC symbols from chain; skipping.", ticker)
        return

    max_allocation = sizing_equity * SETTINGS.kelly_fraction
    quantity = int(max_allocation // (spread_cost * 100))
    if quantity < 1:
        log.info(
            "%s: Kelly allocation $%.2f yields 0 contracts at $%.2f; skipping.",
            ticker, max_allocation, spread_cost,
        )
        return

    implied_move = rec.get("expected_move", "")
    log.info(
        "Opening %s %s: %dx %s/%s @ %s cost=$%.2f alloc=$%.2f implied=%s",
        when_norm, ticker, quantity, expiry_short, expiry_long, strike,
        spread_cost, max_allocation, implied_move,
    )

    base = {
        "Short Symbol": short_symbol,
        "Long Symbol": long_symbol,
        "Ticker": ticker,
        "Implied Move": implied_move,
        "Structure": "Calendar Spread",
        "Side": "debit",
        "When": when_norm,
        "Close Date": "",
        "Close Price": "",
        "Close Comm.": "",
    }

    def _on_filled(filled, base_data=base):
        row = base_data.copy()
        row["Open Date"] = datetime.now().strftime("%Y-%m-%d")
        row["Open Price"] = float(getattr(filled, "filled_avg_price", 0) or 0)
        row["Size"] = int(float(getattr(filled, "filled_qty", 0) or 0))
        row["Open Comm."] = getattr(filled, "commission", 0) or 0
        if row["Size"] > 0:
            trade_fill_queue.put((post_trade, row))
        else:
            log.warning("%s: open-fill callback fired with 0 qty.", base_data["Ticker"])

    status = place_calendar_spread_order(
        short_symbol,
        long_symbol,
        quantity,
        limit_price=spread_cost,
        on_filled=_on_filled,
        max_total_cost_allowed=max_allocation,
        target_debit_price=spread_cost,
    )
    if status is None:
        log.info("%s: no fill confirmed.", ticker)


# ---------------------------------------------------------------------
# Close path (structurally unchanged — just logging + open-debit cap)
# ---------------------------------------------------------------------

def _close_due_trades(client) -> None:
    for trade in get_open_trades():
        try:
            open_date = datetime.strptime(trade["Open Date"], "%Y-%m-%d").date()
            when = trade.get("When", "AMC")
            earnings_date = (
                open_date + timedelta(days=1) if when == "BMO" else open_date
            )
            if not is_time_to_close(earnings_date, when):
                continue

            log.info("Closing %s (opened %s, when=%s)", trade["Ticker"], open_date, when)

            def _on_close_filled(filled, t=trade):
                cp = float(getattr(filled, "filled_avg_price", 0) or 0)
                cc = getattr(filled, "commission", 0) or 0
                trade_fill_queue.put(
                    (
                        update_trade,
                        {
                            "Ticker": t["Ticker"],
                            "Open Date": t["Open Date"],
                            "Close Date": datetime.now().strftime("%Y-%m-%d"),
                            "Close Price": cp,
                            "Close Comm.": cc,
                        },
                    )
                )

            order = close_calendar_spread_order(
                trade.get("Short Symbol"),
                trade.get("Long Symbol"),
                trade.get("Size"),
                original_open_debit=trade.get("Open Price"),
            )
            if order:
                th = monitor_fill_async(client, order, _on_close_filled)
                trade_monitor_threads.append(th)
                continue

            # Spread close failed: probe legs individually.
            _close_fallback_legs(client, trade)
        except Exception as e:  # noqa: BLE001
            log.exception("Error closing trade %s: %s", trade.get("Ticker"), e)


def _close_fallback_legs(client, trade: dict) -> None:
    short_symbol = trade.get("Short Symbol")
    long_symbol = trade.get("Long Symbol")
    size = trade.get("Size") or 0

    def _quotable(sym: Optional[str]) -> bool:
        if not sym or size <= 0:
            return False
        try:
            get_single_option_quotes(sym)
            return True
        except RuntimeError:
            return False
        except Exception as e:  # noqa: BLE001
            log.warning("%s quotability check error: %s", sym, e)
            return False

    short_q = _quotable(short_symbol)
    long_q = _quotable(long_symbol)

    if not short_q and long_q:
        order = close_single_option_leg_order(long_symbol, size, PositionIntent.SELL_TO_CLOSE)
        if order:
            def _cb(filled, t=trade):
                cp = float(getattr(filled, "filled_avg_price", 0) or 0)
                cc = getattr(filled, "commission", 0) or 0
                trade_fill_queue.put((update_trade, {
                    "Ticker": t["Ticker"], "Open Date": t["Open Date"],
                    "Close Date": datetime.now().strftime("%Y-%m-%d"),
                    "Close Price": cp, "Close Comm.": cc,
                }))
            trade_monitor_threads.append(monitor_fill_async(client, order, _cb))
    elif short_q and not long_q:
        order = close_single_option_leg_order(short_symbol, size, PositionIntent.BUY_TO_CLOSE)
        if order:
            def _cb(filled, t=trade):
                cp = float(getattr(filled, "filled_avg_price", 0) or 0)
                cc = getattr(filled, "commission", 0) or 0
                trade_fill_queue.put((update_trade, {
                    "Ticker": t["Ticker"], "Open Date": t["Open Date"],
                    "Close Date": datetime.now().strftime("%Y-%m-%d"),
                    "Close Price": -cp, "Close Comm.": cc,
                }))
            trade_monitor_threads.append(monitor_fill_async(client, order, _cb))
    elif not short_q and not long_q:
        log.warning(
            "%s: both legs unquotable; marking closed at $0.",
            trade["Ticker"],
        )
        trade_fill_queue.put((update_trade, {
            "Ticker": trade["Ticker"], "Open Date": trade["Open Date"],
            "Close Date": datetime.now().strftime("%Y-%m-%d"),
            "Close Price": 0, "Close Comm.": 0,
        }))
    else:
        log.warning(
            "%s: both legs quotable but spread close failed; manual review.",
            trade["Ticker"],
        )


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def _dolthub_earnings_safe(fetch_fn, label: str) -> list[dict]:
    last_err = None
    for attempt in range(SETTINGS.http_retries):
        try:
            return fetch_fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("%s earnings fetch attempt %d failed: %s", label, attempt + 1, e)
            time_mod.sleep(2 ** attempt)
    log.error("%s earnings fetch gave up: %s", label, last_err)
    return []


def run_trade_workflow():
    log.info("Running trade workflow")
    trade_monitor_threads.clear()
    while not trade_fill_queue.empty():
        trade_fill_queue.get()

    # Fresh forks / dry runs may not have broker creds set. That's not a
    # CI failure — it's just "nothing to do".
    if not os.environ.get("APCA_API_KEY_ID") or not os.environ.get("APCA_API_SECRET_KEY"):
        log.warning("APCA_API_KEY_ID / APCA_API_SECRET_KEY not set; skipping run.")
        return 0

    client = init_alpaca_client()
    if not client:
        log.warning("Alpaca client unavailable; skipping run.")
        return 0
    try:
        clock = client.get_clock()
    except Exception as e:  # noqa: BLE001
        log.warning("Alpaca clock lookup failed (%s); skipping run.", e)
        return 0
    if not getattr(clock, "is_open", False):
        log.info("Market closed (next open %s); nothing to do.", clock.next_open)
        return 0
    log.info("Market open (server time %s).", clock.timestamp)

    _close_due_trades(client)

    for th in trade_monitor_threads:
        th.join()
    while not trade_fill_queue.empty():
        func, data = trade_fill_queue.get()
        func(data)
    trade_monitor_threads.clear()

    eastern = ZoneInfo("America/New_York")
    now = datetime.now(tz=eastern)
    if now.time() < time(12, 0):
        log.info("Morning run: close-only; skipping opens.")
        return 0

    todays = _dolthub_earnings_safe(get_todays_earnings, "today")
    tomorrows = _dolthub_earnings_safe(get_tomorrows_earnings, "tomorrow")

    portfolio_value = get_portfolio_value()
    if not portfolio_value:
        log.warning("No portfolio value; skipping opens.")
        return 0

    if SETTINGS.kelly_use_raw_equity:
        sizing_equity = portfolio_value
        log.info("Sizing on raw equity: $%.2f", sizing_equity)
    else:
        total_profit = get_total_profit()
        sizing_equity = portfolio_value - total_profit
        log.info(
            "Sizing equity $%.2f (raw=$%.2f − profit=$%.2f)",
            sizing_equity, portfolio_value, total_profit,
        )

    tomorrow_date = datetime.now().date() + timedelta(days=1)
    today_date = datetime.now().date()

    for info in tomorrows:
        when = (info.get("when") or "").lower()
        if "before" in when:
            _open_trade_for(info, "BMO", tomorrow_date, sizing_equity)

    for info in todays:
        when = (info.get("when") or "").lower()
        if "before" not in when:  # AMC or anything not explicitly BMO
            _open_trade_for(info, "AMC", today_date, sizing_equity)

    for th in trade_monitor_threads:
        th.join()
    while not trade_fill_queue.empty():
        func, data = trade_fill_queue.get()
        func(data)
    return 0


if __name__ == "__main__":
    sys.exit(run_trade_workflow())

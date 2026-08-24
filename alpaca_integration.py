"""Alpaca order and market-data helpers.

This module concentrates all Alpaca SDK usage so the rest of the project
can stay SDK-agnostic. Key changes versus the legacy version:

* A single ``TradingClient`` is cached per process rather than being
  re-created on every call.
* ``close_calendar_spread_order`` accepts an ``original_open_debit`` and
  caps the chase at ``min(open_debit * max_close_debit_multiple,
  max_close_debit_abs)`` so a stuck close can't pay through the roof.
* Commission is estimated from ``COMMISSION_PER_CONTRACT`` because the
  Alpaca ``Order`` object does not expose per-fill fees.
* Dead code (unused ratio computations, never-read flags) is removed.
"""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import (
    OptionLatestQuoteRequest,
    StockLatestBarRequest,
)
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    OrderStatus,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.models import Position
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    LimitOrderRequest,
    OptionLegRequest,
)
from dotenv import load_dotenv

from config import SETTINGS
from log import get_logger

load_dotenv()
log = get_logger(__name__)

API_KEY = os.environ.get("APCA_API_KEY_ID")
API_SECRET = os.environ.get("APCA_API_SECRET_KEY")
PAPER = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

# Cached clients — the SDK's underlying HTTP session is reusable and the
# TradingClient was previously re-instantiated dozens of times per run.
_trading_client: Optional[TradingClient] = None
_option_data_client: Optional[OptionHistoricalDataClient] = None
_stock_data_client: Optional[StockHistoricalDataClient] = None


def init_alpaca_client() -> Optional[TradingClient]:
    global _trading_client
    if _trading_client is not None:
        return _trading_client
    try:
        _trading_client = TradingClient(API_KEY, API_SECRET, paper=PAPER)
        log.info("Alpaca TradingClient initialized (paper=%s)", PAPER)
        return _trading_client
    except Exception as e:  # noqa: BLE001 — surface any init failure
        log.exception("Error initializing Alpaca client: %s", e)
        return None


def _option_client() -> OptionHistoricalDataClient:
    global _option_data_client
    if _option_data_client is None:
        _option_data_client = OptionHistoricalDataClient(
            api_key=API_KEY, secret_key=API_SECRET
        )
    return _option_data_client


def _stock_client() -> StockHistoricalDataClient:
    global _stock_data_client
    if _stock_data_client is None:
        _stock_data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
    return _stock_data_client


def _estimated_commission(filled_qty: int) -> float:
    """Alpaca's Order object does not expose per-fill commissions, so we
    estimate OCC/exchange fees from a flat per-contract rate."""
    if filled_qty <= 0:
        return 0.0
    return float(filled_qty) * SETTINGS.commission_per_contract


def place_calendar_spread_order(
    short_symbol: str,
    long_symbol: str,
    original_intended_quantity: int,
    limit_price: Optional[float] = None,
    on_filled: Optional[Callable] = None,
    max_total_cost_allowed: Optional[float] = None,
    target_debit_price: Optional[float] = None,
):
    """Open a call calendar spread with a creeping-limit chase.

    The limit crawls from the current spread mid up to the market ask,
    slicing each fill to stay within ``max_total_cost_allowed``. Returns
    a synthetic summary order when at least one contract filled, else
    ``None``.
    """
    client = init_alpaca_client()
    if not client:
        return None
    if original_intended_quantity < 1:
        log.info(
            "Quantity for %s/%s is <1; skipping open.", short_symbol, long_symbol
        )
        return None

    try:
        short_bid, short_ask, long_bid, long_ask = get_spread_quotes(
            short_symbol, long_symbol
        )
        log.info(
            "Initial quotes %s bid=%.2f ask=%.2f | %s bid=%.2f ask=%.2f",
            short_symbol, short_bid, short_ask, long_symbol, long_bid, long_ask,
        )

        current_mid = (long_bid + long_ask) / 2 - (short_bid + short_ask) / 2
        price_to_chase = current_mid
        chase_step = max(
            ((short_ask - short_bid) + (long_ask - long_bid)) / 2.0, 0.01
        )

        # Cap chase at the long-leg ask (worst realistic market debit).
        effective_max_chase = long_ask
        if target_debit_price is not None:
            log.info(
                "Ideal target debit for %s/%s: $%.2f; chasing up to $%.2f",
                short_symbol, long_symbol, target_debit_price, effective_max_chase,
            )

        remaining_qty = original_intended_quantity
        total_cost_so_far = 0.0
        total_filled_qty = 0
        total_filled_value = 0.0
        total_commission = 0.0
        last_order_details = None

        while remaining_qty > 0 and price_to_chase <= effective_max_chase:
            attempt_price = round(price_to_chase, 2)

            affordable = remaining_qty
            remaining_budget = math.inf
            if max_total_cost_allowed is not None:
                remaining_budget = max_total_cost_allowed - total_cost_so_far
                if attempt_price <= 0:
                    affordable = remaining_qty if remaining_budget >= 0 else 0
                elif remaining_budget > 0:
                    affordable = math.floor(remaining_budget / (attempt_price * 100))
                else:
                    affordable = 0

            qty_this_order = min(remaining_qty, affordable)
            if qty_this_order < 1:
                log.info(
                    "Stopping chase for %s/%s at $%.2f: budget=$%.2f, qty_needed=%d",
                    short_symbol, long_symbol, attempt_price,
                    remaining_budget if remaining_budget != math.inf else -1,
                    remaining_qty,
                )
                break

            req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                qty=qty_this_order,
                legs=[
                    OptionLegRequest(
                        symbol=short_symbol,
                        ratio_qty=1,
                        side=OrderSide.SELL,
                        position_intent=PositionIntent.SELL_TO_OPEN,
                    ),
                    OptionLegRequest(
                        symbol=long_symbol,
                        ratio_qty=1,
                        side=OrderSide.BUY,
                        position_intent=PositionIntent.BUY_TO_OPEN,
                    ),
                ],
                limit_price=attempt_price,
            )

            submitted = None
            try:
                submitted = client.submit_order(req)
                log.info(
                    "Open %s/%s qty=%d @ $%.2f (order=%s)",
                    short_symbol, long_symbol, qty_this_order, attempt_price,
                    submitted.id,
                )
                filled = wait_for_fill(client, submitted.id, timeout=30)
                filled_qty = int(float(getattr(filled, "filled_qty", 0) or 0))
                if filled_qty > 0:
                    avg = float(getattr(filled, "filled_avg_price", 0) or 0)
                    total_cost_so_far += avg * filled_qty * 100
                    remaining_qty -= filled_qty
                    total_filled_qty += filled_qty
                    total_filled_value += avg * filled_qty
                    total_commission += _estimated_commission(filled_qty)
                    last_order_details = filled
                    log.info(
                        "Order %s filled %d @ $%.4f; remaining=%d cost=$%.2f",
                        submitted.id, filled_qty, avg, remaining_qty,
                        total_cost_so_far,
                    )
                if remaining_qty <= 0:
                    break
                if (
                    filled_qty < qty_this_order
                    and getattr(filled, "status", None) != OrderStatus.CANCELED
                ):
                    _safe_cancel(client, submitted.id)
            except TimeoutError:
                log.warning(
                    "Open order %s for %s/%s timed out; cancelling.",
                    getattr(submitted, "id", "N/A"), short_symbol, long_symbol,
                )
                if submitted:
                    _safe_cancel(client, submitted.id)
            except Exception as e:  # noqa: BLE001
                log.exception("Open submission error for %s/%s: %s",
                              short_symbol, long_symbol, e)

            price_to_chase += chase_step

        if total_filled_qty > 0:
            summary = _SummaryOrder(
                filled_avg_price=str(total_filled_value / total_filled_qty),
                filled_qty=str(total_filled_qty),
                commission=total_commission,
                id=getattr(last_order_details, "id", "cumulative_open_fill"),
                symbol=short_symbol,
            )
            if on_filled:
                on_filled(summary)
            return summary

        log.warning(
            "No quantity filled for %s/%s within price limits", short_symbol, long_symbol
        )
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("place_calendar_spread_order error for %s/%s: %s",
                      short_symbol, long_symbol, e)
        return None


def close_calendar_spread_order(
    short_symbol: str,
    long_symbol: str,
    quantity: int,
    original_open_debit: Optional[float] = None,
):
    """Close both legs with a creeping-limit chase.

    Starts at the most aggressive credit (sell long at ask, buy short at
    bid) and crawls toward a bounded max-debit cap. The cap is
    ``min(original_open_debit * MAX_CLOSE_DEBIT_MULTIPLE,
    MAX_CLOSE_DEBIT_ABS)`` when ``original_open_debit`` is known, else
    it falls back to ``MAX_CLOSE_DEBIT_ABS``.
    """
    client = init_alpaca_client()
    if not client:
        return None
    try:
        short_bid, short_ask, long_bid, long_ask = get_spread_quotes(
            short_symbol, long_symbol
        )
        log.info(
            "Close quotes %s bid=%.2f ask=%.2f | %s bid=%.2f ask=%.2f",
            short_symbol, short_bid, short_ask, long_symbol, long_bid, long_ask,
        )

        initial_target_credit = long_ask - short_bid
        # Signed limit: negative = credit received, positive = debit paid.
        price = -initial_target_credit

        # --- Bounded max debit on close, replaces legacy behavior which
        # chased all the way to short_ask and could blow out losses. ---
        abs_cap = SETTINGS.max_close_debit_abs
        if original_open_debit is not None and original_open_debit > 0:
            abs_cap = min(
                abs_cap,
                original_open_debit * SETTINGS.max_close_debit_multiple,
            )
        max_debit_cap = abs_cap

        step = max(
            ((short_ask - short_bid) + (long_ask - long_bid)) / 2.0, 0.01
        )
        remaining = quantity
        last_order = None
        total_filled_qty = 0
        total_filled_value = 0.0

        while remaining > 0 and price <= max_debit_cap:
            lp = round(price, 2)
            req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                qty=remaining,
                legs=[
                    OptionLegRequest(
                        symbol=short_symbol,
                        ratio_qty=1,
                        side=OrderSide.BUY,
                        position_intent=PositionIntent.BUY_TO_CLOSE,
                    ),
                    OptionLegRequest(
                        symbol=long_symbol,
                        ratio_qty=1,
                        side=OrderSide.SELL,
                        position_intent=PositionIntent.SELL_TO_CLOSE,
                    ),
                ],
                limit_price=lp,
            )
            try:
                last_order = client.submit_order(req)
                log.info("Close %s/%s qty=%d @ $%.2f (order=%s)",
                         short_symbol, long_symbol, remaining, lp, last_order.id)
                filled = wait_for_fill(client, last_order.id, timeout=60)
                filled_qty = int(float(getattr(filled, "filled_qty", 0) or 0))
                if filled_qty > 0:
                    avg = float(getattr(filled, "filled_avg_price", 0) or 0)
                    total_filled_qty += filled_qty
                    total_filled_value += avg * filled_qty
                    remaining -= filled_qty
                    last_order = filled
                if remaining <= 0:
                    break
                if getattr(filled, "status", None) != OrderStatus.CANCELED:
                    _safe_cancel(client, last_order.id)
            except TimeoutError:
                if last_order:
                    _safe_cancel(client, last_order.id)
            price += step

        if total_filled_qty == 0:
            return None

        avg_price = total_filled_value / total_filled_qty
        return _SummaryOrder(
            filled_avg_price=str(avg_price),
            filled_qty=str(total_filled_qty),
            commission=_estimated_commission(total_filled_qty),
            id=getattr(last_order, "id", "cumulative_close_fill"),
            symbol=short_symbol,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("close_calendar_spread_order error: %s", e)
        return None


def close_single_option_leg_order(
    symbol: str,
    quantity: int,
    position_intent: PositionIntent,
):
    """Close a single option leg with a creeping limit order.

    Used as a fallback when one side of a spread has expired/gone
    unquotable and the other side still has a tradable market.
    """
    client = init_alpaca_client()
    if not client:
        log.error("No Alpaca client; cannot close %s", symbol)
        return None
    try:
        bid, ask = get_single_option_quotes(symbol)
        log.info("Close-single %s bid=%.2f ask=%.2f", symbol, bid, ask)

        if position_intent == PositionIntent.SELL_TO_CLOSE:
            side = OrderSide.SELL
            price_to_chase, price_limit, step_direction = ask, bid, -1
        elif position_intent == PositionIntent.BUY_TO_CLOSE:
            side = OrderSide.BUY
            price_to_chase, price_limit, step_direction = bid, ask, 1
        else:
            log.error("Invalid position_intent %s", position_intent)
            return None

        step = max((ask - bid) / 10, 0.01)
        remaining = quantity
        last = None
        total_filled_qty = 0
        total_filled_value = 0.0

        while remaining > 0:
            lp = round(price_to_chase, 2)
            if (step_direction == 1 and lp > price_limit) or (
                step_direction == -1 and lp < price_limit
            ):
                break
            req = LimitOrderRequest(
                symbol=symbol,
                qty=remaining,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=lp,
                order_class=OrderClass.SIMPLE,
                position_intent=position_intent,
            )
            submitted = None
            try:
                submitted = client.submit_order(req)
                filled = wait_for_fill(client, submitted.id, timeout=30)
                fq = int(float(getattr(filled, "filled_qty", 0) or 0))
                if fq > 0:
                    avg = float(getattr(filled, "filled_avg_price", 0) or 0)
                    total_filled_qty += fq
                    total_filled_value += avg * fq
                    remaining -= fq
                    last = filled
                if remaining <= 0:
                    break
                if getattr(filled, "status", None) != OrderStatus.CANCELED:
                    _safe_cancel(client, submitted.id)
            except TimeoutError:
                if submitted:
                    _safe_cancel(client, submitted.id)
            except Exception as e:  # noqa: BLE001
                log.exception("close-single loop error %s: %s", symbol, e)
                break
            price_to_chase += step * step_direction
            if remaining > 0:
                time.sleep(1)

        if total_filled_qty == 0 or last is None:
            return None
        avg_price = total_filled_value / total_filled_qty
        return _SummaryOrder(
            filled_avg_price=str(avg_price),
            filled_qty=str(total_filled_qty),
            commission=_estimated_commission(total_filled_qty),
            id=getattr(last, "id", "cumulative_single_close"),
            symbol=symbol,
        )
    except RuntimeError as e:
        log.warning("Quote error for %s: %s", symbol, e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("close_single_option_leg_order error for %s: %s", symbol, e)
        return None


def _safe_cancel(client: TradingClient, order_id: str) -> None:
    try:
        client.cancel_order_by_id(order_id)
    except APIError as e:
        if getattr(e, "status_code", None) != 422:
            raise
        log.debug("Order %s not cancelable (422); assumed filled/expired.", order_id)


class _SummaryOrder:
    """Minimal duck-type for consumer code that only reads a few attrs."""

    __slots__ = ("filled_avg_price", "filled_qty", "commission", "id", "symbol", "status")

    def __init__(self, filled_avg_price, filled_qty, commission, id, symbol):
        self.filled_avg_price = filled_avg_price
        self.filled_qty = filled_qty
        self.commission = commission
        self.id = id
        self.symbol = symbol
        self.status = OrderStatus.FILLED


def get_open_option_positions():
    client = init_alpaca_client()
    if not client:
        return []
    try:
        positions = client.get_all_positions()
        option_positions = [
            p for p in positions if isinstance(p, Position) and p.asset_class == "option"
        ]
        log.info("Open option positions: %d", len(option_positions))
        return option_positions
    except Exception as e:  # noqa: BLE001
        log.exception("Error fetching positions: %s", e)
        return []


def get_portfolio_value():
    client = init_alpaca_client()
    if not client:
        return None
    try:
        account = client.get_account()
        equity = float(account.equity)
        log.info("Portfolio equity: $%.2f", equity)
        return equity
    except Exception as e:  # noqa: BLE001
        log.exception("Error fetching portfolio value: %s", e)
        return None


def get_alpaca_option_chain(symbol: str):
    """Return ``{expiry: {strike: {"call"|"put": contract}}}`` or None."""
    client = init_alpaca_client()
    if not client:
        return None
    try:
        today = datetime.now().date()
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol.upper()],
            expiration_date_gte=today,
            limit=10000,
        )
        resp = client.get_option_contracts(req)
        chain: dict = {}
        for c in resp.option_contracts or []:
            expiry = c.expiration_date.strftime("%Y-%m-%d")
            strike = float(c.strike_price)
            chain.setdefault(expiry, {}).setdefault(strike, {})[c.type] = c
        return chain
    except Exception as e:  # noqa: BLE001
        log.exception("Error fetching option chain for %s: %s", symbol, e)
        return None


def select_expiries_and_strike_alpaca(symbol: str, earnings_date):
    chain = get_alpaca_option_chain(symbol)
    if not chain:
        return None, None, None
    try:
        exp_dates = sorted(
            datetime.strptime(d, "%Y-%m-%d").date() for d in chain.keys()
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
        bar_resp = _stock_client().get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=symbol)
        )
        if not bar_resp or symbol.upper() not in bar_resp:
            log.warning("No price data for %s", symbol)
            return None, None, None
        underlying_price = bar_resp[symbol.upper()].close
        strikes = list(chain[expiry_short.strftime("%Y-%m-%d")].keys())
        strike = min(strikes, key=lambda x: abs(x - underlying_price))
        return (
            expiry_short.strftime("%Y-%m-%d"),
            expiry_long.strftime("%Y-%m-%d"),
            strike,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Error selecting expiries/strike for %s: %s", symbol, e)
        return None, None, None


def _occ_symbol(symbol: str, expiry: str, strike: float, callput: str = "C") -> str:
    expiry_fmt = expiry.replace("-", "")[2:]
    strike_fmt = f"{int(float(strike) * 1000):08d}"
    return f"{symbol.upper()}{expiry_fmt}{callput.upper()}{strike_fmt}"


def get_option_spread_mid_price(symbol, expiry_short, expiry_long, strike, callput="C"):
    try:
        short_sym = _occ_symbol(symbol, expiry_short, strike, callput)
        long_sym = _occ_symbol(symbol, expiry_long, strike, callput)
        req = OptionLatestQuoteRequest(symbol_or_symbols=[short_sym, long_sym])
        resp = _option_client().get_option_latest_quote(req)
        qs, ql = resp.get(short_sym), resp.get(long_sym)
        if not qs or not ql:
            return None
        if None in (qs.bid_price, qs.ask_price, ql.bid_price, ql.ask_price):
            return None
        short_mid = (qs.bid_price + qs.ask_price) / 2
        long_mid = (ql.bid_price + ql.ask_price) / 2
        return float(long_mid - short_mid)
    except Exception as e:  # noqa: BLE001
        log.exception("spread mid-price error for %s: %s", symbol, e)
        return None


def wait_for_fill(client, order_id, timeout=30, interval=1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ord_ = client.get_order_by_id(order_id)
        if float(getattr(ord_, "filled_qty", 0) or 0) > 0:
            return ord_
        time.sleep(interval)
    raise TimeoutError(f"Order {order_id} not filled in {timeout}s")


def monitor_fill_async(client, order, on_filled, timeout=30, interval=1):
    def _poll():
        try:
            filled = wait_for_fill(client, order.id, timeout=timeout, interval=interval)
            on_filled(filled)
        except Exception as e:  # noqa: BLE001
            log.warning("Fill monitor error for %s: %s", getattr(order, "id", "?"), e)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    return t


def get_spread_quotes(short_symbol: str, long_symbol: str):
    req = OptionLatestQuoteRequest(symbol_or_symbols=[short_symbol, long_symbol])
    resp = _option_client().get_option_latest_quote(req)
    qs, ql = resp.get(short_symbol), resp.get(long_symbol)
    if not qs or not ql or None in (qs.bid_price, qs.ask_price, ql.bid_price, ql.ask_price):
        raise RuntimeError(f"Could not fetch bid/ask for {short_symbol} or {long_symbol}")
    return qs.bid_price, qs.ask_price, ql.bid_price, ql.ask_price


def get_single_option_quotes(symbol: str):
    req = OptionLatestQuoteRequest(symbol_or_symbols=[symbol])
    resp = _option_client().get_option_latest_quote(req)
    q = resp.get(symbol)
    if not q or q.bid_price is None or q.ask_price is None:
        raise RuntimeError(f"Could not fetch valid bid/ask for {symbol}")
    return q.bid_price, q.ask_price

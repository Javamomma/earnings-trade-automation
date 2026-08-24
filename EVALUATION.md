# Evaluation & Changes

This document captures the issues found during a top-to-bottom review of the
repository at `ProgramComputer/earnings-trade-automation@main` and the
improvements that were applied on this branch.

## Summary

The strategy logic (Kelly-sized ATM calendar spreads around earnings, gated
by an IV/RV ratio, term-structure slope, and volume threshold) is coherent.
The failure modes are almost all in the **execution layer**: unbounded
loss exposure on the close-side price chase, missing persistence
guardrails, a trade database committed to git and auto-pushed on every run,
unbounded network I/O, magic numbers sprinkled through the code, and
heavy duplication between the BMO and AMC open paths.

## Issues found

### Correctness / risk

1. **Unbounded chase on close** (*critical*). `close_calendar_spread_order`
   crawled the close limit price all the way up to `short_ask` — paying
   the full ask on the expensive short leg. In a blow-up scenario this
   could pay far more than the original open debit.
   **Fix**: cap chase at `min(open_debit × MAX_CLOSE_DEBIT_MULTIPLE,
   MAX_CLOSE_DEBIT_ABS)`. Defaults to paying no more to close than we paid
   to open.

2. **Commission is always zero** (*medium*). Alpaca's `Order` model does
   not expose `commission`; the legacy `getattr(filled, 'commission', 0)`
   silently returned `0` on every fill, so realized-profit math
   understates costs by OCC/exchange fees. **Fix**: estimate commissions
   from `COMMISSION_PER_CONTRACT` (configurable) at the point of fill.

3. **Anti-compounding Kelly** (*medium*). `sizing_equity = equity − profit`
   shrinks the bet as the account makes money. That's the opposite of
   Kelly. **Fix**: preserved the legacy default for backward compatibility
   but added `KELLY_USE_RAW_EQUITY=true` to switch to standard Kelly on
   raw equity.

4. **No duplicate-insert guard on trades** (*medium*). A retried or
   re-scheduled run could insert two rows for the same (Ticker, Open
   Date) pair. **Fix**: added a `UNIQUE INDEX` on
   `(Ticker, Open Date, Short Symbol)` and used `INSERT OR IGNORE`.

5. **Dolthub HTTP call has no timeout or retry** (*medium*). A network
   stall would hang the scheduled run. **Fix**: centralized `_http_get_json`
   with `HTTP_TIMEOUT_SEC` and `HTTP_RETRIES` (exponential backoff).

6. **Redundant mid-price fetches** (*low*). `get_option_spread_mid_price`
   was called twice per ticker on the open path; the second call was
   assigned to `limit_price` but the creeping loop re-reads quotes anyway.
   **Fix**: pass `spread_cost` as the initial limit.

### Operational / security

7. **`trades.db` is checked into git and auto-pushed** (*high*). The
   scheduled workflow did `git add trades.db && git push` on every run.
   This is fragile (merge conflicts across concurrent runs), bloats
   history, and exposes the entire trading record publicly on a fork.
   **Fix**: removed `trades.db` from version control, added it to
   `.gitignore`, and switched CI persistence to `actions/cache` plus an
   artifact upload.

8. **No concurrency guard on the workflow** (*high*). Two runs could
   race against the same brokerage account. **Fix**: `concurrency.group
   = earnings-trade-automation` (cancel-in-progress: false).

9. **`persist-credentials: true`** (*medium*). The checkout kept a
   git-write token on disk for the whole run. **Fix**: `persist-credentials:
   false`; we no longer push anything from CI.

10. **No auth on the Google Apps Script endpoint** (*medium*). Anyone
    with the deployed URL could write arbitrary rows. **Fix**: the Apps
    Script now checks an optional `AUTH_TOKEN` (via Script Properties)
    against a per-request `auth` field, and the Python client forwards
    `GOOGLE_SCRIPT_TOKEN` if set.

11. **Unpinned dependencies** (*medium*). `requirements.txt` had no
    versions, making installs non-deterministic — a breaking release of
    `alpaca-py` could take the bot down silently. **Fix**: pinned to
    known-working versions.

### Code quality

12. **BMO and AMC trade-open loops were duplicated** (~100 lines). **Fix**:
    single `_open_trade_for(ticker_info, when_norm, earnings_date,
    sizing_equity)` helper.

13. **Dead code**: `ratio_qty_short`, `ratio_qty_long`, `gcd_val`,
    `short_leg_closed_or_expired`, `long_leg_closed_or_expired`,
    unused `stock = yf.Ticker(ticker)` assignments, verbose
    `OrderClass.SIMPLE` preamble. Removed.

14. **`print` everywhere**. No timestamps or levels — hard to triage a
    live run. **Fix**: project-wide `log.get_logger(...)` with
    `LOG_LEVEL` env control.

15. **Magic numbers** (`1500000`, `1.25`, `-0.00406`, `0.06`, `25`,
    `40`, `15`, …). **Fix**: moved to `config.Settings` with
    environment-variable overrides.

16. **Client re-instantiation**. `TradingClient`, `OptionHistoricalDataClient`,
    and `StockHistoricalDataClient` were created on almost every call.
    **Fix**: process-level singletons.

## Not changed (deliberate)

- Strategy itself (thresholds, calendar structure, Kelly) — behavior is
  preserved at the defaults.
- Yahoo Finance fallback path — kept for resilience when Alpaca quotes
  are missing for illiquid tickers.
- Both-legs-unquotable "close at $0" fallback — preserved, though it's
  worth reviewing since it silently records a zero-value close rather
  than raising an alert.

## Suggested follow-ups (not implemented in this PR)

- Add a `tests/` suite covering `is_time_to_open`, `is_time_to_close`,
  the close-debit cap calculation, and `compute_recommendation` on
  fixture data. There are currently no tests.
- Replace the "both legs unquotable → close at $0" path with a
  notification (email/webhook) so manual review is triggered rather than
  silently masking bad state.
- Consider moving `trades.db` to a managed store (e.g. Turso / Postgres)
  so state isn't coupled to runner-local disk + GitHub cache eviction
  (~7 days).
- Split `compute_recommendation` into `_screen_with_alpaca` and
  `_screen_with_yahoo`; the 250-line function is the main testing
  obstacle.
- The `compute_recommendation` Alpaca path uses `DataFeed.IEX` bars,
  which skips most off-exchange volume. For screening that's probably
  fine, but the `avg_volume >= MIN_AVG_VOLUME` check currently uses
  Yahoo regardless — the inconsistency should be documented or
  unified.

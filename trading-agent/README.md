# trading-agent

A **research-only** speculative-trading research assistant. It reads
your watchlists, fetches market and SEC data, scans Reddit for ticker
mentions, scores everything, writes an Obsidian-friendly Markdown
brief, and pings Discord. It **never places orders**, has no broker
SDK, and does not authenticate with any brokerage.

## What it does

1. Reads `config/watchlists.yaml`.
2. For each ticker, fetches price, daily change, volume, and 30-day
   relative volume (yfinance).
3. Pulls recent SEC filings via EDGAR and flags S-1 / S-3 / 424B /
   ATM-language as dilution risk.
4. Scans Reddit JSON endpoints for ticker mentions across the
   configured subreddits, computes a fresh-vs-baseline acceleration
   ratio.
5. Scores each ticker on five axes:
   - momentum (price move × relative volume)
   - reddit acceleration (fresh / baseline mention rate)
   - dilution risk (recent dilutive filings)
   - meme risk (tag + reddit volume)
   - **research priority** (weighted blend, 0-100)
6. Writes `reports/daily/YYYY-MM-DD-morning-brief.md` with frontmatter,
   summary table, and per-ticker sections.
7. Posts a top-N Discord summary when any ticker crosses
   `PRIORITY_ALERT_THRESHOLD`.
8. Stores every brief and every per-ticker score row in a local SQLite
   journal (`data/journal.db`).

## What it does NOT do

- No brokerage API calls.
- No order placement.
- No paper-trade or simulated-trade execution.
- No background process or trading loop — runs are one-shot.

This is a writing assistant for your morning research session, not a
trading system.

## Layout

```
trading-agent/
  config/watchlists.yaml      <- tickers + reddit subreddits
  data/                       <- SQLite journal lives here
  reports/daily/              <- generated briefs (Markdown)
  reports/weekly/             <- reserved for future weekly summaries
  src/
    config.py                 <- env + YAML loaders
    prices.py                 <- yfinance wrappers
    sec_filings.py            <- SEC EDGAR + dilution heuristics
    reddit_scan.py            <- reddit JSON scrape + acceleration
    scoring.py                <- pure scoring (fully unit-tested)
    reporting.py              <- Markdown renderer (Obsidian style)
    alerts.py                 <- Discord webhook
    trade_journal.py          <- SQLite journal
    generate_brief.py         <- morning entry point
    intraday_scan.py          <- intraday refresh entry point
  tests/                      <- pytest scoring tests
  requirements.txt
  .env.example
```

## Setup on macOS (Mac mini)

```bash
# 1. Python 3.11+ from Homebrew if you don't already have it.
brew install python@3.11

# 2. From this folder:
cd trading-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure credentials:
cp .env.example .env
$EDITOR .env          # set SEC_USER_AGENT, DISCORD_WEBHOOK_URL, etc.

# 4. Edit your watchlist:
$EDITOR config/watchlists.yaml

# 5. Try a one-shot run:
python -m src.generate_brief
ls reports/daily/

# 6. Run the tests:
pytest -q
```

## Sample cron schedule

The bot is one-shot, so cron just decides when to run it. Times are in
the Mac mini's local timezone (set with `sudo systemsetup -settimezone
America/New_York` if needed).

`crontab -e`:

```cron
# Morning brief — pre-market, after most pre-open headlines have hit.
30 8 * * 1-5 cd /Users/you/trading-agent && /Users/you/trading-agent/.venv/bin/python -m src.generate_brief >> data/cron.log 2>&1

# Intraday refresh — early afternoon and end-of-day.
0 13 * * 1-5 cd /Users/you/trading-agent && /Users/you/trading-agent/.venv/bin/python -m src.intraday_scan >> data/cron.log 2>&1
30 15 * * 1-5 cd /Users/you/trading-agent && /Users/you/trading-agent/.venv/bin/python -m src.intraday_scan >> data/cron.log 2>&1
```

If you prefer launchd, drop `~/Library/LaunchAgents/com.you.trading-agent.plist`
with the equivalent `StartCalendarInterval` blocks — launchd survives
reboots without `crontab` baggage.

## Future Raspberry Pi watchdog

A Pi can ping the Mac mini periodically (e.g. via SSH:
`ssh mac-mini "tail -1 trading-agent/data/cron.log"`) and post a
Discord alert if the most-recent brief is older than today's date. The
watchdog itself does not need any of this package — it's a one-file
shell or Python script that fires when expected output is missing.

## Operational notes

- **EDGAR User-Agent**: required. Set `SEC_USER_AGENT="Your Name
  your.email@example.com"` in `.env`. Requests without a real contact
  will be denied.
- **Reddit**: we use the public JSON endpoints. No OAuth needed, but a
  polite User-Agent is required and we self-rate-limit on 429s.
- **Discord**: webhook is optional. With no webhook set, alerts are
  logged and skipped.
- **Data sources are best-effort**. yfinance is unofficial, EDGAR can
  rate-limit, Reddit can shadow-ban. The scorer handles missing inputs
  gracefully — `momentum_score(None, None)` returns 0 instead of
  crashing.

## Testing the scoring

```bash
cd trading-agent
pytest -q
```

The scoring module is pure and side-effect free, so the tests run
without network access. They cover:

- Clipping and saturation
- Both-inputs-None paths for momentum
- Monotonicity in acceleration and volume
- Dilution saturation
- Meme-tag contribution
- Composite priority's response to each component

## License

ISC, matching the parent repository.

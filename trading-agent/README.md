# trading-agent

A **research-only** speculative-trading research assistant. It reads
your watchlists, fetches market and SEC data, scans Reddit for ticker
mentions, scores everything, writes an Obsidian-friendly Markdown
brief, and pings Discord. It **never places orders**, has no broker
SDK, and does not authenticate with any brokerage.

## Core idea

**Agent proposes, together we act.** Every recommendation flows
through a `proposals` table with rationale + explicit invalidation.
Discord pings rarely. You review proposals with a CLI and mark each
`accepted` / `rejected` / `deferred`. Outcomes are journaled and
later aggregated into a hit-rate table that calibrates the next
generation of scoring weights.

## What it does

### Daily — morning brief (`generate_brief.py`)
1. Reads `config/watchlists.yaml` (speculative radar).
2. Fetches price, change, volume, 30-day relative volume via yfinance.
3. Pulls recent SEC filings; flags S-1 / S-3 / 424B / ATM language as
   dilution risk.
4. Scans Reddit's public JSON endpoints (paginated) for mention
   acceleration vs. baseline.
5. Scores each ticker on momentum / reddit / dilution / meme / final
   research-priority.
6. Silently checks `config/buy_zones.yaml`; any trigger fires a
   `Proposal` row.
7. Writes `reports/daily/YYYY-MM-DD-morning-brief.md`.
8. Pings Discord only when priority threshold crossed or open
   proposals exist.

### Weekly — Sunday brief (`generate_weekly.py`)
1. Portfolio table: holdings, weights, week-over-week move, unrealized
   P/L.
2. Buy-zone triggers fired this week.
3. Option-income ideas — cash-secured puts on buy-zone tickers, covered
   calls on holdings — ranked by yield × IV-richness × liquidity, with
   earnings-span penalties.
4. Filings deltas: paragraph-level diffs of 10-Q/10-K Risk Factors and
   MD&A against the prior filing. Read the new paragraphs, skip the
   document.
5. Theses approaching invalidation / hit invalidation.
6. Hit-rate summary across the whole journal (the feedback loop).
7. Open proposals awaiting decision.

### Nightly — outcomes (`outcomes.py`)
For every active thesis older than 7 days, records current price,
whether invalidation level fired, and days elapsed.

### Always — proposals CLI
```bash
python -m src.proposals list                    # open queue
python -m src.proposals list --recent 30        # last 30d any status
python -m src.proposals review --id N --action accepted --note "..."
```

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
  config/
    watchlists.yaml      speculative radar + core
    holdings.yaml        what you own (so the agent can reason)
    buy_zones.yaml       standing buy interest
  data/                  SQLite journal (gitignored)
  reports/
    daily/               morning briefs
    weekly/              Sunday briefs
  src/
    # data layer
    prices.py            yfinance OHLCV snapshots
    sec_filings.py       EDGAR + dilution heuristics
    filings_diff.py      Risk Factors / MD&A diffs across filings
    reddit_scan.py       reddit JSON pagination + acceleration math
    portfolio.py         holdings + buy-zone YAML loaders
    volatility.py        Yang-Zhang RV + Black-Scholes delta
    # signals
    scoring.py           speculative scoring (5 pure functions)
    quality.py           quality screen (the inverse of speculative)
    buy_zones.py         silent-until-triggered sentinel
    options_income.py    CSP + CC ranker
    # agent surface
    theses.py            CRUD + invalidation checker
    proposals.py         agent's actionable output + review CLI
    outcomes.py          nightly hit/miss recorder (feedback loop)
    # orchestration
    config.py            env + YAML loaders
    log.py               project logger
    alerts.py            Discord webhook
    reporting.py         Markdown renderer (Obsidian frontmatter)
    trade_journal.py     SQLite schema + connection helper
    generate_brief.py    morning entry point
    generate_weekly.py   Sunday entry point
    intraday_scan.py     cheap intraday refresh
  tests/                 ~125 tests, all network-free
  ROADMAP.md             phased plan + design conventions
  README.md
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
30 8 * * 1-5 cd /Users/you/trading-agent && .venv/bin/python -m src.generate_brief >> data/cron.log 2>&1

# Intraday refresh — early afternoon and end-of-day.
0 13 * * 1-5 cd /Users/you/trading-agent && .venv/bin/python -m src.intraday_scan >> data/cron.log 2>&1
30 15 * * 1-5 cd /Users/you/trading-agent && .venv/bin/python -m src.intraday_scan >> data/cron.log 2>&1

# Sunday brief — 9pm ET so it's waiting Sunday night.
0 21 * * 0 cd /Users/you/trading-agent && .venv/bin/python -m src.generate_weekly >> data/cron.log 2>&1

# Nightly outcome scoring — closes the feedback loop.
30 22 * * * cd /Users/you/trading-agent && .venv/bin/python -m src.outcomes >> data/cron.log 2>&1
```

## Daily workflow

1. **Morning, 8:30am ET:** brief lands in `reports/daily/`. Optional
   Discord ping if anything crossed thresholds.
2. **Whenever:** `python -m src.proposals list` — what's the agent
   thinking? Each row carries rationale + explicit invalidation.
3. **You decide and act** (in your broker). Then:
   `python -m src.proposals review --id N --action accepted --note "..."`.
4. **Sunday, 9pm ET:** weekly brief — portfolio review, filings
   deltas, hit-rate update. 10-minute read.

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

---
type: guide
project: trading-agent
tags: [trading-agent, guide, reference]
created: 2026-06-15
---

# Trading Agent — Field Guide

> [!summary] What this is
> A **research partner**, not a trading bot. A deterministic Python
> pipeline gathers market, SEC, and Reddit data and emits mechanical
> *proposals*. A headless **Claude (Fable 5)** run then evaluates
> everything with judgment and writes you an analyst brief. **Nothing
> ever places a trade.** The contract is: *the agent proposes and
> thinks proactively — together we act.*

## The system in one picture

```
config/*.yaml ──▶ pipeline (Python, deterministic)
                    │  prices • SEC filings • Reddit • options chains
                    ▼
              proposals + briefs (SQLite + Markdown)
                    │
                    ▼
        headless Claude Fable 5 (the analyst)
                    │  reads everything, annotates each proposal
                    │  endorse / caution / oppose + reasoning
                    ▼
        reports/daily/YYYY-MM-DD-analysis.md  ──▶  you read (phone/Obsidian)
                    │
                    ▼
        YOU decide: accept / reject / defer  ──▶  you act in your broker
                    │
                    ▼
        outcomes recorded nightly ──▶ hit-rate feeds next week's judgment
```

## What we built, layer by layer

### 1. The earnings bot (repo root)
The original project: an Alpaca calendar-spread bot around earnings
IV crush. We hardened it (bounded its close-side losses, fixed its CI,
removed its database from git) but it's a separate, self-contained
thing. The trading-agent **shares its volatility math** (Yang-Zhang
realized vol) but shares no execution code — because trading-agent has
none.

### 2. The speculative radar (daily brief)
- Watches `config/watchlists.yaml` tickers.
- Scores momentum, Reddit mention *acceleration* (fresh vs 72h
  baseline, paginated properly), dilution risk (S-1/S-3/424B/ATM
  filings), meme risk.
- Renders `reports/daily/YYYY-MM-DD-morning-brief.md` — with Obsidian
  frontmatter and [[wikilinks]], so it's native in your vault.
- Philosophy: on speculative names the flags are **subtractive** — the
  agent's best output is usually "no, and here's why."

### 3. The quality-stock layer (where the modest gains live)
- **Buy zones** (`config/buy_zones.yaml`): standing buy interest —
  price level, % off 52-week high, P/E ceiling, or FCF-yield floor.
  Silent until triggered. When one fires, it becomes a proposal.
- **Option income** (`src/options_income.py`): cash-secured puts on
  buy-zone names (get paid to place the limit order you wanted anyway)
  and covered calls on holdings ≥100 shares. Ranked by annualized
  yield × IV-richness × liquidity; anything spanning earnings is
  penalized hard.
- **Filings deltas** (`src/filings_diff.py`): paragraph-level diff of
  Risk Factors / MD&A between the two latest 10-Q/10-K. You read the
  *new* paragraphs, never the 100-page filing.
- **Quality screen** (`src/quality.py`): ROIC, FCF yield, buybacks
  (the anti-dilution flag), margins, leverage — refreshes the universe
  monthly.

### 4. The judgment layer (headless Fable 5)
- `bin/daily.sh` runs the pipeline, then invokes Claude Code headless
  (`claude -p --model claude-fable-5`) with `agent/evaluate-daily.md`.
- Claude reads the brief, the configs, and the outcome history, then
  **annotates every open proposal** with a stance — `endorse`,
  `caution`, or `oppose` — plus 2-4 sentences of reasoning and what
  would change its mind.
- It writes `reports/daily/YYYY-MM-DD-analysis.md`: an executive
  summary, per-proposal reasoning, and "pipeline notes" (data problems
  it spotted).
- Its **only** journal write is the annotation. It cannot change a
  proposal's status. That's yours.

### 5. The memory (SQLite journal, `data/journal.db`)
| Table | What it holds |
| --- | --- |
| `proposals` | Every idea, with rationale, invalidation, your decision, and Claude's stance |
| `theses` | Falsifiable hypotheses with explicit kill criteria (price/date/horizon) |
| `outcomes` | Nightly: did each thesis hit its invalidation? days elapsed? |
| `score_history` | Every score of every ticker, forever — the calibration corpus |

The weekly review compares Claude's stances against your decisions and
against what actually happened. Over months, this tells you which
signals (and whose judgment) to trust.

---

## Step-by-step: first-time setup (Mac mini)

1. **Clone and install**
   ```bash
   git clone <your-repo> && cd earnings-trade-automation/trading-agent
   python3.11 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   npm install -g @anthropic-ai/claude-code   # the headless analyst
   ```
2. **Configure secrets** — `cp .env.example .env`, then set:
   - `SEC_USER_AGENT="Your Name you@example.com"` (EDGAR requires it)
   - `DISCORD_WEBHOOK_URL=...` (optional but recommended)
   - `ANTHROPIC_API_KEY` in your shell profile (for headless Claude),
     or run `claude` once interactively to log in.
3. **Tell it what you own** — edit `config/holdings.yaml`: tickers,
   share counts, cost basis, target weights, cash.
4. **Set your standing buy interest** — edit `config/buy_zones.yaml`.
   Be honest: these are prices where you *actually* want to buy, sized
   as % of portfolio. This is the patience-as-a-service layer.
5. **Tune the radar** (optional) — `config/watchlists.yaml` for the
   speculative side; delete tickers you don't care about.
6. **Dry-run everything**
   ```bash
   PYTHONPATH=. python -m pytest tests/ -q        # ~130 tests, no network
   PYTHONPATH=. python -m src.generate_brief      # first morning brief
   bin/evaluate.sh daily                          # first analyst pass
   ```
7. **Schedule it** — `crontab -e`:
   ```cron
   30 8  * * 1-5 cd ~/trading-agent && bin/daily.sh    >> data/cron.log 2>&1
   0  21 * * 0   cd ~/trading-agent && bin/weekly.sh   >> data/cron.log 2>&1
   30 22 * * *   cd ~/trading-agent && .venv/bin/python -m src.outcomes >> data/cron.log 2>&1
   ```
   (Alternative: the GitHub Actions workflow `agent-eval.yml` does the
   same in the cloud — add `ANTHROPIC_API_KEY` to repo secrets.)
8. **Point Obsidian at the reports** — either open `reports/` as a
   vault folder, or symlink it into your existing vault:
   ```bash
   ln -s ~/trading-agent/reports ~/ObsidianVault/TradingAgent
   ```
   Briefs carry frontmatter (`type: morning-brief`, ticker tags) so
   Dataview queries work out of the box.

## Step-by-step: the daily loop (5 minutes)

1. ☕ **Read the analyst brief** — `reports/daily/<today>-analysis.md`
   (or the Discord ping). Executive summary first. If it says "nothing
   today," you're done.
2. **Scan annotated proposals**:
   ```bash
   PYTHONPATH=. python -m src.proposals list
   ```
   Each shows the pipeline's rationale, the invalidation, and Claude's
   stance + reasoning.
3. **Decide.** For anything you act on (in your broker, yourself):
   ```bash
   PYTHONPATH=. python -m src.proposals review --id 12 --action accepted --note "sold MSFT 380p @ 4.20"
   ```
   Reject with a note too — your reasons train the weekly scorecard.
4. **Optionally log a thesis** for anything you entered — explicit
   invalidation price/date. The nightly job then watches it for you.

## Step-by-step: the Sunday review (10 minutes)

1. Read `reports/weekly/<sunday>-analysis.md`:
   - the week in three bullets
   - **Claude's honesty scorecard** — its stance hit-rate, stated plainly
   - portfolio drift and cash deployment
   - next week's watch list with confirm/invalidate criteria
2. Act on stale items: buy zones that sat triggered, theses without
   invalidations, positions past target weight.
3. If the calibration notes propose a threshold change, decide it
   consciously — the agent suggests config edits; it never makes them.

## Guardrails you can rely on

- **No broker code exists** in trading-agent. Not disabled — absent.
- Headless Claude runs with an **allowlisted toolset** (read, write to
  reports, `proposals annotate`, read-only sqlite). Status changes and
  config edits are outside its contract.
- Every proposal and thesis carries an **explicit invalidation** — the
  system never lets an idea die silently.
- Discord pings are **rare by design**. A ping means something crossed
  a threshold you set.

## Files worth bookmarking

- [[Trading Agent — Field Guide]] — this note
- `trading-agent/README.md` — command reference
- `trading-agent/ROADMAP.md` — phases + design conventions
- `trading-agent/CLAUDE.md` — the analyst's standing instructions
- `config/buy_zones.yaml` — your patience, encoded
- `data/journal.db` — the memory; back it up

> [!warning] Reality check
> "Modest monthly gains" is a high bar. The edge this system provides
> is *time*, *breadth*, and *discipline* — reading deltas instead of
> documents, watching zones so you don't, and forcing every idea to
> state what would kill it. It does not predict markets, and any month
> can be red. The analyst's most valuable word is "no."

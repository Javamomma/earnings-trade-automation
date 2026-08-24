# Roadmap: research partner for modest, repeatable gains

**Charter:** an agent that thinks proactively, surfaces specific
proposals with explicit invalidation criteria, and gets sharper as the
journal grows. The human reviews and acts. No order placement. No
broker API. The agent never trades — *together we act*.

**Target:** modest monthly gains from three levers on quality names:
getting paid to wait (option income), buying quality on sale
(pre-committed buy zones), and not bleeding (avoiding overtrading,
dilution traps, theses that quietly died).

---

## Phase 0 — Data trust ✅ shipped

NaN guards in `scoring` and `prices` (NaN no longer scores as
perfect-100). Reddit pagination + corrected baseline math (the older
divisor systematically inflated acceleration on busy subs). EDGAR
ticker-map cache no longer poisons on a transient failure. ATM
language detected on 8-Ks, not only FWPs. **65+ tests covering the
audit fixes alone.**

## Phase 1 — Buy-zone sentinel ✅ shipped

`config/buy_zones.yaml` declares standing buy interest with one of
four trigger kinds: absolute price, % off 52-week high, trailing P/E
ceiling, FCF-yield floor. The agent stays silent until a trigger
fires, then writes a `proposals` row with rationale and an explicit
invalidation. Inverted alerting: pings are rare and meaningful.

Files: `portfolio.py`, `buy_zones.py`, `config/{holdings,buy_zones}.yaml`.

## Phase 2 — Option-income assistant ✅ shipped

The piece that produces a monthly cash cadence. Reuses the
Yang-Zhang realized-vol math from the parent earnings bot, ported into
`volatility.py` along with a small Black-Scholes kit (delta, assignment
probability). `options_income.py` scans yfinance option chains for
cash-secured puts on buy-zone tickers and covered calls on holdings,
ranking by yield × IV-richness × liquidity, with explicit penalties
for strikes that span an earnings date. Ideas surface as proposals,
never trades.

Files: `volatility.py`, `options_income.py`. Tests in
`tests/test_volatility.py` and `tests/test_options_income.py`.

## Phase 3 — Filings deltas ✅ shipped

`filings_diff.py` pulls the two most recent 10-Q/10-K for each
holding, extracts Risk Factors and MD&A sections, and diffs them at
the paragraph level (90% similarity fuzz-match to ignore boilerplate
shuffles). Material additions become a proposal. **The single
biggest research time-saver in the system** — read the new paragraphs,
skip the 100-page filing.

## Phase 4 — Thesis ledger ✅ shipped

Every position or proposal carries a falsifiable thesis with
explicit invalidation criteria — price level, calendar date, or
horizon. `theses.py` provides CRUD plus `check_invalidations()` which
returns active theses whose kill criteria fired today. **The agent
proposes the close; it does not close.**

## Phase 5 — Proposals as first-class output ✅ shipped

`proposals.py` is the agent's actionable surface. Every recommendation
flows through this table with kind ∈ {`buy_zone_hit`, `csp`, `cc`,
`exit`, `thesis_invalidation`, `filings_delta`}, structured payload
(strikes, expiries, observed values), rationale, and invalidation.
CLI:

```bash
python -m src.proposals list                # open queue
python -m src.proposals list --recent 30    # last 30 days, any status
python -m src.proposals review --id N --action accepted --note "..."
```

## Phase 6 — Outcome scoring (the feedback loop) ✅ shipped

`outcomes.py` runs nightly and records for every active thesis older
than 7 days: current price, whether the invalidation level fired, and
days elapsed. `hit_rate_summary()` aggregates by signal/kind into the
weekly report. This is what turns "score weights" from guesses into
evidence — and it's what separates a partner from a horoscope.

## Phase 7 — Sunday brief ✅ shipped

`generate_weekly.py` orchestrates all of the above into one 10-minute
read every Sunday evening:

1. Portfolio table — holdings, weights, week move, unrealized P/L
2. Buy-zone triggers fired this week
3. Top option-income ideas (CSPs on cash-targets, CCs on holdings)
4. Filings deltas on holdings
5. Theses approaching invalidation / hit invalidation
6. Hit-rate summary across the whole journal
7. Open proposals awaiting decision

## Phase 8 — Quality screen ✅ shipped

`quality.py` is the inverse of the speculative flags from the radar
list — it *rewards* persistence: high ROIC, FCF yield, *shrinking*
share count (anti-dilution), stable operating margins, low leverage.
Run monthly to refresh the watchlist that feeds buy-zones and
option-income.

## Phase 9 — Ops hardening ✅ shipped (one item deferred)

- [x] `proposals` CLI for the review workflow; annotate for the agent.
- [x] Pi watchdog (`bin/watchdog.py`): stdlib-only, copyable to the
      Pi standalone; local or `--ssh user@mac-mini` mode; silent when
      healthy, Discord alert when the day's brief is missing.
- [x] launchd plists (`ops/launchd/*.plist`) for daily / weekly /
      nightly — survives reboots, runs missed jobs on wake.
- [x] Nightly journal backup (`bin/backup.sh`): `sqlite3 .backup` (no
      torn copies), rotates at 30.
- [x] `--dry-run` on both briefs: render to stdout, zero journal
      writes, zero proposals, zero Discord — safe format iteration.
- [ ] On-disk HTTP cache (requests-cache) for EDGAR + Reddit —
      deferred until rate limits actually bite; adds a dependency.

## Phase 10 — Calibration (continuous)

Now that outcomes are journaled, re-weight `scoring.py` against
evidence rather than vibes. The agreement is: never re-weight on one
week's data. Quarterly review of `hit_rate_summary()` decides what to
keep, demote, or kill.

---

## Design conventions baked in

1. **Agent proposes, together we act.** Every output is a Proposal
   with rationale + invalidation. The human reviews via CLI. Nothing
   else.
2. **Quiet by default.** Discord and email pings only on genuinely
   actionable events. The summary table at the top of the brief is
   silent; only triggers and threshold crossings ping.
3. **Pure scoring, separable fetching.** Every module has a pure-
   function core and a thin I/O boundary, so audits and tests can
   exercise the logic without the network.
4. **Subtractive flags first.** The dilution / earnings-span / meme
   flags reduce scores; the cheap-FCF / buyback flags raise them. We
   defend before we attack on speculative names; we accumulate
   before we chase on quality names.
5. **Every signal carries an explicit invalidation.** No silent
   abandonment. If a thesis dies, the journal says when and why.

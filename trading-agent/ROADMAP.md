# Roadmap: from scanner to research partner

North star: an agent that acts like a disciplined research partner for
**somewhat speculative assets** — it surfaces candidates, drafts a
falsifiable thesis for each, tracks whether its own calls worked, and
gets more useful as the journal grows. Modest, repeatable edge; not
lottery tickets.

Hard constraint, unchanged: **no order placement, no brokerage APIs.**
The human makes every trade. The agent's output is words and scores.

---

## Phase 1 — Trust the data (mostly done)

The audit fixes landed: NaN guards in scoring and prices, Reddit
pagination + corrected baseline math, ATM detection on 8-Ks, EDGAR
cache fix, fixture-based integration tests (65 passing).

Remaining:
- [ ] Golden-file test for `reporting.render_brief` so format drift is
      caught in review, not in Obsidian.
- [ ] On-disk HTTP cache (e.g. `requests-cache`, SQLite backend) so
      repeat runs within an hour don't re-hit EDGAR/Reddit. Politeness
      and speed.
- [ ] Persist daily raw snapshots (prices, mentions, filings) to
      `data/` — this is the future backtest corpus. Start capturing
      now; cold-start is the enemy.

## Phase 2 — Signals that actually discriminate

Ordered by value-per-effort for speculative small/mid caps:

1. **Liquidity floor (do first).** Dollar-volume and float screens so
   the agent never pitches something untradeable. A great score on a
   $40k/day name is a trap, not an idea. Add `min_dollar_volume` to
   watchlist config; render a hard "untradeable" flag.
2. **Catalyst calendar.** Earnings dates (the parent repo already
   queries Dolthub's calendar — reuse it), FDA/PDUFA dates for biotech,
   lockup expirations, index-rebalance dates. A thesis without a
   catalyst is a hope. Score proximity: catalyst within 2 weeks boosts
   priority.
3. **Insider buying (Form 4).** The EDGAR plumbing already exists in
   `sec_filings.py`. Cluster buys by officers/directors are one of the
   few well-documented positive signals in small caps. Mirror image of
   the dilution flag.
4. **Short interest / borrow.** FINRA publishes short interest
   bi-monthly; days-to-cover plus rising mentions is the squeeze
   set-up the meme flag should distinguish from pure hype.
5. **Reddit quality upgrades.** Author-diversity (10 mentions by 10
   users ≠ 10 by 1 spammer), comment-level scanning, and a simple
   pump-pattern flag (brand-new accounts, identical phrasing).

## Phase 3 — The partner part: theses, not tickers

This is the core of the ask. A ranked table is a scanner; a partner
explains *why* and *what would change its mind*.

1. **Thesis composer.** For every ticker above the alert threshold,
   generate a structured thesis block in the brief:
   - *Setup*: which signals fired (momentum, accel, insider, catalyst)
   - *Thesis*: one paragraph, template-driven from the signals
   - *Confirms*: what evidence would strengthen it
   - *Invalidates*: explicit kill criteria (price level, filing event,
     mention collapse)
   - *Risk flags*: dilution, meme, liquidity — restated, never buried
   Start template-based (deterministic, testable). Optionally add an
   LLM pass (Claude API) that drafts the narrative from the structured
   inputs — the journal becomes its memory: feed it prior theses on
   the same name and their outcomes.
2. **Outcome scoring (the feedback loop).** Nightly job marks every
   journaled hypothesis after N days: did price confirm or hit the
   invalidation first? Write hit-rate by signal combination into the
   weekly report. This is what separates a partner from a horoscope —
   and it generates the labels needed to tune `scoring.py` weights on
   evidence instead of vibes.
3. **Weekly review** (`reports/weekly/`). Biggest movers vs. what the
   agent scored them; misses analyzed; journal post-mortems due; the
   current hit-rate table. The Sunday-evening read.
4. **Two-way journal CLI.** `python -m src.journal add GME --direction
   long --thesis "..." --invalidation "..."` so capturing your own
   ideas is frictionless; the agent then tracks *your* calls with the
   same outcome scoring as its own. Over time you learn which of you
   is right more often, per signal type.

## Phase 4 — Ops hardening

- Pi watchdog (sketched in README): alert when today's brief is
  missing. One-file script, SSH + Discord.
- launchd plists instead of cron on the Mac mini (survives reboots,
  no env-stripping surprises).
- SQLite backup: nightly copy of `journal.db` to `data/backups/`,
  rotate 30.
- A `--dry-run` flag for `generate_brief` that writes to stdout and
  skips Discord — for safe iteration.

## Calibration discipline (applies throughout)

- Every new signal ships with: a pure scoring function, unit tests,
  and a column in `score_history` — so its contribution is measurable
  and removable.
- Re-weight `research_priority` only against journaled outcomes, never
  by eyeballing one week.
- The meme/dilution/liquidity flags must stay *subtractive*. The
  agent's job on speculative names is mostly to say "no, and here's
  why" — the modest-gain goal dies on the names it should have
  filtered.

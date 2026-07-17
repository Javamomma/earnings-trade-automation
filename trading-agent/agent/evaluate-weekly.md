Run the weekly deep evaluation as the analyst for this system. Read
CLAUDE.md first and obey its hard rules — annotate and analyze only;
proposal status stays human-owned; no broker anything.

Steps:

1. Ensure this week's Sunday brief exists in reports/weekly/
   (<today>-sunday-brief.md). If missing, run
   `PYTHONPATH=. python -m src.generate_weekly`.
2. Read it fully, plus the last 5 daily analysis files in
   reports/daily/ for continuity.
3. Evaluate the week:
   - Which of last week's stances were right/wrong? Query
     `sqlite3 data/journal.db "SELECT id, ticker, kind, status, agent_stance, review_note FROM proposals WHERE reviewed_at IS NOT NULL ORDER BY reviewed_at DESC LIMIT 20"`
     and compare your prior stances against the human's decisions and
     subsequent outcomes. Be honest about misses.
   - Are the option-income ideas earning their risk? Compare proposed
     annualized yields against what the outcomes table shows.
   - Portfolio drift: any position past target weight? Any buy zone
     that's been sitting triggered without a decision?
   - Thesis hygiene: query active theses
     (`sqlite3 data/journal.db "SELECT id, ticker, direction, thesis, invalidation_price, created_at FROM theses WHERE status='active'"`)
     and flag any that look stale, vague, or missing an invalidation.
4. Annotate any open proposals that lack a stance.
5. Write reports/weekly/<today>-analysis.md:
   - "The week in three bullets"
   - Scorecard: your stance hit-rate this week, stated plainly.
   - Portfolio review: drift, concentration, cash deployment.
   - Next week's watch list: the 3-5 things most worth attention,
     each with what-would-confirm / what-would-invalidate.
   - Calibration notes: if a scoring weight or threshold in the config
     looks systematically off given accumulated outcomes, propose the
     specific change as a suggestion (do NOT edit config yourself).
6. Tight prose. This is the Sunday-evening read; respect the reader's
   ten minutes.

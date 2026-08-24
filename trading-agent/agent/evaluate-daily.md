Evaluate today's research output as the analyst for this system.
Today's date is available via `date +%F`. Work from the trading-agent
directory. Read CLAUDE.md first and obey its hard rules — you annotate
and analyze; you never change proposal status and never touch a broker.

Steps:

1. Read today's morning brief in reports/daily/ (file named
   <today>-morning-brief.md). If it doesn't exist, run
   `PYTHONPATH=. python -m src.generate_brief` first.
2. List open proposals: `PYTHONPATH=. python -m src.proposals list`.
3. For EACH open proposal, evaluate it on its merits using:
   - the morning brief's data for that ticker,
   - config/holdings.yaml and config/buy_zones.yaml for portfolio
     context (target weights, standing buy interest),
   - the journal's outcome history:
     `sqlite3 data/journal.db "SELECT * FROM outcomes ORDER BY measured_at DESC LIMIT 50"`,
   - your own knowledge of the name and market conditions (be explicit
     about what is knowledge vs. what is from today's data).
   Then annotate it:
   `PYTHONPATH=. python -m src.proposals annotate --id N --stance endorse|caution|oppose --analysis "2-4 sentences"`.
4. Write the daily analysis to reports/daily/<today>-analysis.md:
   - Executive summary: 3-5 bullets, ranked, what deserves the human's
     attention today and why. If nothing does, say exactly that.
   - One short section per proposal you annotated (stance + reasoning +
     what would change your mind).
   - "Pipeline notes": anything mechanical you noticed — data gaps,
     NaN-looking values, stale buy zones, configs that look wrong,
     theses that should be reviewed.
5. Keep total output tight. The human reads this on a phone over
   coffee. Every sentence must earn its place.

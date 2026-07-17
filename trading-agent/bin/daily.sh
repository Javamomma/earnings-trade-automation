#!/usr/bin/env bash
# Full daily run: deterministic pipeline, then headless Fable 5 evaluation,
# then a Discord ping if the analysis flagged anything.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[daily] pipeline"
PYTHONPATH=. python -m src.generate_brief

echo "[daily] headless evaluation"
bin/evaluate.sh daily

# Ping Discord with the analysis executive summary (first 15 lines).
TODAY="$(date +%F)"
ANALYSIS="reports/daily/${TODAY}-analysis.md"
if [ -f "$ANALYSIS" ]; then
  PYTHONPATH=. python - "$ANALYSIS" <<'PY'
import sys
from src.alerts import send_discord
path = sys.argv[1]
head = "".join(open(path).readlines()[:15])
send_discord(f"**Analyst brief ready** — {path}\n{head}")
PY
fi
echo "[daily] complete"

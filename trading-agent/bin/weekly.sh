#!/usr/bin/env bash
# Full weekly run: Sunday brief, then headless Fable 5 deep evaluation.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[weekly] pipeline"
PYTHONPATH=. python -m src.generate_weekly

echo "[weekly] headless evaluation"
bin/evaluate.sh weekly

TODAY="$(date +%F)"
ANALYSIS="reports/weekly/${TODAY}-analysis.md"
if [ -f "$ANALYSIS" ]; then
  PYTHONPATH=. python - "$ANALYSIS" <<'PY'
import sys
from src.alerts import send_discord
path = sys.argv[1]
head = "".join(open(path).readlines()[:15])
send_discord(f"**Weekly analyst review ready** — {path}\n{head}")
PY
fi
echo "[weekly] complete"

#!/usr/bin/env bash
# Headless Fable 5 evaluation over the pipeline's output.
#
# Usage:
#   bin/evaluate.sh daily     # run after generate_brief
#   bin/evaluate.sh weekly    # run after generate_weekly
#
# Requires: Claude Code CLI (`npm i -g @anthropic-ai/claude-code`) and
# ANTHROPIC_API_KEY in the environment (or `claude setup-token` login).
#
# The evaluator's write surface is deliberately tiny:
#   - `python -m src.proposals ...` (list + annotate only by prompt contract)
#   - read-only sqlite3 queries
#   - Write/Edit inside reports/ for the analysis file
# It cannot touch a broker because no broker code exists here.

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-daily}"
MODEL="${CLAUDE_MODEL:-claude-fable-5}"

case "$MODE" in
  daily)   PROMPT_FILE="agent/evaluate-daily.md" ;;
  weekly)  PROMPT_FILE="agent/evaluate-weekly.md" ;;
  *) echo "usage: $0 daily|weekly" >&2; exit 2 ;;
esac

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found. Install: npm i -g @anthropic-ai/claude-code" >&2
  exit 1
fi

echo "[evaluate] mode=$MODE model=$MODEL"

claude -p "$(cat "$PROMPT_FILE")" \
  --model "$MODEL" \
  --permission-mode acceptEdits \
  --allowedTools \
    "Read" "Glob" "Grep" "Write" "Edit" \
    "Bash(python -m src.proposals:*)" \
    "Bash(PYTHONPATH=. python -m src.proposals:*)" \
    "Bash(PYTHONPATH=. python -m src.generate_brief:*)" \
    "Bash(PYTHONPATH=. python -m src.generate_weekly:*)" \
    "Bash(PYTHONPATH=. python -m pytest:*)" \
    "Bash(sqlite3 data/journal.db:*)" \
    "Bash(date:*)" \
  --max-turns 40

echo "[evaluate] done"

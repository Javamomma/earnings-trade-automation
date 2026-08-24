#!/usr/bin/env bash
# Nightly journal backup: copy data/journal.db to data/backups/ with a
# date stamp, keep the most recent 30. SQLite .backup is used (not cp)
# so an in-flight write can't produce a torn copy.
set -euo pipefail
cd "$(dirname "$0")/.."

DB="data/journal.db"
DEST_DIR="data/backups"
KEEP=30

[ -f "$DB" ] || { echo "[backup] no journal at $DB; nothing to do"; exit 0; }
mkdir -p "$DEST_DIR"

STAMP="$(date +%Y%m%d)"
DEST="$DEST_DIR/journal-$STAMP.db"

sqlite3 "$DB" ".backup '$DEST'"
echo "[backup] wrote $DEST"

# Rotate: delete all but the newest $KEEP.
ls -1t "$DEST_DIR"/journal-*.db 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
  echo "[backup] rotated out $old"
done

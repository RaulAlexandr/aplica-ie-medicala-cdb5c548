#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
[[ -f "$PILOT_DB_PATH" ]] || { echo "Nu există baza: $PILOT_DB_PATH" >&2; exit 1; }
[[ ! -f "$PILOT_ROOT/api.pid" ]] || { echo "Oprește runtime-ul înainte de backup: scripts/pilot/stop.sh --checkout '$CHECKOUT'" >&2; exit 1; }
DEST="${EXTRA_ARGS[0]:-$PILOT_ROOT/backups/fictional-clinic-$(date -u +%Y%m%dT%H%M%SZ).sqlite3}"
[[ ! -e "$DEST" ]] || { echo "Destinația există; refuz suprascriere: $DEST" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
"$(python_for_checkout)" - "$PILOT_DB_PATH" "$DEST" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1:]
with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok": raise SystemExit(f"Backup integrity_check: {result}")
print(f"Backup valid: {dest}")
PY
chmod 600 "$DEST"
echo "Backup: $DEST"

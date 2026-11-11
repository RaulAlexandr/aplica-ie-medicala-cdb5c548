#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
[[ ${#EXTRA_ARGS[@]} -ge 1 ]] || { echo "Usage: restore.sh --checkout PATH BACKUP.sqlite3 [DEST.sqlite3]" >&2; exit 2; }
BACKUP="${EXTRA_ARGS[0]}"
[[ -f "$BACKUP" ]] || { echo "Backup lipsește: $BACKUP" >&2; exit 1; }
[[ ! -f "$PILOT_ROOT/api.pid" && ! -f "$PILOT_ROOT/web.pid" ]] || { echo "Oprește runtime-ul înainte de restaurare." >&2; exit 1; }
DEST="${EXTRA_ARGS[1]:-$PILOT_ROOT/restore-copy-$(date -u +%Y%m%dT%H%M%SZ).sqlite3}"
[[ ! -e "$DEST" ]] || { echo "Destinația există; refuz suprascriere: $DEST" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
"$(python_for_checkout)" - "$BACKUP" "$DEST" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1:]
with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok": raise SystemExit(f"Restore integrity_check: {result}")
print(f"Restore valid: {dest}")
PY
chmod 600 "$DEST"
echo "Copie restaurată la: $DEST (sursa păstrată; destinația nu a fost suprascrisă)"

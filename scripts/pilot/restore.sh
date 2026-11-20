#!/usr/bin/env bash
# Restaurează un backup al pilotului într-o copie izolată (DB + documente T11), fără a atinge sursa.
# Un backup vechi .sqlite3 (doar DB) este acceptat, dar raportat explicit ca fără documente.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
[[ ${#EXTRA_ARGS[@]} -ge 1 ]] || { echo "Usage: restore.sh --checkout PATH BACKUP_DIR|BACKUP.sqlite3 [DEST_DIR]" >&2; exit 2; }
BACKUP="${EXTRA_ARGS[0]}"
[[ -e "$BACKUP" ]] || { echo "Backup lipsește: $BACKUP" >&2; exit 1; }
[[ ! -f "$PILOT_ROOT/api.pid" && ! -f "$PILOT_ROOT/web.pid" ]] || { echo "Oprește runtime-ul înainte de restaurare." >&2; exit 1; }
DEST="${EXTRA_ARGS[1]:-$PILOT_ROOT/restore-copy-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ ! -e "$DEST" ]] || { echo "Destinația există; refuz suprascriere: $DEST" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
"$(python_for_checkout)" "$HERE/pilot_backup.py" restore "$BACKUP" "$DEST"
echo "Copie restaurată la: $DEST (sursa păstrată; destinația nu a fost suprascrisă)."
echo "Pentru a o folosi: PILOT_DB_PATH=$DEST/fictional-clinic.sqlite3 PILOT_DOCUMENTS_DIR=$DEST/documents în config."

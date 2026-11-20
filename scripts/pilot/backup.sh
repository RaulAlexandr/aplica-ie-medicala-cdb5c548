#!/usr/bin/env bash
# Backup consistent al pilotului: baza SQLite + fișierele T11 (documente/imagini) + manifest SHA-256.
# Runtime-ul trebuie oprit, astfel încât DB și fișierele să fie în aceeași stare.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
[[ -f "$PILOT_DB_PATH" ]] || { echo "Nu există baza: $PILOT_DB_PATH" >&2; exit 1; }
[[ ! -f "$PILOT_ROOT/api.pid" ]] || { echo "Oprește runtime-ul înainte de backup: scripts/pilot/stop.sh --checkout '$CHECKOUT'" >&2; exit 1; }
DEST="${EXTRA_ARGS[0]:-$PILOT_ROOT/backups/pilot-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ ! -e "$DEST" ]] || { echo "Destinația există; refuz suprascriere: $DEST" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
"$(python_for_checkout)" "$HERE/pilot_backup.py" backup "$PILOT_DB_PATH" "$PILOT_DOCUMENTS_DIR" "$DEST"
echo "Backup (DB + documente T11): $DEST"

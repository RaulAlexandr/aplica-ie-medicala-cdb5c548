#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
[[ -f "$PILOT_ROOT/venv/bin/python" ]] || { echo "Rulează setup.sh mai întâi." >&2; exit 1; }
[[ -f "$PILOT_DB_PATH" ]] || { echo "Baza nu există; pornește runtime-ul prin start.sh." >&2; exit 1; }
export DATABASE_URL="sqlite+aiosqlite:///$PILOT_DB_PATH" ENVIRONMENT=development PILOT_MODE=1
export JWT_SECRET="$(cat "$PILOT_ROOT/jwt-secret")" CORS_ORIGINS="$PILOT_WEB_URL"
PYTHONPATH="$CHECKOUT/backend${PYTHONPATH:+:$PYTHONPATH}" "$PILOT_ROOT/venv/bin/python" "$HERE/seed.py"

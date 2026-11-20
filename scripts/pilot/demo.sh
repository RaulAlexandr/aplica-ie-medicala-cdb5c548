#!/usr/bin/env bash
# One command to start the pilot on a NEW fictional SQLite database (T33).
#   scripts/pilot/demo.sh up   [--checkout PATH] [--allow-dirty]
#   scripts/pilot/demo.sh stop [--checkout PATH]
# `up` refuses when the target database already exists: it never overwrites, deletes or resets anything.
# To resume an existing demo use start.sh/stop.sh; for a fresh one choose another DENTACARE_PILOT_HOME.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"
COMMAND="${1:-}"; [[ -n "$COMMAND" ]] && shift || true
case "$COMMAND" in up|stop) ;; *) echo "Utilizare: demo.sh up|stop [--checkout PATH] [--allow-dirty]" >&2; exit 2 ;; esac
export DENTACARE_PILOT_HOME="${DENTACARE_PILOT_HOME:-$HOME/.local/share/dentacare-pilot-demo}"
CONFIG_PATH="${PILOT_CONFIG:-$HOME/.config/dentacare-pilot-demo/pilot.env}"
CHECKOUT_ARGS=(--checkout "$(cd "$HERE/../.." && pwd -P)")
PASS=()
while (($#)); do case "$1" in
  --checkout) (($# >= 2)) || { echo "--checkout cere o cale." >&2; exit 2; }; CHECKOUT_ARGS=(--checkout "$2"); shift 2 ;;
  --config) (($# >= 2)) || { echo "--config cere o cale." >&2; exit 2; }; CONFIG_PATH="$2"; shift 2 ;;
  *) PASS+=("$1"); shift ;;
esac; done
COMMON=("${CHECKOUT_ARGS[@]}" --config "$CONFIG_PATH" "${PASS[@]}")

if [[ "$COMMAND" == stop ]]; then exec "$HERE/stop.sh" "${COMMON[@]}"; fi

# Step 1 — config (created once, mode 600, no secrets). An existing config is never rewritten.
if [[ ! -f "$CONFIG_PATH" ]]; then
  mkdir -p "$(dirname "$CONFIG_PATH")"
  ( umask 077; cat > "$CONFIG_PATH" <<CFG
# Config locală demo T33, fără secrete.
PILOT_DB_PATH="$DENTACARE_PILOT_HOME/fictional-clinic.sqlite3"
PILOT_API_PORT=8039
PILOT_WEB_PORT=5179
CFG
  )
  chmod 600 "$CONFIG_PATH"
fi

# Step 2 — resolve paths exactly as the other scripts do (also enforces a clean checkout), then refuse an existing DB.
source "$HERE/common.sh"; parse_common_args "${COMMON[@]}"
if [[ -e "$PILOT_DB_PATH" ]]; then
  echo "Refuz: baza $PILOT_DB_PATH există deja; nu o suprascriu și nu o șterg." >&2
  echo "Reia demo-ul existent cu start.sh/stop.sh, sau alege altă bază: DENTACARE_PILOT_HOME=\$HOME/.local/share/dentacare-demo-2 (și alt --config)." >&2
  exit 3
fi
for spec in "API $PILOT_API_HOST $PILOT_API_PORT" "Frontend $PILOT_WEB_HOST $PILOT_WEB_PORT"; do
  read -r name host port <<< "$spec"
  python3 -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: s.bind((sys.argv[1],int(sys.argv[2])))
except OSError: sys.exit(1)' "$host" "$port" || { echo "$name: portul $host:$port este ocupat; schimbă-l în $CONFIG." >&2; exit 1; }
done

# Step 3 — environment, then start + seed on the new database.
"$HERE/setup.sh" "${COMMON[@]}"
"$HERE/start.sh" "${COMMON[@]}"
echo
echo "Demo pornit. Web: $PILOT_WEB_URL | API health: $PILOT_API_URL/health"
echo "Conturile (email/parolă) sunt în $PILOT_ROOT/credentials.json (600) — deschide-l local, nu îl copia."
echo "Ghid: docs/pilot/DEMO.md | Oprire: $HERE/demo.sh stop"

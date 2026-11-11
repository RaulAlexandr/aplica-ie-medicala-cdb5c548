#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
PYTHON="$(python_for_checkout)"
NODE_BIN="$(dirname "$(command -v node 2>/dev/null || true)")"
[[ -x /home/raul/Projects/dentacare-tools/node/bin/node ]] && NODE_BIN=/home/raul/Projects/dentacare-tools/node/bin
[[ -x "$PILOT_ROOT/venv/bin/python" ]] || { echo "Rulează setup.sh --checkout '$CHECKOUT' mai întâi." >&2; exit 1; }
for spec in "API $PILOT_API_HOST $PILOT_API_PORT" "Frontend $PILOT_WEB_HOST $PILOT_WEB_PORT"; do
  read -r name host port <<< "$spec"
  port_available "$PYTHON" "$host" "$port" || { echo "$name: portul $host:$port este ocupat. Alege alt port din config; procesul ocupant nu va fi oprit." >&2; exit 1; }
done
[[ ! -e "$PILOT_ROOT/api.pid" && ! -e "$PILOT_ROOT/web.pid" ]] || { echo "PID-uri existente; inspectează logurile și rulează stop.sh." >&2; exit 1; }
mkdir -p "$PILOT_ROOT/logs"
export DATABASE_URL="sqlite+aiosqlite:///$PILOT_DB_PATH" ENVIRONMENT=development CORS_ORIGINS="$PILOT_WEB_URL" VITE_API_URL="$PILOT_API_URL/api"
export PYTHONPATH="$CHECKOUT/backend${PYTHONPATH:+:$PYTHONPATH}"
if [[ ! -f "$PILOT_ROOT/jwt-secret" ]]; then "$PYTHON" -c 'import secrets,sys; open(sys.argv[1],"w").write(secrets.token_urlsafe(48))' "$PILOT_ROOT/jwt-secret"; chmod 600 "$PILOT_ROOT/jwt-secret"; fi
export JWT_SECRET="$(cat "$PILOT_ROOT/jwt-secret")"
PILOT_RUN_ID="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(24))')"
export PILOT_RUN_ID
[[ -f "$PILOT_DB_PATH" ]] || { touch "$PILOT_DB_PATH"; chmod 600 "$PILOT_DB_PATH"; }
echo "SHA: $PILOT_SHA"; echo "API $PILOT_API_URL | Web $PILOT_WEB_URL | DB $PILOT_DB_PATH"
nohup bash -c 'cd "$1" && exec "$2" -m uvicorn app.main:app --host "$3" --port "$4"' _ "$CHECKOUT/backend" "$PILOT_ROOT/venv/bin/python" "$PILOT_API_HOST" "$PILOT_API_PORT" </dev/null >> "$PILOT_ROOT/logs/api.log" 2>&1 & API_PID=$!
printf '%s\n%s\n' "$API_PID" "$PILOT_RUN_ID" > "$PILOT_ROOT/api.pid"
nohup bash -c 'cd "$1" && exec "$2" "$3" --host "$4" --port "$5" --strictPort' _ "$CHECKOUT/frontend" "$NODE_BIN/node" "$CHECKOUT/frontend/node_modules/vite/bin/vite.js" "$PILOT_WEB_HOST" "$PILOT_WEB_PORT" </dev/null >> "$PILOT_ROOT/logs/web.log" 2>&1 & WEB_PID=$!
printf '%s\n%s\n' "$WEB_PID" "$PILOT_RUN_ID" > "$PILOT_ROOT/web.pid"
cleanup() { kill "$API_PID" "$WEB_PID" 2>/dev/null || true; for pid in "$API_PID" "$WEB_PID"; do for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done; done; rm -f "$PILOT_ROOT/api.pid" "$PILOT_ROOT/web.pid"; }
ready=0
for _ in $(seq 1 60); do
  if ! kill -0 "$API_PID" 2>/dev/null || ! kill -0 "$WEB_PID" 2>/dev/null; then echo "Pornire eșuată; vezi $PILOT_ROOT/logs/{api,web}.log" >&2; cleanup; exit 1; fi
  if "$PYTHON" -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=1)' "$PILOT_API_URL/health" 2>/dev/null && "$PYTHON" -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=1)' "$PILOT_WEB_URL" 2>/dev/null; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "Health sau frontend indisponibil; vezi logurile." >&2; cleanup; exit 1; }
if [[ "${PILOT_AUTO_SEED:-1}" == 1 ]]; then args=(--checkout "$CHECKOUT" --config "$CONFIG"); [[ "$ALLOW_DIRTY" == 1 ]] && args+=(--allow-dirty); if ! "$HERE/seed.sh" "${args[@]}"; then echo "Seed-ul a eșuat; opresc numai procesele pornite de acest start." >&2; cleanup; exit 1; fi; fi
echo "Health: $PILOT_API_URL/health (200)"; echo "Deschide în browser: $PILOT_WEB_URL"; echo "Oprire: $HERE/stop.sh --checkout '$CHECKOUT' --config '$CONFIG'"

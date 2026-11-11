#!/usr/bin/env bash
set -euo pipefail
usage() { echo "Usage: scripts/pilot/<command>.sh --checkout PATH [--config PATH] [--allow-dirty]"; }
parse_common_args() {
  CHECKOUT="" EXTRA_ARGS=() CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dentacare-pilot/pilot.env" ALLOW_DIRTY=0
  while (($#)); do case "$1" in
    --checkout) (($# >= 2)) || { usage >&2; exit 2; }; CHECKOUT="$2"; shift 2 ;;
    --config) (($# >= 2)) || { usage >&2; exit 2; }; CONFIG="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac; done
  [[ -n "$CHECKOUT" ]] || { usage >&2; exit 2; }
  CHECKOUT="$(cd "$CHECKOUT" && pwd -P)"
  [[ -d "$CHECKOUT/.git" || -f "$CHECKOUT/.git" ]] || { echo "Nu este un checkout Git: $CHECKOUT" >&2; exit 2; }
  [[ -f "$CHECKOUT/backend/app/main.py" && -f "$CHECKOUT/frontend/package.json" ]] || { echo "Checkout-ul nu conține API și frontend." >&2; exit 2; }
  [[ "$(git -C "$CHECKOUT" rev-parse --show-toplevel)" == "$CHECKOUT" ]] || { echo "Furnizează rădăcina checkout-ului." >&2; exit 2; }
  PILOT_SHA="$(git -C "$CHECKOUT" rev-parse HEAD)"
  if [[ -n "$(git -C "$CHECKOUT" status --porcelain)" ]]; then
    [[ "$ALLOW_DIRTY" == 1 ]] || { echo "Checkout murdar; refuz release curat. --allow-dirty este numai pentru dezvoltare." >&2; exit 2; }
    echo "CHECKOUT: $CHECKOUT | SHA: $PILOT_SHA | DIRTY DEVELOPMENT"
  else echo "CHECKOUT: $CHECKOUT | SHA: $PILOT_SHA | CLEAN"; fi
  PILOT_ROOT="${DENTACARE_PILOT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/dentacare-pilot}"
  mkdir -p "$PILOT_ROOT"; chmod 700 "$PILOT_ROOT"
  if [[ -f "$CONFIG" ]]; then set -a; # shellcheck disable=SC1090
    source "$CONFIG"; set +a; fi
  PILOT_DB_PATH="${PILOT_DB_PATH:-$PILOT_ROOT/fictional-clinic.sqlite3}"
  PILOT_API_HOST="${PILOT_API_HOST:-127.0.0.1}" PILOT_WEB_HOST="${PILOT_WEB_HOST:-127.0.0.1}"
  PILOT_API_PORT="${PILOT_API_PORT:-8018}" PILOT_WEB_PORT="${PILOT_WEB_PORT:-5188}"
  PILOT_API_URL="http://${PILOT_API_HOST}:${PILOT_API_PORT}" PILOT_WEB_URL="http://${PILOT_WEB_HOST}:${PILOT_WEB_PORT}"
  mkdir -p "$(dirname "$PILOT_DB_PATH")"
  PILOT_CHECKOUT="$CHECKOUT"
  export CHECKOUT PILOT_CHECKOUT CONFIG ALLOW_DIRTY PILOT_SHA PILOT_ROOT PILOT_DB_PATH PILOT_API_HOST PILOT_WEB_HOST PILOT_API_PORT PILOT_WEB_PORT PILOT_API_URL PILOT_WEB_URL
}
python_for_checkout() {
  [[ -x "$PILOT_ROOT/venv/bin/python" ]] && { echo "$PILOT_ROOT/venv/bin/python"; return; }
  command -v python3.12 || { echo "Python 3.12 lipsește; rulează setup.sh în terminalul gazdă." >&2; exit 1; }
}
port_available() { "$1" -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: s.bind((sys.argv[1],int(sys.argv[2])))
except OSError: sys.exit(1)
finally: s.close()' "$2" "$3"; }

#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
PYTHON="$(command -v python3.12 || true)"
[[ -n "$PYTHON" ]] || PYTHON="$(test -x /home/raul/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 && echo /home/raul/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 || true)"
[[ -n "$PYTHON" ]] || { echo "Instalează Python 3.12 pentru utilizatorul curent și reia setup." >&2; exit 1; }
"$PYTHON" --version
NODE_BIN="$(dirname "$(command -v node 2>/dev/null || true)")"
[[ -x /home/raul/Projects/dentacare-tools/node/bin/node ]] && NODE_BIN=/home/raul/Projects/dentacare-tools/node/bin
[[ -x "$NODE_BIN/node" ]] || { echo "Instalează/activează Node 22 pentru utilizatorul curent." >&2; exit 1; }
NODE_VERSION="$("$NODE_BIN/node" -p 'process.versions.node')"
[[ "${NODE_VERSION%%.*}" == 22 ]] || { echo "Este necesar Node 22; detectat $NODE_VERSION." >&2; exit 1; }
export PATH="$NODE_BIN:$PATH"
[[ -x "$PILOT_ROOT/venv/bin/python" ]] || "$PYTHON" -m venv "$PILOT_ROOT/venv"
"$PILOT_ROOT/venv/bin/python" -m pip install -e "$CHECKOUT/backend"
npm --prefix "$CHECKOUT/frontend" ci
mkdir -p "$(dirname "$CONFIG")"
if [[ ! -f "$CONFIG" ]]; then
  cat > "$CONFIG" <<CFG
# Config locală fără secrete.
PILOT_DB_PATH="$PILOT_ROOT/fictional-clinic.sqlite3"
PILOT_API_PORT=8018
PILOT_WEB_PORT=5188
CFG
  chmod 600 "$CONFIG"
fi
echo "Setup terminat pentru SHA $PILOT_SHA. Bază: $PILOT_DB_PATH"

#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; source "$HERE/common.sh"; parse_common_args "$@"
errors=0
for service in api web; do
  pidfile="$PILOT_ROOT/$service.pid"
  [[ -f "$pidfile" ]] || continue
  mapfile -t pid_data < "$pidfile"
  pid="${pid_data[0]:-}" run_id="${pid_data[1]:-}"
  if [[ ! "$pid" =~ ^[0-9]+$ || ! "$run_id" =~ ^[a-f0-9]{48}$ ]]; then echo "Marcaj PID invalid în $pidfile; nu îl opresc." >&2; errors=1; continue; fi
  if kill -0 "$pid" 2>/dev/null; then
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    env_markers="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null || true)"
    if [[ "$env_markers" != *"PILOT_RUN_ID=$run_id"* ]] || [[ "$cwd" != "$CHECKOUT/backend" && "$cwd" != "$CHECKOUT/frontend" ]] || [[ "$service" == api && "$cmd" != *"uvicorn"* ]] || [[ "$service" == web && "$cmd" != *"vite"* ]]; then
      echo "PID $pid nu mai corespunde serviciului pilot din checkout; nu îl opresc." >&2; errors=1; continue
    fi
    kill "$pid"
    for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$pid" 2>/dev/null && { echo "PID $pid nu s-a oprit în 2.5s." >&2; errors=1; continue; }
    echo "Oprit $service PID $pid"
  else echo "$service PID $pid era deja închis"; fi
  rm -f "$pidfile"
done
exit "$errors"

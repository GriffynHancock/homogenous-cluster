#!/usr/bin/env bash
# Start the Missing Link web UI.
#
# Run from a SCRIPT, not inline: an inline `pkill -f 'uvicorn missing_link'`
# alongside the launch command matches the launch command in the same shell's
# own cmdline and kills the shell. The [n] bracket stops the pattern matching
# ITSELF but not a sibling command beside it. Cost three dead shells to learn.
#
#   ./missing-link/start-ui.sh [llama-url] [port]
set -uo pipefail
cd "$(dirname "$0")"

LLAMA_URL="${1:-http://127.0.0.1:8080}"
PORT="${2:-8000}"
DB="${MISSING_LINK_DB:-/opt/missing-link/jobs.sqlite}"
# Fan out across R endpoints (job-level, see docs/DESIGN-NOTES.md section G):
# set LLAMA_URLS in the environment as a comma-separated list, e.g. from
# provisioning/nodes.env's INFERENCE_ENDPOINTS. Left unset, app.py falls back
# to the single LLAMA_URL above -- one worker, one server, exactly as before.
LLAMA_URLS="${LLAMA_URLS:-}"

mkdir -p "$(dirname "$DB")" 2>/dev/null || sudo mkdir -p "$(dirname "$DB")"
[ -w "$(dirname "$DB")" ] || sudo chown "$(id -un)" "$(dirname "$DB")"

# Stop any previous instance. Bracketed so it cannot match this script's own
# invocation, and it is the ONLY thing on this line.
pkill -f "uvicor[n] missing_link.app" 2>/dev/null
sleep 1

echo "=== starting Missing Link UI ==="
if [ -n "$LLAMA_URLS" ]; then
  echo "  backends: $LLAMA_URLS"
else
  echo "  backend : $LLAMA_URL"
fi
echo "  db      : $DB"
echo "  port    : $PORT"

MISSING_LINK_DB="$DB" LLAMA_URL="$LLAMA_URL" LLAMA_URLS="$LLAMA_URLS" \
  nohup .venv/bin/python -m uvicorn missing_link.app:app \
    --host 0.0.0.0 --port "$PORT" > /tmp/missing-link.log 2>&1 &

for _ in $(seq 1 30); do
  curl -sf --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done

if curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "  UP. health:"
  curl -s "http://127.0.0.1:$PORT/health"
  echo
else
  echo "  FAILED to start. Log:"
  tail -25 /tmp/missing-link.log
  exit 1
fi

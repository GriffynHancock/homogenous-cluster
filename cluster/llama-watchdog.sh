#!/usr/bin/env bash
# Restart llama-server if it is HUNG rather than dead.
#
# WHY THIS EXISTS. On 2026-08-17 a client disconnected mid-generation (a
# `systemctl restart missing-link` while a job was running) and left
# llama-server **alive, accepting TCP, and serving nothing**: /slots returned no
# response at all, and a trivial completion request timed out after 85 seconds.
# It recovered instantly on restart, so it was wedged, not slow.
#
# systemd's Restart=always CANNOT catch this, because the process never exited.
# A liveness check has to come from outside the process, which is the simplest
# possible instance of the "agent appliance" idea in CLAUDE.md -- and it is why
# that section insists a monitor must not share the thing's failure modes.
#
# Deliberately conservative: two consecutive failures before acting, because
# restarting a healthy server costs a multi-minute 65 GB reload (F3).
set -uo pipefail

PORT="${1:-8080}"
UNIT="llama-server@${PORT}"
STATE="/run/llama-watchdog-${PORT}.fails"
TIMEOUT="${WATCHDOG_TIMEOUT:-25}"
THRESHOLD="${WATCHDOG_THRESHOLD:-2}"

fails=$(cat "$STATE" 2>/dev/null || echo 0)

# Only probe if systemd thinks it is up. If it is genuinely down, Restart=always
# owns the problem and we must not fight it.
if [ "$(systemctl is-active "$UNIT")" != "active" ]; then
  echo 0 > "$STATE"
  exit 0
fi

if curl -sf --max-time "$TIMEOUT" "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  [ "$fails" != "0" ] && logger -t llama-watchdog "$UNIT healthy again after $fails failure(s)"
  echo 0 > "$STATE"
  exit 0
fi

fails=$((fails + 1))
echo "$fails" > "$STATE"
logger -t llama-watchdog "$UNIT did not answer /health within ${TIMEOUT}s (failure ${fails}/${THRESHOLD})"

if [ "$fails" -ge "$THRESHOLD" ]; then
  logger -t llama-watchdog "restarting $UNIT -- hung but alive, Restart=always cannot see this"
  systemctl restart "$UNIT"
  echo 0 > "$STATE"
fi

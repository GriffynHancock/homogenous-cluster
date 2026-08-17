#!/usr/bin/env bash
# Restart llama-server if it is WEDGED, and leave it alone if it is merely BUSY.
#
# ---------------------------------------------------------------------------
# WHY THIS EXISTS (F36). On 2026-08-17 a client disconnected mid-generation and
# left llama-server **alive, accepting TCP, and serving nothing**: /slots
# returned no response at all, a trivial completion timed out after 85 s, and
# the machine sat at load 0.11. `Restart=always` cannot see that, because the
# process never exited. Liveness has to be judged from outside the process.
#
# WHY IT LOOKS LIKE THIS (F39). The first version of this script probed
# `/health` with a 25 s timeout and restarted after two consecutive failures.
# **That version destroyed a healthy job 79 minutes after it was installed** --
# 10m55s of real work on a 97,299-character document, thrown away mid-chunk.
#
# The reason is in ik_llama.cpp's `server.cpp`. `handle_health` does NOT answer
# out of band; it builds a SERVER_TASK_TYPE_METRICS task, posts it onto
# `ctx_server.queue_tasks` -- the SAME queue `update_slots()` drains for token
# generation -- and blocks on `queue_results.recv()`. `/slots` and `/metrics`
# use the identical pattern. So all three share the resource they are supposed
# to be probing, and go silent exactly when the server is busiest.
#
# Measured on this hardware from the incident log: during prefill the queue is
# drained only once per `n_batch` (2048 tokens) step, and one step took 90-130 s
# at the measured 21.14 t/s. `/health` therefore has a legitimate worst-case
# latency of ~97-130 s on a perfectly healthy server -- TWICE the 60 s timer
# period. **No `--max-time` value can separate busy from wedged.**
#
# Nor can its payload: during prefill `/health` reports
# `n_idle_slots=4 n_processing_slots=0` -- the server calls itself idle while it
# is working hardest. Do not gate on `slots_processing` either.
#
# WHAT SEPARATES THEM. A busy server is silent BUT BURNING CPU. A wedged server
# is silent AND IDLE (F36: load 0.11). So the decision is made on cgroup CPU
# time, which is genuinely out of band -- it does not touch the task queue, the
# HTTP layer, or the model:
#
#   /health answers with ANY http code   -> alive. Not a fault. (503 "Loading
#                                           model" included: it is talking.)
#   /health silent + CPU advancing       -> BUSY. Not a fault. Never restart.
#   /health silent + CPU flat            -> candidate fault. Start/extend a
#                                           wall-clock streak. Restart only
#                                           after WATCHDOG_GRACE seconds of
#                                           UNBROKEN silent-and-idle evidence.
#
# `/props` is probed as corroboration only. It is the one endpoint that does
# NOT post to the task queue (it reads model metadata directly), so it
# separates "HTTP layer alive, inference dead" -- the F36 signature -- from
# "whole process gone". It never decides anything on its own.
#
# WHY CGROUP CPU AND NOT LOAD AVERAGE. Load average is machine-wide. This
# coordinator routinely runs model downloads, test suites and agent work; it
# read 2.30 while llama-server was completely idle. `CPUUsageNSec` is scoped to
# the unit's own cgroup and is immune to that. Measured idle baseline: **0 ns
# over 20 s**, against ~4 core-seconds per wall second during prefill (-t 4).
# There is no ambiguity to resolve.
#
# WHY NOT JOURNAL PROGRESS. llama-server logs per REQUEST, not per ubatch. Its
# only output during a 97 s prefill step is the line the watchdog's own probe
# provokes, so journal advance mostly measures the watchdog. Rejected.
#
# FAIL SAFE. Every ambiguous case resets the streak. A false restart costs the
# in-flight job plus a model reload (F3), so the script only acts on positive
# evidence of death, never on absence of evidence of life.
# ---------------------------------------------------------------------------
set -uo pipefail

PORT="${1:-8080}"
UNIT="llama-server@${PORT}"
STATE="/run/llama-watchdog-${PORT}.state"
RECORD_DIR="${WATCHDOG_RECORD_DIR:-/var/lib/llama-watchdog}"
RECORD="${RECORD_DIR}/restarts.jsonl"

# Probe timeout. There is no point going long: under prefill the queue is only
# served every ~97 s, so anything short of that still times out, and every
# timed-out probe leaves one of the server's SEVEN http worker threads
# (n_threads_http=7, from the startup log) blocked in queue_results.recv until
# the queue drains. Short probes keep that pool from being exhausted by the
# monitor itself.
TIMEOUT="${WATCHDOG_TIMEOUT:-20}"

# Unbroken wall-clock seconds of silent-AND-idle before restarting.
# 300 s = five consecutive minute ticks that ALL saw no HTTP response and no
# CPU burned. F36's real wedge held that signature for 7+ minutes.
GRACE="${WATCHDOG_GRACE:-300}"

# Longer grace when RPC backends are configured: with `--rpc`, the coordinator
# legitimately sits idle on a socket while a remote shard computes, so local
# CPU is NOT proof of life. Untested against a real wedge in that topology --
# treat this number as a safety margin, not a measurement.
RPC_GRACE="${WATCHDOG_RPC_GRACE:-900}"

# "Burning CPU" threshold, as a fraction of one core over the probe window.
# Idle measures 0 ns; prefill measures ~400% of one core. 5% is a floor chosen
# to be ~2 orders of magnitude clear of both.
BUSY_PCT="${WATCHDOG_BUSY_PCT:-5}"

DRY_RUN="${WATCHDOG_DRY_RUN:-0}"

log() { logger -t llama-watchdog -p daemon.info -- "$*"; echo "$*"; }

cpu_ns() {
  local v
  v=$(systemctl show -p CPUUsageNSec --value "${UNIT}.service" 2>/dev/null)
  case "$v" in (*[!0-9]*|'') echo "" ;; (*) echo "$v" ;; esac
}

clear_streak() { rm -f "$STATE"; }

# ---------------------------------------------------------------------------
# 0. Never fight Restart=always. If systemd already knows it is down, it owns
#    the problem and there is nothing here to judge.
# ---------------------------------------------------------------------------
if [ "$(systemctl is-active "$UNIT")" != "active" ]; then
  clear_streak
  exit 0
fi

MAIN_PID=$(systemctl show -p MainPID --value "${UNIT}.service" 2>/dev/null)
CMDLINE=""
[ -n "${MAIN_PID:-}" ] && [ "$MAIN_PID" != "0" ] && \
  CMDLINE=$(tr '\0' ' ' < "/proc/${MAIN_PID}/cmdline" 2>/dev/null)
case "$CMDLINE" in
  *--rpc*) GRACE="$RPC_GRACE"; RPC_NOTE=" rpc=yes" ;;
  *)       RPC_NOTE="" ;;
esac

# ---------------------------------------------------------------------------
# 1. Probe /health, measuring the unit's own CPU consumption ACROSS the probe.
#    The probe window is the observation window; no extra sleeping.
# ---------------------------------------------------------------------------
c0=$(cpu_ns); t0=$(date +%s%N)
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
       "http://127.0.0.1:${PORT}/health" 2>/dev/null)
t1=$(date +%s%N); c1=$(cpu_ns)

if [ "${code:-000}" != "000" ]; then
  if [ -f "$STATE" ]; then
    log "$UNIT answered /health (HTTP ${code}) -- clearing fault streak"
  fi
  clear_streak
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. No HTTP response. Is it working or is it dead? Ask the CPU.
# ---------------------------------------------------------------------------
wall_ns=$((t1 - t0))
[ "$wall_ns" -le 0 ] && wall_ns=1

if [ -z "$c0" ] || [ -z "$c1" ]; then
  # No CPU accounting available. We cannot tell busy from wedged, so we must
  # NOT act. Say so loudly rather than guessing.
  log "$UNIT silent for ${TIMEOUT}s but CPUUsageNSec is unavailable -- cannot" \
      "distinguish busy from wedged, taking NO action. Enable CPUAccounting."
  clear_streak
  exit 0
fi

cpu_delta=$((c1 - c0))
# core-percent = 100 * cpu_delta / wall  (integer maths, no bc dependency)
cpu_pct=$(( cpu_delta * 100 / wall_ns ))

if [ "$cpu_pct" -ge "$BUSY_PCT" ]; then
  log "$UNIT did not answer /health within ${TIMEOUT}s but burned" \
      "$((cpu_delta / 1000000)) ms CPU in $((wall_ns / 1000000)) ms" \
      "(${cpu_pct}% of one core) -- BUSY, not wedged. No action." \
      "This is the map-reduce prefill path: /health queues behind" \
      "generation work in ik_llama.cpp's single task queue (F39)."
  clear_streak
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. Silent AND idle. Corroborate with /props, which is the only endpoint that
#    does not post to the task queue, then accumulate wall-clock evidence.
# ---------------------------------------------------------------------------
props=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        "http://127.0.0.1:${PORT}/props" 2>/dev/null)
if [ "${props:-000}" = "000" ]; then
  layer="http layer also silent (/props ${props:-000}) -- whole process unresponsive"
else
  layer="http layer ALIVE (/props ${props}) but task queue not draining -- the F36 signature"
fi

now=$(date +%s)
since=$(cat "$STATE" 2>/dev/null)
case "${since:-}" in (*[!0-9]*|'') since="$now"; echo "$since" > "$STATE" ;; esac
streak=$((now - since))

evidence="silent=${TIMEOUT}s cpu=$((cpu_delta / 1000000))ms/$((wall_ns / 1000000))ms(${cpu_pct}%) props=${props:-000} streak=${streak}s/${GRACE}s${RPC_NOTE}"

if [ "$streak" -lt "$GRACE" ]; then
  log "$UNIT SUSPECT: no /health response and no CPU burned. ${layer}. ${evidence}." \
      "Holding -- will not restart until ${GRACE}s of unbroken evidence."
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Act, and leave a record an investigator can correlate with a dead job.
# ---------------------------------------------------------------------------
server_since=$(systemctl show -p ActiveEnterTimestamp --value "${UNIT}.service" 2>/dev/null)

log "RESTARTING $UNIT -- WEDGED: ${streak}s of unbroken silence with the" \
    "unit's cgroup burning no CPU. ${layer}. ${evidence}." \
    "ANY IN-FLIGHT JOB ON PORT ${PORT} DIED AT THIS TIMESTAMP" \
    "(clients see RemoteDisconnected)."

mkdir -p "$RECORD_DIR" 2>/dev/null
printf '{"ts":"%s","epoch":%s,"unit":"%s","port":"%s","action":"%s","reason":"wedged","streak_s":%s,"grace_s":%s,"cpu_delta_ms":%s,"window_ms":%s,"cpu_pct_of_core":%s,"health":"000","props":"%s","probe_timeout_s":%s,"server_active_since":"%s"}\n' \
  "$(date -Is)" "$now" "$UNIT" "$PORT" \
  "$([ "$DRY_RUN" = "1" ] && echo dry-run || echo restart)" \
  "$streak" "$GRACE" "$((cpu_delta / 1000000))" "$((wall_ns / 1000000))" \
  "$cpu_pct" "${props:-000}" "$TIMEOUT" "$server_since" \
  >> "$RECORD" 2>/dev/null

if [ "$DRY_RUN" = "1" ]; then
  log "WATCHDOG_DRY_RUN=1 -- would have restarted $UNIT, but did not."
  clear_streak
  exit 0
fi

systemctl restart "$UNIT"
rc=$?
log "$UNIT restart issued (systemctl rc=${rc}). Restart recorded in ${RECORD}."
clear_streak

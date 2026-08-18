#!/usr/bin/env bash
# Does chunk size matter for WALL-CLOCK, not just summary quality?
#
# STATUS.md/CLAUDE.md's retained research note ("chunk size barely matters for
# map-reduce, ~4K with 10% overlap is fine, not worth tuning") is about summary
# QUALITY (BooookScore). It says nothing about wall-clock, and wall-clock is
# what this project is bottlenecked on. Chunk size trades off in at least four
# measured ways -- this script measures all four in one pass:
#
#   1. Prefill throughput vs prompt length is MODEL-DEPENDENT (F24: gpt-oss
#      loses ~1% from pp512->pp2048; a dense 4B lost 14%). Must be measured on
#      the model actually run, not assumed from another model's curve.
#   2. Smaller chunks -> more chunks -> more fixed per-request overhead (a
#      harmony-format system/template preamble is paid on EVERY call) and more
#      total generated summary tokens (generation is the slow, expensive side:
#      ~5 t/s on ik_llama/gpt-oss-120b, F27).
#   3. With a FIXED overlap FRACTION (10%, as this repo uses), the total
#      prefill token volume across a whole document is
#      doc_tokens / (1 - overlap_fraction) -- i.e. an ~11% tax for f=0.10 --
#      and it does NOT depend on chunk size. Verify this prediction; it means
#      any wall-clock difference between sizes comes from PER-CALL overhead and
#      generation volume, not from re-reading more of the document.
#   4. The slot budget is a hard ceiling, not a preference. n_ctx_slot = -c /
#      --parallel (confirmed via `journalctl ... | grep n_ctx_slot`, never
#      inferred from -c), and CHUNK_TOKENS + prompt wrapper + MAP_MAX_TOKENS
#      must fit inside it. This silently broke a real job before (STATUS.md).
#
# This script drives the REAL missing_link.worker pipeline (chunk_document,
# build_prompt, build_reduce_prompt, LlamaClient, extract_content) against a
# REAL document already in the jobs database -- see chunk_size_driver.py's
# docstring for why that matters more than a synthetic fixture (F34/F38).
#
# Usage:
#   ./bench/chunk-size-bench.sh <endpoint> [chunk_sizes_csv]
#   ./bench/chunk-size-bench.sh http://10.10.0.39:8080
#   ./bench/chunk-size-bench.sh http://10.10.0.39:8080 1024,2048,4096
#
# Does NOT hardcode a node -- the endpoint is a required argument. Point it at
# whichever llama-server is safe to load right now; NEVER at a node serving
# other work (this project's own node 1 llama-server@8080 was off-limits for
# the run that produced docs/measurements.md's numbers -- see the header of
# that section).
set -euo pipefail

ENDPOINT="${1:?Usage: $0 <endpoint e.g. http://10.10.0.39:8080> [chunk_sizes_csv]}"
CHUNK_SIZES="${2:-1024,2048,3072,4096,6144}"

DB="${DB:-/opt/missing-link/jobs.sqlite}"
JOB_ID="${JOB_ID:-06af2911d7fc}"
KIND="${KIND:-summarise}"
OVERLAP_FRACTION="${OVERLAP_FRACTION:-0.10}"
OUT_DIR="${OUT_DIR:-bench/out/chunk-size-bench}"
JSON_OUT="${JSON_OUT:-$OUT_DIR/results.json}"

# Prefer the repo's own venv (missing_link has no external deps beyond the
# stdlib, but this keeps one interpreter authoritative for the whole project).
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$(dirname "$0")/../missing-link/.venv/bin/python" ]; then
    PYTHON="$(dirname "$0")/../missing-link/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

echo "=== Chunk-size wall-clock sweep ==="
echo "  endpoint : $ENDPOINT"
echo "  sizes    : $CHUNK_SIZES"
echo "  db/job   : $DB  ($JOB_ID)"
echo "  overlap  : $OVERLAP_FRACTION"
echo

echo "=== Preflight: endpoint healthy? ==="
if ! curl -sf --max-time 10 "$ENDPOINT/health" >/dev/null; then
  echo "FATAL: $ENDPOINT/health did not respond. Is llama-server up on that node?" >&2
  exit 1
fi
curl -s --max-time 10 "$ENDPOINT/health"; echo
echo

# n_ctx_slot MUST be read from the server's own log, never inferred from -c --
# see the STATUS.md incident this repo already had. This script cannot always
# reach the remote journal (SSH access varies), so it prints the reminder
# loudly and checks automatically when NODE_SSH_HOST is given.
echo "=== Slot budget check ==="
echo "  CHUNK_TOKENS (max of sweep) + wrapper (~50-900 tok, harmony/system"
echo "  overhead -- measured, not assumed) + MAP_MAX_TOKENS (1024, unless"
echo "  overridden) must fit inside ONE slot's n_ctx_slot."
if [ -n "${NODE_SSH_HOST:-}" ]; then
  echo "  n_ctx_slot on $NODE_SSH_HOST, from the server's own startup log:"
  ssh -o BatchMode=yes "$NODE_SSH_HOST" \
    "journalctl -u llama-server@${LLAMA_PORT:-8080} --no-pager 2>/dev/null | grep n_ctx_slot | tail -4" \
    || echo "  (could not read journal on $NODE_SSH_HOST -- check manually)"
else
  echo "  NODE_SSH_HOST not set -- check manually:"
  echo "    ssh <node> 'journalctl -u llama-server@8080 | grep n_ctx_slot'"
fi
echo

mkdir -p "$OUT_DIR"

# RESTART THE SERVER BEFORE A SWEEP, or the small chunk sizes measure the prompt
# cache instead of prefill. Observed 2026-08-18: a run started minutes after a
# reproducer that had sent the same chunk prompts reported prompt_n=5 on a 1339
# token chunk and a wall clock a third of the truth (F17's cache trap, in the one
# form the driver's docstring said did not apply).
if [ -n "${NODE_SSH_HOST:-}" ] && [ "${RESTART_BEFORE_SWEEP:-1}" = "1" ]; then
  echo "=== Clearing the prompt cache (restarting llama-server@${LLAMA_PORT:-8080}) ==="
  ssh -o BatchMode=yes "$NODE_SSH_HOST" \
    "sudo -n systemctl restart llama-server@${LLAMA_PORT:-8080}" || {
      echo "FATAL: could not restart llama-server on $NODE_SSH_HOST." >&2; exit 1; }
  echo -n "  waiting for the model to reload (minutes, F3)"
  for _ in $(seq 1 90); do
    if curl -sf --max-time 5 "$ENDPOINT/health" >/dev/null 2>&1; then echo " ready"; break; fi
    echo -n "."; sleep 10
  done
  curl -sf --max-time 10 "$ENDPOINT/health" >/dev/null || {
    echo; echo "FATAL: $ENDPOINT never came back after the restart." >&2; exit 1; }
  echo
fi

# A dead backend must end this sweep in minutes with an error, not silently absorb
# hours of it. The first run of this script blocked for ~48 minutes against a
# server that had fatal-errored and printed nothing (F40). NODE_SSH_HOST is now
# load-bearing rather than decorative: it lets the driver read the unit's own
# CPUUsageNSec, the ONLY signal that separates a saturated server from a wedged
# one (F39 -- /health rides the same task queue as the work).
WATCHDOG_ARGS=()
if [ -n "${NODE_SSH_HOST:-}" ]; then
  WATCHDOG_ARGS=(--node-ssh-host "$NODE_SSH_HOST" --node-unit "llama-server@${LLAMA_PORT:-8080}")
else
  echo "WARNING: NODE_SSH_HOST is not set. The sweep still cannot hang -- per-request"
  echo "  timeouts are finite and /health is gated between requests -- but a backend"
  echo "  that dies MID-REQUEST will not be noticed until that request's timeout"
  echo "  expires, and the prompt cache is NOT cleared beforehand."
  echo
fi

echo "=== Driving the real map-reduce pipeline per chunk size ==="
# `set -e` would abort here before RC is read, skipping the "where the results
# are" footer on exactly the runs where the operator most needs it.
set +e
"$PYTHON" "$(dirname "$0")/chunk_size_driver.py" \
  --endpoint "$ENDPOINT" \
  --db "$DB" \
  --job-id "$JOB_ID" \
  --kind "$KIND" \
  --chunk-sizes "$CHUNK_SIZES" \
  --overlap-fraction "$OVERLAP_FRACTION" \
  --out-dir "$OUT_DIR" \
  --json-out "$JSON_OUT" \
  "${WATCHDOG_ARGS[@]}"
RC=$?
set -e

if [ "$RC" -ne 0 ]; then
  echo
  echo "=== SWEEP FAILED (exit $RC) ==="
  echo "The driver stops the WHOLE sweep on a dead backend rather than working"
  echo "through the remaining sizes against a corpse. Check the server's own"
  echo "journal before re-running:"
  echo "  ssh <node> 'sudo journalctl -u llama-server@${LLAMA_PORT:-8080} -n 60'"
  echo "and note that ik_llama.cpp can abort into a forked backtrace handler that"
  echo "never exits, so systemd will still report the unit active (F40)."
fi

echo
echo "Per-size summaries (for a coherence read, not just the numbers) are in"
echo "  $OUT_DIR/chunk_<N>.txt"
echo "Raw JSON: $JSON_OUT"
echo "Record the result in docs/measurements.md. A faster chunk size that"
echo "degrades output is not a win (CLAUDE.md, Verification)."
exit $RC

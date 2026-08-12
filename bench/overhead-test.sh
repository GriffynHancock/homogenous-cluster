#!/usr/bin/env bash
# Isolate llama.cpp RPC protocol overhead with the network removed.
#
# Runs llama-bench locally, then again through an rpc-server on 127.0.0.1. The
# delta is pure protocol cost -- serialisation, syscalls and round-trips -- with
# zero network latency and zero bandwidth limit. Any overhead measured here is
# a floor: the real cluster adds gigabit on top.
#
# This is the gate for the whole architecture. See the decision rule at the end.
set -euo pipefail

BIN="${BIN:-/opt/llama.cpp/bin}"
MODEL="${MODEL:-/opt/models/qwen3-4b-q4km.gguf}"
# NOTE: node 1 is 4 physical cores / 8 threads. nproc reports 8. Generation is
# bandwidth-bound and SMT siblings share a memory pipe, so -t 8 is not
# obviously better than -t 4 -- which is why THREADS is measured, not assumed.
THREADS="${THREADS:-$(nproc)}"
PORT="${PORT:-50052}"
REPS="${REPS:-3}"

[ -f "$MODEL" ] || { echo "FATAL: model not found: $MODEL" >&2; exit 1; }

echo "Model:   $MODEL"
echo "Threads: $THREADS"
echo "Build:   $(cat "${BIN%/bin}/VERSION" 2>/dev/null || echo unknown)"
echo

echo "=== Baseline: local, no RPC ==="
"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512 -n 128 -r "$REPS"

echo
echo "=== Through RPC on localhost ==="
# -c enables the local tensor cache. Always on: without it every start
# re-pushes the full model, and there is a report of the process going
# <defunct> when run headless without it.
"$BIN/rpc-server" -H 127.0.0.1 -p "$PORT" -t "$THREADS" -c &
RPC_PID=$!
trap 'kill $RPC_PID 2>/dev/null || true' EXIT

# Wait for the port rather than sleeping a fixed interval -- on a cold page
# cache this can take longer than any constant you would pick.
for _ in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && { exec 3<&-; break; }
  sleep 1
done

"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512 -n 128 -r "$REPS" \
  --rpc 127.0.0.1:"$PORT"

kill $RPC_PID 2>/dev/null || true

cat <<'EOF'

Compare the pp512 (prefill) and tg128 (generation) rows between the two tables.

Decision rule -- on the GENERATION (tg128) figure:
  under 15%  -> proceed to Phase 1 as planned
  15-30%     -> proceed, record as a known cost, re-test when PR #18626 lands
  over 30%   -> STOP and escalate. Do not silently continue.
EOF

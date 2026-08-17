#!/usr/bin/env bash
# Two-node RPC smoke test. RUN THIS BEFORE PROVISIONING NODES 3-7 AND BEFORE
# DOWNLOADING MODEL B.
#
# Why it exists (docs/FINDINGS.md F2): upstream PR #26500 is an OPEN, UNMERGED
# bug where a graph tensor whose buffer belongs to a DIFFERENT rpc-server gets
# its pointer serialised anyway. The worker aborts with
#
#     [create_node] invalid data ptr
#
# It is triggered by having TWO OR MORE rpc workers -- never one -- and has been
# reproduced on a CPU-only multi-worker cluster running a sparse MoE model,
# which is exactly this architecture. It is fixed in NO released tag, so the
# pinned build cannot dodge it.
#
# Finding this at node 2 costs an afternoon. Finding it at node 7, after
# fetching half a terabyte of weights, costs a week.
#
# Usage:  ./bench/two-node-smoke.sh <worker-ip> [model]
set -uo pipefail

BIN=/opt/llama.cpp/bin
WORKER_IP="${1:?usage: two-node-smoke.sh <worker-ip> [model]}"
MODEL="${2:-/opt/models/qwen3-4b-q4km.gguf}"
RPC_PORT="${RPC_PORT:-50052}"
PORT="${PORT:-8081}"
CORES=$(lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l)
LOG=/tmp/two-node-smoke.log

[ -f "$MODEL" ] || { echo "FATAL: model not found: $MODEL" >&2; exit 1; }

cleanup() {
  [ -n "${SRV_PID:-}" ] && kill "$SRV_PID" 2>/dev/null
  [ -n "${LOCAL_RPC:-}" ] && kill "$LOCAL_RPC" 2>/dev/null
  return 0
}
trap cleanup EXIT

echo "=== Starting local rpc-server (node 1) on 127.0.0.1:$RPC_PORT ==="
"$BIN/rpc-server" -H 127.0.0.1 -p "$RPC_PORT" -t "$CORES" -c \
  >/tmp/smoke-local-rpc.log 2>&1 &
LOCAL_RPC=$!

echo "=== Checking worker $WORKER_IP:$RPC_PORT ==="
if ! timeout 5 bash -c "cat < /dev/null > /dev/tcp/$WORKER_IP/$RPC_PORT" 2>/dev/null; then
  echo "FATAL: $WORKER_IP:$RPC_PORT not reachable." >&2
  echo "  ssh $WORKER_IP systemctl status rpc-server@$RPC_PORT" >&2
  exit 1
fi
echo "  reachable"

for _ in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/"$RPC_PORT") 2>/dev/null && { exec 3<&-; break; }
  sleep 1
done

echo
echo "=== Launching llama-server sharded across BOTH nodes ==="
# An even split forces tensors onto both servers, which is what provokes the
# cross-server pointer bug. A lopsided split could keep everything on one node
# and pass vacuously.
"$BIN/llama-server" \
  --rpc "127.0.0.1:$RPC_PORT,$WORKER_IP:$RPC_PORT" \
  --tensor-split 1,1 \
  -m "$MODEL" -t "$CORES" -c 4096 \
  --host 127.0.0.1 --port "$PORT" --no-webui > "$LOG" 2>&1 &
SRV_PID=$!

echo "  waiting for the server to become healthy (first load pushes weights over the wire)..."
HEALTHY=0
for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then HEALTHY=1; break; fi
  kill -0 "$SRV_PID" 2>/dev/null || break
  sleep 5
done

if [ "$HEALTHY" -ne 1 ]; then
  echo
  echo "=== FAIL: server never became healthy ==="
  if grep -q 'invalid data ptr' "$LOG" /tmp/smoke-local-rpc.log 2>/dev/null; then
    cat <<'EOF'

  >>> "[create_node] invalid data ptr" -- this IS upstream bug #26500. <<<

  Confirmed: multi-worker RPC is broken on this build. Options:
    1. Cherry-pick PR #26500 onto the pinned tag (small, self-contained), then
       rebuild and redistribute.
    2. Wait for it to merge and re-pin.
  DO NOT provision nodes 3-7 or download Model B until this passes.
EOF
  fi
  echo "--- llama-server log (tail) ---"; tail -40 "$LOG"
  echo "--- local rpc-server log (tail) ---"; tail -20 /tmp/smoke-local-rpc.log
  echo "--- worker log ---"
  ssh "$WORKER_IP" "journalctl -u rpc-server@$RPC_PORT -n 40 --no-pager" 2>/dev/null \
    || echo "(could not read worker journal)"
  exit 1
fi

echo "  healthy"
echo
echo "=== Layer assignment ==="
grep -iE 'assigned|RPC\[|buffer size|offloaded' "$LOG" | head -20

echo
echo "=== Generating (the real test -- graph compute across both nodes) ==="
REPLY=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"In two sentences, explain why an organisation with sensitive records might run an AI model on its own hardware."}],"max_tokens":150}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"])' 2>/dev/null)

if [ -z "$REPLY" ]; then
  echo "FAIL: no usable response."
  grep -iE 'invalid data ptr|error|abort|crash' "$LOG" | tail -20
  exit 1
fi

echo "$REPLY"
echo
echo "=== Server-reported timings ==="
grep -E 'prompt eval time|eval time' "$LOG" | tail -4

cat <<'EOF'

=== PASS ===
Two-node RPC sharding works on this build: coherent output, no
"[create_node] invalid data ptr". Bug #26500 does not fire for this model.

NOTE: it is model-dependent -- the public reproductions involve MoE graphs
with unusual constant/view nodes. Re-run this with the ACTUAL Model B GGUF
before committing to the full 7-node launch.
EOF

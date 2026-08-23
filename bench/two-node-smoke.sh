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

# The worker's admin login is per node (nodes.env 5th field, default = this
# machine's own login). Only used for the DIAGNOSTIC journal read on failure --
# the test itself speaks RPC on a raw IP, which is deliberately login-agnostic.
WORKER_SSH="$WORKER_IP"
if [ -r "$(dirname "$0")/../provisioning/nodes.env" ]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/../provisioning/nodes.env"
  WORKER_SSH=$(node_target_for "$WORKER_IP")
fi

[ -f "$MODEL" ] || { echo "FATAL: model not found: $MODEL" >&2; exit 1; }

cleanup() {
  [ -n "${SRV_PID:-}" ] && kill "$SRV_PID" 2>/dev/null
  [ -n "${LOCAL_RPC:-}" ] && kill "$LOCAL_RPC" 2>/dev/null
  return 0
}
trap cleanup EXIT

# This script predates cluster/install-services.sh, which now runs
# rpc-server@50052 under systemd on EVERY node including the coordinator.
# Starting a second one here would fail to bind (systemd holds 0.0.0.0:50052,
# which already covers 127.0.0.1) and the test would then silently proceed
# against the systemd instance anyway -- passing for reasons other than the ones
# printed. Detect and reuse instead.
if timeout 3 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$RPC_PORT" 2>/dev/null; then
  echo "=== Reusing the rpc-server already listening on 127.0.0.1:$RPC_PORT ==="
  echo "    (systemd rpc-server@$RPC_PORT -- not starting a second instance)"
  : > /tmp/smoke-local-rpc.log
else
  echo "=== Starting local rpc-server (node 1) on 127.0.0.1:$RPC_PORT ==="
  "$BIN/rpc-server" -H 127.0.0.1 -p "$RPC_PORT" -t "$CORES" -c \
    >/tmp/smoke-local-rpc.log 2>&1 &
  LOCAL_RPC=$!
fi

echo "=== Checking worker $WORKER_IP:$RPC_PORT ==="
if ! timeout 5 bash -c "cat < /dev/null > /dev/tcp/$WORKER_IP/$RPC_PORT" 2>/dev/null; then
  echo "FATAL: $WORKER_IP:$RPC_PORT not reachable." >&2
  echo "  ssh $WORKER_SSH sudo systemctl status rpc-server@$RPC_PORT" >&2
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
  # sudo, not plain journalctl. The admin account is in neither `adm` nor
  # `systemd-journal` on ANY node in this fleet, so an unprivileged journalctl
  # prints "-- No entries --" and a hint, on a healthy node and a broken one
  # alike. This is the one command that runs when the #26500 gate fails, and it
  # was silently returning nothing. Verified on nodes 2 and 3, 2026-08-23.
  ssh "$WORKER_SSH" "sudo journalctl -u rpc-server@$RPC_PORT -n 40 --no-pager" 2>/dev/null \
    || echo "(could not read worker journal)"
  exit 1
fi

echo "  healthy"
echo
echo "=== Layer assignment ==="
grep -iE 'assigned|RPC\[|buffer size|offloaded' "$LOG" | head -20

echo
echo "=== Generating (the real test -- graph compute across both nodes) ==="
# Two F21 defences, both learned the hard way -- this test reported FAIL on
# 2026-08-17 while the cluster was working perfectly:
#   1. enable_thinking=false. Qwen3 and relatives emit chain-of-thought into a
#      SEPARATE reasoning_content field. With thinking on, a modest max_tokens is
#      spent thinking and "content" comes back as an EMPTY STRING on a 200 OK.
#   2. A generous max_tokens, so a model that ignores (1) still reaches an answer.
RESP=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"In two sentences, explain why an organisation with sensitive records might run an AI model on its own hardware."}],
       "max_tokens":400,
       "chat_template_kwargs":{"enable_thinking":false}}')

# Distinguish "the cluster is broken" from "the model answered into a different
# field". Conflating them makes a PASSING cluster look like a FAILING one.
REPLY=$(printf '%s' "$RESP" | python3 -c '
import json, sys
try:
    m = json.load(sys.stdin)["choices"][0]["message"]
except Exception:
    sys.exit(0)
c = (m.get("content") or "").strip()
if c:
    print(c)
elif (m.get("reasoning_content") or "").strip():
    sys.stderr.write("EMPTY content but reasoning_content IS populated -- F21.\n"
                     "The cluster generated fine; the token budget was spent thinking.\n")
' 2>/tmp/smoke-extract.err)

if [ -z "$REPLY" ]; then
  echo "FAIL: no usable response."
  [ -s /tmp/smoke-extract.err ] && { echo "--- extraction diagnostic ---"; cat /tmp/smoke-extract.err; }
  # Only a graph-compute abort indicts the CLUSTER. Say which it was.
  if grep -qiE 'invalid data ptr|abort|SIGILL|malformed' "$LOG"; then
    echo "--- cluster-level error found in the server log ---"
    grep -iE 'invalid data ptr|abort|SIGILL|malformed' "$LOG" | tail -20
  else
    echo "--- NO cluster-level error in the server log. Generation itself: ---"
    grep -E 'prompt eval time|eval time|n_decoded' "$LOG" | tail -3
    echo "    => RPC sharding worked; this is a response-parsing failure, not #26500."
  fi
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

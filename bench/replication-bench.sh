#!/usr/bin/env bash
# THE measurement the architecture rests on: does replication deliver R x?
#
# Sharding buys capacity and never speed (measured: -39% prefill on localhost,
# roughly -49% generation across two real machines on a 100 Mb link). Replication
# is supposed to buy speed LINEARLY, because independent llama-servers share no
# hot path at all. That claim has never been tested on hardware. This tests it.
#
# It starts ONE INDEPENDENT llama-server per node -- no --rpc, no --tensor-split.
# That is the entire point: if you see either flag here, someone has confused
# replication with sharding.
#
# Usage:
#   ./bench/replication-bench.sh                          # all endpoints in nodes.env
#   ENGINE=/opt/llama.cpp ./bench/replication-bench.sh    # mainline instead of ik
#   MODEL=/opt/models/qwen3-4b-q4km.gguf ./bench/replication-bench.sh
set -uo pipefail

cd "$(dirname "$0")/.."
source provisioning/nodes.env

# ik_llama.cpp by default: prefill is ~79% of document wall-clock and ik is +52%
# there, a net +22% end-to-end (F27). Its CLI differs from mainline -- notably it
# has NO --no-webui -- so do not assume flags transfer.
ENGINE="${ENGINE:-/opt/ik_llama.cpp}"
MODEL="${MODEL:-/opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf}"
PORT="${PORT:-8080}"
CTX="${CTX:-16384}"
PARALLEL="${PARALLEL:-4}"          # 4, never 8 -- 8 is worse than 1 on MoE (F24)
PER_ENDPOINT="${PER_ENDPOINT:-$PARALLEL}"
MAX_TOKENS="${MAX_TOKENS:-200}"
LOAD_WAIT="${LOAD_WAIT:-1800}"     # 65 GB, single-core-serialised load (F3)

# Endpoints come from nodes.env. INFERENCE endpoints are deliberately a separate
# list from RPC endpoints -- they are not the same thing.
if [ -n "${INFERENCE_ENDPOINTS+x}" ] && [ "${#INFERENCE_ENDPOINTS[@]}" -gt 0 ]; then
  ENDPOINTS=("${INFERENCE_ENDPOINTS[@]}")
else
  ENDPOINTS=()
  for entry in "${NODES[@]}"; do
    set -- $entry
    ENDPOINTS+=("http://$2:$PORT")
  done
fi

echo "=== Replication benchmark ==="
echo "  engine    : $ENGINE"
echo "  model     : $MODEL"
echo "  endpoints : ${ENDPOINTS[*]}"
echo "  -t        : PHYSICAL cores per node (from nodes.env)"
echo "  --parallel: $PARALLEL   per-endpoint concurrency: $PER_ENDPOINT"
echo

# --- preflight: the model and engine must exist on EVERY node -------------------
# Under replication each node serves independently, so each needs its own full
# copy. A missing copy on one node would silently make this a single-node test.
FAIL=0
for entry in "${NODES[@]}"; do
  set -- $entry
  NAME=$1; IP=$2; TGT=$(node_target "$entry")
  SZ=$(ssh -o BatchMode=yes "$TGT" "stat -c %s '$MODEL' 2>/dev/null" 2>/dev/null || echo 0)
  BIN=$(ssh -o BatchMode=yes "$TGT" "test -x '$ENGINE/bin/llama-server' && echo yes" 2>/dev/null || echo no)
  printf '  %-8s model %14s bytes   engine %s\n' "$NAME" "${SZ:-0}" "$BIN"
  [ "${SZ:-0}" -gt 0 ] || { echo "    FATAL: $NAME has no model at $MODEL" >&2; FAIL=1; }
  [ "$BIN" = yes ]     || { echo "    FATAL: $NAME has no $ENGINE/bin/llama-server" >&2; FAIL=1; }
done
[ "$FAIL" -eq 0 ] || { echo; echo "Preflight failed. Distribute the model/engine first." >&2; exit 1; }
echo

# --- stop the RPC workers ------------------------------------------------------
# They hold no model, but they do hold RAM and cores, and this test must measure
# a node serving on its own.
echo "=== Stopping rpc-server on all nodes (replication uses no RPC) ==="
for entry in "${NODES[@]}"; do
  set -- $entry
  ssh -o BatchMode=yes "$(node_target "$entry")" "sudo systemctl stop rpc-server@${RPC_PORT} 2>/dev/null" || true
  echo "  $1 stopped"
done

cleanup() {
  echo
  echo "=== Cleanup: stopping llama-servers ==="
  for entry in "${NODES[@]}"; do
    set -- $entry
    ssh -o BatchMode=yes "$(node_target "$entry")" "pkill -f 'llama-server .*--por[t] $PORT'" 2>/dev/null || true
  done
  echo "  (rpc-server left stopped; restart with ./cluster/install-services.sh"
  echo "   or: sudo systemctl start rpc-server@${RPC_PORT})"
}
trap cleanup EXIT

# --- launch one independent server per node -----------------------------------
echo
echo "=== Launching an INDEPENDENT llama-server per node ==="
for entry in "${NODES[@]}"; do
  set -- $entry
  NAME=$1; IP=$2; CORES=${4:-4}; TGT=$(node_target "$entry")
  # TWO separate ssh calls, deliberately. Combining them puts both the pkill
  # pattern AND a literal "llama-server ... --port $PORT" into the SAME remote
  # shell's command line, so pkill -f matches its own shell and kills the launch
  # before it happens. (Cost me two dead shells to find. The [t] bracket stops
  # the pattern matching itself, but not a sibling command beside it.)
  ssh -o BatchMode=yes "$TGT" "pkill -f 'llama-server .*--por[t] $PORT'" 2>/dev/null || true
  ssh -o BatchMode=yes "$TGT" \
    "nohup $ENGINE/bin/llama-server \
       -m '$MODEL' -t $CORES -c $CTX --parallel $PARALLEL \
       --host 0.0.0.0 --port $PORT --no-warmup --jinja \
       > /tmp/llama-server-$PORT.log 2>&1 & echo started" >/dev/null
  echo "  $NAME launching with -t $CORES (physical cores)"
done

echo
echo "=== Waiting for all endpoints to become healthy (65 GB load is slow, F3) ==="
DEADLINE=$((SECONDS + LOAD_WAIT))
for ep in "${ENDPOINTS[@]}"; do
  printf '  %s ' "$ep"
  while true; do
    if curl -sf --max-time 5 "$ep/health" >/dev/null 2>&1; then echo "healthy"; break; fi
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
      echo "TIMEOUT"
      echo "  FATAL: $ep never became healthy. Check /tmp/llama-server-$PORT.log on that node." >&2
      exit 1
    fi
    sleep 5
  done
done

echo
echo "=== Driving load ==="
JOINED=$(IFS=,; echo "${ENDPOINTS[*]}")
python3 bench/replication_driver.py \
  --endpoints "$JOINED" \
  --per-endpoint "$PER_ENDPOINT" \
  --max-tokens "$MAX_TOKENS" \
  --json-out /tmp/replication-result.json
RC=$?

echo
echo "=== Server-reported timings (authoritative, per node) ==="
for entry in "${NODES[@]}"; do
  set -- $entry
  echo "  --- $1 ---"
  ssh -o BatchMode=yes "$(node_target "$entry")" "grep -E 'prompt eval time|eval time' /tmp/llama-server-$PORT.log | tail -4" 2>/dev/null \
    || echo "    (no timings found)"
done

echo
echo "Record the result in docs/measurements.md. Aggregate ~= R x single-node is"
echo "the claim the replication-first architecture depends on."
exit $RC

#!/usr/bin/env bash
# Install and start the rpc-server systemd unit on every node.
set -euo pipefail
cd "$(dirname "$0")/.."
source provisioning/nodes.env

# PHYSICAL cores, not nproc. Measured on node 1 (docs/measurements.md):
# generation is 26% SLOWER at -t 8 than -t 4 on a 4c/8t CPU, because generation
# is memory-bandwidth-bound (running at ~99% of STREAM) and SMT siblings
# contend for the same memory pipe. Prefill saturates at the physical core
# count too. nproc would have picked the worst of the five values tested.
CORE_CMD="lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l"

for entry in "${NODES[@]}"; do
  set -- $entry
  NAME=$1; IP=$2; CORES=${4:-}

  # Prefer the measured value from nodes.env; fall back to deriving it.
  if [ -z "$CORES" ]; then
    CORES=$(ssh "$IP" "$CORE_CMD")
    echo "  $NAME: cores not in nodes.env, derived $CORES -- record it there"
  fi

  scp -q cluster/rpc-server@.service "$IP:/tmp/"
  ssh "$IP" "sudo mv /tmp/rpc-server@.service /etc/systemd/system/ && \
             echo RPC_THREADS=$CORES | sudo tee /etc/default/rpc-server >/dev/null && \
             sudo systemctl daemon-reload && \
             sudo systemctl enable --now rpc-server@${RPC_PORT}"

  ACTUAL=$(ssh "$IP" "cat /etc/default/rpc-server")
  echo "  $NAME started ($ACTUAL, nproc=$(ssh "$IP" nproc))"
done

echo
echo "Verifying all endpoints reachable from the master:"
for entry in "${NODES[@]}"; do
  set -- $entry
  if timeout 3 bash -c "cat < /dev/null > /dev/tcp/$2/$RPC_PORT" 2>/dev/null; then
    echo "  $1 $2:$RPC_PORT open"
  else
    echo "  FATAL: $1 $2:$RPC_PORT unreachable" >&2
    echo "  check: ssh $2 journalctl -u rpc-server@$RPC_PORT -n 30" >&2
    exit 1
  fi
done

cat <<'EOF'

NEXT -- do not skip this (docs/FINDINGS.md F2):
  PR #26500 is an OPEN, UNMERGED upstream bug that breaks clusters with 2 or
  more RPC workers ("[create_node] invalid data ptr"), confirmed on a CPU-only
  multi-worker cluster running a sparse MoE model. It is fixed in NO released
  tag. Smoke-test TWO nodes before committing to seven and before fetching
  ~550 GB of weights.
EOF

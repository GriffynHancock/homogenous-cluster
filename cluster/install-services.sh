#!/usr/bin/env bash
# Install and start the per-node systemd units: rpc-server, and the liveness
# watchdog agent + timers.
#
# ONLY_NODES=node1,node2 restricts the run to named nodes (or IPs). The default
# is every node in nodes.env. It exists because provisioning one node must not
# require touching the others -- a node mid-benchmark is a node you do not
# restart services on.
set -euo pipefail
cd "$(dirname "$0")/.."
source provisioning/nodes.env

ONLY_NODES="${ONLY_NODES:-}"
wanted() {
  [ -z "$ONLY_NODES" ] && return 0
  case ",${ONLY_NODES}," in *",$1,"*|*",$2,"*) return 0 ;; esac
  return 1
}

# PHYSICAL cores, not nproc. Measured on node 1 (docs/measurements.md):
# generation is 26% SLOWER at -t 8 than -t 4 on a 4c/8t CPU, because generation
# is memory-bandwidth-bound (running at ~99% of STREAM) and SMT siblings
# contend for the same memory pipe. Prefill saturates at the physical core
# count too. nproc would have picked the worst of the five values tested.
CORE_CMD="lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l"

for entry in "${NODES[@]}"; do
  set -- $entry
  NAME=$1; IP=$2; CORES=${4:-}
  wanted "$NAME" "$IP" || { echo "  $NAME skipped (ONLY_NODES=$ONLY_NODES)"; continue; }

  # Per-node admin login (nodes.env 5th field; default = coordinator's own).
  # A bare `ssh "$IP"` uses the CALLER's name -- fine while every node happened
  # to be `debian1`, wrong the moment one is not. See FINDINGS F53.
  TGT=$(node_target "$entry")
  NUSER=$(node_user "$entry")

  # Prefer the measured value from nodes.env; fall back to deriving it.
  if [ -z "$CORES" ]; then
    CORES=$(ssh "$TGT" "$CORE_CMD")
    echo "  $NAME: cores not in nodes.env, derived $CORES -- record it there"
  fi

  scp -q cluster/rpc-server@.service "$TGT:/tmp/"
  ssh "$TGT" "sudo mv /tmp/rpc-server@.service /etc/systemd/system/ && \
             echo RPC_THREADS=$CORES | sudo tee /etc/default/rpc-server >/dev/null && \
             sudo systemctl daemon-reload && \
             sudo systemctl enable --now rpc-server@${RPC_PORT}"

  ACTUAL=$(ssh "$TGT" "cat /etc/default/rpc-server")
  echo "  $NAME started ($ACTUAL, nproc=$(ssh "$TGT" nproc))"

  # -------------------------------------------------------------------------
  # llama-server@.service: install the unit and the ONE per-node thing in it.
  #
  # `User=` is the only setting in that unit that differs per machine, and
  # systemd does NOT expand environment variables in `User=` -- only ExecStart-
  # family settings get ${VAR}. So the EnvironmentFile that carries every other
  # per-node value cannot carry this one. The options were: generate the unit
  # per node (the shipped file then never matches what is installed, and a
  # `diff` check stops meaning anything), or a drop-in. Drop-in wins: the
  # tracked unit stays byte-identical everywhere, the per-node part is one
  # line in one file, and reverting is deleting that file.
  #
  # NOT enabled or started here. Starting it needs /etc/default/llama-server
  # and a model on disk, both of which are separate steps, and this script is
  # run against nodes that are already serving -- an `enable --now` here would
  # bounce a live inference server.
  # -------------------------------------------------------------------------
  scp -q cluster/llama-server@.service "$TGT:/tmp/"
  # Drop-in FIRST, unit SECOND. The tracked unit's `User=` is a placeholder that
  # does not resolve (see the unit file), so a chain that replaced the unit and
  # then failed to write the drop-in would leave the node one restart away from
  # status=217/USER. In this order the incomplete state is always harmless.
  ssh "$TGT" "printf '# Written by cluster/install-services.sh -- per-node admin login.\n# systemd cannot expand a variable in User=, so it cannot live in\n# /etc/default/llama-server with the rest. Delete this file to revert.\n[Service]\nUser=${NUSER}\n' \
               | sudo install -D -m 0644 -o root -g root /dev/stdin /etc/systemd/system/llama-server@.service.d/10-node-user.conf && \
             sudo install -D -m 0644 -o root -g root /tmp/llama-server@.service /etc/systemd/system/llama-server@.service && \
             rm -f /tmp/llama-server@.service && \
             sudo systemctl daemon-reload"
  EFFECTIVE=$(ssh "$TGT" "systemctl show -p User --value llama-server@8080")
  if [ "$EFFECTIVE" != "$NUSER" ]; then
    echo "  FATAL: $NAME llama-server@ resolves to User='$EFFECTIVE', expected '$NUSER'" >&2
    exit 1
  fi
  echo "  $NAME llama-server@ unit installed, User=$EFFECTIVE (not started)"
done

# ---------------------------------------------------------------------------
# The liveness watchdog. Until 2026-08-18 `llama-watchdog@.service` and
# `.timer` existed ONLY in /etc/systemd/system on node 1 and were untracked in
# git, so the F36/F39 fix could not reach node 2 or any future node -- a fix
# that cannot be provisioned is a fix for one machine.
#
# Delegated to install-watchdog.sh because it also creates a restricted SSH
# credential, which deserves its own file and its own justification.
# ---------------------------------------------------------------------------
echo
echo "Installing the liveness watchdog (agent, restricted key, in-band timer):"
./cluster/install-watchdog.sh ${ONLY_NODES:+--nodes "$ONLY_NODES"}

echo
echo "Verifying all endpoints reachable from the master:"
for entry in "${NODES[@]}"; do
  set -- $entry
  wanted "$1" "$2" || continue
  if timeout 3 bash -c "cat < /dev/null > /dev/tcp/$2/$RPC_PORT" 2>/dev/null; then
    echo "  $1 $2:$RPC_PORT open"
  else
    echo "  FATAL: $1 $2:$RPC_PORT unreachable" >&2
    # sudo: the admin account is in neither adm nor systemd-journal on any node,
    # so a plain journalctl here prints "-- No entries --" and nothing else.
    echo "  check: ssh $(node_target "$entry") sudo journalctl -u rpc-server@$RPC_PORT -n 30" >&2
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

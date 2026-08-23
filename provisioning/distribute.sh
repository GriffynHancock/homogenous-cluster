#!/usr/bin/env bash
# Push the master's llama.cpp build to every worker and assert they match.
#
# Three ways a fleet can be subtly wrong, in increasing order of how long it
# takes to work out what happened:
#   1. version mismatch  -> rejected loudly at the RPC handshake. Easy.
#   2. libc mismatch     -> loader error on the worker. Medium.
#   3. ISA mismatch      -> SIGILL partway into a graph, AFTER a successful
#                           handshake and model load. Brutal. See F8.
# This script checks all three before it will report success.
set -euo pipefail

cd "$(dirname "$0")/.."
source provisioning/nodes.env

# Which engine prefix to ship. Both /opt/llama.cpp (mainline) and
# /opt/ik_llama.cpp (the fork, +52% prefill per F27) are built side by side and
# must BOTH reach every node under the replicated topology -- the fork was on the
# coordinator only until 2026-08-17, so a "distributed" fleet could not actually
# run the build the document workload is supposed to use.
#
#   ./provisioning/distribute.sh                     # mainline (default)
#   ./provisioning/distribute.sh /opt/ik_llama.cpp   # the fork
#
# NEVER mix engines within one RPC shard group -- the protocols differ. Under
# replication the nodes are independent, so having both installed is safe.
SRC="${1:-/opt/llama.cpp}"
[ -d "$SRC/bin" ] || { echo "FATAL: no such engine prefix: $SRC/bin" >&2; exit 1; }

# ik_llama.cpp is a fork with no upstream b-tag, so it ships a COMMIT but no
# VERSION. Synthesise one rather than failing: the assertion that matters is
# that master and worker agree, not that the string looks like a release tag.
if [ -f "$SRC/VERSION" ]; then
  VERSION=$(cat "$SRC/VERSION")
else
  VERSION="$(basename "$SRC")-$(cut -d' ' -f1 "$SRC/COMMIT" 2>/dev/null || echo unknown)"
  echo "  no VERSION in $SRC -- using derived '$VERSION'"
fi
COMMIT=$(cat "$SRC/COMMIT" 2>/dev/null || echo unknown)
MASTER_LIBC=$(apt-cache policy libc6 | awk '/Installed:/{print $2}')

# The ISA the binaries were actually compiled for. Anything the master's build
# uses that a worker lacks is a latent SIGILL.
MASTER_FLAGS=$(grep -oE '\bavx512[a-z_]*|\bavx2\b|\bfma\b|\bf16c\b' /proc/cpuinfo \
               | sort -u | tr '\n' ' ')

echo "Distributing llama.cpp $VERSION ($COMMIT)"
echo "  built against libc6 $MASTER_LIBC"
echo "  master ISA: $MASTER_FLAGS"
echo

# The target list, computed ONCE and printed, because the empty case is a
# silent no-op that looks like success: this used to `exit 0` with a one-line
# message and a caller who had just added a node read that as "done". Printing
# the resolved user@ip for every target makes both failure modes visible --
# an empty list, and a target whose login name is wrong.
TARGETS=("${NODES[@]:1}")
if [ "${#TARGETS[@]}" -eq 0 ]; then
  cat >&2 <<'EOF'
NOTHING TO DISTRIBUTE -- provisioning/nodes.env lists only the master.

This is a NO-OP, not a success. If you just added a node, its line is not in
the NODES=() array (check for a stray '#', or that you edited a worktree copy
rather than the one this script sourced).
EOF
  exit 0
fi

echo "Targets (${#TARGETS[@]}):"
for entry in "${TARGETS[@]}"; do
  # shellcheck disable=SC2086
  set -- $entry
  echo "  $1  $(node_target "$entry")"
done
echo

for entry in "${TARGETS[@]}"; do
  # shellcheck disable=SC2086
  set -- $entry
  NAME=$1; IP=$2
  # The admin login is per-node (5th field, default = coordinator's own login).
  # A bare `ssh "$IP"` silently uses the CALLER's name, which is correct for
  # nodes 1-2 only by coincidence -- see nodes.env and FINDINGS F53.
  TGT=$(node_target "$entry")

  REMOTE_LIBC=$(ssh "$TGT" "apt-cache policy libc6 | awk '/Installed:/{print \$2}'")
  if [ "$REMOTE_LIBC" != "$MASTER_LIBC" ]; then
    echo "FATAL: $NAME libc6 $REMOTE_LIBC != master $MASTER_LIBC" >&2
    echo "Binaries built on the master may not run. Align the point release." >&2
    exit 1
  fi

  # ISA check. distribute.sh is the only place this can be caught cheaply --
  # the RPC handshake compares versions, not instruction sets.
  REMOTE_FLAGS=$(ssh "$TGT" "grep -oE '\bavx512[a-z_]*|\bavx2\b|\bfma\b|\bf16c\b' /proc/cpuinfo | sort -u | tr '\n' ' '")
  for flag in $MASTER_FLAGS; do
    case " $REMOTE_FLAGS " in
      *" $flag "*) ;;
      *) echo "FATAL: $NAME lacks '$flag' which the master has." >&2
         echo "  master: $MASTER_FLAGS" >&2
         echo "  $NAME:  $REMOTE_FLAGS" >&2
         echo "Rebuild with a lower LLAMA_MARCH, or exclude this node." >&2
         exit 1 ;;
    esac
  done

  # Ship the shared libraries too. ggml builds as .so and llama-server is an
  # 18 KB stub -- binaries alone are useless. -a preserves the symlink chain
  # (libggml.so -> .so.0 -> .so.0.19.0) and the rpc-server -> ggml-rpc-server
  # compatibility symlink.
  ssh "$TGT" "mkdir -p $SRC/bin"
  rsync -az --delete "$SRC/bin/" "$TGT:$SRC/bin/"
  # Write VERSION from the (possibly derived) value rather than copying a file
  # that may not exist. COMMIT is copied when present.
  ssh "$TGT" "printf '%s\n' '$VERSION' | sudo -n tee $SRC/VERSION >/dev/null 2>&1 || printf '%s\n' '$VERSION' > $SRC/VERSION"
  [ -f "$SRC/COMMIT" ] && scp -q "$SRC/COMMIT" "$TGT:$SRC/"

  REMOTE_VERSION=$(ssh "$TGT" "cat $SRC/VERSION")
  if [ "$REMOTE_VERSION" != "$VERSION" ]; then
    echo "FATAL: $NAME version $REMOTE_VERSION != $VERSION" >&2
    exit 1
  fi

  # Actually execute it. Catches the relocatable-binary problem (F13) that the
  # version and libc checks cannot see.
  ssh "$TGT" "$SRC/bin/rpc-server --help >/dev/null 2>&1" \
    || { echo "FATAL: rpc-server will not execute on $NAME." >&2
         echo "Check for missing .so files: ssh $TGT ldd $SRC/bin/ggml-rpc-server" >&2
         exit 1; }

  echo "  $NAME ok"
done

echo
echo "All nodes at $VERSION ($COMMIT)"

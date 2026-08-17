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

SRC=/opt/llama.cpp
VERSION=$(cat "$SRC/VERSION")
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

if [ "${#NODES[@]}" -le 1 ]; then
  echo "Only the master is in nodes.env -- nothing to distribute."
  exit 0
fi

for entry in "${NODES[@]:1}"; do
  set -- $entry
  NAME=$1; IP=$2

  REMOTE_LIBC=$(ssh "$IP" "apt-cache policy libc6 | awk '/Installed:/{print \$2}'")
  if [ "$REMOTE_LIBC" != "$MASTER_LIBC" ]; then
    echo "FATAL: $NAME libc6 $REMOTE_LIBC != master $MASTER_LIBC" >&2
    echo "Binaries built on the master may not run. Align the point release." >&2
    exit 1
  fi

  # ISA check. distribute.sh is the only place this can be caught cheaply --
  # the RPC handshake compares versions, not instruction sets.
  REMOTE_FLAGS=$(ssh "$IP" "grep -oE '\bavx512[a-z_]*|\bavx2\b|\bfma\b|\bf16c\b' /proc/cpuinfo | sort -u | tr '\n' ' '")
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
  ssh "$IP" "mkdir -p $SRC/bin"
  rsync -az --delete "$SRC/bin/" "$IP:$SRC/bin/"
  scp -q "$SRC/VERSION" "$SRC/COMMIT" "$IP:$SRC/"

  REMOTE_VERSION=$(ssh "$IP" "cat $SRC/VERSION")
  if [ "$REMOTE_VERSION" != "$VERSION" ]; then
    echo "FATAL: $NAME version $REMOTE_VERSION != $VERSION" >&2
    exit 1
  fi

  # Actually execute it. Catches the relocatable-binary problem (F13) that the
  # version and libc checks cannot see.
  ssh "$IP" "$SRC/bin/rpc-server --help >/dev/null 2>&1" \
    || { echo "FATAL: rpc-server will not execute on $NAME." >&2
         echo "Check for missing .so files: ssh $IP ldd $SRC/bin/ggml-rpc-server" >&2
         exit 1; }

  echo "  $NAME ok"
done

echo
echo "All nodes at $VERSION ($COMMIT)"

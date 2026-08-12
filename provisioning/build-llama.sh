#!/usr/bin/env bash
# Build llama.cpp once on the master. NEVER run this on a worker -- binaries are
# distributed by distribute.sh so every node runs byte-identical builds. A
# version mismatch is rejected at the RPC handshake; an ISA mismatch is not, and
# fails much later and much less legibly (see "Why not GGML_NATIVE" below).
set -euo pipefail

LLAMA_TAG="${LLAMA_TAG:?set LLAMA_TAG to a pinned release tag, e.g. b10369}"
# Source lives *under* $DEST rather than in a sibling /opt/llama.cpp-src, so a
# single `chown` on /opt/llama.cpp covers both and the build needs no root at
# all once bootstrap.sh has made the directory.
SRC="${SRC:-/opt/llama.cpp/src}"
DEST="${DEST:-/opt/llama.cpp}"

# --- Why not GGML_NATIVE=ON --------------------------------------------------
# GGML_NATIVE bakes `-march=native` in, targeting the *build* machine's exact
# ISA. On a uniform fleet that is free performance. On a salvaged fleet it is a
# trap: a node with an older CPU accepts the binary, passes the RPC version
# handshake, loads the model, and then dies with SIGILL / "Illegal instruction"
# partway into a graph -- with nothing in the logs pointing at the real cause.
#
# node 1 is a Xeon E5-1620 v4 (Broadwell): AVX2 + FMA + F16C, and NO AVX-512.
# `haswell` is the common denominator that keeps AVX2/FMA/F16C while remaining
# safe on anything from 2013 onward, which covers the whole plausible fleet.
#
# Override deliberately, only once every node's ISA is confirmed identical:
#   LLAMA_MARCH=native ./build-llama.sh
LLAMA_MARCH="${LLAMA_MARCH:-haswell}"

if [ ! -w "$(dirname "$DEST")" ] && [ ! -d "$DEST" ]; then
  sudo mkdir -p "$SRC" "$DEST"
  sudo chown -R "$USER" "$DEST"
else
  mkdir -p "$SRC" "$DEST/bin"
fi

if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/ggml-org/llama.cpp "$SRC"
fi

cd "$SRC"
git fetch --tags --force
git checkout --detach "$LLAMA_TAG"
git rev-parse HEAD > /tmp/llama-build-sha

echo "==> Building $LLAMA_TAG (-march=$LLAMA_MARCH) with $(nproc) jobs"

# A stale CMake cache silently keeps the *previous* tag's flags, which is how
# you end up with a fleet-wide ISA or version mismatch you cannot explain.
# Wipe only when the tag actually changed, so re-running to fix a packaging
# step does not cost a 20-minute rebuild.
if [ ! -f build/.built-tag ] || [ "$(cat build/.built-tag)" != "$LLAMA_TAG" ]; then
  rm -rf build
fi

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_RPC=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_BACKEND_DL=OFF \
  -DCMAKE_C_FLAGS="-march=$LLAMA_MARCH -mtune=native" \
  -DCMAKE_CXX_FLAGS="-march=$LLAMA_MARCH -mtune=native" \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
  -DCMAKE_INSTALL_RPATH='$ORIGIN' \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON

cmake --build build --config Release -j"$(nproc)"
echo "$LLAMA_TAG" > build/.built-tag

mkdir -p "$DEST/bin"
for b in llama-cli llama-server llama-bench llama-batched-bench \
         ggml-rpc-server rpc-server; do
  if [ -f "build/bin/$b" ]; then
    cp -a "build/bin/$b" "$DEST/bin/"
  fi
done

# Upstream renamed the RPC server target `rpc-server` -> `ggml-rpc-server`
# (tools/rpc/CMakeLists.txt, present in b10369). Every script and systemd unit
# in this repo -- and every guide on the internet -- says `rpc-server`. Keep a
# symlink so both names work, rather than chasing the rename through the fleet.
if [ -f "$DEST/bin/ggml-rpc-server" ] && [ ! -e "$DEST/bin/rpc-server" ]; then
  ln -sf ggml-rpc-server "$DEST/bin/rpc-server"
fi

# ggml builds as shared libraries by default, so the binaries are NOT
# self-contained. distribute.sh must ship these too or workers die with a
# loader error that looks nothing like a version mismatch.
cp -a build/bin/*.so* "$DEST/bin/" 2>/dev/null || true

for required in llama-server llama-bench rpc-server; do
  [ -e "$DEST/bin/$required" ] || { echo "FATAL: $required missing" >&2; exit 1; }
done

# Assert the binaries do NOT depend on the source tree. Default CMake RPATH
# points at build/bin, which resolves fine on the build machine and fails on
# every worker -- silently passing the one place you would test it. Built with
# RPATH=$ORIGIN so libs resolve next to the binary.
if ldd "$DEST/bin/ggml-rpc-server" | grep -q "$SRC"; then
  echo "FATAL: binaries still reference the build tree ($SRC)." >&2
  echo "They will not run on a worker. Check CMAKE_INSTALL_RPATH." >&2
  ldd "$DEST/bin/ggml-rpc-server" | grep "$SRC" >&2
  exit 1
fi
echo "RPATH check passed: no build-tree dependencies."

# The VERSION file is what distribute.sh asserts on. Record the resolved commit
# too -- a tag can in principle be moved, a SHA cannot.
{
  echo "$LLAMA_TAG"
} > "$DEST/VERSION"
cp /tmp/llama-build-sha "$DEST/COMMIT"

echo
"$DEST/bin/llama-cli" --version
echo
echo "Built $LLAMA_TAG ($(cat "$DEST/COMMIT")) -> $DEST/bin"
ls -la "$DEST/bin"

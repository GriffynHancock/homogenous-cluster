#!/usr/bin/env bash
# Build ik_llama.cpp alongside mainline for A/B testing.
#
# WHY: prefill is compute-bound on this hardware (4 cores, no AVX-512) and is
# 79% of document wall-clock. Every other software lever has been measured and
# eliminated -- -t saturates at 4, -ub does nothing, uncore/EPB are already
# maxed. ik_llama.cpp's whole premise is better CPU kernels and quant types,
# which is exactly the remaining opportunity.
#
# Installs to a SEPARATE prefix so both builds coexist and can be A/B'd on the
# same model file without rebuilding either.
set -euo pipefail

SRC="${SRC:-/opt/ik_llama.cpp/src}"
DEST="${DEST:-/opt/ik_llama.cpp}"
LLAMA_MARCH="${LLAMA_MARCH:-haswell}"

mkdir -p "$SRC" "$DEST/bin"
if [ ! -d "$SRC/.git" ]; then
  git clone --filter=blob:none https://github.com/ikawrakow/ik_llama.cpp "$SRC"
fi
cd "$SRC"
git fetch --all
git log --oneline -1 > "$DEST/COMMIT"

# Same flags as mainline so the comparison is fair, plus the same $ORIGIN RPATH
# fix -- the default points into the build tree and is not relocatable (F13).
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_RPC=ON \
  -DGGML_NATIVE=OFF \
  -DCMAKE_C_FLAGS="-march=$LLAMA_MARCH -mtune=native" \
  -DCMAKE_CXX_FLAGS="-march=$LLAMA_MARCH -mtune=native" \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
  -DCMAKE_INSTALL_RPATH='$ORIGIN' \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF

cmake --build build --config Release -j"$(nproc)"

for b in llama-cli llama-server llama-bench llama-batched-bench rpc-server ggml-rpc-server; do
  [ -f "build/bin/$b" ] && cp -a "build/bin/$b" "$DEST/bin/"
done
# ik_llama.cpp scatters its shared libs across build/src, build/ggml/src and
# build/examples/mtmd rather than collecting them in build/bin the way mainline
# does. Copying only build/bin/*.so* silently produces binaries that cannot
# start ("cannot open shared object file: libllama.so").
find build -name '*.so*' -exec cp -a {} "$DEST/bin/" \;
echo "ik_llama.cpp built -> $DEST/bin  ($(cat "$DEST/COMMIT"))"
ls "$DEST/bin"

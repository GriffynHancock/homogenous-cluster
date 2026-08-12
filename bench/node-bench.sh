#!/usr/bin/env bash
# Single-node baseline: throughput via llama-bench, and time-to-first-token
# measured separately against llama-server.
#
# TTFT is a SEPARATE metric from tok/s and matters more for document workloads.
# Prefill is compute-bound; generation is bandwidth-bound. llama-bench does not
# report TTFT at all, hence the second half of this script.
#
# On node 1 (4-core Broadwell, no AVX-512) TTFT is the number at risk, not
# tok/s. The GPU-revisit threshold is TTFT > 90 s at ~2000 tokens.
set -euo pipefail

BIN="${BIN:-/opt/llama.cpp/bin}"
MODEL="${MODEL:-/opt/models/qwen3-4b-q4km.gguf}"
THREADS="${THREADS:-$(nproc)}"
PORT="${PORT:-8080}"
REPS="${REPS:-3}"
CTX="${CTX:-8192}"

[ -f "$MODEL" ] || { echo "FATAL: model not found: $MODEL" >&2; exit 1; }

echo "Model:   $MODEL"
echo "Threads: $THREADS"
echo

echo "=== Throughput: prefill and generation ==="
"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512,2048 -n 128 -r "$REPS"

echo
echo "=== Time to first token ==="
"$BIN/llama-server" -m "$MODEL" -t "$THREADS" -c "$CTX" \
  --port "$PORT" --host 127.0.0.1 --no-webui >/tmp/node-bench-server.log 2>&1 &
SRV_PID=$!
trap 'kill $SRV_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
  || { echo "FATAL: server never became healthy" >&2; tail -20 /tmp/node-bench-server.log >&2; exit 1; }

# Build the request body in Python and write it to a file. The plan's original
# version interpolated the prompt into a shell string inside a python -c inside
# a $(...) -- any apostrophe in the prompt would break it, and the failure looks
# like a model problem rather than a quoting problem.
python3 - "$CTX" <<'PY' > /tmp/ttft-request.json
import json, sys
# ~2000 tokens, representative of one map-reduce chunk of a real document.
prompt = "The quick brown fox jumps over the lazy dog. " * 220
json.dump({
    "messages": [{"role": "user", "content": "Summarise this:\n\n" + prompt}],
    "max_tokens": 64,
    "stream": True,
}, sys.stdout)
PY

echo "Prompt tokens (server-reported) appear in /tmp/node-bench-server.log"
for i in $(seq 1 "$REPS"); do
  curl -s -o /dev/null \
    -w "run $i: TTFT %{time_starttransfer}s   total %{time_total}s\n" \
    -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    --data @/tmp/ttft-request.json
done

echo
echo "--- server-reported prompt eval (authoritative token counts) ---"
grep -E 'prompt eval|eval time|n_prompt_tokens' /tmp/node-bench-server.log | tail -10 || true

kill $SRV_PID 2>/dev/null || true

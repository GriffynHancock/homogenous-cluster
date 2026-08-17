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
# DO NOT use curl's %{time_starttransfer} here. It reports when the HTTP
# *headers* arrive, which llama-server sends immediately on accepting a
# streaming request -- ~15 ms, totally insensitive to prefill. Measured on
# node 1 it reported 0.015 s against a real TTFT of 89 s. See FINDINGS F17.
#
# Instead: read the SSE stream and stamp the first chunk that carries content.
#
# Each run also gets a UNIQUE prompt. Identical prompts hit llama-server's
# prompt cache and report `prompt eval time = ... / 1 tokens` -- measuring the
# cache rather than the model. Runs 2 and 3 of the original script were
# meaningless for exactly this reason.
for i in $(seq 1 "$REPS"); do
  python3 - "$PORT" "$i" <<'PY'
import json, sys, time, urllib.request

port, run = sys.argv[1], int(sys.argv[2])
# ~2000 tokens, representative of one map-reduce chunk of a real document.
# The run-specific prefix defeats the prompt cache.
prompt = f"Document {run}. " + "The quick brown fox jumps over the lazy dog. " * 220
body = json.dumps({
    "messages": [{"role": "user", "content": "Summarise this:\n\n" + prompt}],
    "max_tokens": 64,
    "stream": True,
}).encode()

req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=body, headers={"Content-Type": "application/json"})

t0 = time.monotonic()
ttft = None
ntok = 0
with urllib.request.urlopen(req) as r:
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            delta = json.loads(payload)["choices"][0].get("delta", {})
        except (ValueError, KeyError, IndexError):
            continue
        # Only content counts. The first chunk is often a role marker with no
        # text, and stamping that would understate TTFT.
        if delta.get("content"):
            ntok += 1
            if ttft is None:
                ttft = time.monotonic() - t0
total = time.monotonic() - t0
gen = (ntok - 1) / (total - ttft) if ttft and total > ttft and ntok > 1 else float("nan")
print(f"run {run}: TTFT {ttft:.2f}s   total {total:.2f}s   "
      f"gen {gen:.2f} tok/s   ({ntok} chunks)")
PY
done

echo
echo "--- server-reported timings (authoritative; cross-check the above) ---"
grep -E 'prompt eval time|eval time' /tmp/node-bench-server.log | tail -8 || true
cat <<'EOF'

Read `prompt eval time = X ms / N tokens` as the ground truth for TTFT.
If N is 1 on a run, that run hit the prompt cache and is NOT a valid sample.
EOF

kill $SRV_PID 2>/dev/null || true

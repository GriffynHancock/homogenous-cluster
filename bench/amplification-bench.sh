#!/usr/bin/env bash
# Does MAP-REDUCE amplify fabrication relative to SINGLE-PASS, on our documents?
#
# The question STATUS.md section 4 reframed the faithfulness evaluation around.
# Reproducing a hallucination leaderboard needs ~260 documents per model and is
# not worth cluster-nights. The pipeline question is answerable on a few dozen
# units BECAUSE IT IS PAIRED: same text, same model, same server, two
# architectures, so document-level variance cancels instead of swamping the
# effect.
#
# The exposure being measured is real and has been observed once already (F42):
# a reduce step asserted a death year that appears in NEITHER the source NOR any
# of the five chunk summaries. A chunk summary is source material for the reduce
# step, where invention is indistinguishable from genuine content -- so an error
# does not merely survive, it is laundered.
#
# WHAT YOU WILL NOT GET FROM THIS, stated up front:
#   * It is NOT a comparison of whole documents. At n_ctx_slot=8192 exactly ONE
#     of the 17 corpus documents fits a single-pass call, so the unit is a
#     paragraph-aligned SECTION that provably fits one slot and still produces
#     >=2 chunks. Both arms get byte-identical text. Whatever amplification this
#     measures is therefore a LOWER BOUND on the full-document case.
#   * It is NOT a speed comparison. Consecutive arms share the server's prompt
#     cache, which deflates the second arm's prefill. Faithfulness is unaffected
#     (the same prompt yields the same logits either way); wall-clock is not.
#   * It is NOT a finding until a human has adjudicated the flagged claims AND a
#     sample of the passing ones. `score` writes both.
#
# THE PHASES ARE SEPARATE ON PURPOSE. `plan` and `score` are CPU-bound on the
# coordinator, and F44 measured a CPU-bound sidecar starving llama-server on a
# 4-core node even when niced. Never overlap them with `run`.
#
#   ./bench/amplification-bench.sh estimate
#   ./bench/amplification-bench.sh plan     http://10.10.0.39:8080
#   ./bench/amplification-bench.sh run      http://10.10.0.39:8080
#   ./bench/amplification-bench.sh score
#   ./bench/amplification-bench.sh analyse
#
# RESTART llama-server BEFORE `run`, on an otherwise idle node. The prompt cache
# is shared across processes and a stale cache once turned a 1339-token prefill
# into `prompt_n=5` and a third of the real wall-clock (see
# bench/chunk_size_driver.py). It cannot corrupt the faithfulness result, but a
# run whose timings are fiction is harder to defend than one whose are not.
#
# Does NOT hardcode a node. Point it at whichever llama-server is free; NEVER at
# one serving real work.
set -euo pipefail

PHASE="${1:?Usage: $0 <estimate|plan|run|score|analyse> [endpoint]}"
ENDPOINT="${2:-}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${DB:-/opt/missing-link/jobs.sqlite}"
OUT_DIR="${OUT_DIR:-$ROOT/bench/out/amplification}"
PER_DOC="${PER_DOC:-3}"
NODES="${NODES:-1}"
KIND="${KIND:-summarise}"
ARMS="${ARMS:-single_pass map_reduce_4096}"
# Optional: enables F39's CPU watchdog, the only signal that separates a BUSY
# llama-server from a WEDGED one. Without it the driver degrades to finite
# per-request timeouts rather than guessing.
NODE_SSH_HOST="${NODE_SSH_HOST:-}"
UNIT="${UNIT:-llama-server@8080.service}"
LIMIT="${LIMIT:-}"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/missing-link/.venv/bin/python" ]; then
    PYTHON="$ROOT/missing-link/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

DRIVER="$ROOT/bench/amplification_driver.py"
COMMON=(--out "$OUT_DIR" --db "$DB")

case "$PHASE" in
  estimate)
    exec "$PYTHON" "$DRIVER" "${COMMON[@]}" estimate --sections "${SECTIONS:-51}"
    ;;

  plan)
    # plan uses the TOKENISER, not inference: POST /tokenize runs on the HTTP
    # thread and constructs no task, so it takes no inference slot and is safe
    # against a server that is otherwise busy. Verified in the server source,
    # not assumed -- see ServerTokenizer in amplification_driver.py.
    : "${ENDPOINT:?plan needs an endpoint (it uses the tokeniser only)}"
    echo "Planning against $ENDPOINT (tokeniser only, no generation)."
    echo "This is a few minutes of single-threaded CPU on the coordinator."
    exec nice -n 10 "$PYTHON" "$DRIVER" "${COMMON[@]}" plan \
      --endpoint "$ENDPOINT" --per-doc "$PER_DOC" --nodes "$NODES" \
      --check-whole --arms $ARMS
    ;;

  run)
    : "${ENDPOINT:?run needs an endpoint}"

    if [ ! -f "$OUT_DIR/manifest.json" ]; then
      echo "No manifest at $OUT_DIR/manifest.json -- run the plan phase first." >&2
      echo "The manifest IS the pre-registration: it records which sections were" >&2
      echo "chosen and why the rest were rejected, before any output exists to" >&2
      echo "choose around." >&2
      exit 2
    fi
    echo "Have you restarted llama-server on that node? A warm prompt cache"
    echo "makes the timings fiction (it will not affect the faithfulness result)."
    ARGS=(run --endpoint "$ENDPOINT" --kind "$KIND" --arms $ARMS)
    [ -n "$LIMIT" ] && ARGS+=(--limit "$LIMIT")
    [ -n "$NODE_SSH_HOST" ] && ARGS+=(--node-ssh-host "$NODE_SSH_HOST" --unit "$UNIT")
    exec "$PYTHON" "$DRIVER" "${COMMON[@]}" "${ARGS[@]}"
    ;;

  score)
    # No cluster time. Runs the deterministic cascade (tiers 1 and 2, classifier
    # OFF -- F41) over whatever `run` has produced so far, which makes it safe to
    # run against a partially complete run.
    exec nice -n 10 "$PYTHON" "$DRIVER" "${COMMON[@]}" score ${FULL_LEDGERS:+--full-ledgers}
    ;;

  analyse)
    exec "$PYTHON" "$DRIVER" "${COMMON[@]}" analyse --arms $ARMS
    ;;

  *)
    echo "Unknown phase '$PHASE'. Use: estimate | plan | run | score | analyse" >&2
    exit 2
    ;;
esac

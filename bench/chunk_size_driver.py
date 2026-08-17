#!/usr/bin/env python3
"""Driver for bench/chunk-size-bench.sh.

Runs the REAL map-reduce pipeline (missing_link.worker's own chunk_document /
build_prompt / build_reduce_prompt / LlamaClient / extract_content -- not a
reimplementation) against a real document, sweeping CHUNK_TOKENS, and reports
END-TO-END wall-clock per size decomposed into total prefill time, total
generation time, and number of calls.

Why import the real module instead of copying its logic: chunk_document's
chunk_tokens/overlap_tokens are bound as DEFAULT ARGUMENTS at function
definition time, so monkeypatching worker.CHUNK_TOKENS after import would NOT
change what summarise() actually does. The only faithful way to vary chunk
size is to call chunk_document(..., chunk_tokens=N, overlap_tokens=M)
explicitly, exactly as this script does -- and everything else (prompts,
reasoning kwargs, truncation/empty-completion guards, timings extraction) is
untouched, so a result here is a result about the real pipeline.

TTFT/timing trap (F17): this NEVER uses curl -w %{time_starttransfer}. It reads
the `timings` object llama-server returns on its own OpenAI-compatible
response body (prompt_n, prompt_ms, predicted_n, predicted_ms) via
LlamaClient.timings_log, which worker.py already populates BEFORE
extract_content can raise -- so even a failed call leaves real prefill
evidence behind.

Cache trap (F17): not applicable here in the way node-bench.sh guards against
it -- every chunk is a different slice of the source document, so no two
requests in a sweep are byte-identical, and no request is repeated.

BUT THE SERVER'S PROMPT CACHE IS SHARED ACROSS RUNS, and that DID contaminate a
run (2026-08-18): a reproducer had just sent these exact chunk prompts, so the
sweep's first request came back `prompt_n=5` -- five tokens of prefill instead of
1339 -- and its wall clock was a third of the truth. **Restart llama-server before
a sweep**, or the small chunk sizes silently measure the cache.

INVISIBLE-WORK TRAP (F40), learned the hard way: the first run of this script
blocked for ~48 minutes against a backend that had fatal-errored, and printed
nothing at all while it did. Three defects combined:

  1. LlamaClient's DEFAULT_TIMEOUT_S is 3600 -- correct for the worker, where a
     real chunk can legitimately take many minutes, but it means a dead server
     costs an hour of silence per request.
  2. Nothing checked the endpoint between requests, so a server that died during
     chunk 4 would have been hammered for chunks 5..26 with no complaint.
  3. A failure aborted only the CURRENT chunk size and moved to the next one, so
     a dead backend produces five silent failures rather than one loud one.

All three are fixed below: a FINITE per-request timeout sized from the request
itself, a two-strike /health gate between requests, and a dead backend aborts the
WHOLE sweep with a non-zero exit.

The failure was NOT "a concurrent process restarted the server". F40 establishes
the cause by reproduction: ik_llama.cpp fatal-errors in its SWA flash-attention
tail slice on the (--parallel + 1)-th request, then deadlocks in a forked
backtrace handler, so the process never exits, systemd still reports the unit
active, and the listening socket stays open because the forked children inherited
it. The restarts observed afterwards were the RECOVERY, not the cause.

LIVENESS TRAP (F39), which constrains how the fix may be written: /health posts to
llama-server's own task queue, so a SATURATED server and a WEDGED one both answer
it with a timeout. This script therefore NEVER treats a /health timeout during an
in-flight request as evidence of death -- the gate runs only BETWEEN requests,
when a healthy server is provably idle and must answer in milliseconds. The one
signal that does separate busy from wedged is the unit's own CPU time (F39), which
needs SSH to the node; that is offered via --node-ssh-host and is opt-in, and when
it is absent the script degrades to finite timeouts rather than guessing.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "missing-link"))
from missing_link import worker  # noqa: E402


class BackendDead(RuntimeError):
    """The endpoint is confirmed not serving. Aborts the whole sweep, not one size."""


# Floors, not expectations. Measured on this hardware (docs/measurements.md):
# ik_llama.cpp prefills gpt-oss-120b at 18-25 tok/s and mainline at a flat ~16.3;
# both generate at ~5.2 tok/s. These floors sit well below the slowest measured
# value, so a request that exceeds the derived timeout is not slow -- it is not
# happening.
PREFILL_FLOOR_TPS = 4.0
GENERATION_FLOOR_TPS = 1.5
TIMEOUT_SLACK_S = 60


def request_timeout_for(prompt, max_tokens):
    """A FINITE timeout sized from this request, never DEFAULT_TIMEOUT_S (3600)."""
    prompt_tokens = len(prompt.split()) / worker.WORDS_PER_TOKEN
    return int(prompt_tokens / PREFILL_FLOOR_TPS
               + max_tokens / GENERATION_FLOOR_TPS
               + TIMEOUT_SLACK_S)


def health_gate(client, endpoint, label, strikes=2, timeout_s=20, gap_s=15):
    """Confirm the endpoint is serving BETWEEN requests, where idle is guaranteed.

    Two strikes, not one: a single timeout can be a transient. Raises BackendDead
    on confirmed failure so the caller aborts the sweep instead of continuing to
    submit work into a hole.
    """
    last = ""
    for attempt in range(1, strikes + 1):
        try:
            client.assert_reachable()
            return
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            print(f"    [health] {label}: probe {attempt}/{strikes} FAILED -- {last}",
                  flush=True)
            if attempt < strikes:
                time.sleep(gap_s)
    raise BackendDead(
        f"{endpoint} failed {strikes} consecutive /health probes ({timeout_s}s each) "
        f"{label}. Last error: {last}. The server is not serving -- check "
        f"`journalctl -u llama-server@<port>` on that node for a fatal error, and "
        f"note it can be WEDGED while systemd still reports it active (F36, F40).")


def unit_cpu_ms(ssh_host, unit):
    """CPUUsageNSec for a remote systemd unit, in ms. None if unavailable.

    F39's discriminator: a busy server is silent but burning CPU; a wedged one is
    silent and burning none. This is the only signal that separates them, and it
    is the only reason this script may call a mid-request stall a death.
    """
    try:
        out = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host,
             f"systemctl show {unit} -p CPUUsageNSec --value"],
            capture_output=True, text=True, timeout=25).stdout.strip()
        return int(out) / 1e6
    except Exception:
        return None


def guarded_complete(client, endpoint, prompt, max_tokens, label, watchdog):
    """One completion with a FINITE timeout and, optionally, F39's CPU watchdog.

    Returns (text, seconds). Raises BackendDead if the backend is confirmed dead,
    or the client's own exception for a bad-but-served completion.

    The request runs on a DAEMON thread that is never joined. That is deliberate:
    a socket against a wedged llama-server can stay open indefinitely, and
    concurrent.futures.ThreadPoolExecutor cannot be used here because BOTH its
    `with`-block exit AND the interpreter's own atexit hook join their workers --
    which would silently reintroduce the very hang this exists to prevent.
    """
    client.timeout = request_timeout_for(prompt, max_tokens)
    box = {}
    done = threading.Event()

    def run():
        try:
            box["text"] = client.complete(prompt, max_tokens)
        except BaseException as exc:  # noqa: BLE001 -- re-raised on the main thread
            box["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="completion").start()
    t0 = time.monotonic()
    poll_s = watchdog.interval_s if watchdog else client.timeout + 30
    while not done.wait(timeout=poll_s):
        verdict = watchdog.check(endpoint) if watchdog else None
        if verdict:
            raise BackendDead(f"{label}: backend confirmed dead after "
                              f"{time.monotonic() - t0:.0f}s -- {verdict}")
    if "exc" in box:
        raise box["exc"]
    return box["text"], time.monotonic() - t0


class CpuWatchdog:
    """Silent AND burning no CPU == dead. Silent AND burning CPU == busy (F39).

    Only meaningful with SSH to the node, because the discriminator is the unit's
    own CPUUsageNSec and nothing reachable over HTTP can substitute for it: a
    saturated llama-server times out /health, /slots and /metrics identically to a
    wedged one, since all three post to the same task queue.
    """

    def __init__(self, ssh_host, unit, interval_s=45, confirm_s=180):
        self.ssh_host = ssh_host
        self.unit = unit
        self.interval_s = interval_s
        self.confirm_s = confirm_s
        self._last_cpu = None
        self._flat_since = None

    def check(self, endpoint):
        """None if alive/busy/unknown; a reason string only on CONFIRMED death."""
        cpu = unit_cpu_ms(self.ssh_host, self.unit)
        if cpu is None:
            print("    [watchdog] CPU accounting unavailable -- taking no action "
                  "(F39: never act on absence of evidence)", flush=True)
            self._flat_since = None
            self._last_cpu = None
            return None
        advanced = self._last_cpu is not None and cpu - self._last_cpu > 50.0
        self._last_cpu = cpu
        if advanced:
            self._flat_since = None
            return None
        now = time.monotonic()
        if self._flat_since is None:
            self._flat_since = now
            return None
        flat_for = now - self._flat_since
        if flat_for < self.confirm_s:
            print(f"    [watchdog] {self.unit} CPU flat for {flat_for:.0f}s "
                  f"(need {self.confirm_s}s to call it dead)", flush=True)
            return None
        return (f"{self.unit} consumed no CPU for {flat_for:.0f}s while a request "
                f"was in flight. A busy server burns ~400% of one core (F39); this "
                f"one burned none. Check `journalctl -u {self.unit}` for a fatal "
                f"error -- ik_llama.cpp can abort into a forked backtrace handler "
                f"that never exits, leaving systemd reporting the unit active (F40).")


def chunk_spans(document, chunks, chunk_tokens, overlap_tokens):
    """Character offsets into `document` for each chunk `worker.chunk_document` made.

    NOT a reimplementation of chunking. `worker.chunk_document` remains the sole
    authority for the chunk TEXT; this only recovers where each chunk came from,
    because `chunk_document` returns `" ".join(words[a:b])` -- whitespace-normalised,
    so a chunk is not literally a substring of the source and its span cannot be
    found by searching for it.

    The word-index arithmetic below duplicates `chunk_document`'s, which is exactly
    the kind of copy that rots silently. So every span is ASSERTED against the real
    chunk text, and a mismatch aborts rather than emitting plausible-looking wrong
    offsets. A summary whose provenance is wrong is worse than one with none: the
    audit tool would score it against the wrong source and report a hallucination
    that is really a bookkeeping error.
    """
    word_spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", document)]
    words = document.split()
    if len(words) != len(word_spans):
        raise SystemExit("FATAL: word tokenisation disagrees with \\S+ scan; "
                         "cannot produce trustworthy offsets")

    size = max(1, int(chunk_tokens * worker.WORDS_PER_TOKEN))
    overlap = int(overlap_tokens * worker.WORDS_PER_TOKEN)
    stride = size - overlap

    ranges = []
    if len(words) <= size:
        ranges.append((0, len(words)))
    else:
        start = 0
        while start < len(words):
            ranges.append((start, min(start + size, len(words))))
            if start + size >= len(words):
                break
            start += stride

    if len(ranges) != len(chunks):
        raise SystemExit(f"FATAL: derived {len(ranges)} spans for {len(chunks)} "
                         f"chunks at chunk_tokens={chunk_tokens} -- chunk_document's "
                         f"logic has changed; fix chunk_spans() before trusting any "
                         f"provenance it emits")
    out = []
    for i, (a, b) in enumerate(ranges):
        if " ".join(words[a:b]) != chunks[i]:
            raise SystemExit(f"FATAL: span {i + 1} does not reproduce chunk "
                             f"{i + 1} at chunk_tokens={chunk_tokens}")
        out.append({"word_start": a, "word_end": b,
                    "start_char": word_spans[a][0], "end_char": word_spans[b - 1][1]})
    return out


def detect_engine(ssh_host, unit):
    """Which llama.cpp build is actually serving, read from the node's own config.

    Load-bearing metadata, not a nicety: ik_llama.cpp and mainline produce different
    summaries from the same prompt, and the audit corpus is useless as evidence if
    nobody can tell which one wrote it -- the same reason the resume guard refuses
    to reuse chunk summaries across a model change.
    """
    if not ssh_host:
        return None
    try:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host,
             "grep '^LLAMA_BIN=' /etc/default/llama-server"],
            capture_output=True, text=True, timeout=25
        ).stdout.strip().split("=", 1)[-1] or None
    except Exception:
        return None


def load_document(db_path, job_id):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT document FROM jobs WHERE id LIKE ?", (f"{job_id}%",)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"FATAL: no job matching id LIKE '{job_id}%' in {db_path}")
    return row[0]


def run_one_size(endpoint, document, kind, chunk_tokens, overlap_fraction,
                  map_max_tokens, reduce_max_tokens, out_dir, watchdog=None,
                  engine=None, job_id=None):
    overlap_tokens = max(1, round(chunk_tokens * overlap_fraction))
    if overlap_tokens >= chunk_tokens:
        return {"chunk_tokens": chunk_tokens, "status": "SKIPPED",
                "error": f"overlap_tokens ({overlap_tokens}) >= chunk_tokens"}

    chunks = worker.chunk_document(document, chunk_tokens=chunk_tokens,
                                    overlap_tokens=overlap_tokens)
    n_chunks = len(chunks)
    est_chunk_tokens = [int(len(c.split()) / worker.WORDS_PER_TOKEN) for c in chunks]
    spans = chunk_spans(document, chunks, chunk_tokens, overlap_tokens)

    client = worker.LlamaClient(endpoint)

    # THE AUDIT CORPUS. `missing_link.audit` scores a summary's sentences against
    # THE CHUNK THEY CAME FROM, and until now it has had exactly one real chunk
    # summary in existence to work with -- everything else validating it is
    # synthetic. This sweep produces ~90 real ones, so they are written to disk
    # as they arrive rather than left to evaporate with the process.
    #
    # Offsets are the point. A summary without its source span cannot be audited,
    # only admired.
    os.makedirs(out_dir, exist_ok=True)
    corpus_path = os.path.join(out_dir, f"corpus_chunk_{chunk_tokens}.json")
    corpus = {
        "schema_version": 1,
        "produced_by": "bench/chunk_size_driver.py",
        "produced_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "real chunk summaries with source provenance, for missing_link.audit",
        "engine": engine,
        "endpoint": endpoint,
        "model_name": client.model_name(),
        "job_id": job_id,
        "kind": kind,
        "document_chars": len(document),
        "document_sha256": hashlib.sha256(document.encode()).hexdigest(),
        "chunk_tokens": chunk_tokens,
        "overlap_tokens": overlap_tokens,
        "words_per_token": worker.WORDS_PER_TOKEN,
        "map_max_tokens": map_max_tokens,
        "reduce_max_tokens": reduce_max_tokens,
        "n_chunks": n_chunks,
        "offsets_note": ("start_char/end_char index the ORIGINAL document. "
                         "chunk_text is worker.chunk_document's whitespace-normalised "
                         "form, i.e. ' '.join(document[start_char:end_char].split()). "
                         "Verified equal at write time."),
        "chunks": [],
        "reduce": None,
    }

    def persist_corpus():
        with open(corpus_path, "w") as f:
            json.dump(corpus, f, indent=1)
    print(f"  chunk_tokens={chunk_tokens} overlap_tokens={overlap_tokens} "
          f"-> {n_chunks} chunks (est. tokens/chunk: min={min(est_chunk_tokens)} "
          f"max={max(est_chunk_tokens)})", flush=True)

    status = "ok"
    error = None
    partials = []
    wall_start = time.monotonic()

    for idx, c in enumerate(chunks):
        label = f"chunk {idx + 1}/{n_chunks} (chunk_tokens={chunk_tokens})"
        # Between requests a healthy server is provably idle, so a /health
        # timeout HERE means something (unlike mid-request -- see F39).
        health_gate(client, endpoint, f"before {label}")
        prompt = worker.build_prompt(kind, c)
        try:
            text, dt = guarded_complete(client, endpoint, prompt, map_max_tokens,
                                        label, watchdog)
            partials.append(text)
        except Exception as exc:  # EmptyCompletion / Truncated / BackendDead / HTTPError
            status = "FAILED"
            error = f"map {label}: {type(exc).__name__}: {exc}"
            print(f"    {label}: FAILED after {client.timeout}s budget -- {error}",
                  flush=True)
            corpus["chunks"].append({"index": idx + 1, **spans[idx],
                                     "status": "FAILED", "error": error,
                                     "summary": None})
            persist_corpus()
            if isinstance(exc, BackendDead):
                raise
            # Not obviously fatal, but confirm the endpoint is still serving
            # before writing this off as a bad completion.
            health_gate(client, endpoint, f"after failed {label}")
            break
        tim = client.timings_log[-1] if client.timings_log else {}
        corpus["chunks"].append({
            "index": idx + 1, **spans[idx], "status": "ok",
            "chunk_text": chunks[idx], "summary": text,
            "prompt_n": tim.get("prompt_n"), "prompt_ms": tim.get("prompt_ms"),
            "predicted_n": tim.get("predicted_n"), "predicted_ms": tim.get("predicted_ms"),
            "wall_s": round(dt, 1),
        })
        # Written after EVERY chunk, not at the end: this sweep has already been
        # killed mid-flight once, and a corpus that only exists on success is a
        # corpus you get to collect exactly when you did not need it.
        persist_corpus()
        print(f"    chunk {idx + 1}/{n_chunks}: est~{est_chunk_tokens[idx]}tok  "
              f"prompt_n={tim.get('prompt_n')} predicted_n={tim.get('predicted_n')}  "
              f"wall {dt:.1f}s", flush=True)

    final_text = None
    if status == "ok":
        if n_chunks == 1:
            final_text = partials[0]
        else:
            reduce_prompt = worker.build_reduce_prompt(kind, partials)
            label = f"reduce (chunk_tokens={chunk_tokens})"
            health_gate(client, endpoint, f"before {label}")
            try:
                final_text, dt = guarded_complete(client, endpoint, reduce_prompt,
                                                  reduce_max_tokens, label, watchdog)
                tim = client.timings_log[-1] if client.timings_log else {}
                corpus["reduce"] = {
                    "status": "ok", "summary": final_text,
                    "n_partials": len(partials),
                    "prompt_n": tim.get("prompt_n"), "prompt_ms": tim.get("prompt_ms"),
                    "predicted_n": tim.get("predicted_n"),
                    "predicted_ms": tim.get("predicted_ms"),
                    "wall_s": round(dt, 1),
                }
                persist_corpus()
                print(f"    reduce: prompt_n={tim.get('prompt_n')} "
                      f"predicted_n={tim.get('predicted_n')}  wall {dt:.1f}s", flush=True)
            except Exception as exc:
                status = "FAILED"
                error = f"reduce: {type(exc).__name__}: {exc}"
                corpus["reduce"] = {"status": "FAILED", "error": error, "summary": None}
                persist_corpus()
                print(f"    reduce: FAILED -- {error}", flush=True)
                if isinstance(exc, BackendDead):
                    raise

    wall_total = time.monotonic() - wall_start

    total_prefill_s = sum((t.get("prompt_ms") or 0) for t in client.timings_log) / 1000.0
    total_generation_s = sum((t.get("predicted_ms") or 0) for t in client.timings_log) / 1000.0
    total_prompt_tokens = sum((t.get("prompt_n") or 0) for t in client.timings_log)
    total_completion_tokens = sum((t.get("predicted_n") or 0) for t in client.timings_log)
    n_calls = len(client.timings_log)

    # Persist output for a human coherence check, whatever we got.
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"chunk_{chunk_tokens}.txt")
    with open(out_path, "w") as f:
        f.write(f"# chunk_tokens={chunk_tokens} overlap_tokens={overlap_tokens} "
                f"n_chunks={n_chunks} status={status}\n")
        if error:
            f.write(f"# error: {error}\n")
        f.write("\n--- FINAL (reduced) OUTPUT ---\n\n")
        f.write(final_text or "(none -- run failed before reduce completed)")
        f.write("\n\n--- MAP-STEP PARTIALS ---\n\n")
        for i, p in enumerate(partials):
            f.write(f"\n[chunk {i + 1}/{n_chunks}]\n{p}\n")

    return {
        "chunk_tokens": chunk_tokens,
        "overlap_tokens": overlap_tokens,
        "n_chunks": n_chunks,
        "status": status,
        "error": error,
        "wall_s": round(wall_total, 1),
        "total_prefill_s": round(total_prefill_s, 1),
        "total_generation_s": round(total_generation_s, 1),
        "n_calls": n_calls,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "final_chars": len(final_text) if final_text else 0,
        "model_name": client.model_name(),
        "out_file": out_path,
        "corpus_file": corpus_path,
        "engine": engine,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="e.g. http://10.10.0.39:8080")
    ap.add_argument("--db", default="/opt/missing-link/jobs.sqlite")
    ap.add_argument("--job-id", default="06af2911d7fc")
    ap.add_argument("--kind", default="summarise", choices=list(worker.PROMPTS))
    ap.add_argument("--chunk-sizes", default="1024,2048,3072,4096,6144")
    ap.add_argument("--overlap-fraction", type=float, default=0.10)
    ap.add_argument("--map-max-tokens", type=int, default=worker.MAP_MAX_TOKENS)
    ap.add_argument("--reduce-max-tokens", type=int, default=worker.REDUCE_MAX_TOKENS)
    ap.add_argument("--out-dir", default="/tmp/chunk-size-bench")
    ap.add_argument("--json-out", default="/tmp/chunk-size-bench/results.json")
    ap.add_argument("--node-ssh-host", default=os.environ.get("NODE_SSH_HOST"),
                    help="SSH host of the node serving --endpoint. Enables F39's "
                         "CPU-progress watchdog, the only signal that tells a "
                         "SATURATED server from a WEDGED one. Without it the sweep "
                         "still cannot hang, but a dead backend takes one finite "
                         "request timeout to notice instead of ~3 minutes.")
    ap.add_argument("--node-unit", default="llama-server@8080",
                    help="systemd unit on --node-ssh-host, for CPU accounting.")
    ap.add_argument("--engine", default=None,
                    help="Which llama.cpp build is serving --endpoint, e.g. "
                         "/opt/llama.cpp/bin or /opt/ik_llama.cpp/bin. Recorded in "
                         "the audit corpus, where it is load-bearing metadata. "
                         "Auto-detected over SSH when --node-ssh-host is given.")
    args = ap.parse_args()

    watchdog = None
    if args.node_ssh_host:
        watchdog = CpuWatchdog(args.node_ssh_host, args.node_unit)
        probe = unit_cpu_ms(args.node_ssh_host, args.node_unit)
        if probe is None:
            print(f"WARNING: --node-ssh-host {args.node_ssh_host} given but "
                  f"CPUUsageNSec for {args.node_unit} could not be read. The "
                  f"mid-request watchdog will take no action (F39).")
        else:
            print(f"Watchdog: {args.node_ssh_host}:{args.node_unit} "
                  f"(CPUUsageNSec readable, currently {probe:.0f} ms)")
    else:
        print("Watchdog: DISABLED (no --node-ssh-host). Finite per-request "
              "timeouts and between-request /health gates still apply.")

    engine = args.engine or detect_engine(args.node_ssh_host, args.node_unit)
    if engine:
        print(f"Engine: {engine}")
    else:
        print("Engine: UNKNOWN -- pass --engine or --node-ssh-host. The audit "
              "corpus will record null, and a corpus that cannot say which build "
              "produced it is weak evidence.")

    document = load_document(args.db, args.job_id)
    print(f"Document: {len(document)} chars, {len(document.split())} words "
          f"(job {args.job_id}, kind={args.kind})")
    print(f"Endpoint: {args.endpoint}")
    print(f"map_max_tokens={args.map_max_tokens} reduce_max_tokens={args.reduce_max_tokens} "
          f"overlap_fraction={args.overlap_fraction}")
    print()

    sizes = [int(s) for s in args.chunk_sizes.split(",") if s.strip()]
    results = []
    aborted = None

    def persist():
        os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
        with open(args.json_out, "w") as f:
            json.dump({"document_chars": len(document),
                       "document_words": len(document.split()),
                       "job_id": args.job_id, "endpoint": args.endpoint,
                       "aborted": aborted, "results": results}, f, indent=2)

    for size in sizes:
        print(f"=== chunk_tokens={size} ===", flush=True)
        try:
            r = run_one_size(args.endpoint, document, args.kind, size,
                              args.overlap_fraction, args.map_max_tokens,
                              args.reduce_max_tokens, args.out_dir, watchdog,
                              engine, args.job_id)
        except BackendDead as exc:
            # A dead backend invalidates every remaining sweep point, so stop.
            # Continuing would produce five silent failures instead of one loud
            # one, which is the exact invisible-work failure this guards against.
            aborted = str(exc)
            results.append({"chunk_tokens": size, "status": "ABORTED",
                            "error": aborted})
            persist()
            print(f"\nFATAL: {aborted}\n", flush=True)
            print("SWEEP ABORTED. Remaining sizes were NOT run. Partial results "
                  f"are in {args.json_out}.", flush=True)
            sys.exit(2)
        results.append(r)
        print(f"  -> status={r['status']} wall={r.get('wall_s')}s "
              f"prefill={r.get('total_prefill_s')}s gen={r.get('total_generation_s')}s "
              f"n_calls={r.get('n_calls')}", flush=True)
        persist()
        print()

    print("=== Summary ===")
    print(f"{'chunk_tokens':>12} {'n_chunks':>9} {'status':>8} {'wall_s':>8} "
          f"{'prefill_s':>10} {'gen_s':>8} {'n_calls':>8} {'prompt_tok':>11} {'compl_tok':>10}")
    for r in results:
        print(f"{r['chunk_tokens']:>12} {r.get('n_chunks', '-'):>9} {r['status']:>8} "
              f"{r.get('wall_s', '-'):>8} {r.get('total_prefill_s', '-'):>10} "
              f"{r.get('total_generation_s', '-'):>8} {r.get('n_calls', '-'):>8} "
              f"{r.get('total_prompt_tokens', '-'):>11} {r.get('total_completion_tokens', '-'):>10}")
    print()
    print(f"JSON written to {args.json_out}")
    print(f"Per-size outputs (for coherence review) in {args.out_dir}/chunk_<N>.txt")
    print(f"AUDIT CORPUS (real chunk summaries + source offsets, for "
          f"missing_link.audit) in {args.out_dir}/corpus_chunk_<N>.json")


if __name__ == "__main__":
    main()

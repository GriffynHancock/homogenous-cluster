#!/usr/bin/env python3
"""Concurrent load driver for the replication measurement.

Measures AGGREGATE throughput across R independent llama-server endpoints, which
is the number the whole architecture rests on: sharding buys capacity, only
replication buys speed (aggregate ~= R x single-node).

Deliberate choices, each of which is a lesson already paid for in this repo:

  * Every prompt is UNIQUE. Reusing a prompt measures llama.cpp's prompt cache,
    not the model -- F17 caught the plan reporting a cached run as a real one.
  * enable_thinking=false and a generous max_tokens. Reasoning models emit into
    reasoning_content and return an EMPTY content string when the budget runs
    out mid-thought, a 200 OK carrying nothing -- F21.
  * An empty completion is counted as a FAILURE, never as a zero-length success.
    F21's whole danger was a successful-looking job with no output.
  * Timing comes from the server's own usage counts, not from a token estimate.

Stdlib only: this runs on a bare Debian 12 node with no pip install.
"""

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

# Varied subject matter so no two prompts share a prefix. A shared prefix would
# be served from the prompt cache on the second request and inflate throughput.
TOPICS = [
    "a regional hospital deciding where to run clinical summarisation",
    "a community legal centre handling protected client records",
    "a university research office with an ethics-approval backlog",
    "a state education department processing student welfare reports",
    "a disability services provider summarising case notes",
    "a local council reviewing planning submissions",
    "an aged-care operator auditing incident reports",
    "a family violence service triaging intake documents",
    "a public health unit collating outbreak field notes",
    "a child protection agency reviewing historical case files",
    "an Aboriginal health service managing culturally sensitive records",
    "a coroner's office indexing decades of inquest material",
]


def build_prompt(index: int, filler_words: int) -> str:
    """A unique prompt of roughly fixed length.

    The counter appears FIRST so no two prompts share a cacheable prefix -- the
    prompt cache matches on leading tokens, so a trailing nonce would not
    defeat it.
    """
    topic = TOPICS[index % len(TOPICS)]
    head = f"[request {index:04d}] Consider {topic}. "
    # Deterministic filler: reproducible across runs, still unique per request.
    filler = " ".join(
        f"item{index}-{w}" for w in range(filler_words)
    )
    tail = (
        " Given the material above, explain in two or three sentences why this "
        "organisation might run a language model on hardware it already owns, "
        "rather than sending the data to an external service."
    )
    return head + filler + tail


def strip_thinking(text: str):
    """Remove <think>...</think> blocks. Returns (answer, thought).

    Handles the unclosed case too: if generation was cut off mid-thought there is
    an opening tag and no closing one, and everything after it is thought, not
    answer.
    """
    thought_parts = []
    out = []
    i = 0
    low = text.lower()
    while True:
        start = low.find("<think>", i)
        if start == -1:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = low.find("</think>", start)
        if end == -1:                      # truncated mid-thought
            thought_parts.append(text[start + 7:])
            break
        thought_parts.append(text[start + 7:end])
        i = end + 8
    return "".join(out).strip(), "".join(thought_parts).strip()


class Result:
    __slots__ = ("endpoint", "ok", "prompt_tokens", "completion_tokens",
                 "latency", "error", "text", "thinking")

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.ok = False
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latency = 0.0
        self.error = ""
        self.text = ""
        self.thinking = False


def one_request(endpoint, index, max_tokens, filler_words, timeout) -> Result:
    r = Result(endpoint)
    body = {
        "messages": [{"role": "user", "content": build_prompt(index, filler_words)}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        # F21: keep the token budget for the ANSWER, not the chain of thought.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - report anything, never crash the run
        r.latency = time.monotonic() - t0
        r.error = f"{type(exc).__name__}: {exc}"
        return r
    r.latency = time.monotonic() - t0

    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError):
        r.error = "malformed response: no choices[0].message"
        return r

    raw = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    if not raw:
        # F21. Never score this as a success.
        r.error = ("EMPTY content"
                   + (" but reasoning_content populated (F21)" if reasoning else ""))
        return r

    # ik_llama.cpp (and mainline without --jinja) can IGNORE enable_thinking and
    # emit the chain of thought INLINE in content, wrapped in <think>...</think>.
    # That slips straight past an empty-content check while containing no answer
    # at all -- a strictly nastier variant of F21, because the guard passes.
    # Strip the block and judge what is actually left.
    content, thought = strip_thinking(raw)
    r.thinking = bool(thought)
    if not content:
        r.error = ("content is ONLY a <think> block, no answer "
                   "(enable_thinking not honoured; raise max_tokens or pass --jinja)")
        return r

    usage = payload.get("usage") or {}
    r.prompt_tokens = int(usage.get("prompt_tokens") or 0)
    r.completion_tokens = int(usage.get("completion_tokens") or 0)
    if r.completion_tokens == 0:
        r.error = "server reported 0 completion_tokens"
        return r

    r.ok = True
    r.text = content
    return r


def run_phase(endpoints, per_endpoint, max_tokens, filler_words, timeout, tag,
              index_base):
    """Fire per_endpoint concurrent requests at EACH endpoint, all at once.

    Returns (wall_seconds, [Result]). Wall time spans the first dispatch to the
    last completion, which is what aggregate throughput must be divided by.
    """
    results = []
    lock = threading.Lock()
    threads = []

    def worker(ep, idx):
        res = one_request(ep, idx, max_tokens, filler_words, timeout)
        with lock:
            results.append(res)

    idx = index_base
    for ep in endpoints:
        for _ in range(per_endpoint):
            threads.append(threading.Thread(target=worker, args=(ep, idx), daemon=True))
            idx += 1

    print(f"  [{tag}] dispatching {len(threads)} requests "
          f"({per_endpoint} per endpoint x {len(endpoints)} endpoint(s))",
          flush=True)
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - t0
    return wall, results


def summarise(tag, endpoints, wall, results):
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    comp = sum(r.completion_tokens for r in ok)
    prom = sum(r.prompt_tokens for r in ok)

    print(f"\n  === {tag} ===")
    print(f"    endpoints           : {len(endpoints)}")
    print(f"    requests ok / total : {len(ok)} / {len(results)}")
    print(f"    wall time           : {wall:.2f} s")
    print(f"    prompt tokens       : {prom}")
    print(f"    completion tokens   : {comp}")
    if wall > 0:
        # BOTH figures are per TOTAL WALL CLOCK, so each is diluted by the other:
        # the "generation" rate is divided by a wall time that is mostly prefill,
        # and vice versa. They are end-to-end fleet throughput numbers and are
        # NOT comparable to llama-bench's tg128 / pp2048, which isolate one phase
        # at batch 1. Quoting them as if they were is exactly the class of error
        # F17 and F28 were caused by.
        #
        # What they ARE valid for is the Phase A / Phase B ratio, because both
        # phases use identical methodology -- and that ratio is the whole point.
        print(f"    completion tok/s (per total wall clock): {comp / wall:.2f}")
        print(f"    prompt     tok/s (per total wall clock): {prom / wall:.2f}")
        print(f"    combined   tok/s (prompt + completion) : {(comp + prom) / wall:.2f}")
    if ok:
        lat = [r.latency for r in ok]
        print(f"    per-request latency : min {min(lat):.1f}s  "
              f"median {statistics.median(lat):.1f}s  max {max(lat):.1f}s")
        n_think = sum(1 for r in ok if r.thinking)
        if n_think:
            print(f"    !! {n_think}/{len(ok)} answers still contained a <think> block -- "
                  "thinking tokens are inflating completion_tokens")
    for ep in endpoints:
        e_ok = [r for r in ok if r.endpoint == ep]
        e_comp = sum(r.completion_tokens for r in e_ok)
        print(f"      {ep}: {len(e_ok)} ok, {e_comp} tokens"
              + (f", {e_comp / wall:.2f} tok/s" if wall > 0 else ""))
    if bad:
        print(f"    !! {len(bad)} FAILED:")
        seen = {}
        for r in bad:
            seen[r.error] = seen.get(r.error, 0) + 1
        for err, n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"       {n}x {err}")
    return {"tag": tag, "endpoints": len(endpoints), "wall": wall,
            "ok": len(ok), "total": len(results),
            "completion_tokens": comp, "prompt_tokens": prom,
            "gen_tok_s": (comp / wall) if wall else 0.0,
            "prefill_tok_s": (prom / wall) if wall else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoints", required=True,
                    help="comma-separated, e.g. http://10.10.0.34:8080,http://10.10.0.39:8080")
    ap.add_argument("--per-endpoint", type=int, default=4,
                    help="concurrent requests per endpoint (match --parallel; never 8, F24)")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--filler-words", type=int, default=300,
                    help="rough prompt length knob; 300 ~= 1000+ tokens")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    endpoints = [e.strip().rstrip("/") for e in args.endpoints.split(",") if e.strip()]
    if not endpoints:
        sys.exit("no endpoints")

    print(f"Endpoints: {endpoints}")
    print(f"Per-endpoint concurrency: {args.per_endpoint}   max_tokens: {args.max_tokens}")

    phases = []
    # Phase A: ONE endpoint only -> the single-node denominator.
    wall, res = run_phase(endpoints[:1], args.per_endpoint, args.max_tokens,
                          args.filler_words, args.timeout, "single node",
                          index_base=1000)
    phases.append(summarise("PHASE A - 1 endpoint (baseline)", endpoints[:1], wall, res))
    sample = next((r.text for r in res if r.ok), "")

    # Phase B: ALL endpoints, same per-endpoint load. If replication works this
    # completes in about the same wall time while doing R times the work.
    if len(endpoints) > 1:
        wall, res = run_phase(endpoints, args.per_endpoint, args.max_tokens,
                              args.filler_words, args.timeout, "all nodes",
                              index_base=5000)
        phases.append(summarise(f"PHASE B - {len(endpoints)} endpoints (replicated)",
                                endpoints, wall, res))

    print("\n=== SCALING -- the number the architecture rests on ===")
    print("  Aggregate ~= R x single-node is the claim. Both phases put the SAME")
    print("  load on each endpoint, so if replication is linear the wall time is")
    print("  unchanged while the work done doubles.")
    base = phases[0]
    for p in phases[1:]:
        ideal = p["endpoints"] / base["endpoints"]
        print(f"\n  {p['endpoints']} endpoints vs {base['endpoints']} "
              f"(ideal {ideal:.2f}x):")
        if base["gen_tok_s"] > 0:
            f = p["gen_tok_s"] / base["gen_tok_s"]
            print(f"    completion throughput : {f:.2f}x  "
                  f"({100 * f / ideal:.0f}% of linear)")
        if base["prefill_tok_s"] > 0:
            f = p["prefill_tok_s"] / base["prefill_tok_s"]
            print(f"    prompt throughput     : {f:.2f}x  "
                  f"({100 * f / ideal:.0f}% of linear)")
        if base["wall"] > 0:
            print(f"    wall time             : {base['wall']:.1f}s -> "
                  f"{p['wall']:.1f}s  ({p['wall'] / base['wall']:.2f}x "
                  f"for {ideal:.0f}x the work)")

    if sample:
        print("\n=== COHERENCE CHECK (a real completion, verbatim) ===")
        print("  " + sample.replace("\n", "\n  ")[:700])

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(phases, fh, indent=2)
        print(f"\nwrote {args.json_out}")

    # Any failed request invalidates the throughput number it contributed to.
    if any(p["ok"] != p["total"] for p in phases):
        print("\nWARNING: some requests failed -- throughput above is NOT clean.")
        sys.exit(2)


if __name__ == "__main__":
    main()

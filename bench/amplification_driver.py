#!/usr/bin/env python3
"""Paired fabrication-amplification experiment: single-pass vs map-reduce.

THE INFERENCE HALF. The model-free half -- section selection, unit building,
scoring, statistics, cost model -- is `missing_link.amplification`, and the
split is deliberate: CLAUDE.md requires prompts, chunking and evaluation stay
separable from the queue and worker because that seam becomes the skill's
task-profile interface later, and it is far cheaper to preserve than to
retrofit. This file is allowed to open sockets; that one is not.

FIVE PHASES, RUN SEPARATELY AND ON PURPOSE
------------------------------------------
    estimate   arithmetic only. What will this cost? No network, no database.
    plan       choose the sections. Needs the server's TOKENISER (not its
               inference), reads the corpus read-only, writes a manifest.
    run        the only phase that generates. Resumable per (section, arm).
    score      the deterministic cascade over what `run` produced. CPU-only,
               no cluster, and it MUST NOT overlap with `run` -- F44 measured a
               niced CPU-bound sidecar starving llama-server on a 4-core node.
    analyse    the paired statistics and the adjudication pack.

`plan` is separate from `run` because the manifest is the pre-registration: it
records which sections were chosen, why the rest were rejected, and what the
run is expected to cost, BEFORE any output exists to be chosen around.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not submit to the Missing Link queue. The queue always chunks, so it
cannot express the single-pass arm at all -- and the brief for this harness is
explicit that the live queue is not to be used. It drives llama-server directly
through `missing_link.worker`'s OWN client, prompts and chunker, exactly as
`bench/chunk_size_driver.py` does, so a result here is a result about the real
pipeline rather than about a reimplementation of it.

It does not restart, stop or reconfigure any service. It REFUSES to start if
the live queue has a job running (STATUS.md: do not benchmark a node while it
or its peer is doing real work; F39 lost 10m55s of completed work to exactly
that).

TRAPS ALREADY PAID FOR, INHERITED RATHER THAN REDISCOVERED
----------------------------------------------------------
The health gate, the finite per-request timeout and the CPU watchdog are
imported from `chunk_size_driver` rather than copied. They encode F17 (never
time TTFT from the client), F39 (/health shares the queue it probes, so it is
only meaningful BETWEEN requests) and F40 (a wedged server keeps its socket
open, so only the unit's own CPU time separates busy from dead). Re-deriving
them here would mean re-earning them.

THE PROMPT CACHE IS SHARED ACROSS RUNS AND ACROSS ARMS, AND HERE THAT IS NOT A
TIMING PROBLEM, IT IS A VALIDITY PROBLEM. The single-pass arm and the
map-reduce arm are given the SAME section text. Run back to back, the second
arm's prefill partly hits the first arm's cache. That does not change what the
model outputs -- the logits for a given prompt are the same whether the KV came
from cache or was recomputed -- so the FAITHFULNESS endpoints are unaffected.
It does deflate the second arm's measured prefill time, so the wall-clock
numbers this script reports are NOT a fair arm-vs-arm speed comparison and are
labelled as such wherever they are written. Speed is not what this experiment
measures.
"""
import argparse
import datetime
import hashlib
import json
import os
import queue
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "missing-link"))
sys.path.insert(0, HERE)

from missing_link import amplification as amp   # noqa: E402
from missing_link import worker                 # noqa: E402
import chunk_size_driver as guards              # noqa: E402

class _EndpointDied(RuntimeError):
    """This endpoint is out of the run. The others are not."""


DEFAULT_DB = "/opt/missing-link/jobs.sqlite"
DEFAULT_OUT = os.path.join(HERE, "out", "amplification")


# ---------------------------------------------------------------------------
# Exact token counting -- and why it is free
# ---------------------------------------------------------------------------
class ServerTokenizer:
    """POST /tokenize against llama-server. EXACT, and it does not queue.

    VERIFIED FROM THE SERVER'S OWN SOURCE, not assumed: in
    `/opt/llama.cpp/src/tools/server/server-context.cpp` the `/tokenize`
    handler calls `tokenize_mixed(ctx_server.vocab, ...)` directly on the HTTP
    thread and constructs no task. So it does not take a slot, does not enter
    the inference queue, and cannot perturb a benchmark or a real job running on
    the same server. That is what makes it safe to call hundreds of times during
    `plan`.

    Why this matters at all: `worker.WORDS_PER_TOKEN = 0.70` is a documented
    approximation and worker.py's own comments flag it as known-optimistic. The
    chunk-size sweep's raw output shows the true ratio varying from ~0.95 to
    ~1.87 tokens per word WITHIN ONE DOCUMENT. An estimate that wrong decides
    whether the single-pass arm fits its slot -- and a section that does not fit
    either errors at the server or, far worse, silently drops its own opening,
    which would be a truncated control arm being compared against a complete
    treatment arm. So this is measured, per section, every time.
    """

    def __init__(self, endpoint, timeout=60):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.calls = 0

    def __call__(self, text):
        if not text:
            return 0
        body = json.dumps({"content": text, "add_special": True,
                           "parse_special": False}).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/tokenize", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            payload = json.load(r)
        self.calls += 1
        return len(payload.get("tokens") or [])


class RatioTokenizer:
    """Fallback for a machine with no server. LOUDLY not exact.

    Present so `plan --dry-run` can be exercised off-cluster and so the tests
    have something to inject. A manifest built with this is marked
    `tokenizer: "ratio (ESTIMATED -- NOT SAFE TO RUN)"` and `run` refuses it.
    """

    def __init__(self, tokens_per_word=amp.ASSUMED_TOKENS_PER_WORD):
        self.tokens_per_word = tokens_per_word

    def __call__(self, text):
        return int(len(text.split()) * self.tokens_per_word)


def read_n_ctx_slot(endpoint):
    """The slot size, from the server rather than from `-c / --parallel`.

    CLAUDE.md is explicit that `n_ctx_slot` must be read, not inferred. The
    startup log is the canonical place, but that needs journalctl on the right
    node; `/props` carries `n_ctx` per slot on this build and is reachable from
    anywhere, so it is tried first and the log line is the fallback the operator
    is told to check. Returns (value, source) or (None, reason).
    """
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/props",
                                    timeout=15) as r:
            props = json.load(r)
    except Exception as exc:
        return None, f"/props unreachable ({type(exc).__name__}: {exc})"
    for key in ("n_ctx_per_seq", "n_ctx_slot"):
        if isinstance(props.get(key), int):
            return props[key], f"/props {key}"
    dp = props.get("default_generation_settings") or {}
    for key in ("n_ctx", "n_ctx_slot"):
        if isinstance(dp.get(key), int):
            return dp[key], f"/props default_generation_settings.{key}"
    return None, ("/props answered but carries no per-slot context size; read "
                  "it from the server log instead: "
                  "journalctl -u llama-server@8080 | grep n_ctx_slot")


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
def cmd_estimate(args):
    for nodes in (1, 2):
        for arms in ([amp.ARM_SINGLE_PASS, amp.ARM_MAP_REDUCE],
                     [amp.ARM_SINGLE_PASS, amp.ARM_MAP_REDUCE,
                      amp.ARM_MAP_REDUCE_FINE]):
            est = amp.estimate_run(args.sections, arms=arms, nodes=nodes,
                                   section_tokens=args.section_tokens)
            print(f"{len(arms)} arms, {args.sections} sections, {nodes} node(s): "
                  f"{est['total_hours']:>6.1f} h "
                  f"({est['seconds_per_section_one_node'] / 60:.1f} min/section "
                  f"on one node)")
    print()
    est = amp.estimate_run(args.sections, nodes=1,
                           section_tokens=args.section_tokens)
    for a in est["per_arm"]:
        print(f"  {a['arm']:<18} {a['calls']:>2} call(s)  "
              f"prefill {a['prefill_tokens']:>6} tok / {a['prefill_s']:>6.1f} s   "
              f"gen {a['generation_tokens']:>5} tok / {a['generation_s']:>6.1f} s   "
              f"= {a['seconds'] / 60:.1f} min")
    print(f"\nbasis: {est['basis']}")
    print(f"section budget: {amp.single_pass_budget_tokens()} tokens "
          f"(n_ctx_slot {worker.N_CTX_SLOT} - output "
          f"{amp.SECTION_OUTPUT_TOKENS} - wrapper {amp.SECTION_WRAPPER_TOKENS}, "
          f"x{amp.SECTION_SAFETY} headroom)")
    print(f"McNemar needs >= {amp.min_discordant_for_significance()} discordant "
          "pairs before ANY split of them can reach p < 0.05")
    return 0


def _whole_doc_fit(doc, tok, budget):
    """True / False / "not measured (obviously too long)".

    Real prose does not tokenise below roughly one token per word, so a document
    with more than 2x the budget in WORDS cannot fit however generous the
    tokeniser. Stated as a named function rather than inlined because it is an
    assumption, and an assumption deserves somewhere to be argued with.
    """
    if (doc.get("n_words") or 0) > 2 * budget:
        return "not measured (word count alone rules it out)"
    return tok(doc["text"]) <= budget


def cmd_plan(args):
    if args.dry_run:
        tok = RatioTokenizer()
        tok_label = "ratio (ESTIMATED -- NOT SAFE TO RUN)"
        slot, slot_src = worker.N_CTX_SLOT, "worker.N_CTX_SLOT (no server asked)"
    else:
        tok = ServerTokenizer(args.endpoint)
        tok_label = f"{args.endpoint}/tokenize (exact)"
        slot, slot_src = read_n_ctx_slot(args.endpoint)
        if slot is None:
            print(f"REFUSING TO PLAN: {slot_src}", file=sys.stderr)
            return 2
        print(f"n_ctx_slot = {slot}  (from {slot_src})")
        if slot != worker.N_CTX_SLOT:
            print(f"  NOTE: worker.N_CTX_SLOT says {worker.N_CTX_SLOT}. The "
                  "server wins; worker.py's constant is a snapshot.")

    budget = amp.single_pass_budget_tokens(slot_tokens=slot)
    print(f"single-pass source budget: {budget} tokens\n")

    docs = amp.read_corpus(args.db, doc_ids=args.doc or None)
    print(f"{len(docs)} corpus documents (status=ready)\n")

    by_doc, doc_meta = {}, {}
    for d in docs:
        cands = amp.candidate_sections(
            d["text"], tok, budget_tokens=budget,
            chunk_tokens=worker.CHUNK_TOKENS,
            overlap_tokens=worker.OVERLAP_TOKENS)
        by_doc[d["id"]] = cands
        acc = [c for c in cands if not c.get("rejected")]
        doc_meta[d["id"]] = {
            "filename": d["filename"], "genre": d["genre"],
            "n_words": d["n_words"], "text_sha256": d["text_sha256"],
            "candidates": len(cands), "accepted": len(acc),
            # "Could this document have been single-passed INTACT?" -- the
            # question the section design exists because of. Only actually
            # tokenised for documents within a factor of two of the budget: a
            # 200,000-word standard cannot fit 5,094 tokens under any tokeniser,
            # and pushing 1.6 MB through /tokenize to prove it would occupy the
            # server's HTTP thread for no information.
            "fits_whole_document_single_pass": _whole_doc_fit(
                d, tok, budget) if args.check_whole else None,
        }
        print(f"  {d['filename'][:52]:<54} {d['n_words']:>7} words  "
              f"{len(acc):>3} usable sections / {len(cands)} candidates")

    picked = amp.sample_sections(by_doc, per_doc=args.per_doc, seed=args.seed)
    est = amp.estimate_run(len(picked), arms=args.arms, nodes=args.nodes)

    manifest = {
        "schema_version": 1,
        "produced_by": "bench/amplification_driver.py plan",
        "produced_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "purpose": ("paired single-pass vs map-reduce fabrication-amplification "
                    "experiment; see missing_link/amplification.py"),
        "db": args.db,
        "endpoint": None if args.dry_run else args.endpoint,
        "tokenizer": tok_label,
        "n_ctx_slot": slot,
        "n_ctx_slot_source": slot_src,
        "single_pass_budget_tokens": budget,
        "chunk_tokens": worker.CHUNK_TOKENS,
        "overlap_tokens": worker.OVERLAP_TOKENS,
        "arms": {a: amp.ARMS[a] for a in args.arms},
        "per_doc": args.per_doc,
        "seed": args.seed,
        "documents": doc_meta,
        "rejected_examples": {
            doc: [c for c in cands if c.get("rejected")][:3]
            for doc, cands in by_doc.items()},
        "sections": picked,
        "estimate": est,
        "min_discordant_for_significance":
            amp.min_discordant_for_significance(),
    }
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w") as fh:
        json.dump(manifest, fh, indent=1)

    print(f"\n{len(picked)} sections selected across "
          f"{len({p['doc_id'] for p in picked})} documents")
    print(f"estimated {est['total_hours']} h on {args.nodes} node(s)")
    print(f"manifest: {path}")
    return 0


def _load_manifest(out_dir):
    with open(os.path.join(out_dir, "manifest.json")) as fh:
        return json.load(fh)


def _results_path(out_dir):
    return os.path.join(out_dir, "results.json")


def _load_results(out_dir):
    p = _results_path(out_dir)
    if os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    return {"schema_version": 1, "runs": {}}


def _save_results(out_dir, results):
    tmp = _results_path(out_dir) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(results, fh, indent=1)
    os.replace(tmp, _results_path(out_dir))


def run_arm(client, endpoint, section_text, arm, kind, watchdog):
    """One arm over one section. Returns the record `score` will read.

    Single-pass and map-reduce differ in exactly one thing here -- whether the
    section is chunked -- and both go through `worker.build_prompt` /
    `worker.build_reduce_prompt`. Reusing the real prompts is the point: a
    single-pass arm with a hand-written prompt would be measuring the prompt,
    not the architecture.
    """
    spec = amp.ARMS[arm]
    calls = []

    def one(prompt, max_tokens, label):
        guards.health_gate(client, endpoint, f"before {label}")
        text, dt = guards.guarded_complete(client, endpoint, prompt, max_tokens,
                                           label, watchdog)
        tim = client.timings_log[-1] if client.timings_log else {}
        calls.append({"label": label, "wall_s": round(dt, 1),
                      "prompt_n": tim.get("prompt_n"),
                      "prompt_ms": tim.get("prompt_ms"),
                      "predicted_n": tim.get("predicted_n"),
                      "predicted_ms": tim.get("predicted_ms")})
        return text

    if spec["chunk_tokens"] is None:
        # THE CONTROL. One call, the whole section, the map prompt (which is the
        # summarise instruction) at the REDUCE token budget -- see
        # amplification.SECTION_OUTPUT_TOKENS for why the budget and not the
        # prompt is what changes.
        final = one(worker.build_prompt(kind, section_text),
                    worker.REDUCE_MAX_TOKENS, f"{arm} single call")
        chunk_summaries = []
    else:
        chunks = worker.chunk_document(section_text,
                                       chunk_tokens=spec["chunk_tokens"],
                                       overlap_tokens=spec["overlap_tokens"])
        chunk_summaries = []
        for i, c in enumerate(chunks):
            chunk_summaries.append(
                one(worker.build_prompt(kind, c), worker.MAP_MAX_TOKENS,
                    f"{arm} map {i + 1}/{len(chunks)}"))
        if len(chunks) == 1:
            # Should not happen -- candidate_sections rejects such sections --
            # but if chunking ever changes under us, say so rather than
            # silently running the control arm twice and calling it a treatment.
            final = chunk_summaries[0]
            calls.append({"label": "reduce SKIPPED",
                          "note": "one chunk, so there is no reduce step and "
                                  "this arm is the control arm in disguise"})
        else:
            final = one(worker.build_reduce_prompt(kind, chunk_summaries),
                        worker.REDUCE_MAX_TOKENS, f"{arm} reduce")

    return {"arm": arm, "status": "ok", "final": final,
            "chunk_summaries": chunk_summaries, "calls": calls,
            "n_chunks": max(1, len(chunk_summaries))}


def cmd_run(args):
    manifest = _load_manifest(args.out)
    if "ESTIMATED" in (manifest.get("tokenizer") or ""):
        print("REFUSING TO RUN: this manifest was planned with the estimating "
              "tokenizer (plan --dry-run). Section fit was never verified, so "
              "the single-pass arm may silently overflow its slot. Re-plan "
              "against a live endpoint.", file=sys.stderr)
        return 2

    if not args.allow_busy:
        n = amp.running_jobs(args.db)
        if n:
            print(f"REFUSING TO RUN: {n} job(s) are running in the live queue. "
                  "Benchmarking a node while it is doing real work measures "
                  "neither. Wait, or pass --allow-busy if you know the queue is "
                  "pointed elsewhere.", file=sys.stderr)
            return 2

    endpoints = list(args.endpoint or ([manifest["endpoint"]]
                                       if manifest.get("endpoint") else []))
    if not endpoints:
        print("no endpoint: pass --endpoint", file=sys.stderr)
        return 2

    # EVERY ENDPOINT MUST SERVE THE SAME MODEL, and this is checked rather than
    # assumed. Node 2's engine has already flipped mid-session once in this
    # project's history (STATUS.md records it happening while a documentation
    # pass was reading it). A paired experiment whose sections were summarised
    # by two different models is not one experiment, it is two halves of two.
    clients, models = {}, {}
    for ep in endpoints:
        c = worker.LlamaClient(ep)
        clients[ep] = c
        models[ep] = c.model_name()
    if len({m for m in models.values()}) > 1:
        print("REFUSING TO RUN: the endpoints are serving DIFFERENT models: "
              + ", ".join(f"{e} -> {m!r}" for e, m in models.items())
              + ". Every section must be summarised by the same model or the "
                "comparison is between models, not between architectures.",
              file=sys.stderr)
        return 2
    model = next(iter(models.values()), "")

    # SSH hosts line up with endpoints POSITIONALLY, so a two-node run can have
    # F39's CPU watchdog on both. Fewer hosts than endpoints simply means the
    # remainder degrade to finite timeouts, which is the documented fallback.
    watchdogs = {}
    for i, ep in enumerate(endpoints):
        hosts = args.node_ssh_host or []
        watchdogs[ep] = (guards.CpuWatchdog(hosts[i], args.unit)
                         if i < len(hosts) else None)

    docs = {d["id"]: d for d in amp.read_corpus(args.db)}
    results = _load_results(args.out)

    sections = manifest["sections"]
    if args.limit:
        sections = sections[:args.limit]
    # WORK IS DISPATCHED BY SECTION, NEVER BY (SECTION, ARM), and that is the
    # one thing about the fan-out that is not an implementation detail. If arm A
    # of a section ran on node 1 and arm B on node 2, any difference between the
    # nodes would land INSIDE a pair and be indistinguishable from the effect
    # being measured. Keeping a section's arms together means a node difference
    # is common to both halves of the pair and cancels, which is the whole
    # reason the design is paired.
    todo = [s for s in sections
            if any(f"{s['section_id']}|{a}" not in results["runs"]
                   for a in args.arms)]
    print(f"{len(todo)} sections outstanding of {len(sections)}; "
          f"model={model!r}; endpoints={endpoints}")

    pending = queue.Queue()
    for s in todo:
        pending.put(s)
    lock = threading.Lock()
    dead = {}
    done = [0]
    active = [0]     # sections currently being processed by SOME thread

    def _claim():
        """Take a section, or say whether to wait or to stop. Atomic.

        A worker may NOT simply exit when the queue looks empty. A thread that
        loses its endpoint mid-section puts that section BACK, and if the last
        other worker had already drained the queue and returned, the section
        would be orphaned -- the run would report success having silently
        dropped a pair. (Observed: the first version of this fan-out did exactly
        that, and only a test that killed an endpoint mid-section found it.)
        So a worker stops only when the queue is empty AND no other worker is
        still in a position to re-queue. The claim and the active-count
        increment happen under ONE lock, or the same race reappears one level
        down.
        """
        with lock:
            if pending.empty():
                return None, (active[0] == 0)
            active[0] += 1
            return pending.get_nowait(), False

    def worker_thread(ep):
        client, watchdog = clients[ep], watchdogs[ep]
        while True:
            sec, stop = _claim()
            if sec is None:
                if stop:
                    return
                time.sleep(1.0)
                continue
            try:
                _process(ep, client, watchdog, sec)
            except _EndpointDied:
                return
            finally:
                with lock:
                    active[0] -= 1

    def _process(ep, client, watchdog, sec):
        doc = docs.get(sec["doc_id"])
        if doc is None:
            print(f"  SKIP {sec['section_id']}: no longer in corpus")
            return
        text = doc["text"][sec["start_char"]:sec["end_char"]]
        # Hashed and stored with EVERY arm. If a corpus document is ever
        # re-uploaded and re-extracted, a later arm would silently run on
        # different text and the pairing -- the entire basis of the design
        # -- would break with no visible symptom. This makes that loud.
        sha = hashlib.sha256(text.encode()).hexdigest()
        written_here = []
        for arm in args.arms:
            key = f"{sec['section_id']}|{arm}"
            if key in results["runs"]:
                continue
            with lock:
                done[0] += 1
                n = done[0]
            print(f"[{n}] {ep} {sec['section_id']} {arm} "
                  f"({sec['n_tokens']} tok, {sec['n_chunks']} chunks)",
                  flush=True)
            try:
                rec = run_arm(client, ep, text, arm, args.kind, watchdog)
            except guards.BackendDead as exc:
                # This endpoint is gone; the others are not. Put the section
                # back so a live node finishes it, and stop using this one.
                #
                # AND DISCARD THE ARMS THIS THREAD ALREADY FINISHED FOR THIS
                # SECTION. Keeping them would leave the section's arms split
                # across two endpoints, which is precisely the arrangement
                # the by-section dispatch exists to prevent: a node
                # difference would then sit INSIDE a pair, indistinguishable
                # from the effect. Throwing away at most one section's work
                # to protect the pairing is the right trade, and it is
                # announced rather than done quietly.
                print(f"  {ep} IS DEAD: {exc}", file=sys.stderr)
                with lock:
                    dead[ep] = str(exc)
                    for k in written_here:
                        results["runs"].pop(k, None)
                    if written_here:
                        print(f"  discarding {len(written_here)} completed "
                              f"arm(s) of {sec['section_id']} so the whole "
                              "section is redone on ONE endpoint",
                              file=sys.stderr)
                        _save_results(args.out, results)
                    pending.put(sec)
                raise _EndpointDied(ep)
            except Exception as exc:              # noqa: BLE001
                rec = {"arm": arm, "status": "FAILED",
                       "error": f"{type(exc).__name__}: {exc}",
                       "final": None, "chunk_summaries": [], "calls": []}
                print(f"  FAILED: {rec['error']}")
            rec.update({"section_id": sec["section_id"],
                        "doc_id": sec["doc_id"], "section_sha256": sha,
                        "endpoint": ep, "model_name": model,
                        "kind": args.kind,
                        "ran_at": datetime.datetime.now().astimezone()
                        .isoformat(timespec="seconds")})
            with lock:
                results["runs"][key] = rec
                written_here.append(key)
                # Persisted after EVERY arm, not at the end. This run is
                # tens of hours long and has to survive being interrupted.
                _save_results(args.out, results)

    threads = [threading.Thread(target=worker_thread, args=(ep,), name=ep)
               for ep in endpoints]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if dead and len(dead) == len(endpoints):
        print("ABORTED: every endpoint died. " + "; ".join(
            f"{e}: {r}" for e, r in dead.items()), file=sys.stderr)
        return 3
    if dead:
        print(f"WARNING: {len(dead)} of {len(endpoints)} endpoints died; the "
              "rest finished the work. Re-run to pick up anything left.",
              file=sys.stderr)
    if not pending.empty():
        print(f"{pending.qsize()} sections were not attempted. Re-run.",
              file=sys.stderr)
        return 3
    return 0


def cmd_score(args):
    """The cascade, offline. No cluster time, and deliberately its own phase.

    F44: a CPU-bound sidecar measurably starves llama-server on a 4-core node
    even when niced. Scoring an entire run costs seconds, but running it
    alongside `run` would contaminate the very timings the run records and slow
    the inference it depends on.
    """
    manifest = _load_manifest(args.out)
    results = _load_results(args.out)
    docs = {d["id"]: d for d in amp.read_corpus(args.db)}
    by_section = {s["section_id"]: s for s in manifest["sections"]}

    scored = {"schema_version": 1,
              "scorer": "missing_link.cascade tiers 1-2, classifier OFF (F41)",
              "cascade_config": None, "sections": {}}
    n_ok = n_mismatch = 0
    for key, rec in sorted(results["runs"].items()):
        if rec.get("status") != "ok" or not rec.get("final"):
            continue
        sec = by_section.get(rec["section_id"])
        doc = docs.get(rec["doc_id"])
        if sec is None or doc is None:
            continue
        text = doc["text"][sec["start_char"]:sec["end_char"]]
        if hashlib.sha256(text.encode()).hexdigest() != rec.get("section_sha256"):
            print(f"  SKIP {key}: section text has CHANGED since the run. "
                  "Scoring it would compare arms on different text.")
            n_mismatch += 1
            continue
        scope = amp.document_scope(doc["text"], sec,
                                   label=f"the document {doc['filename']!r}")
        ledger = amp.score_output(text, rec["final"], scope=scope,
                                  source={"section_id": rec["section_id"],
                                          "arm": rec["arm"],
                                          "model_name": rec.get("model_name")})
        ep = amp.endpoints(ledger)
        ep["e5_output_chars"] = len(rec["final"])
        ep["e5_n_chunks"] = rec.get("n_chunks")
        entry = scored["sections"].setdefault(
            rec["section_id"], {"doc_id": rec["doc_id"], "arms": {}})
        entry["arms"][rec["arm"]] = {
            "ran_on": rec.get("endpoint"),
            "endpoints": ep,
            "laundering": amp.laundering_decomposition(
                ledger, rec.get("chunk_summaries") or []),
            "adjudication": amp.adjudication_sample(ledger),
        }
        if scored["cascade_config"] is None:
            scored["cascade_config"] = ledger["config"]
        if args.full_ledgers:
            entry["arms"][rec["arm"]]["ledger"] = ledger
        n_ok += 1

    path = os.path.join(args.out, "scored.json")
    with open(path, "w") as fh:
        json.dump(scored, fh, indent=1)
    print(f"scored {n_ok} arm-runs across {len(scored['sections'])} sections"
          + (f"; {n_mismatch} SKIPPED on changed source text" if n_mismatch else ""))
    print(f"-> {path}")
    return 0


def cmd_analyse(args):
    with open(os.path.join(args.out, "scored.json")) as fh:
        scored = json.load(fh)

    pairs, mixed = [], []
    for sid, entry in scored["sections"].items():
        arms = entry["arms"]
        if not all(a in arms for a in args.arms):
            continue
        # A PAIR WHOSE ARMS RAN ON DIFFERENT ENDPOINTS IS EXCLUDED BY DEFAULT.
        # `run` already prevents this within a single invocation, but a run
        # resumed against a different node -- "node 1 today, node 2 tomorrow" --
        # would assemble exactly such pairs, and in the worst case would put one
        # ARM entirely on one node and the other arm entirely on the other,
        # confounding architecture with hardware completely. Excluding them
        # costs n; not excluding them costs the experiment.
        ran_on = {arms[a].get("ran_on") for a in args.arms}
        if len(ran_on) > 1:
            mixed.append({"section_id": sid, "endpoints": sorted(
                str(x) for x in ran_on)})
            if not args.allow_mixed_endpoints:
                continue
        p = {"section_id": sid, "doc_id": entry["doc_id"]}
        for a in args.arms:
            p[a] = arms[a]["endpoints"]
        pairs.append(p)

    a, b = args.arms[0], args.arms[1]
    out = {"schema_version": 1, "arm_a": a, "arm_b": b,
           "n_complete_pairs": len(pairs),
           "mixed_endpoint_pairs": mixed,
           "mixed_endpoint_policy": (
               "included (--allow-mixed-endpoints)" if args.allow_mixed_endpoints
               else "EXCLUDED -- a pair whose two arms ran on different nodes "
                    "puts a hardware difference inside the pair, where it is "
                    "indistinguishable from the architecture difference being "
                    "measured"),
           "endpoints": {}}
    for key in ("e1_number_findings", "e1_number_fabricated", "e1_any",
                "e2_entity_claims", "e2_entity_terms", "e2_any",
                "e4_escalated", "claims"):
        out["endpoints"][key] = amp.paired_summary(pairs, key, arm_a=a, arm_b=b)

    launder = {"inherited": 0, "invented_at_reduce": 0, "sections": 0}
    for entry in scored["sections"].values():
        ld = (entry["arms"].get(b) or {}).get("laundering") or {}
        if ld.get("applicable"):
            launder["sections"] += 1
            launder["inherited"] += ld.get("inherited", 0)
            launder["invented_at_reduce"] += ld.get("invented_at_reduce", 0)
    out["e3_laundering"] = launder
    out["e3_note"] = (
        "`invented_at_reduce` is the failure mode with NO counterpart in the "
        "single-pass arm: a figure absent from the source AND from every chunk "
        "summary, asserted by a reduce step whose entire input was those "
        "summaries. That is F42's exact shape. `inherited` is a map-step "
        "fabrication the reduce step passed through, which is F25's predicted "
        "laundering path.")

    disc = out["endpoints"]["e1_any"]["section_level"]["mcnemar_on_any"]["discordant"]
    need = amp.min_discordant_for_significance()
    out["verdict_guard"] = (
        f"PRIMARY endpoint produced {disc} discordant pairs; {need} is the "
        "minimum that can reach p<0.05. Below that the honest report is "
        "UNDERPOWERED, not 'no amplification'."
        if disc < need else
        "Primary endpoint has enough discordant pairs to be testable.")
    out["adjudication_required"] = (
        "No number in here is a finding until every flagged claim has been read "
        "against its section and marked genuine or false alarm, and a sample of "
        "PASSING claims has been read too. The cascade's 0/978 false-positive "
        "result was established that way and does not transfer for free to a "
        "corpus of statutes and standards, which is clean where that one was "
        "OCR-damaged.")

    path = os.path.join(args.out, "analysis.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)

    print(f"{len(pairs)} complete pairs, {a} vs {b}"
          + (f"  ({len(mixed)} EXCLUDED as mixed-endpoint)" if mixed
             and not args.allow_mixed_endpoints else "") + "\n")
    for key, s in out["endpoints"].items():
        sl, dl = s["section_level"], s["document_level"]
        print(f"  {key:<24} section mean {sl['mean_difference']!s:>8}  "
              f"p={sl['permutation']['p']:.4f}   "
              f"document mean {dl['mean_difference']!s:>8}  "
              f"p={dl['permutation']['p']:.4f}")
    print(f"\n  laundering: {launder['invented_at_reduce']} invented at reduce, "
          f"{launder['inherited']} inherited, over {launder['sections']} sections")
    print(f"\n  {out['verdict_guard']}")
    print(f"\n-> {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("estimate", help="arithmetic only; no network, no DB")
    e.add_argument("--sections", type=int, default=51)
    e.add_argument("--section-tokens", type=int, default=None)
    e.set_defaults(func=cmd_estimate)

    p = sub.add_parser("plan", help="choose sections; writes manifest.json")
    p.add_argument("--endpoint", default="http://127.0.0.1:8080")
    p.add_argument("--per-doc", type=int, default=3)
    p.add_argument("--seed", default=None)
    p.add_argument("--nodes", type=int, default=1)
    p.add_argument("--doc", action="append", help="restrict to these doc ids")
    p.add_argument("--dry-run", action="store_true",
                   help="estimate tokens instead of asking the server; the "
                        "manifest it writes is marked unrunnable")
    p.add_argument("--check-whole", action="store_true",
                   help="also tokenise each WHOLE document, to record how many "
                        "could have been single-passed intact")
    p.add_argument("--arms", nargs="+", default=list(amp.DEFAULT_ARMS),
                   choices=list(amp.ARMS))
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("run", help="the only phase that generates")
    r.add_argument("--endpoint", nargs="+", default=None,
                   help="one or more llama-server endpoints. With more than "
                        "one, sections are dispatched across them -- BOTH ARMS "
                        "OF A SECTION ALWAYS RUN ON THE SAME ENDPOINT, so a "
                        "node difference cancels within the pair instead of "
                        "masquerading as the effect. Measured aggregate on two "
                        "nodes is ~1.8x, not 2x (docs/measurements.md).")
    r.add_argument("--kind", default="summarise", choices=list(worker.PROMPTS))
    r.add_argument("--arms", nargs="+", default=list(amp.DEFAULT_ARMS),
                   choices=list(amp.ARMS))
    r.add_argument("--limit", type=int, default=None,
                   help="first N sections only -- this is the pilot")
    r.add_argument("--node-ssh-host", nargs="+", default=None,
                   help="enables F39's CPU watchdog. Positional against "
                        "--endpoint; endpoints without a host degrade to "
                        "finite per-request timeouts.")
    r.add_argument("--unit", default="llama-server@8080.service")
    r.add_argument("--allow-busy", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="deterministic cascade; no cluster time")
    s.add_argument("--full-ledgers", action="store_true")
    s.set_defaults(func=cmd_score)

    an = sub.add_parser("analyse", help="paired statistics")
    an.add_argument("--arms", nargs=2, default=list(amp.DEFAULT_ARMS),
                    choices=list(amp.ARMS))
    an.add_argument("--allow-mixed-endpoints", action="store_true",
                    help="include pairs whose two arms ran on different nodes. "
                         "Off by default: that puts a hardware difference "
                         "inside the pair.")
    an.set_defaults(func=cmd_analyse)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

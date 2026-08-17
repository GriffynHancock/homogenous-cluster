"""Job worker: map-reduce summarisation against llama-server.

DESIGN NOTE -- the seam that matters later.

CLAUDE.md requires that prompts, chunking and evaluation stay separable from
the queue and the worker, because that seam becomes the skill's task-profile
interface. So this module keeps three things apart:

  * PROMPTS / REDUCE_PROMPTS / CHUNK_TOKENS  -- the task profile (data)
  * chunk_document / build_prompt / summarise -- pure functions over that data
  * run_one / run_forever                     -- queue mechanics, profile-blind

Adding a task profile should mean adding entries to the dicts, never editing
run_one.

WHY MAP-REDUCE, decided on evidence rather than preference:

  * "Lost in the middle" is not fixed by a bigger context window -- accuracy
    drops for material mid-context, and extended-context variants show the same
    position bias (arXiv:2307.03172).
  * CPU prefill collapses with length. Measured on node 1: 33.0 t/s at 512
    tokens, 28.3 at 2048, 24.8 at 2214. Small chunks stay in the efficient band.
  * Map-reduce beats refine decisively on book-length text (arXiv:2310.00785),
    and refine is strictly sequential, so far slower in wall-clock.
  * Chunk size barely matters for map-reduce (unlike refine), so ~4K with 10%
    overlap is fine and is not worth tuning.
"""
import re
import time
import json
import urllib.request

# --- Task profile -----------------------------------------------------------
# Each kind is a (map prompt, reduce prompt) pair. This dict IS the extension
# point; see the design note above.

PROMPTS = {
    "summarise": (
        "Summarise the following text. Be faithful to the source: do not add "
        "facts, opinions or conclusions that are not present in it. If the text "
        "is inconclusive, say so.{instruction}\n\n---\n{document}\n---\n\nSummary:"
    ),
    "report": (
        "Using only the material below, draft a clear, well-structured report. "
        "Do not introduce facts that are not present in the source. Mark any "
        "gaps explicitly rather than filling them.{instruction}\n\n"
        "---\n{document}\n---\n\nReport:"
    ),
    "qa": (
        "Read the following material and extract the facts it contains that a "
        "reader would most likely need. Quote figures exactly. Do not "
        "speculate.{instruction}\n\n---\n{document}\n---\n\nKey facts:"
    ),
}

REDUCE_PROMPTS = {
    "summarise": (
        "Below are summaries of consecutive sections of one document. Combine "
        "them into a single coherent summary. Remove repetition caused by "
        "overlapping sections. Do not add anything not present in the "
        "sections.{instruction}\n\n---\n{summaries}\n---\n\nCombined summary:"
    ),
    "report": (
        "Below are drafted sections of one report, in order. Combine them into "
        "a single coherent report, removing repetition introduced by "
        "overlapping sections. Do not add new material.{instruction}\n\n"
        "---\n{summaries}\n---\n\nCombined report:"
    ),
    "qa": (
        "Below are fact lists extracted from consecutive sections of one "
        "document. Combine them into a single deduplicated list, preserving "
        "figures exactly.{instruction}\n\n---\n{summaries}\n---\n\nCombined facts:"
    ),
}

# ~4K tokens with 10% overlap. Approximated in words: this deliberately does NOT
# tokenise, because doing so would mean shipping the model's tokeniser into the
# queue process. Chunk size barely matters for map-reduce, so an approximation
# is adequate -- but it is an approximation, so the value is conservative.
CHUNK_TOKENS = 4096
OVERLAP_TOKENS = 410
# Empirical ratio for English prose. Under-filling a chunk is safe; overfilling
# it risks a hard HTTP error from the server on prompt-too-long.
WORDS_PER_TOKEN = 0.70

# llama-server on this hardware takes minutes per request, not seconds.
# BOTH LlamaIndex and LangChain default to a 60 s timeout and retry 3-6 times;
# against a multi-minute backend that is a retry storm, not a summary.
DEFAULT_TIMEOUT_S = 3600

# Per-model reasoning suppression. There is NO universal flag, and assuming one
# is how the replication benchmark lost a request to F21.
#
# MEASURED on gpt-oss-120b, 2026-08-17, same prompt, --jinja server:
#   no kwargs                      -> 89 completion tokens
#   {"enable_thinking": false}     -> 129 tokens  (IGNORED -- did nothing)
#   {"reasoning_effort": "low"}    ->  61 tokens  (31% fewer than baseline)
#   {"reasoning_effort": "low"} at max_tokens=80 -> 49 tokens, finish_reason=stop
#
# Confirmed from the template actually embedded in the GGUF (served at /props):
# it mentions `reasoning_effort` 4 times and `enable_thinking` ZERO times. So the
# Qwen-family knob is silently inert on harmony-format models.
#
# For map-reduce the reasoning trace is pure cost -- it is discarded, and on this
# hardware every token costs ~110 ms.
REASONING_KWARGS = {
    "gpt-oss": {"reasoning_effort": "low"},   # harmony format
    "qwen3":   {"enable_thinking": False},    # verified on Qwen3-4B
    "qwen":    {"enable_thinking": False},
    "deepseek": {"enable_thinking": False},   # UNVERIFIED -- same family convention
    "glm":     {"enable_thinking": False},    # UNVERIFIED
}


def reasoning_kwargs_for(model_name):
    """Pick the thinking-suppression kwargs for a model, or {} if unknown.

    An unknown model gets NO kwargs rather than a guess: sending an inert flag
    costs nothing but creates false confidence that thinking is suppressed, and
    then the token budget silently goes to reasoning. Budget generously instead.
    """
    if not model_name:
        return {}
    lowered = str(model_name).lower()
    # Longest key first, so "qwen3" wins over "qwen".
    for family in sorted(REASONING_KWARGS, key=len, reverse=True):
        if family in lowered:
            return dict(REASONING_KWARGS[family])
    return {}


# Token budgets. The old single default of 512 was too small and failed SILENTLY:
# the first real end-to-end run (2026-08-17, Qwen3-4B, a 2057-char document)
# generated exactly 512 tokens, was cut off mid-sentence at "Recommendations
# include implementing automated archival for clinical", and was stored with
# status='done'. See TruncatedCompletion below.
#
# The reduce step gets a larger budget than the map step: it must cover every
# chunk summary, so its output is legitimately longer than any single one.
MAP_MAX_TOKENS = 1024
REDUCE_MAX_TOKENS = 2048


def chunk_document(text, chunk_tokens=CHUNK_TOKENS, overlap_tokens=OVERLAP_TOKENS):
    """Split text into overlapping chunks, measured in whitespace words."""
    if overlap_tokens >= chunk_tokens:
        raise ValueError(
            f"overlap_tokens ({overlap_tokens}) must be less than chunk_tokens "
            f"({chunk_tokens}); otherwise the stride is <= 0 and this loops forever"
        )

    words = text.split()
    size = max(1, int(chunk_tokens * WORDS_PER_TOKEN))
    overlap = int(overlap_tokens * WORDS_PER_TOKEN)
    stride = size - overlap

    if len(words) <= size:
        return [" ".join(words)]

    chunks = []
    start = 0
    while start < len(words):
        chunks.append(" ".join(words[start:start + size]))
        if start + size >= len(words):
            break
        start += stride
    return chunks


def _instruction_clause(instruction):
    """Turn an optional per-job operator instruction into a prompt fragment.

    Returns "" when there is none, so PROMPTS/REDUCE_PROMPTS render byte-for-byte
    identical to before this existed -- the existing tests assert exact prompt
    text and must keep passing unchanged.
    """
    if not instruction or not instruction.strip():
        return ""
    return f" Additional instructions from the operator for this job: {instruction.strip()}"


def build_prompt(kind, document, instruction=None):
    return PROMPTS[kind].format(document=document,
                                instruction=_instruction_clause(instruction))


def build_reduce_prompt(kind, summaries, instruction=None):
    joined = "\n\n".join(
        f"[Section {i + 1}]\n{s}" for i, s in enumerate(summaries)
    )
    return REDUCE_PROMPTS[kind].format(summaries=joined,
                                       instruction=_instruction_clause(instruction))


class LlamaClient:
    """Minimal client for llama-server's OpenAI-compatible endpoint."""

    def __init__(self, base_url, timeout=DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # One entry per completion, in call order. llama-server returns a
        # `timings` object on its OpenAI-compatible endpoint (verified in
        # tools/server/server-task.cpp: prompt_n, prompt_ms, predicted_ms), and
        # `prompt_ms` is the AUTHORITATIVE time-to-first-token.
        #
        # F17: TTFT must never be measured with curl's %{time_starttransfer} --
        # that times the HTTP headers, which llama-server sends immediately, and
        # under-reports by ~5800x (0.015 s reported against a real 89 s). Reading
        # the server's own number avoids the whole trap.
        self.timings_log = []
        # Reasoning-suppression kwargs, resolved once from the server's own
        # /props on first use. Lazy rather than in __init__ so constructing a
        # client never does I/O and never fails because a node is down.
        self._reasoning = None
        # /props itself, cached so _detect_reasoning_kwargs and model_name()
        # share one HTTP round trip instead of each fetching it independently.
        self._props_cache = None

    def _props(self):
        """Fetch and cache GET /props. {} on any failure.

        A node that will not answer /props is a problem for the caller to
        discover on the actual completion request, not here -- so failure
        here is silent and the caller gets an empty dict, never an exception.
        """
        if self._props_cache is not None:
            return self._props_cache
        try:
            req = urllib.request.Request(f"{self.base_url}/props")
            with urllib.request.urlopen(req, timeout=10) as r:
                self._props_cache = json.load(r)
        except Exception:
            self._props_cache = {}
        return self._props_cache

    def _detect_reasoning_kwargs(self):
        """Ask the server which model it is serving, once, and map to a knob.

        llama-server SILENTLY DROPS chat-template kwargs its template does not
        reference -- no error, no warning. So sending the wrong family's knob
        looks exactly like sending the right one, and the budget quietly goes to
        reasoning. That is what cost the replication benchmark a request, so the
        model must be identified rather than assumed.
        """
        if self._reasoning is not None:
            return self._reasoning
        self._reasoning = reasoning_kwargs_for(self.model_name())
        return self._reasoning

    def model_name(self):
        """The model this server is currently serving, per its own /props.

        "" if /props could not be reached -- callers (run_one's resumability
        check) must treat that the same as "unknown", not as a match against
        anything. This is also what makes resuming a job safe: chunk summaries
        are persisted alongside the model that produced them, and a resume is
        only trusted when this matches the recorded value exactly.
        """
        props = self._props()
        return props.get("model_name") or props.get("model_path") or ""

    def assert_reachable(self, timeout=20):
        """Raise unless the server answers /health promptly."""
        try:
            req = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise BackendUnavailable(
                        f"{self.base_url}/health returned {r.status}")
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable(
                f"{self.base_url} did not answer /health within {timeout}s "
                f"({type(exc).__name__}: {exc}). The server may be wedged -- a "
                f"client disconnecting mid-generation can leave it alive but "
                f"serving nothing. Try: sudo systemctl restart llama-server@8080"
            ) from exc

    def complete(self, prompt, max_tokens=MAP_MAX_TOKENS):
        body_dict = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        # Spend the budget on the ANSWER, not the chain of thought. For
        # map-reduce the reasoning trace is pure cost: it is discarded, and on
        # this hardware every token costs ~110 ms. Needs the server on --jinja.
        kwargs = self._detect_reasoning_kwargs()
        if kwargs:
            body_dict["chat_template_kwargs"] = kwargs
        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            payload = json.load(r)

        # Record before extracting, so a truncated or empty completion still
        # leaves evidence of how long the prefill took.
        t = payload.get("timings")
        if isinstance(t, dict):
            self.timings_log.append(t)

        choice = payload["choices"][0]
        return extract_content(choice, max_tokens)


class EmptyCompletion(RuntimeError):
    """The model returned no usable text."""


class BackendUnavailable(RuntimeError):
    """The inference server is not answering. Distinct from a bad completion."""


class JobCancelled(RuntimeError):
    """Raised when a cooperative stop request is observed between chunks.

    HOW FAR "stop a running job" ACTUALLY GOES, stated plainly rather than
    overclaimed: there is no way to interrupt an in-flight HTTP call to
    llama-server from here. That request keeps running server-side regardless
    of what the client does -- closing the socket does not reliably stop
    generation, and llama-server exposes no cancellation endpoint. So a stop
    request cannot preempt whichever chunk is currently being summarised.

    What IS achievable, and is what this implements: the flag is checked
    BETWEEN chunks (and once more before the reduce step), so a stop takes
    effect at the next chunk boundary rather than waiting out the rest of a
    multi-hour document. Chunks already completed are not lost -- they were
    persisted as they finished (see save_chunk_summaries) -- so a stopped job
    can be resumed later exactly like a crashed one.
    """


class TruncatedCompletion(RuntimeError):
    """The model ran out of tokens mid-answer, so the text is incomplete.

    Found by the FIRST real end-to-end run, 2026-08-17. The 41 unit tests all
    use a FakeClient, so this path had never executed against a live server.

    Qwen3-4B, a 2057-char document, max_tokens=512. The server reported
    `eval time = 512 tokens` -- exactly the ceiling -- and the stored summary
    ended mid-sentence on "Recommendations include implementing automated
    archival for clinical". The job was marked **done**.

    This is the same family as EmptyCompletion (F21) and dangerous for the same
    reason: a successful-looking job carrying degraded output, discovered the
    morning after an overnight run. It is arguably worse, because an empty
    summary is obviously wrong while a truncated one looks fine until you check
    the last sentence -- and under map-reduce a truncated CHUNK summary becomes
    source material for the reduce step, where its missing content is
    indistinguishable from content the document never had.
    """


def extract_content(choice, max_tokens):
    """Pull the answer out of a chat completion, refusing to return nothing.

    Reasoning models (Qwen3, DeepSeek-R1 and friends) emit their chain of
    thought into a SEPARATE `reasoning_content` field. If `max_tokens` runs out
    while the model is still thinking, `content` comes back as an EMPTY STRING
    with `finish_reason: "length"` -- a 200 OK carrying nothing.

    Observed on node 1: a 120-token request to Qwen3-4B returned
    `content` of 0 chars and `reasoning_content` of 659. Returning that
    verbatim would have stored an empty summary and marked the job DONE --
    silent data loss discovered the morning after an overnight run, which is
    the single worst failure mode for this project.

    So: fail loudly instead. The queue records it as a failed job with an
    actionable message.
    """
    message = choice.get("message", {})
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    finish = choice.get("finish_reason")

    if content:
        # Non-empty but CUT OFF. Refuse it: an incomplete summary presented as
        # complete is a faithfulness failure, not a formatting one.
        if finish == "length":
            raise TruncatedCompletion(
                f"model hit max_tokens ({max_tokens}) mid-answer -- the text is "
                f"incomplete ({len(content)} chars, ending {content[-60:]!r}). "
                "Raise max_tokens for this kind, or shorten the chunk.")
        # Some servers emit the chain of thought INLINE in content rather than in
        # reasoning_content. Strip it and re-check that an answer remains.
        if "<think>" in content.lower():
            answer = _strip_think(content)
            if not answer:
                raise EmptyCompletion(
                    f"content was ONLY a <think> block ({len(content)} chars), no "
                    f"answer. Disable thinking (enable_thinking=false needs the "
                    f"server started with --jinja) or raise max_tokens "
                    f"({max_tokens}).")
            return answer
        return content

    if reasoning and finish == "length":
        raise EmptyCompletion(
            f"model exhausted max_tokens ({max_tokens}) while still reasoning: "
            f"{len(reasoning)} chars of reasoning_content, 0 of content. "
            "Raise max_tokens, or disable thinking for this model "
            "(e.g. /no_think, or --chat-template-kwargs '{\"enable_thinking\":false}')."
        )
    if finish == "length":
        raise EmptyCompletion(
            f"model hit max_tokens ({max_tokens}) and returned no content.")
    raise EmptyCompletion(
        f"model returned empty content (finish_reason={finish!r}).")


def _first_prefill_s(timings_log):
    """Seconds of prefill on the FIRST completion, from the server's own timings.

    Returns None when unavailable (a client that reports no timings, or a job
    that failed before any call succeeded) so the column stays NULL rather than
    recording a fabricated 0.0.
    """
    if not timings_log:
        return None
    ms = timings_log[0].get("prompt_ms")
    return round(ms / 1000.0, 2) if isinstance(ms, (int, float)) else None


def _strip_think(text):
    """Remove <think>...</think>, including an unclosed trailing block."""
    out, i, low = [], 0, text.lower()
    while True:
        start = low.find("<think>", i)
        if start == -1:
            out.append(text[i:])
            return "".join(out).strip()
        out.append(text[i:start])
        end = low.find("</think>", start)
        if end == -1:
            return "".join(out).strip()
        i = end + 8


def summarise(kind, document, base_url, client,
              max_tokens=MAP_MAX_TOKENS, reduce_max_tokens=REDUCE_MAX_TOKENS,
              instruction=None):
    """Map-reduce over the document. Returns the final text."""
    chunks = chunk_document(document)

    # A single chunk needs no reduce step. Running one anyway would cost a
    # second multi-minute inference pass to summarise a single summary.
    if len(chunks) == 1:
        return client.complete(build_prompt(kind, chunks[0], instruction),
                               max_tokens=max_tokens)

    partials = [
        client.complete(build_prompt(kind, c, instruction), max_tokens=max_tokens)
        for c in chunks
    ]
    # The reduce step must cover every chunk summary, so it legitimately needs a
    # bigger budget than any single map step. Using the same value is how you
    # get a truncated final answer on a long document.
    return client.complete(
        build_reduce_prompt(kind, partials, instruction), max_tokens=reduce_max_tokens)


def count_chunks(document):
    return len(chunk_document(document))


def run_one(db_path, base_url, client=None):
    """Process one job. Returns True if a job was handled, False if idle.

    RESUMABILITY (Part 1). Previously, save_chunk_summaries was called exactly
    once, after summarise_traced returned -- i.e. after the WHOLE document
    (every map chunk, plus reduce) had finished. A job killed mid-document
    (OOM, power cut, an operator stop) lost every completed chunk summary with
    it: a 40-chunk document that died at chunk 39 restarted from zero on the
    next claim. Chunks are now persisted one at a time as they complete (see
    _persist_chunk below), and a job that already has persisted chunks for it
    resumes from them instead of redoing that work -- safe because map outputs
    are independent (chunk N's summary does not depend on chunk M's), PROVIDED
    the same model produced them (see the model check below).
    """
    from missing_link import db

    job = db.claim_next_pending(db_path)
    if job is None:
        return False

    if client is None:
        client = LlamaClient(base_url)

    started = time.monotonic()
    current_model = None       # resolved below; closed over by _persist_chunk
    completed_chunks = [0]     # mutable counter closed over by _persist_chunk

    def _persist_chunk(record):
        db.save_chunk_summaries(db_path, job["id"], [record], model=current_model)
        completed_chunks[0] += 1

    def _should_stop():
        return db.is_cancel_requested(db_path, job["id"])

    try:
        # A wedged llama-server accepts TCP and never answers. Observed
        # 2026-08-17: a client disconnecting mid-generation left the server alive
        # but serving nothing, and Restart=always cannot help because it has not
        # crashed. Without this probe the worker would block for DEFAULT_TIMEOUT_S
        # (an hour) against a dead backend, holding a job in 'running'.
        probe = getattr(client, "assert_reachable", None)
        if probe is not None:
            probe()

        # Identify the current model so a resume can be checked for safety.
        # getattr keeps FakeClient (and any other injected client) working
        # without a model_name() method -- and simply never resumes, which is
        # the safe default (see below).
        model_fn = getattr(client, "model_name", None)
        if model_fn is not None:
            try:
                current_model = model_fn()
            except Exception:
                current_model = None

        n_chunks = count_chunks(job["document"])

        prior = db.get_chunk_summaries(db_path, job["id"])
        resume_records = None
        if prior:
            recorded_model = db.get_recorded_model(db_path, job["id"])
            # The tie goes to caution: only resume when BOTH the recorded and
            # current model are known AND equal. An unknown current model
            # (no model_name(), or /props unreachable) or an unknown recorded
            # model (rows saved before this column existed) means the match
            # cannot be POSITIVELY confirmed -- and mixing chunk summaries
            # produced by two different models into one reduce step is exactly
            # the kind of silent-mixing failure this project keeps getting
            # burned by (F21, F25). Chosen response is "discard and restart"
            # rather than "refuse": an unattended overnight queue should not
            # need an operator to unstick a job just because the server was
            # restarted onto a different model, and restarting the map phase
            # is always correct, just slower than a true resume.
            if recorded_model and current_model and recorded_model == current_model:
                resume_records = [
                    {"index": r["idx"], "start": r["start_char"],
                     "end": r["end_char"], "summary": r["summary"]}
                    for r in prior
                ]
                completed_chunks[0] = len(resume_records)
            else:
                db.delete_chunk_summaries(db_path, job["id"])

        if _should_stop():
            raise JobCancelled("stop requested before any work began")

        # summarise_traced keeps each chunk's identity and offsets, so the final
        # output stays traceable to spans of the source. See "Provenance" above.
        # job.get() rather than job["instruction"]: older rows created before
        # this column existed, and FakeClient-driven tests that build a job dict
        # by hand, must not KeyError here.
        #
        # No trailing save_chunk_summaries: on_chunk_done persists each chunk AS
        # IT COMPLETES. Persisting only after the whole document finished is
        # exactly the bug that made a 40-chunk job dying at chunk 39 restart from
        # zero -- and it is not hypothetical, job 06af2911d7fc lost 10m55s of a
        # 97299-character document with zero chunk_summaries rows to show for it.
        result, chunk_records = summarise_traced(
            job["kind"], job["document"], client,
            instruction=job.get("instruction"),
            resume_records=resume_records,
            on_chunk_done=_persist_chunk,
            should_stop=_should_stop,
        )
        db.complete_job(db_path, job["id"], result, {
            "total_s": round(time.monotonic() - started, 2),
            "chunks": n_chunks,
            "tokens": len(result.split()),
            # ttft_s: the FIRST call's prefill, per the server's own timings.
            # The schema has reserved this column since the beginning and nothing
            # ever filled it, which mattered because prefill is ~79% of document
            # wall-clock on this hardware -- the metric most worth tracking.
            "ttft_s": _first_prefill_s(getattr(client, "timings_log", None)),
        })
    except JobCancelled:
        # Not an error -- see the docstring on JobCancelled for how far a stop
        # actually goes. Chunks completed before the stop are already
        # persisted (via _persist_chunk), so this job is resumable later
        # exactly like a crashed one.
        db.finish_cancelled(db_path, job["id"], {
            "total_s": round(time.monotonic() - started, 2),
            "chunks": completed_chunks[0],
        })
    except Exception as exc:  # noqa: BLE001 -- a failed job must not kill the worker
        # Record and move on. One bad document must not stall every job behind
        # it in a queue whose jobs take hours.
        db.fail_job(db_path, job["id"], f"{type(exc).__name__}: {exc}")

    # Part 3: documented hook point for a future notification integration.
    # Deliberately fired here, after the terminal status is committed, so a
    # future webhook/email body can be built from the job's final state.
    notify_completion(db.get_job(db_path, job["id"]))
    return True


def notify_completion(job):
    """Hook point for a future completion notification (email, webhook, ...).

    A NO-OP TODAY, deliberately: CLAUDE.md rules out adding an SMTP dependency
    or any outbound network call from this project. What Missing Link offers
    instead, right now, is dependency-free and already wired up: the `seen_at`
    column (db.mark_seen / db.mark_all_seen / db.count_unseen) that lets the
    UI show returning users what finished while they were away, without any
    notification channel at all.

    When a real integration is wanted, it plugs in HERE: called from run_one
    with the finished job's full row (id, kind, status, result or error, timing)
    every time a job reaches done/failed/cancelled. Receiving the whole row
    rather than just an id means a webhook payload or email template has
    everything it needs without a second database read.
    """


def run_forever(db_path, base_url, poll_s=5.0):
    from missing_link import db

    # Recover jobs stranded 'running' by a crash. At multi-hour job durations,
    # an OOM kill or power cut mid-job is routine, and a job stuck in 'running'
    # with no process behind it never completes and never reports an error.
    requeued = db.requeue_running(db_path)
    if requeued:
        print(f"requeued {requeued} job(s) stranded by a previous run")

    while True:
        if not run_one(db_path, base_url):
            time.sleep(poll_s)


# --- Preview estimate --------------------------------------------------------
# "When will this be done?" is the single most useful thing an async tool can
# tell a user, and the UI had no answer. See DESIGN-NOTES F.
#
# FALLBACK ONLY, and labelled ESTIMATE wherever surfaced. Derived from
# docs/measurements.md for gpt-oss-120b on ik_llama.cpp, one node:
#   prefill ~24.5 t/s, generation ~5.2 t/s  (F27)
# A 4096-token chunk plus ~200 generated tokens is therefore roughly
#   4096/24.5 + 200/5.2 ~= 167 + 38 ~= 205 s/chunk.
# The moment two real jobs have completed, observed data replaces this.
FALLBACK_SECONDS_PER_CHUNK = 205.0


def estimate_seconds(document, seconds_per_chunk=None):
    """Estimated wall-clock seconds for one document. Returns (seconds, basis).

    basis is "measured" when derived from this cluster's own completed jobs, and
    "estimate" when it fell back to the constant above. Callers MUST surface the
    distinction -- an estimate presented as a measurement is exactly the error
    this repo keeps catching.
    """
    n = count_chunks(document)
    if seconds_per_chunk is None:
        seconds_per_chunk, basis = FALLBACK_SECONDS_PER_CHUNK, "estimate"
    else:
        basis = "measured"
    total = n * seconds_per_chunk
    # A multi-chunk document pays an extra reduce pass over the map summaries.
    if n > 1:
        total += seconds_per_chunk * 0.5
    return total, basis


def humanise_seconds(s):
    """Round to something a non-specialist reads without decoding it."""
    if s is None:
        return "unknown"
    s = float(s)
    if s < 90:
        return f"{s:.0f} seconds"
    if s < 5400:
        return f"{s / 60:.0f} minutes"
    return f"{s / 3600:.1f} hours"


# --- Provenance --------------------------------------------------------------
# chunk_document() returns plain strings, so the map step destroys every chunk's
# identity: the reduce step consumes prose and emits prose, and nothing in the
# final summary can be traced back to the source.
#
# That is a real defect, not a missing nicety, and THREE independent lines of
# reasoning arrived at the same fix:
#   1. DESIGN-NOTES E concession 3 -- RAG systems keep a span per claim and so
#      structurally cannot launder a fabrication through a reduce step; we can.
#   2. docs/EVALUATION.md -- arXiv:2511.07689 found factual-consistency metrics
#      are unreliable at whole-document scope but improve markedly when scored
#      against a correctly-scoped evidence window, ESPECIALLY for legal text. That
#      window is exactly "this chunk summary vs its own chunk".
#   3. DESIGN-NOTES F -- a summary of a legal document with no route back to the
#      source is not usable evidence, whatever its quality.
#
# So chunks carry (index, start_char, end_char) and the per-chunk summaries are
# PERSISTED rather than discarded.

def word_spans(text):
    """[(start_char, end_char)] for every whitespace-delimited token."""
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def chunk_spans(text, chunk_tokens=CHUNK_TOKENS, overlap_tokens=OVERLAP_TOKENS):
    """Chunk with TRUE character offsets into the original text.

    Returns [{"index", "start", "end", "text"}]. Note `text` is sliced from the
    ORIGINAL string rather than rejoined from split words, so original spacing and
    line breaks survive -- which matters when a human is asked to check a claim
    against the source.
    """
    if overlap_tokens >= chunk_tokens:
        raise ValueError(
            f"overlap_tokens ({overlap_tokens}) must be less than chunk_tokens "
            f"({chunk_tokens}); otherwise the stride is <= 0 and this loops forever")

    spans = word_spans(text)
    if not spans:
        return []

    size = max(1, int(chunk_tokens * WORDS_PER_TOKEN))
    overlap = int(overlap_tokens * WORDS_PER_TOKEN)
    stride = size - overlap

    out = []
    start_w = 0
    while start_w < len(spans):
        end_w = min(start_w + size, len(spans))
        s_char = spans[start_w][0]
        e_char = spans[end_w - 1][1]
        out.append({"index": len(out), "start": s_char, "end": e_char,
                    "text": text[s_char:e_char]})
        if end_w >= len(spans):
            break
        start_w += stride
    return out


def summarise_traced(kind, document, client,
                     max_tokens=MAP_MAX_TOKENS, reduce_max_tokens=REDUCE_MAX_TOKENS,
                     instruction=None,
                     resume_records=None, on_chunk_done=None, should_stop=None):
    """Map-reduce that RETAINS provenance, and can RESUME a partial run.

    Returns (final_text, chunk_records) where each record is
    {"index", "start", "end", "summary"} -- enough to show a reader which span of
    the source produced which part of the output, and enough to score each chunk
    summary against its own chunk rather than against the whole document.

    `instruction` is an optional per-job operator note (see build_prompt) applied
    to every map call and to the reduce call, so guidance like "focus on the
    financial terms" shapes both the per-chunk summaries and how they are combined.
    It is part of what makes a resumed chunk reusable or not: see run_one, which
    owns the higher-level safety question.
    resume_records: chunk records already produced by an earlier, interrupted
        attempt at this SAME document. A chunk whose index has an entry here,
        AND whose start/end still match the current chunking of the document,
        is reused instead of re-sent to the model. This is sound (not just
        convenient) because map outputs are independent -- chunk N's summary
        never depends on chunk M's -- so it is fine for some summaries in one
        reduce step to come from an earlier process than others. The
        start/end check is a defensive guard against the (currently
        impossible, but cheap to guard) case of CHUNK_TOKENS/OVERLAP_TOKENS
        changing between the two runs, which would shift chunk boundaries and
        make a stale record wrong even at the same index. Caller (run_one) is
        responsible for the higher-level question of whether resuming is safe
        AT ALL -- i.e. whether the same MODEL produced these records; see
        run_one and db.get_recorded_model.
    on_chunk_done: optional callback(record), invoked right after each NEWLY
        computed chunk (not a resumed one) completes. This is the hook that
        makes resumability possible in the first place -- run_one uses it to
        persist progress to the database one chunk at a time, rather than only
        after summarise_traced returns. See run_one for what that used to mean
        for a job killed mid-document.
    should_stop: optional callable, checked before each chunk that would
        require a real model call (not before a resumed/free one) and once
        more before the reduce step. If it returns True, raises JobCancelled
        immediately rather than starting that call -- see JobCancelled for
        exactly how far this goes.
>>>>>>> worktree-agent-a2c3ef4fcf6c9806f
    """
    chunks = chunk_spans(document)
    if not chunks:
        raise ValueError("document contains no words")

    resume_by_index = {r["index"]: r for r in (resume_records or [])}
    records = []
    for ch in chunks:
        cached = resume_by_index.get(ch["index"])
        if (cached is not None
                and cached["start"] == ch["start"] and cached["end"] == ch["end"]):
            records.append(cached)
            continue
        if should_stop is not None and should_stop():
            raise JobCancelled(
                f"stop requested before chunk {ch['index'] + 1}/{len(chunks)}")
        summary = client.complete(build_prompt(kind, ch["text"], instruction),
                                  max_tokens=max_tokens)
        record = {"index": ch["index"], "start": ch["start"],
                  "end": ch["end"], "summary": summary}
        records.append(record)
        if on_chunk_done is not None:
            on_chunk_done(record)

    if len(records) == 1:
        return records[0]["summary"], records

    if should_stop is not None and should_stop():
        raise JobCancelled("stop requested before the reduce step")

    final = client.complete(
        build_reduce_prompt(kind, [r["summary"] for r in records], instruction),
        max_tokens=reduce_max_tokens)
    return final, records

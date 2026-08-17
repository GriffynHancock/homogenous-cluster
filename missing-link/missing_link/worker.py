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
import http.client
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Sibling module, no heavy imports at its top level (pypdf is imported lazily
# inside its own functions). Named here so classify_failure can treat an
# unreadable upload as a PERMANENT failure by intent rather than by default.
from missing_link.extract import ExtractionError

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
# BOTH LlamaIndex and LangChain default to a 60 s timeout; against a
# multi-minute backend that is a retry storm, not a summary. Corrected
# 2026-08-18: max_retries defaults to 2 in the underlying OpenAI/Anthropic
# SDKs both libraries build on, not the 3-6 originally assumed here -- the
# retry-storm risk is still real, since each retry re-waits the full timeout.
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


# --- Guidance length guard ----------------------------------------------------
# Operator guidance -- typed, or extracted from an uploaded file (a style
# guide, a report template, a question list) -- is embedded via `{instruction}`
# in build_prompt/build_reduce_prompt (_instruction_clause). That means it is
# repeated on EVERY map call, once per chunk, not once per document -- so it
# must fit, alongside ONE chunk and the model's own output, inside a single
# llama-server SLOT, not inside the whole context window. A 40-page style
# guide "multiplied across 26 chunks" does not cost 26x the tokens (each
# request is independent), but it DOES have to fit in the slot 26 times over,
# and if it does not fit even once, every one of those 26 requests either
# errors or (worse) silently drops the start of the chunk to make room.
#
# n_ctx_slot is NOT `-c / --parallel` by assumption -- CLAUDE.md is explicit
# that this must be read from the server's own startup log, not inferred:
#   journalctl -u llama-server@8080 | grep n_ctx_slot
# Confirmed on node 1, 2026-08-18 (most recent server startup, `-c 32768
# --parallel 4`): id_slot 0-3 all report n_ctx_slot=8192.
N_CTX_SLOT = 8192

# The static template text around {document}/{instruction} in PROMPTS /
# REDUCE_PROMPTS -- roughly 35-45 words per kind (~50-65 tokens at
# WORDS_PER_TOKEN). Rounded up well past that so this guard never undercounts
# and blames the wrong thing when a request is still rejected.
_PROMPT_WRAPPER_TOKENS = 150

# One slot must hold, at once: one chunk (CHUNK_TOKENS) + the guidance
# (repeated on every map call) + the model's own answer (MAP_MAX_TOKENS) + the
# wrapper text. Solve for the guidance budget, then keep the project's
# standard 15% safety margin (CLAUDE.md: "leave 15% memory headroom... do not
# spec configurations that fit only marginally") rather than spend the
# arithmetic ceiling exactly -- KV growth and the reduce step (which embeds
# the guidance once more, alongside every chunk summary) both draw on the same
# slot.
_INSTRUCTION_HEADROOM_TOKENS = (
    N_CTX_SLOT - CHUNK_TOKENS - MAP_MAX_TOKENS - _PROMPT_WRAPPER_TOKENS)
_ARCH_SAFETY_MARGIN = 0.85  # the standard 15% headroom above

# WORDS_PER_TOKEN (0.70) IS UNDER MEASUREMENT AND KNOWN OPTIMISTIC, separately
# from the architecture margin above -- do not read it as settled. A real PDF
# measured ~0.62 words/token (STATUS.md), and the chunk-size sweep in flight on
# node 2 is showing llama-server's own prompt_n running ~30% over what the
# words-per-token estimate predicted for the same chunk. Since this guard
# converts a word count to an assumed token count via WORDS_PER_TOKEN, an
# optimistic WORDS_PER_TOKEN makes the guard let through MORE words than
# actually fit -- the unsafe direction, for a check whose entire job is
# refusing what does not fit. This factor is separate insurance against THAT,
# not a substitute for the real fix: MAX_INSTRUCTION_WORDS is derived FROM
# WORDS_PER_TOKEN below, so when the sweep lands a corrected (lower) value,
# this guard tightens automatically without needing to change. Do not fold
# this into _ARCH_SAFETY_MARGIN above -- they guard different things and
# should be able to move independently (this one goes away once
# WORDS_PER_TOKEN is corrected; the architecture one does not).
_TOKENIZER_UNCERTAINTY_MARGIN = 0.75

MAX_INSTRUCTION_TOKENS = int(
    _INSTRUCTION_HEADROOM_TOKENS * _ARCH_SAFETY_MARGIN * _TOKENIZER_UNCERTAINTY_MARGIN)
MAX_INSTRUCTION_WORDS = int(MAX_INSTRUCTION_TOKENS * WORDS_PER_TOKEN)


class GuidanceTooLong(ValueError):
    """Operator guidance (typed, or extracted from a file) would not fit in
    one context slot alongside a document chunk and its output.

    Refused, not truncated -- for the same reason extract.py refuses a file it
    cannot read rather than degrading it (F38): a silently shortened style
    guide is a plausible-looking input that quietly means something other than
    what the operator gave it, and the operator has no way to notice.
    """


def check_instruction_length(instruction):
    """Raise GuidanceTooLong if `instruction` would not fit the per-chunk
    budget. No-op for empty/None guidance -- there is nothing to check."""
    if not instruction or not instruction.strip():
        return
    words = len(instruction.split())
    if words > MAX_INSTRUCTION_WORDS:
        raise GuidanceTooLong(
            f"guidance is about {words} words (~{int(words / WORDS_PER_TOKEN)} "
            f"tokens); the limit is {MAX_INSTRUCTION_WORDS} words "
            f"(~{MAX_INSTRUCTION_TOKENS} tokens) so it still fits in one "
            f"context slot (n_ctx_slot={N_CTX_SLOT}) alongside a document "
            f"chunk ({CHUNK_TOKENS} tokens) and its output ({MAP_MAX_TOKENS} "
            "tokens) -- it is repeated on every chunk, not sent once. Shorten "
            "it, or trim it down to the parts that matter most.")


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


# --- Retry policy: transient vs permanent ------------------------------------
# THE PROBLEM THIS SOLVES. Until now every exception escaping the pipeline took
# the same exit: run_one's broad `except` -> db.fail_job -> terminal. So the
# overnight sequence was "the backend dies at 02:00, the watchdog restarts it at
# 02:05, and nothing happens until morning" -- with, since chunk summaries are
# now persisted as they complete, a pile of finished work sitting unused in the
# database. That happened twice in two days (F39, and ik_llama.cpp fatal-erroring
# in its flash-attention kernel on node 2).
#
# THE DANGER OF THE OBVIOUS FIX. A blanket retry is worse than no retry. A
# document that cannot be chunked, a guidance note that is too long, a 400 from
# the server: retrying those reproduces them exactly, forever, on a queue nobody
# is watching. So failures are classified, and ONLY the transient class is
# retried.
#
# TRANSIENT means "the infrastructure moved under a document that was fine":
# the backend died mid-request, refused the connection, or never answered.
# PERMANENT means "the input or the request is wrong", and no number of retries
# will change that.
#
# AN UNRECOGNISED EXCEPTION IS PERMANENT. This is the same discipline as
# reasoning_kwargs_for returning {} for a model it does not know rather than
# guessing a knob: a mystery error retried all night is worse than one that
# stops and is visible in the morning. Widening the transient class is a
# deliberate act, done by naming a type here.

_TRANSIENT_EXCEPTIONS = (
    # Our own pre-flight probe (F36). The backend is up as a process and
    # answering TCP, but not answering /health -- wedged, restarting, or
    # reloading 65 GB of model.
    BackendUnavailable,
    # ConnectionResetError / ConnectionRefusedError / ConnectionAbortedError /
    # BrokenPipeError. http.client.RemoteDisconnected is a ConnectionResetError
    # too, so this is the class that catches "the server went away mid-request"
    # -- exactly what destroyed job 06af2911d7fc when the watchdog restarted the
    # unit under it.
    ConnectionError,
    # RemoteDisconnected (again, via BadStatusLine), IncompleteRead,
    # CannotSendRequest and friends. Every member of this hierarchy is a
    # transport-level failure of the HTTP conversation, never a statement about
    # the document.
    http.client.HTTPException,
    # socket.timeout is an alias for the builtin TimeoutError on 3.10+. The
    # request outlived DEFAULT_TIMEOUT_S against a server that may well come
    # back -- and on this hardware "slow" and "dead" are genuinely hard to tell
    # apart from the client side (F39), so the benefit of the doubt goes to
    # retrying, bounded.
    TimeoutError,
    # urllib wraps socket-level errors (refused, DNS, unreachable) in URLError.
    # NOTE: HTTPError is a SUBCLASS of URLError and is decided by status code
    # in classify_failure BEFORE this tuple is consulted.
    urllib.error.URLError,
)

_PERMANENT_EXCEPTIONS = (
    # F21 and F34. Both are TOKEN-BUDGET failures, and this is the one
    # classification worth arguing for explicitly, because at first glance they
    # look retryable -- generation is stochastic, so surely another roll of the
    # dice might fit?
    #
    # No, for three reasons:
    #   1. A retry re-sends the identical prompt with the identical max_tokens
    #      to the identical model. The overwhelmingly likely outcome is the
    #      identical failure, bought at the price of a full prefill -- ~79% of
    #      document wall-clock on this hardware. Repeating identical work
    #      identically and hoping is the definition of waste.
    #   2. The circumstance in which a retry WOULD help is a model change --
    #      and that is precisely the circumstance in which the persisted chunk
    #      summaries are discarded (run_one's model check), so the "retry" is a
    #      full restart of the document anyway. An operator resubmitting after
    #      changing the model gets that, with full visibility.
    #   3. These guards exist to make a specific class of silent degradation
    #      VISIBLE (F21/F34/F38: a plausible-looking result that is worthless).
    #      Automatically retrying them buries the signal in a retry loop, which
    #      is the opposite of what they are for.
    EmptyCompletion,
    TruncatedCompletion,
    # Guidance that does not fit alongside a chunk in one slot. Deterministic in
    # the input; the operator must shorten it.
    GuidanceTooLong,
    # "document contains no words" (summarise_traced), and the chunking config
    # guard in chunk_spans. Both are statements about the input.
    ValueError,
    # An upload that could not be turned into text. Normally raised at submit
    # time, before a job exists, but named here so that if it ever reaches the
    # worker it is classified by intent rather than by the default.
    ExtractionError,
)

# Total STARTS allowed per job, counted by db.claim_next_pending. 4 means the
# original attempt plus three retries. The bound matters more than its exact
# value: it is what stops a permanently-broken backend turning the queue into a
# log-filling retry loop, and what guarantees the operator eventually sees a
# terminal state with an explanation rather than a job that has been "about to
# work" all night.
MAX_ATTEMPTS = 4

# Backoff between retries, doubling and capped: 60s, 120s, 240s. Deliberately
# not longer, because the PRIMARY defence against spinning is elsewhere and is
# stronger -- app._worker_loop probes /health before claiming anything, so
# while the backend is actually down no attempt is consumed at all; the job
# simply is not claimed. This backoff covers the nastier case the health probe
# cannot see: a server that answers /health and then fails every completion.
RETRY_BACKOFF_BASE_S = 60
RETRY_BACKOFF_CAP_S = 600


def classify_failure(exc):
    """"transient" (worth retrying) or "permanent" (never). Unknown -> permanent."""
    if isinstance(exc, urllib.error.HTTPError):
        # 5xx is the server failing at its job; 429 is it asking us to wait.
        # Everything else in the 4xx range -- a 400 from a malformed request, a
        # 404 from a wrong path -- is our request being wrong, and will be
        # exactly as wrong next time.
        return "transient" if (exc.code >= 500 or exc.code == 429) else "permanent"
    if isinstance(exc, _PERMANENT_EXCEPTIONS):
        return "permanent"
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return "transient"
    return "permanent"


def is_recognised_failure(exc):
    """True when classify_failure matched a NAMED type rather than defaulting.

    Kept separate from the classification itself so the operator-facing message
    can distinguish "we know what this is and it is not worth retrying" from
    "we have never seen this before, so we stopped rather than looping on it".
    Those are different things to read at 8am and they warrant different next
    steps.
    """
    return isinstance(exc, (urllib.error.HTTPError,)
                      + _PERMANENT_EXCEPTIONS + _TRANSIENT_EXCEPTIONS)


def retry_delay_seconds(attempts_so_far):
    """Seconds to wait before attempt `attempts_so_far` + 1. Doubling, capped."""
    n = max(1, int(attempts_so_far))
    return min(RETRY_BACKOFF_CAP_S, RETRY_BACKOFF_BASE_S * (2 ** (n - 1)))


def retry_error_message(exc, attempts, delay_s, chunks_done):
    """What the job page shows while a job is waiting to be retried."""
    kept = (f" The {chunks_done} chunk summar"
            f"{'y' if chunks_done == 1 else 'ies'} already completed are kept "
            f"and will be reused, so the retry resumes rather than restarts."
            if chunks_done else "")
    return (f"attempt {attempts} of {MAX_ATTEMPTS} failed and will be retried in "
            f"{delay_s}s. The inference backend went away -- this is not a problem "
            f"with the document.{kept} Last error: {type(exc).__name__}: {exc}")


def final_error_message(exc, attempts, chunks_done, endpoint, retried):
    """What the job page shows once a job has failed FOR GOOD.

    The distinction this has to carry, at 8am, to somebody who was asleep for
    all of it: "your document is bad" versus "the cluster was broken all
    night". Those need different actions from the operator, and a bare
    "TypeError: ..." tells them neither.
    """
    detail = f"{type(exc).__name__}: {exc}"
    kept = (f" The {chunks_done} chunk summar"
            f"{'y' if chunks_done == 1 else 'ies'} completed before the last "
            f"failure are kept on disk and would be reused by a further attempt."
            if chunks_done else "")
    if retried:
        return (f"FAILED AFTER {attempts} ATTEMPTS. Every attempt ended with the "
                f"inference backend unreachable or dying mid-document, so this is "
                f"a CLUSTER problem, not a problem with this document -- check "
                f"llama-server on {endpoint or 'the endpoint that ran it'} before "
                f"resubmitting.{kept} Last error: {detail}")
    if is_recognised_failure(exc):
        return (f"{detail} -- NOT RETRIED: this is a problem with the document or "
                f"the request, and retrying it would reproduce it exactly.{kept}")
    return (f"{detail} -- NOT RETRIED: this failure is not one Missing Link "
            f"recognises, so it was stopped rather than looped on. An unknown "
            f"error retried all night is worse than one that stops and is "
            f"visible.{kept}")


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


def _last_timings(client):
    """The server's own `timings` object for the MOST RECENT client.complete()
    call, as {"prompt_n", "prompt_ms", "predicted_n", "predicted_ms"} (only
    the keys actually present), or {} if the client never reported timings.

    Used to attach per-chunk prefill/generation numbers to a chunk record as
    it is produced -- see summarise_traced's map loop and chunk_rate_stats,
    which derives live tok/s and a per-job ETA from these. Deliberately reads
    only LlamaClient.timings_log (already populated from llama-server's own
    response, never from wall-clock -- see LlamaClient.complete). {} rather
    than raising for a client with no such attribute (FakeClient in most
    tests) or with nothing logged yet.
    """
    log = getattr(client, "timings_log", None)
    if not log:
        return {}
    t = log[-1]
    if not isinstance(t, dict):
        return {}
    return {k: t[k] for k in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms")
            if k in t}


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


def run_one(db_path, base_url, client=None, on_claim=None):
    """Process one job. Returns True if a job was handled, False if idle.

    `on_claim`, if given, is called with the claimed job dict right after the
    atomic claim succeeds. Optional and additive -- existing callers are
    unaffected. It exists for job-level fan-out (see app.py's per-endpoint
    worker loop): the loop needs to know which job a given endpoint is
    currently working on for the status page, and this is cheaper and safer
    than a second query racing against the worker that already holds the row.

    RESUMABILITY. Previously, save_chunk_summaries was called exactly
    once, after summarise_traced returned -- i.e. after the WHOLE document
    (every map chunk, plus reduce) had finished. A job killed mid-document
    (OOM, power cut, an operator stop) lost every completed chunk summary with
    it: a 40-chunk document that died at chunk 39 restarted from zero on the
    next claim. Chunks are now persisted one at a time as they complete (see
    _persist_chunk below), and a job that already has persisted chunks for it
    resumes from them instead of redoing that work -- safe because map outputs
    are independent (chunk N's summary does not depend on chunk M's), PROVIDED
    the same model produced them (see the model check below).

    RETRIES. Resumability only pays off if something actually resumes. Nothing
    did: every failure went to db.fail_job and stayed there, so a backend that
    died at 02:00 left a terminal job and a pile of completed chunk summaries
    that no watchdog restart could ever pick up. A TRANSIENT failure (see
    classify_failure) now returns the job to 'pending' with a backoff, up to
    MAX_ATTEMPTS starts in total; a PERMANENT one still fails immediately. A
    retried job then takes the resume path above, so the retry costs only the
    chunks that were still outstanding.
    """
    from missing_link import db

    job = db.claim_next_pending(db_path)
    if job is None:
        return False
    if on_claim is not None:
        on_claim(job)
    # Persisted immediately, not just held in the in-memory ENDPOINT_STATE
    # (app.py): that dict is cleared the moment this worker moves on, so a
    # FAILED job would otherwise lose all record of which node it died on --
    # and under fan-out, "which endpoint" is the difference between "1/R of
    # throughput is gone" and "the whole cluster is mysteriously slower".
    db.set_job_endpoint(db_path, job["id"], base_url)

    if client is None:
        client = LlamaClient(base_url)

    started = time.monotonic()
    current_model = None       # resolved below; closed over by _persist_chunk
    completed_chunks = [0]     # mutable counter closed over by _persist_chunk

    def _persist_chunk(record):
        db.save_chunk_summaries(db_path, job["id"], [record], model=current_model,
                                instruction=job.get("instruction"))
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
            model_ok = bool(recorded_model) and bool(current_model) \
                and recorded_model == current_model
            # SAME REASONING, for the operator's guidance rather than the
            # model. `instruction` is embedded in every map call (build_prompt)
            # exactly like the document chunk is, so it is just as capable of
            # making two runs' chunk summaries incompatible. Unlike the model
            # check, "no instruction" (None) is trusted when BOTH sides agree
            # it is None -- that was already true of every job before this
            # column existed, so it is not new information to distrust; what
            # is untrustworthy is DISAGREEMENT or an unconfirmable record
            # (get_recorded_instruction's first element being False).
            instr_ok, recorded_instruction = db.get_recorded_instruction(
                db_path, job["id"])
            instruction_ok = instr_ok and recorded_instruction == job.get("instruction")
            if model_ok and instruction_ok:
                resume_records = [
                    {"index": r["idx"], "start": r["start_char"],
                     "end": r["end_char"], "summary": r["summary"]}
                    for r in prior
                ]
                completed_chunks[0] = len(resume_records)
            else:
                db.delete_chunk_summaries(db_path, job["id"])

        # Recorded EXPLICITLY every attempt, including as 0, so the job page can
        # state whether this attempt resumed or restarted rather than inferring
        # it from the presence of chunk rows -- which would be wrong exactly
        # when it matters, since the branch above DELETES those rows when the
        # resume is rejected. This is the number that says whether retrying was
        # worth doing at all.
        db.record_resume(db_path, job["id"], len(resume_records or ()))

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
        #
        # BUT: "record and move on" used to mean TERMINAL, for every failure
        # equally -- so a backend that died at 02:00 and was restarted by the
        # watchdog at 02:05 left a failed job and a pile of completed chunk
        # summaries that nothing would ever pick up again. Transient failures
        # are now returned to the queue, bounded and backed off; permanent ones
        # still fail immediately, because retrying them is pure waste and, on an
        # unattended queue, an infinite loop. See classify_failure.
        attempts = int(job.get("attempts") or 1)
        done_chunks = completed_chunks[0]
        if classify_failure(exc) == "transient" and attempts < MAX_ATTEMPTS:
            delay = retry_delay_seconds(attempts)
            db.schedule_retry(
                db_path, job["id"],
                retry_error_message(exc, attempts, delay, done_chunks),
                _retry_at(delay))
            # No notify_completion: this job has NOT reached a terminal state,
            # and notify_completion's contract (see its docstring) is that it
            # fires once, on done/failed/cancelled. Firing it here would email
            # the operator about a job that is about to run again.
            return True
        db.fail_job(db_path, job["id"], final_error_message(
            exc, attempts, done_chunks, job.get("endpoint") or base_url,
            retried=attempts > 1))

    # Part 3: documented hook point for a future notification integration.
    # Deliberately fired here, after the terminal status is committed, so a
    # future webhook/email body can be built from the job's final state.
    notify_completion(db.get_job(db_path, job["id"]))
    return True


def _retry_at(delay_s):
    """ISO-8601 UTC timestamp `delay_s` in the future, for db.schedule_retry.

    Same format as db._now() writes, because claim_next_pending compares the
    two as STRINGS -- which is sound only while both are UTC-aware isoformat
    output (identical width, identical '+00:00' suffix).
    """
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_s)).isoformat()


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


def remaining_seconds(n_chunks, chunks_done, reduce_pending,
                      seconds_per_chunk=None, basis=None):
    """Estimated seconds LEFT for a job already in flight. Returns (seconds, basis).

    Distinct from estimate_seconds, which predicts a whole document from
    scratch before it starts. This one is calibrated against progress already
    made -- chunks_done comes from persisted chunk_summaries rows, which is
    real per-chunk progress, not a guess -- for the live-progress poll (see
    app.py's /jobs/{id}/progress).

    `basis` labels WHICH rate the caller is passing, and must be preserved
    through to display, never collapsed into a single "measured" bucket --
    an inferred number must look inferred, and (per the operator's own
    request) a rate calibrated on THIS job's own first couple of chunks is a
    materially weaker claim than the cluster-wide average, which is in turn
    weaker than nothing. Three tiers, caller's choice:
      "job"      -- THIS job's own chunk timings (see chunk_rate_stats)
      "measured" -- the cluster-wide average (db.seconds_per_chunk)
      "estimate" -- the benchmark fallback constant (seconds_per_chunk=None
                    always forces this, regardless of what basis was passed --
                    there is nothing else it could mean).

    reduce_pending: True once every map chunk is done but the reduce call (a
    real, separately-timed inference call) has not returned yet -- there is no
    finer-grained signal than that available from persisted state alone.
    """
    if seconds_per_chunk is None:
        seconds_per_chunk, basis = FALLBACK_SECONDS_PER_CHUNK, "estimate"
    elif basis is None:
        basis = "measured"
    remaining_chunks = max(n_chunks - chunks_done, 0)
    total = remaining_chunks * seconds_per_chunk
    if n_chunks > 1 and (remaining_chunks > 0 or reduce_pending):
        total += seconds_per_chunk * 0.5
    return total, basis


# Below this many of THIS JOB's own chunks have real timings, its own rate is
# too noisy to trust over the cluster-wide average -- the same min_samples
# posture db.seconds_per_chunk takes for the cluster figure. A per-job rate
# from one sample is just that one chunk's random variance, not a rate.
MIN_JOB_TIMED_CHUNKS = 2


def chunk_rate_stats(timings):
    """Turn a job's OWN persisted per-chunk timings (db.get_chunk_timings)
    into live-progress numbers. Returns None if there is nothing timed yet --
    e.g. every chunk so far was resumed from an earlier run, or the client in
    use does not report timings (tests' FakeClient) -- rather than fabricate a
    rate from zero real samples.

    PREFILL AND GENERATION ARE KEPT SEPARATE, deliberately, never blended into
    one tok/s: they run at very different speeds on this hardware (measured:
    prefill ~16-25 t/s, generation ~5-6 t/s; prefill is ~79% of document
    wall-clock, F27), so a single blended number would be dominated by
    whichever phase happened to be running when it was read, and would mean a
    different thing every time it was sampled.

    Returns a dict:
      n_timed            -- how many of this job's chunks have real timings
      last_prefill_tok_s / last_gen_tok_s  -- from the MOST RECENTLY
          completed chunk only (there is no partial/in-flight number to show:
          LlamaClient sends stream=false, so a number only exists once a call
          returns)
      avg_prefill_tok_s / avg_gen_tok_s    -- pooled across every timed chunk
          so far (sum of tokens / sum of milliseconds, not a mean of ratios --
          correct when chunks vary in size)
      seconds_per_chunk   -- mean (prompt_ms + predicted_ms) per timed chunk,
          for remaining_seconds's ETA math; None if nothing is timed
    """
    if not timings:
        return None

    def rate(n, ms):
        return (n / (ms / 1000.0)) if n and ms else None

    last = timings[-1]
    total_prompt_n = sum(t.get("prompt_n") or 0 for t in timings)
    total_prompt_ms = sum(t.get("prompt_ms") or 0 for t in timings)
    total_pred_n = sum(t.get("predicted_n") or 0 for t in timings)
    total_pred_ms = sum(t.get("predicted_ms") or 0 for t in timings)
    per_chunk_s = [
        ((t.get("prompt_ms") or 0) + (t.get("predicted_ms") or 0)) / 1000.0
        for t in timings
    ]

    return {
        "n_timed": len(timings),
        "last_prefill_tok_s": rate(last.get("prompt_n"), last.get("prompt_ms")),
        "last_gen_tok_s": rate(last.get("predicted_n"), last.get("predicted_ms")),
        "avg_prefill_tok_s": rate(total_prompt_n, total_prompt_ms),
        "avg_gen_tok_s": rate(total_pred_n, total_pred_ms),
        "seconds_per_chunk": (sum(per_chunk_s) / len(per_chunk_s)) if per_chunk_s else None,
    }


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
        # The server's own timings for THIS call, if the client reports them --
        # never fabricated, never derived from wall-clock (F17: a wall-clock
        # TTFT once reported 0.015s against a real 89s, off by ~5800x, and
        # survived because nothing cross-checked it against the server log).
        # Absent for a resumed chunk (this branch never runs for one) and for
        # any client that does not expose timings_log (e.g. FakeClient in most
        # tests) -- record.get(...) downstream then sees None, not a fabricated
        # zero, per this project's standing rule (ttft_s is None, not 0.0).
        record.update(_last_timings(client))
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

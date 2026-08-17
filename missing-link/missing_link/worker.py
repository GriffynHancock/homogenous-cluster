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
        "is inconclusive, say so.\n\n---\n{document}\n---\n\nSummary:"
    ),
    "report": (
        "Using only the material below, draft a clear, well-structured report. "
        "Do not introduce facts that are not present in the source. Mark any "
        "gaps explicitly rather than filling them.\n\n"
        "---\n{document}\n---\n\nReport:"
    ),
    "qa": (
        "Read the following material and extract the facts it contains that a "
        "reader would most likely need. Quote figures exactly. Do not "
        "speculate.\n\n---\n{document}\n---\n\nKey facts:"
    ),
}

REDUCE_PROMPTS = {
    "summarise": (
        "Below are summaries of consecutive sections of one document. Combine "
        "them into a single coherent summary. Remove repetition caused by "
        "overlapping sections. Do not add anything not present in the "
        "sections.\n\n---\n{summaries}\n---\n\nCombined summary:"
    ),
    "report": (
        "Below are drafted sections of one report, in order. Combine them into "
        "a single coherent report, removing repetition introduced by "
        "overlapping sections. Do not add new material.\n\n"
        "---\n{summaries}\n---\n\nCombined report:"
    ),
    "qa": (
        "Below are fact lists extracted from consecutive sections of one "
        "document. Combine them into a single deduplicated list, preserving "
        "figures exactly.\n\n---\n{summaries}\n---\n\nCombined facts:"
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


def build_prompt(kind, document):
    return PROMPTS[kind].format(document=document)


def build_reduce_prompt(kind, summaries):
    joined = "\n\n".join(
        f"[Section {i + 1}]\n{s}" for i, s in enumerate(summaries)
    )
    return REDUCE_PROMPTS[kind].format(summaries=joined)


class LlamaClient:
    """Minimal client for llama-server's OpenAI-compatible endpoint."""

    def __init__(self, base_url, timeout=DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, prompt, max_tokens=512):
        body = json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            payload = json.load(r)

        choice = payload["choices"][0]
        return extract_content(choice, max_tokens)


class EmptyCompletion(RuntimeError):
    """The model returned no usable text."""


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
    if content:
        return content

    reasoning = (message.get("reasoning_content") or "").strip()
    finish = choice.get("finish_reason")

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


def summarise(kind, document, base_url, client, max_tokens=512):
    """Map-reduce over the document. Returns the final text."""
    chunks = chunk_document(document)

    # A single chunk needs no reduce step. Running one anyway would cost a
    # second multi-minute inference pass to summarise a single summary.
    if len(chunks) == 1:
        return client.complete(build_prompt(kind, chunks[0]), max_tokens=max_tokens)

    partials = [
        client.complete(build_prompt(kind, c), max_tokens=max_tokens)
        for c in chunks
    ]
    return client.complete(
        build_reduce_prompt(kind, partials), max_tokens=max_tokens)


def count_chunks(document):
    return len(chunk_document(document))


def run_one(db_path, base_url, client=None):
    """Process one job. Returns True if a job was handled, False if idle."""
    from missing_link import db

    job = db.claim_next_pending(db_path)
    if job is None:
        return False

    if client is None:
        client = LlamaClient(base_url)

    started = time.monotonic()
    try:
        n_chunks = count_chunks(job["document"])
        result = summarise(job["kind"], job["document"], base_url, client)
        db.complete_job(db_path, job["id"], result, {
            "total_s": round(time.monotonic() - started, 2),
            "chunks": n_chunks,
            "tokens": len(result.split()),
        })
    except Exception as exc:  # noqa: BLE001 -- a failed job must not kill the worker
        # Record and move on. One bad document must not stall every job behind
        # it in a queue whose jobs take hours.
        db.fail_job(db_path, job["id"], f"{type(exc).__name__}: {exc}")
    return True


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

import os
import tempfile
import pytest
from missing_link import db, worker


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


class FakeClient:
    """Stands in for the llama-server HTTP client.

    Records prompts so tests can assert on map-reduce structure without a
    multi-minute round trip against a real model.
    """

    def __init__(self, replies=None, fail_with=None):
        self.prompts = []
        # Token budget per call. Needed to assert that the reduce step gets a
        # larger budget than the map step -- using one value for both is how a
        # long document ends in a truncated final answer.
        self.budgets = []
        self.replies = replies
        self.fail_with = fail_with
        self.calls = 0

    def complete(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        self.budgets.append(max_tokens)
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        if self.replies is None:
            return f"summary-{self.calls}"
        return self.replies[(self.calls - 1) % len(self.replies)]


# --- prompt construction ----------------------------------------------------

def test_build_prompt_includes_document():
    assert "MY DOCUMENT" in worker.build_prompt("summarise", "MY DOCUMENT")


def test_build_prompt_differs_by_kind():
    assert worker.build_prompt("summarise", "x") != worker.build_prompt("report", "x")


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        worker.build_prompt("no-such-kind", "x")


def test_reduce_prompt_includes_every_summary():
    p = worker.build_reduce_prompt("summarise", ["alpha", "beta", "gamma"])
    for s in ("alpha", "beta", "gamma"):
        assert s in p


# --- optional per-job operator instruction -----------------------------------

def test_build_prompt_without_instruction_is_unchanged():
    """No instruction must produce byte-identical output to before this existed."""
    assert worker.build_prompt("summarise", "x", instruction=None) == \
        worker.build_prompt("summarise", "x")
    assert worker.build_prompt("summarise", "x", instruction="   ") == \
        worker.build_prompt("summarise", "x")


def test_build_prompt_injects_instruction():
    p = worker.build_prompt("summarise", "MY DOC", instruction="Focus on dates.")
    assert "Focus on dates." in p
    assert "MY DOC" in p


def test_build_reduce_prompt_injects_instruction():
    p = worker.build_reduce_prompt("report", ["a", "b"], instruction="Keep it short.")
    assert "Keep it short." in p


def test_summarise_traced_passes_instruction_to_every_call():
    client = FakeClient()
    doc = "word " * 20000  # forces map + reduce
    worker.summarise_traced("summarise", doc, client, instruction="Focus on risk.")
    assert client.prompts, "expected at least one call"
    assert all("Focus on risk." in p for p in client.prompts), \
        "instruction must reach every map call and the reduce call"


def test_run_one_reads_instruction_from_the_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world", instruction="Be terse.")
    client = FakeClient()
    assert worker.run_one(dbpath, "http://x", client) is True
    assert db.get_job(dbpath, job_id)["status"] == "done"
    assert all("Be terse." in p for p in client.prompts)


def test_run_one_works_without_an_instruction_column_value(dbpath):
    """A job created with no instruction (the common case) must not KeyError."""
    db.create_job(dbpath, "summarise", "hello world")
    client = FakeClient()
    assert worker.run_one(dbpath, "http://x", client) is True
    assert db.list_jobs(dbpath)[0]["status"] == "done"


# --- chunking ---------------------------------------------------------------

def test_short_document_is_one_chunk():
    chunks = worker.chunk_document("a b c d e", chunk_tokens=100, overlap_tokens=10)
    assert chunks == ["a b c d e"]


def test_long_document_is_split():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = worker.chunk_document(text, chunk_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1


def test_chunks_overlap():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = worker.chunk_document(text, chunk_tokens=100, overlap_tokens=10)
    # The tail of one chunk must reappear at the head of the next, or material
    # spanning a boundary is lost -- the failure mode overlap exists to prevent.
    first_tail = chunks[0].split()[-5:]
    assert all(w in chunks[1].split() for w in first_tail)


def test_chunking_covers_whole_document():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = worker.chunk_document(text, chunk_tokens=100, overlap_tokens=10)
    seen = set()
    for c in chunks:
        seen.update(c.split())
    assert seen == set(text.split()), "chunking dropped content"


def test_zero_overlap_still_covers():
    text = " ".join(f"w{i}" for i in range(500))
    chunks = worker.chunk_document(text, chunk_tokens=100, overlap_tokens=0)
    seen = set()
    for c in chunks:
        seen.update(c.split())
    assert seen == set(text.split())


def test_overlap_not_less_than_chunk_guard():
    """Overlap >= chunk size would make the stride zero and loop forever."""
    with pytest.raises(ValueError):
        worker.chunk_document("a b c", chunk_tokens=10, overlap_tokens=10)


# --- map-reduce -------------------------------------------------------------

def test_short_document_skips_reduce():
    client = FakeClient()
    out = worker.summarise("summarise", "short doc", "http://x", client)
    assert client.calls == 1, "a single-chunk document must not trigger a reduce"
    assert out == "summary-1"


def test_long_document_maps_then_reduces():
    client = FakeClient()
    text = " ".join(f"w{i}" for i in range(20000))
    out = worker.summarise("summarise", text, "http://x", client)
    # N chunk calls plus exactly one reduce call.
    assert client.calls > 2
    assert out == f"summary-{client.calls}"
    assert "Combine" in client.prompts[-1] or "combine" in client.prompts[-1]


# --- job execution ----------------------------------------------------------

def test_run_one_completes_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")
    client = FakeClient()
    assert worker.run_one(dbpath, "http://x", client) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["result"] == "summary-1"
    assert job["chunks"] == 1
    assert job["total_s"] is not None


def test_run_one_returns_false_when_idle(dbpath):
    assert worker.run_one(dbpath, "http://x", FakeClient()) is False


def test_run_one_records_failure(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello")
    client = FakeClient(fail_with=RuntimeError("connection refused"))
    assert worker.run_one(dbpath, "http://x", client) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert "connection refused" in job["error"]


def test_failure_does_not_stall_the_queue(dbpath):
    """One bad job must not block every job behind it."""
    db.create_job(dbpath, "summarise", "bad")
    good = db.create_job(dbpath, "summarise", "good")
    worker.run_one(dbpath, "http://x", FakeClient(fail_with=RuntimeError("boom")))
    worker.run_one(dbpath, "http://x", FakeClient())
    assert db.get_job(dbpath, good)["status"] == "done"


# --- reasoning-model empty-content guard (F21) ------------------------------

def test_extract_content_returns_normal_content():
    choice = {"message": {"content": "the summary"}, "finish_reason": "stop"}
    assert worker.extract_content(choice, 512) == "the summary"


def test_empty_content_while_reasoning_raises():
    """Observed against Qwen3-4B on node 1: content='', reasoning_content=659
    chars, finish_reason='length'. Returning '' would store an empty summary
    and mark the job done."""
    choice = {
        "message": {"content": "", "reasoning_content": "thinking " * 80},
        "finish_reason": "length",
    }
    with pytest.raises(worker.EmptyCompletion) as e:
        worker.extract_content(choice, 120)
    assert "reasoning" in str(e.value)
    assert "120" in str(e.value)


def test_empty_content_without_reasoning_raises():
    choice = {"message": {"content": ""}, "finish_reason": "length"}
    with pytest.raises(worker.EmptyCompletion):
        worker.extract_content(choice, 512)


def test_whitespace_only_content_is_empty():
    choice = {"message": {"content": "   \n  "}, "finish_reason": "stop"}
    with pytest.raises(worker.EmptyCompletion):
        worker.extract_content(choice, 512)


def test_empty_completion_fails_the_job_not_the_worker(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello")
    client = FakeClient(fail_with=worker.EmptyCompletion("exhausted max_tokens"))
    assert worker.run_one(dbpath, "http://x", client) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["result"] is None
    assert "exhausted max_tokens" in job["error"]


# --- Truncation: found by the FIRST real end-to-end run, 2026-08-17 -----------
# Every test above uses FakeClient, so extract_content had never seen a live
# server's finish_reason. A 512-token request to Qwen3-4B came back cut off
# mid-sentence and was stored with status='done'.

def test_truncated_content_is_rejected_not_returned():
    """finish_reason='length' with non-empty text must NOT be a success.

    This is the regression that matters: before the fix this returned the
    truncated string and the job was marked done.
    """
    choice = {
        "message": {"content": "Recommendations include implementing automated"},
        "finish_reason": "length",
    }
    with pytest.raises(worker.TruncatedCompletion) as e:
        worker.extract_content(choice, 512)
    # The message must name the budget and show where it stopped, or an operator
    # reading an overnight failure cannot act on it.
    assert "512" in str(e.value)
    assert "automated" in str(e.value)


def test_complete_content_with_stop_is_still_returned():
    """The guard must not reject normal completions."""
    choice = {"message": {"content": "a whole summary."}, "finish_reason": "stop"}
    assert worker.extract_content(choice, 1024) == "a whole summary."


def test_inline_think_block_is_stripped():
    """Some servers put the chain of thought in content, not reasoning_content."""
    choice = {
        "message": {"content": "<think>Let me consider the document.</think>The answer."},
        "finish_reason": "stop",
    }
    assert worker.extract_content(choice, 1024) == "The answer."


def test_content_that_is_only_thinking_is_rejected():
    """A response containing nothing but thought has no answer in it.

    This one slips past the F21 empty-content guard, because content is not
    empty -- it is just entirely useless.
    """
    choice = {
        "message": {"content": "<think>Still thinking about it"},
        "finish_reason": "stop",
    }
    with pytest.raises(worker.EmptyCompletion) as e:
        worker.extract_content(choice, 1024)
    assert "think" in str(e.value).lower()


def test_reduce_step_gets_a_bigger_budget_than_the_map_step():
    """The reduce output covers every chunk summary, so it is legitimately longer.

    Using one budget for both is how a long document ends with a truncated final
    answer even though every chunk succeeded.
    """
    assert worker.REDUCE_MAX_TOKENS > worker.MAP_MAX_TOKENS

    client = FakeClient()
    doc = "word " * 20000                      # forces multiple chunks
    worker.summarise("summarise", doc, "http://x", client)
    assert client.calls > 1, "expected a map phase plus a reduce"
    # Every map call gets the map budget; the final (reduce) call gets more.
    assert client.budgets[-1] == worker.REDUCE_MAX_TOKENS
    assert all(b == worker.MAP_MAX_TOKENS for b in client.budgets[:-1])


# --- ttft_s: the schema reserved the column and nothing ever filled it --------

def test_first_prefill_reads_the_servers_own_timings():
    """prompt_ms is the AUTHORITATIVE TTFT (F17). Never curl's time_starttransfer."""
    log = [{"prompt_n": 2214, "prompt_ms": 89147.95, "predicted_ms": 10147.46},
           {"prompt_n": 100, "prompt_ms": 1000.0}]
    # First call, not last, and converted to seconds.
    assert worker._first_prefill_s(log) == 89.15


def test_first_prefill_is_none_when_unavailable():
    """NULL beats a fabricated 0.0 -- a missing measurement must look missing."""
    assert worker._first_prefill_s(None) is None
    assert worker._first_prefill_s([]) is None
    assert worker._first_prefill_s([{"prompt_n": 5}]) is None
    assert worker._first_prefill_s([{"prompt_ms": "not a number"}]) is None


def test_run_one_records_ttft_when_the_client_reports_timings(dbpath):
    class TimingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.timings_log = [{"prompt_ms": 4200.0}]

    db.create_job(dbpath, "summarise", "a short document")
    assert worker.run_one(dbpath, "http://x", TimingClient()) is True
    job = db.list_jobs(dbpath)[0]
    assert job["status"] == "done"
    assert job["ttft_s"] == 4.2


def test_run_one_still_works_with_a_client_that_reports_no_timings(dbpath):
    """FakeClient has no timings_log; the job must still complete."""
    db.create_job(dbpath, "summarise", "a short document")
    assert worker.run_one(dbpath, "http://x", FakeClient()) is True
    job = db.list_jobs(dbpath)[0]
    assert job["status"] == "done"
    assert job["ttft_s"] is None


# --- per-model reasoning control (measured 2026-08-17) ------------------------
# llama-server silently drops chat-template kwargs its template does not
# reference, so the wrong family's knob is indistinguishable from the right one.

def test_gpt_oss_gets_reasoning_effort_not_enable_thinking():
    """MEASURED: enable_thinking is inert on harmony; reasoning_effort works.

    Same prompt, same 80-token budget: no kwargs -> EMPTY content (F21);
    reasoning_effort=low -> 49 tokens, clean stop.
    """
    kw = worker.reasoning_kwargs_for("/opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf")
    assert kw == {"reasoning_effort": "low"}
    assert "enable_thinking" not in kw


def test_qwen3_gets_enable_thinking():
    assert worker.reasoning_kwargs_for("qwen3-4b-q4km.gguf") == {"enable_thinking": False}


def test_unknown_model_gets_NO_kwargs_rather_than_a_guess():
    """Sending an inert flag is worse than sending nothing: it creates false
    confidence that thinking is suppressed while the budget drains into it."""
    assert worker.reasoning_kwargs_for("some-model-we-have-never-seen.gguf") == {}
    assert worker.reasoning_kwargs_for("") == {}
    assert worker.reasoning_kwargs_for(None) == {}


def test_longest_family_match_wins():
    """'qwen3' must beat the more general 'qwen' entry."""
    assert worker.reasoning_kwargs_for("Qwen3-Next-80B") == {"enable_thinking": False}


# --- preview estimate (DESIGN-NOTES F: "when will this be done?") -------------

def test_estimate_scales_with_chunks_and_flags_its_basis():
    one = worker.estimate_seconds("short doc", 100.0)
    many = worker.estimate_seconds("word " * 20000, 100.0)
    assert many[0] > one[0]
    assert one[1] == "measured", "an explicit rate is measured, not an estimate"


def test_estimate_falls_back_and_says_so():
    """The basis must travel with the number. An estimate presented as a
    measurement is the error F1/F17/F28 were all instances of."""
    secs, basis = worker.estimate_seconds("short doc")
    assert basis == "estimate"
    assert secs == worker.FALLBACK_SECONDS_PER_CHUNK


def test_multi_chunk_pays_for_the_reduce_pass():
    """A single chunk skips reduce; more than one does not."""
    single, _ = worker.estimate_seconds("tiny", 100.0)
    assert single == 100.0
    multi, _ = worker.estimate_seconds("word " * 20000, 100.0)
    assert multi > worker.count_chunks("word " * 20000) * 100.0


def test_humanise_is_readable_by_a_non_specialist():
    assert worker.humanise_seconds(45) == "45 seconds"
    assert worker.humanise_seconds(600) == "10 minutes"
    assert worker.humanise_seconds(7200) == "2.0 hours"
    assert worker.humanise_seconds(None) == "unknown"


def test_seconds_per_chunk_needs_enough_samples(dbpath):
    """One completed job is not a calibration."""
    spc, n = db.seconds_per_chunk(dbpath)
    assert spc is None and n == 0

    j1 = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, j1, "r", {"total_s": 200.0, "chunks": 2})
    spc, n = db.seconds_per_chunk(dbpath)
    assert spc is None and n == 1, "must refuse to calibrate on a single sample"

    j2 = db.create_job(dbpath, "summarise", "b")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, j2, "r", {"total_s": 400.0, "chunks": 2})
    spc, n = db.seconds_per_chunk(dbpath)
    assert n == 2
    # Recency-weighted, so it sits between the two samples but leans toward the
    # NEWER (slower) one rather than landing on the plain mean of 150.
    assert 100.0 < spc < 200.0
    assert spc > 150.0, "the newer sample must carry more weight, got %.1f" % spc


# --- provenance: offsets must point at the real source -----------------------

def test_chunk_spans_offsets_are_exact():
    """doc[start:end] must equal the chunk text, or provenance is a lie."""
    doc = "ALPHA one two three. " * 900 + "OMEGA final."
    for ch in worker.chunk_spans(doc):
        assert doc[ch["start"]:ch["end"]] == ch["text"]


def test_chunk_spans_cover_the_whole_document():
    doc = " ".join(f"w{i}" for i in range(5000))
    chunks = worker.chunk_spans(doc)
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] == len(doc)


def test_chunk_spans_preserve_original_formatting():
    """Sliced from the original, not rejoined from split() -- so a human checking
    a claim sees the document as it was written."""
    doc = "Line one.\n\n    Indented line two.\n\nLine three."
    ch = worker.chunk_spans(doc)[0]
    assert "\n\n" in ch["text"]


def test_summarise_traced_returns_a_record_per_chunk():
    client = FakeClient()
    doc = "word " * 20000
    final, records = worker.summarise_traced("summarise", doc, client)
    assert len(records) == worker.count_chunks(doc)
    for i, r in enumerate(records):
        assert r["index"] == i
        assert r["end"] > r["start"]
        assert r["summary"]
    assert final


def test_single_chunk_record_matches_the_final_output():
    client = FakeClient()
    final, records = worker.summarise_traced("summarise", "a short document", client)
    assert len(records) == 1
    assert final == records[0]["summary"], "no reduce pass for one chunk"


def test_provenance_survives_a_real_job(dbpath):
    from missing_link import db as _db
    job_id = _db.create_job(dbpath, "summarise", "word " * 20000)
    worker.run_one(dbpath, "http://x", FakeClient())
    rows = _db.get_chunk_summaries(dbpath, job_id)
    assert len(rows) > 1, "per-chunk summaries must be PERSISTED, not discarded"
    assert all(r["end_char"] > r["start_char"] for r in rows)


def test_rate_is_recency_weighted_not_a_plain_average(dbpath):
    """The rate changes under us -- model, engine, quant, --parallel, node count.
    A newer, slower reality must dominate older, faster history."""
    from missing_link import db as _db
    for total_s in (100.0, 100.0, 100.0, 100.0, 400.0):   # last one is 4x slower
        j = _db.create_job(dbpath, "summarise", "x")
        _db.claim_next_pending(dbpath)
        _db.complete_job(dbpath, j, "r", {"total_s": total_s, "chunks": 1})
    rate, n = _db.seconds_per_chunk(dbpath)
    assert n == 5
    plain_mean = (100 + 100 + 100 + 100 + 400) / 5      # == 160
    assert rate > plain_mean, "recent slowdown must be weighted UP, got %.1f" % rate


# --- backend wedged: alive, accepting TCP, serving nothing -------------------
# Observed 2026-08-17. A client disconnecting mid-generation left llama-server
# hung: /slots returned nothing, a trivial request timed out at 85s, and
# Restart=always could not help because the process had not crashed.

def test_unreachable_backend_fails_the_job_not_the_worker(dbpath):
    """A wedged server must produce a FAILED job with an actionable message,
    not a job frozen in 'running' for the full hour-long timeout."""
    class WedgedClient(FakeClient):
        def assert_reachable(self, timeout=20):
            raise worker.BackendUnavailable(
                "http://x did not answer /health within 20s. The server may be "
                "wedged. Try: sudo systemctl restart llama-server@8080")

    job_id = db.create_job(dbpath, "summarise", "a document")
    assert worker.run_one(dbpath, "http://x", WedgedClient()) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert "wedged" in job["error"]
    assert "systemctl restart" in job["error"], "must tell the operator what to DO"


def test_client_without_a_health_probe_still_works(dbpath):
    """Injected clients need not implement assert_reachable."""
    db.create_job(dbpath, "summarise", "a document")
    assert worker.run_one(dbpath, "http://x", FakeClient()) is True
    assert db.list_jobs(dbpath)[0]["status"] == "done"

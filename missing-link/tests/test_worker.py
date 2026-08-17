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

    def __init__(self, replies=None, fail_with=None, model="fake-model"):
        self.prompts = []
        # Token budget per call. Needed to assert that the reduce step gets a
        # larger budget than the map step -- using one value for both is how a
        # long document ends in a truncated final answer.
        self.budgets = []
        self.replies = replies
        self.fail_with = fail_with
        self.calls = 0
        # model_name() opts a FakeClient into the resumability path (run_one
        # only resumes when BOTH the recorded and current model are known --
        # see run_one). Every FakeClient answers a fixed name by default,
        # which does not affect any test that never persists chunk summaries;
        # tests that care about the resume/discard decision pass a different
        # `model` to simulate the server now serving something else.
        self.model = model

    def complete(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        self.budgets.append(max_tokens)
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        if self.replies is None:
            return f"summary-{self.calls}"
        return self.replies[(self.calls - 1) % len(self.replies)]

    def model_name(self):
        return self.model


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

def test_unreachable_backend_does_not_freeze_or_kill_the_worker(dbpath):
    """A wedged server must leave an actionable message and a job that is not
    frozen in 'running' for the full hour-long timeout.

    The TERMINAL half of this assertion moved on 2026-08-18: a wedged backend
    is a TRANSIENT failure (worker.classify_failure), so the job now returns to
    'pending' for a bounded, backed-off retry instead of failing outright --
    the watchdog restarts llama-server minutes later and the job should pick
    itself back up rather than wait for a human. The exhaustion path (retried
    MAX_ATTEMPTS times, then failed for good) is covered in tests/test_retry.py.
    """
    class WedgedClient(FakeClient):
        def assert_reachable(self, timeout=20):
            raise worker.BackendUnavailable(
                "http://x did not answer /health within 20s. The server may be "
                "wedged. Try: sudo systemctl restart llama-server@8080")

    job_id = db.create_job(dbpath, "summarise", "a document")
    assert worker.run_one(dbpath, "http://x", WedgedClient()) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "pending"
    assert job["retry_after"] is not None, "a retry must be backed off, not immediate"
    assert "wedged" in job["error"]
    assert "systemctl restart" in job["error"], "must tell the operator what to DO"


def test_client_without_a_health_probe_still_works(dbpath):
    """Injected clients need not implement assert_reachable."""
    db.create_job(dbpath, "summarise", "a document")
    assert worker.run_one(dbpath, "http://x", FakeClient()) is True
    assert db.list_jobs(dbpath)[0]["status"] == "done"


# --- resumability (Part 1) ----------------------------------------------------
# BEFORE this feature, save_chunk_summaries was called exactly once, after
# summarise_traced returned -- i.e. after the WHOLE document (every chunk plus
# the reduce step) had finished. A job killed mid-document lost every
# completed chunk summary with it. These tests cover the fix: incremental
# persistence via on_chunk_done, skipping already-done chunks via
# resume_records, and the model-identity guard around trusting a resume.

def test_summarise_traced_skips_chunks_present_in_resume_records():
    """A chunk whose index/span is already in resume_records must not be
    re-sent to the model -- that is the entire point of resuming."""
    client = FakeClient()
    doc = "word " * 20000
    spans = worker.chunk_spans(doc)
    assert len(spans) > 2

    # Pretend chunks 0 and 1 already completed in an earlier, interrupted run.
    resume = [
        {"index": spans[0]["index"], "start": spans[0]["start"],
         "end": spans[0]["end"], "summary": "PRIOR SUMMARY 0"},
        {"index": spans[1]["index"], "start": spans[1]["start"],
         "end": spans[1]["end"], "summary": "PRIOR SUMMARY 1"},
    ]
    final, records = worker.summarise_traced(
        "summarise", doc, client, resume_records=resume)

    assert records[0]["summary"] == "PRIOR SUMMARY 0"
    assert records[1]["summary"] == "PRIOR SUMMARY 1"
    # The resumed summaries legitimately appear in the FINAL reduce prompt
    # (that is the whole point -- the reduce step covers the union of old and
    # new chunk summaries). What must never happen is a MAP call re-deriving
    # them from the source text, so check every call except the last (reduce).
    assert not any("PRIOR" in p for p in client.prompts[:-1])
    assert "PRIOR SUMMARY 0" in client.prompts[-1]
    # Only the REMAINING chunks (+ 1 reduce call) hit the model.
    assert client.calls == (len(spans) - 2) + 1


def test_summarise_traced_ignores_a_resume_record_with_a_stale_span():
    """If a resumed record's offsets no longer match the current chunking
    (e.g. CHUNK_TOKENS changed between runs), it must be redone, not trusted."""
    client = FakeClient()
    doc = "word " * 20000
    stale = [{"index": 0, "start": 999999, "end": 999999 + 5, "summary": "STALE"}]
    final, records = worker.summarise_traced(
        "summarise", doc, client, resume_records=stale)
    assert records[0]["summary"] != "STALE"


def test_on_chunk_done_fires_once_per_newly_computed_chunk_only():
    """The callback must fire for NEW chunks and must NOT fire for resumed
    ones -- resumed chunks are already persisted, re-saving them is at best
    redundant and at worst overwrites a real record with a stale one."""
    client = FakeClient()
    doc = "word " * 20000
    spans = worker.chunk_spans(doc)
    resume = [{"index": spans[0]["index"], "start": spans[0]["start"],
               "end": spans[0]["end"], "summary": "PRIOR"}]
    seen = []
    worker.summarise_traced("summarise", doc, client, resume_records=resume,
                            on_chunk_done=seen.append)
    assert len(seen) == len(spans) - 1
    assert all(r["index"] != spans[0]["index"] for r in seen)


def test_should_stop_halts_before_the_next_new_chunk():
    """should_stop is honoured between chunks, and raises JobCancelled rather
    than silently returning a partial result -- callers must not mistake a
    stopped job for a completed one."""
    client = FakeClient()
    doc = "word " * 20000
    calls_before_stop = 2
    flag = {"stop": False}

    def should_stop():
        return flag["stop"]

    def on_chunk_done(record):
        if record["index"] == calls_before_stop - 1:
            flag["stop"] = True

    with pytest.raises(worker.JobCancelled):
        worker.summarise_traced("summarise", doc, client,
                                on_chunk_done=on_chunk_done, should_stop=should_stop)
    assert client.calls == calls_before_stop, "must not start another chunk once stopped"


def test_crash_mid_job_resumes_without_redoing_completed_chunks(dbpath):
    """The core regression test: simulate a real process crash mid-document
    (not a caught exception -- an actual death) and confirm the chunks
    completed before it survive, and a resumed run does not redo them.
    """
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)
    job = db.claim_next_pending(dbpath)
    assert job["id"] == job_id
    n_chunks = worker.count_chunks(text)
    assert n_chunks > 3

    crashy = FakeClient(model="model-A")
    crash_after = 2

    def on_chunk_done(record):
        # Exactly what run_one's _persist_chunk does: persist as each chunk
        # completes, not at the end.
        db.save_chunk_summaries(dbpath, job_id, [record], model=crashy.model_name())
        if record["index"] == crash_after - 1:
            raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError):
        worker.summarise_traced("summarise", text, crashy, on_chunk_done=on_chunk_done)

    persisted = db.get_chunk_summaries(dbpath, job_id)
    assert len(persisted) == crash_after, \
        "chunks completed before the crash must already be persisted"

    # The process is gone; the job is stuck 'running' until the next startup.
    assert db.get_job(dbpath, job_id)["status"] == "running"
    n = db.requeue_running(dbpath)
    assert n == 1

    resumer = FakeClient(model="model-A")   # same model serving after restart
    assert worker.run_one(dbpath, "http://x", resumer) is True

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    # Only the chunks NOT already persisted, plus one reduce call, should have
    # reached the model on resume.
    assert resumer.calls == (n_chunks - crash_after) + 1
    assert len(db.get_chunk_summaries(dbpath, job_id)) == n_chunks


def test_resume_against_a_different_model_discards_and_restarts(dbpath):
    """If the server is now serving a different model than produced the
    persisted chunks, resuming must NOT silently mix outputs from two models.
    Chosen behaviour (see worker.run_one's comment): discard and restart the
    map phase cleanly, not refuse -- so an unattended queue is not stuck
    waiting on an operator just because the server was restarted onto a
    different model.
    """
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)
    db.claim_next_pending(dbpath)
    n_chunks = worker.count_chunks(text)

    crashy = FakeClient(model="model-A")

    def on_chunk_done(record):
        db.save_chunk_summaries(dbpath, job_id, [record], model=crashy.model_name())
        if record["index"] == 1:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        worker.summarise_traced("summarise", text, crashy, on_chunk_done=on_chunk_done)
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 2

    db.requeue_running(dbpath)

    resumer = FakeClient(model="model-B")   # a DIFFERENT model now serving
    assert worker.run_one(dbpath, "http://x", resumer) is True

    assert db.get_job(dbpath, job_id)["status"] == "done"
    # Every chunk was redone (none skipped) because the model changed, plus
    # one reduce call.
    assert resumer.calls == n_chunks + 1
    # And the persisted rows now all carry the NEW model, not a mix.
    assert db.get_recorded_model(dbpath, job_id) == "model-B"


def test_resume_against_a_different_instruction_discards_and_restarts(dbpath):
    """The same soundness gap the model check exists to prevent, arriving
    through `instruction` instead: chunks persisted under one operator
    instruction must not be reused when the job's instruction no longer
    matches -- otherwise a reduce step could silently combine chunk summaries
    written under two different sets of guidance.
    """
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text, instruction="Focus on dates.")
    db.claim_next_pending(dbpath)
    n_chunks = worker.count_chunks(text)

    crashy = FakeClient(model="model-A")

    def on_chunk_done(record):
        db.save_chunk_summaries(dbpath, job_id, [record], model=crashy.model_name(),
                                instruction="Focus on dates.")
        if record["index"] == 1:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        worker.summarise_traced("summarise", text, crashy, instruction="Focus on dates.",
                                on_chunk_done=on_chunk_done)
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 2

    # Simulate the job's instruction having changed before it was resumed
    # (the row itself is immutable via any current route, but the resume
    # check must not rely on that -- see the report on why this is guarded
    # explicitly rather than assumed impossible).
    import sqlite3
    conn = sqlite3.connect(dbpath)
    conn.execute("UPDATE jobs SET status='pending', instruction=? WHERE id=?",
                ("Focus on risk instead.", job_id))
    conn.commit()
    conn.close()

    resumer = FakeClient(model="model-A")   # SAME model, DIFFERENT instruction
    assert worker.run_one(dbpath, "http://x", resumer) is True

    assert db.get_job(dbpath, job_id)["status"] == "done"
    # Every chunk redone (none skipped) because the instruction changed, even
    # though the model did not -- both must match to trust a resume.
    assert resumer.calls == n_chunks + 1
    ok, recorded = db.get_recorded_instruction(dbpath, job_id)
    assert ok is True
    assert recorded == "Focus on risk instead."


def test_resume_with_matching_model_and_no_instruction_on_either_side_resumes(dbpath):
    """The common case: no guidance given, then or now. Must resume normally
    -- "no instruction" recorded is not the same kind of unknown as "no model
    recorded", and must not be treated as untrustworthy."""
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)  # no instruction
    db.claim_next_pending(dbpath)
    n_chunks = worker.count_chunks(text)

    crashy = FakeClient(model="model-A")

    def on_chunk_done(record):
        db.save_chunk_summaries(dbpath, job_id, [record], model=crashy.model_name())
        if record["index"] == 1:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        worker.summarise_traced("summarise", text, crashy, on_chunk_done=on_chunk_done)

    db.requeue_running(dbpath)
    resumer = FakeClient(model="model-A")
    assert worker.run_one(dbpath, "http://x", resumer) is True

    assert db.get_job(dbpath, job_id)["status"] == "done"
    # Only the chunks NOT already persisted, plus the reduce call -- i.e. a
    # TRUE resume, not a full redo.
    assert resumer.calls == (n_chunks - 2) + 1


def test_resume_with_unknown_current_model_does_not_resume(dbpath):
    """A client that cannot identify its model (no model_name(), or /props
    unreachable) must not be trusted to resume -- an unconfirmed match is
    treated the same as a mismatch, not as a pass."""
    class NoModelClient(FakeClient):
        model_name = None  # simulate a client with no model_name() at all

    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)
    db.claim_next_pending(dbpath)
    n_chunks = worker.count_chunks(text)

    db.save_chunk_summaries(
        dbpath, job_id,
        [{"index": 0, "start": 0, "end": 4, "summary": "prior"}],
        model="model-A")
    db.requeue_running(dbpath)

    resumer = NoModelClient()
    assert worker.run_one(dbpath, "http://x", resumer) is True
    assert db.get_job(dbpath, job_id)["status"] == "done"
    # No chunk was skipped -- the whole document was redone from scratch.
    assert resumer.calls == n_chunks + 1


# --- stopping a running job (Part 2) -------------------------------------------

def test_run_one_honours_a_cancel_request_between_chunks(dbpath):
    """Simulates an operator clicking Stop while a job is mid-document: the
    cancel_requested flag is set concurrently (here, from inside a chunk
    completion, standing in for a concurrent web request), and the job must
    stop at the next chunk boundary rather than finishing the document."""
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)
    n_chunks = worker.count_chunks(text)
    assert n_chunks > 3
    stop_after = 2

    class StoppingClient(FakeClient):
        def complete(self, prompt, max_tokens=512):
            out = super().complete(prompt, max_tokens=max_tokens)
            if self.calls == stop_after:
                db.request_cancel(dbpath, job_id)
            return out

    assert worker.run_one(dbpath, "http://x", StoppingClient()) is True

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "cancelled"
    assert job["chunks"] == stop_after
    # Chunks completed before the stop are kept, not discarded.
    assert len(db.get_chunk_summaries(dbpath, job_id)) == stop_after
    # And the job must genuinely have stopped short of the full document.
    assert job["result"] is None


def test_cancelled_job_is_resumable_like_a_crashed_one(dbpath):
    """A cooperatively-stopped job keeps its persisted chunks, so moving it
    back to pending (whatever triggers that -- here, direct as a stand-in for
    a future 'resume' action) continues rather than restarts."""
    text = "word " * 20000
    job_id = db.create_job(dbpath, "summarise", text)
    n_chunks = worker.count_chunks(text)
    stop_after = 2

    class StoppingClient(FakeClient):
        def complete(self, prompt, max_tokens=512):
            out = super().complete(prompt, max_tokens=max_tokens)
            if self.calls == stop_after:
                db.request_cancel(dbpath, job_id)
            return out

    worker.run_one(dbpath, "http://x", StoppingClient())
    assert db.get_job(dbpath, job_id)["status"] == "cancelled"

    # Put it back in the queue (a manual/future "resume" action).
    import sqlite3
    conn = sqlite3.connect(dbpath)
    conn.execute("UPDATE jobs SET status='pending', cancel_requested=0 WHERE id=?",
                (job_id,))
    conn.commit()
    conn.close()

    resumer = FakeClient()
    assert worker.run_one(dbpath, "http://x", resumer) is True
    assert db.get_job(dbpath, job_id)["status"] == "done"
    assert resumer.calls == (n_chunks - stop_after) + 1


# --- notification hook (Part 3) ------------------------------------------------

def test_notify_completion_hook_is_called_and_is_a_documented_noop(dbpath):
    """notify_completion is the documented hook point for a future
    email/webhook integration. It must be called with the finished job's full
    row, and it must do nothing on its own -- CLAUDE.md rules out adding an
    SMTP dependency or any outbound network call here."""
    calls = []
    orig = worker.notify_completion
    worker.notify_completion = lambda job: calls.append(job)
    try:
        job_id = db.create_job(dbpath, "summarise", "hello world")
        worker.run_one(dbpath, "http://x", FakeClient())
    finally:
        worker.notify_completion = orig

    assert len(calls) == 1
    assert calls[0]["id"] == job_id
    assert calls[0]["status"] == "done"
    # The real (default) implementation must not raise or do anything network-y.
    assert worker.notify_completion(calls[0]) is None


# --- job-level fan-out: N endpoint workers, one queue --------------------------
# app.py now runs one worker per inference endpoint concurrently, all calling
# run_one against the SAME database. test_db.test_claim_is_atomic_under_
# concurrency already proves the db primitive is safe in isolation; these prove
# run_one -- what a real endpoint worker actually calls -- does not somehow
# undo that guarantee (e.g. by doing work between the atomic claim and the
# on_claim hook a fan-out worker relies on to know what it is doing).

def test_run_one_calls_on_claim_with_the_claimed_job(dbpath):
    """on_claim is how a fan-out worker learns which job it just picked up, to
    show on the status page. It must fire with the real claimed job, and only
    when a job was actually claimed."""
    job_id = db.create_job(dbpath, "summarise", "hello world")
    seen = []
    assert worker.run_one(dbpath, "http://x", FakeClient(), on_claim=seen.append) is True
    assert len(seen) == 1
    assert seen[0]["id"] == job_id

    # Queue now empty: idle return must not fire on_claim with nothing.
    seen.clear()
    assert worker.run_one(dbpath, "http://x", FakeClient(), on_claim=seen.append) is False
    assert seen == []


def test_run_one_without_on_claim_is_unaffected(dbpath):
    """on_claim is optional and additive -- every pre-existing caller passes
    none, and must keep working exactly as before."""
    db.create_job(dbpath, "summarise", "hello world")
    assert worker.run_one(dbpath, "http://x", FakeClient()) is True


# --- endpoint attribution (live telemetry support) -----------------------------

def test_run_one_records_which_endpoint_claimed_the_job(dbpath):
    """Persisted immediately at claim, not only held in app.py's in-memory
    ENDPOINT_STATE -- that dict is cleared the moment a worker moves on, so a
    FAILED job would otherwise carry no record of which node it died on."""
    job_id = db.create_job(dbpath, "summarise", "hello world")
    assert worker.run_one(dbpath, "http://node3:8080", FakeClient()) is True
    assert db.get_job(dbpath, job_id)["endpoint"] == "http://node3:8080"


def test_run_one_persists_timings_through_to_the_db(dbpath):
    """End-to-end: run_one -> summarise_traced -> _persist_chunk ->
    save_chunk_summaries -> get_chunk_timings, with a client that reports
    timings the way the real LlamaClient does."""
    class TimedClient(FakeClient):
        def complete(self, prompt, max_tokens=512):
            out = super().complete(prompt, max_tokens=max_tokens)
            self.timings_log.append({
                "prompt_n": 4096, "prompt_ms": 4000.0,
                "predicted_n": 200, "predicted_ms": 2000.0})
            return out

    client = TimedClient()
    client.timings_log = []
    job_id = db.create_job(dbpath, "summarise", "hello world")
    assert worker.run_one(dbpath, "http://x", client) is True
    timings = db.get_chunk_timings(dbpath, job_id)
    assert len(timings) == 1
    assert timings[0]["prompt_n"] == 4096


def test_run_one_records_endpoint_even_when_the_job_fails(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")
    assert worker.run_one(
        dbpath, "http://node4:8080",
        FakeClient(fail_with=RuntimeError("boom"))) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["endpoint"] == "http://node4:8080"


# --- guidance length guard ------------------------------------------------------
# Guidance is embedded in EVERY map call (once per chunk, not once per
# document), so it must fit alongside one chunk and the model's output inside
# a single llama-server slot. See the constants' comments for the arithmetic.

def test_check_instruction_length_accepts_empty_and_none():
    worker.check_instruction_length(None)
    worker.check_instruction_length("")
    worker.check_instruction_length("   ")


def test_check_instruction_length_accepts_a_normal_note():
    worker.check_instruction_length("Focus on the financial terms and dates.")


def test_check_instruction_length_refuses_an_oversized_guide():
    too_long = "word " * (worker.MAX_INSTRUCTION_WORDS + 500)
    with pytest.raises(worker.GuidanceTooLong) as excinfo:
        worker.check_instruction_length(too_long)
    # The message must name the limit, not just say "too long" -- an operator
    # needs the number to know how much to cut.
    assert str(worker.MAX_INSTRUCTION_WORDS) in str(excinfo.value)


def test_check_instruction_length_boundary_is_inclusive():
    at_limit = "word " * worker.MAX_INSTRUCTION_WORDS
    worker.check_instruction_length(at_limit)  # must not raise
    over_by_one = "word " * (worker.MAX_INSTRUCTION_WORDS + 1)
    with pytest.raises(worker.GuidanceTooLong):
        worker.check_instruction_length(over_by_one)


# --- remaining_seconds (live-progress ETA) --------------------------------------

def test_remaining_seconds_uses_measured_rate_when_given():
    secs, basis = worker.remaining_seconds(
        n_chunks=10, chunks_done=4, reduce_pending=False, seconds_per_chunk=100.0)
    assert basis == "measured"
    # 6 map chunks left, PLUS the reduce call still ahead (this job has more
    # than 1 chunk and has not reduced yet) -- same 0.5x overhead
    # estimate_seconds already applies for a whole document.
    assert secs == 6 * 100.0 + 0.5 * 100.0


def test_remaining_seconds_falls_back_to_the_estimate_constant():
    secs, basis = worker.remaining_seconds(
        n_chunks=10, chunks_done=4, reduce_pending=False, seconds_per_chunk=None)
    assert basis == "estimate"
    assert secs == 6 * worker.FALLBACK_SECONDS_PER_CHUNK + 0.5 * worker.FALLBACK_SECONDS_PER_CHUNK


def test_remaining_seconds_adds_reduce_overhead_once_maps_are_done():
    """Every map chunk finished but the reduce call has not returned -- there
    is still real work left (the reduce inference call itself), which a naive
    "chunks remaining" count of zero would miss entirely."""
    secs, _basis = worker.remaining_seconds(
        n_chunks=10, chunks_done=10, reduce_pending=True, seconds_per_chunk=100.0)
    assert secs == 0.5 * 100.0


def test_remaining_seconds_is_zero_once_a_single_chunk_document_is_done():
    secs, _basis = worker.remaining_seconds(
        n_chunks=1, chunks_done=1, reduce_pending=False, seconds_per_chunk=100.0)
    assert secs == 0


def test_remaining_seconds_preserves_an_explicit_basis_label():
    """The operator's own request: a per-job rate must not collapse into the
    same 'measured' label as the cluster-wide rate -- they are different
    claims and must stay visibly different through to display."""
    secs, basis = worker.remaining_seconds(
        n_chunks=10, chunks_done=4, reduce_pending=False,
        seconds_per_chunk=80.0, basis="job")
    assert basis == "job"
    assert secs == (6 + 0.5) * 80.0


def test_remaining_seconds_none_rate_always_means_estimate_regardless_of_basis():
    """seconds_per_chunk=None can only ever mean the fallback constant -- a
    caller passing a basis alongside None is nonsensical and must not leak
    through as a false claim of being job- or cluster-calibrated."""
    secs, basis = worker.remaining_seconds(
        n_chunks=10, chunks_done=4, reduce_pending=False,
        seconds_per_chunk=None, basis="job")
    assert basis == "estimate"
    assert secs == (6 + 0.5) * worker.FALLBACK_SECONDS_PER_CHUNK


# --- per-chunk timings: prefill/generation tok/s, kept separate -----------------

def test_last_timings_reads_the_most_recent_call():
    class TimedClient(FakeClient):
        def complete(self, prompt, max_tokens=512):
            out = super().complete(prompt, max_tokens=max_tokens)
            self.timings_log.append({
                "prompt_n": 100 * self.calls, "prompt_ms": 1000.0,
                "predicted_n": 10 * self.calls, "predicted_ms": 200.0})
            return out

    client = TimedClient()
    client.timings_log = []
    client.complete("p1")
    client.complete("p2")
    t = worker._last_timings(client)
    assert t == {"prompt_n": 200, "prompt_ms": 1000.0,
                "predicted_n": 20, "predicted_ms": 200.0}


def test_last_timings_empty_for_a_client_with_no_timings_log():
    assert worker._last_timings(FakeClient()) == {}


def test_summarise_traced_attaches_timings_to_new_chunk_records():
    class TimedClient(FakeClient):
        def complete(self, prompt, max_tokens=512):
            out = super().complete(prompt, max_tokens=max_tokens)
            self.timings_log.append({
                "prompt_n": 4096, "prompt_ms": 4000.0,
                "predicted_n": 200, "predicted_ms": 2000.0})
            return out

    client = TimedClient()
    client.timings_log = []
    doc = "word " * 20000
    _final, records = worker.summarise_traced("summarise", doc, client)
    assert records, "expected at least one chunk"
    for r in records:
        assert r["prompt_n"] == 4096
        assert r["predicted_ms"] == 2000.0


def test_summarise_traced_resumed_chunks_have_no_new_timings():
    """A resumed chunk made no new call, so it must not be attributed the
    NEXT chunk's timings, or any timing at all from this run."""
    client = FakeClient()
    doc = "word " * 20000
    spans = worker.chunk_spans(doc)
    resume = [{"index": spans[0]["index"], "start": spans[0]["start"],
               "end": spans[0]["end"], "summary": "PRIOR"}]
    _final, records = worker.summarise_traced(
        "summarise", doc, client, resume_records=resume)
    assert "prompt_n" not in records[0]


def test_chunk_rate_stats_is_none_for_no_timings():
    assert worker.chunk_rate_stats([]) is None
    assert worker.chunk_rate_stats(None) is None


def test_chunk_rate_stats_splits_prefill_and_generation():
    timings = [
        {"idx": 0, "prompt_n": 4000, "prompt_ms": 200000.0,   # 20 tok/s prefill
         "predicted_n": 100, "predicted_ms": 20000.0},         # 5 tok/s gen
        {"idx": 1, "prompt_n": 4000, "prompt_ms": 160000.0,   # 25 tok/s prefill
         "predicted_n": 100, "predicted_ms": 20000.0},         # 5 tok/s gen
    ]
    stats = worker.chunk_rate_stats(timings)
    assert stats["n_timed"] == 2
    # LAST chunk only.
    assert stats["last_prefill_tok_s"] == 25.0
    assert stats["last_gen_tok_s"] == 5.0
    # POOLED across both (sum tokens / sum ms), not a mean of per-chunk ratios.
    assert stats["avg_prefill_tok_s"] == pytest.approx(8000 / 360.0)
    assert stats["avg_gen_tok_s"] == pytest.approx(200 / 40.0)
    assert stats["seconds_per_chunk"] == pytest.approx(
        ((200000 + 20000) / 1000.0 + (160000 + 20000) / 1000.0) / 2)


def test_chunk_rate_stats_never_blends_prefill_and_generation():
    """The rates must be reported separately -- never averaged together into
    one number, which would be dominated by whichever phase happened to run
    when sampled (prefill and generation run at very different speeds)."""
    timings = [{"idx": 0, "prompt_n": 1000, "prompt_ms": 40000.0,
               "predicted_n": 1000, "predicted_ms": 200000.0}]
    stats = worker.chunk_rate_stats(timings)
    assert stats["last_prefill_tok_s"] != stats["last_gen_tok_s"]
    assert set(stats) >= {"last_prefill_tok_s", "last_gen_tok_s",
                          "avg_prefill_tok_s", "avg_gen_tok_s"}


def test_concurrent_endpoint_workers_never_double_claim(dbpath):
    """N concurrent per-endpoint workers (the shape app.py's fan-out uses) must
    never process the same job twice, and every job must finish exactly once.

    Not hypothetical: this is the reason job-level fan-out is safe to turn on
    at all. If run_one somehow raced (e.g. by inspecting job state outside the
    atomic claim), this would show a job double-processed or a wrong final
    count, the same way test_claim_is_atomic_under_concurrency demonstrated for
    the bare db primitive.
    """
    import threading

    n_jobs = 40
    n_endpoints = 5
    for i in range(n_jobs):
        db.create_job(dbpath, "summarise", f"doc {i}")

    claimed_ids = []
    lock = threading.Lock()

    def record(job):
        with lock:
            claimed_ids.append(job["id"])

    def endpoint_worker(base_url):
        client = FakeClient()
        while worker.run_one(dbpath, base_url, client, on_claim=record):
            pass

    endpoints = [f"http://node{i}:8080" for i in range(n_endpoints)]
    threads = [threading.Thread(target=endpoint_worker, args=(url,)) for url in endpoints]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == n_jobs, f"expected {n_jobs} claims, got {len(claimed_ids)}"
    assert len(set(claimed_ids)) == n_jobs, "a job was claimed by more than one endpoint worker"

    jobs = db.list_jobs(dbpath)
    assert len(jobs) == n_jobs
    assert all(j["status"] == "done" for j in jobs), \
        "every job must finish exactly once, not be left running or reprocessed"

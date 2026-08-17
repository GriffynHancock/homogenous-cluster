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

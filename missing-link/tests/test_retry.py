"""Retry of TRANSIENT failures, and the resume it is supposed to compose with.

WHY THIS FILE EXISTS. Chunk summaries have been persisted one row at a time
since 2026-08-17, so a job killed mid-document no longer loses the work. But
nothing ever retried: a backend death produced RemoteDisconnected (or
BackendUnavailable from the pre-flight probe), hit run_one's broad `except`,
and went straight to db.fail_job -- terminal. The overnight sequence was
therefore "the engine aborts at 02:00, the watchdog restarts llama-server at
02:05, and nothing happens until morning", with N chunks of completed work
sitting unused in the database. That is not hypothetical: it happened twice in
two days (F39's destroyed 97,299-character job, and ik_llama.cpp fatal-erroring
on node 2).

WHAT THIS FILE HAS TO PROVE, and what it deliberately does NOT rely on. F34 is
the finding where 41 passing tests hid a pipeline that had never processed a
document, and a FakeClient raising RemoteDisconnected proves only the
classifier. So the centrepiece here
(test_retry_resumes_and_costs_only_the_outstanding_chunks) drives a REAL
sqlite database through a REAL 26-chunk document, fails partway, retries, and
asserts on the NUMBER OF MODEL CALLS ACTUALLY MADE. If the retry did not
compose with the resume, that number would be 26 and the whole feature would
be an expensive way to repeat hours of work.
"""
import http.client
import os
import socket
import tempfile
import urllib.error

import pytest

from missing_link import db, extract, worker


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def webclient(monkeypatch):
    """The real FastAPI app against a scratch database, no background worker.

    Same wiring as tests/test_app.py's `client` fixture. Used here because the
    UI half of this feature has to be exercised through the actual template --
    a payload dict with the right keys in it is not evidence that an operator
    can see anything (F34).
    """
    from fastapi.testclient import TestClient

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c
    os.unlink(path)


@pytest.fixture
def no_backoff(monkeypatch):
    """Collapse the retry backoff so a test can drive several attempts.

    Patches the POLICY constant rather than writing retry_after directly, so
    the claim predicate under test is still the one production uses.
    """
    monkeypatch.setattr(worker, "RETRY_BACKOFF_BASE_S", 0)
    monkeypatch.setattr(worker, "RETRY_BACKOFF_CAP_S", 0)


class RecordingClient:
    """FakeClient plus a scripted failure at a chosen call number.

    `model_name` is answered (and `assert_reachable` is not defined) so that
    run_one takes the resume path: it only trusts persisted chunks when the
    recorded and current model are both known and equal.
    """

    def __init__(self, fail_on_call=None, fail_with=None, model="fake-model"):
        self.prompts = []
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.fail_with = fail_with
        self.model = model

    def complete(self, prompt, max_tokens=512):
        self.calls += 1
        if self.fail_with is not None and (
                self.fail_on_call is None or self.calls == self.fail_on_call):
            raise self.fail_with
        self.prompts.append(prompt)
        return f"summary-{self.calls}"

    def model_name(self):
        return self.model


# The reduce prompt is the only one that asks for summaries to be combined --
# see worker.REDUCE_PROMPTS. Used to split "map calls" from "the reduce call",
# which is the distinction the chunk-count evidence rests on.
def _is_reduce(prompt):
    return "Combine" in prompt or "combine" in prompt


@pytest.fixture(scope="module")
def long_document():
    """A document that really does chunk into 26 pieces.

    Deliberately built from worker's own constants rather than assumed: the
    test asserts the chunk count below, so a change to CHUNK_TOKENS breaks the
    fixture loudly instead of silently weakening the evidence.
    """
    return " ".join(f"word{i}" for i in range(66000))


# --- classification ----------------------------------------------------------
# The heart of the feature, and the part that must NOT be a blanket retry: an
# infinite loop on an unattended overnight queue is worse than a job that stops.

@pytest.mark.parametrize("exc", [
    worker.BackendUnavailable("did not answer /health within 20s"),
    # What the client actually saw when the watchdog restarted llama-server
    # under job 06af2911d7fc (F39).
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    ConnectionResetError(104, "Connection reset by peer"),
    ConnectionRefusedError(111, "Connection refused"),
    BrokenPipeError(32, "Broken pipe"),
    http.client.IncompleteRead(b"half a response"),
    http.client.BadStatusLine(""),
    # socket.timeout is an alias for TimeoutError on 3.10+; both spellings are
    # asserted because both appear in the wild.
    socket.timeout("timed out"),
    TimeoutError("timed out"),
    urllib.error.URLError("[Errno 111] Connection refused"),
    urllib.error.HTTPError("http://x", 503, "Service Unavailable", None, None),
    urllib.error.HTTPError("http://x", 500, "Internal Server Error", None, None),
    urllib.error.HTTPError("http://x", 429, "Too Many Requests", None, None),
])
def test_backend_failures_are_transient(exc):
    assert worker.classify_failure(exc) == "transient"


@pytest.mark.parametrize("exc", [
    # F21 / F34. Token-budget failures: an identical retry reproduces them
    # exactly, at the price of a full prefill (~79% of document wall-clock).
    worker.EmptyCompletion("the model returned no usable text"),
    worker.TruncatedCompletion("ran out of tokens mid-answer"),
    worker.GuidanceTooLong("guidance is 900 words; the limit is 400"),
    # "document contains no words", and the chunking-config guard.
    ValueError("document contains no words"),
    extract.ExtractionError("this PDF has no text layer; it needs OCR"),
    # Our request is wrong and will be exactly as wrong next time.
    urllib.error.HTTPError("http://x", 400, "Bad Request", None, None),
    urllib.error.HTTPError("http://x", 404, "Not Found", None, None),
])
def test_bad_input_and_bad_requests_are_permanent(exc):
    assert worker.classify_failure(exc) == "permanent"


@pytest.mark.parametrize("exc", [
    RuntimeError("something nobody has seen before"),
    KeyError("choices"),
    TypeError("'NoneType' object is not subscriptable"),
    OSError(28, "No space left on device"),
])
def test_an_unrecognised_failure_defaults_to_permanent(exc):
    """A mystery error retried all night is worse than one that stops.

    Same discipline as reasoning_kwargs_for returning {} for a model it does
    not know: refuse rather than guess. Widening the transient class must be a
    deliberate act of naming a type, never a side effect.
    """
    assert worker.classify_failure(exc) == "permanent"
    assert worker.is_recognised_failure(exc) is False


def test_recognised_permanent_and_transient_types_are_marked_recognised():
    assert worker.is_recognised_failure(worker.EmptyCompletion("x")) is True
    assert worker.is_recognised_failure(worker.BackendUnavailable("x")) is True


# --- the bound and the backoff ----------------------------------------------

def test_retry_delay_doubles_and_is_capped():
    assert worker.retry_delay_seconds(1) == worker.RETRY_BACKOFF_BASE_S
    assert worker.retry_delay_seconds(2) == worker.RETRY_BACKOFF_BASE_S * 2
    assert worker.retry_delay_seconds(3) == worker.RETRY_BACKOFF_BASE_S * 4
    assert worker.retry_delay_seconds(99) == worker.RETRY_BACKOFF_CAP_S


def test_transient_failure_requeues_with_a_backoff_and_counts_the_attempt(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a short document")
    client = RecordingClient(fail_with=http.client.RemoteDisconnected("closed"))

    assert worker.run_one(dbpath, "http://node2:8080", client) is True

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "pending", "a dead backend must not be terminal"
    assert job["attempts"] == 1
    assert job["retry_after"] is not None
    assert job["started_at"] is None, "the next attempt's elapsed must be its own"
    # The row still says WHY, or a pending retry is indistinguishable from a
    # job that has simply never run.
    assert "will be retried" in job["error"]
    assert "RemoteDisconnected" in job["error"]
    assert "not a problem with the document" in job["error"]


def test_a_job_in_backoff_is_not_claimed_but_does_not_block_the_queue(dbpath):
    """The backoff is a timestamp on the row, not a sleep in the worker.

    A sleeping worker cannot run anybody else's job, so a permanently-dead
    backend would starve the whole queue. This asserts the opposite: the
    backed-off job is skipped and the next job runs.
    """
    stuck = db.create_job(dbpath, "summarise", "first in the queue")
    other = db.create_job(dbpath, "summarise", "queued behind it")

    worker.run_one(dbpath, "http://x",
                   RecordingClient(fail_with=ConnectionResetError("reset")))
    assert db.get_job(dbpath, stuck)["status"] == "pending"

    # The very next claim must skip the backed-off job and take the other one.
    ok = RecordingClient()
    assert worker.run_one(dbpath, "http://x", ok) is True
    assert db.get_job(dbpath, other)["status"] == "done"
    assert db.get_job(dbpath, stuck)["status"] == "pending"
    assert db.get_job(dbpath, stuck)["attempts"] == 1, "must not have been re-claimed"

    # And with nothing else claimable, the worker reports idle rather than
    # spinning on the job that is waiting out its backoff.
    assert worker.run_one(dbpath, "http://x", RecordingClient()) is False


def test_retries_are_bounded_and_the_final_error_blames_the_cluster(
        dbpath, no_backoff):
    job_id = db.create_job(dbpath, "summarise", "a short document")

    attempts = 0
    while worker.run_one(dbpath, "http://node2:8080",
                         RecordingClient(fail_with=worker.BackendUnavailable(
                             "http://node2:8080 did not answer /health within 20s"))):
        attempts += 1
        assert attempts <= worker.MAX_ATTEMPTS + 1, "retry loop is not bounded"
        if db.get_job(dbpath, job_id)["status"] == "failed":
            break

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["attempts"] == worker.MAX_ATTEMPTS
    assert job["retry_after"] is None
    # The operator reading this at 08:00 needs "the cluster was broken all
    # night", not "your document is bad" -- they are different next actions.
    assert f"FAILED AFTER {worker.MAX_ATTEMPTS} ATTEMPTS" in job["error"]
    assert "CLUSTER problem" in job["error"]
    assert "not a problem with this document" in job["error"]
    assert "node2" in job["error"], "must name the endpoint that kept dying"


def test_a_permanent_failure_is_never_retried(dbpath, no_backoff):
    """Retrying an identical prompt at an identical budget against an identical
    model reproduces F21 exactly, and buys a full prefill for it."""
    job_id = db.create_job(dbpath, "summarise", "a short document")
    assert worker.run_one(dbpath, "http://x", RecordingClient(
        fail_with=worker.EmptyCompletion("budget exhausted while thinking"))) is True

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["attempts"] == 1
    assert "NOT RETRIED" in job["error"]
    assert "budget exhausted while thinking" in job["error"]
    assert worker.run_one(dbpath, "http://x", RecordingClient()) is False


def test_an_unknown_failure_stops_and_says_it_was_not_recognised(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a short document")
    worker.run_one(dbpath, "http://x", RecordingClient(
        fail_with=TypeError("'NoneType' object is not subscriptable")))
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert "not one Missing Link recognises" in job["error"]


def test_a_requeued_job_does_not_fire_the_completion_hook(dbpath, monkeypatch):
    """notify_completion's contract is done/failed/cancelled -- once. Firing it
    on a requeue would notify the operator about a job that is about to run
    again."""
    seen = []
    monkeypatch.setattr(worker, "notify_completion", lambda job: seen.append(job))

    db.create_job(dbpath, "summarise", "a short document")
    worker.run_one(dbpath, "http://x",
                   RecordingClient(fail_with=ConnectionRefusedError("refused")))
    assert seen == []

    db.create_job(dbpath, "summarise", "another document")
    worker.run_one(dbpath, "http://x", RecordingClient())
    assert [j["status"] for j in seen] == ["done"]


# --- THE ONE THAT MATTERS ----------------------------------------------------

def test_retry_resumes_and_costs_only_the_outstanding_chunks(
        dbpath, no_backoff, long_document):
    """A job that dies at chunk 5 of 26 must, on retry, make 22 map calls -- not 26.

    This is the whole point of retrying. If the retry does not compose with the
    resume then it is just repeating hours of work, and the assertion that
    proves it is a COUNT OF MODEL CALLS, not a status string.
    """
    n_chunks = worker.count_chunks(long_document)
    assert n_chunks == 26, f"fixture drifted: document now chunks into {n_chunks}"

    job_id = db.create_job(dbpath, "summarise", long_document)

    # Attempt 1: four chunks complete and are persisted, then the backend dies
    # in the middle of the fifth -- the F39 shape exactly.
    first = RecordingClient(fail_on_call=5,
                            fail_with=http.client.RemoteDisconnected("closed"))
    assert worker.run_one(dbpath, "http://node1:8080", first) is True
    assert first.calls == 5

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "pending"
    assert job["attempts"] == 1
    persisted = db.get_chunk_summaries(dbpath, job_id)
    assert len(persisted) == 4, "the completed chunks must survive the failure"
    assert job["resumed_chunks"] == 0, "attempt 1 had nothing to resume from"

    # Attempt 2: same model, same instruction, so the four persisted chunk
    # summaries are reused.
    second = RecordingClient()
    assert worker.run_one(dbpath, "http://node1:8080", second) is True

    map_calls = [p for p in second.prompts if not _is_reduce(p)]
    reduce_calls = [p for p in second.prompts if _is_reduce(p)]
    assert len(map_calls) == n_chunks - 4 == 22, (
        f"the retry made {len(map_calls)} map calls; it must make only the 22 "
        f"chunks that were still outstanding, or the resume bought nothing")
    assert len(reduce_calls) == 1
    assert second.calls == 23

    # It resumed at the right PLACE, not merely the right number of times: the
    # first map call of the retry must carry chunk 4's text, and no call may
    # re-send chunk 0's.
    spans = worker.chunk_spans(long_document)
    assert spans[4]["text"][:60] in map_calls[0]
    assert not any(spans[0]["text"][:60] in p for p in map_calls)

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["attempts"] == 2
    assert job["resumed_chunks"] == 4, "the job page must be able to say it resumed"
    assert job["chunks"] == 26
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 26

    # The reduce step must cover all 26 sections, including the four that came
    # from the earlier process -- mixing runs of the SAME model is sound, and
    # is the reason resuming is allowed at all.
    assert "summary-1" in reduce_calls[0], "chunk 1's summary came from attempt 1"
    assert reduce_calls[0].count("summary-") >= 26


def test_the_job_page_and_progress_json_show_the_retry(webclient):
    """A retried job must be VISIBLY a retried job.

    Rendered through the real app and the real template, not asserted on a
    dict: the whole value of this feature to an operator is that at 08:00 the
    page says "attempt 2, resumed from 4 of 26 chunks" rather than silently
    taking three times as long.
    """
    from missing_link import db
    from missing_link.app import DB_PATH

    document = " ".join(f"word{i}" for i in range(66000))
    job_id = db.create_job(DB_PATH, "summarise", document)

    # Attempt 1 dies after four chunks.
    worker.run_one(DB_PATH, "http://node2:8080",
                   RecordingClient(fail_on_call=5,
                                   fail_with=http.client.RemoteDisconnected("closed")))

    # PENDING, waiting out the backoff.
    page = webclient.get(f"/jobs/{job_id}").text
    assert "Waiting to retry" in page
    assert f"attempt 1 of {worker.MAX_ATTEMPTS} failed" in page
    assert "resume from" in page, "must say the completed chunks are not lost"
    body = webclient.get(f"/jobs/{job_id}/progress").json()
    assert body["attempts"] == 1
    assert body["max_attempts"] == worker.MAX_ATTEMPTS
    assert body["retry_after"] is not None
    assert body["chunks_done"] == 4

    # RUNNING again, this time resumed. (Claimed directly, then the resume
    # bookkeeping recorded, which is what run_one does before its first call.)
    db._connect(DB_PATH).execute(
        "UPDATE jobs SET retry_after=NULL WHERE id=?", (job_id,))
    job = db.claim_next_pending(DB_PATH)
    assert job["id"] == job_id and job["attempts"] == 2
    db.record_resume(DB_PATH, job_id, 4)

    # Normalised because the template wraps these sentences across source
    # lines; the assertion is about what a reader sees, not about whitespace.
    page = " ".join(webclient.get(f"/jobs/{job_id}").text.split())
    assert f"Attempt 2 of {worker.MAX_ATTEMPTS}" in page
    assert "Resumed — 4 of 26 chunks were already summarised" in page
    assert "remaining 22 are being sent to the model" in page, (
        "must say what the retry actually costs")
    body = webclient.get(f"/jobs/{job_id}/progress").json()
    assert body["attempts"] == 2
    assert body["resumed_chunks"] == 4


def test_the_job_page_says_how_many_attempts_a_dead_backend_got(webclient):
    from missing_link import db
    from missing_link.app import DB_PATH

    job_id = db.create_job(DB_PATH, "summarise", "a short document")
    db.claim_next_pending(DB_PATH)
    db.claim_next_pending(DB_PATH)  # no-op; the job is already running
    db.fail_job(DB_PATH, job_id, worker.final_error_message(
        worker.BackendUnavailable("did not answer /health within 20s"),
        attempts=worker.MAX_ATTEMPTS, chunks_done=0,
        endpoint="http://node2:8080", retried=True))
    db._connect(DB_PATH).execute(
        "UPDATE jobs SET attempts=? WHERE id=?", (worker.MAX_ATTEMPTS, job_id))

    page = webclient.get(f"/jobs/{job_id}").text
    assert f"after {worker.MAX_ATTEMPTS} attempts" in page
    assert "CLUSTER problem" in page
    assert "node2" in page


def test_a_retry_after_a_model_change_restarts_and_says_so(dbpath, no_backoff):
    """The resume guard still wins over the retry.

    Chunk summaries from two different models must never be mixed into one
    reduce step, so a retry against a server now serving something else
    discards and restarts -- and resumed_chunks records 0 so the job page says
    "restarted", not "resumed".
    """
    document = " ".join(f"word{i}" for i in range(4000))
    assert worker.count_chunks(document) == 2
    job_id = db.create_job(dbpath, "summarise", document)

    first = RecordingClient(fail_on_call=2, model="model-a",
                            fail_with=ConnectionResetError("reset"))
    worker.run_one(dbpath, "http://x", first)
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 1

    second = RecordingClient(model="model-b")
    worker.run_one(dbpath, "http://x", second)

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["resumed_chunks"] == 0
    assert len([p for p in second.prompts if not _is_reduce(p)]) == 2, (
        "both chunks must be recomputed under the new model, not mixed")

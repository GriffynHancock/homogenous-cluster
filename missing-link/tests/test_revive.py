"""Reviving a terminally-failed or cancelled job: db.revive_job and the
`POST /jobs/{id}/revive` route.

THE GAP THIS CLOSES. A job that exhausts its retry budget, fails
permanently, or is cancelled has no way back except hand-written SQL against
the live database -- yet its chunk_summaries rows are still sitting on disk
and WOULD be reused by worker.run_one's resume check if the job ever ran
again. Without this route, the operator's only recovery path for a night the
cluster was broken is to resubmit the document, discarding completed work
that is sitting right there. See db.revive_job's docstring for the full
reasoning, including why 'cancelled' is included and 'done' is not.

This file drives BOTH the store function directly (db.revive_job) and the
real FastAPI app through TestClient + the real job.html template -- a status
code and a dict are not evidence an operator can actually reach and use this
(the project's own standing complaint, F34): the button has to exist, be
findable from the job page, and say something true before it is clicked.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from missing_link import db, worker


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def client(monkeypatch):
    """The real app, real templates, no background worker -- MISSING_LINK_NO_WORKER
    means the lifespan never starts a worker loop against a real inference
    endpoint, so this is safe to run with no cluster present.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        c._dbpath = path
        yield c
    os.unlink(path)


def _make_failed(path, job_id=None, attempts=4, cancelled_via_request=False,
                  chunks=0, model="qwen3-4b", instruction=None):
    """Build a job in a terminal state directly against the store, the way a
    real worker run would have left it -- not by calling private helpers, so
    this stays honest about what a real failed/cancelled row looks like.
    """
    jid = job_id or db.create_job(path, "summarise", "word " * 500,
                                   instruction=instruction)
    if chunks:
        db.save_chunk_summaries(
            path, jid,
            [{"index": i, "start": i * 10, "end": i * 10 + 10, "summary": f"s{i}"}
             for i in range(chunks)],
            model=model, instruction=instruction)
    conn = db._connect(path)
    try:
        if cancelled_via_request:
            conn.execute(
                "UPDATE jobs SET status='running', attempts=?, started_at='x', "
                "cancel_requested=1 WHERE id=?", (attempts, jid))
            db.finish_cancelled(path, jid, {"chunks": chunks, "total_s": 12.0})
        else:
            conn.execute(
                "UPDATE jobs SET status='failed', attempts=?, error='boom', "
                "started_at='x', finished_at='y' WHERE id=?", (attempts, jid))
    finally:
        conn.close()
    return jid


# --- db.revive_job: the mechanism -------------------------------------------

def test_revive_failed_job_returns_to_pending_with_fresh_budget(dbpath):
    jid = _make_failed(dbpath, attempts=4)
    result = db.revive_job(dbpath, jid)
    assert result == "revived"
    job = db.get_job(dbpath, jid)
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    assert job["retry_after"] is None
    assert job["error"] is None
    assert job["result"] is None
    assert job["started_at"] is None
    assert job["finished_at"] is None


def test_revive_cancelled_job_clears_cancel_requested(dbpath):
    """LOAD-BEARING regression: request_cancel sets cancel_requested=1 for a
    job cancelled while running, and nothing before revive_job ever clears it
    back to 0. If revive left it set, the revived job would call
    worker._should_stop() and cancel ITSELF again before the first chunk --
    silently reproducing the exact state this button exists to escape.
    """
    jid = _make_failed(dbpath, attempts=1, cancelled_via_request=True, chunks=2)
    job_before = db.get_job(dbpath, jid)
    assert job_before["status"] == "cancelled"
    assert job_before["cancel_requested"] == 1  # sanity: this IS the case that matters

    result = db.revive_job(dbpath, jid)
    assert result == "revived"
    job = db.get_job(dbpath, jid)
    assert job["cancel_requested"] == 0
    assert db.is_cancel_requested(dbpath, jid) is False


def test_revive_preserves_chunk_summaries(dbpath):
    """The whole point: revive is a status transition, not a data wipe. The
    persisted chunks worker.run_one's resume check will read are untouched.
    """
    jid = _make_failed(dbpath, attempts=4, chunks=5, model="qwen3-4b")
    db.revive_job(dbpath, jid)
    rows = db.get_chunk_summaries(dbpath, jid)
    assert len(rows) == 5
    assert db.get_recorded_model(dbpath, jid) == "qwen3-4b"


@pytest.mark.parametrize("status,setup", [
    ("pending", lambda p: db.create_job(p, "summarise", "doc")),
    ("done", lambda p: _done_job(p)),
])
def test_revive_refuses_non_terminal_or_succeeded_jobs(dbpath, status, setup):
    jid = setup(dbpath)
    result = db.revive_job(dbpath, jid)
    assert result == status  # unchanged status returned, not "pending"
    assert db.get_job(dbpath, jid)["status"] == status


def _flat(resp):
    """Response body with whitespace collapsed, so a phrase that the template
    wraps across a source line (as most of this project's prose does) can
    still be matched as one string -- a browser collapses that whitespace
    too, so this is what the phrase actually reads as on the page.
    """
    return " ".join(resp.text.split())


def _done_job(path):
    jid = db.create_job(path, "summarise", "doc")
    db.complete_job(path, jid, "the summary", {"total_s": 1.0, "chunks": 1,
                                                "tokens": 3, "ttft_s": 0.1})
    return jid


def test_revive_refuses_running_job(dbpath):
    jid = db.create_job(dbpath, "summarise", "doc")
    claimed = db.claim_next_pending(dbpath)
    assert claimed["id"] == jid
    result = db.revive_job(dbpath, jid)
    assert result == "running"
    assert db.get_job(dbpath, jid)["status"] == "running"


def test_revive_no_such_job_returns_none(dbpath):
    assert db.revive_job(dbpath, "does-not-exist") is None


# --- the route, through the real app + template -----------------------------

def test_revive_route_moves_job_to_pending_and_redirects(client):
    jid = _make_failed(client._dbpath, attempts=4, chunks=3, model="qwen3-4b")
    resp = client.post(f"/jobs/{jid}/revive", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/jobs/{jid}"

    job = db.get_job(client._dbpath, jid)
    assert job["status"] == "pending"
    assert job["attempts"] == 0


def test_revive_route_404_for_unknown_job(client):
    resp = client.post("/jobs/does-not-exist/revive")
    assert resp.status_code == 404


def test_revive_route_409_for_done_job(client):
    jid = _done_job(client._dbpath)
    resp = client.post(f"/jobs/{jid}/revive")
    assert resp.status_code == 409
    assert db.get_job(client._dbpath, jid)["status"] == "done"


def test_revive_route_409_for_pending_job(client):
    jid = db.create_job(client._dbpath, "summarise", "doc")
    resp = client.post(f"/jobs/{jid}/revive")
    assert resp.status_code == 409


def test_job_page_shows_revive_button_for_failed_job(client):
    jid = _make_failed(client._dbpath, attempts=4, chunks=2, model="qwen3-4b")
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    body = _flat(resp)
    assert f'action="/jobs/{jid}/revive"' in body
    assert "Retry now" in body
    # "the right button" case: attempts == max_attempts, so the exhausted-
    # backend framing must appear, not the "will fail the same way" warning.
    assert "exhausted all" in body
    assert "will very likely fail again" not in body
    # What will be reused, stated BEFORE the click -- the model that produced
    # the persisted chunks must be named on the page.
    assert "qwen3-4b" in body
    assert "2 chunk summar" in body


def test_job_page_warns_when_permanent_failure_did_not_exhaust_attempts(client):
    """attempts < max_attempts on a FAILED job means classify_failure returned
    "permanent" before the budget ran out -- the document/request case, not
    the cluster case. The page must say retrying will likely reproduce it,
    not invite a blind multi-hour rerun.
    """
    jid = _make_failed(client._dbpath, attempts=1, chunks=0)
    resp = client.get(f"/jobs/{jid}")
    body = _flat(resp)
    assert "will very likely fail again in exactly the same way" in body
    assert "No chunk summaries were saved" in body


def test_job_page_shows_revive_button_for_cancelled_job(client):
    jid = _make_failed(client._dbpath, attempts=1, cancelled_via_request=True,
                        chunks=4, model="qwen3-4b")
    resp = client.get(f"/jobs/{jid}")
    body = _flat(resp)
    assert f'action="/jobs/{jid}/revive"' in body
    assert "Resume" in body
    assert "not a failure" in body


def test_job_page_flags_instruction_mismatch(client):
    """The persisted chunks were produced under a DIFFERENT instruction than
    the one currently on the job row -- e.g. an old row saved before this
    column existed. The preview must not claim a match it cannot back up.
    """
    jid = db.create_job(client._dbpath, "summarise", "word " * 500,
                         instruction="be terse")
    db.save_chunk_summaries(
        client._dbpath, jid,
        [{"index": 0, "start": 0, "end": 10, "summary": "s0"}],
        model="qwen3-4b", instruction="be verbose")
    conn = db._connect(client._dbpath)
    conn.execute("UPDATE jobs SET status='failed', attempts=4, error='boom' "
                 "WHERE id=?", (jid,))
    conn.close()

    resp = client.get(f"/jobs/{jid}")
    body = _flat(resp)
    assert "unlikely to match" in body or "do not currently agree" in body


def test_no_revive_button_for_done_job(client):
    jid = _done_job(client._dbpath)
    resp = client.get(f"/jobs/{jid}")
    assert f'action="/jobs/{jid}/revive"' not in resp.text

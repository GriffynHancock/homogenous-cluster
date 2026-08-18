"""job_failures -- the append-only record of every failed ATTEMPT.

WHY THIS FILE EXISTS. `db.schedule_retry` has always kept `jobs.error` on the
row while a job awaits retry, with a good comment explaining why (a job
sitting in 'pending' must still say WHY). But `jobs.error` is a single
string: each retry overwrites it, and a job that fails on attempt 1 and
succeeds on attempt 2 keeps ONLY attempt 2's message forever after via
db.complete_job leaving the column untouched -- which for a MULTI-failure
job (fails twice, succeeds the third time, or fails on endpoint A then
recovers on B) loses everything but the very last attempt. That is a real
diagnostic loss on a cluster where failures are the interesting signal --
concretely, job 6c0358825609 in the live database (attempts=2,
resumed_chunks=4) failed once and recovered, and confirming what killed it
required reading `jobs.error` at all (whether the UI ever showed it), not
inferring it from attempts/resumed_chunks alone.

This file proves: the table survives init_db being called twice (idempotent
migration, like every other table in this module); a retry appends a row
WITHOUT touching prior rows; a job that eventually succeeds keeps its full
history even though jobs.error itself only ever shows the latest attempt;
and the job page actually renders it -- a dict with the right keys is not
evidence an operator can see anything (F34's lesson, restated).
"""
import os
import tempfile

import pytest

from missing_link import db


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def webclient(monkeypatch):
    """The real FastAPI app against a scratch database, no background worker."""
    from fastapi.testclient import TestClient

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c, path
    os.unlink(path)


def test_init_db_creates_job_failures_and_is_idempotent(dbpath):
    conn = db._connect(dbpath)
    try:
        names = {r["name"] for r in
                 conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "job_failures" in names
    # Calling init_db a second time against an already-migrated database must
    # not raise -- this is exactly the path a systemd restart takes against
    # /opt/missing-link/jobs.sqlite.
    db.init_db(dbpath)


def test_schedule_retry_appends_without_losing_jobs_error(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")
    db.claim_next_pending(dbpath)  # attempts -> 1
    db.set_job_endpoint(dbpath, job_id, "http://node2:8080")

    db.schedule_retry(dbpath, job_id, "attempt 1 failed: RemoteDisconnected",
                       "2026-01-01T00:00:00+00:00")

    job = db.get_job(dbpath, job_id)
    assert job["error"] == "attempt 1 failed: RemoteDisconnected"
    assert job["status"] == "pending"

    history = db.get_job_failures(dbpath, job_id)
    assert len(history) == 1
    assert history[0]["attempt"] == 1
    assert history[0]["endpoint"] == "http://node2:8080"
    assert history[0]["error"] == "attempt 1 failed: RemoteDisconnected"
    assert history[0]["occurred_at"]


def test_multiple_retries_accumulate_in_order(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")

    db.claim_next_pending(dbpath)  # attempts -> 1
    db.set_job_endpoint(dbpath, job_id, "http://node1:8080")
    db.schedule_retry(dbpath, job_id, "attempt 1: died on node1",
                       "2026-01-01T00:00:00+00:00")

    db.claim_next_pending(dbpath)  # attempts -> 2
    db.set_job_endpoint(dbpath, job_id, "http://node2:8080")
    db.schedule_retry(dbpath, job_id, "attempt 2: died on node2",
                       "2026-01-01T00:01:00+00:00")

    history = db.get_job_failures(dbpath, job_id)
    assert [h["attempt"] for h in history] == [1, 2]
    assert [h["endpoint"] for h in history] == [
        "http://node1:8080", "http://node2:8080"]
    assert history[0]["error"] == "attempt 1: died on node1"
    assert history[1]["error"] == "attempt 2: died on node2"


def test_complete_job_does_not_erase_failure_history(dbpath):
    """The central claim of this feature: a job that fails then succeeds
    still shows the trouble it had, even though jobs.error itself only ever
    holds the latest string (here, whatever it was left as by schedule_retry
    -- complete_job never touches it, and never has)."""
    job_id = db.create_job(dbpath, "summarise", "hello world")

    db.claim_next_pending(dbpath)  # attempts -> 1
    db.set_job_endpoint(dbpath, job_id, "http://flaky:8080")
    db.schedule_retry(dbpath, job_id, "attempt 1: RemoteDisconnected",
                       "2026-01-01T00:00:00+00:00")

    db.claim_next_pending(dbpath)  # attempts -> 2
    db.set_job_endpoint(dbpath, job_id, "http://flaky:8080")
    db.complete_job(dbpath, job_id, "the summary", {"total_s": 1.0, "chunks": 1})

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"

    history = db.get_job_failures(dbpath, job_id)
    assert len(history) == 1
    assert history[0]["attempt"] == 1
    assert history[0]["endpoint"] == "http://flaky:8080"
    assert "RemoteDisconnected" in history[0]["error"]


def test_fail_job_also_logs_the_final_attempt(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")
    db.claim_next_pending(dbpath)  # attempts -> 1
    db.set_job_endpoint(dbpath, job_id, "http://node1:8080")

    db.fail_job(dbpath, job_id, "permanent: prompt too long")

    history = db.get_job_failures(dbpath, job_id)
    assert len(history) == 1
    assert history[0]["attempt"] == 1
    assert history[0]["endpoint"] == "http://node1:8080"
    assert history[0]["error"] == "permanent: prompt too long"


def test_job_with_no_failures_has_empty_history(dbpath):
    job_id = db.create_job(dbpath, "summarise", "hello world")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, job_id, "the summary", {"total_s": 1.0, "chunks": 1})
    assert db.get_job_failures(dbpath, job_id) == []


def test_job_page_shows_recovered_job_had_trouble(webclient):
    """The UI half. A dict with the right keys is not evidence an operator
    can see anything -- render the real template through the real route."""
    client, path = webclient

    job_id = db.create_job(path, "summarise", "hello world")
    db.claim_next_pending(path)
    db.set_job_endpoint(path, job_id, "http://node2:8080")
    db.schedule_retry(path, job_id,
                       "attempt 1 of 4 failed and will be retried: RemoteDisconnected",
                       "2020-01-01T00:00:00+00:00")  # already due -> claimable

    db.claim_next_pending(path)
    db.complete_job(path, job_id, "final summary text", {"total_s": 2.0, "chunks": 1})

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.text
    normalised = " ".join(body.split())  # template wraps this across lines
    assert "Succeeded after 1 failed attempt." in normalised
    assert "RemoteDisconnected" in body
    assert "node2:8080" in body


def test_job_page_falls_back_to_jobs_error_for_pre_existing_rows(webclient):
    """A job completed BEFORE job_failures existed looks, to get_job_failures,
    identical to one that never had trouble (no rows -- there was nowhere to
    write them). But jobs.error was never cleared by complete_job, so a
    legacy row like the live database's 6c0358825609 (attempts=2,
    resumed_chunks=4, non-null error, done) still has ONE surviving message.
    The page must show that instead of silently showing nothing."""
    client, path = webclient
    job_id = db.create_job(path, "summarise", "hello world")
    db.claim_next_pending(path)
    db.complete_job(path, job_id, "final summary text", {"total_s": 2.0, "chunks": 1})
    # Simulate a legacy row: error left over from a pre-job_failures retry,
    # with no job_failures rows behind it (this connection never wrote any).
    conn = db._connect(path)
    conn.execute("UPDATE jobs SET error=? WHERE id=?",
                 ("attempt 1 of 4 failed and will be retried: RemoteDisconnected", job_id))
    conn.close()

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert db.get_job_failures(path, job_id) == []  # confirms the legacy shape
    normalised = " ".join(resp.text.split())
    assert "This job had trouble before completing." in normalised
    assert "RemoteDisconnected" in resp.text


def test_job_page_for_clean_job_has_no_failure_history_section(webclient):
    client, path = webclient
    job_id = db.create_job(path, "summarise", "hello world")
    db.claim_next_pending(path)
    db.complete_job(path, job_id, "final summary text", {"total_s": 2.0, "chunks": 1})

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert "Succeeded after" not in resp.text
    assert "Failure history" not in resp.text

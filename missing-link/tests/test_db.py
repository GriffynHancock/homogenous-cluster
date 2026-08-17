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


def test_create_and_get_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "some document text")
    job = db.get_job(dbpath, job_id)
    assert job["id"] == job_id
    assert job["kind"] == "summarise"
    assert job["document"] == "some document text"
    assert job["status"] == "pending"
    assert job["result"] is None


def test_get_missing_job_returns_none(dbpath):
    assert db.get_job(dbpath, "nonexistent") is None


def test_claim_next_pending_returns_oldest_first(dbpath):
    first = db.create_job(dbpath, "summarise", "a")
    db.create_job(dbpath, "summarise", "b")
    claimed = db.claim_next_pending(dbpath)
    assert claimed["id"] == first
    assert db.get_job(dbpath, first)["status"] == "running"


def test_claim_next_pending_skips_running(dbpath):
    db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    assert db.claim_next_pending(dbpath) is None


def test_complete_job_records_result_and_metrics(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, job_id, "the summary",
                    {"ttft_s": 12.5, "total_s": 60.0, "tokens": 128, "chunks": 3})
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["result"] == "the summary"
    assert job["ttft_s"] == 12.5
    assert job["tokens"] == 128
    assert job["chunks"] == 3
    assert job["finished_at"] is not None


def test_fail_job_records_error(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.fail_job(dbpath, job_id, "connection refused")
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "connection refused"


def test_jobs_survive_reopen(dbpath):
    job_id = db.create_job(dbpath, "summarise", "persisted")
    db.init_db(dbpath)  # re-init must not wipe
    assert db.get_job(dbpath, job_id)["document"] == "persisted"


def test_list_jobs_newest_first(dbpath):
    db.create_job(dbpath, "summarise", "old")
    newest = db.create_job(dbpath, "report", "new")
    jobs = db.list_jobs(dbpath)
    assert len(jobs) == 2
    assert jobs[0]["id"] == newest


def test_claim_is_atomic_under_concurrency(dbpath):
    """Two workers must never claim the same job.

    Not hypothetical: the whole point of the queue is that a job runs once.
    A naive SELECT-then-UPDATE without a transaction passes every
    single-threaded test above and loses this one.
    """
    import threading

    for i in range(20):
        db.create_job(dbpath, "summarise", f"doc {i}")

    claimed = []
    lock = threading.Lock()

    def worker():
        while True:
            job = db.claim_next_pending(dbpath)
            if job is None:
                return
            with lock:
                claimed.append(job["id"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 20, f"expected 20 claims, got {len(claimed)}"
    assert len(set(claimed)) == 20, "a job was claimed more than once"


def test_requeue_stale_running_jobs(dbpath):
    """A job left 'running' by a crashed worker must be recoverable.

    Without this the queue silently loses work on any restart -- which
    defeats the point of an async runner whose jobs take hours.
    """
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    assert db.get_job(dbpath, job_id)["status"] == "running"

    n = db.requeue_running(dbpath)
    assert n == 1
    assert db.get_job(dbpath, job_id)["status"] == "pending"
    assert db.claim_next_pending(dbpath)["id"] == job_id

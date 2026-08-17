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


def test_create_job_stores_instruction(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a", instruction="Focus on dates.")
    assert db.get_job(dbpath, job_id)["instruction"] == "Focus on dates."


def test_create_job_instruction_defaults_to_none(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    assert db.get_job(dbpath, job_id)["instruction"] is None


def test_create_batch_and_get_batch(dbpath):
    records = [
        {"filename": "a.txt", "text": "hello", "preview": "hello",
         "status": "ready", "error": None},
        {"filename": "b.jpg", "text": "", "preview": "",
         "status": "refused", "error": "JPEG image is not supported"},
    ]
    batch_id = db.create_batch(dbpath, records)
    docs = db.get_batch(dbpath, batch_id)
    assert len(docs) == 2
    assert docs[0]["filename"] == "a.txt"
    assert docs[0]["status"] == "ready"
    assert docs[1]["status"] == "refused"
    assert docs[1]["error"] == "JPEG image is not supported"
    # Every document gets its own id, distinct from the shared batch id.
    assert docs[0]["id"] != docs[1]["id"]
    assert docs[0]["batch_id"] == batch_id


def test_get_batch_unknown_id_returns_empty(dbpath):
    assert db.get_batch(dbpath, "nonexistent") == []


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


# --- new jobs get priority=0, cancel_requested=0, seen_at=NULL ---------------

def test_new_job_defaults(dbpath):
    job = db.get_job(dbpath, db.create_job(dbpath, "summarise", "a"))
    assert job["priority"] == 0
    assert job["cancel_requested"] == 0
    assert job["seen_at"] is None


# --- schema upgrade: an existing database must survive gaining the new columns

def test_init_db_upgrades_a_pre_existing_database_without_dropping_data():
    """The live database at /opt/missing-link/jobs.sqlite predates priority,
    cancel_requested and seen_at. init_db must ADD those columns to an
    existing table, not require CREATE TABLE (which CREATE TABLE IF NOT
    EXISTS would skip anyway) and must never drop or recreate the table."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, document TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', result TEXT, error TEXT,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                ttft_s REAL, total_s REAL, tokens INTEGER, chunks INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO jobs (id, kind, document, created_at) VALUES (?,?,?,?)",
            ("preexisting", "summarise", "old data must survive", "2020-01-01"))
        conn.commit()
        conn.close()

        db.init_db(path)  # must not drop/recreate -- old row must survive

        job = db.get_job(path, "preexisting")
        assert job["document"] == "old data must survive"
        assert job["priority"] == 0            # new column, default applied
        assert job["cancel_requested"] == 0
        assert job["seen_at"] is None

        # And it must be usable by the new queue-control functions immediately.
        assert db.request_cancel(path, "preexisting") == "cancelled"
    finally:
        os.unlink(path)


# --- priority ordering (Part 2) -----------------------------------------------

def test_claim_next_pending_respects_priority_over_creation_order(dbpath):
    """A lower priority number must be claimed first, even if it was
    submitted later -- that is the entire point of letting an operator
    reorder the queue."""
    first_submitted = db.create_job(dbpath, "summarise", "a")
    second_submitted = db.create_job(dbpath, "summarise", "b")
    db.reorder_pending(dbpath, [second_submitted, first_submitted])
    claimed = db.claim_next_pending(dbpath)
    assert claimed["id"] == second_submitted


def test_reorder_pending_only_touches_pending_jobs(dbpath):
    """A job that has already been claimed must not be reprioritised by a
    reorder submitted after the fact -- claim_next_pending is where the new
    order takes effect, and only for jobs still waiting."""
    running_job = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)  # now running
    pending_job = db.create_job(dbpath, "summarise", "b")

    db.reorder_pending(dbpath, [running_job, pending_job])

    assert db.get_job(dbpath, running_job)["priority"] == 0  # untouched: not pending
    # pending_job is still assigned its POSITION in ordered_ids (1), even
    # though the entry before it was skipped -- reorder_pending does not
    # compact the sequence, it just needs relative order to be correct among
    # jobs that are actually pending, which a single job trivially satisfies.
    assert db.get_job(dbpath, pending_job)["priority"] == 1


def test_default_priority_ties_break_on_creation_order(dbpath):
    """Every job before this feature existed has priority=0. Ties among them
    must still resolve in submission order, so old behaviour is preserved."""
    first = db.create_job(dbpath, "summarise", "a")
    db.create_job(dbpath, "summarise", "b")
    assert db.claim_next_pending(dbpath)["id"] == first


# --- cancel / stop (Part 2) ---------------------------------------------------

def test_request_cancel_on_pending_job_is_immediate(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    assert db.request_cancel(dbpath, job_id) == "cancelled"
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "cancelled"
    assert job["finished_at"] is not None


def test_request_cancel_on_running_job_only_sets_the_flag(dbpath):
    """A running job cannot be stopped from here directly -- see
    worker.JobCancelled for why. request_cancel only sets the flag; the
    worker itself must observe it and finalise the status."""
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    assert db.request_cancel(dbpath, job_id) == "stopping"
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "running", "status must not change until the worker acts"
    assert job["cancel_requested"] == 1
    assert db.is_cancel_requested(dbpath, job_id) is True


def test_request_cancel_on_terminal_job_is_a_noop(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, job_id, "result", {})
    assert db.request_cancel(dbpath, job_id) == "done"
    assert db.get_job(dbpath, job_id)["status"] == "done"


def test_request_cancel_on_missing_job_returns_none(dbpath):
    assert db.request_cancel(dbpath, "does-not-exist") is None


def test_finish_cancelled_records_partial_progress(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.finish_cancelled(dbpath, job_id, {"chunks": 3, "total_s": 12.5})
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "cancelled"
    assert job["chunks"] == 3
    assert job["total_s"] == 12.5
    assert job["result"] is None
    assert job["error"] is None


def test_cancelled_is_distinct_from_failed(dbpath):
    """A user cancellation must not read as a cluster error."""
    cancelled = db.create_job(dbpath, "summarise", "a")
    db.request_cancel(dbpath, cancelled)
    failed = db.create_job(dbpath, "summarise", "b")
    db.claim_next_pending(dbpath)
    db.fail_job(dbpath, failed, "boom")

    statuses = {j["id"]: j["status"] for j in db.list_jobs(dbpath)}
    assert statuses[cancelled] == "cancelled"
    assert statuses[failed] == "failed"


# --- completion notification marker (Part 3) ----------------------------------

def test_count_unseen_only_counts_terminal_unacknowledged_jobs(dbpath):
    pending = db.create_job(dbpath, "summarise", "a")
    done = db.create_job(dbpath, "summarise", "b")
    db.claim_next_pending(dbpath)  # claims 'pending' (oldest) -- reclaim 'done' next
    # Force `done` into running/done explicitly regardless of claim order:
    db.complete_job(dbpath, done, "result", {})
    assert db.count_unseen(dbpath) == 1  # only `done`; `pending` is not terminal

    db.mark_seen(dbpath, done)
    assert db.count_unseen(dbpath) == 0


def test_mark_all_seen_clears_the_banner(dbpath):
    a = db.create_job(dbpath, "summarise", "a")
    b = db.create_job(dbpath, "summarise", "b")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, a, "r", {})
    db.claim_next_pending(dbpath)
    db.fail_job(dbpath, b, "boom")

    assert db.count_unseen(dbpath) == 2
    db.mark_all_seen(dbpath)
    assert db.count_unseen(dbpath) == 0


def test_mark_seen_is_idempotent(dbpath):
    """Viewing a job page twice must not raise or misbehave."""
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, job_id, "r", {})
    db.mark_seen(dbpath, job_id)
    first_seen_at = db.get_job(dbpath, job_id)["seen_at"]
    db.mark_seen(dbpath, job_id)
    assert db.get_job(dbpath, job_id)["seen_at"] == first_seen_at


# --- chunk-summary model identity (Part 1 support) ----------------------------

def test_get_recorded_model_with_no_chunks_is_none(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    assert db.get_recorded_model(dbpath, job_id) is None


def test_get_recorded_model_returns_the_common_model(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.save_chunk_summaries(
        dbpath, job_id, [{"index": 0, "start": 0, "end": 1, "summary": "s"}],
        model="model-A")
    assert db.get_recorded_model(dbpath, job_id) == "model-A"


def test_get_recorded_model_is_none_when_rows_disagree(dbpath):
    """Defensive: this should not happen in normal operation (every chunk of
    one run is saved with the SAME model), but if rows ever disagree, treat
    the model as unknown rather than picking one arbitrarily."""
    job_id = db.create_job(dbpath, "summarise", "a")
    db.save_chunk_summaries(
        dbpath, job_id, [{"index": 0, "start": 0, "end": 1, "summary": "s0"}],
        model="model-A")
    db.save_chunk_summaries(
        dbpath, job_id, [{"index": 1, "start": 1, "end": 2, "summary": "s1"}],
        model="model-B")
    assert db.get_recorded_model(dbpath, job_id) is None


def test_init_chunks_upgrades_a_pre_existing_chunk_summaries_table():
    """chunk_summaries predates the `model` column too (F-provenance landed
    before resumability). init_chunks must add it without touching existing
    rows, exactly like the jobs-table migration above."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE chunk_summaries (
                job_id TEXT NOT NULL, idx INTEGER NOT NULL,
                start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
                summary TEXT NOT NULL, PRIMARY KEY (job_id, idx)
            )
        """)
        conn.execute(
            "INSERT INTO chunk_summaries VALUES ('j1', 0, 0, 5, 'old summary')")
        conn.commit()
        conn.close()

        db.init_chunks(path)  # must not drop/recreate -- old row must survive

        rows = db.get_chunk_summaries(path, "j1")
        assert len(rows) == 1
        assert rows[0]["summary"] == "old summary"
        assert rows[0]["model"] is None  # new column, no default value assumed
    finally:
        os.unlink(path)


def test_delete_chunk_summaries_removes_all_rows_for_the_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.save_chunk_summaries(
        dbpath, job_id, [{"index": 0, "start": 0, "end": 1, "summary": "s"}],
        model="model-A")
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 1
    db.delete_chunk_summaries(dbpath, job_id)
    assert len(db.get_chunk_summaries(dbpath, job_id)) == 0


# --- The check-then-ALTER race (F20's shape, arriving through a new door) ----
#
# `model` was added to chunk_summaries by the RESUMABILITY branch, as a lazy
# check-then-ALTER migration run from inside every chunk read and write. R
# concurrent workers arrived on the FAN-OUT branch. Neither branch's tests
# could catch the combination, because neither branch contained the other's
# code -- so the first thing that would have exercised it is the live
# /opt/missing-link/jobs.sqlite, which does not yet have the column, on the
# first document to be chunked after deployment. The loser of the race raises
# OperationalError, run_one's broad except turns that into a FAILED job, and a
# multi-hour overnight summarisation is gone.
#
# Both tests below start from a database that GENUINELY lacks the column --
# built with the pre-resumability schema, not by dropping a column from the
# current one -- because the window closes for good once the ALTER succeeds
# once. Both fail reliably against the pre-fix code (20/20 runs) and pass
# reliably against the fix (20/20).

_PRE_MIGRATION_CHUNK_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_summaries (
    job_id     TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    start_char INTEGER NOT NULL,
    end_char   INTEGER NOT NULL,
    summary    TEXT NOT NULL,
    PRIMARY KEY (job_id, idx)
);
"""


@pytest.fixture
def pre_migration_dbpath():
    """A jobs.sqlite in the state the deployed one is in: no `model` column.

    WAL, like every database this module opens -- the journal mode changes the
    lock timing enough to matter to the race being tested here.
    """
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_PRE_MIGRATION_CHUNK_SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunk_summaries)")]
    conn.close()
    assert "model" not in cols, f"fixture is not pre-migration: {cols}"
    yield path
    os.unlink(path)


def _run_concurrently(fn, n_workers=5):
    """Run fn(i) on n_workers threads released together. Returns exceptions."""
    import threading
    barrier = threading.Barrier(n_workers)
    errors = []
    lock = threading.Lock()

    def work(i):
        barrier.wait()   # maximise the overlap rather than hoping for it
        try:
            fn(i)
        except Exception as exc:                     # noqa: BLE001 -- recording
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_chunk_writes_survive_a_pending_migration(pre_migration_dbpath):
    """The real deploy path: init_db once, then R workers writing chunks.

    This is what a restart of missing-link.service against the live database
    does. init_db must leave the schema fully migrated, so that the write path
    -- which R workers run concurrently -- performs no migration at all.
    """
    path = pre_migration_dbpath
    db.init_db(path)                                  # lifespan does this once

    errors = _run_concurrently(lambda i: db.save_chunk_summaries(
        path, f"job{i}",
        [{"index": 0, "start": 0, "end": 10, "summary": f"summary {i}"}],
        model="model-A"))

    assert not errors, f"concurrent chunk writes raised: {errors!r}"
    for i in range(5):
        rows = db.get_chunk_summaries(path, f"job{i}")
        assert len(rows) == 1, f"job{i} lost its chunk: {rows!r}"
        assert rows[0]["model"] == "model-A"


def test_concurrent_init_chunks_does_not_raise_duplicate_column(pre_migration_dbpath):
    """The safety net, tested directly.

    init_db is the only production caller now, and it runs once at startup --
    but tests call init_chunks directly, two PROCESSES can still overlap on a
    service restart, and the next person to add a column will not read this
    file first. So _add_missing_columns must be safe on its own terms, not
    merely never called concurrently.
    """
    import sqlite3
    path = pre_migration_dbpath

    errors = _run_concurrently(lambda i: db.init_chunks(path))

    assert not errors, f"concurrent init_chunks raised: {errors!r}"
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunk_summaries)")]
    conn.close()
    assert "model" in cols, f"migration did not run: {cols}"


def test_add_missing_columns_still_raises_on_anything_but_a_duplicate(dbpath):
    """The narrow catch must not have become a broad one.

    Swallowing OperationalError as a CLASS would hide a missing table, a
    corrupt file or a disk-full write behind a silently-unmigrated schema --
    the "degrade instead of refuse" failure this project has hit four times
    (F21, F34, F36, F38). Only the exact duplicate-column message is absorbed.
    """
    import sqlite3
    conn = sqlite3.connect(dbpath)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            db._add_missing_columns(conn, "no_such_table", [("model", "TEXT")])
    finally:
        conn.close()
    assert "duplicate column" not in str(caught.value).lower()

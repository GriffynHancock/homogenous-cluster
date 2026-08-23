"""SQLite job store.

Jobs must outlive any single process run -- a queue that loses work on restart
defeats the point of an async runner whose jobs take hours.
"""
import sqlite3
import uuid
from datetime import datetime, timezone

# Table creation and index creation are kept as SEPARATE scripts (not one
# executescript) because the indexes reference `priority`, which does not
# exist yet on a pre-upgrade database -- CREATE TABLE IF NOT EXISTS is a
# no-op there, but a CREATE INDEX naming a missing column is not, and would
# fail init_db on every startup against the live database until an operator
# noticed. _add_missing_columns must run between the two (see init_db).
TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    document    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    ttft_s      REAL,
    total_s     REAL,
    tokens      INTEGER,
    chunks      INTEGER,
    instruction      TEXT,
    priority         INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    seen_at          TEXT
);
"""
INDEX_SCHEMA = """
-- rowid cannot be named in an index (SQLite rejects it), but it is the
-- implicit tiebreaker anyway, so (status, created_at) is sufficient.
CREATE INDEX IF NOT EXISTS idx_jobs_pending
    ON jobs (status, created_at);
-- Separate name rather than redefining idx_jobs_pending above: CREATE INDEX
-- IF NOT EXISTS is a no-op against an index that already exists under that
-- name, so reusing it on an upgraded database would silently keep the OLD
-- definition and never pick up the new priority column.
CREATE INDEX IF NOT EXISTS idx_jobs_pending_order
    ON jobs (status, priority, created_at);
"""
# Kept for backward compatibility with anything importing db.SCHEMA directly.
SCHEMA = TABLE_SCHEMA + INDEX_SCHEMA

# Columns added after the initial release. Guarded with a table_info check
# rather than a bare ALTER TABLE, because ALTER TABLE ADD COLUMN has no
# "IF NOT EXISTS" clause and this must run, harmlessly, against the live
# database at /opt/missing-link/jobs.sqlite on every startup (CLAUDE.md:
# schema changes must be additive and idempotent, never a drop/recreate).
_JOBS_NEW_COLUMNS = [
    # `instruction` predates the queue-control columns but reaches a deployed
    # database by exactly the same route: CREATE TABLE IF NOT EXISTS is a no-op
    # against the live /opt/missing-link/jobs.sqlite, so a column named only in
    # TABLE_SCHEMA never actually arrives there.
    ("instruction", "TEXT"),
    ("priority", "INTEGER NOT NULL DEFAULT 0"),
    ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("seen_at", "TEXT"),
    # Which inference endpoint (LLAMA_URLS entry) claimed and ran this job.
    # Set once at claim time (see worker.run_one / set_job_endpoint) and left
    # in place through to done/failed/cancelled, so a FAILED job still shows
    # which node it died on -- under fan-out a node dying costs 1/R of
    # throughput, and "which one" is exactly what ENDPOINT_STATE (in-memory,
    # cleared once the worker moves on) cannot answer after the fact.
    ("endpoint", "TEXT"),
    # How many times this job has been STARTED (incremented by
    # claim_next_pending, so a claim that ends in a crashed worker counts too,
    # not only a clean transient failure). Bounds worker.MAX_ATTEMPTS: a job
    # whose backend keeps dying is retried a few times and then fails FOR
    # GOOD, with an error that says so. Without a persisted counter a retry
    # loop is unbounded, which on an unattended overnight queue is worse than
    # not retrying at all.
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    # Earliest time this job may be claimed again, ISO-8601 UTC, NULL for a
    # job that has never been retried. This is the BACKOFF, and it is stored
    # on the row rather than slept on in the worker deliberately: a sleeping
    # worker cannot run anybody else's job, so a permanently-dead backend
    # would starve the whole queue. A timestamp lets the retried job wait
    # while every other pending job keeps moving. See claim_next_pending.
    ("retry_after", "TEXT"),
    # How many persisted chunk summaries the CURRENT attempt reused instead of
    # recomputing (0 when it started from scratch). Written once per attempt by
    # worker.run_one, after the model/instruction resume check has decided. It
    # is the evidence for the only thing that makes a retry worth doing at all
    # -- that it resumes rather than restarts -- and without it the job page
    # can only guess (chunk rows are deleted when the resume is rejected, so
    # "chunks exist" is not the same claim).
    ("resumed_chunks", "INTEGER"),
]
_CHUNK_NEW_COLUMNS = [
    # Which model produced this row, from LlamaClient.model_name() (/props).
    # Needed so a resumed job can tell whether its persisted chunks came from
    # the model that is currently serving -- see save_chunk_summaries and
    # worker.run_one. NULL for rows written before this column existed, which
    # get_recorded_model treats as "unknown", not as "matches".
    ("model", "TEXT"),
    # Which operator instruction was in effect when this row was produced.
    # `instruction` shapes the map prompt exactly as much as the model does
    # (see worker.build_prompt), so a resume that only checks the model and
    # not this would mix chunk summaries written under two different
    # instructions -- the same unsoundness the model check exists to
    # prevent, arriving through a different door. See get_recorded_instruction
    # and worker.run_one's resume check.
    ("instruction", "TEXT"),
    # llama-server's OWN timings for the call that produced this chunk --
    # never derived from wall-clock (F17: a wall-clock-derived TTFT once
    # reported 0.015s against a real 89s). NULL for a resumed chunk (no new
    # call was made) or a client that does not report timings (e.g. tests'
    # FakeClient) -- a missing measurement must stay NULL, never a fabricated
    # 0, per this project's standing rule (ttft_s is None, not 0.0). Live
    # per-chunk tok/s and a per-job ETA are derived from these -- see
    # worker.chunk_rate_stats and get_chunk_timings. Prefill and generation
    # are kept as SEPARATE counters (prompt_* vs predicted_*), never blended
    # into one rate: they run at very different speeds on this hardware
    # (prefill ~16-25 t/s, generation ~5-6 t/s) and a blended figure would be
    # dominated by whichever phase happened to be running when read.
    ("prompt_n", "INTEGER"),
    ("prompt_ms", "REAL"),
    ("predicted_n", "INTEGER"),
    ("predicted_ms", "REAL"),
    # When this row was written (server clock, stamped by save_chunk_summaries
    # itself -- not supplied by the caller, so it cannot drift from whichever
    # clock actually persisted it). Lets a live-progress view show "last chunk
    # landed Ns ago" and is a cheap sanity check against a stalled worker.
    ("completed_at", "TEXT"),
]


def _add_missing_columns(conn, table, coldefs):
    """Additive, idempotent schema upgrade. Safe to call concurrently.

    The PRAGMA and the ALTER are two separate statements in two separate
    transactions, so this is a CHECK-THEN-ACT: two callers can both read "the
    column is missing" and both then try to add it, and the loser gets
    `OperationalError: duplicate column name: X`. That was harmless while the
    runner was single-worker, and stopped being harmless the moment there was
    one worker per inference endpoint -- run_one's broad except turns the
    OperationalError into a FAILED job, i.e. a whole night of cluster work
    thrown away by a schema upgrade that had in fact succeeded. Same shape as
    F20: a race a single-threaded test suite cannot see, on a queue whose jobs
    cost hours.

    The primary defence is that this now runs ONCE at startup (see init_db)
    rather than on every chunk write. This handler is the belt to that's braces
    -- two PROCESSES racing startup are still possible (a systemd restart
    overlapping the outgoing process), and tests call these helpers directly.

    The catch is deliberately narrow: only the exact "duplicate column name:
    <this column>" message is swallowed, and only for the column we were just
    trying to add. Everything else -- a missing table, a locked database, a
    disk-full write -- re-raises untouched. A handler that swallowed
    OperationalError as a CLASS would be the "degrade instead of refuse"
    pattern this project has been burned by four times (F21, F34, F36, F38).
    """
    have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in coldefs:
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError as exc:
            if str(exc).lower() != f"duplicate column name: {name}".lower():
                raise
            # Another caller added it between our PRAGMA and our ALTER. The
            # column exists with the declaration we wanted, which is the whole
            # postcondition of this function, so there is nothing to do.


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(path):
    # isolation_level=None puts us in autocommit mode so that BEGIN IMMEDIATE
    # below means what it says, rather than being deferred by the driver.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets the web process read job status while the worker holds a write
    # transaction. Without it, a long-running claim blocks the status page.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(path):
    """Create or upgrade the ENTIRE schema. Call once, at process startup.

    This is the single migration entry point: jobs, chunk_summaries,
    batch_documents, job_failures and corpus_documents all get created and
    upgraded here, so that after one call every table this module touches is
    present and current.

    That is deliberate, and it is a change from how this worked. The chunk and
    batch tables used to be initialised LAZILY, from inside each read and write
    that used them (`init_chunks` at the top of save_chunk_summaries,
    get_chunk_summaries, get_recorded_model, delete_chunk_summaries). Two
    things were wrong with that once the runner grew one worker per inference
    endpoint:

    1. It put a schema migration on the hot path of every chunk write -- a
       PRAGMA and a possible ALTER TABLE per chunk, so 26 pointless migrations
       for a 26-chunk document.
    2. It ran that migration CONCURRENTLY, from R workers at once, against a
       check-then-ACT that cannot survive it. See _add_missing_columns.

    Running it once, before any worker task is created (app.lifespan), removes
    the concurrency from the migration entirely rather than merely surviving
    it. _add_missing_columns is hardened too, but as a second line of defence.
    """
    conn = _connect(path)
    try:
        # Tables, then missing columns, then indexes -- in that order, and as
        # three separate steps. CREATE TABLE IF NOT EXISTS is a no-op against an
        # already-deployed jobs.sqlite, so every column added after the initial
        # release arrives only via _add_missing_columns; and idx_jobs_pending_order
        # names `priority`, so creating it before that column exists would fail
        # init_db on every startup against the live database.
        conn.executescript(TABLE_SCHEMA)
        _add_missing_columns(conn, "jobs", _JOBS_NEW_COLUMNS)
        conn.executescript(INDEX_SCHEMA)
    finally:
        conn.close()
    # The other tables are initialised through their own functions, which
    # live next to their own schemas further down this file rather than being
    # hoisted up here away from them.
    init_chunks(path)
    init_batch_documents(path)
    init_job_failures(path)
    init_corpus_documents(path)


def create_job(path, kind, document, instruction=None):
    job_id = uuid.uuid4().hex[:12]
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, kind, document, instruction, created_at) "
            "VALUES (?,?,?,?,?)",
            (job_id, kind, document, instruction, _now()),
        )
    finally:
        conn.close()
    return job_id


def get_job(path, job_id):
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_jobs(path):
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def claim_next_pending(path):
    """Atomically move the highest-priority pending job to running and return it.

    BEGIN IMMEDIATE is load-bearing. A plain SELECT-then-UPDATE lets two
    workers read the same pending row and both claim it -- the job then runs
    twice, which on a multi-hour summarisation job is an expensive way to find
    out about a race. IMMEDIATE takes the write lock up front, so the second
    worker blocks until the first has committed its UPDATE and then sees the
    row as 'running'.

    Ordering extends (not replaces) the original created_at/rowid tiebreak:
    `priority` (lower first) is the operator-controlled queue order set by
    reorder_pending, and created_at/rowid remain the tiebreak among jobs at
    the same priority (including every job before this feature existed, which
    all default to priority 0). This is the ONE place ordering is decided, so
    a reorder takes effect "from the next claim" automatically -- a job
    already claimed and running is not touched.

    RETRY BACKOFF is applied HERE, as a `retry_after` predicate, not as a sleep
    in the worker: a job requeued after a transient backend failure (see
    schedule_retry) is simply invisible to the claim until its timestamp
    passes, so the queue keeps serving every other job in the meantime. A
    worker that slept instead would hand a dead backend the power to stall
    jobs that had nothing to do with it.

    `attempts` is incremented as part of the same atomic claim, so it counts
    STARTS rather than clean failures -- a claim whose worker is then OOM-killed
    and requeued by requeue_running has still consumed an attempt, which is
    what bounds a crash loop as well as a retry loop. The returned dict carries
    the POST-increment value (the row was read before the UPDATE), because
    worker.run_one's retry decision is about the attempt it is currently on.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='pending' "
                "  AND (retry_after IS NULL OR retry_after <= ?) "
                "ORDER BY priority ASC, created_at ASC, rowid ASC LIMIT 1",
                (_now(),),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            started = _now()
            conn.execute(
                "UPDATE jobs SET status='running', started_at=?, "
                "attempts=attempts+1, retry_after=NULL WHERE id=?",
                (started, row["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    job = dict(row)
    job["status"] = "running"
    job["started_at"] = started
    job["attempts"] = (row["attempts"] or 0) + 1
    job["retry_after"] = None
    return job


def complete_job(path, job_id, result, metrics):
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE jobs SET status='done', result=?, finished_at=?, "
            "ttft_s=?, total_s=?, tokens=?, chunks=? WHERE id=?",
            (result, _now(), metrics.get("ttft_s"), metrics.get("total_s"),
             metrics.get("tokens"), metrics.get("chunks"), job_id),
        )
    finally:
        conn.close()


def fail_job(path, job_id, error):
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT attempts, endpoint FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error, _now(), job_id),
        )
        if row is not None:
            _record_job_failure(conn, job_id, row["attempts"], row["endpoint"], error)
    finally:
        conn.close()


def schedule_retry(path, job_id, error, retry_at):
    """Return ONE job to 'pending' for another attempt after `retry_at`.

    The mechanism half of the retry; the POLICY -- which failures are worth
    retrying at all, how many times, and how long to wait -- lives in
    worker.classify_failure / worker.retry_delay_seconds, so that this module
    stays a job store and the question "is this failure the document's fault or
    the cluster's?" stays in one place.

    Distinct from requeue_running(), which is a blanket startup sweep for jobs
    stranded by a dead PROCESS. This is per-job, deliberate, and carries a
    timestamp.

    `error` is kept on the row rather than cleared, so a job sitting in
    'pending' awaiting a retry still says WHY -- otherwise the most alarming
    state in the system (the backend died mid-document) would look identical to
    a job that has simply never run. started_at/finished_at are cleared so the
    next attempt's elapsed time is that attempt's, not a blend of two.

    Also appends a row to job_failures (see schema below) BEFORE clobbering
    `jobs.error` with the next attempt's message. `jobs.error` only ever holds
    the LATEST message -- complete_job leaves it in place rather than clearing
    it, so a job that failed once and then succeeded still has *a* trail, but a
    single string cannot say "failed on endpoint A twice then succeeded on B",
    and a second failure on the same job overwrites the first without a copy.
    job_failures is the append-only copy that survives both.
    """
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT attempts, endpoint FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        conn.execute(
            "UPDATE jobs SET status='pending', error=?, retry_after=?, "
            "started_at=NULL, finished_at=NULL WHERE id=?",
            (error, retry_at, job_id),
        )
        if row is not None:
            _record_job_failure(conn, job_id, row["attempts"], row["endpoint"], error)
    finally:
        conn.close()


# --- failure history ----------------------------------------------------
# `jobs.error` (above) has always held exactly one string: whatever the most
# recent attempt said. That is enough for a job sitting in 'pending' awaiting
# retry (there is only one live message) or a job that failed for good on its
# LAST attempt. It loses information the moment a job has MORE than one
# attempt worth recording -- a job that failed on endpoint A, retried, failed
# on endpoint A again, then succeeded on B is a genuine cluster finding ("A is
# flaky"), and the single-string column cannot express it: each retry
# overwrites the last, and completion leaves whatever string happened to be
# there. This table is the append-only copy: one row per failed attempt,
# attempt number and endpoint recorded structurally rather than parsed back
# out of prose, so it survives both the next retry's overwrite and the job's
# eventual success.
#
# Written from INSIDE schedule_retry/fail_job (same connection, so a crash
# between the two would have to land in a few milliseconds of gap between two
# statements on one connection) rather than added as a new call site in
# worker.py, which this task does not touch.
JOB_FAILURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    attempt     INTEGER,
    endpoint    TEXT,
    error       TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_failures_job
    ON job_failures (job_id, id);
"""


def init_job_failures(path):
    """Create job_failures. Called from init_db at startup ONLY -- same
    reasoning as init_chunks/init_batch_documents: a migration on every write
    is both wasted work and a race between concurrent per-endpoint workers
    (see _add_missing_columns' docstring). This table has no added columns
    yet, so it is pure CREATE ... IF NOT EXISTS, like batch_documents.
    """
    conn = _connect(path)
    try:
        conn.executescript(JOB_FAILURES_SCHEMA)
    finally:
        conn.close()


def _record_job_failure(conn, job_id, attempt, endpoint, error):
    """Append one row. Takes an OPEN connection (not a path) because both
    call sites (schedule_retry, fail_job) must log on the SAME connection as
    the status UPDATE they accompany, not open a second one.
    """
    conn.execute(
        "INSERT INTO job_failures (job_id, attempt, endpoint, error, occurred_at) "
        "VALUES (?,?,?,?,?)",
        (job_id, attempt, endpoint, error, _now()),
    )


def get_job_failures(path, job_id):
    """This job's full failure history, oldest first. Empty for a job that
    has never failed an attempt -- including every job that predates this
    table, which is the correct answer for those too (there is nothing to
    show, not "unknown").
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT attempt, endpoint, error, occurred_at FROM job_failures "
            "WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def record_resume(path, job_id, n_reused):
    """Record how many persisted chunk summaries THIS attempt reused.

    Called once per attempt by worker.run_one, with 0 when the attempt is
    starting the map phase from scratch -- explicitly, so a value left over
    from an earlier attempt cannot be read as this one's.
    """
    conn = _connect(path)
    try:
        conn.execute("UPDATE jobs SET resumed_chunks=? WHERE id=?",
                     (int(n_reused), job_id))
    finally:
        conn.close()


def requeue_running(path):
    """Return every 'running' job to 'pending'. Returns the number moved.

    Call this at worker startup. Jobs here take hours, so a power cut or an
    OOM kill mid-job is a routine event, not an exceptional one -- and a job
    stuck in 'running' with no process behind it is invisible work that never
    completes and never reports an error.

    Missing Link now runs one concurrent worker per inference endpoint
    (job-level fan-out), but that does NOT make this unsafe: it is called
    exactly ONCE per process, at startup, BEFORE any worker task is created
    (see app.py's lifespan). So every 'running' row it finds was left behind by
    a PREVIOUS process, never by a sibling worker in this one. If that ordering
    ever changes -- e.g. this gets called per-worker, or after the worker tasks
    already exist -- this must key off a heartbeat or a worker id rather than
    blanket-requeueing everything, or a live worker's in-progress job would be
    yanked back to 'pending' and claimed a second time.
    """
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='pending', started_at=NULL WHERE status='running'"
        )
        return cur.rowcount
    finally:
        conn.close()


# --- queue control: cancel, stop, reorder ------------------------------------
# "cancelled" is a TERMINAL STATUS DISTINCT FROM "failed", by design: a user
# stopping a job they no longer want is not an error, and seconds_per_chunk /
# throughput_stats both filter on status='done', so a cancelled job never
# pollutes the cluster's self-calibrated rate the way a real failure legitimately
# might skew operator attention.

def request_cancel(path, job_id):
    """Ask for a job to stop. Returns the resulting status, or None if no such job.

    A PENDING job is cancelled immediately -- nothing is running, so there is
    no race and no reason to wait.

    A RUNNING job cannot be stopped from here: the worker thread is holding a
    blocking HTTP call to llama-server that can take minutes, and there is no
    clean way to abort that call from outside the thread that made it (closing
    the socket does not reliably stop server-side generation, and llama-server
    exposes no cancellation endpoint). So a running job only gets a REQUEST --
    cancel_requested=1 -- and worker.run_one polls is_cancel_requested()
    between chunks and finalises the status itself once it is actually safe to
    stop. See worker.JobCancelled for the honest statement of how far this goes.

    BEGIN IMMEDIATE for the same reason as claim_next_pending: this is a
    status transition, and it must not race the worker's own claim/finalise
    transitions on the same row.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            status = row["status"]
            if status == "pending":
                conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                    (_now(), job_id))
                conn.execute("COMMIT")
                return "cancelled"
            if status == "running":
                conn.execute(
                    "UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                conn.execute("COMMIT")
                return "stopping"
            conn.execute("ROLLBACK")
            return status  # already terminal (done/failed/cancelled) -- nothing to do
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def is_cancel_requested(path, job_id):
    """Polled by worker.run_one between chunks. See request_cancel."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["cancel_requested"])


def revive_job(path, job_id):
    """Explicit OPERATOR OVERRIDE: return a TERMINAL job to 'pending' for
    another attempt, with a fresh attempt budget.

    THE GAP THIS FILLS. A job that exhausts MAX_ATTEMPTS, or fails
    permanently, or is cancelled, has no way back except hand-written SQL --
    yet its chunk_summaries rows are still sitting on disk and WOULD be reused
    (worker.run_one's resume check) if the job ever ran again. Without this,
    the operator's only recovery path after a night the cluster was broken is
    to resubmit the document from scratch, discarding work that is sitting
    right there. See templates/job.html for what the operator is told, BEFORE
    they click, about what those persisted chunks will actually get them --
    this function only performs the transition, it does not predict the
    outcome (that depends on which model is serving at the NEXT claim, which
    this function has no way to know and does not guess at).

    Revivable statuses: 'failed' and 'cancelled'. Deliberately NOT 'done'
    (nothing was lost -- there is no recovery to perform), and NOT 'pending'
    or 'running' (already active; reviving those would race the very claim
    this is meant to unstick). CANCELLATION IS INCLUDED ON PURPOSE: a
    cancelled job is not a failure and carries no signal that a retry would
    fail identically the way a permanent failure does -- it was stopped by
    request, mid-document, with whatever chunks had completed already
    persisted exactly like a crash would leave them. Refusing to revive it
    would make "I stopped this for now" a one-way door, which is a worse
    property for an operator-facing stop button to have than the one it has
    today.

    RESETS, and why each one: `attempts` -> 0 so the revived run gets a FULL
    fresh budget (worker.MAX_ATTEMPTS starts) rather than inheriting an
    exhausted one -- claim_next_pending increments it to 1 on the next claim,
    same as a first-ever attempt. `retry_after` -> NULL, in case any residual
    timestamp is present (normally already NULL by the time a job reaches a
    terminal status -- claim_next_pending clears it at claim time -- cleared
    again here defensively, since a job revived while a backoff timestamp
    somehow survived must be claimable immediately, not silently invisible to
    claim_next_pending's predicate until that timestamp passed).
    `cancel_requested` -> 0: LOAD-BEARING for a cancelled job specifically --
    request_cancel sets this to 1 for a job cancelled while RUNNING, and
    nothing before this function ever clears it back to 0. Leaving it set
    would mean a revived job calls worker._should_stop() and cancels itself
    again before the first chunk, silently reproducing the exact status this
    was meant to escape. `error`/`result`/`started_at`/`finished_at` -> NULL,
    the same fields schedule_retry clears and for the same reason: the next
    attempt's state must read as its own, not a blend with the terminal run's.
    `seen_at` -> NULL, because a revived job is not "seen" in its new,
    unfinished state -- it is exactly what the index page's "did anything
    finish while I was away" banner exists to (re)surface once it lands
    somewhere terminal again.

    BEGIN IMMEDIATE for the same reason as claim_next_pending/request_cancel:
    this is a status transition on a row a worker could be touching (a
    concurrent auto-retry finalising, or another operator click), and must
    not interleave with either.

    Returns the string "revived" on success -- deliberately NOT "pending",
    even though "pending" is the status this writes: a job asked to revive
    while it happens to ALREADY be pending (a stale page, a double click) has
    a CURRENT status of "pending" too, and reusing that string for the
    success case would make request_cancel-style "return the current status"
    indistinguishable from "the transition just happened", exactly the bug a
    caller comparing `result != "pending"` would silently get wrong. Returns
    None if no such job exists, or the job's CURRENT status unchanged if it
    is not in a revivable state -- the caller turns that into an honest 409
    rather than silently doing nothing.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            status = row["status"]
            if status not in ("failed", "cancelled"):
                conn.execute("ROLLBACK")
                return status
            conn.execute(
                "UPDATE jobs SET status='pending', error=NULL, result=NULL, "
                "started_at=NULL, finished_at=NULL, attempts=0, retry_after=NULL, "
                "cancel_requested=0, seen_at=NULL WHERE id=?",
                (job_id,))
            conn.execute("COMMIT")
            return "revived"
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def finish_cancelled(path, job_id, metrics=None):
    """Finalise a RUNNING job that honoured a cooperative stop request.

    Distinct from fail_job for the same reason 'cancelled' is a distinct
    status: this is the worker reporting "I stopped because I was asked to",
    not "something went wrong". metrics carries whatever partial progress was
    made (chunks completed, elapsed time) -- there is no result/error to store.
    """
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE jobs SET status='cancelled', finished_at=?, "
            "chunks=?, total_s=? WHERE id=?",
            (_now(), (metrics or {}).get("chunks"),
             (metrics or {}).get("total_s"), job_id),
        )
    finally:
        conn.close()


def reorder_pending(path, ordered_ids):
    """Set priority = position in ordered_ids (0-based) for PENDING jobs only.

    Jobs not in ordered_ids, or no longer pending (claimed between the reorder
    page loading and the save being submitted), are left untouched -- a job
    already running is not something reordering can affect anyway, and
    claim_next_pending is where the new order actually takes effect, on the
    NEXT claim, not this one. BEGIN IMMEDIATE so a concurrent claim cannot
    interleave with a partial reorder.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for i, job_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE jobs SET priority=? WHERE id=? AND status='pending'",
                    (i, job_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# --- completion notification --------------------------------------------------
# Part 3: for a submit-and-leave tool, "how do I know it's done without
# polling?" is close to the most important missing feature. seen_at is the
# dependency-free half of the answer: NULL means "finished since this was last
# looked at". The other half -- actually pushing a notification out (email,
# webhook) -- is a documented hook point in worker.notify_completion, not
# implemented here: CLAUDE.md rules out an SMTP dependency or any outbound
# network call from this project.

def mark_seen(path, job_id):
    """Acknowledge one job's completion. Called when its detail page is viewed."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE jobs SET seen_at=? WHERE id=? AND seen_at IS NULL",
            (_now(), job_id))
    finally:
        conn.close()


def mark_all_seen(path):
    """Acknowledge every finished job at once, for the index page's banner."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE jobs SET seen_at=? "
            "WHERE seen_at IS NULL AND status IN ('done','failed','cancelled')",
            (_now(),))
    finally:
        conn.close()


def count_unseen(path):
    """How many finished jobs nobody has looked at yet -- the index banner."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs "
            "WHERE seen_at IS NULL AND status IN ('done','failed','cancelled')"
        ).fetchone()
    finally:
        conn.close()
    return row["n"]


def seconds_per_chunk(path, min_samples=2):
    """Median seconds-per-chunk from COMPLETED jobs. None if too few samples.

    Self-calibrating: the estimate improves as the cluster runs, rather than
    relying on a constant that goes stale the moment the model, engine or node
    count changes. That is the whole point -- this project's recurring failure
    mode is inherited numbers nobody re-measured (F1, F28, and the 18-month-old
    dependency pins).

    Returns (median_seconds_per_chunk, n_samples) or (None, n).
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT total_s, chunks FROM jobs "
            "WHERE status='done' AND total_s IS NOT NULL "
            "AND chunks IS NOT NULL AND chunks > 0"
        ).fetchall()
    finally:
        conn.close()

    rates = [r["total_s"] / r["chunks"] for r in rows]   # oldest -> newest
    if len(rates) < min_samples:
        return None, len(rates)

    # RECENCY-WEIGHTED, not a plain median. The rate genuinely changes under us --
    # a different model, engine, quant, --parallel setting or node count all move
    # it, and this session changed several of those in one evening. An unweighted
    # mean over all history would keep quoting a rate the cluster no longer has.
    # Exponential weights, newest heaviest, halving every ~5 jobs.
    decay = 0.87
    num = den = 0.0
    for age, rate in enumerate(reversed(rates)):     # age 0 == most recent
        w = decay ** age
        num += w * rate
        den += w
    return num / den, len(rates)


def pending_chunk_backlog(path):
    """(n_pending_jobs, running_count). Queue depth ahead of a new submission."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT "
            " SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS p, "
            " SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS r "
            "FROM jobs"
        ).fetchone()
    finally:
        conn.close()
    return (row["p"] or 0), (row["r"] or 0)


CHUNK_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_summaries (
    job_id     TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    start_char INTEGER NOT NULL,
    end_char   INTEGER NOT NULL,
    summary    TEXT NOT NULL,
    PRIMARY KEY (job_id, idx)
);
"""


def init_chunks(path):
    """Create/upgrade chunk_summaries. Called from init_db at startup ONLY.

    Not called from the read/write helpers below any more -- see init_db for
    why (a per-chunk migration that R concurrent workers raced).
    """
    conn = _connect(path)
    try:
        conn.executescript(CHUNK_SCHEMA)
        _add_missing_columns(conn, "chunk_summaries", _CHUNK_NEW_COLUMNS)
    finally:
        conn.close()


def save_chunk_summaries(path, job_id, records, model=None, instruction=None):
    """Persist per-chunk summaries so the final output stays traceable.

    Without this the map step's output is consumed by the reduce step and thrown
    away, and no claim in the final summary can be checked against its source.

    Called ONE CHUNK AT A TIME by worker.run_one as each completes (not once at
    the end after the whole document finishes) -- that incremental persistence
    is what makes a killed job resumable instead of restarting from zero. See
    worker.run_one and the module-level report on when this used to be called.

    `model` records which model produced these summaries (from
    LlamaClient.model_name()), so a later resume attempt can tell whether it is
    safe to reuse them -- see get_recorded_model. `instruction` records the
    operator guidance in effect for this run, for the same reason -- see
    get_recorded_instruction.

    Each record MAY also carry `prompt_n`/`prompt_ms`/`predicted_n`/
    `predicted_ms` (from worker._last_timings, itself from llama-server's own
    `timings` object) -- absent for a resumed chunk or a client that reports
    no timings, and stored as NULL rather than a fabricated 0 when absent, per
    this project's standing rule that a missing measurement must look missing.
    `completed_at` is stamped here, from this process's own clock, not
    supplied by the caller.
    """
    now = _now()
    conn = _connect(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_summaries "
            "(job_id, idx, start_char, end_char, summary, model, instruction, "
            " prompt_n, prompt_ms, predicted_n, predicted_ms, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(job_id, r["index"], r["start"], r["end"], r["summary"], model, instruction,
              r.get("prompt_n"), r.get("prompt_ms"), r.get("predicted_n"),
              r.get("predicted_ms"), now)
             for r in records])
    finally:
        conn.close()


def get_chunk_summaries(path, job_id):
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT idx, start_char, end_char, summary, model FROM chunk_summaries "
            "WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_chunk_timings(path, job_id):
    """This job's OWN per-chunk timings, in chunk order, for rows that were
    actually timed (prompt_ms IS NOT NULL) -- excludes a resumed chunk (no new
    call was made for it) and rows written before this column existed.

    This is the raw data behind a PER-JOB live tok/s and ETA, as distinct from
    the cluster-wide average in seconds_per_chunk: this job's own measured
    rate is the better predictor of what THIS job will do next, once there is
    enough of it to trust -- see worker.chunk_rate_stats, which turns this
    into prefill/generation tok/s and a seconds-per-chunk figure.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT idx, prompt_n, prompt_ms, predicted_n, predicted_ms "
            "FROM chunk_summaries WHERE job_id=? AND prompt_ms IS NOT NULL "
            "ORDER BY idx", (job_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_recorded_model(path, job_id):
    """The model that produced this job's persisted chunk summaries, if known.

    Returns None -- meaning "do not trust a resume" -- when there are no
    persisted chunks, OR when the rows disagree on which model produced them
    (should not happen in normal operation, since every chunk of one run is
    saved with the same model; defensive anyway), OR when the recorded value
    itself is NULL (rows written before this column existed). Returning a
    single unambiguous string is deliberate: worker.run_one's resume check
    wants a clean equality test, not a set to reason about.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT model FROM chunk_summaries WHERE job_id=?",
            (job_id,)).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        return None
    return rows[0]["model"]


def get_recorded_instruction(path, job_id):
    """The operator instruction recorded against this job's persisted chunks.

    Returns (True, instruction) when every persisted chunk row agrees --
    including agreeing it was None, which is the ordinary case of no guidance
    given, and is trustworthy (unlike model=None, "no instruction" was already
    true of every job before this column existed, so it is not new
    information to distrust). Returns (False, None) when there are no
    persisted chunks, or the rows disagree (should not happen in normal
    operation -- every chunk of one run is saved with the same instruction;
    defensive anyway, same posture as get_recorded_model).

    Why this check exists at all: `instruction` shapes the map prompt exactly
    as much as the model does (see worker.build_prompt), so trusting a resume
    on model alone would let a job resumed after its guidance changed
    silently mix chunk summaries produced under two different instructions --
    the same unsoundness the model check exists to prevent. See worker.run_one.

    No lazy init_chunks() call here, deliberately -- see init_db's docstring
    for why that pattern was removed from every hot-path read/write.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT instruction FROM chunk_summaries WHERE job_id=?",
            (job_id,)).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        return False, None
    return True, rows[0]["instruction"]


def set_job_endpoint(path, job_id, url):
    """Record which inference endpoint claimed this job. See the `endpoint`
    column's note in _JOBS_NEW_COLUMNS for why this is persisted rather than
    read from the in-memory ENDPOINT_STATE (app.py), which is cleared the
    moment the worker moves on to its next iteration.
    """
    conn = _connect(path)
    try:
        conn.execute("UPDATE jobs SET endpoint=? WHERE id=?", (url, job_id))
    finally:
        conn.close()


def delete_chunk_summaries(path, job_id):
    """Discard a job's persisted chunk summaries.

    Used when a resume is attempted and the currently-serving model does not
    match (or cannot be confirmed to match) the model that produced the
    existing rows -- see worker.run_one. Chunk summaries are independent map
    outputs, but only across runs of the SAME model; mixing summaries from two
    different models into one reduce step is not sound, so the chosen response
    is to wipe and restart the map phase cleanly rather than risk a silent mix
    (see the report for why "discard and restart" was picked over "refuse").
    """
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM chunk_summaries WHERE job_id=?", (job_id,))
    finally:
        conn.close()


def throughput_stats(path, limit=10):
    """Observed tok/s over recent completed jobs, for a live readout.

    `tokens` is a WORD COUNT of the output, not a real token count -- the queue
    process deliberately does not carry the model's tokeniser. So this is labelled
    approximate wherever it is shown; it is a trend indicator, not a benchmark
    figure, and must never be quoted against docs/measurements.md.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT total_s, tokens, chunks, ttft_s FROM jobs "
            "WHERE status='done' AND total_s > 0 AND tokens IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    out_per_s = [r["tokens"] / r["total_s"] for r in rows]
    ttfts = [r["ttft_s"] for r in rows if r["ttft_s"]]
    return {
        "samples": len(rows),
        "out_words_per_s": sum(out_per_s) / len(out_per_s),
        "median_prefill_s": (sorted(ttfts)[len(ttfts) // 2] if ttfts else None),
        "last_job_s": rows[0]["total_s"],
        "last_job_chunks": rows[0]["chunks"],
    }


# --- Batch upload staging -----------------------------------------------------
# "Here are 40 case files" is the real workload (DESIGN-NOTES F gap 1), not one
# document at a time. A batch upload is a TWO-STEP, no-JS-required flow:
#
#   1. POST /batch with N files -- each is run through extract.extract() and
#      staged here as a row, accepted or refused, WITHOUT yet becoming a job
#      (a job needs a workflow `kind`, which the operator has not chosen yet).
#   2. The review page renders one row per staged document with a tick box per
#      workflow. Confirming turns each ticked (document, workflow) pair into a
#      real job via create_job().
#
# Kept as its own table rather than reusing `jobs` so a refused file (bad PDF,
# unsupported format) can be shown to the operator without ever being a job.

BATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_documents (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT NOT NULL,
    filename    TEXT NOT NULL,
    text        TEXT NOT NULL,
    preview     TEXT NOT NULL,
    status      TEXT NOT NULL,  -- 'ready' (extracted, awaiting workflow ticks)
                                 -- or 'refused' (extraction failed -- see error)
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_documents_batch
    ON batch_documents (batch_id);
"""


def init_batch_documents(path):
    """Create batch_documents. Called from init_db at startup ONLY.

    Unlike chunk_summaries this table has never needed an added column, so it
    is pure CREATE ... IF NOT EXISTS and was never exposed to the race in
    _add_missing_columns. It is hoisted anyway, for one initialisation path
    rather than two conventions, and so the FIRST column added to it does not
    quietly reintroduce the bug.
    """
    conn = _connect(path)
    try:
        conn.executescript(BATCH_SCHEMA)
    finally:
        conn.close()


def create_batch(path, records):
    """Stage a batch of uploaded documents. records: list of dicts with
    filename, text, preview, status ('ready'/'refused'), error.

    Returns the new batch_id. IDs are assigned here so the caller never has to
    invent them, matching create_job()'s pattern.
    """
    batch_id = uuid.uuid4().hex[:12]
    conn = _connect(path)
    try:
        now = _now()
        conn.executemany(
            "INSERT INTO batch_documents "
            "(id, batch_id, filename, text, preview, status, error, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(uuid.uuid4().hex[:12], batch_id, r["filename"], r.get("text", ""),
              r.get("preview", ""), r["status"], r.get("error"), now)
             for r in records],
        )
    finally:
        conn.close()
    return batch_id


def get_batch(path, batch_id):
    """All staged documents for a batch, in upload order."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM batch_documents WHERE batch_id=? ORDER BY rowid",
            (batch_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# --- Benchmark corpus ---------------------------------------------------------
# A CORPUS, NOT A QUEUE. Rows here are INPUTS TO MEASUREMENT -- legislative
# text, religious/narrative text, standards documents -- held so that
# `chunk_boundary_audit.py`, `bench/chunk_size_driver.py` and anything else that
# needs a real document can name one deliberately, instead of reaching into
# `jobs` for whatever happened to be submitted last. Nothing in this table ever
# becomes a job, and nothing here costs cluster time: every stored figure is
# string analysis (see missing_link/corpus.py).
#
# WHY IT EXISTS, concretely. docs/chunk-boundary-measurement.md returned 0 of 84
# events and could not answer its own question, because the only legal-styled
# document in the store is 2,202 characters -- too short to produce a single
# chunk boundary at any size -- while the two documents long enough to produce
# boundaries are narrative prose with 50-500x lower clause-marker density. That
# measurement was blocked on CORPUS COMPOSITION, not on method. So composition
# is what this table records and displays: chunk count at the current
# CHUNK_TOKENS, clause-marker density, numeric density -- the three numbers that
# decide whether a given document can answer a given question at all.
#
# Separate from `jobs` and from `batch_documents` for the same reason those two
# are separate from each other: a corpus document has no workflow, no status
# transitions and no result, and putting it in `jobs` is exactly how
# measurements ended up running against arbitrary submitted work.

CORPUS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus_documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    genre         TEXT NOT NULL,   -- free text, NOT an enum -- see corpus.normalise_genre
    status        TEXT NOT NULL,   -- 'ready' (extracted and profiled) or
                                   -- 'refused' (see error; never usable as input)
    error         TEXT,
    text          TEXT NOT NULL,   -- extracted text, '' for a refused row
    note          TEXT,            -- operator's own note (provenance, licence, source)
    sha256        TEXT NOT NULL,   -- of the RAW UPLOADED BYTES
    text_sha256   TEXT,            -- of the EXTRACTED TEXT -- see add_corpus_document
    n_bytes       INTEGER,
    n_chars       INTEGER,
    n_words       INTEGER,
    n_chunks      INTEGER,         -- at chunk_tokens, from the real worker.chunk_spans
    chunk_tokens  INTEGER,         -- stored so n_chunks stays interpretable if the
                                   -- default ever moves
    n_sentences        INTEGER,
    n_marker_sentences INTEGER,
    marker_rate        REAL,
    n_numbers          INTEGER,
    numbers_per_1k_words REAL,
    created_at    TEXT NOT NULL
);
"""
CORPUS_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_corpus_documents_genre
    ON corpus_documents (genre, created_at);
-- Deliberately NOT UNIQUE: the duplicate check lives in the upload route so the
-- operator gets a message naming the row that already holds those bytes, rather
-- than an IntegrityError; and a corpus that somehow held one document twice is
-- better VISIBLE (two rows, identical hashes) than an upload that fails.
CREATE INDEX IF NOT EXISTS idx_corpus_documents_sha
    ON corpus_documents (sha256);
"""
# The FIRST column added to this table, via the migration plumbing that was
# put in place for exactly this -- additive, applied once at startup by
# init_corpus_documents, never by a lazy per-operation init_*() call (the race
# that marked real jobs failed; see init_db and _add_missing_columns).
_CORPUS_NEW_COLUMNS = [
    # "plain" | "pdf" | "html" -- see extract.extract_with_method(). Recorded
    # because a corpus row's marker_rate/numbers_per_1k_words are only
    # interpretable in light of what actually produced the stored text: HTML
    # extraction strips markup and reflows block structure into newlines
    # before profile() ever runs, which is exactly the kind of transform that
    # made an operator's raw-passthrough numbers look wrong for reasons that
    # had nothing to do with the document (see extract.py's module docstring).
    # NULL for rows added before this column existed -- not backfilled,
    # because "unknown" and "plain" are different claims and conflating them
    # would misrepresent old rows as verified passthrough.
    ("extraction_method", "TEXT"),
    # "nupunkt" | "regex-fallback" -- which sentence splitter produced
    # n_sentences / n_marker_sentences / marker_rate on this row. F48 measured
    # the two disagreeing by 4x on legislative marker_rate (2.85% -> 10.53%),
    # so those three figures are a property of the document AND the
    # instrument, and a corpus sorted on marker_rate across a mixed-instrument
    # table sorts on nothing. NULL for every row written before this column
    # existed; those predate nupunkt and so came from the regex rung, but they
    # are left NULL rather than backfilled, on the same reasoning as
    # extraction_method above -- "unknown" and "regex-fallback" are different
    # claims. `missing_link/reprofile_corpus.py` is what fills them in, and it
    # refuses to run on the wrong splitter rather than quietly relabelling.
    ("sentence_splitter", "TEXT"),
]


def init_corpus_documents(path):
    """Create/upgrade corpus_documents. Called from init_db at startup ONLY.

    Same three-step shape as init_db itself -- tables, then missing columns,
    then indexes -- rather than one executescript, so that an index naming a
    column added later cannot fail against an already-deployed database. See
    the comment above TABLE_SCHEMA for the incident that established that rule.
    """
    conn = _connect(path)
    try:
        conn.executescript(CORPUS_TABLE_SCHEMA)
        _add_missing_columns(conn, "corpus_documents", _CORPUS_NEW_COLUMNS)
        conn.executescript(CORPUS_INDEX_SCHEMA)
    finally:
        conn.close()


def find_corpus_by_sha256(path, sha256):
    """The existing corpus row holding these exact raw bytes, or None.

    Used to REFUSE a re-upload with a message naming the row that already has
    it, rather than accumulating duplicates that any sweep enumerating the
    corpus would then silently double-count.
    """
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, filename, genre, created_at FROM corpus_documents "
            "WHERE sha256=? ORDER BY created_at LIMIT 1", (sha256,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


_CORPUS_FIELDS = (
    "filename", "genre", "status", "error", "text", "note", "sha256",
    "text_sha256", "n_bytes", "n_chars", "n_words", "n_chunks", "chunk_tokens",
    "n_sentences", "n_marker_sentences", "marker_rate", "n_numbers",
    "numbers_per_1k_words", "extraction_method", "sentence_splitter",
)


def add_corpus_document(path, record):
    """Store one corpus document (or one refusal). Returns the new id.

    `record` carries filename/genre/status/error/text/note/sha256 plus whatever
    figures corpus.profile() produced -- all optional, stored as NULL when
    absent, because a REFUSED row has no text to profile and a fabricated 0
    would be indistinguishable from a genuine measurement of zero (this
    project's standing rule; see the ttft_s note in _CHUNK_NEW_COLUMNS).

    Two hashes, deliberately. `sha256` is of the RAW UPLOADED BYTES -- the
    answer to "is this still the same document the measurement ran against".
    `text_sha256` is of the EXTRACTED TEXT, which is what analysis actually
    consumes: a pypdf upgrade can change the extracted text without changing a
    byte of the PDF, and a measurement re-run after that is not comparable with
    the one before it even though the file is untouched.

    `extraction_method` ("plain"/"pdf"/"html", from
    extract.extract_with_method) is the third piece of that same provenance:
    it says whether `text` is the upload verbatim or the product of a
    transform, which is what a reader needs to know before trusting
    marker_rate/numbers_per_1k_words on a document that turned out to be HTML.
    """
    doc_id = uuid.uuid4().hex[:12]
    values = [record.get(f) for f in _CORPUS_FIELDS]
    placeholders = ",".join("?" * (len(_CORPUS_FIELDS) + 2))
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO corpus_documents (id, " + ", ".join(_CORPUS_FIELDS) +
            f", created_at) VALUES ({placeholders})",
            [doc_id] + values + [_now()])
    finally:
        conn.close()
    return doc_id


def list_corpus_documents(path, genre=None, status="ready", with_text=False):
    """The corpus, newest first. THE accessor for benchmark and analysis code.

    `genre=None` means every genre; pass e.g. "legislative" to sweep just one.
    `status="ready"` is the default because a refused row has no text and must
    never reach a measurement; pass status=None to list everything including
    refusals (what the corpus PAGE does, so the operator can see and delete
    them).

    `with_text=False` by default: documents run to megabytes and neither the
    page nor a "which of these could answer this question" pass needs the
    bytes. A harness that wants the text calls get_corpus_document(), or this
    with with_text=True.
    """
    cols = "*" if with_text else \
        ", ".join(["id"] + [f for f in _CORPUS_FIELDS if f != "text"] + ["created_at"])
    sql = f"SELECT {cols} FROM corpus_documents"
    where, params = [], []
    if genre is not None:
        where.append("genre=?")
        params.append(genre)
    if status is not None:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, rowid DESC"
    conn = _connect(path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_corpus_document(path, doc_id):
    """One corpus document INCLUDING its text, or None. The other half of the
    benchmark-facing accessor pair -- list to choose, get to fetch."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM corpus_documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def corpus_genres(path):
    """Distinct genres currently held, alphabetically.

    Feeds the upload form's <datalist>, which is why `genre` is free text
    rather than a CHECK constraint: the operator named legislative / religious
    / standards, and the next useful category (case law, clinical guidelines,
    committee minutes) is not knowable now. Suggest what exists; accept
    anything.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT genre FROM corpus_documents ORDER BY genre").fetchall()
    finally:
        conn.close()
    return [r["genre"] for r in rows]


def delete_corpus_document(path, doc_id):
    """Remove one corpus document. True if a row was actually deleted.

    Returns whether it hit anything rather than None, so the route can 404 a
    stale link instead of redirecting as though it had worked -- same posture
    as request_cancel/revive_job. Nothing references these rows (they never
    become jobs), so there is no cascade to consider.
    """
    conn = _connect(path)
    try:
        cur = conn.execute("DELETE FROM corpus_documents WHERE id=?", (doc_id,))
        return cur.rowcount > 0
    finally:
        conn.close()

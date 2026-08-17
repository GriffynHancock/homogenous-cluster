"""SQLite job store.

Jobs must outlive any single process run -- a queue that loses work on restart
defeats the point of an async runner whose jobs take hours.
"""
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
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
    chunks      INTEGER
);
-- rowid cannot be named in an index (SQLite rejects it), but it is the
-- implicit tiebreaker anyway, so (status, created_at) is sufficient.
CREATE INDEX IF NOT EXISTS idx_jobs_pending
    ON jobs (status, created_at);
"""


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
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def create_job(path, kind, document):
    job_id = uuid.uuid4().hex[:12]
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, kind, document, created_at) VALUES (?,?,?,?)",
            (job_id, kind, document, _now()),
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
    """Atomically move the oldest pending job to running and return it.

    BEGIN IMMEDIATE is load-bearing. A plain SELECT-then-UPDATE lets two
    workers read the same pending row and both claim it -- the job then runs
    twice, which on a multi-hour summarisation job is an expensive way to find
    out about a race. IMMEDIATE takes the write lock up front, so the second
    worker blocks until the first has committed its UPDATE and then sees the
    row as 'running'.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='pending' "
                "ORDER BY created_at ASC, rowid ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            started = _now()
            conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
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
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error, _now(), job_id),
        )
    finally:
        conn.close()


def requeue_running(path):
    """Return every 'running' job to 'pending'. Returns the number moved.

    Call this at worker startup. Jobs here take hours, so a power cut or an
    OOM kill mid-job is a routine event, not an exceptional one -- and a job
    stuck in 'running' with no process behind it is invisible work that never
    completes and never reports an error.

    Safe only because the runner is deliberately single-worker (see the plan).
    If Missing Link ever grows concurrent workers, this must key off a heartbeat
    or a worker id rather than blanket-requeueing everything.
    """
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='pending', started_at=NULL WHERE status='running'"
        )
        return cur.rowcount
    finally:
        conn.close()


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

    rates = sorted(r["total_s"] / r["chunks"] for r in rows)
    if len(rates) < min_samples:
        return None, len(rates)
    mid = len(rates) // 2
    if len(rates) % 2:
        return rates[mid], len(rates)
    return (rates[mid - 1] + rates[mid]) / 2.0, len(rates)


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

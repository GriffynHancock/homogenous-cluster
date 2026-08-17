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
    conn = _connect(path)
    try:
        conn.executescript(CHUNK_SCHEMA)
    finally:
        conn.close()


def save_chunk_summaries(path, job_id, records):
    """Persist per-chunk summaries so the final output stays traceable.

    Without this the map step's output is consumed by the reduce step and thrown
    away, and no claim in the final summary can be checked against its source.
    """
    init_chunks(path)
    conn = _connect(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_summaries "
            "(job_id, idx, start_char, end_char, summary) VALUES (?,?,?,?,?)",
            [(job_id, r["index"], r["start"], r["end"], r["summary"])
             for r in records])
    finally:
        conn.close()


def get_chunk_summaries(path, job_id):
    init_chunks(path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT idx, start_char, end_char, summary FROM chunk_summaries "
            "WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


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

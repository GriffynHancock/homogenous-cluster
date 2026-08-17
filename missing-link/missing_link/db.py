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
    ("priority", "INTEGER NOT NULL DEFAULT 0"),
    ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("seen_at", "TEXT"),
]
_CHUNK_NEW_COLUMNS = [
    # Which model produced this row, from LlamaClient.model_name() (/props).
    # Needed so a resumed job can tell whether its persisted chunks came from
    # the model that is currently serving -- see save_chunk_summaries and
    # worker.run_one. NULL for rows written before this column existed, which
    # get_recorded_model treats as "unknown", not as "matches".
    ("model", "TEXT"),
]


def _add_missing_columns(conn, table, coldefs):
    have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in coldefs:
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
        conn.executescript(TABLE_SCHEMA)
        _add_missing_columns(conn, "jobs", _JOBS_NEW_COLUMNS)
        conn.executescript(INDEX_SCHEMA)
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
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='pending' "
                "ORDER BY priority ASC, created_at ASC, rowid ASC LIMIT 1"
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
    conn = _connect(path)
    try:
        conn.executescript(CHUNK_SCHEMA)
        _add_missing_columns(conn, "chunk_summaries", _CHUNK_NEW_COLUMNS)
    finally:
        conn.close()


def save_chunk_summaries(path, job_id, records, model=None):
    """Persist per-chunk summaries so the final output stays traceable.

    Without this the map step's output is consumed by the reduce step and thrown
    away, and no claim in the final summary can be checked against its source.

    Called ONE CHUNK AT A TIME by worker.run_one as each completes (not once at
    the end after the whole document finishes) -- that incremental persistence
    is what makes a killed job resumable instead of restarting from zero. See
    worker.run_one and the module-level report on when this used to be called.

    `model` records which model produced these summaries (from
    LlamaClient.model_name()), so a later resume attempt can tell whether it is
    safe to reuse them -- see get_recorded_model.
    """
    init_chunks(path)
    conn = _connect(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_summaries "
            "(job_id, idx, start_char, end_char, summary, model) VALUES (?,?,?,?,?,?)",
            [(job_id, r["index"], r["start"], r["end"], r["summary"], model)
             for r in records])
    finally:
        conn.close()


def get_chunk_summaries(path, job_id):
    init_chunks(path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT idx, start_char, end_char, summary, model FROM chunk_summaries "
            "WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
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
    init_chunks(path)
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
    init_chunks(path)
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

"""sqlite3.OperationalError must be TRANSIENT when it means "locked", and
PERMANENT for everything else in that class.

WHY THIS IS ITS OWN FILE. Before this, sqlite3.OperationalError was never
named in worker.classify_failure at all -- it fell through every branch to
the unknown-exception default, which is PERMANENT. That default is correct
for what OperationalError usually means here: `no such column`,
`no such table`, `duplicate column name` -- the exact error
`db._add_missing_columns` guards against, from a genuine startup race hit
earlier in this project (see its docstring). Retrying one of those forever
on an unattended queue is the failure mode this whole classification scheme
exists to prevent.

But the SAME exception class also covers "two workers briefly wanted the
same write lock" -- store is WAL with a 30s busy_timeout and short
transactions (db._connect), so it should be rare, but this queue runs for
months and R grows as nodes are added. Terminally failing a multi-hour
document because of a few milliseconds of lock contention has nothing to do
with what the document contains, which is exactly the class of defect this
project keeps finding (F20, F34, F36, F39).

So: a genuinely LOCKED database must classify as transient; a genuinely
BROKEN schema must still classify as permanent, from the identical exception
type. Both are reproduced here against REAL sqlite3 connections, not a
mocked exception -- a message-matching guard is exactly the kind of code a
mock can make look right while matching nothing real.
"""
import sqlite3
import tempfile
import os

import pytest

from missing_link import worker


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.close()
    yield path
    os.unlink(path)


def test_locked_database_is_transient(dbpath):
    """A REAL lock: one connection holds an open write transaction, a second
    -- with a deliberately short busy_timeout so the test does not have to
    wait out the real 30s production value -- tries to start its own and
    times out. This is exactly the contention two workers briefly hitting the
    same row would produce; it says nothing about the document or the model.
    """
    holder = sqlite3.connect(dbpath, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (2)")
    try:
        contender = sqlite3.connect(dbpath, timeout=0.05, isolation_level=None)
        contender.execute("PRAGMA busy_timeout=50")
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            contender.execute("BEGIN IMMEDIATE")
        exc = excinfo.value
        assert "locked" in str(exc).lower()  # sanity: this IS the locked case

        assert worker.classify_failure(exc) == "transient"
        assert worker.is_recognised_failure(exc) is True
    finally:
        holder.execute("COMMIT")
        holder.close()


def test_no_such_column_is_still_permanent(dbpath):
    """The exact OTHER member of this exception class: a real SQL error
    against a real (small, in-memory-shaped) schema, nothing to do with
    locking. Must NOT be caught by the narrow locked-message match, and must
    still default to permanent -- retrying a schema bug is a bug someone will
    find at 8am, over and over, all night.
    """
    conn = sqlite3.connect(dbpath)
    try:
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            conn.execute("SELECT no_such_column FROM t")
        exc = excinfo.value
        assert "locked" not in str(exc).lower()  # sanity: this is NOT the locked case

        assert worker.classify_failure(exc) == "permanent"
        # Unrecognised -- not merely permanent, but permanent BY DEFAULT, not
        # by a named rule. The operator-facing message (final_error_message)
        # reads that distinction to say "not one Missing Link recognises"
        # rather than "a problem with the document or the request".
        assert worker.is_recognised_failure(exc) is False
    finally:
        conn.close()


def test_duplicate_column_name_is_still_permanent(dbpath):
    """The specific error `db._add_missing_columns` was written to survive --
    two racing schema upgrades, the loser getting
    `OperationalError: duplicate column name: x`. db.py's own handler
    swallows this ONE case by exact message match, at the point the ALTER is
    issued. This test is about what happens if it ever reached
    classify_failure instead (a future caller of _add_missing_columns that
    does not catch it, or a duplicate-column error from a path this project
    has not seen yet): it must NOT be treated as retryable just because it
    shares a type with the locked case.
    """
    conn = sqlite3.connect(dbpath)
    conn.execute("ALTER TABLE t ADD COLUMN y TEXT")
    try:
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            conn.execute("ALTER TABLE t ADD COLUMN y TEXT")
        exc = excinfo.value
        assert "duplicate column" in str(exc).lower()

        assert worker.classify_failure(exc) == "permanent"
        assert worker.is_recognised_failure(exc) is False
    finally:
        conn.close()


def test_locked_message_match_is_narrow_not_by_type():
    """classify_failure must not have quietly widened to "any OperationalError
    is transient" -- the exact regression this file exists to prevent. A
    freshly constructed OperationalError with an unrelated message must still
    be permanent, proving the match is on the message, not the class.
    """
    exc = sqlite3.OperationalError("disk I/O error")
    assert worker.classify_failure(exc) == "permanent"
    assert worker.is_recognised_failure(exc) is False

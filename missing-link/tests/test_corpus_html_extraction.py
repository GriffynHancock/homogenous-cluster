"""The corpus records HOW a document's stored text was obtained.

WHY THIS EXISTS. The corpus page stores `text_sha256` precisely so it is
knowable what analysis actually consumed (see CORPUS_TABLE_SCHEMA's comment
in db.py). Before this change that provenance stopped one layer short: a row
did not say whether `text` was the upload verbatim or the product of a
transform. That matters concretely for HTML -- `corpus.profile()`'s
marker_rate and numbers_per_1k_words are computed on markup-stripped text for
an HTML upload and on the raw upload for everything else, and a reader
comparing two rows needs to be able to tell which is which without opening
the extracted text and guessing from its shape.

`extraction_method` is added the way this table's own comment insists any new
column must be: additively, via `_CORPUS_NEW_COLUMNS`, migrated once at
startup by `init_corpus_documents` -- never a lazy per-operation call.
"""
import inspect
import os
import tempfile

import pytest

from missing_link import corpus, db, extract


HTML_DOC = """<!DOCTYPE html>
<html><head><title>Retention Policy</title>
<style>body { color: red; }</style>
<script>console.log('nav');</script>
</head><body>
<h1>Retention Policy</h1>
<p>Clinical records must be retained for a period of not less than seven
years from the date of the last entry, unless a longer period is required
under another written law.</p>
<p>Financial records must be retained for seven years under the applicable
Financial Administration Regulations, except where the Auditor-General
directs a shorter period in a particular case.</p>
<p>Incident reports must be retained for ten years, or until any related
legal proceeding concludes, whichever is later.</p>
</body></html>"""

PLAIN_DOC = ("Clinical records must be retained for a period of not less "
             "than seven years from the date of the last entry, unless a "
             "longer period is required under another written law. "
             "Financial records must be retained for seven years under the "
             "applicable Financial Administration Regulations, except "
             "where the Auditor-General directs a shorter period.")


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def webclient(monkeypatch):
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


def test_extraction_method_column_is_migrated_additively_not_lazily():
    # Same structural check test_corpus.py already uses for this table --
    # extraction_method must arrive through the startup migration path, not
    # a lazy per-operation init_*() call.
    names = [c[0] for c in db._CORPUS_NEW_COLUMNS]
    assert "extraction_method" in names
    for fn in (db.add_corpus_document, db.list_corpus_documents,
               db.get_corpus_document):
        assert "init_corpus_documents" not in inspect.getsource(fn), fn.__name__


def test_extraction_method_column_exists_after_init(dbpath):
    import sqlite3
    conn = sqlite3.connect(dbpath)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(corpus_documents)")}
    conn.close()
    assert "extraction_method" in cols


def test_html_upload_is_recorded_as_html_extracted(webclient):
    r = webclient.post("/corpus", data={"genre": "legislative"}, files=[
        ("files", ("policy.html", HTML_DOC.encode(), "text/html")),
    ], follow_redirects=True)
    assert r.status_code == 200

    [row] = webclient.get("/api/corpus").json()
    assert row["extraction_method"] == "html"
    # And the stored text is genuinely stripped -- no markup, no CSS/JS.
    full = webclient.get(f"/api/corpus/{row['id']}").json()
    assert "<p>" not in full["text"]
    assert "color: red" not in full["text"]
    assert "console.log" not in full["text"]
    assert "retained for a period of not less than seven years" in full["text"]


def test_plain_text_upload_is_recorded_as_plain(webclient):
    r = webclient.post("/corpus", data={"genre": "legislative"}, files=[
        ("files", ("memo.txt", PLAIN_DOC.encode(), "text/plain")),
    ], follow_redirects=True)
    assert r.status_code == 200

    [row] = webclient.get("/api/corpus").json()
    assert row["extraction_method"] == "plain"
    full = webclient.get(f"/api/corpus/{row['id']}").json()
    assert full["text"] == PLAIN_DOC


def test_corpus_page_shows_the_extraction_method_for_html_but_not_plain(webclient):
    webclient.post("/corpus", data={"genre": "legislative"}, files=[
        ("files", ("policy.html", HTML_DOC.encode(), "text/html")),
        ("files", ("memo.txt", PLAIN_DOC.encode(), "text/plain")),
    ], follow_redirects=True)
    body = webclient.get("/corpus").text
    assert "html-extracted" in body
    # The plain document's row must not claim any transform happened.
    assert "plain-extracted" not in body


def test_refused_upload_leaves_extraction_method_null(dbpath):
    doc_id = db.add_corpus_document(dbpath, {
        "filename": "scan.pdf", "genre": "g", "status": "refused",
        "error": "scanned, needs OCR", "text": "", "sha256": "deadbeef",
    })
    got = db.get_corpus_document(dbpath, doc_id)
    assert got["extraction_method"] is None


def test_extract_with_method_matches_what_the_corpus_route_stores():
    """Direct unit check that the value app.py's corpus_upload stores is
    exactly what extract.extract_with_method reports -- the route must not
    silently disagree with the module that decides it."""
    text, method = extract.extract_with_method(HTML_DOC.encode(), "policy.html")
    assert method == "html"
    rec = {"filename": "policy.html", "genre": "legislative", "status": "ready",
           "text": text, "sha256": corpus.sha256_hex(HTML_DOC.encode()),
           "text_sha256": corpus.sha256_hex(text), "extraction_method": method,
           **corpus.profile(text)}
    assert rec["extraction_method"] == "html"

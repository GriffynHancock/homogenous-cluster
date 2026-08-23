"""The benchmark corpus: storage, profiling, and the page.

WHY THIS EXISTS. `docs/chunk-boundary-measurement.md` ran a chunk-boundary
sweep over every real document in the job store, produced 0 stranded cases out
of 84 boundary events, and could not answer its own question -- because the only
legal-styled document held was 2,202 characters and produced **zero internal
chunk boundaries at any chunk size**, while the two documents long enough to
produce boundaries were narrative prose at 50-500x lower clause-marker density.
The measurement was blocked on CORPUS COMPOSITION.

So these tests are mostly about composition being VISIBLE and CHECKABLE, not
about CRUD working:

  - the chunk count comes from the real `worker.chunk_spans`, so it cannot
    drift from what the pipeline would actually do (a reimplemented
    ceil(words/size) would have been right until the first stride change);
  - the marker definitions come from `chunk_boundary_audit`, not a second copy
    -- a corpus page whose markers disagreed with the measurement's markers
    would be worse than no page;
  - a document that cannot exercise boundary behaviour SAYS SO on its row,
    rather than being discovered useless after a null result;
  - a corpus upload creates NO job and makes NO inference call -- these are
    inputs to measurement, not work.
"""
import os
import tempfile

import pytest

from missing_link import cascade, chunk_boundary_audit, corpus, db, worker


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


# A legislative-styled document deliberately long enough to produce more than
# one chunk at the production default (4096 tokens * 0.70 words/token = 2867
# words per chunk), because the whole point of the corpus is that the SHORT
# legal document could not.
_CLAUSE = (
    "A record to which this section applies must be retained for seven years "
    "from the date of the last entry, unless the Minister determines a shorter "
    "period under subsection (4), or until the relevant proceedings conclude, "
    "whichever is later. This obligation is subject to section 19 and does not "
    "apply to a record other than one created in the course of clinical care, "
    "apart from a record held by a contracted service provider, except where "
    "regulation 22 provides that the shorter period applies. "
)
LONG_LEGISLATIVE = _CLAUSE * 90        # ~7,900 words -> several chunks
SHORT_LEGISLATIVE = _CLAUSE * 2        # ~176 words -> one chunk, no boundary
NARRATIVE = ("He walked down to the river and looked at the water for a long "
             "while before turning back towards the house. ") * 200


# --- profiling: derived from the real code, not reimplemented ---------------

def test_chunk_count_is_exactly_what_the_real_chunker_produces():
    # Not ceil(words / size). If the stride/overlap bookkeeping in chunk_spans
    # ever changes, this figure must change with it -- otherwise the corpus
    # page predicts a pipeline that no longer exists.
    p = corpus.profile(LONG_LEGISLATIVE)
    assert p["n_chunks"] == len(worker.chunk_spans(LONG_LEGISLATIVE))
    assert p["n_chunks"] >= corpus.MIN_CHUNKS_FOR_BOUNDARY
    assert p["chunk_tokens"] == worker.CHUNK_TOKENS


def test_word_count_uses_the_chunkers_own_tokenisation():
    # n_words and n_chunks are shown side by side, so they have to be counted
    # the same way or the page invites an arithmetic that does not hold.
    p = corpus.profile(NARRATIVE)
    assert p["n_words"] == len(worker.word_spans(NARRATIVE))


def test_marker_density_matches_chunk_boundary_audit_exactly():
    # The corpus page and docs/chunk-boundary-measurement.md must be quoting
    # the same number from the same markers and the same sentence splitter.
    p = corpus.profile(LONG_LEGISLATIVE)
    d = chunk_boundary_audit.marker_density(LONG_LEGISLATIVE)
    assert (p["n_sentences"], p["n_marker_sentences"], p["marker_rate"]) == \
           (d["n_sentences"], d["n_with_marker"], d["rate"])


def test_corpus_module_does_not_redefine_the_markers():
    # Imported, not duplicated -- asserted structurally so a later "just add
    # one more marker here" cannot silently fork the definition.
    import inspect
    src = inspect.getsource(corpus)
    assert "unless" not in src.split('"""')[-1] or "MARKER" not in src
    assert not hasattr(corpus, "MARKER_PATTERNS")
    assert not hasattr(corpus, "MARKER_RE")


def test_genre_separation_is_visible_in_marker_density():
    # The actual finding that motivated this page: legal-styled text carries
    # qualifying clauses at an order of magnitude more density than narrative
    # prose, which is why a corpus of only the latter answered nothing.
    legal = corpus.profile(LONG_LEGISLATIVE)
    prose = corpus.profile(NARRATIVE)
    assert legal["marker_rate"] > 10 * prose["marker_rate"]


def test_numeric_density_uses_the_cascades_own_scanner():
    p = corpus.profile(LONG_LEGISLATIVE)
    assert p["n_numbers"] == len(cascade.extract_numbers(LONG_LEGISLATIVE))
    assert p["numbers_per_1k_words"] > 0


def test_profile_costs_no_io_and_no_model():
    # A corpus document is an INPUT to measurement. Profiling one must never
    # be work: pure function of the text, no client, no endpoint.
    import inspect
    src = inspect.getsource(corpus.profile)
    for forbidden in ("LlamaClient", "requests", "httpx", "urlopen", "sqlite3"):
        assert forbidden not in src


def test_normalise_genre_is_a_stable_key_and_never_rejects():
    assert corpus.normalise_genre("  Legislative! ") == "legislative"
    assert corpus.normalise_genre("IEEE / standards") == "ieee  standards".replace("  ", " ")
    assert corpus.normalise_genre("") == corpus.UNCLASSIFIED
    assert corpus.normalise_genre(None) == corpus.UNCLASSIFIED
    assert len(corpus.normalise_genre("x" * 500)) <= corpus.GENRE_MAX_CHARS


def test_usability_notes_name_the_single_chunk_problem():
    row = dict(status="ready", **corpus.profile(SHORT_LEGISLATIVE))
    notes = corpus.usability_notes(row)
    assert row["n_chunks"] < corpus.MIN_CHUNKS_FOR_BOUNDARY
    assert any("boundary" in n for n in notes), notes


def test_usability_notes_name_absent_clause_markers_and_figures():
    plain = "the cat sat on the mat. " * 400
    row = dict(status="ready", **corpus.profile(plain))
    notes = " | ".join(corpus.usability_notes(row))
    assert "clause markers" in notes
    assert "hard number tier" in notes


def test_a_good_benchmark_document_gets_no_warnings():
    row = dict(status="ready", **corpus.profile(LONG_LEGISLATIVE))
    assert corpus.usability_notes(row) == []


# --- storage ----------------------------------------------------------------

def test_init_db_creates_the_table_and_is_idempotent(dbpath):
    db.init_db(dbpath)          # second call must be a no-op, not an error
    assert db.list_corpus_documents(dbpath) == []


def test_no_lazy_per_operation_init(dbpath):
    # The lazy init_*() pattern was a race that marked real jobs FAILED (see
    # db.init_db's docstring). Asserted structurally, because the symptom only
    # appears under concurrency a test suite cannot reproduce.
    import inspect
    for fn in (db.add_corpus_document, db.list_corpus_documents,
               db.get_corpus_document, db.delete_corpus_document,
               db.find_corpus_by_sha256, db.corpus_genres):
        assert "init_corpus_documents" not in inspect.getsource(fn), fn.__name__
    assert "init_corpus_documents(path)" in inspect.getsource(db.init_db)


def test_round_trip_with_profile_and_hashes(dbpath):
    text = LONG_LEGISLATIVE
    rec = {"filename": "act.txt", "genre": "legislative", "status": "ready",
           "text": text, "note": "test", "sha256": corpus.sha256_hex(b"raw"),
           "text_sha256": corpus.sha256_hex(text), "n_bytes": 3,
           **corpus.profile(text)}
    doc_id = db.add_corpus_document(dbpath, rec)

    got = db.get_corpus_document(dbpath, doc_id)
    assert got["text"] == text
    assert got["genre"] == "legislative"
    assert got["n_chunks"] == rec["n_chunks"]
    assert got["sha256"] != got["text_sha256"]   # raw bytes vs extracted text


def test_list_excludes_text_by_default_and_can_include_it(dbpath):
    rec = {"filename": "a.txt", "genre": "legislative", "status": "ready",
           "text": "hello world", "sha256": "aa", **corpus.profile("hello world")}
    db.add_corpus_document(dbpath, rec)
    [row] = db.list_corpus_documents(dbpath)
    assert "text" not in row          # documents are megabytes; the list never needs them
    assert row["n_chars"] == 11
    [full] = db.list_corpus_documents(dbpath, with_text=True)
    assert full["text"] == "hello world"


def test_list_filters_by_genre_and_hides_refusals_by_default(dbpath):
    db.add_corpus_document(dbpath, {"filename": "a", "genre": "legislative",
                                    "status": "ready", "text": "x", "sha256": "1"})
    db.add_corpus_document(dbpath, {"filename": "b", "genre": "standards",
                                    "status": "ready", "text": "y", "sha256": "2"})
    db.add_corpus_document(dbpath, {"filename": "c", "genre": "legislative",
                                    "status": "refused", "text": "",
                                    "error": "scanned", "sha256": "3"})
    assert len(db.list_corpus_documents(dbpath)) == 2
    assert [r["filename"] for r in
            db.list_corpus_documents(dbpath, genre="legislative")] == ["a"]
    # A refused row has no text and must never reach a measurement...
    assert all(r["status"] == "ready" for r in db.list_corpus_documents(dbpath))
    # ...but the page can still see it.
    assert len(db.list_corpus_documents(dbpath, status=None)) == 3
    assert db.corpus_genres(dbpath) == ["legislative", "standards"]


def test_find_by_sha256_and_delete(dbpath):
    doc_id = db.add_corpus_document(dbpath, {"filename": "a", "genre": "g",
                                             "status": "ready", "text": "x",
                                             "sha256": "deadbeef"})
    assert db.find_corpus_by_sha256(dbpath, "deadbeef")["id"] == doc_id
    assert db.find_corpus_by_sha256(dbpath, "nope") is None
    assert db.delete_corpus_document(dbpath, doc_id) is True
    assert db.delete_corpus_document(dbpath, doc_id) is False   # 404, not a silent no-op
    assert db.get_corpus_document(dbpath, doc_id) is None


# --- the page and the API ----------------------------------------------------

def test_corpus_is_reachable_by_clicking(webclient):
    # REQUIREMENTS.md: "a page nobody can find is a page that does not exist".
    assert '/corpus' in webclient.get("/").text
    assert webclient.get("/corpus").status_code == 200


def test_upload_several_genres_and_show_the_computed_columns(webclient):
    r = webclient.post("/corpus", data={"genre": "Legislative"}, files=[
        ("files", ("act.txt", LONG_LEGISLATIVE.encode(), "text/plain")),
        ("files", ("memo.txt", SHORT_LEGISLATIVE.encode(), "text/plain")),
    ], follow_redirects=True)
    assert r.status_code == 200
    rows = webclient.get("/api/corpus").json()
    assert {row["filename"] for row in rows} == {"act.txt", "memo.txt"}
    assert all(row["genre"] == "legislative" for row in rows)

    long_row = next(r for r in rows if r["filename"] == "act.txt")
    short_row = next(r for r in rows if r["filename"] == "memo.txt")
    assert long_row["n_chunks"] >= 2
    assert short_row["n_chunks"] == 1
    assert long_row["marker_rate"] > 0

    body = webclient.get("/corpus").text
    # The document that cannot answer the boundary question says so, on its row.
    assert "cannot exercise chunk-boundary behaviour" in body
    assert "legislative" in body


def test_a_refused_file_does_not_block_the_rest_of_the_batch(webclient):
    webclient.post("/corpus", data={"genre": "standards"}, files=[
        ("files", ("good.txt", b"IEEE 802.3 requires 4 pairs. " * 40, "text/plain")),
        ("files", ("photo.jpg", b"\xff\xd8\xff\xe0rubbish", "image/jpeg")),
        ("files", ("also-good.md", b"# Standard\n\nClause 5 applies. " * 40, "text/markdown")),
    ], follow_redirects=True)

    ready = webclient.get("/api/corpus").json()
    assert {r["filename"] for r in ready} == {"good.txt", "also-good.md"}

    body = webclient.get("/corpus").text
    assert "photo.jpg" in body
    # F38: the refusal names the format detected and what IS supported.
    assert "JPEG image" in body
    assert "PDF with a text layer" in body


def test_identical_bytes_are_refused_naming_the_existing_row(webclient):
    payload = ("Section 4 applies unless the Minister determines otherwise. " * 30).encode()
    webclient.post("/corpus", data={"genre": "legislative"},
                   files=[("files", ("a.txt", payload, "text/plain"))],
                   follow_redirects=True)
    first = webclient.get("/api/corpus").json()[0]
    webclient.post("/corpus", data={"genre": "religious"},
                   files=[("files", ("copy-of-a.txt", payload, "text/plain"))],
                   follow_redirects=True)
    assert len(webclient.get("/api/corpus").json()) == 1
    assert first["id"] in webclient.get("/corpus").text


def test_corpus_upload_creates_no_job_and_queues_nothing(webclient):
    # THE invariant. These are inputs to measurement, not work: an upload that
    # quietly queued 12 legislative texts would cost the cluster a night.
    webclient.post("/corpus", data={"genre": "legislative"},
                   files=[("files", ("act.txt", LONG_LEGISLATIVE.encode(), "text/plain"))],
                   follow_redirects=True)
    assert webclient.get("/api/jobs").json() == []


def test_api_fetch_by_id_and_by_genre_for_benchmark_code(webclient):
    webclient.post("/corpus", data={"genre": "religious"},
                   files=[("files", ("psalms.txt", NARRATIVE.encode(), "text/plain"))],
                   follow_redirects=True)
    webclient.post("/corpus", data={"genre": "legislative"},
                   files=[("files", ("act.txt", LONG_LEGISLATIVE.encode(), "text/plain"))],
                   follow_redirects=True)

    legis = webclient.get("/api/corpus", params={"genre": "legislative"}).json()
    assert [r["filename"] for r in legis] == ["act.txt"]

    full = webclient.get(f"/api/corpus/{legis[0]['id']}").json()
    assert full["text"] == LONG_LEGISLATIVE.strip()
    assert webclient.get(f"/corpus/{legis[0]['id']}/text").text == LONG_LEGISLATIVE.strip()

    assert webclient.get("/api/corpus/nosuchid").status_code == 404
    assert webclient.get("/corpus/nosuchid/text").status_code == 404


def test_delete_from_the_page(webclient):
    webclient.post("/corpus", data={"genre": "other"},
                   files=[("files", ("a.txt", b"hello there friend " * 20, "text/plain"))],
                   follow_redirects=True)
    doc_id = webclient.get("/api/corpus").json()[0]["id"]
    r = webclient.post(f"/corpus/{doc_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert webclient.get("/api/corpus").json() == []
    assert webclient.post(f"/corpus/{doc_id}/delete").status_code == 404


def test_upload_with_no_files_is_refused(webclient):
    # No `files` part at all -> FastAPI's own validation, before the handler.
    assert webclient.post("/corpus", data={"genre": "other"}).status_code == 422
    # A part present but with an empty filename is coerced to a plain string
    # field by Starlette's multipart parser, so it fails validation the same
    # way rather than arriving as an unnamed UploadFile. The handler keeps its
    # own `if not named` guard anyway -- it is one line, and the parser's
    # coercion rules are not something this route should depend on.
    r = webclient.post("/corpus", data={"genre": "other"},
                       files=[("files", ("", b"", "text/plain"))])
    assert r.status_code == 422
    assert webclient.get("/api/corpus").json() == []


# --- provenance: which splitter produced marker_rate -------------------------

def test_profile_records_the_splitter_that_produced_the_rate():
    """F45/F48: the rate is a property of the document AND the instrument.
    A row that does not name its instrument cannot be compared with one that
    names a different one."""
    p = corpus.profile(LONG_LEGISLATIVE)
    assert p["sentence_splitter"] in {"nupunkt", "regex-fallback"}
    assert p["sentence_splitter"] == \
        chunk_boundary_audit.marker_density(LONG_LEGISLATIVE)["splitter"]


def test_corpus_page_badges_a_row_whose_splitter_is_not_nupunkt(webclient):
    """The live store's existing rows predate the column and read NULL. The
    page must SAY SO rather than render their marker_rate beside a nupunkt
    row as though the two were on one scale -- that is exactly the mistake
    F45 is a record of."""
    live = os.environ["MISSING_LINK_DB"]     # the page's own db, not `dbpath`
    db.add_corpus_document(live, {
        "filename": "legacy_row.html", "genre": "legislative", "status": "ready",
        "text": LONG_LEGISLATIVE, "sha256": "cafe", "text_sha256": "beef",
        "n_bytes": 10, "n_chars": 10, "n_words": 10, "n_chunks": 2,
        "chunk_tokens": 4096, "n_sentences": 8455, "n_marker_sentences": 241,
        "marker_rate": 0.0285, "n_numbers": 1, "numbers_per_1k_words": 1.0,
        "sentence_splitter": None,          # the legacy case
    })
    body = webclient.get("/corpus").text
    assert "legacy_row.html" in body
    assert "splitter unknown" in body

    db.add_corpus_document(live, {
        "filename": "regex_row.html", "genre": "legislative", "status": "ready",
        "text": LONG_LEGISLATIVE, "sha256": "f00d", "text_sha256": "d00d",
        "n_bytes": 10, "n_chars": 10, "n_words": 10, "n_chunks": 2,
        "chunk_tokens": 4096, "n_sentences": 8455, "n_marker_sentences": 241,
        "marker_rate": 0.0285, "n_numbers": 1, "numbers_per_1k_words": 1.0,
        "sentence_splitter": "regex-fallback",
    })
    body = webclient.get("/corpus").text
    assert "regex-fallback" in body

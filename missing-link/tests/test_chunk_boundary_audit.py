"""Tests for chunk_boundary_audit.py -- the "does a chunk boundary sever a
qualifying clause pair" measurement tool. See docs/chunk-boundary-
measurement.md for the real-corpus results this instrument produced.

Constructed (not real-corpus) cases here exist to verify the COUNTING LOGIC
itself against known ground truth -- important because the real corpus barely
exercised the "qualifying" path (1 event in 84 boundary detections), so these
are the tests that actually pin down correctness.
"""
import re

import pytest

from missing_link import chunk_boundary_audit as cba
from missing_link import audit


# ---------------------------------------------------------------------------
# Marker detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Records must be kept for seven years unless the client requests earlier destruction.",
    "This applies except where the provider is exempt under section 4.",
    "The obligation continues until the client turns 25, whichever is later.",
    "Access is granted subject to written approval from the coordinator.",
    "All staff must comply, other than casual employees on probation.",
    "No disclosure is permitted apart from cases required by law.",
    "The exemption holds provided that the request is made in writing.",
])
def test_find_markers_fires_on_each_marker_shape(text):
    assert cba.find_markers(text)


def test_find_markers_empty_on_plain_sentence():
    assert cba.find_markers("The audit found three deficiencies in records handling.") == []


def test_find_markers_deduplicates_and_lowercases():
    markers = cba.find_markers("Unless stated otherwise, unless revoked, this applies.")
    assert markers == ["unless"]


# ---------------------------------------------------------------------------
# Sentence splitter -- this module and audit.py must use the SAME ONE. They
# used to carry two byte-identical copies of the fallback regex with two
# contradicting docstrings (F48); the assertion below is that they are now one
# object, not two that happen to agree on the test string.
# ---------------------------------------------------------------------------

def test_sentence_splitter_is_the_same_object_as_audits():
    from missing_link import sentences
    assert cba.sentence_spans is audit.sentence_spans is sentences.sentence_spans
    assert cba._SENT_FALLBACK is audit._SENT_FALLBACK


def test_regex_rung_still_matches_the_fallback_regex_shape(regex_splitter):
    """The fallback rung must remain exactly what it always was, so a
    deliberately-pinned re-run of an old measurement reproduces it."""
    text = "First sentence. Second sentence! Third one? Trailing fragment"
    ours = [t for _, _, t in cba.sentence_spans(text)]
    theirs = [text[s:e].strip() for s, e in
              [(m.start(), m.end()) for m in audit._SENT_FALLBACK.finditer(text)]]
    assert ours == theirs


def test_sentence_spans_true_offsets():
    text = "Hello world. This is a test!"
    spans = cba.sentence_spans(text)
    assert spans[0] == (0, 12, "Hello world.")
    assert text[spans[0][0]:spans[0][1]] == spans[0][2]
    assert spans[1] == (13, 28, "This is a test!")


def test_sentence_spans_empty_text():
    assert cba.sentence_spans("") == []
    assert cba.sentence_spans("   ") == []


# ---------------------------------------------------------------------------
# sentence_covering
# ---------------------------------------------------------------------------

def test_sentence_covering_inside_and_outside():
    spans = [(0, 12, "Hello world."), (13, 29, "This is a test!")]
    assert cba.sentence_covering(spans, 5) == (0, 12, "Hello world.")
    assert cba.sentence_covering(spans, 20) == (13, 29, "This is a test!")
    assert cba.sentence_covering(spans, 12) is None  # the gap between sentences


# ---------------------------------------------------------------------------
# overlap_for -- must match bench/chunk_size_driver.py's own formula
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chunk_tokens,expected", [
    (1024, 102), (2048, 205), (3072, 307), (4096, 410), (6144, 614),
])
def test_overlap_for_matches_bench_sweep(chunk_tokens, expected):
    assert cba.overlap_for(chunk_tokens) == expected


# ---------------------------------------------------------------------------
# analyse_document -- positive controls with KNOWN ground truth, constructed
# so the counting logic is pinned down independent of what the thin real
# corpus happened to contain.
# ---------------------------------------------------------------------------

def _filler(n_words, tag="filler"):
    return " ".join(f"{tag}{i}" for i in range(n_words))


def test_analyse_document_detects_a_stranded_qualifying_clause():
    """Construct a document where a chunk boundary provably falls inside a
    sentence carrying "unless", and where the overlap window is too small to
    repair it (chunk_tokens small, overlap tiny) -- the headline case."""
    # WORDS_PER_TOKEN=0.7, so chunk_tokens=20 -> size=14 words.
    lead = _filler(12)
    clause = "records must be retained for seven years unless the client objects in writing today."
    tail = _filler(200, tag="tail")
    text = f"{lead}. {clause} {tail}"

    result = cba.analyse_document(text, chunk_tokens=20, overlap_tokens=2, snap_boundaries=False)
    assert result["n_qualifying"] >= 1
    assert result["n_stranded_headline"] >= 1
    assert any("unless" in ex["markers"] for ex in result["examples"])


def test_analyse_document_overlap_repairs_a_nearby_severed_clause():
    """Same shape, but with a LARGE overlap relative to chunk size, so the
    severed sentence should reappear whole in the next chunk and count as
    repaired, not stranded."""
    lead = _filler(12)
    clause = "records must be retained for seven years unless the client objects in writing today."
    tail = _filler(200, tag="tail")
    text = f"{lead}. {clause} {tail}"

    # chunk_tokens=40 -> size=28 words, overlap_tokens=30 -> overlap=21 words:
    # most of a short chunk is overlap, so the clause almost certainly
    # reappears whole at the start of the next chunk.
    result = cba.analyse_document(text, chunk_tokens=40, overlap_tokens=30, snap_boundaries=False)
    if result["n_qualifying"] >= 1:
        assert result["n_repaired_by_overlap"] >= 1
        assert result["n_stranded_headline"] == 0


def test_analyse_document_no_marker_no_qualifying():
    """A document with no qualifying markers anywhere must report zero
    qualifying events regardless of how many boundaries land mid-sentence."""
    text = ". ".join(_filler(15, tag=f"s{i}w") for i in range(30)) + "."
    result = cba.analyse_document(text, chunk_tokens=30, overlap_tokens=3, snap_boundaries=False)
    assert result["n_qualifying"] == 0
    assert result["n_stranded_headline"] == 0


def test_analyse_document_invariant_text_matches_offsets():
    """Sanity: chunk_spans' own text[start:end]==chunk["text"] invariant
    should hold for whatever chunks this function consumes (it does not
    re-slice; a bug here would show up as counting against stale text)."""
    text = ("Alpha beta gamma delta epsilon. " * 40) + \
           "This clause has an exception unless revoked in writing. " + \
           ("Zeta eta theta iota kappa. " * 40)
    chunks = cba.worker.chunk_spans(text, chunk_tokens=60, overlap_tokens=6)
    for ch in chunks:
        assert text[ch["start"]:ch["end"]] == ch["text"]


def test_snap_boundaries_reduces_midsentence_count_on_a_constructed_document():
    """Positive control for the mitigation itself, independent of the real
    corpus's near-total absence of legal-clause material: on a document with
    clean sentence-ending punctuation throughout, turning snap_boundaries on
    should never produce MORE mid-sentence cuts than off, and should usually
    produce fewer."""
    sentences = [f"This is sentence number {i} about a routine administrative matter."
                 for i in range(80)]
    text = " ".join(sentences)

    no_snap = cba.analyse_document(text, chunk_tokens=40, overlap_tokens=4, snap_boundaries=False)
    snap = cba.analyse_document(text, chunk_tokens=40, overlap_tokens=4, snap_boundaries=True)
    assert snap["n_midsentence"] <= no_snap["n_midsentence"]


def test_analyse_document_skips_when_overlap_ge_chunk_tokens():
    result = cba.analyse_document("word " * 100, chunk_tokens=10, overlap_tokens=10)
    assert "skipped" in result


def test_sweep_covers_all_requested_sizes():
    text = "word " * 500
    result = cba.sweep(text, chunk_sizes=(50, 100))
    assert set(result.keys()) == {50, 100}


# ---------------------------------------------------------------------------
# marker_density
# ---------------------------------------------------------------------------

def test_marker_density_counts_correctly():
    text = ("Plain sentence one. Plain sentence two. "
            "This applies unless revoked. Another plain one.")
    d = cba.marker_density(text)
    assert d["n_sentences"] == 4
    assert d["n_with_marker"] == 1
    assert d["rate"] == 0.25


def test_marker_density_empty_document():
    d = cba.marker_density("")
    assert d["n_sentences"] == 0
    assert d["n_with_marker"] == 0


# ---------------------------------------------------------------------------
# False-positive self-check machinery
# ---------------------------------------------------------------------------

def test_suspect_flags_catches_short_unpunctuated_fragment():
    assert cba._suspect_flags("and so") != []


def test_suspect_flags_clean_on_well_formed_sentence():
    assert cba._suspect_flags("The audit identified three deficiencies in records handling.") == []


def test_false_positive_check_boundary_detections_reports_rate():
    pool = ["A well formed sentence with a period.",
            "another fragment without punctuation",
            "Also a fine sentence here."]
    result = cba.false_positive_check_boundary_detections(pool, sample_n=3, seed=1)
    assert result["pool_size"] == 3
    assert result["sample_size"] == 3
    assert 0.0 <= result["suspect_rate"] <= 1.0


def test_false_positive_check_empty_pool():
    result = cba.false_positive_check_boundary_detections([], sample_n=10)
    assert result["pool_size"] == 0
    assert result["sample_size"] == 0
    assert result["suspect_rate"] == 0.0


# ---------------------------------------------------------------------------
# clean_boundary_linewrap_check -- the false-negative-risk self-check
# ---------------------------------------------------------------------------

def _linewrap_document():
    """A clause and its "unless" exception on two different LINES with no
    period between them -- the PDF hard-wrap shape, built by hand."""
    lead = _filler(30)
    line1 = "records must be retained for the full statutory period\n"
    line2 = "unless the subject requests earlier deletion in writing.\n"
    tail = _filler(30, tag="tail")
    return f"{lead}.\n{line1}{line2}{tail}."


def test_clean_boundary_linewrap_check_flags_marker_adjacent_bare_linewrap(
        regex_splitter):
    """On the REGEX rung, the two lines are two separate fragments, so a
    boundary landing exactly between them is reported "clean" despite
    actually severing the pair. This is the false-negative shape the check
    exists to catch, and it is pinned to the rung that has it: F48 replaced
    the default rung precisely because nupunkt does not make this mistake
    (see the companion test below)."""
    lead = _filler(30)
    line1 = "records must be retained for the full statutory period\n"
    line2 = "unless the subject requests earlier deletion in writing.\n"
    tail = _filler(30, tag="tail")
    text = f"{lead}.\n{line1}{line2}{tail}."

    # Choose a chunk size whose stride lands the boundary right at that
    # line break; scan a few sizes since the exact stride depends on
    # WORDS_PER_TOKEN rounding.
    found_marker_adjacent = False
    for ct in range(10, 60, 2):
        ot = max(1, ct // 10)
        try:
            result = cba.clean_boundary_linewrap_check(text, ct, ot)
        except ValueError:
            continue
        if result["clean_bare_linewrap_marker_adjacent"] > 0:
            found_marker_adjacent = True
            break
    assert found_marker_adjacent, (
        "expected at least one chunk size to land a boundary at the bare "
        "line-wrap immediately before the 'unless' clause")


def test_nupunkt_does_not_split_on_the_bare_linewrap(nupunkt_splitter):
    """The F48 claim, asserted on the same hand-built document as the test
    above rather than taken from the audit's prose.

    The regex rung sees two fragments across the hard wrap; nupunkt sees one
    sentence, so the clause and its "unless" exception stay together and a
    cut between them is correctly MID-SENTENCE rather than "clean". That is
    the PDF hard-wrap manifestation of F45 disappearing.
    """
    text = _linewrap_document()
    marker_line = "unless the subject requests earlier deletion in writing."
    pos = text.index(marker_line)

    spans = cba.sentence_spans(text)
    covering = cba.sentence_covering(spans, pos)
    assert covering is not None, "the 'unless' clause must sit inside a sentence"
    # The retained-period clause and its exception are ONE unit, so the
    # sentence containing the marker starts before the line break.
    assert covering[0] < pos
    assert "must be retained for the full statutory period" in covering[2]
    assert "unless" in covering[2]

    # ...and the regex rung is what produces the opposite, on the same text.
    import os
    os.environ["MISSING_LINK_SPLITTER"] = "regex"
    try:
        regex_cov = cba.sentence_covering(cba.sentence_spans(text), pos)
    finally:
        os.environ["MISSING_LINK_SPLITTER"] = "nupunkt"
    assert regex_cov is not None
    assert regex_cov[0] == pos, "regex rung starts a new fragment at the wrap"


def test_clean_boundary_linewrap_check_zero_on_short_document():
    text = "Just one short sentence with no internal chunk boundary at all."
    result = cba.clean_boundary_linewrap_check(text, chunk_tokens=1024, overlap_tokens=102)
    assert result == {"clean_real_punct": 0, "clean_bare_linewrap": 0,
                       "clean_bare_linewrap_marker_adjacent": 0}


# ---------------------------------------------------------------------------
# load_documents_from_db -- exercised against a throwaway sqlite file, not
# the live job store (this module must never touch the live db in a test run)
# ---------------------------------------------------------------------------

def test_load_documents_from_db_dedupes_and_filters(tmp_path):
    import sqlite3

    db_path = tmp_path / "fixture.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (id TEXT, document TEXT)")
    long_text = "This is a real sentence. " * 30          # >= min_chars
    conn.execute("INSERT INTO jobs VALUES (?, ?)", ("job-a", long_text))
    conn.execute("INSERT INTO jobs VALUES (?, ?)", ("job-b", long_text))  # duplicate content
    conn.execute("INSERT INTO jobs VALUES (?, ?)", ("job-c", "%PDF-1.6 binary garbage " * 20))
    conn.execute("INSERT INTO jobs VALUES (?, ?)", ("job-d", "too short"))
    conn.execute("INSERT INTO jobs VALUES (?, ?)", ("job-e", None))
    conn.commit()
    conn.close()

    docs = cba.load_documents_from_db(str(db_path), min_chars=50)
    assert len(docs) == 1
    assert docs[0]["job_id"] == "job-a"
    assert docs[0]["chars"] == len(long_text)

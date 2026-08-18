"""Tests for the boundary-snapping option on chunk_spans (worker.py).

EXPERIMENTAL and default OFF -- see docs/chunking-research.md for the
evidence (and the lack of it) behind this, and the design note above
`_nearest_boundary` in missing_link/worker.py for the mechanism.

These tests exist to prove two separate things:
  1. Default behaviour (snap_boundaries omitted, or explicitly False) is
     BYTE-FOR-BYTE unchanged from before this option existed -- the existing
     `test_chunk_spans_*` tests in test_worker.py already pin that; these
     tests pin it again from the angle of "the new parameter doesn't leak".
  2. With snap_boundaries=True, boundaries move to a nearby sentence/
     paragraph break when one exists, fall back to the naive cut when none
     does, and every invariant chunk_spans already promises still holds:
     doc[start:end] == text, the first chunk starts at 0, the last chunk
     ends at len(doc), and the document is still fully covered.
"""
from missing_link import worker


# --- default behaviour is unaffected ------------------------------------

def test_snap_boundaries_defaults_to_off():
    doc = "ALPHA one two three. " * 900 + "OMEGA final."
    assert worker.chunk_spans(doc) == worker.chunk_spans(doc, snap_boundaries=False)


def test_snap_boundaries_off_is_identical_to_no_flag_at_all():
    doc = "Sentence one. Sentence two. " * 500
    plain = worker.chunk_spans(doc)
    explicit = worker.chunk_spans(doc, snap_boundaries=False)
    assert plain == explicit


# --- _nearest_boundary, the primitive -----------------------------------

def test_nearest_boundary_snaps_to_a_close_sentence_end():
    text = "This is a clause. This is the next clause that goes on for a while."
    # naive cut lands mid-word inside "clause" of the second sentence
    naive_pos = text.index("next clause") + 3
    snapped = worker._nearest_boundary(text, naive_pos, tolerance=40)
    # Should land right after "clause. " -- the space after the FIRST period
    assert snapped == text.index("This is the next")


def test_nearest_boundary_returns_pos_unchanged_when_nothing_nearby():
    text = "a " * 500  # no punctuation anywhere
    pos = 250
    assert worker._nearest_boundary(text, pos, tolerance=20) == pos


def test_nearest_boundary_prefers_the_closer_of_two_candidates():
    text = "Short. " + "x" * 30 + ". " + "y" * 30 + "."
    # a boundary right after "Short." (pos 7) and one much further away
    pos = 5
    snapped = worker._nearest_boundary(text, pos, tolerance=10)
    assert snapped == 7


def test_nearest_boundary_recognises_paragraph_breaks():
    text = "First paragraph here" + "\n\n" + "Second paragraph starts here"
    pos = text.index("\n\n")
    snapped = worker._nearest_boundary(text, pos, tolerance=10)
    assert snapped == pos + 2  # right after the blank line


# --- chunk_spans(snap_boundaries=True): invariants ----------------------

def _sentence_doc(n_sentences=1200):
    # Varied sentence lengths so the naive word-count cut and a nearby real
    # sentence boundary are almost never at the same position -- the case
    # that actually exercises the snap.
    parts = []
    for i in range(n_sentences):
        if i % 7 == 0:
            parts.append(f"Clause number {i} is somewhat longer than the others "
                         f"and carries a qualifier unless an exception applies.")
        else:
            parts.append(f"Short clause {i}.")
    return " ".join(parts)


def test_snapped_chunks_still_slice_exactly():
    doc = _sentence_doc()
    for ch in worker.chunk_spans(doc, snap_boundaries=True):
        assert doc[ch["start"]:ch["end"]] == ch["text"]


def test_snapped_chunks_still_cover_the_whole_document():
    doc = _sentence_doc()
    chunks = worker.chunk_spans(doc, snap_boundaries=True)
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] == len(doc)


def test_snapping_moves_at_least_one_boundary_on_a_realistic_document():
    """If this ever fails, the test fixture stopped exercising the snap --
    not proof the feature works on real text, just a guard against the test
    silently degrading into a no-op."""
    doc = _sentence_doc()
    plain = worker.chunk_spans(doc, snap_boundaries=False)
    snapped = worker.chunk_spans(doc, snap_boundaries=True)
    ends_differ = any(p["end"] != s["end"] for p, s in zip(plain, snapped))
    starts_differ = any(p["start"] != s["start"] for p, s in zip(plain, snapped))
    assert ends_differ or starts_differ


def test_snapping_never_produces_a_degenerate_chunk():
    """Punctuation packed close to a cut point must never collapse start>=end."""
    doc = ("a. " * 2000)  # a sentence boundary roughly every 3 chars
    chunks = worker.chunk_spans(doc, snap_boundaries=True)
    for ch in chunks:
        assert ch["start"] < ch["end"]
        assert doc[ch["start"]:ch["end"]] == ch["text"]


def test_snapping_falls_back_to_naive_cut_with_no_nearby_punctuation():
    """A document with no sentence punctuation at all must chunk exactly as
    the un-snapped path would, because every _nearest_boundary call returns
    pos unchanged."""
    doc = " ".join(f"word{i}" for i in range(6000))
    plain = worker.chunk_spans(doc, snap_boundaries=False)
    snapped = worker.chunk_spans(doc, snap_boundaries=True)
    assert plain == snapped


def test_snapping_does_not_change_chunk_count_drastically():
    """Snapping nudges offsets by at most BOUNDARY_SNAP_TOLERANCE chars, so it
    must not change the number of chunks produced for the same document by
    more than one (an off-by-one at the final boundary is the only way the
    loop's own termination check could differ)."""
    doc = _sentence_doc()
    plain = worker.chunk_spans(doc, snap_boundaries=False)
    snapped = worker.chunk_spans(doc, snap_boundaries=True)
    assert abs(len(plain) - len(snapped)) <= 1


def test_first_chunk_start_and_last_chunk_end_are_never_snapped():
    """The document's true start/end must stay put even when a sentence
    boundary happens to sit within tolerance of them -- there is nothing to
    snap AWAY from at the very edges of the document."""
    doc = "Right at the start. " + _sentence_doc(200) + " Right at the very end."
    chunks = worker.chunk_spans(doc, snap_boundaries=True)
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] == len(doc)

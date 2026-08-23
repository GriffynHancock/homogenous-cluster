"""What a benchmark document can and cannot be used to measure.

WHY THIS EXISTS
---------------
`docs/chunk-boundary-measurement.md` asked the cheapest, most decisive
question about chunking -- *does a chunk boundary sever a qualifying clause
pair?* -- ran it over every real document in the job store, and came back with
**0 stranded cases out of 84 boundary events, which answered nothing.** The
reason was corpus composition, not method:

  - the only legal-styled document held was 2,202 characters, which produces
    **zero internal chunk boundaries at every chunk size tested**, so it
    contributed zero data points to the count;
  - the two documents long enough to produce boundaries were narrative prose
    at 0.03% and 1.4% clause-marker density, against 15.4% for the legal one
    -- 50-500x sparser, so a fixed-word cut had almost no chance of hitting a
    marker regardless of where it fell.

Both of those are properties a machine can compute from the text in
milliseconds, before any measurement is run and without any cluster time. That
is what this module does. A corpus page that listed only filenames and sizes
would have hidden exactly the same problem in exactly the same way; these three
numbers are the ones that would have shown it immediately.

DERIVED, NOT DUPLICATED
-----------------------
Every figure comes from the code that production or the existing measurement
tools actually run, imported rather than reimplemented -- so a corpus profile
can never quietly disagree with the thing it is predicting:

  - **chunk count** from `worker.chunk_spans`, the real chunker, at the real
    `worker.CHUNK_TOKENS`/`OVERLAP_TOKENS` defaults;
  - **word count** from `worker.word_spans`, the same tokenisation the chunker
    counts in, so n_words and n_chunks are commensurable;
  - **clause-marker density** from `chunk_boundary_audit.marker_density`,
    which carries the marker list and the sentence splitter that the boundary
    measurement itself used -- now `missing_link.sentences`, nupunkt if
    installed and the regex fallback if not, with the name of whichever ran
    stored on the row as `sentence_splitter` (F45/F48: this rate is a
    property of the document AND the instrument, and rows profiled under
    different instruments are not comparable);
  - **numeric density** from `cascade.extract_numbers`, the same scanner the
    faithfulness cascade's HARD tier keys on.

NO MODEL, NO CLUSTER TIME. Everything here is string analysis over text that
is already in memory. Profiling the largest document in the store (97,299
chars) costs ~0.09s total. A corpus document is an INPUT to measurement; it
must never itself become work.
"""
import hashlib
import re

from missing_link import cascade, chunk_boundary_audit, worker

# Below this, a document cannot exercise chunk-boundary behaviour AT ALL: with
# one chunk there is no internal cut point, so every boundary question returns
# zero for reasons that have nothing to do with the answer. This is precisely
# what made the 2,202-character health-records memo useless to
# docs/chunk-boundary-measurement.md, and the corpus page says so on the row
# rather than leaving the operator to work it out after a null result.
MIN_CHUNKS_FOR_BOUNDARY = 2

# A document with no checkable figures cannot exercise cascade.py's HARD tier
# (tier 1: "every figure in a claim must occur in the cited span"), which is
# the tier that actually caught F42's laundered fabrication. Per 1000 words so
# it is comparable across documents of different lengths.
LOW_NUMERIC_DENSITY = 1.0

# Suggested, NOT enforced. The operator named legislative / religious /
# standards; `other` is the escape hatch and the genre column is free text, so
# the next useful category (case law, clinical guidelines, committee minutes)
# needs no schema change. db.corpus_genres() adds whatever has actually been
# used to this list in the form's datalist.
GENRE_SUGGESTIONS = ("legislative", "religious", "standards", "narrative", "other")

UNCLASSIFIED = "unclassified"

# Genre is a grouping key for `db.list_corpus_documents(genre=...)` and lands
# in a URL query string, so it is normalised to something a sweep can match
# exactly: lowercase, single-spaced, punctuation-free, bounded. Not validated
# against an enum -- see GENRE_SUGGESTIONS.
_GENRE_STRIP = re.compile(r"[^a-z0-9 _-]+")
_GENRE_SPACE = re.compile(r"\s+")
GENRE_MAX_CHARS = 40


def normalise_genre(raw):
    """Free-text genre -> a stable grouping key. Blank becomes 'unclassified'.

    Deliberately never rejects: refusing an upload because a category name had
    an ampersand in it would be the tail wagging the dog. It normalises, and
    the normalised value is what is displayed, so the operator can see what
    their input became.
    """
    g = _GENRE_SPACE.sub(" ", _GENRE_STRIP.sub("", (raw or "").strip().lower())).strip()
    return g[:GENRE_MAX_CHARS] or UNCLASSIFIED


def sha256_hex(data):
    """SHA-256 of bytes (or of str, encoded UTF-8). See db.add_corpus_document
    for why both the raw upload and the extracted text are hashed."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def profile(text, chunk_tokens=None, overlap_tokens=None):
    """Everything measurable about a document, without running anything.

    Returns a dict of exactly the columns db.add_corpus_document stores. Pure
    function of `text` -- no I/O, no model, no cluster.

    `chunk_tokens`/`overlap_tokens` default to the production values so the
    stored chunk count answers "what would THIS pipeline do with this
    document", not a hypothetical. `chunk_tokens` is stored alongside the
    count, because a count without the size it was computed at stops meaning
    anything the moment the default moves.
    """
    if chunk_tokens is None:
        chunk_tokens = worker.CHUNK_TOKENS
    if overlap_tokens is None:
        overlap_tokens = worker.OVERLAP_TOKENS

    n_words = len(worker.word_spans(text))
    # The REAL chunker, at the real defaults -- not ceil(words/size). The
    # stride/overlap bookkeeping in chunk_spans is exactly the thing that
    # decides how many internal boundaries exist, so reimplementing it here
    # would be reimplementing the thing being predicted.
    n_chunks = len(worker.chunk_spans(text, chunk_tokens=chunk_tokens,
                                      overlap_tokens=overlap_tokens))

    density = chunk_boundary_audit.marker_density(text)
    n_numbers = len(cascade.extract_numbers(text))

    return {
        "n_chars": len(text),
        "n_words": n_words,
        "n_chunks": n_chunks,
        "chunk_tokens": chunk_tokens,
        "n_sentences": density["n_sentences"],
        "n_marker_sentences": density["n_with_marker"],
        "marker_rate": density["rate"],
        # Which splitter produced the three figures above. NOT decoration:
        # F48 measured nupunkt and the regex fallback disagreeing 4x on
        # legislative marker_rate, so two rows profiled under different
        # instruments are not two points on one scale (F45's actual finding).
        # NULL on rows written before this column existed -- those came from
        # the regex rung, but "unknown" and "regex-fallback" are different
        # claims and backfilling would assert a provenance nobody verified.
        "sentence_splitter": density["splitter"],
        "n_numbers": n_numbers,
        "numbers_per_1k_words": round(1000.0 * n_numbers / n_words, 2) if n_words else 0.0,
    }


def usability_notes(row):
    """Short, plain warnings about what this document CANNOT answer.

    Deterministic and stated on the row itself, because the alternative -- the
    one this project actually lived through -- is running a measurement,
    getting a floor of zero, and only then working out that the corpus could
    never have produced a non-zero. Returns [] for a document with no caveats.
    """
    notes = []
    if row.get("status") != "ready":
        return notes
    n_chunks = row.get("n_chunks")
    if n_chunks is not None and n_chunks < MIN_CHUNKS_FOR_BOUNDARY:
        notes.append(
            f"{n_chunks} chunk at {row.get('chunk_tokens')} tokens - no internal "
            "boundary, so it cannot exercise chunk-boundary behaviour at all")
    if not row.get("n_marker_sentences"):
        notes.append(
            "no qualifying clause markers (unless/except/provided that/...) - "
            "cannot exercise the severed-clause-pair question")
    dens = row.get("numbers_per_1k_words")
    if dens is not None and dens < LOW_NUMERIC_DENSITY:
        notes.append(
            f"{dens} numbers per 1000 words - too few checkable figures to "
            "exercise the cascade's hard number tier")
    return notes

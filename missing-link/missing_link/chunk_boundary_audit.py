"""Does a chunk boundary sever a qualifying clause pair, and does overlap or
`snap_boundaries` repair it? A standalone measurement tool, not wired into the
job flow -- same posture as `audit.py`.

WHY THIS EXISTS
---------------
`docs/chunking-research.md` concluded the published chunking literature is
almost entirely about RETRIEVAL and does not transfer to map-reduce
summarisation, implemented an opt-in `worker.chunk_spans(..., snap_boundaries=
True)`, and explicitly refused to make it the default without measurement. It
named the cheapest, most decisive thing to check first:

    How often does a chunk boundary actually fall inside a qualifying clause
    pair on the real corpus?

That is what this module answers. `docs/audit-ledger.md` / F41 found the
faithfulness checkers' one systematic failure shape is "a clause with a
second, competing clause attached" -- a retention period plus its "unless"
exception, an exemption plus its carve-out. A fixed-word-count cut has no
notion of clause boundaries, so it will periodically sever exactly that
pairing. If that happens often on real documents, the chunker is
MANUFACTURING this project's worst-measured failure mode. If it essentially
never happens, the naive splitter is fine.

METHOD
------
For each real document, at each of several chunk sizes:
  1. Run the REAL `worker.chunk_spans` (imported, not reimplemented).
  2. Split the document into sentences with `missing_link.sentences.
     sentence_spans` -- the single shared splitter, nupunkt if installed and
     the regex fallback if not. Which one ran is recorded in this module's
     JSON output under `sentence_splitter`, and it MATTERS: F48 measured the
     two disagreeing 4x on legislative marker rate.
  3. For each chunk's END offset (the only real internal cut point produced
     by a stride-and-overlap chunker -- see chunk_spans' own docstring), ask:
       - does it fall STRICTLY INSIDE a detected sentence? (n_midsentence)
       - does that sentence carry a QUALIFYING MARKER? (n_qualifying)
       - does the WHOLE sentence, verbatim, also appear in the NEXT chunk's
         own text (the empirical test of "overlap repairs it")? (n_repaired)
       - qualifying AND NOT repaired is the headline case: chunk N's own text
         carries only half the clause pair, uncorrected. (n_stranded)

QUALIFYING MARKERS -- defined here, before measuring, per the task brief.
Drawn directly from the failure shapes docs/audit-ledger.md / F41 actually
found (retention-plus-disjunction, exemption-plus-carve-out,
obligation-plus-condition): "unless", "except", "provided that", "or until",
"whichever is later/earlier", "subject to", "other than", "apart from".

KNOWN INSTRUMENT LIMITATION -- ON THE REGEX RUNG ONLY, AND THIS IS WHY THE
RUNG THAT RAN IS REPORTED. Sanity-checked rather than assumed away: on
hard-line-wrapped, PDF-extracted prose, the regex fallback's non-punctuated
branch (`\\S[^.!?\\n]*$`) treats each un-punctuated LINE as its own pseudo-
"sentence" (chunking-research.md section 4 already warned a bare `\\n` is not
a real boundary; this is that warning manifesting inside the sentence
splitter itself, not just in `snap_boundaries`). F45 is the measurement of it
and F48 is the fix: on the ISM PDF, short fragments fall from 52.4% to 3.5%
under nupunkt, and no nupunkt unit lacks terminal punctuation at all (0.0%).
The checks below are kept anyway, because they are also how a reader
distinguishes a run that had nupunkt from a run that silently did not.
On the regex rung that means:
  - "midsentence" can be measured on a LINE FRAGMENT rather than a true
    clause, inflating word-position matches that aren't real multi-clause
    sentences (see `false_positive_check_boundary_detections` below).
  - a true clause pair straddling a hard line-wrap could be reported as a
    "clean" cut (two separate line-fragments) when it is actually still one
    severed sentence -- a FALSE NEGATIVE for the headline count. See
    `clean_boundary_linewrap_check`, which specifically re-examines "clean"
    boundaries for this risk.
This makes any count from this module a plausible LOWER BOUND on true
severing, not a guaranteed exact count, and `docs/chunk-boundary-
measurement.md` says so explicitly rather than quoting it as precise.
"""
import argparse
import json
import random
import re
import sqlite3
import sys

from missing_link import worker

# --- sentence splitter -----------------------------------------------------
# This module used to carry its OWN copy of `_SENT_FALLBACK`, byte-identical to
# `audit.py`'s and used unconditionally, on the reasoning that the regex was
# "the splitter that actually runs in production". That reasoning was right
# about production and wrong about the instrument: F45 measured the regex
# inflating the sentence denominator with structural fragments on exactly the
# legislative material this module is pointed at, and F48 replaced it.
# Both copies are gone; `missing_link.sentences` is the only one left, and it
# reports which rung ran.
from missing_link.sentences import (  # noqa: E402,F401 (re-exported on purpose)
    _SENT_FALLBACK,
    sentence_spans,
    splitter_name,
)


def sentence_covering(spans, pos):
    """The (start, end, text) sentence span containing character offset
    `pos`, or None if `pos` is not inside any detected span."""
    for s, e, t in spans:
        if s <= pos < e:
            return (s, e, t)
    return None


# --- qualifying-clause markers -- see module docstring for why these ------
MARKER_PATTERNS = [
    r"\bunless\b",
    r"\bexcept\b",            # covers "except where", "except that", "except for"
    r"\bprovided that\b",
    r"\bor until\b",
    r"\bwhichever (?:is )?(?:the )?(?:later|earlier)\b",
    r"\bsubject to\b",
    r"\bother than\b",
    r"\bapart from\b",
]
MARKER_RE = re.compile("|".join(MARKER_PATTERNS), re.IGNORECASE)


def find_markers(text):
    """Sorted, deduplicated list of qualifying markers found in `text`."""
    return sorted(set(m.group(0).lower() for m in MARKER_RE.finditer(text)))


# --- chunk-size sweep, matching bench/chunk_size_driver.py's own params ---
DEFAULT_CHUNK_SIZES = (1024, 2048, 3072, 4096, 6144)


def overlap_for(chunk_tokens):
    """bench/chunk_size_driver.py: overlap_tokens = max(1, round(chunk_tokens * 0.1))."""
    return max(1, round(chunk_tokens * 0.1))


def analyse_document(text, chunk_tokens, overlap_tokens, snap_boundaries=False,
                      max_examples=3):
    """Run worker.chunk_spans over `text` and count boundary events.

    Returns a dict with n_chunks, n_internal_boundaries, n_midsentence,
    n_qualifying, n_repaired_by_overlap, n_stranded_headline, examples (short
    illustrative fragments only), and midsentence_sentences (the raw list of
    sentence texts flagged mid-sentence, for pooled false-positive checking).
    """
    sent_spans = sentence_spans(text)
    try:
        chunks = worker.chunk_spans(text, chunk_tokens=chunk_tokens,
                                     overlap_tokens=overlap_tokens,
                                     snap_boundaries=snap_boundaries)
    except ValueError as e:
        return {"skipped": str(e)}

    n_boundaries = 0
    n_midsentence = 0
    n_qualifying = 0
    n_repaired = 0
    n_stranded = 0
    examples = []
    midsentence_sentences = []

    for i, ch in enumerate(chunks):
        if i == len(chunks) - 1:
            continue  # last chunk's end is the document end, not a real cut
        pos = ch["end"]
        n_boundaries += 1

        cov = sentence_covering(sent_spans, pos)
        if cov is None:
            continue
        s, e, sent_text = cov
        if pos <= s or pos >= e:
            continue  # boundary sits at the sentence's own edge -- a clean cut
        n_midsentence += 1
        midsentence_sentences.append(sent_text)

        markers = find_markers(sent_text)
        if not markers:
            continue
        n_qualifying += 1

        next_chunk_text = chunks[i + 1]["text"]
        if sent_text.strip() in next_chunk_text:
            n_repaired += 1
        else:
            n_stranded += 1
            if len(examples) < max_examples:
                words = sent_text.split()
                frag = " ".join(words[:12]) + (" ..." if len(words) > 12 else "")
                examples.append({"markers": markers, "fragment_first_12_words": frag,
                                  "sentence_chars": len(sent_text)})

    return {
        "n_chunks": len(chunks),
        "n_internal_boundaries": n_boundaries,
        "n_midsentence": n_midsentence,
        "n_qualifying": n_qualifying,
        "n_repaired_by_overlap": n_repaired,
        "n_stranded_headline": n_stranded,
        "examples": examples,
        "midsentence_sentences": midsentence_sentences,
    }


def sweep(text, chunk_sizes=DEFAULT_CHUNK_SIZES, snap_boundaries=False):
    """analyse_document at every size in `chunk_sizes`, keyed by chunk_tokens."""
    return {ct: analyse_document(text, ct, overlap_for(ct), snap_boundaries)
            for ct in chunk_sizes}


# --- false-positive self-check on the instrument itself -------------------

def _suspect_flags(sentence_text):
    """Mechanical proxy for 'does this look like a real sentence, or a
    splitter artefact (line-wrap fragment, heading, abbreviation split)'."""
    words = sentence_text.split()
    flags = []
    if len(words) < 4:
        flags.append("very_short(<4 words)")
    if not re.search(r"[.!?]['\"’”)\]]*$", sentence_text):
        flags.append("no_terminal_punct")
    if words and re.match(r"^[A-Z]\.$", words[0]):
        flags.append("bare_initial_leadin")
    if sentence_text.isupper() and len(words) < 8:
        flags.append("heading_like_caps")
    return flags


def false_positive_check_boundary_detections(midsentence_sentence_pool, sample_n=60, seed=42):
    """Sample from sentences the detector actually flagged at a real chunk
    boundary (NOT a random sample of every sentence in the document -- that
    is a different, less relevant population). Reports how many look like
    splitter artefacts by the mechanical proxy above."""
    rng = random.Random(seed)
    sample = rng.sample(midsentence_sentence_pool, min(sample_n, len(midsentence_sentence_pool)))
    suspects = [{"fragment": " ".join(s.split()[:12]), "flags": _suspect_flags(s)}
                for s in sample if _suspect_flags(s)]
    return {"pool_size": len(midsentence_sentence_pool), "sample_size": len(sample),
            "n_suspect": len(suspects),
            "suspect_rate": round(len(suspects) / max(1, len(sample)), 3)}


def clean_boundary_linewrap_check(text, chunk_tokens, overlap_tokens):
    """For boundaries classified 'clean' (not mid-sentence), distinguish a
    clean cut after REAL terminal punctuation from one that is 'clean' only
    because the line-based regex fallback happened to end a fragment there
    with no punctuation at all (a bare hard line-wrap). The latter carries a
    false-negative risk: a true clause pair straddling such a line could be
    invisibly severed while this instrument reports 'clean'. Flags any such
    case where the NEXT fragment's first few words start with a qualifying
    marker, which is the shape that would actually matter."""
    sent_spans = sentence_spans(text)
    chunks = worker.chunk_spans(text, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens,
                                 snap_boundaries=False)
    real_punct = 0
    bare_linewrap = 0
    bare_linewrap_marker_adjacent = 0
    for i, ch in enumerate(chunks[:-1]):
        pos = ch["end"]
        cov = sentence_covering(sent_spans, pos)
        if cov is not None and cov[0] < pos < cov[1]:
            continue  # mid-sentence, counted elsewhere
        preceding = None
        for s, e, t in sent_spans:
            if e <= pos:
                preceding = (s, e, t)
            else:
                break
        following = None
        for s, e, t in sent_spans:
            if s >= pos:
                following = (s, e, t)
                break
        if preceding and re.search(r"[.!?]['\"’”)\]]*$", preceding[2]):
            real_punct += 1
        else:
            bare_linewrap += 1
            if following:
                first_words = " ".join(following[2].split()[:4])
                if find_markers(first_words):
                    bare_linewrap_marker_adjacent += 1
    return {"clean_real_punct": real_punct, "clean_bare_linewrap": bare_linewrap,
            "clean_bare_linewrap_marker_adjacent": bare_linewrap_marker_adjacent}


def marker_density(text):
    """Of every well-formed sentence anywhere in `text`, what fraction carry
    a qualifying marker? Context stat -- tells us whether a near-zero
    boundary count reflects sparse boundaries landing on markers, or markers
    simply being rare in the document to begin with.

    Returns the SPLITTER alongside the rate, because the rate is a property of
    the pair. F48 measured 2.85% and 10.53% for the same Privacy Act text
    under the two rungs; a stored 2.85% with no instrument attached is not a
    weaker number, it is an unreadable one. `corpus.profile` carries this
    straight into the `corpus_documents` row.
    """
    spans = sentence_spans(text)
    n = len(spans)
    n_marked = sum(1 for s, e, t in spans if find_markers(t))
    return {"n_sentences": n, "n_with_marker": n_marked,
            "rate": round(n_marked / max(1, n), 4),
            "splitter": sentence_spans.last_splitter}


# ---------------------------------------------------------------------------
# CLI: read real documents from the job store (read-only), sweep, report.
# Standalone, like audit.py -- not wired into worker.py or app.py.
# ---------------------------------------------------------------------------

def load_documents_from_db(db_path, min_chars=300):
    """Real documents from the job store, deduplicated by content hash.
    Excludes rows whose 'document' column holds raw PDF bytes (a known
    artefact of failed-extraction test jobs, detectable by the '%PDF' magic
    prefix) and anything shorter than `min_chars`."""
    import hashlib
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT id, document FROM jobs WHERE document IS NOT NULL")
    rows = cur.fetchall()
    conn.close()

    seen = set()
    docs = []
    for jid, doc in rows:
        if not doc or len(doc) < min_chars or doc.lstrip()[:4] == "%PDF":
            continue
        h = hashlib.sha256(doc.encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        docs.append({"job_id": jid, "text": doc, "chars": len(doc), "sha256_16": h[:16]})
    return docs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="/opt/missing-link/jobs.sqlite")
    p.add_argument("--out", default=None, help="write full JSON here (default: stdout summary only)")
    p.add_argument("--chunk-sizes", default=",".join(str(c) for c in DEFAULT_CHUNK_SIZES))
    args = p.parse_args(argv)

    chunk_sizes = tuple(int(x) for x in args.chunk_sizes.split(","))
    docs = load_documents_from_db(args.db)
    print(f"Loaded {len(docs)} real documents from {args.db}", file=sys.stderr)
    for d in docs:
        print(f"  job={d['job_id']} chars={d['chars']}", file=sys.stderr)

    # Stamp the instrument on the output BEFORE running it. Every figure in
    # docs/chunk-boundary-measurement.md was produced by the regex rung and is
    # not comparable with a nupunkt run (F48); a JSON blob that does not say
    # which one it came from is the ambiguity F45 is about.
    splitter = splitter_name()
    print(f"sentence splitter: {splitter}", file=sys.stderr)
    out = {"marker_patterns": MARKER_PATTERNS, "chunk_sizes_tokens": list(chunk_sizes),
           "sentence_splitter": splitter,
           "documents": [{"job_id": d["job_id"], "chars": d["chars"]} for d in docs],
           "per_document": {}}

    pool_no_snap = []
    for d in docs:
        no_snap = sweep(d["text"], chunk_sizes, snap_boundaries=False)
        snap = sweep(d["text"], chunk_sizes, snap_boundaries=True)
        for r in no_snap.values():
            pool_no_snap.extend(r.get("midsentence_sentences", []))
        out["per_document"][d["job_id"]] = {
            "no_snap": {ct: {k: v for k, v in r.items() if k != "midsentence_sentences"}
                        for ct, r in no_snap.items()},
            "snap": {ct: {k: v for k, v in r.items() if k != "midsentence_sentences"}
                     for ct, r in snap.items()},
            "marker_density": marker_density(d["text"]),
        }

    out["false_positive_check_boundary_detections"] = \
        false_positive_check_boundary_detections(pool_no_snap)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

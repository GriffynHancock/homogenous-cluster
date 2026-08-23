"""THE sentence splitter. One implementation, one provenance record.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
Until F48 there were TWO copies of the same fallback regex -- `audit.py:168`
and `chunk_boundary_audit.py:79` -- byte-identical, carrying two DIFFERENT
docstrings about when nltk was preferred. That is not a tidiness complaint.
F45 is the record of a hand-rolled text primitive silently distorting the
number that gates corpus decisions, and a primitive that exists twice can
drift into disagreeing with itself while both copies keep reporting numbers.
So: one function, one regex, one name for what ran.

THE LADDER, AND WHY IT IS THIS ONE
----------------------------------
1. **nupunkt** -- a LEGAL-DOMAIN Punkt variant (ALEA Institute, MIT, pure
   Python, ZERO runtime dependencies, model bundled in the 9.1 MB wheel, no
   first-use download). F48 measured it on the real corpus in the job store,
   on the exact metric F45 says is broken:

       structural fragments   65.0%  ->  12.3%
       fragments with no terminal punctuation      ->   0.0%
       legislative marker rate  2.85%  ->  10.53%
       legislative:regulatory separation  2.2x  ->   4.8x
       ISM short fragments (PDF hard-wrap)  52.4%  ->   3.5%

2. **regex fallback** -- `_SENT_FALLBACK` below. Free, dependency-free, and
   what shipped before. It is a real fallback, not a formality: nupunkt is an
   optional wheel and a node that has not had it installed still has to work.

Two rungs, deliberately. Note what is NOT on the ladder:

- **pysbd**, the most-recommended library for this job, carrying a
  peer-reviewed 97.92% Golden Rules claim, MEASURED WORSE than the regex it
  would replace on our material (2.76% vs 2.85%) and took 133.94 s to be
  worse (F48). It was evaluated on well-formed prose; our failure case is a
  document that is structurally a list.
- **nltk punkt**, which `audit.py` used to prefer. Its only rationale was that
  MiniCheck uses the same splitter, so the claim units lined up -- and F41
  took the classifier tier off the table entirely, so nothing is left to line
  up with. F41 separately measured nltk emitting 9.1% degenerate fragments on
  real markdown ("K.P. Dutt" -> a fragment "P."), and F45 records its fallback
  behaviour as no better than ours. It is ~1.5 GB of stack for a splitter that
  is worse than both rungs above, and it is deliberately absent from the
  production venv. Removed rather than demoted, because a rung nobody wants
  reached is a rung that will be reached by accident.

THE OFFSET CONTRACT -- the reason this is a wrapper and not an import
--------------------------------------------------------------------
`sentence_spans` returns `[(start, end, text)]` where `text[start:end] ==
returned_text`, TRUE offsets into the string that was passed in. Everything
downstream computes locations from those offsets rather than asking a model
where something came from (F46's citation resolution, `audit.line_of`, the
ledger's evidence spans). `nupunkt.sent_tokenize` returns strings and would
break that. `nupunkt.sent_spans` returns offsets directly -- contiguous and
gap-free over the whole input, so the wrapper's only job is to trim the
surrounding whitespace off each span and shift the offsets to match, exactly
as the regex path already did. The round trip is ASSERTED, per span, in
tests/test_sentences.py; it is not assumed from the upstream docstring.

THE FALLBACK IS LOUD
--------------------
F45 happened because a fallback produced distorted numbers quietly, and F48
because nobody could tell which splitter had been in play. So falling back
now:

  - records `sentence_spans.last_splitter` (as before) AND
    `sentence_spans.last_fallback_reason`, the actual exception text;
  - logs a WARNING and writes one line to stderr, ONCE per process, naming
    what would fix it;
  - can be forbidden outright with `MISSING_LINK_SPLITTER=nupunkt`, which
    raises instead of degrading. Any script whose OUTPUT IS A NUMBER should
    set that, because a silent downgrade there is precisely F45.

`MISSING_LINK_SPLITTER=regex` forces the old path, which is how a
pre-nupunkt number gets reproduced deliberately rather than by accident.

INSTALLING IT ON THE FLEET
--------------------------
    pip install nupunkt            # or, air-gapped, per DESIGN-NOTES K:
    pip download nupunkt -d wheelhouse/          # on a machine with internet
    pip install --no-index --find-links=wheelhouse nupunkt

One wheel, no transitive dependencies, nothing fetched at first use (verified
in a network namespace with no interfaces and socket() blocked).

**Python floor: nupunkt requires >= 3.11.** Node 1 and node 2 are both on
Debian 12 / Python 3.11.2 -- exactly on the line. Nodes 3-7 are
uncharacterised. A node below the floor does not crash; it takes rung 2 and
says so.

COST
----
`import nupunkt` is 33 ms. The first `sentence_spans` call pays ~2.1 s to load
the bundled model, cached for the life of the process (`lru_cache` upstream);
subsequent calls are free. That is why the import is INSIDE the function --
importing this module must stay cheap for `worker.py` and `app.py`, neither of
which splits sentences on the hot path.
"""
import logging
import os
import re
import sys

log = logging.getLogger(__name__)

# The dependency-free fallback. THE ONLY COPY -- `audit.py` and
# `chunk_boundary_audit.py` both import it from here. Matches a run of
# non-terminator characters ending in .!? plus trailing quotes/brackets, or a
# non-empty un-punctuated line. That second branch is the F45 defect in one
# expression: on legislation's paragraph-per-clause HTML and on PDF hard-wraps
# it turns every heading and every wrapped line into its own pseudo-sentence,
# inflating the denominator with fragments that structurally cannot carry a
# qualifying marker. It is kept because it is the honest floor, not because it
# is good.
_SENT_FALLBACK = re.compile(r"[^.!?\n]*[.!?]+[\"')\]]*|\S[^.!?\n]*$", re.MULTILINE)

NUPUNKT = "nupunkt"
REGEX_FALLBACK = "regex-fallback"

_ENV = "MISSING_LINK_SPLITTER"
_warned = False


class SplitterUnavailable(RuntimeError):
    """`MISSING_LINK_SPLITTER=nupunkt` was set and nupunkt could not be used.

    Raised rather than degraded on purpose: a caller that asked for a specific
    instrument is a caller whose output is a number, and F45 is what silent
    substitution of the instrument costs.
    """


def _preference():
    """"auto" | "nupunkt" | "regex", from $MISSING_LINK_SPLITTER."""
    pref = (os.environ.get(_ENV) or "auto").strip().lower()
    if pref not in ("auto", "nupunkt", "regex"):
        raise ValueError(
            f"{_ENV}={pref!r}: expected one of auto, nupunkt, regex")
    return pref


def _warn_fallback(reason):
    """Say it once, to the log AND to stderr, and name the fix."""
    global _warned
    msg = (f"sentence splitter: nupunkt unavailable ({reason}); using the "
           f"{REGEX_FALLBACK}. Clause-marker and sentence-count numbers "
           f"produced now are NOT comparable with nupunkt numbers (F45/F48). "
           f"Fix: pip install nupunkt (needs Python >= 3.11); or set "
           f"{_ENV}=regex to say you meant it.")
    log.warning("%s", msg)
    if not _warned:
        _warned = True
        print("WARNING: " + msg, file=sys.stderr)


def _nupunkt_spans(text):
    """Raw (start, end) pairs from nupunkt, or raise.

    Kept separate so the import failure and a segmentation failure are the
    same code path to the caller -- either way the reason is recorded and the
    fallback runs -- and so a test can monkeypatch one without the other.
    """
    import nupunkt

    return list(nupunkt.sent_spans(text))


def _trim(text, spans):
    """Strip surrounding whitespace, keeping offsets TRUE, dropping blanks.

    This is the whole offset contract. `nupunkt.sent_spans` returns contiguous
    spans that include the whitespace between sentences; `_SENT_FALLBACK` can
    match leading whitespace too. Both are normalised identically here, so the
    two rungs differ in WHERE they cut and in nothing else.
    """
    out = []
    for s, e in spans:
        frag = text[s:e]
        stripped = frag.strip()
        if not stripped:
            continue
        lead = len(frag) - len(frag.lstrip())
        out.append((s + lead, s + lead + len(stripped), stripped))
    return out


def sentence_spans(text):
    """[(start, end, sentence)] with TRUE offsets into `text`.

    `text[start:end] == sentence` for every tuple returned, from either rung.
    Which rung ran is on `sentence_spans.last_splitter`; if it was the
    fallback, why is on `sentence_spans.last_fallback_reason`.
    """
    if not text or not text.strip():
        return []

    pref = _preference()
    reason = None
    if pref != "regex":
        try:
            spans = _nupunkt_spans(text)
        except Exception as exc:                # ImportError, or a bad segment
            reason = f"{type(exc).__name__}: {exc}"
            if pref == "nupunkt":
                raise SplitterUnavailable(
                    f"{_ENV}=nupunkt but nupunkt could not be used: {reason}"
                ) from exc
            _warn_fallback(reason)
        else:
            sentence_spans.last_splitter = NUPUNKT
            sentence_spans.last_fallback_reason = None
            return _trim(text, spans)

    spans = [(m.start(), m.end()) for m in _SENT_FALLBACK.finditer(text)]
    sentence_spans.last_splitter = REGEX_FALLBACK
    sentence_spans.last_fallback_reason = reason or (
        f"{_ENV}=regex" if pref == "regex" else None)
    return _trim(text, spans)


sentence_spans.last_splitter = "unknown"
sentence_spans.last_fallback_reason = None


def splitter_name():
    """Which sentence splitter this interpreter would actually use.

    Probes with a real (tiny) string rather than reading a flag, because the
    answer depends on an import that can fail at any point in the process's
    life, not on configuration. Cheap after the first call; the first call
    pays nupunkt's ~2.1 s model load, which is the load every real call would
    have paid anyway.
    """
    sentence_spans("Probe sentence.")
    return sentence_spans.last_splitter


def require(name=NUPUNKT):
    """Assert `name` is the splitter that will run, or raise.

    For the top of any script whose output is a stored or published number.
    `docs/chunk-boundary-measurement.md` and every `marker_rate` in
    `corpus_documents` were produced by the old instrument; the way that stops
    being ambiguous is for the re-run to refuse to start on the wrong one.
    """
    actual = splitter_name()
    if actual != name:
        raise SplitterUnavailable(
            f"required sentence splitter {name!r}, but {actual!r} is what runs "
            f"here (reason: {sentence_spans.last_fallback_reason})")
    return actual

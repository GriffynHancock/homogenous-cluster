"""Faithfulness cascade: deterministic checks that decide, a classifier that does not.

WHY THIS EXISTS
---------------
`worker.parse_section_citations` resolves a `[Section N]` marker to a real
character span of the source. That is an ATTRIBUTION, not a verification: it
records that the model SAID a paragraph came from section 4. Nothing in it
checks that section 4 supports the paragraph. Two CONFIRMED results make that
gap dangerous rather than merely incomplete -- citations raise perceived trust
EVEN WHEN RANDOM (arXiv:2501.01303), and readers verify at under 25% whatever
the UI does (arXiv:2512.12207). The reader will not check, so the machine must.

THE CLASSIFIER WAS SUPPOSED TO DO THAT CHECKING. IT CANNOT (F41)
----------------------------------------------------------------
This module was designed as a cost optimisation: run cheap checks first and
spend the two-model MiniCheck ensemble only on what they cannot resolve. While
it was being built, the ensemble was validated at production evidence size and
the property the whole design rested on did not survive:

    cross-model disagreement, precision   1.00 -> 0.75
    cross-model disagreement, recall      1.00 -> 0.43
    errors BOTH models made silently      0    -> 5.6%
    cost                                  17.74 s + 8.81 s per claim

and hop 1 alone would cost ~199 minutes against a ~143-minute summarisation
job -- an audit more expensive than the work it audits. See F41 and
`docs/audit-production-scale.md`.

**So the ordering is inverted. The deterministic tier is the product; the
classifier is opt-in and, on current evidence, not recommended.** The
classifier seam is kept because it is cheap to keep and the evidence may change,
not because anything here depends on it. `classifier=None` is the default and
the module is fully useful in that configuration.

WHAT THE DETERMINISTIC TIER ACTUALLY DOES
-----------------------------------------
    tier 1  HARD   NUMBERS. Every figure in a claim must occur in the cited
                   span. A figure that does not is a HARD FAIL -- never
                   escalated, because no classifier can make a missing number
                   present. Measured on 978 real claims: 3 findings, all 3
                   verified genuine fabrications, ZERO false positives.
                   Distinctive terms are checked too but only ROUTE; see
                   ENTITY_MODE for why that was demoted, and on what evidence.
    tier 2  SOFT   lexical overlap, trigram similarity, unit agreement,
                   negation polarity, quantifier scope, dropped qualifiers.
                   These NEVER decide. They route: clearly fine, or hand it on.
    tier 3  CLASSIFIER  optional, off by default, see above.

If every number in a claim is present and no soft signal objects, the claim
passes without escalation. That is the operator's "if the numbers and words
match leave it", and it resolves 35% of real claims outright.

REFUSE, DO NOT DEGRADE
----------------------
A claim the cheap tier cannot resolve, with no classifier configured, is
recorded as `needs_classifier` -- NOT as passing. A paragraph with no citation
is `unscoreable` with the reason. Nothing here scores something it did not
actually check (F21, F34, F38, F39).

EVERY SIGNAL IS LABELLED BY KIND
--------------------------------
`hard`, `soft`, `classifier`. A hard number mismatch and a soft similarity
doubt are different evidence and are never merged into one opaque score --
a reader must be able to see WHY something was flagged. A `status` of "fail"
anywhere in a ledger always means a hard, deterministic failure.

RELATIONSHIP TO audit.py
------------------------
This module IMPORTS from `audit.py` and does not modify it. The scorer
protocol, the offset arithmetic, the negation cues and the finding-category
system are audit's; this module adds three categories, a tier field, a repaired
sentence splitter and clause decomposition. audit.py's extensibility contract
says a consumer must iterate `totals.by_category` and ignore unknown
categories, which is what makes this additive.

Nothing here calls llama-server. It is string work, plus an optional CPU
classifier.
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from . import audit
from .audit import (  # noqa: F401  (re-exported for callers of this module)
    CAT_DISAGREEMENT,
    CAT_POLARITY,
    CAT_UNSCOREABLE,
    CAT_UNSUPPORTED,
    DISAGREEMENT_GAP,
    HOP_CHUNK,
    HOP_FINAL,
    SUPPORT_THRESHOLD,
    line_of,
    negation_cues,
    overlap_score,
    sentence_spans,
    splitter_name,
)

CASCADE_SCHEMA_VERSION = 1

# Split sentences into clauses before checking them. ON, and F41 is why: 55.6%
# of real gpt-oss summary sentences carry more than one claim, so a
# sentence-level verdict cannot say WHICH claim is wrong. One switch, so the
# effect on every measured number can be isolated by flipping it.
DECOMPOSE = True

# --- new finding categories (additive to audit.py's) ------------------------
CAT_NUMBER = "number_unsupported"
CAT_ENTITY = "entity_unsupported"
CAT_NEEDS_CLASSIFIER = "needs_classifier"

# Signal kinds. EVERY signal carries one. A hard number mismatch and a soft
# similarity doubt are different evidence and are never merged into one opaque
# score -- the reader has to be able to see WHY something was flagged.
KIND_HARD = "hard"
KIND_SOFT = "soft"
KIND_CLASSIFIER = "classifier"

# Ordering. audit.py's relative intent is preserved (never-checked first,
# disagreement above agreed-unsupported, polarity last) but the scale is this
# module's own and is published in `config.category_priority`, which is what
# makes it self-describing. The new hard categories rank high because they are
# the only findings in the whole ledger that are DETERMINISTIC: a missing
# figure is a fact about the text, not a model's opinion of it.
CASCADE_CATEGORY_PRIORITY = {
    CAT_UNSCOREABLE: 0,
    CAT_NUMBER: 1,
    CAT_ENTITY: 2,
    CAT_NEEDS_CLASSIFIER: 3,
    CAT_DISAGREEMENT: 4,
    CAT_UNSUPPORTED: 5,
    CAT_POLARITY: 6,
}

TIER_UNSCOREABLE = "unscoreable"
TIER_HARD = "hard"
TIER_SOFT = "soft"
TIER_CLASSIFIER = "classifier"

# Measured cost of one claim through the two-model ensemble at production
# evidence size (docs/audit-ledger.md 5: 17-21 s/pair Flan-T5 against a full
# 4096-token chunk; 18.3 s is the figure this cascade is costed against).
# Used only to REPORT the saving; nothing branches on it.
CLASSIFIER_SECONDS_PER_CLAIM = 18.3


# ===========================================================================
# Normalisation
# ===========================================================================

_DIACRITIC_MAP = {
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ð": "d", "þ": "th",
    "ł": "l", "ı": "i", "⁄": "/", "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}


def fold(text):
    """Aggressive case/diacritic/whitespace fold for MATCHING ONLY.

    Never used for display. This exists because the real material this project
    processes is OCR output: the bench corpus contains `SwAmE B.R. SrEdhar`
    style manglings of `Swami B.R. Sridhar`, and a checker that treats those as
    different terms would flag every proper noun in the document. Folding is the
    lax direction and is stated as such.
    """
    if not text:
        return ""
    text = normalise_lookalikes(text)
    out = []
    for ch in text:
        out.append(_DIACRITIC_MAP.get(ch, ch))
    s = unicodedata.normalize("NFKD", "".join(out))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


# Characters that are typographically a hyphen or a space but are not the
# ASCII ones. Mapped 1:1 so character offsets are PRESERVED -- the whole audit
# stack computes locations from offsets, so a normalisation that changed length
# would silently corrupt every span it touched.
_LOOKALIKES = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ",
    "\u202f": " ", "\u2060": " ", "\ufeff": " ",
}
_LOOKALIKE_TABLE = {ord(k): v for k, v in _LOOKALIKES.items()}


def normalise_lookalikes(text):
    """ASCII-ise hyphens and spaces WITHOUT changing string length.

    MEASURED DEFECT, fixed here. Real model output writes "twenty\u2011four"
    with a non-breaking hyphen. The number-word scanner splits on ASCII
    "[\\s-]", so it read that as 20 and 4 rather than 24 -- and 20, being above
    DERIVED_COUNT_MAX, is strict, so a faithful sentence HARD FAILED on a
    number the model never claimed.
    """
    return (text or "").translate(_LOOKALIKE_TABLE)


def singular(word):
    """Crude English singulariser. Deliberately crude: it only has to make
    "years"/"year" and "policies"/"policy" compare equal for a unit-agreement
    SOFT signal. Nothing hard-fails on its output."""
    w = word
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)


def fold_tokens(text):
    return [singular(t) for t in _TOKEN_RE.findall(fold(text))]


_DIGIT_END = re.compile(r"\d[.]$")
_DIGIT_START = re.compile(r"^\d")
# "K." / "P." -- an initial, not the end of a sentence. F41 recorded nltk
# splitting "K.P. Dutt" into a fragment "P.", and the regex fallback this venv
# actually uses splits it too.
_INITIAL_END = re.compile(r"(?:^|[\s(\[.])[A-Z][.]$")
# Abbreviations that end in a period without ending a sentence.
_ABBREV = frozenset("""mr mrs ms dr prof st sr jr rev hon no nos fig figs vol
vols ch chap sec secs para paras cf eg ie etc vs viz approx dept govt univ
ltd pty inc co corp est al ed eds pp""".split())
_ABBREV_END = re.compile(r"(?:^|[\s(\[])([A-Za-z]{1,7})[.]$")


def _is_continuation(prev, nxt):
    """Should `nxt` be glued back onto `prev`?

    Every rule here fixes a split that was OBSERVED on this project's real
    model output, not one imagined from first principles.
    """
    if not prev or not nxt:
        return False
    if _DIGIT_END.search(prev) and _DIGIT_START.match(nxt):
        return True                      # "18." + "55" -- a verse reference
    if _INITIAL_END.search(prev):
        return True                      # "K." + "P. Dutt"
    m = _ABBREV_END.search(prev)
    if m and m.group(1).lower() in _ABBREV:
        return True                      # "Ltd." + "was incorporated"
    return False


# A fragment carrying no letters and no digits is punctuation debris, not a
# claim. F41 measured 9.1% such fragments from nltk on real markdown.
_HAS_CONTENT = re.compile(r"[0-9A-Za-zÀ-ɏ]")


def sentence_units(text):
    """audit.sentence_spans, repaired for real markdown model output.

    THREE MEASURED DEFECTS, all fixed here, all observed on real gpt-oss
    summaries rather than on fixtures:

      * A period between two digits is a decimal point or a reference
        separator. "Bhagavad-gita 18.55" became three "sentences", each
        carrying an orphaned number, and every one of them HARD FAILED against
        a source that contained the reference intact.
      * A period after an initial or an abbreviation does not end a sentence.
        F41 recorded "K.P. Dutt" splitting into a fragment "P.".
      * A fragment with no letters or digits is debris and is dropped rather
        than scored, because scoring it produces a finding a reader cannot act
        on.

    Offsets remain TRUE offsets into `text`; the audit stack computes every
    location from them.
    """
    spans = sentence_spans(text)
    out = []
    for s0, s1, sent in spans:
        if out and _is_continuation(out[-1][2], sent):
            p0 = out[-1][0]
            out[-1] = (p0, s1, text[p0:s1].strip())
            continue
        out.append((s0, s1, sent))
    return [(a, b, c) for a, b, c in out if _HAS_CONTENT.search(c)]


# ---------------------------------------------------------------------------
# Sub-sentence decomposition -- a PREREQUISITE, per F41
# ---------------------------------------------------------------------------
#
# F41 measured **55.6% of real gpt-oss summary sentences carrying more than one
# claim**. That breaks any sentence-level checker, including this one: a
# sentence with three figures of which one is fabricated needs to say WHICH
# figure is unsupported, and a single verdict over the whole sentence cannot.
#
# The number tier already reports each unmatched figure individually, so the
# FINDING was always per-figure. What was still sentence-level was the CLAIM
# UNIT -- routing, overlap, and the span a reader is sent to. Decomposing into
# clauses fixes that, and costs nothing but string work.
#
# This is deliberately shallow. There is no parser and no POS tagger here: it
# splits on punctuation and enumeration structure that is unambiguous in
# written English, and it REFUSES to split on anything requiring syntax. An
# over-eager split would attribute a figure to the wrong clause, which is worse
# than not splitting -- the reader is then pointed confidently at the wrong
# half of the sentence.

# Enumeration markers: "(1)", "(a)", " 1. " at an item boundary.
_ENUM_MARK = re.compile(r"\s*(?:\((?:\d{1,2}|[a-hA-H])\)|(?<=[;:])\s*\d{1,2}[.)])\s*")
# Semicolons and colons introducing a list are hard clause boundaries.
_HARD_BREAK = re.compile(r"\s*;\s*")
# ", and" / ", but" / ", while" -- only counted when both sides look like real
# claims (see _worth_splitting), because "apples, and pears" is one claim.
_COORD = re.compile(
    r",\s+(?:and|but|or|while|whereas|although|though|yet)\s+", re.IGNORECASE)
# Bullet / list-item starts inside one "sentence".
_BULLET = re.compile(r"(?m)^\s*(?:[-*•·]|\d{1,2}[.)])\s+")

_CLAUSE_MIN_WORDS = 4


def _worth_splitting(piece):
    """A candidate clause must look like a claim, not a fragment of a list."""
    words = _TOKEN_RE.findall(piece)
    return len(words) >= _CLAUSE_MIN_WORDS


def clause_spans(text, base=0):
    """[(start, end, clause)] with TRUE offsets into the ORIGINAL text.

    Separators are located, then the CLAUSES ARE THE GAPS BETWEEN THEM. The
    separator text itself -- ";", ", and ", the "(2)" of an enumeration -- is
    discarded rather than attached to a neighbour, because a marker glued to
    the end of the previous clause makes the reader think the next item's
    number belongs to the previous claim.
    """
    if not text or not text.strip():
        return []

    seps = []
    for rx in (_BULLET, _ENUM_MARK, _HARD_BREAK, _COORD):
        for m in rx.finditer(text):
            if m.start() == 0:
                continue                 # a leading marker separates nothing
            seps.append((m.start(), m.end()))
    if not seps:
        return _finish_clauses([(0, len(text))], text, base)

    seps.sort()
    merged_seps = [list(seps[0])]
    for a, b in seps[1:]:
        if a <= merged_seps[-1][1]:
            merged_seps[-1][1] = max(merged_seps[-1][1], b)
        else:
            merged_seps.append([a, b])

    pieces, cursor = [], 0
    for a, b in merged_seps:
        if a > cursor:
            pieces.append((cursor, a))
        cursor = b
    if cursor < len(text):
        pieces.append((cursor, len(text)))

    # Merge any piece too short to be a claim into its neighbour. This keeps
    # "apples, and pears" as one claim while separating two real clauses.
    merged = []
    for a, b in pieces:
        frag = text[a:b]
        if not _HAS_CONTENT.search(frag):
            continue
        if merged and (not _worth_splitting(frag)
                       or not _worth_splitting(text[merged[-1][0]:merged[-1][1]])):
            merged[-1] = (merged[-1][0], b)
            continue
        merged.append((a, b))
    return _finish_clauses(merged, text, base)


def _finish_clauses(pieces, text, base):
    out = []
    for a, b in pieces:
        frag = text[a:b]
        stripped = frag.strip()
        if not stripped or not _HAS_CONTENT.search(stripped):
            continue
        lead = len(frag) - len(frag.lstrip())
        out.append((base + a + lead, base + a + lead + len(stripped), stripped))
    return out


def claim_spans(text, decompose=True):
    """The unit the cascade actually checks: a clause, or a whole sentence.

    Returns [(start, end, text, sentence_index, clause_index)].
    """
    units = []
    for s_i, (s0, _s1, sent) in enumerate(sentence_units(text)):
        if not decompose:
            units.append((s0, s0 + len(sent), sent, s_i, 0))
            continue
        clauses = clause_spans(sent, base=s0)
        if len(clauses) <= 1:
            units.append((s0, s0 + len(sent), sent, s_i, 0))
            continue
        for c_i, (c0, c1, ctext) in enumerate(clauses):
            units.append((c0, c1, ctext, s_i, c_i))
    return units


# ===========================================================================
# Tier 1a -- NUMBERS
# ===========================================================================
#
# The single highest-value check for this project's material. Retention
# periods, staff counts, statutory deadlines, dollar figures: a fabricated
# figure in a summary of a retention policy is the exact failure the project
# exists to prevent, and unlike everything else in the audit stack it can be
# checked exactly, in microseconds, with no model and no threshold.
#
# The whole difficulty is NORMALISATION. "seven" and "7" and "seven-year" and
# "7 years" are the same number; "$1.2m" and "1,200,000" are the same number;
# "3 February 2026" and "2026-02-03" are the same date. A checker that misses
# any of those is a false-positive machine, and a false-positive machine is
# worse than no checker because the reader learns to skim the flags.

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10 ** 6,
           "billion": 10 ** 9, "trillion": 10 ** 12}
# Ordinal word forms -> the cardinal they denote.
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
    "hundredth": 100, "thousandth": 1000,
}
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12,
    "dec": 12,
}
_MAGNITUDE_SUFFIX = {"k": 1000, "m": 10 ** 6, "bn": 10 ** 9, "b": 10 ** 9,
                     "tn": 10 ** 12}
_MAGNITUDE_WORD = dict(_SCALES)
_MAGNITUDE_WORD.update({"hundreds": 100, "thousands": 1000, "millions": 10 ** 6,
                        "billions": 10 ** 9})

_CURRENCY = "$£€¥"

# Number kinds. `kind` decides STRICTNESS, not matching: matching is on value.
K_MONEY = "money"
K_PERCENT = "percent"
K_YEAR = "year"
K_MONTH = "month"
K_ORDINAL = "ordinal"
K_DAY = "day"
K_PLAIN = "plain"

# Units that make a small number a MEASUREMENT rather than a count of things
# on the page. "7 years" is a retention period -- this project's flagship
# example and the exact figure a fabrication would corrupt -- so it must be
# STRICT even though 7 is small. "three deficiencies" is a count of enumerated
# items and may legitimately be derived, so it stays lax. This set is the line
# between those two readings.
MEASURE_UNITS = frozenset("""
year yr month week day hour hr minute min second sec decade fortnight
dollar cent pound euro pence
percent pc
page paragraph clause section article schedule part chapter
gb mb kb tb byte
km metre meter mile foot feet inch centimetre cm mm
kg gram tonne ton pound lb
degree celsius fahrenheit
""".split())

# A bare integer at or below this, with no year/money/percent character, is
# treated as POSSIBLY DERIVED -- see `_strict` and the derived-count discussion
# in the module tests and in docs. Above it, a number that is not in the source
# is a hard fail.
DERIVED_COUNT_MAX = 10


class Num:
    """One numeric mention, normalised."""

    __slots__ = ("text", "start", "end", "value", "kind", "unit", "prev_word",
                 "mantissa", "in_range")

    def __init__(self, text, start, end, value, kind, unit=None, prev_word=None,
                 mantissa=None, in_range=False):
        self.text = text
        self.start = start
        self.end = end
        self.value = value          # Decimal
        self.kind = kind
        self.unit = unit            # singularised word following the number
        self.prev_word = prev_word  # singularised word preceding it
        self.mantissa = mantissa    # pre-magnitude value, e.g. 1.2 for "$1.2m"
        self.in_range = in_range

    def as_dict(self):
        d = {"text": self.text, "value": str(self.value), "kind": self.kind,
             "unit": self.unit, "strict": self.strict}
        if self.mantissa is not None and self.mantissa != self.value:
            d["mantissa"] = str(self.mantissa)
        if self.in_range:
            d["in_range"] = True
        return d

    # -- the bias decision, stated in one place -----------------------------
    #
    # BIASED STRICT where fabrication is dangerous, LAX where derivation is
    # plausible -- and the lax cases still ESCALATE, they are never silently
    # passed.
    #
    # STRICT (a missing counterpart is a HARD FAIL): money, percentages, years
    # and dates, anything with a fractional part, and any integer above
    # DERIVED_COUNT_MAX. None of these can be arrived at by counting the items
    # in front of you. "$1.2m", "7 years", "72 hours", "2026", "3.5%", "41
    # staff" -- these are precisely the figures the project's material turns on,
    # and a summary that states one the source does not state is the failure
    # mode the whole audit stack exists for.
    #
    # LAX (unmatched -> escalate, not fail): a bare integer <= 10 with no year,
    # money or percent character. This is the derived-count case: a source that
    # enumerates "(1) ... (2) ... (3) ..." supports "three deficiencies"
    # faithfully while containing no token "three" anywhere. `enumeration_count`
    # below tries to resolve those before giving up, and only the residue
    # escalates. Getting this wrong in the strict direction would flood the
    # ledger with false alarms on ordinary correct summarising; getting it wrong
    # in the lax direction would miss a fabricated small count -- so the lax
    # branch does not pass, it hands the claim to the next tier.
    @property
    def strict(self):
        if self.kind in (K_MONEY, K_PERCENT, K_YEAR, K_MONTH, K_DAY):
            return True
        if self.value != self.value.to_integral_value():
            return True
        if self.unit in MEASURE_UNITS or self.prev_word in MEASURE_UNITS:
            # A measurement, not a count of items on the page. "7 years",
            # "72 hours", "30 days", "12 pages" cannot be arrived at by
            # counting enumerated things, so a missing counterpart is a fail
            # however small the number is.
            return True
        try:
            iv = int(self.value)
        except (ValueError, OverflowError):
            return True
        return not (0 < iv <= DERIVED_COUNT_MAX)


def _dec(s):
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


_WORD_AT = re.compile(r"[^\W\d_]+", re.UNICODE)


def _neighbour_words(text, start, end):
    """(previous word, following word), singularised and folded.

    The following word is the number's UNIT in ordinary prose ("seven years",
    "41 staff") and is what makes "7 years" and "seven-year" comparable, since
    a hyphen is just a separator here.
    """
    before = text[max(0, start - 40):start]
    after = text[end:end + 40]
    prev_m = None
    for prev_m in _WORD_AT.finditer(before):
        pass
    nxt_m = _WORD_AT.search(after)
    prev_w = singular(fold(prev_m.group(0))) if prev_m else None
    nxt_w = singular(fold(nxt_m.group(0))) if nxt_m else None
    return prev_w, nxt_w


# Pass 1: ISO dates. Claimed first so 2026-03-12 is never read as a range.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# Pass 2: slash / dotted dates.
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
# Pass 3: currency / percent / magnitude-suffixed digit numbers.
_RICH_NUM = re.compile(
    r"(?P<cur>[" + _CURRENCY + r"]\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?(?:%|per\s?cent|percent|k\b|m\b|bn\b|b\b|tn\b|"
    r"hundred|thousand|million|billion|trillion))?",
    re.IGNORECASE)
# Pass 5: month names.
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)
# Pass 6: English number words, including hyphen-joined compounds.
_NUMWORD_VOCAB = set(_ONES) | set(_TENS) | set(_SCALES) | set(_ORDINAL_WORDS) | {"and"}
_NUMWORD_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_NUMWORD_VOCAB, key=len, reverse=True)) +
    r")(?:[\s-]+(?:" + "|".join(sorted(_NUMWORD_VOCAB, key=len, reverse=True)) +
    r"))*\b", re.IGNORECASE)
# Digit ordinals: 1st, 22nd, 3rd, 14th.
_DIGIT_ORD = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)
# A range: two numeric-looking things joined by a dash or "to".
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _parse_number_words(parts):
    """Strict English number-word parser over an already-split token list.

    ("forty","one") -> 41.  ("one","hundred","and","twelve") -> 112.
    ("seven","and","eight") -> REJECTED, deliberately.

    THE REJECTION IS THE POINT. A permissive left-to-right adder turns "seven
    and eight" into 15, and 15 occurs nowhere in the source, so a permissive
    parser would raise a HARD FAIL on a perfectly faithful sentence. This is
    the single easiest way for a number checker to become the false-positive
    machine the brief warns about, so the parser tracks PLACE VALUE and refuses
    anything that is not a well-formed English numeral. A rejected phrase is
    then re-scanned token by token (see `_scan_number_words`), which recovers
    the 7 and the 8 separately -- missing a compound is the lax direction and
    costs a missed catch; inventing one is the strict direction and costs a
    false alarm on correct output. Lax wins here.

    Returns (Decimal, is_ordinal) or (None, False).
    """
    parts = [p for p in parts if p]
    if not parts:
        return None, False
    total = Decimal(0)
    current = Decimal(0)
    seen_value = False
    is_ordinal = False
    # None -> nothing in the current group; "tens" -> a 20..90 is present and a
    # units digit may still follow; "ones" -> the group's units slot is taken.
    place = None
    for i, p in enumerate(parts):
        last = (i == len(parts) - 1)
        if p == "and":
            # Only the "hundred and twelve" join. Anywhere else "and" is
            # ordinary conjunction between two separate numbers.
            if current == 0 or place is not None:
                return None, False
            continue
        if p in _ORDINAL_WORDS and (p not in _ONES and p not in _TENS):
            if not last:
                return None, False       # an ordinal can only end a numeral
            v = Decimal(_ORDINAL_WORDS[p])
            if v >= 100:
                if not seen_value:
                    return None, False
                current = (current or Decimal(1)) * v
            else:
                if place == "ones":
                    return None, False
                if place == "tens" and v >= 10:
                    return None, False
                current += v
            is_ordinal = True
            seen_value = True
            place = "ones"
            continue
        if p in _ONES:
            v = _ONES[p]
            if place == "ones":
                return None, False       # "seven eight" is two numbers
            if place == "tens" and v >= 10:
                return None, False       # "twenty twelve" is not a numeral
            current += v
            seen_value = True
            place = "ones"
            continue
        if p in _TENS:
            if place is not None:
                return None, False       # "twenty thirty" is two numbers
            current += _TENS[p]
            seen_value = True
            place = "tens"
            continue
        if p == "hundred":
            if not seen_value:
                return None, False       # bare "hundred" is a magnitude word
            current = (current or Decimal(1)) * 100
            place = None
            continue
        if p in _SCALES:
            if not seen_value:
                return None, False       # bare "million" is a magnitude word
            total += (current or Decimal(1)) * _SCALES[p]
            current = Decimal(0)
            place = None
            continue
        return None, False
    if not seen_value:
        return None, False
    return total + current, is_ordinal


_NUMWORD_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _scan_number_words(phrase, base):
    """Greedy longest-valid-numeral scan over a run of number words.

    Yields (value, is_ordinal, start, end) with offsets relative to `base`.
    Longest-first so "one hundred and twelve" is 112 rather than 1 then 100
    then 12, while "seven and eight" -- which has no valid long reading --
    falls back to its two single tokens.
    """
    toks = [(m.group(0).lower(), m.start(), m.end())
            for m in _NUMWORD_TOKEN.finditer(phrase)]
    out = []
    i = 0
    while i < len(toks):
        for j in range(len(toks), i, -1):
            words = [t[0] for t in toks[i:j]]
            if words[-1] == "and":
                continue                 # never end a numeral on "and"
            val, is_ord = _parse_number_words(words)
            if val is not None:
                out.append((val, is_ord,
                            base + toks[i][1], base + toks[j - 1][2]))
                i = j
                break
        else:
            i += 1
    return out


# Month words that are also ordinary English words. These need a day or a year
# beside them before they count as a date.
_AMBIGUOUS_MONTHS = frozenset({"may", "march", "august", "mar", "aug", "jun",
                               "jul", "sept", "sep"})
_DATE_PARTNER = re.compile(r"\d{1,4}")


def _has_date_partner(text, start, end, window=14):
    """Is there a day or year number immediately beside this month word?"""
    before = text[max(0, start - window):start]
    after = text[end:end + window]
    return bool(_DATE_PARTNER.search(before) or _DATE_PARTNER.search(after))


class _Claimed:
    """Character spans already consumed by an earlier extraction pass."""

    def __init__(self):
        self.spans = []

    def free(self, start, end):
        return all(end <= s or start >= e for s, e in self.spans)

    def take(self, start, end):
        self.spans.append((start, end))


def extract_numbers(text):
    """Every numeric mention in `text`, normalised. Order is by position.

    Passes run most-specific first and consume their character spans, so
    "2026-03-12" is a date rather than a subtraction and "$1.2m" is one number
    rather than two.
    """
    text = normalise_lookalikes(text)
    claimed = _Claimed()
    out = []

    def add(t, s, e, value, kind, mantissa=None):
        prev_w, next_w = _neighbour_words(text, s, e)
        out.append(Num(t, s, e, value, kind, unit=next_w, prev_word=prev_w,
                       mantissa=mantissa))

    # 1. ISO dates -> three components (year, month, day).
    for m in _ISO_DATE.finditer(text):
        claimed.take(m.start(), m.end())
        y, mo, d = (Decimal(m.group(1)), Decimal(m.group(2)), Decimal(m.group(3)))
        add(m.group(0), m.start(), m.end(), y, K_YEAR)
        add(m.group(0), m.start(), m.end(), mo, K_MONTH)
        add(m.group(0), m.start(), m.end(), d, K_DAY)

    # 2. d/m/y dates. Ambiguous between day-first and month-first, so BOTH
    #    leading components are emitted -- the check is "does this number occur
    #    in the source", and emitting both is the lax direction.
    for m in _SLASH_DATE.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        claimed.take(m.start(), m.end())
        a, b, c = Decimal(m.group(1)), Decimal(m.group(2)), Decimal(m.group(3))
        if c < 100:
            c += 2000 if c < 50 else 1900
        add(m.group(0), m.start(), m.end(), a, K_DAY)
        add(m.group(0), m.start(), m.end(), b, K_MONTH)
        add(m.group(0), m.start(), m.end(), c, K_YEAR)

    # 3. Digit ordinals, before the general digit pass so "1st" keeps its kind.
    for m in _DIGIT_ORD.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        claimed.take(m.start(), m.end())
        add(m.group(0), m.start(), m.end(), Decimal(m.group(1)), K_ORDINAL)

    # 4. Currency / percent / magnitude / plain digit numbers.
    for m in _RICH_NUM.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        raw = m.group("num").replace(",", "")
        val = _dec(raw)
        if val is None:
            continue
        claimed.take(m.start(), m.end())
        suf = (m.group("suf") or "").strip().lower().replace(" ", "")
        cur = m.group("cur")
        mantissa = val
        kind = K_PLAIN
        if suf == "%" or suf.startswith("percent"):
            # `suf` has had its internal spaces stripped, so "per cent",
            # "percent" and "%" all arrive here as one of two forms.
            kind = K_PERCENT
        elif suf in _MAGNITUDE_SUFFIX:
            val = val * _MAGNITUDE_SUFFIX[suf]
        elif suf in _MAGNITUDE_WORD:
            val = val * _MAGNITUDE_WORD[suf]
        if cur:
            kind = K_MONEY
        elif kind == K_PLAIN and val == val.to_integral_value() \
                and 1000 <= val <= 2999 and len(raw) == 4:
            # A bare four-digit integer in the plausible-year range. Tagged
            # K_YEAR only to make it STRICT: a fabricated year is exactly the
            # "statutory deadline" failure and can never be a derived count.
            kind = K_YEAR
        add(m.group(0).strip(), m.start(), m.end(), val, kind,
            mantissa=mantissa if mantissa != val else None)

    # 5. Month names -- CAPITALISED ONLY, and the ambiguous ones need a partner.
    #
    #    MEASURED FALSE POSITIVE, fixed here: an earlier version matched "may"
    #    case-insensitively, so "beginners may need to follow external rules"
    #    yielded month 5, which is STRICT, which HARD FAILED a completely
    #    faithful sentence of real model output. "may", "march" and "august"
    #    are ordinary English words, so they are only read as months when a day
    #    or year sits next to them. Months are proper nouns, so a lowercase
    #    match is never a month.
    for m in _MONTH_RE.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        word = m.group(0)
        if not word[:1].isupper():
            continue
        if word.lower() in _AMBIGUOUS_MONTHS and not _has_date_partner(
                text, m.start(), m.end()):
            continue
        claimed.take(m.start(), m.end())
        add(word, m.start(), m.end(), Decimal(_MONTHS[word.lower()]), K_MONTH)

    # 6. English number words, greedily longest-first within each run.
    for m in _NUMWORD_RE.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        for val, is_ord, s, e in _scan_number_words(m.group(0), m.start()):
            if not claimed.free(s, e):
                continue
            claimed.take(s, e)
            add(text[s:e], s, e, val, K_ORDINAL if is_ord else K_PLAIN)

    # 7. Ranges: mark endpoints, so a claimed range is only supported when BOTH
    #    endpoints are.
    for m in _RANGE_RE.finditer(text):
        for n in out:
            if n.start >= m.start() and n.end <= m.end():
                n.in_range = True

    out.sort(key=lambda n: (n.start, n.kind))
    return out


_ENUM_PATTERNS = [
    re.compile(r"\(\s*\d+\s*\)"),                        # (1) (2) (3)
    re.compile(r"\(\s*[a-h]\s*\)", re.IGNORECASE),       # (a) (b) (c)
    re.compile(r"(?m)^\s*\d+\s*[.)]\s+"),                # 1.  2)  at line start
    re.compile(r"(?m)^\s*[-*•·]\s+"),          # bullets
]


def enumeration_count(evidence):
    """How many enumerated items the evidence appears to contain.

    This is the answer to the DERIVED-NUMBER problem. A source that says
    "deficiencies: (1) ... (2) ... (3) ..." fully supports the summary sentence
    "the audit found three deficiencies", but contains no token "three"
    anywhere, so a naive token check flags a faithful summary. Counting the
    enumeration markers recovers the derived value without a model.

    Returns the largest count any single marker family yields, or 0. Deliberately
    not clever: it is one more way to say "supported", never a way to say
    "unsupported".
    """
    best = 0
    for rx in _ENUM_PATTERNS:
        n = len(rx.findall(evidence or ""))
        if n > best:
            best = n
    return best


_CENTURY_WORDS = frozenset({"century", "centuries", "c"})


def century_support(mention, evidence_numbers):
    """Is an ordinal century DERIVABLE from a year in the evidence?

    MEASURED FALSE POSITIVE, fixed here. On the real 5-chunk job the model
    wrote "a 15th-century Indian saint" from a source that says 1485. That is
    correct summarising and arithmetic a reader does without noticing, but
    there is no token "15" anywhere in the span, so the hard tier failed a
    faithful sentence.

    Centuries are the one derivation common enough, and mechanical enough, to
    be worth computing: year 1485 lies in the 15th century because
    1485 // 100 + 1 == 15. Like `enumeration_count`, this is only ever a way to
    say SUPPORTED -- it can never turn a matched number into a failure.
    """
    if mention.kind != K_ORDINAL:
        return None
    if not ({mention.unit, mention.prev_word} & _CENTURY_WORDS):
        return None
    try:
        want = int(mention.value)
    except (ValueError, OverflowError):
        return None
    for n in evidence_numbers:
        if n.kind != K_YEAR:
            continue
        try:
            year = int(n.value)
        except (ValueError, OverflowError):
            continue
        if year // 100 + 1 == want:
            return year
    return None


def _value_index(nums):
    idx = {}
    for n in nums:
        idx.setdefault(n.value, []).append(n)
    return idx


def check_numbers(claim, evidence):
    """Hard signal: does every number in `claim` occur in `evidence`?

    Matching is on the normalised VALUE, not on the surface form and not on the
    kind. "$1.2m" in the claim matches "1,200,000" in the source; "seven-year"
    matches "7 years"; "March 2026" matches "12/03/2026". A KIND difference
    (money claimed against a plain source number) does not fail the hard check
    -- it is reported as a soft `unit_agreement` observation instead, because
    the figure itself is present and a reader can see the rest.
    """
    c_nums = extract_numbers(claim)
    e_nums = extract_numbers(evidence)
    e_idx = _value_index(e_nums)
    e_mantissa = {}
    for n in e_nums:
        if n.mantissa is not None:
            e_mantissa.setdefault(n.mantissa, []).append(n)
    enum_n = enumeration_count(evidence)

    matched, unmatched, derived, unit_notes = [], [], [], []
    for c in c_nums:
        hits = e_idx.get(c.value)
        via = "value"
        if not hits and c.mantissa is not None:
            # "1.2 million" claimed where the source writes "1.2" plus a
            # magnitude word the extractor grouped differently. Lax on purpose.
            hits = e_idx.get(c.mantissa) or e_mantissa.get(c.mantissa)
            via = "mantissa"
        if not hits and c.mantissa is None:
            hits = e_mantissa.get(c.value)
            via = "mantissa"
        if hits:
            best = hits[0]
            matched.append({"claim": c.as_dict(), "evidence": best.as_dict(),
                            "via": via})
            if c.unit and best.unit and c.unit != best.unit \
                    and c.unit != best.prev_word:
                unit_notes.append(
                    {"value": str(c.value), "claim_unit": c.unit,
                     "evidence_unit": best.unit})
            elif c.kind != best.kind and K_PLAIN not in (c.kind, best.kind):
                unit_notes.append(
                    {"value": str(c.value), "claim_kind": c.kind,
                     "evidence_kind": best.kind})
            continue
        year = century_support(c, e_nums)
        if year is not None:
            matched.append({"claim": c.as_dict(),
                            "evidence": {"text": str(year), "value": str(year),
                                         "kind": K_YEAR},
                            "via": "century_of_year"})
            continue
        if not c.strict and enum_n and Decimal(enum_n) == c.value:
            derived.append({"claim": c.as_dict(), "enumeration_count": enum_n})
            continue
        (unmatched if c.strict else derived).append(
            {"claim": c.as_dict()} if c.strict
            else {"claim": c.as_dict(), "enumeration_count": enum_n})

    status = "fail" if unmatched else ("warn" if derived else "pass")
    return {
        "name": "numbers", "kind": KIND_HARD, "status": status,
        "claim_numbers": len(c_nums),
        "matched": matched,
        "unmatched": unmatched,
        "derived_or_uncountable": derived,
        "evidence_enumeration_count": enum_n,
        "unit_disagreements": unit_notes,
        "detail": _numbers_detail(unmatched, derived),
    }


def _numbers_detail(unmatched, derived):
    if unmatched:
        return ("HARD FAIL. " + "; ".join(
            f"the claim states {u['claim']['text']!r} "
            f"(normalised {u['claim']['value']}) and no number with that value "
            f"occurs in the cited span" for u in unmatched)
            + ". A classifier cannot make a missing figure present, so this is "
              "not escalated -- read the cited span.")
    if derived:
        return ("A small count in the claim has no literal counterpart in the "
                "cited span. It may be DERIVED (correctly counting enumerated "
                "items), so it is escalated rather than failed: "
                + "; ".join(f"{d['claim']['text']!r}" for d in derived))
    return "Every number in the claim occurs in the cited span."


# ===========================================================================
# Tier 1b -- NAMED ENTITIES AND DISTINCTIVE TERMS
# ===========================================================================
#
# Weaker than the number check and known to be so. A summary legitimately
# paraphrases, and the fuzzy allowance below exists because the real corpus is
# OCR output. So this is scoped narrowly to terms that a summary has no
# business inventing: proper-noun phrases, acronyms and alphanumeric
# identifiers. General lexical divergence is left to the SOFT overlap signal,
# where it belongs, rather than being promoted into a hard flag it cannot
# support.

_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_IDENT_RE = re.compile(r"\b(?=[A-Za-z]*\d)[A-Za-z]+[-/]?\d[A-Za-z0-9-]*\b")
_CAP_SEQ_RE = re.compile(r"\b[A-Z][\w'’.-]*(?:\s+(?:of|the|and|for|de|van|von)\s+)?"
                         r"(?:\s*\b[A-Z][\w'’.-]*)*")
# Words that are capitalised for reasons other than being a name.
_CAP_STOP = frozenset("""the a an this that these those it he she they we you i and or but
if then when while for to of in on at by with from as is are was were be been being
however therefore thus moreover furthermore additionally section sections chapter
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday""".split())

# Words _CAP_SEQ_RE may span but that must never END a proper-noun phrase.
_CAP_CONNECTORS = frozenset("of the and for de van von in on at to".split())

# MEASURED, by sweeping it against both error directions on 592 real summary
# claims and 238 injected fabricated names:
#
#   fuzz   false positives on real output   catch rate on fabricated names
#   0.88            22.5%                              99.6%
#   0.85            19.8%                              99.6%
#   0.80            17.4%                              99.6%
#   0.75            15.2%                              99.6%
#   0.70            14.9%                              99.6%
#   0.65            12.5%                              72.7%   <- catch collapses
#
# 0.75 is the knee: everything above it costs false positives for nothing, and
# below 0.70 the catch rate falls off a cliff. Note what the whole curve says --
# even at its best this signal flags roughly ONE IN SEVEN faithful sentences,
# which is why it does not decide anything. See ENTITY_MODE.
ENTITY_FUZZ = 0.75
# Entities shorter than this are too collision-prone to check.
ENTITY_MIN_LEN = 3
# Require a fuzzy match to agree on the first folded character. Transliteration
# and OCR variants of a name overwhelmingly preserve the initial letter
# (Radharani/Radharao^, Vaikuntha/Vaikuoeha), while two genuinely different
# names usually differ there. It buys a lower fuzz threshold without buying
# the false matches a lower threshold would otherwise admit.
ENTITY_REQUIRE_INITIAL = True

# What an absent distinctive term DOES.
#
#   "hard"   -> a hard failure, like an absent number
#   "route"  -> escalate the claim, never fail it            (DEFAULT)
#   "off"    -> compute and report the signal, but do not route on it
#
# DEFAULT IS "route", AND THAT IS A MEASURED DECISION, not caution. On 592
# real summary claims the entity check flags 15% even at its best threshold,
# and inspection shows almost all of them are the checker's fault rather than
# the model's: the source is mojibake ("Rådhåråò^") and the model correctly
# reconstructs the transliteration ("Rādhārāṇī"), so the term IS supported and
# the checker cannot see it. A hard failure on one in seven faithful sentences
# would teach the reader to skim the flags -- exactly what disqualified the
# polarity check in docs/audit-ledger.md section 2, measured the same way.
#
# It is kept rather than deleted because it catches 99.6% of injected
# fabricated names, and on clean (non-OCR) source it should be far quieter.
# An operator who knows their corpus is clean can set "hard".
ENTITY_MODE = "route"


def extract_entities(text):
    """Distinctive terms a summary should not be inventing.

    Returns [(surface, category)] with category in
    {"acronym", "identifier", "proper_noun"}.
    """
    text = normalise_lookalikes(text)
    found = []
    seen = set()

    def push(s, cat):
        s = s.strip(" \t\n.,;:'\"()[]*_")
        # Possessives: the source says "Maharaja", the summary says
        # "Maharaja's". Matching the inflected form against the source failed
        # on real output, so the clitic is removed before comparison.
        for suffix in ("'s", "\u2019s", "s'", "s\u2019"):
            if s.endswith(suffix):
                s = s[:-len(suffix)] + ("s" if suffix[0] == "s" else "")
                break
        s = s.strip(" \t\n.,;:'\"()[]*_")
        if len(s) < ENTITY_MIN_LEN:
            return
        key = (fold(s), cat)
        if key in seen:
            return
        seen.add(key)
        found.append((s, cat))

    for m in _IDENT_RE.finditer(text):
        push(m.group(0), "identifier")
    for m in _ACRONYM_RE.finditer(text):
        push(m.group(0), "acronym")

    # Proper nouns: runs of capitalised words, with two corrections that both
    # matter for the false-positive rate.
    #
    #   * Leading capitalised FUNCTION words are stripped, so "However
    #     Riverside failed" yields "Riverside" and not the phrase "However
    #     Riverside", which would never occur in the source and would flag a
    #     faithful sentence.
    #   * A run that is a SINGLE word at the very start of a sentence is
    #     skipped, because "Records must be kept" capitalises "Records" for
    #     grammar, not because it is a name. A MULTI-word sentence-initial run
    #     is kept in full -- "Riverside Health was audited" opens with a real
    #     name, and an earlier version of this function dropped exactly that
    #     case and silently stopped checking it.
    for _s_start, _s_end, sent in sentence_units(text):
        # Where the sentence's PROSE starts, ignoring list bullets and markdown
        # emphasis. MEASURED FALSE POSITIVE: real summaries are markdown, so
        # sentences begin "* Spiritual evolution ..." or "**Discussion of ...".
        # Judging sentence-initial position by character 0 made "Spiritual",
        # "Ultimate", "Several", "Using" and "Critique" look like proper nouns
        # that the source did not contain, and each one hard failed.
        lead = len(sent) - len(sent.lstrip(" \t*#>-\u2022\u00b7\"'\u201c\u201d_"))
        for m in _CAP_SEQ_RE.finditer(sent):
            toks = [t for t in m.group(0).split() if t]
            dropped = 0
            while toks and fold(toks[0].strip(".,;:")) in _CAP_STOP:
                toks.pop(0)
                dropped += 1
            # A phrase must not END on a connector: _CAP_SEQ_RE happily spans
            # "Discussion of", which occurs in no source as a unit.
            while toks and fold(toks[-1].strip(".,;:")) in _CAP_CONNECTORS:
                toks.pop()
            if not toks:
                continue
            if m.start() <= lead and dropped == 0 and len(toks) == 1:
                continue
            if all(fold(t.strip(".,;:")) in _CAP_STOP for t in toks):
                continue
            if len(toks) > 1:
                push(" ".join(toks), "proper_noun")
            for t in toks:
                if fold(t.strip(".,;:")) not in _CAP_STOP:
                    push(t, "proper_noun")
    return found


def _entity_supported(term, ev_fold, ev_tokens):
    """Is `term` present in the evidence, allowing OCR-level damage?"""
    f = fold(term)
    compact = re.sub(r"[^0-9a-z]+", "", f)
    if not compact:
        return True, "empty"
    if f in ev_fold:
        return True, "substring"
    # Space/hyphen-insensitive containment, for "Sri Chaitanya"/"SriChaitanya".
    ev_compact = re.sub(r"[^0-9a-z]+", "", ev_fold)
    if compact in ev_compact:
        return True, "compact_substring"
    parts = [p for p in re.split(r"[\s.-]+", f) if len(p) >= ENTITY_MIN_LEN]
    if parts and all(p in ev_fold for p in parts):
        return True, "all_parts_present"
    best = 0.0
    for t in ev_tokens:
        if abs(len(t) - len(compact)) > max(3, len(compact) // 3):
            continue
        if ENTITY_REQUIRE_INITIAL and t[:1] != compact[:1]:
            continue
        r = SequenceMatcher(None, compact, t).ratio()
        if r > best:
            best = r
            if best >= ENTITY_FUZZ:
                return True, f"fuzzy:{best:.2f}"
    return False, f"best_fuzzy:{best:.2f}"


def check_entities(claim, evidence):
    """Hard signal: distinctive terms in the claim absent from the cited span."""
    terms = extract_entities(claim)
    ev_fold = fold(evidence)
    ev_tokens = [re.sub(r"[^0-9a-z]+", "", t) for t in fold_tokens(evidence)]
    ev_tokens = [t for t in ev_tokens if t]
    matched, missing = [], []
    for surface, cat in terms:
        ok, how = _entity_supported(surface, ev_fold, ev_tokens)
        (matched if ok else missing).append(
            {"term": surface, "category": cat, "match": how})
    return {
        "name": "entities", "kind": KIND_HARD,
        "status": "fail" if missing else "pass",
        "claim_terms": len(terms),
        "matched": matched, "missing": missing,
        "detail": (
            "Distinctive terms in the claim that do not occur in the cited span: "
            + ", ".join(repr(m["term"]) for m in missing)
            + ". Proper nouns, acronyms and identifiers are not things a summary "
              "should invent; matching already tolerates OCR damage and "
              "diacritics, so a miss here means the term is genuinely absent."
            if missing else
            "Every distinctive term in the claim occurs in the cited span.")}


# ===========================================================================
# Tier 2 -- SOFT SIGNALS. These route. They never decide.
# ===========================================================================

def trigram_similarity(a, b):
    """Character-trigram containment of `a` in `b`, on folded text.

    NOT a semantic similarity and deliberately not named one. A sentence
    embedding model would be a second multi-gigabyte dependency and a second
    thing to validate, and this module's whole point is that the cheap tier
    must be trustworthy on its own. Trigram containment is robust to the
    inflection and word-order changes that defeat bag-of-words overlap, costs
    microseconds, and has no failure mode more interesting than being crude.
    """
    fa, fb = fold(a), fold(b)
    if len(fa) < 3:
        return 0.0
    ta = {fa[i:i + 3] for i in range(len(fa) - 2)}
    tb = {fb[i:i + 3] for i in range(len(fb) - 2)}
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


# --- quantifier scope and dropped qualifiers -------------------------------
#
# MEASURED BLIND SPOT, addressed here. On the 36-pair negation battery the
# number and entity checks passed 9 of 36 fabricated claims outright, and every
# one of them was the same shape: the source says "three of twelve files" and
# the summary says "ALL twelve files"; the source says "unless written consent
# is obtained" and the summary drops the "unless". Every FIGURE and every NAME
# in those sentences is genuinely present in the source, so a token-matching
# tier cannot see the fabrication -- what changed is the SCOPE of the claim.
#
# This is the identical failure signature docs/audit-ledger.md 1.2 measured for
# the classifiers ("a qualifier that overrides the main clause"), and the
# classifiers DO get most of these right -- so the cheap tier's job here is not
# to decide them, it is to ESCALATE them. Both signals below are SOFT.

_UNIVERSAL = [r"\ball\b", r"\bevery\b", r"\beach\b", r"\bany\b", r"\bno\b",
              r"\bnone\b", r"\balways\b", r"\bnever\b", r"\bentire\b",
              r"\bwhole\b", r"\bregardless\b", r"\birrespective\b",
              r"\bin all circumstances\b", r"\bwithout exception\b"]
_QUALIFIER = [r"\bunless\b", r"\bexcept\b", r"\bother than\b",
              r"\bprovided that\b", r"\bsubject to\b", r"\bonly if\b",
              r"\bonly where\b", r"\bexcluding\b", r"\bapart from\b",
              r"\bsave for\b", r"\bwhichever is\b", r"\bif and only if\b"]
_NUMWORDS_ALT = "|".join(sorted(set(_ONES) | set(_TENS), key=len, reverse=True))
# "three of twelve", "two of the seven" -- a partial quantification that a
# universalising summary would be contradicting.
_PARTITIVE = re.compile(
    r"\b(?:\d+|" + _NUMWORDS_ALT + r")\s+of\s+(?:the\s+)?(?:\d+|"
    + _NUMWORDS_ALT + r")\b", re.IGNORECASE)


def _present(patterns, text):
    found = []
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            found.append(m.group(0).lower())
    return sorted(set(found))


def scope_signals(claim, evidence):
    """Two SOFT signals about SCOPE rather than content. Routing only."""
    sents = [s for _, _, s in sentence_units(evidence)]
    idx, score = audit.best_match(claim, sents) if sents else (-1, 0.0)
    ev_sent = sents[idx] if idx >= 0 else ""
    out = []

    c_uni = _present(_UNIVERSAL, claim)
    e_uni = _present(_UNIVERSAL, ev_sent)
    partitive = bool(_PARTITIVE.search(ev_sent))
    added = [u for u in c_uni if u not in e_uni]
    out.append({
        "name": "quantifier_scope", "kind": KIND_SOFT,
        "status": "warn" if (added and (partitive or not e_uni)) else "pass",
        "claim_universals": c_uni, "evidence_universals": e_uni,
        "evidence_is_partitive": partitive,
        "match_score": round(score, 3),
        "detail": "The claim quantifies universally where its closest evidence "
                  "sentence does not. 'All twelve files' against a source that "
                  "says 'three of twelve' keeps every number and every name "
                  "intact while reversing the meaning, so no token check can "
                  "see it."})

    e_qual = _present(_QUALIFIER, ev_sent)
    c_qual = _present(_QUALIFIER, claim)
    dropped = [q for q in e_qual if q not in c_qual]
    out.append({
        "name": "dropped_qualifier", "kind": KIND_SOFT,
        "status": "warn" if dropped else "pass",
        "dropped": dropped, "claim_qualifiers": c_qual,
        "evidence_qualifiers": e_qual,
        "match_score": round(score, 3),
        "detail": "The evidence sentence carries a condition or carve-out that "
                  "the claim does not repeat. docs/audit-ledger.md 1.2 measured "
                  "this exact shape -- an overriding qualifier -- as the "
                  "classifiers' weakest category too."})
    return out


# Above this, the claim's vocabulary is so well covered by the cited span that
# escalating adds nothing. Below it, escalate. CHOSEN, then measured -- see the
# sensitivity sweep in the report; the cascade's escalation rate is reported
# alongside the threshold it used, and `config` records it.
PASS_OVERLAP = 0.55
PASS_TRIGRAM = 0.55


def soft_signals(claim, evidence, cues=None):
    """Cheap routing signals. Each is returned separately and labelled."""
    ov = overlap_score(claim, evidence)
    tri = trigram_similarity(claim, evidence)
    pol = audit.polarity_check(claim, evidence, cues)
    sigs = [
        {"name": "lexical_overlap", "kind": KIND_SOFT,
         "value": round(ov, 4), "threshold": PASS_OVERLAP,
         "status": "pass" if ov >= PASS_OVERLAP else "warn",
         "detail": "Fraction of the claim's content words that occur in the "
                   "cited span."},
        {"name": "trigram_similarity", "kind": KIND_SOFT,
         "value": round(tri, 4), "threshold": PASS_TRIGRAM,
         "status": "pass" if tri >= PASS_TRIGRAM else "warn",
         "detail": "Character-trigram containment. Lexical, not semantic."},
    ]
    sigs.extend(scope_signals(claim, evidence))
    if pol:
        sigs.append({
            "name": "polarity", "kind": KIND_SOFT,
            "status": "warn" if pol["mismatch"] else "pass",
            "claim_cues": pol["claim_cues"],
            "evidence_cues": pol["evidence_cues"],
            "match_score": pol["match_score"],
            "detail": "Negation cues in the claim versus its closest evidence "
                      "sentence. ROUTING ONLY: docs/audit-ledger.md 2 measured "
                      "this firing on 36% of claims as a standalone flag, which "
                      "is why it may escalate a claim but never fail one."})
    return sigs


# ===========================================================================
# The cascade
# ===========================================================================

class CascadeResult:
    __slots__ = ("tier", "category", "signals", "detail", "scores")

    def __init__(self, tier, category, signals, detail, scores=None):
        self.tier = tier
        self.category = category      # None when the claim passed
        self.signals = signals
        self.detail = detail
        self.scores = scores or []


def route(claim, evidence, cues=None, escalate_hard=False,
          entity_mode=None):
    """Run tiers 1 and 2 and say what should happen next.

    Returns (decision, signals) where decision is one of:
      "hard_fail_number", "hard_fail_entity", "pass", "escalate".

    `escalate_hard` also sends hard failures to the classifier. OFF by default:
    a hard failure already names a specific, checkable fact -- "the claim says
    9 years and no 9 occurs in the cited span" -- and 18.3 s of classifier adds
    a probability to a certainty. Turning it on is for validating the cascade
    against the ensemble, not for production.
    """
    mode = entity_mode or ENTITY_MODE
    numbers = check_numbers(claim, evidence)
    entities = check_entities(claim, evidence)
    entities["mode"] = mode
    if entities["status"] == "fail" and mode != "hard":
        # Downgrade in the OUTPUT too, not just in the branch below, so that a
        # `status` of "fail" anywhere in a ledger always means a hard failure.
        entities["status"] = "warn" if mode == "route" else "info"
        entities["detail"] += (
            " Reported as a routing signal, not a failure: measured to flag "
            "15% of faithful sentences on real OCR-derived source.")
    soft = soft_signals(claim, evidence, cues)
    signals = [numbers, entities] + soft

    if numbers["status"] == "fail":
        return ("escalate" if escalate_hard else "hard_fail_number"), signals
    if entities["status"] == "fail":
        return ("escalate" if escalate_hard else "hard_fail_entity"), signals

    if numbers["status"] == "warn":
        return "escalate", signals          # unresolved derived count
    if numbers["unit_disagreements"]:
        return "escalate", signals
    if entities["status"] == "warn":
        return "escalate", signals
    if any(s["status"] == "warn" for s in soft):
        return "escalate", signals
    return "pass", signals


def _unit_id(unit, category):
    claim = unit["claim"]
    return "{}:{}:{}.{}:{}".format(
        "h1" if unit["hop"] == HOP_CHUNK else "h2",
        claim["chunk_index"] if claim["chunk_index"] is not None else "final",
        claim["sentence_index"], claim.get("clause_index", 0), category)


def _finding(unit, category, tier, detail, signals, scores=None, extra=None):
    out = {
        "id": _unit_id(unit, category),
        "category": category,
        "priority": CASCADE_CATEGORY_PRIORITY.get(category, 99),
        "tier": tier,
        "hop": unit["hop"],
        "detail": detail,
        "claim": dict(unit["claim"], text=unit["claim_text"]),
        "evidence": unit["evidence"],
        "signals": signals,
        "scores": scores or [],
    }
    if extra:
        out.update(extra)
    return out


def build_cascade_ledger(units, classifier=None, cues=None,
                         escalate_hard=False, include_passing=False,
                         job_id=None, progress=None, notes=None,
                         source=None):
    """Run the cascade over prepared claim units and return a ledger.

    `units` is the shape `audit._claim_units` produces, or `citation_units`
    below. Keeping the unit builder outside this function is what lets the same
    cascade run over map-reduce hops and over `[Section N]` citations without
    either knowing about the other.

    `classifier`: None (default, tier 3 gated off), or a list of >= 2 audit
    scorers. Two is audit.py's requirement, not a default, and it is enforced
    here for the same reason: a single-model gate is what passed the inverted
    retention clause in docs/minicheck-spike.md 4.
    """
    if classifier is not None and len(classifier) < 2:
        raise ValueError(
            "the classifier tier needs at least TWO scorers, for the reason "
            "audit.build_ledger gives: cross-model disagreement is the signal, "
            "and a single-model gate is precisely what passed an inverted "
            "retention obligation in docs/minicheck-spike.md section 4.")

    t0 = time.time()
    findings, passing = [], []
    escalated = []          # indices routed to tier 3
    counts = {"pass": 0, "hard_fail_number": 0, "hard_fail_entity": 0,
              "escalate": 0, "unscoreable": 0}
    routed = []

    for u_i, unit in enumerate(units):
        if unit.get("unscoreable"):
            counts["unscoreable"] += 1
            findings.append(_finding(
                unit, CAT_UNSCOREABLE, TIER_UNSCOREABLE,
                "NOT CHECKED. " + unit["unscoreable"],
                [], extra={"refused_by": "cascade"}))
            routed.append(None)
            continue
        decision, signals = route(unit["claim_text"], unit["evidence_text"],
                                  cues, escalate_hard)
        counts[decision] += 1
        routed.append((decision, signals))
        if decision == "escalate":
            escalated.append(u_i)

    hard_seconds = time.time() - t0

    # ---- tier 3, on the ambiguous remainder only --------------------------
    per_model, refusals = {}, {}
    classifier_seconds = 0.0
    if classifier and escalated:
        for sc in classifier:
            for u_i in escalated:
                if u_i in refusals:
                    continue
                pf = sc.preflight(units[u_i]["evidence_text"],
                                  units[u_i]["claim_text"])
                if not pf.ok:
                    refusals[u_i] = (sc.name, pf)
        scoreable = [i for i in escalated if i not in refusals]
        c0 = time.time()
        for sc in classifier:
            pairs = [(units[i]["evidence_text"], units[i]["claim_text"])
                     for i in scoreable]
            if progress:
                progress(f"tier 3: {len(pairs)} escalated pairs -> {sc.name}")
            per_model[sc.name] = sc.score(pairs)
        classifier_seconds = time.time() - c0
        pos_of = {u_i: p for p, u_i in enumerate(scoreable)}
    else:
        scoreable, pos_of = [], {}

    # ---- emit --------------------------------------------------------------
    for u_i, unit in enumerate(units):
        r = routed[u_i]
        if r is None:
            continue
        decision, signals = r

        if decision == "hard_fail_number":
            findings.append(_finding(
                unit, CAT_NUMBER, TIER_HARD,
                signals[0]["detail"], signals))
            continue
        if decision == "hard_fail_entity":
            findings.append(_finding(
                unit, CAT_ENTITY, TIER_HARD,
                signals[1]["detail"], signals))
            continue
        if decision == "pass":
            if include_passing:
                passing.append({"hop": unit["hop"], "tier": TIER_HARD,
                                "claim": dict(unit["claim"],
                                              text=unit["claim_text"]),
                                "signals": signals})
            continue

        # escalated
        if u_i in refusals:
            name, pf = refusals[u_i]
            findings.append(_finding(
                unit, CAT_UNSCOREABLE, TIER_UNSCOREABLE,
                f"NOT CHECKED past the cheap tier. {name} refused: {pf.reason}",
                signals, extra={"refused_by": name,
                                "preflight": pf.as_dict()}))
            continue

        if not classifier:
            findings.append(_finding(
                unit, CAT_NEEDS_CLASSIFIER, TIER_SOFT,
                "The cheap tier could not resolve this sentence and no "
                "classifier is configured, so IT HAS NOT BEEN CHECKED for "
                "support -- only for the deterministic signals listed. "
                + _why_escalated(signals), signals))
            continue

        pos = pos_of[u_i]
        scores = []
        for sc in classifier:
            res = per_model[sc.name][pos]
            scores.append({"model": sc.name, "kind": KIND_CLASSIFIER,
                           "support_prob": round(res["support_prob"], 4),
                           "label": res["label"],
                           "evidence_units_seen": res["n_units"]})
        probs = [s["support_prob"] for s in scores]
        labels = {s["label"] for s in scores}
        gap = max(probs) - min(probs)
        flagged = False
        if len(labels) > 1 or gap >= DISAGREEMENT_GAP:
            flagged = True
            findings.append(_finding(
                unit, CAT_DISAGREEMENT, TIER_CLASSIFIER,
                "The two checkers do not agree about this sentence ("
                + ", ".join(f"{s['model']} {s['support_prob']:.3f} {s['label']}"
                            for s in scores) + f"; gap {gap:.3f}).",
                signals, scores, {"models_agree": False,
                                  "prob_gap": round(gap, 4)}))
        if labels == {"unsupported"}:
            flagged = True
            findings.append(_finding(
                unit, CAT_UNSUPPORTED, TIER_CLASSIFIER,
                "Both checkers score this sentence as unsupported by the cited "
                "span.", signals, scores,
                {"models_agree": True, "prob_gap": round(gap, 4)}))
        if not flagged and include_passing:
            passing.append({"hop": unit["hop"], "tier": TIER_CLASSIFIER,
                            "claim": dict(unit["claim"],
                                          text=unit["claim_text"]),
                            "signals": signals, "scores": scores})

    findings.sort(key=lambda f: (
        f["priority"], f["hop"],
        f["claim"]["chunk_index"] if f["claim"]["chunk_index"] is not None
        else 1 << 30,
        f["claim"]["sentence_index"], f["claim"].get("clause_index", 0)))

    by_category, by_tier, by_hop = {}, {}, {}
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
        by_tier[f["tier"]] = by_tier.get(f["tier"], 0) + 1
        by_hop[f["hop"]] = by_hop.get(f["hop"], 0) + 1

    n = len(units)
    n_checkable = n - counts["unscoreable"]
    esc_rate = (len(escalated) / n_checkable) if n_checkable else None

    return {
        "schema_version": CASCADE_SCHEMA_VERSION,
        "tool": "missing_link.cascade",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job_id,
        "source": source,
        "verdict": "review_required" if findings else "no_flags_raised",
        "disclaimer": (
            audit.DISCLAIMER +
            " The cheap tier is DETERMINISTIC and its failures are facts about "
            "the text, but its passes are not certifications either: a sentence "
            "whose numbers and named terms all occur in the cited span can "
            "still misstate their relationship."),
        "models": ([{"name": sc.name, "max_model_len": sc.limit}
                    for sc in classifier] if classifier else []),
        "config": {
            "cascade": True,
            "classifier_enabled": bool(classifier),
            "escalate_hard_failures": bool(escalate_hard),
            "pass_overlap": PASS_OVERLAP,
            "pass_trigram": PASS_TRIGRAM,
            "entity_fuzz": ENTITY_FUZZ,
            "entity_mode": ENTITY_MODE,
            "decompose_clauses": DECOMPOSE,
            "derived_count_max": DERIVED_COUNT_MAX,
            "support_threshold": SUPPORT_THRESHOLD,
            "disagreement_gap": DISAGREEMENT_GAP,
            "sentence_splitter": splitter_name(),
            "category_priority": CASCADE_CATEGORY_PRIORITY,
            "signal_kinds": [KIND_HARD, KIND_SOFT, KIND_CLASSIFIER],
        },
        "cascade": {
            "claims": n,
            "claims_checkable": n_checkable,
            "resolved_by_hard_tier": counts["pass"] + counts["hard_fail_number"]
            + counts["hard_fail_entity"],
            "passed_cheaply": counts["pass"],
            "hard_fail_number": counts["hard_fail_number"],
            "hard_fail_entity": counts["hard_fail_entity"],
            "escalated": len(escalated),
            "escalation_rate": round(esc_rate, 4) if esc_rate is not None else None,
            "unscoreable": counts["unscoreable"],
            "cheap_tier_seconds": round(hard_seconds, 4),
            "classifier_seconds": round(classifier_seconds, 2),
            "cost_model": {
                "classifier_seconds_per_claim": CLASSIFIER_SECONDS_PER_CLAIM,
                "seconds_if_every_claim_classified":
                    round(n_checkable * CLASSIFIER_SECONDS_PER_CLAIM, 1),
                "seconds_with_cascade":
                    round(len(escalated) * CLASSIFIER_SECONDS_PER_CLAIM
                          + hard_seconds, 1),
                "saving_fraction":
                    round(1 - (len(escalated) / n_checkable), 4)
                    if n_checkable else None,
                "note": "18.3 s/claim is docs/audit-ledger.md section 5's "
                        "measured cost at production evidence size. The saving "
                        "is arithmetic on the MEASURED escalation rate, not a "
                        "measured wall-clock unless classifier_seconds is "
                        "non-zero.",
            },
        },
        "totals": {
            "claims_examined": n,
            "claims_flagged": len({(f["hop"], f["claim"]["chunk_index"],
                                    f["claim"]["sentence_index"])
                                   for f in findings}),
            "findings": len(findings),
            "by_category": by_category,
            "by_tier": by_tier,
            "by_hop": by_hop,
        },
        "findings": findings,
        "passing": passing if include_passing else None,
        "notes": list(notes or []),
    }


def _why_escalated(signals):
    bits = []
    for s in signals:
        if s.get("status") == "warn":
            if s["name"] == "numbers":
                bits.append("a small count with no literal counterpart")
            elif s["name"] == "lexical_overlap":
                bits.append(f"low word overlap ({s['value']})")
            elif s["name"] == "trigram_similarity":
                bits.append(f"low trigram similarity ({s['value']})")
            elif s["name"] == "polarity":
                bits.append("a negation-cue difference")
        if s["name"] == "numbers" and s.get("unit_disagreements"):
            bits.append("a number matched but its unit did not")
    return "Escalated because: " + (", ".join(bits) if bits else "unspecified") + "."


# ===========================================================================
# Unit builders
# ===========================================================================

def citation_units(document, records, final_summary):
    """Claim units built from `[Section N]` CITATIONS, not from the hop graph.

    This is the unit builder the operator's design is actually about. A
    paragraph tagged `[Section 4]` asserts a reference; the evidence window is
    then exactly `document[start:end]` for chunk 4, resolved in code by
    `worker.parse_section_citations` -- never by asking a model for an offset.

    Each SENTENCE of a cited paragraph becomes a claim against the union of
    that paragraph's cited spans. A paragraph with NO citation is emitted as
    `unscoreable`: there is nothing to check it against, and inventing an
    evidence window by best-match would produce exactly the plausible-looking
    attribution `parse_section_citations` refuses to render.
    """
    from . import worker  # lazy: keeps this module importable without requests

    parsed = worker.parse_section_citations(final_summary or "", records)
    by_index = {}
    for r in records or []:
        idx = r.get("index", r.get("idx"))
        if idx is not None:
            by_index[int(idx)] = r

    # Walk the segment list, accumulating text until a citation closes a
    # paragraph. `parse_section_citations` puts a cite segment where the model
    # put the marker, which the prompt asks for at the END of each paragraph.
    units = []
    buf, cites = [], []
    para_i = 0

    def flush():
        nonlocal buf, cites, para_i
        text = "".join(buf).strip()
        buf = []
        local_cites = cites
        cites = []
        if not text:
            return
        idx = para_i
        para_i += 1
        if not local_cites:
            for s0, s1, sent, s_i, c_i in claim_spans(text, DECOMPOSE):
                units.append({
                    "hop": HOP_FINAL, "claim_text": sent, "evidence_text": "",
                    "unscoreable": (
                        "this paragraph carries no [Section N] citation, so "
                        "there is no cited span to check it against. It is not "
                        "checked and is not certified; the cascade will not "
                        "invent an evidence window by content matching, "
                        "because a guessed attribution is indistinguishable "
                        "from a claimed one once rendered."),
                    "claim": {"unit": "final_summary_paragraph",
                              "chunk_index": None, "paragraph_index": idx,
                              "sentence_index": s_i, "clause_index": c_i,
                              "start_char": s0,
                              "end_char": s1, "line": line_of(text, s0)},
                    "evidence": {"unit": "cited_sections", "chunk_index": None,
                                 "location_confidence": "none",
                                 "cited_sections": []},
                })
            return
        spans, sections = [], []
        for c in local_cites:
            r = by_index.get(c["index"])
            if r is None:
                continue
            spans.append(document[c["start"]:c["end"]])
            sections.append(c["section"])
        evidence = "\n\n".join(spans)
        for s0, s1, sent, s_i, c_i in claim_spans(text, DECOMPOSE):
            units.append({
                "hop": HOP_FINAL, "claim_text": sent, "evidence_text": evidence,
                "claim": {"unit": "final_summary_paragraph",
                          "chunk_index": local_cites[0]["index"],
                          "paragraph_index": idx, "sentence_index": s_i,
                          "clause_index": c_i,
                          "start_char": s0, "end_char": s1,
                          "line": line_of(text, s0)},
                "evidence": {
                    "unit": "cited_sections",
                    "chunk_index": local_cites[0]["index"],
                    "cited_sections": sorted(set(sections)),
                    "start_char": min(c["start"] for c in local_cites),
                    "end_char": max(c["end"] for c in local_cites),
                    "start_line": line_of(document,
                                          min(c["start"] for c in local_cites)),
                    "end_line": line_of(document,
                                        max(c["end"] for c in local_cites)),
                    # DIRECT: the span is the chunk's own persisted offset,
                    # looked up from a label the model was handed. It is not a
                    # located quotation and it is not inferred either -- it is
                    # exactly what the model said it drew on.
                    "location_confidence": "direct",
                    "location_note": (
                        "The span is what the model CITED, resolved through "
                        "stored chunk offsets. The cascade checks whether the "
                        "span supports the sentence; the citation itself only "
                        "asserts that it was consulted."),
                },
            })

    # A paragraph closes when TEXT arrives after at least one citation. Cites
    # are accumulated first, so "[Section 1][Section 2]" attaches BOTH sections
    # to the paragraph they follow rather than closing it on the first one and
    # silently discarding the second.
    for seg in parsed["segments"]:
        if seg["kind"] == "text":
            if cites and seg["text"].strip():
                flush()
            buf.append(seg["text"])
        else:
            cites.append(seg)
    flush()
    return units, parsed


def hop_units(document, chunk_records, final_summary):
    """audit.py's two hops, rebuilt on this module's repaired splitter.

    Same hop definitions and same evidence windows as `audit._claim_units` --
    hop 1 scores a chunk summary against its OWN source span, hop 2 scores the
    final summary against the CONCATENATED CHUNK SUMMARIES, which is what the
    reduce step actually read (F25, DESIGN-NOTES E). The only difference is the
    sentence splitter: `sentence_units` does not break "18.55" in half, and on
    real output that fragmentation alone produced spurious hard failures.
    """
    units = []
    chunk_summaries = [r["summary"] for r in chunk_records]

    for rec in chunk_records:
        idx = rec["index"] if "index" in rec else rec["idx"]
        start, end = rec["start_char"], rec["end_char"]
        evidence = document[start:end]
        for s0, s1, sent, s_i, c_i in claim_spans(rec["summary"], DECOMPOSE):
            units.append({
                "hop": HOP_CHUNK, "claim_text": sent, "evidence_text": evidence,
                "claim": {"unit": "chunk_summary", "chunk_index": idx,
                          "sentence_index": s_i, "clause_index": c_i,
                          "start_char": s0,
                          "end_char": s1, "line": line_of(rec["summary"], s0)},
                "evidence": {"unit": "source_document", "chunk_index": idx,
                             "start_char": start, "end_char": end,
                             "start_line": line_of(document, start),
                             "end_line": line_of(document, end),
                             "location_confidence": "direct"},
            })

    # Hop 2 only exists if there WAS a reduce step; on a single-chunk job the
    # final summary IS the chunk summary and scoring it against itself would
    # report a flawless pass that means nothing.
    if final_summary and len(chunk_records) > 1:
        joined = "\n\n".join(chunk_summaries)
        for s0, s1, sent, s_i, c_i in claim_spans(final_summary, DECOMPOSE):
            m_i, m_score = audit.best_match(sent, chunk_summaries)
            if m_i >= 0:
                rec = chunk_records[m_i]
                idx = rec["index"] if "index" in rec else rec["idx"]
                ev = {"unit": "chunk_summaries_concatenated", "chunk_index": idx,
                      "start_char": rec["start_char"], "end_char": rec["end_char"],
                      "start_line": line_of(document, rec["start_char"]),
                      "end_line": line_of(document, rec["end_char"]),
                      "location_confidence": "indirect",
                      "match_method": "content_word_containment",
                      "match_score": round(m_score, 3)}
            else:
                ev = {"unit": "chunk_summaries_concatenated", "chunk_index": None,
                      "location_confidence": "none",
                      "match_method": "content_word_containment",
                      "match_score": 0.0}
            units.append({
                "hop": HOP_FINAL, "claim_text": sent, "evidence_text": joined,
                "claim": {"unit": "final_summary", "chunk_index": None,
                          "sentence_index": s_i, "clause_index": c_i,
                          "start_char": s0,
                          "end_char": s1, "line": line_of(final_summary, s0)},
                "evidence": ev,
            })
    return units


# ===========================================================================
# Corpus: the real chunk summaries the chunk-size sweep writes
# ===========================================================================

def load_corpus(path):
    """Read a bench/out/chunk-size-bench/corpus_chunk_*.json. READ-ONLY.

    Yields the same unit shape as hop 1: each sentence of a real chunk summary
    against the real chunk text it was produced from. The file records
    start_char/end_char against the ORIGINAL document and `chunk_text` as
    `worker.chunk_document`'s whitespace-normalised form, asserted equal at
    write time -- so `chunk_text` IS the evidence window, and no document is
    needed.
    """
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    units = []
    for c in blob.get("chunks") or []:
        if c.get("status") != "ok" or not c.get("summary"):
            continue
        idx = int(c["index"])
        evidence = c["chunk_text"]
        for s0, s1, sent, s_i, c_i in claim_spans(c["summary"], DECOMPOSE):
            units.append({
                "hop": HOP_CHUNK, "claim_text": sent, "evidence_text": evidence,
                "claim": {"unit": "chunk_summary", "chunk_index": idx,
                          "sentence_index": s_i, "clause_index": c_i,
                          "start_char": s0,
                          "end_char": s1, "line": line_of(c["summary"], s0)},
                "evidence": {"unit": "source_document", "chunk_index": idx,
                             "start_char": c["start_char"],
                             "end_char": c["end_char"],
                             "location_confidence": "direct"},
            })
    meta = {k: blob.get(k) for k in
            ("engine", "model_name", "job_id", "kind", "chunk_tokens",
             "overlap_tokens", "n_chunks", "document_chars", "document_sha256")}
    return units, meta


# ===========================================================================
# CLI
# ===========================================================================

def _build_classifier(names, cache_dir):
    if not names:
        return None
    return [audit.MiniCheckScorer(n, cache_dir=cache_dir) for n in names]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m missing_link.cascade",
        description="Faithfulness cascade: hard deterministic checks first, "
                    "the MiniCheck ensemble only on what they cannot resolve.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--classifier", nargs="*", default=None,
                        help="MiniCheck models for tier 3. Two or more. "
                             "OMIT to run the cheap tiers only -- which is the "
                             "default, because docs/audit-ledger.md 6 has not "
                             "established the ensemble at production chunk size.")
    common.add_argument("--cache-dir", default=os.environ.get("MINICHECK_CACHE_DIR"))
    common.add_argument("--out")
    common.add_argument("--include-passing", action="store_true")
    common.add_argument("--escalate-hard", action="store_true",
                        help="Also send hard failures to the classifier. For "
                             "validating the cascade, not for production.")

    j = sub.add_parser("job", parents=[common],
                       help="Cascade one job from the jobs database (read-only).")
    j.add_argument("--db", default="/opt/missing-link/jobs.sqlite")
    j.add_argument("--job-id", required=True)
    j.add_argument("--mode", choices=["hops", "citations"], default="hops")

    c = sub.add_parser("corpus", parents=[common],
                       help="Cascade real chunk summaries from a bench corpus file.")
    c.add_argument("--path", required=True)

    args = ap.parse_args(argv)
    classifier = _build_classifier(args.classifier, args.cache_dir)
    notes, source, job_id = [], None, None

    if args.cmd == "corpus":
        units, meta = load_corpus(args.path)
        source = dict(meta, path=os.path.abspath(args.path))
        job_id = meta.get("job_id")
        notes.append(
            "Evidence is the corpus file's `chunk_text`, which the producer "
            "asserts equals the whitespace-normalised source span at write "
            "time. Claims are real model output, not fixtures.")
    else:
        document, records, final = audit.load_job(args.db, args.job_id)
        job_id = args.job_id
        source = {"db": os.path.abspath(args.db), "mode": args.mode}
        if args.mode == "citations":
            units, parsed = citation_units(document, records, final)
            notes.append(
                f"{parsed['valid_count']} of {parsed['marker_count']} "
                f"[Section N] markers resolved; {parsed['dropped_count']} "
                f"dropped as unresolvable.")
        else:
            units = hop_units(document, records, final)

    ledger = build_cascade_ledger(
        units, classifier=classifier, escalate_hard=args.escalate_hard,
        include_passing=args.include_passing, job_id=job_id,
        progress=lambda m: print(m, file=sys.stderr), notes=notes, source=source)

    text = json.dumps(ledger, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Faithfulness cascade: cheap deterministic checks first, the classifier last.

WHY THIS EXISTS
---------------
`worker.parse_section_citations` resolves a `[Section N]` marker to a real
character span of the source. That is an attribution, not a verification: it
records that the model SAID a paragraph came from section 4. Nothing in it
checks that section 4 supports the paragraph. Two CONFIRMED results make that
gap dangerous rather than merely incomplete -- citations raise perceived trust
EVEN WHEN RANDOM (arXiv:2501.01303), and readers verify at under 25% whatever
the UI does (arXiv:2512.12207). The reader will not check, so the machine must.

`audit.py` can check, but at ~18.3 s per claim against a production-size
evidence window (docs/audit-ledger.md 5) it is far too expensive to run over
every sentence of every job.

So: a CASCADE. Deterministic checks that cost microseconds run first and
resolve most claims outright; the two-model MiniCheck ensemble is spent only on
what they cannot resolve.

    tier 1  HARD   numbers, then named entities / distinctive terms.
                   Deterministic, explainable, free.
                   A number in the claim with no counterpart in the cited span
                   is a HARD FAIL -- flagged immediately, never escalated,
                   because a classifier cannot make a missing figure present.
    tier 2  SOFT   lexical overlap, character-trigram similarity, unit
                   agreement, negation polarity. These NEVER decide. They only
                   route: clearly fine / clearly suspect / ambiguous.
    tier 3  CLASSIFIER  the audit.py ensemble, on the ambiguous remainder only.

If every number and every distinctive term in the claim is present in the cited
span, and no soft signal objects, the claim PASSES without escalation. That is
the operator's "if the numbers and words match leave it", and it is the whole
reason the cascade is affordable.

THE CLASSIFIER TIER IS PLUGGABLE AND GATED, DELIBERATELY
--------------------------------------------------------
`docs/audit-ledger.md` 6 is explicit that the ensemble's numbers were all
measured on one-to-three-sentence documents and that its reliability at a
4096-token evidence window is NOT established. So `classifier=None` is the
default and the hard tier must stand on its own. When no classifier is
configured, ambiguous claims are reported as `needs_classifier` -- not as
passing, and not as failing. Refuse, do not degrade (F21, F34, F38, F39).

RELATIONSHIP TO audit.py
------------------------
This module IMPORTS from `audit.py` and does not modify it. The scorer
protocol (`.name`, `.limit`, `.preflight`, `.score`), the sentence splitter, the
offset arithmetic, the negation cues and the finding-category system are all
audit's; this module adds three categories and a tier field. `audit.py`'s own
extensibility contract says a consumer must iterate `totals.by_category` and
ignore categories it does not know, which is exactly what makes this additive.

Nothing here calls llama-server. It is string work plus, optionally, a CPU
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
    out = []
    for ch in text:
        out.append(_DIACRITIC_MAP.get(ch, ch))
    s = unicodedata.normalize("NFKD", "".join(out))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


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
K_PLAIN = "plain"

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
        if self.kind in (K_MONEY, K_PERCENT, K_YEAR, K_MONTH):
            return True
        if self.value != self.value.to_integral_value():
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


def _parse_number_words(phrase):
    """"forty-one" -> 41, "twenty-five" -> 25, "one hundred and twelve" -> 112.

    Returns (value, is_ordinal) or (None, False). Rejects bare "and" and bare
    scale words ("million" on its own is a magnitude, not a quantity) so
    ordinary prose does not manufacture numbers.
    """
    parts = [p for p in re.split(r"[\s-]+", phrase.lower()) if p and p != "and"]
    if not parts:
        return None, False
    total = Decimal(0)
    current = Decimal(0)
    seen_value = False
    is_ordinal = False
    for i, p in enumerate(parts):
        if p in _ONES:
            current += _ONES[p]
            seen_value = True
        elif p in _TENS:
            current += _TENS[p]
            seen_value = True
        elif p in _ORDINAL_WORDS:
            if i != len(parts) - 1:
                return None, False       # ordinals only terminate a phrase
            v = _ORDINAL_WORDS[p]
            if v >= 100:
                current = (current or Decimal(1)) * v
            else:
                current += v
            is_ordinal = True
            seen_value = True
        elif p == "hundred":
            if not seen_value:
                return None, False       # bare "hundred" is not a quantity
            current = (current or Decimal(1)) * 100
        elif p in _SCALES:
            if not seen_value:
                return None, False       # bare "million" is a magnitude word
            total += (current or Decimal(1)) * _SCALES[p]
            current = Decimal(0)
        else:
            return None, False
    if not seen_value:
        return None, False
    return total + current, is_ordinal


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
    text = text or ""
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
        add(m.group(0), m.start(), m.end(), d, K_PLAIN)

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
        add(m.group(0), m.start(), m.end(), a, K_PLAIN)
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
        if suf in ("%", "percent", "percent", "per cent", "percent"):
            kind = K_PERCENT
        elif suf in ("percent", "percent"):
            kind = K_PERCENT
        elif suf.startswith("percent") or suf.startswith("percent") or suf == "%":
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

    # 5. Month names.
    for m in _MONTH_RE.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        claimed.take(m.start(), m.end())
        add(m.group(0), m.start(), m.end(),
            Decimal(_MONTHS[m.group(0).lower()]), K_MONTH)

    # 6. English number words.
    for m in _NUMWORD_RE.finditer(text):
        if not claimed.free(m.start(), m.end()):
            continue
        val, is_ord = _parse_number_words(m.group(0))
        if val is None:
            continue
        claimed.take(m.start(), m.end())
        add(m.group(0), m.start(), m.end(), val,
            K_ORDINAL if is_ord else K_PLAIN)

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

# Below this ratio two folded strings are different terms. 0.88 is chosen, not
# measured: it accepts OCR damage ("sredhar"/"sridhar" = 0.92) and rejects
# genuinely different names ("riverside"/"brookside" = 0.67). It is a LAX
# setting and its cost is missed entity fabrications, not false alarms.
ENTITY_FUZZ = 0.88
# Entities shorter than this are too collision-prone to check.
ENTITY_MIN_LEN = 3


def extract_entities(text):
    """Distinctive terms a summary should not be inventing.

    Returns [(surface, category)] with category in
    {"acronym", "identifier", "proper_noun"}.
    """
    text = text or ""
    found = []
    seen = set()

    def push(s, cat):
        s = s.strip(" \t\n.,;:'\"()[]")
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

    # Proper nouns: capitalised runs, minus sentence-initial position and minus
    # capitalised function words. Sentence-initial is excluded because "Records
    # must be kept" would otherwise make "Records" a name.
    for s_start, _s_end, sent in sentence_spans(text):
        for m in _CAP_SEQ_RE.finditer(sent):
            if m.start() == 0:
                # Strip only the first token of a sentence-initial run; the rest
                # of the run is still evidence of a name.
                rest = m.group(0).split(None, 1)
                if len(rest) < 2:
                    continue
                surface = rest[1]
            else:
                surface = m.group(0)
            toks = [t for t in surface.split() if t]
            if not toks:
                continue
            if all(fold(t.strip(".,;:")) in _CAP_STOP for t in toks):
                continue
            push(surface, "proper_noun")
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


def route(claim, evidence, cues=None, escalate_hard=False):
    """Run tiers 1 and 2 and say what should happen next.

    Returns (decision, signals) where decision is one of:
      "hard_fail_number", "hard_fail_entity", "pass", "escalate".

    `escalate_hard` also sends hard failures to the classifier. OFF by default:
    a hard failure already names a specific, checkable fact -- "the claim says
    9 years and no 9 occurs in the cited span" -- and 18.3 s of classifier adds
    a probability to a certainty. Turning it on is for validating the cascade
    against the ensemble, not for production.
    """
    numbers = check_numbers(claim, evidence)
    entities = check_entities(claim, evidence)
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
    if any(s["status"] == "warn" for s in soft):
        return "escalate", signals
    return "pass", signals


def _unit_id(unit, category):
    claim = unit["claim"]
    return "{}:{}:{}:{}".format(
        "h1" if unit["hop"] == HOP_CHUNK else "h2",
        claim["chunk_index"] if claim["chunk_index"] is not None else "final",
        claim["sentence_index"], category)


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
        f["claim"]["sentence_index"]))

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
            for s_i, (s0, s1, sent) in enumerate(sentence_spans(text)):
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
                              "sentence_index": s_i, "start_char": s0,
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
        for s_i, (s0, s1, sent) in enumerate(sentence_spans(text)):
            units.append({
                "hop": HOP_FINAL, "claim_text": sent, "evidence_text": evidence,
                "claim": {"unit": "final_summary_paragraph",
                          "chunk_index": local_cites[0]["index"],
                          "paragraph_index": idx, "sentence_index": s_i,
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

    for seg in parsed["segments"]:
        if seg["kind"] == "text":
            buf.append(seg["text"])
            if "\n\n" in seg["text"] and cites:
                flush()
        else:
            cites.append(seg)
            buf_text = "".join(buf)
            if buf_text.strip():
                flush()
    flush()
    return units, parsed


def hop_units(document, chunk_records, final_summary):
    """audit.py's two hops, unchanged, so the cascade can be run over them."""
    return audit._claim_units(document, chunk_records, final_summary)


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
        for s_i, (s0, s1, sent) in enumerate(sentence_spans(c["summary"])):
            units.append({
                "hop": HOP_CHUNK, "claim_text": sent, "evidence_text": evidence,
                "claim": {"unit": "chunk_summary", "chunk_index": idx,
                          "sentence_index": s_i, "start_char": s0,
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

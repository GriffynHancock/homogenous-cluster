"""A canonical entity index: "is this a person the document talks about?"

WHY THIS EXISTS
---------------
`cascade.check_entities` asks a narrower question than it looks: *do these
exact characters occur in this 4096-token window*. Measured on this project's
real material that question flags **one faithful sentence in seven** even at
its best threshold (`docs/faithfulness-cascade.md` section 6, F42), and
inspection says the checker is wrong nearly every time it fires. The source is
OCR mojibake and the model **correctly** reconstructs the transliteration, so
`Śrīla Kedārnāth` is genuinely supported by a span that spells it
`Srila Kedåränåth` -- and character containment cannot see it.

The operator's framing, which is the better mechanism:

    "we need a system that can recognise a person out of a list of people in a
     corpus sometimes right? to make sure they are mentioned in the document?"

So: build a **canonical index of the entities a scope actually contains**, and
resolve a summary's entity against that index. The question stops being "are
these characters here" and becomes "is this one of the people this text is
about". Two consequences fall out for free:

  * a multi-word name can be compared against a multi-word name. The old check
    compacted `Śrīla Kedārnāth` to one 14-character string and fuzzy-matched it
    against individual SOURCE TOKENS, which are ~8 characters, so the length
    band rejected the comparison before it was made. Entity-to-entity
    comparison is the fix, and it is the single largest source of the measured
    improvement.
  * scopes are first-class. The same index answers "in the cited span?", "in
    another chunk of the same document?" and "in a different document
    entirely?" -- which is exactly the CITATION-ERROR / FABRICATION
    distinction the cascade needs, and it is why resolution always reports
    WHICH scope matched rather than a bare boolean.

THE DANGEROUS DIRECTION, AND THE BIAS AGAINST IT
------------------------------------------------
A checker that is too permissive resolves a **fabricated** name onto a real one
and passes silently -- the exact failure the tool exists to catch, now
laundered by the tool itself. That is strictly worse than the false positives
being fixed, because a false positive is visible and a silent miss is not.

So every rule here is ordered strictest-first, each rule is named in the
output, and each was measured for BOTH error directions before being enabled.
`RULES` is the enabled set and every entry in it earns its place by
measurement, not by plausibility. Two candidate rules were measured and
REJECTED as too permissive; they are listed in `REJECTED_RULES` with their
numbers so the next person does not re-propose them.

STDLIB ONLY
-----------
No spaCy, no NER model, no embeddings. `requirements.txt` is deliberately
seven packages and `docs/DESIGN-NOTES.md` K argues the install has to stay
light enough for an air-gapped wheelhouse. This is `re`, `difflib` and a dict.

The extraction and folding primitives are `cascade`'s, imported lazily so the
two modules do not form an import cycle; nothing here re-implements them,
because two folding functions that drift apart would be a checker whose two
scopes disagree about what a name is.
"""

import re
from difflib import SequenceMatcher

# --- lazily bound cascade primitives ---------------------------------------
# `cascade` imports this module at module level; this module needs `fold` and
# `extract_entities` from `cascade`. Binding on first use rather than at import
# breaks the cycle without duplicating either function.
_FOLD = None
_EXTRACT = None


def _prims():
    global _FOLD, _EXTRACT
    if _FOLD is None:
        from .cascade import extract_entities, fold
        _FOLD = fold
        _EXTRACT = extract_entities
    return _FOLD, _EXTRACT


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

# A token shorter than this is too collision-prone to carry a match on its own.
# Same value, and the same reasoning, as cascade.ENTITY_MIN_LEN.
MIN_TOKEN = 3

# --- thresholds; swept on real output, see docs/two-scope-and-entity-index.md
# Whole-name fuzzy similarity, and the SINGLE definition of it -- cascade's
# ENTITY_FUZZ is an alias of this, because two thresholds that could drift
# apart would be a checker whose two scopes disagree about what counts as the
# same name. The value is the one the project already measured; re-swept here
# against the index and the knee did not move.
WHOLE_FUZZ = 0.75
# Per-token fuzzy, used by `part_fuzzy`. HIGHER than WHOLE_FUZZ on purpose: a
# short token has fewer characters to disagree about, so the same ratio is much
# weaker evidence. Swept 0.74-0.90.
PART_FUZZ = 0.80
# The surname anchor in an initials match must be a near-exact match. This is
# the rule that stops "K.P. Smith" resolving onto "Kedarnath Prasad Dutt".
SURNAME_FUZZ = 0.90
# A fuzzy match must agree on the first folded character. Transliteration and
# OCR variants overwhelmingly preserve the initial letter; two genuinely
# different names usually do not share one. It buys a lower threshold without
# buying the false matches a lower threshold would otherwise admit.
REQUIRE_INITIAL = True

# Rules, strictest first. Resolution stops at the first rule that hits.
R_EXACT = "exact"
R_SUBSTRING = "substring"
R_COMPACT_SUBSTRING = "compact_substring"
R_ALL_PARTS = "all_parts"
R_INITIALS = "initials"
R_PART_FUZZY = "part_fuzzy"
R_WHOLE_FUZZY = "whole_fuzzy"

# The enabled set. `substring`, `compact_substring`, `all_parts` and
# `whole_fuzzy` reproduce what `cascade._entity_supported` already did (though
# `whole_fuzzy` now has multi-word ENTITY RECORDS to compare against, which is
# where the false-positive win comes from); `exact` and `initials` are new.
#
# `part_fuzzy` IS IMPLEMENTED AND DELIBERATELY NOT ENABLED -- see
# REJECTED_RULES for the numbers that took it out.
RULES = (R_EXACT, R_SUBSTRING, R_COMPACT_SUBSTRING, R_ALL_PARTS, R_INITIALS,
         R_WHOLE_FUZZY)

# The no-fuzzy configuration, for an operator whose corpus is NOT OCR damage.
# Measured on this (hostile) corpus: false positives 17.3% but a 1-character
# corruption of a real name is caught 100% of the time. On clean source the
# false-positive rate should collapse and this becomes the better default --
# untested there, so it is offered rather than chosen.
STRICT_RULES = (R_EXACT, R_SUBSTRING, R_COMPACT_SUBSTRING, R_ALL_PARTS,
                R_INITIALS)

# Rules that resolve on APPROXIMATE evidence. A term resolved by one of these
# is reported as such and counted separately, because it is the only class of
# match that could be hiding a fabrication: everything else is containment.
FUZZY_RULES = frozenset({R_PART_FUZZY, R_WHOLE_FUZZY})

# MEASURED AND REJECTED. Kept here with their numbers so the next person does
# not re-propose them as obvious improvements; all are the dangerous direction.
REJECTED_RULES = {
    "part_fuzzy": (
        "Resolve a multi-word name if EVERY token fuzzy-matches some token in "
        "the scope. Swept on 1,014 real claims and two mutation batteries: at "
        "part_fuzz 0.80 it removes 0.9 points of false positives (8.48% -> "
        "7.59%) and costs 5.4 points of catch on 1-character name corruptions "
        "(14.6% -> 9.2%). Trading catch for quiet is the direction this "
        "checker must never move in, so it is off. Raising part_fuzz to 0.93 "
        "makes it a no-op rather than making it safe."),
    "any_part_present": (
        "Resolve a multi-word name if ANY of its tokens occurs in the scope. "
        "Not swept, because it fails by construction: it resolves a fabricated "
        "surname onto a real given name -- 'Kedarnath Fenwick' passes because "
        "'Kedarnath' is present -- which is precisely the fabrication the "
        "entity signal exists to catch."),
    "lower whole_fuzz": (
        "Swept 0.75 / 0.80 / 0.85 / 0.90. The false-positive and catch curves "
        "move together and monotonically (0.75: 8.5%/14.6%, 0.85: "
        "12.9%/43.9%, 0.90: 15.9%/87.5%), so there is no knee to find -- it is "
        "one dial, not a tradeoff with a sweet spot. 0.75 is kept because it "
        "is the value the project already measured (cascade.ENTITY_FUZZ) and "
        "moving it is a separate decision from this one."),
}


def canon(text):
    """Fold for matching: lookalike hyphens/spaces ASCII-ised, diacritics
    stripped, case folded. `cascade.fold`, not a second implementation."""
    fold, _ = _prims()
    return fold(text or "")


def canon_tokens(name):
    """('K.P. Dutt') -> ('k', 'p', 'dutt').

    Hyphens and periods are token separators here, which is right for names
    (`Bhakti-Vinod` is `Bhakti Vinod`) and is NOT the number-scanner's bug: the
    non-breaking-hyphen defect that read `twenty-four` as 20 and 4 was about
    NUMERALS, where a hyphen joins rather than separates. `canon` normalises
    the non-breaking forms to ASCII first either way, so a lookalike hyphen and
    an ASCII one tokenise identically.
    """
    return tuple(t for t in _WORD.findall(canon(name)) if t)


def compact(name):
    """('Śrī Chaitanya') -> 'srichaitanya'. Space- and punctuation-insensitive."""
    return "".join(canon_tokens(name))


def _fuzzy(a, b, threshold):
    """SequenceMatcher ratio with a length band and an initial-letter gate.

    The length band is not an optimisation, it is part of the strictness: two
    strings of very different length that still score highly are scoring on a
    shared prefix, which is how a fabricated name resolves onto a real one.
    """
    if not a or not b:
        return 0.0
    if abs(len(a) - len(b)) > max(3, len(a) // 3):
        return 0.0
    if REQUIRE_INITIAL and a[:1] != b[:1]:
        return 0.0
    r = SequenceMatcher(None, a, b).ratio()
    return r if r >= threshold else 0.0


def _initials_align(short, long_):
    """Does `short` read as an initialised form of `long_`?

    ('k','p','dutt') against ('kedarnath','prasad','dutt') -> True.
    ('k','p','smith') against ('kedarnath','prasad','dutt') -> False.

    Right-anchored, because the surname is the part that carries the identity
    and the part a fabrication changes. THREE constraints, each of which is
    what stops this rule laundering a fabricated name:

      * the last token of `short` may not itself be an initial -- a bare
        "K.P." resolves to nothing;
      * the anchor must match in full (SURNAME_FUZZ, near-exact, so OCR damage
        of one character survives but a different surname does not);
      * every remaining token must be either the same word or a single letter
        that begins the corresponding word, IN ORDER. No skipping, so
        "P. Dutt" does not silently absorb "Kedarnath".
    """
    if len(short) < 2 or len(short) > len(long_):
        return False
    if not any(len(t) == 1 for t in short):
        return False                        # not an initialised form at all
    if len(short[-1]) == 1:
        return False                        # a surname is never an initial
    qi, ri = len(short) - 1, len(long_) - 1
    anchored = False
    while qi >= 0:
        if ri < 0:
            return False
        q, r = short[qi], long_[ri]
        if q == r or _fuzzy(q, r, SURNAME_FUZZ):
            if not anchored and qi == len(short) - 1:
                anchored = True
        elif len(q) == 1 and r.startswith(q):
            pass
        else:
            return False
        qi -= 1
        ri -= 1
    return anchored


class Scope:
    """One searchable region: a cited span, a chunk, or a whole document."""

    __slots__ = ("label", "folded", "compacted", "tokens", "records", "meta")

    def __init__(self, label, text, meta=None):
        fold, extract = _prims()
        self.label = label
        self.meta = meta or {}
        self.folded = fold(text or "")
        self.compacted = _NON_ALNUM.sub("", self.folded)
        self.tokens = set()
        for t in _WORD.findall(self.folded):
            if len(t) >= MIN_TOKEN:
                self.tokens.add(t)
        # Multi-token entity records. Single-token names are already covered by
        # `tokens`; storing them again as records would double the fuzzy work
        # for no extra reach.
        self.records = {}
        for surface, _cat in extract(text or ""):
            toks = canon_tokens(surface)
            if len(toks) < 2:
                continue
            key = "".join(toks)
            rec = self.records.get(key)
            if rec is None:
                self.records[key] = {"tokens": toks, "surfaces": [surface]}
            elif surface not in rec["surfaces"]:
                rec["surfaces"].append(surface)


class Resolution:
    """What resolving a name produced. `scopes` is never empty on a hit."""

    __slots__ = ("rule", "score", "scopes", "matched")

    def __init__(self, rule, scopes, matched=None, score=None):
        self.rule = rule
        self.scopes = scopes
        self.matched = matched
        self.score = score

    def as_dict(self):
        d = {"rule": self.rule, "scopes": list(self.scopes)}
        if self.matched:
            d["matched"] = self.matched
        if self.score is not None:
            d["score"] = round(self.score, 3)
        return d


class EntityIndex:
    """Canonical entities per scope, plus the resolution ladder over them.

    Scopes are ordered as added and resolution reports every scope that
    matched under the winning rule, so a consumer can tell "in the cited span"
    from "somewhere else in the document" WITHOUT the index having to know what
    those labels mean.
    """

    def __init__(self, rules=RULES, whole_fuzz=WHOLE_FUZZ,
                 part_fuzz=PART_FUZZ):
        self.scopes = []
        self.rules = tuple(rules)
        self.whole_fuzz = whole_fuzz
        self.part_fuzz = part_fuzz

    # -- building ----------------------------------------------------------
    def add(self, label, text, meta=None):
        self.scopes.append(Scope(label, text, meta))
        return self

    @classmethod
    def from_text(cls, text, label="scope", **kw):
        return cls(**kw).add(label, text)

    @classmethod
    def from_parts(cls, parts, **kw):
        """parts: iterable of (label, text)."""
        idx = cls(**kw)
        for label, text in parts:
            idx.add(label, text)
        return idx

    # -- resolving ---------------------------------------------------------
    def resolve(self, name):
        """Resolve `name` against every scope. Returns a Resolution or None.

        Rules are tried strictest-first ACROSS ALL SCOPES before the next rule
        is tried, so a name that is exactly present in one scope is never
        resolved by a fuzzy match in another. That ordering is what keeps the
        cheap answer cheap and the permissive answer last.
        """
        q_folded = canon(name)
        q_tokens = canon_tokens(name)
        q_compact = "".join(q_tokens)
        if len(q_compact) < MIN_TOKEN:
            # Too short to carry a match either way. Treated as resolved, which
            # is the lax direction and is deliberate: a two-character token is
            # not a distinctive term and flagging it would be noise.
            return Resolution("trivial", [s.label for s in self.scopes])
        parts = [t for t in q_tokens if len(t) >= MIN_TOKEN]

        for rule in self.rules:
            hits, best, matched = [], 0.0, None
            for sc in self.scopes:
                ok, score, m = self._rule_hit(rule, sc, q_folded, q_compact,
                                              q_tokens, parts)
                if ok:
                    hits.append(sc.label)
                    if score and score > best:
                        best, matched = score, m
                    elif matched is None:
                        matched = m
            if hits:
                return Resolution(rule, hits, matched, best or None)
        return None

    def _rule_hit(self, rule, sc, q_folded, q_compact, q_tokens, parts):
        if rule == R_EXACT:
            if q_compact in sc.records:
                return True, None, sc.records[q_compact]["surfaces"][0]
            if q_compact in sc.tokens:
                return True, None, q_compact
            return False, 0.0, None
        if rule == R_SUBSTRING:
            return (q_folded in sc.folded), None, None
        if rule == R_COMPACT_SUBSTRING:
            return (q_compact in sc.compacted), None, None
        if rule == R_ALL_PARTS:
            return (bool(parts) and all(p in sc.folded for p in parts)), None, None
        if rule == R_INITIALS:
            for key, rec in sc.records.items():
                if _initials_align(q_tokens, rec["tokens"]) \
                        or _initials_align(rec["tokens"], q_tokens):
                    return True, None, rec["surfaces"][0]
            return False, 0.0, None
        if rule == R_PART_FUZZY:
            if not parts:
                return False, 0.0, None
            worst, hit = 1.0, None
            for p in parts:
                best_p = 0.0
                for t in sc.tokens:
                    r = _fuzzy(p, t, self.part_fuzz)
                    if r > best_p:
                        best_p, hit = r, t
                if not best_p:
                    return False, 0.0, None
                worst = min(worst, best_p)
            return True, worst, hit
        if rule == R_WHOLE_FUZZY:
            best, hit = 0.0, None
            for key, rec in sc.records.items():
                r = _fuzzy(q_compact, key, self.whole_fuzz)
                if r > best:
                    best, hit = r, rec["surfaces"][0]
            for t in sc.tokens:
                r = _fuzzy(q_compact, t, self.whole_fuzz)
                if r > best:
                    best, hit = r, t
            return (best > 0.0), best, hit
        raise ValueError("unknown rule " + repr(rule))

    # -- introspection -----------------------------------------------------
    def stats(self):
        return {
            "scopes": len(self.scopes),
            "entities": sum(len(s.records) for s in self.scopes),
            "tokens": sum(len(s.tokens) for s in self.scopes),
            "rules": list(self.rules),
            "whole_fuzz": self.whole_fuzz,
            "part_fuzz": self.part_fuzz,
            "require_initial": REQUIRE_INITIAL,
        }

"""Faithfulness audit ledger: what in this summary is NOT in its source?

WHAT THIS IS
------------
A standalone auditing step for map-reduce summarisation. It takes a document, its
per-chunk summaries (with the character offsets `db.chunk_summaries` already
stores) and the final summary, and emits a machine-readable ledger of the places
a human should look before trusting the output.

It is deliberately NOT wired into `worker.run_one` or the job flow. Two reasons:
the evidence base behind it is 36 constructed negation pairs and one real chunk
row, which is too thin to justify running it on every job; and keeping it
standalone means the tool is also the harness for its own experiment (see
`battery` below).

WHAT THIS IS NOT
----------------
**It flags for review. It never certifies a summary as faithful.** That is not
caution-by-default, it is a measured result. `docs/minicheck-spike.md` section 4
recorded MiniCheck-Flan-T5-Large INVERTING a retention-obligation pair -- scoring
the true claim 0.258 (unsupported) and the fabricated negation 0.748 (supported)
-- on exactly the clause type this project exists to process. A classifier that
can invert "must be retained for seven years" against "must not be retained
beyond seven years" may not be permitted to stamp a legal summary clean. So there
is no pass verdict anywhere in the output schema, only `review_required` and
`no_flags_raised`, and the second says nothing about faithfulness.

WHY A CLASSIFIER AND NOT AN LLM JUDGE
-------------------------------------
Re-invoking the summarisation model to check itself is rejected. LangChain
shipped precisely that (`LLMSummarizationCheckerChain`), documented that the
checker itself hallucinates, defaulted it to running twice to paper over it, and
then deprecated it. Prompted verifiers invent discrepancies that are not there
(arXiv:2310.01798). A discriminative classifier has a fixed, measurable error
rate; a prompted judge has a fabrication rate.

THE TWO HOPS
------------
Both are scoped to the evidence window arXiv:2511.07689 found more reliable --
metrics degrade badly at whole-document scope, and improve markedly when the
window is correctly scoped, *particularly for legal text*.

  hop 1  `chunk_vs_source`
         each sentence of a chunk summary against ITS OWN source span,
         `document[start_char:end_char]`.

  hop 2  `final_vs_chunk_summaries`
         each sentence of the final summary against the CONCATENATION OF CHUNK
         SUMMARIES -- what the reduce step actually read, not the raw document.
         This is the hop that catches reduce-step laundering: a fabrication
         invented during reduce is unsupported by its own inputs, whereas one
         inherited from a bad chunk summary is faithfully carried and will show
         up in hop 1 instead. Separating the two is the point (F25,
         `docs/DESIGN-NOTES.md` E).

LOCATIONS ARE COMPUTED, NEVER ASKED FOR
---------------------------------------
No model is asked for a line number. Models are poor at it -- 38% accuracy on the
easier citation-validation task, off-by-10-to-50-line errors. Every line number
here is derived by counting newlines up to a stored character offset. Where a
location cannot be derived directly -- a final-summary sentence has no source
offset of its own -- the schema says so: `location_confidence` is `"indirect"`
and the match method and score are recorded alongside it.

REFUSE, DO NOT DEGRADE
----------------------
See `MiniCheckScorer.preflight`. MiniCheck silently truncates over-long input and
its own `used_chunks` diagnostic misreports what the model actually saw, so a
caller trusting that diagnostic would believe nothing was lost. This module
measures tokenized length itself, with the model's own tokenizer, and REFUSES to
score an over-long unit -- recording it as `unscoreable` with a reason. A
silently truncated claim scored as "supported" is the failure this codebase has
hit four times (F21, F34, F38, F39).

DEPENDENCIES
------------
`torch`, `transformers` and `minicheck` are imported LAZILY, inside
`MiniCheckScorer.load()`. Nothing in this module's import path pulls them in, so
`missing-link/requirements.txt` stays free of a multi-gigabyte model stack and
the production install is unaffected. See `missing-link/requirements-audit.txt`.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

# The sentence splitter lives in ONE module now (F48). `sentence_spans` and
# `splitter_name` are re-exported here unchanged, because `audit.sentence_spans`
# is the name the ledger, the cascade and three test modules already call.
from missing_link.sentences import (  # noqa: F401  (re-exported on purpose)
    _SENT_FALLBACK,
    sentence_spans,
    splitter_name,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

LEDGER_SCHEMA_VERSION = 1

# Finding categories. A CONSUMER MUST TOLERATE UNKNOWN CATEGORIES: iterate
# `totals.by_category` rather than reading a fixed set of keys, and ignore a
# `category` value it does not recognise rather than failing. That is what makes
# a new category (say a numeric-consistency check) an additive change.
CAT_UNSCOREABLE = "unscoreable"
CAT_DISAGREEMENT = "model_disagreement"
CAT_UNSUPPORTED = "unsupported"
CAT_POLARITY = "polarity_mismatch"

# Sort order for findings, lowest first. Ordering is part of the contract and is
# argued, not arbitrary:
#   0 unscoreable       -- the reader must first know what was never checked at all
#   1 model_disagreement-- where the spike's dangerous case lived; ranked ABOVE
#                          agreed-unsupported deliberately, because a single-model
#                          gate is exactly what would have passed it silently
#   2 unsupported       -- both models flag it
#   3 polarity_mismatch -- cheap deterministic signal, noisiest, lowest priority
CATEGORY_PRIORITY = {
    CAT_UNSCOREABLE: 0,
    CAT_DISAGREEMENT: 1,
    CAT_UNSUPPORTED: 2,
    CAT_POLARITY: 3,
}

HOP_CHUNK = "chunk_vs_source"
HOP_FINAL = "final_vs_chunk_summaries"

# A MiniCheck label is `prob > 0.5`. Kept as a named constant rather than a bare
# 0.5 so the ledger can record what it used.
SUPPORT_THRESHOLD = 0.5

# Two models "disagree" if their labels differ, or if their support
# probabilities are this far apart even when the labels happen to match.
#
# HONEST NOTE ON THIS CONSTANT: on the 36-pair battery the gap rule never fired
# on its own. All five escalations came from LABEL DIVERGENCE, and no pair had
# matching labels with a gap this wide. So 0.35 is an unexercised belt-and-braces
# rule, not a measured threshold, and it must not be quoted as one. It is kept
# because a pair at 0.95/0.55 agrees on the label while plainly not agreeing, and
# on the battery it cost nothing: zero extra escalations.
DISAGREEMENT_GAP = 0.35

DISCLAIMER = (
    "This ledger FLAGS FOR REVIEW. It never certifies a summary as faithful. "
    "An empty findings list means no configured check fired, not that the "
    "summary is accurate: the underlying classifiers are measured to invert "
    "obligation-clause polarity on this project's own material "
    "(docs/minicheck-spike.md section 4, docs/audit-ledger.md)."
)


# ---------------------------------------------------------------------------
# Locations: computed from offsets, never reported by a model
# ---------------------------------------------------------------------------

def line_of(text, offset):
    """1-based line number of `offset` within `text`, by counting newlines.

    Clamped to the text, so a stale or out-of-range offset yields a line number
    at the boundary rather than raising -- the ledger is a review aid and a bad
    offset must not take the whole audit down. `offset` is a character offset
    into the ORIGINAL text, which is why `worker.chunk_spans` slices from the
    original string rather than rejoining split words.
    """
    if offset <= 0:
        return 1
    offset = min(offset, len(text))
    return text.count("\n", 0, offset) + 1


# `sentence_spans`, `splitter_name` and `_SENT_FALLBACK` used to be defined
# HERE, and a byte-identical `_SENT_FALLBACK` was defined a second time in
# `chunk_boundary_audit.py` with a contradicting docstring. F48 found the pair;
# both now come from `missing_link.sentences`, imported at the top of this
# module. The ladder there is nupunkt -> regex; the nltk rung this module used
# to prefer is gone, because its only rationale was lining up with MiniCheck's
# own tokenizer and F41 took the classifier tier off the table. What ran is
# still reported under `config.sentence_splitter` in every ledger, and it is
# now MORE load-bearing, not less: nupunkt and the regex disagree by 4x on
# legislative marker rate, so a number without its splitter name is unreadable.


# ---------------------------------------------------------------------------
# The cheap deterministic complement: negation-cue polarity
# ---------------------------------------------------------------------------

# A large share of the failures this tool exists to catch are polarity errors,
# and a polarity error is visible without a model. This check is trivially cheap
# and has no model failure mode -- so it was built and then MEASURED against the
# 36-pair battery, and it DID NOT EARN A PLACE AS A DEFAULT FLAG:
#
#   fires on 26 of 72 battery claims (36%)
#   catches 1 of the 5 claims the model ensemble got wrong
#   25 of its 26 firings are on claims cross-model disagreement did NOT flag,
#     and none of those 25 is an error
#   on true claims specifically it false-positives at 11% (4 of 36)
#
# A flag that fires on a third of every summary while adding no error detection
# the ensemble does not already provide is worse than no flag: it teaches the
# reader to skim past the flags that matter. So `polarity=False` is the default.
# It is kept, not deleted, for two reasons: it is the only check that caught the
# flagship retention inversion WITHOUT a model, and it costs nothing to run when
# a reader specifically wants a polarity sweep. See docs/audit-ledger.md.
NEGATION_CUES = [
    r"\bnot\b", r"n't\b", r"\bno\b", r"\bnone\b", r"\bnever\b", r"\bcannot\b",
    r"\bwithout\b", r"\bunless\b", r"\bexcept\b", r"\bexcluded?\b",
    r"\bexempt\w*\b", r"\bprohibit\w*\b", r"\bforbid\w*\b", r"\bforbidden\b",
    r"\bdenied\b", r"\brefus\w+\b", r"\bneither\b", r"\bnor\b",
    r"\bfail(?:s|ed|ing)? to\b", r"\bother than\b",
]
# Cues considered and held back: "beyond" and "only". Both appear constantly in
# non-negated legal prose ("funding beyond June 2027", "only two of seven
# sites"), and the battery measured what including them costs. See
# docs/audit-ledger.md.
WIDE_NEGATION_CUES = NEGATION_CUES + [r"\bbeyond\b", r"\bonly\b"]

_CUE_RE_CACHE = {}


def negation_cues(text, cues=None):
    """Sorted list of negation markers present in `text`."""
    cues = cues or NEGATION_CUES
    key = id(cues)
    rx = _CUE_RE_CACHE.get(key)
    if rx is None:
        rx = [(c, re.compile(c, re.IGNORECASE)) for c in cues]
        _CUE_RE_CACHE[key] = rx
    found = set()
    for pattern, compiled in rx:
        m = compiled.search(text)
        if m:
            found.add(m.group(0).lower())
    return sorted(found)


def is_negated(text, cues=None):
    """Presence-parity, not count-parity.

    Counting cues and taking parity looks more principled and is worse: "must
    not be retained beyond seven years" carries two cues and would come out
    "positive". Presence is what actually separates the pairs that matter.
    """
    return bool(negation_cues(text, cues))


_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_STOP = frozenset("""a an the of to in on at for and or is are was were be been being by
with as that this these those it its from which who whom whose has have had not no""".split())


def content_words(text):
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOP]


def overlap_score(claim, candidate):
    """Fraction of the claim's content words that appear in `candidate`.

    Containment, not Jaccard: a claim sentence is short and its evidence is
    long, so Jaccard would punish the correct match for being verbose.
    """
    cw = set(content_words(claim))
    if not cw:
        return 0.0
    return len(cw & set(content_words(candidate))) / len(cw)


def best_match(claim, candidates):
    """(index, score) of the candidate with the highest content-word overlap."""
    best_i, best_s = -1, 0.0
    for i, cand in enumerate(candidates):
        s = overlap_score(claim, cand)
        if s > best_s:
            best_i, best_s = i, s
    return best_i, best_s


def polarity_check(claim, evidence, cues=None):
    """Compare the claim's polarity with that of its best-matching evidence sentence.

    Returns a dict, or None when no evidence sentence matches well enough to
    make the comparison meaningful. Comparing a claim against a whole 4000-token
    chunk is useless -- the chunk contains negated and non-negated sentences
    both -- so the comparison is against the single best-matching sentence.
    """
    sents = [s for _, _, s in sentence_spans(evidence)]
    if not sents:
        return None
    idx, score = best_match(claim, sents)
    if idx < 0 or score < 0.34:
        return None
    ev = sents[idx]
    claim_cues = negation_cues(claim, cues)
    ev_cues = negation_cues(ev, cues)
    return {
        "mismatch": bool(claim_cues) != bool(ev_cues),
        "claim_cues": claim_cues,
        "evidence_cues": ev_cues,
        "evidence_sentence": ev,
        "match_score": round(score, 3),
    }


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

class Preflight:
    """Verdict on whether a (evidence, claim) pair CAN be scored honestly."""

    __slots__ = ("ok", "reason", "max_unit_tokens", "limit", "n_units")

    def __init__(self, ok, reason=None, max_unit_tokens=None, limit=None, n_units=None):
        self.ok = ok
        self.reason = reason
        self.max_unit_tokens = max_unit_tokens
        self.limit = limit
        self.n_units = n_units

    def as_dict(self):
        return {"ok": self.ok, "reason": self.reason,
                "max_unit_tokens": self.max_unit_tokens, "limit": self.limit,
                "units": self.n_units}


class MiniCheckScorer:
    """One MiniCheck model, with a truncation guard the library does not provide.

    `minicheck`, `torch` and `transformers` are imported inside `load()`, not at
    module import, so importing `missing_link.audit` costs nothing.
    """

    def __init__(self, model_name, cache_dir=None, batch_size=16, max_model_len=None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.max_model_len = max_model_len
        self._mc = None

    @property
    def name(self):
        return self.model_name

    def load(self):
        if self._mc is not None:
            return self._mc
        from minicheck.minicheck import MiniCheck  # lazy: see module docstring

        self._mc = MiniCheck(model_name=self.model_name, cache_dir=self.cache_dir,
                             batch_size=self.batch_size, max_model_len=self.max_model_len)
        return self._mc

    # -- internals mirrored from minicheck/inference.py -------------------
    #
    # Replicated rather than reused because the chunking generator is a closure
    # inside `Inferencer.inference_per_example` and cannot be called from
    # outside. `battery --verify-chunking` checks this replication against the
    # library's own `used_chunks` output on live data, so drift is detectable
    # rather than assumed away.

    def _inner(self):
        return self.load().model

    @property
    def limit(self):
        return self._inner().max_model_len

    @property
    def chunk_size(self):
        return 500 if self.model_name == "flan-t5-large" else 400

    def _sent_tokenize_with_newlines(self, text):
        from nltk.tokenize import sent_tokenize

        blocks = text.split("\n")
        out = []
        for block in blocks:
            out.extend(sent_tokenize(block))
            out.append("\n")
        return out[:-1]

    def evidence_units(self, evidence):
        """The document chunks MiniCheck will actually build for this evidence."""
        inner = self._inner()
        sents = self._sent_tokenize_with_newlines(evidence) or [""]
        n = self.chunk_size
        flan = self.model_name == "flan-t5-large"

        def size(s):
            if flan:
                return len(s.split())
            return len(inner.tokenizer(s, padding=False, add_special_tokens=False,
                                       max_length=inner.max_model_len,
                                       truncation=True)["input_ids"])

        units, cur, count = [], [], 0
        for s in sents:
            w = size(s)
            if count + w > n:
                units.append(" ".join(cur))
                cur, count = [s], w
            else:
                cur.append(s)
                count += w
        if cur:
            units.append(" ".join(cur))
        units = [u.replace(" \n ", "\n").strip() for u in units]
        return [u for u in units if u != ""]

    def unit_token_length(self, unit, claim):
        """Tokens in the exact string the model is fed for (unit, claim).

        This is the guard. `minicheck` tokenizes with `truncation=True` and
        never raises; when the tokenized length exceeds `max_model_len` the tail
        is cut, and because the claim is appended AFTER the document the claim
        is what gets cut -- possibly in its entirety. Worse, `used_chunks` is
        built from the PRE-truncation string, so the library's own diagnostic
        reports text the model never saw. Measuring here, with the model's own
        tokenizer, is the only way to know.
        """
        inner = self._inner()
        tok = inner.tokenizer
        text = tok.eos_token.join([unit, claim])
        if self.model_name == "flan-t5-large":
            text = "predict: " + text
        return len(tok(text, add_special_tokens=True)["input_ids"])

    def preflight(self, evidence, claim):
        try:
            units = self.evidence_units(evidence)
        except Exception as exc:  # a splitter failure must not be scored past
            return Preflight(False, f"could not split evidence: {exc!r}")
        if not units:
            return Preflight(False, "evidence contains no scoreable text")
        lengths = [self.unit_token_length(u, claim) for u in units]
        worst = max(lengths)
        limit = self.limit
        if worst > limit:
            return Preflight(
                False,
                f"one evidence unit plus the claim tokenizes to {worst} tokens against "
                f"{self.model_name}'s {limit}-token limit. minicheck would truncate it "
                f"silently and could drop the claim entirely, then score the remainder; "
                f"its own used_chunks diagnostic would still report the full text. "
                f"Refusing to score rather than reporting a number that means nothing.",
                worst, limit, len(units))
        return Preflight(True, None, worst, limit, len(units))

    def score(self, pairs):
        """[(evidence, claim)] -> [{'support_prob', 'label', 'n_units', 'used_chunks'}]"""
        if not pairs:
            return []
        mc = self.load()
        docs = [p[0] for p in pairs]
        claims = [p[1] for p in pairs]
        labels, probs, used_chunks, _per_chunk = mc.score(docs=docs, claims=claims)
        out = []
        for lbl, prob, chunks in zip(labels, probs, used_chunks):
            out.append({
                "support_prob": float(prob),
                "label": "supported" if float(prob) > SUPPORT_THRESHOLD else "unsupported",
                "raw_label": int(lbl),
                "n_units": len(chunks),
                "used_chunks": chunks,
            })
        return out


class StaticScorer:
    """Deterministic scorer for tests and for exercising the ledger without models.

    Not a mock of MiniCheck's behaviour -- it is a lookup table keyed by
    (evidence, claim). It exists so the ledger's structure, ordering, location
    arithmetic and refusal path can be tested without a 1.5 GB dependency stack.
    """

    def __init__(self, name, table, default=0.9, limit=2048, refuse=()):
        self.model_name = name
        self.table = table
        self.default = default
        self._limit = limit
        self.refuse = set(refuse)

    @property
    def name(self):
        return self.model_name

    @property
    def limit(self):
        return self._limit

    def preflight(self, evidence, claim):
        if claim in self.refuse:
            return Preflight(False, "static scorer configured to refuse this claim",
                             self._limit + 1, self._limit, 1)
        return Preflight(True, None, 10, self._limit, 1)

    def score(self, pairs):
        out = []
        for evidence, claim in pairs:
            prob = float(self.table.get(claim, self.default))
            out.append({
                "support_prob": prob,
                "label": "supported" if prob > SUPPORT_THRESHOLD else "unsupported",
                "raw_label": 1 if prob > SUPPORT_THRESHOLD else 0,
                "n_units": 1,
                "used_chunks": [evidence],
            })
        return out


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def _claim_units(document, chunk_records, final_summary):
    """Every (hop, claim, evidence, location) the audit will examine.

    Returns a list of dicts. Building this separately from scoring keeps the
    expensive part (models) away from the part that has to be right (offsets).
    """
    units = []
    chunk_summaries = [r["summary"] for r in chunk_records]

    for rec in chunk_records:
        idx = rec["index"] if "index" in rec else rec["idx"]
        start, end = rec["start_char"], rec["end_char"]
        evidence = document[start:end]
        for s_i, (s_start, s_end, sent) in enumerate(sentence_spans(rec["summary"])):
            units.append({
                "hop": HOP_CHUNK,
                "claim_text": sent,
                "evidence_text": evidence,
                "claim": {
                    "unit": "chunk_summary",
                    "chunk_index": idx,
                    "sentence_index": s_i,
                    "start_char": s_start,
                    "end_char": s_end,
                    "line": line_of(rec["summary"], s_start),
                },
                "evidence": {
                    "unit": "source_document",
                    "chunk_index": idx,
                    "start_char": start,
                    "end_char": end,
                    "start_line": line_of(document, start),
                    "end_line": line_of(document, end),
                    "location_confidence": "direct",
                },
            })

    # Hop 2 only exists if there WAS a reduce step. `worker.summarise_traced`
    # returns `records[0]["summary"]` unchanged when a document is one chunk, so
    # on a single-chunk job the final summary IS the chunk summary and hop 2
    # would score text against itself and report a flawless pass. That is a
    # fabricated reassurance, which is worse than no hop at all.
    if final_summary and len(chunk_records) > 1:
        joined = "\n\n".join(chunk_summaries)
        for s_i, (s_start, s_end, sent) in enumerate(sentence_spans(final_summary)):
            m_i, m_score = best_match(sent, chunk_summaries)
            if m_i >= 0:
                rec = chunk_records[m_i]
                idx = rec["index"] if "index" in rec else rec["idx"]
                ev_loc = {
                    "unit": "chunk_summaries_concatenated",
                    "chunk_index": idx,
                    # The source span of the chunk whose SUMMARY best matches this
                    # sentence. It is where to start reading, not a located
                    # quotation -- hence location_confidence below.
                    "start_char": rec["start_char"],
                    "end_char": rec["end_char"],
                    "start_line": line_of(document, rec["start_char"]),
                    "end_line": line_of(document, rec["end_char"]),
                    "location_confidence": "indirect",
                    "location_note": (
                        "This final-summary sentence has no source offset of its own. "
                        "The span given is that of the chunk whose SUMMARY best matches "
                        "it by content-word overlap, so it names where to start reading, "
                        "not the sentence that produced the claim."),
                    "match_method": "content_word_containment",
                    "match_score": round(m_score, 3),
                }
            else:
                ev_loc = {
                    "unit": "chunk_summaries_concatenated",
                    "chunk_index": None,
                    "location_confidence": "none",
                    "location_note": (
                        "No chunk summary shares enough content words with this sentence "
                        "to locate it. That is itself worth a look."),
                    "match_method": "content_word_containment",
                    "match_score": 0.0,
                }
            units.append({
                "hop": HOP_FINAL,
                "claim_text": sent,
                "evidence_text": joined,
                "claim": {
                    "unit": "final_summary",
                    "chunk_index": None,
                    "sentence_index": s_i,
                    "start_char": s_start,
                    "end_char": s_end,
                    "line": line_of(final_summary, s_start),
                },
                "evidence": ev_loc,
            })
    return units


def _finding(unit, category, detail, scores, extra=None):
    claim = unit["claim"]
    fid = "{}:{}:{}:{}".format(
        "h1" if unit["hop"] == HOP_CHUNK else "h2",
        claim["chunk_index"] if claim["chunk_index"] is not None else "final",
        claim["sentence_index"], category)
    out = {
        "id": fid,
        "category": category,
        "priority": CATEGORY_PRIORITY.get(category, 99),
        "hop": unit["hop"],
        "detail": detail,
        "claim": dict(claim, text=unit["claim_text"]),
        "evidence": unit["evidence"],
        "scores": scores,
    }
    if extra:
        out.update(extra)
    return out


def build_ledger(document, chunk_records, final_summary, scorers,
                 job_id=None, polarity=False, cues=None,
                 include_passing=False, progress=None):
    """Audit a summary against its sources and return the ledger dict.

    chunk_records: [{"index"|"idx", "start_char", "end_char", "summary"}] -- the
        shape `db.get_chunk_summaries` returns.
    scorers: two or more objects with .name/.limit/.preflight/.score. Two is the
        design, not a default: cross-model disagreement is a first-class output,
        and with one scorer there is nothing to disagree.
    polarity: the deterministic negation-cue check. OFF by default because the
        battery measured it firing on 36% of claims while catching nothing the
        two-model ensemble missed -- see NEGATION_CUES above and
        docs/audit-ledger.md section 2.
    """
    if len(scorers) < 2:
        raise ValueError(
            "build_ledger requires at least TWO scorers. A single-model gate is "
            "precisely what docs/minicheck-spike.md section 4 showed passing an "
            "inverted retention obligation; cross-model disagreement is the "
            "signal this tool is built around, not an internal detail.")

    units = _claim_units(document, chunk_records, final_summary)

    # Preflight everything first, per model. A unit any model refuses is scored
    # by none of them: a ledger row backed by one model where the schema
    # promises two is the kind of quiet degradation this project keeps getting
    # bitten by.
    refusals = {}
    for u_i, unit in enumerate(units):
        for sc in scorers:
            pf = sc.preflight(unit["evidence_text"], unit["claim_text"])
            if not pf.ok:
                refusals[u_i] = (sc.name, pf)
                break

    scoreable = [i for i in range(len(units)) if i not in refusals]
    per_model = {}
    for sc in scorers:
        pairs = [(units[i]["evidence_text"], units[i]["claim_text"]) for i in scoreable]
        if progress:
            progress(f"scoring {len(pairs)} claim/evidence pairs with {sc.name}")
        t0 = time.time()
        per_model[sc.name] = sc.score(pairs)
        if progress:
            progress(f"  {sc.name}: {time.time() - t0:.1f}s")

    findings = []
    passing = []

    for u_i, (model_name, pf) in sorted(refusals.items()):
        unit = units[u_i]
        findings.append(_finding(
            unit, CAT_UNSCOREABLE,
            f"NOT CHECKED. {model_name} refused: {pf.reason}",
            [], {"refused_by": model_name, "preflight": pf.as_dict()}))

    for pos, u_i in enumerate(scoreable):
        unit = units[u_i]
        scores = []
        for sc in scorers:
            r = per_model[sc.name][pos]
            scores.append({"model": sc.name,
                           "support_prob": round(r["support_prob"], 4),
                           "label": r["label"],
                           "evidence_units_seen": r["n_units"]})
        probs = [s["support_prob"] for s in scores]
        labels = {s["label"] for s in scores}
        gap = max(probs) - min(probs)
        agree = len(labels) == 1 and gap < DISAGREEMENT_GAP
        flagged = False

        if len(labels) > 1 or gap >= DISAGREEMENT_GAP:
            flagged = True
            findings.append(_finding(
                unit, CAT_DISAGREEMENT,
                "The two checkers do not agree about this sentence ("
                + ", ".join(f"{s['model']} {s['support_prob']:.3f} {s['label']}"
                            for s in scores)
                + f"; gap {gap:.3f}). Disagreement is where the measured failure "
                  "lives -- read this one first.",
                scores, {"models_agree": False, "prob_gap": round(gap, 4)}))

        if labels == {"unsupported"}:
            flagged = True
            findings.append(_finding(
                unit, CAT_UNSUPPORTED,
                "Both checkers score this sentence as unsupported by its evidence "
                "window. Check it against the source span named below.",
                scores, {"models_agree": True, "prob_gap": round(gap, 4)}))

        if polarity:
            pc = polarity_check(unit["claim_text"], unit["evidence_text"], cues)
            if pc and pc["mismatch"]:
                flagged = True
                findings.append(_finding(
                    unit, CAT_POLARITY,
                    "Negation cues differ between this sentence and its closest "
                    f"evidence sentence (claim: {pc['claim_cues'] or 'none'}; "
                    f"evidence: {pc['evidence_cues'] or 'none'}). This check is "
                    "deterministic and noisy -- it fires on ordinary paraphrase as "
                    "well as on real polarity flips.",
                    scores, {"polarity": pc, "models_agree": agree,
                             "prob_gap": round(gap, 4)}))

        if not flagged and include_passing:
            passing.append({"hop": unit["hop"],
                            "claim": dict(unit["claim"], text=unit["claim_text"]),
                            "scores": scores, "prob_gap": round(gap, 4)})

    findings.sort(key=lambda f: (f["priority"], f["hop"],
                                 f["claim"]["chunk_index"] if f["claim"]["chunk_index"]
                                 is not None else 1 << 30,
                                 f["claim"]["sentence_index"]))

    by_category = {}
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
    by_hop = {}
    for f in findings:
        by_hop[f["hop"]] = by_hop.get(f["hop"], 0) + 1

    flagged_claims = {(f["hop"], f["claim"]["chunk_index"], f["claim"]["sentence_index"])
                      for f in findings}

    notes = []
    if len(chunk_records) <= 1:
        notes.append(
            "Hop 2 (final_vs_chunk_summaries) was NOT run: this job has "
            f"{len(chunk_records)} chunk summary, so there was no reduce step and "
            "the final summary is the chunk summary verbatim. Scoring it against "
            "itself would report a flawless pass that means nothing.")
    elif not final_summary:
        notes.append("Hop 2 was NOT run: this job has no final summary recorded.")

    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job_id,
        "verdict": "review_required" if findings else "no_flags_raised",
        "disclaimer": DISCLAIMER,
        "models": [{"name": sc.name, "max_model_len": sc.limit} for sc in scorers],
        "config": {
            "support_threshold": SUPPORT_THRESHOLD,
            "disagreement_gap": DISAGREEMENT_GAP,
            "sentence_splitter": splitter_name(),
            "polarity_check": bool(polarity),
            "hops": [HOP_CHUNK, HOP_FINAL],
            "category_priority": CATEGORY_PRIORITY,
        },
        "totals": {
            "claims_examined": len(units),
            "claims_scoreable": len(scoreable),
            "claims_unscoreable": len(refusals),
            "claims_flagged": len(flagged_claims),
            "findings": len(findings),
            "by_category": by_category,
            "by_hop": by_hop,
        },
        "findings": findings,
        "notes": notes,
        "coverage_note": (
            "This ledger reports claims present in the SUMMARY that its evidence "
            "does not support. It deliberately does not report material present in "
            "the SOURCE but absent from the summary: omission is what summarising "
            "is, so that flag would fire on nearly every sentence of every document "
            "and would train the reader to ignore the ledger."),
    }
    if include_passing:
        ledger["passing"] = passing
    return ledger


# ---------------------------------------------------------------------------
# Reading a job (READ-ONLY)
# ---------------------------------------------------------------------------

def load_job(db_path, job_id):
    """(document, chunk_records, final_summary) from the jobs database.

    Opened READ-ONLY (`mode=ro`). The audit is an observer; it must never be
    able to write to, migrate, or lock a database a worker is using.
    """
    uri = "file:{}?mode=ro".format(os.path.abspath(db_path))
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        job = conn.execute("SELECT id, document, result FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        if job is None:
            raise KeyError(f"no job {job_id!r} in {db_path}")
        rows = conn.execute(
            "SELECT idx, start_char, end_char, summary FROM chunk_summaries "
            "WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
    finally:
        conn.close()
    records = [{"index": r["idx"], "start_char": r["start_char"],
                "end_char": r["end_char"], "summary": r["summary"]} for r in rows]
    return job["document"], records, job["result"]


# ---------------------------------------------------------------------------
# The negation battery
# ---------------------------------------------------------------------------

BATTERY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "negation_battery.json")


def load_battery(path=None):
    with open(path or BATTERY_PATH, encoding="utf-8") as fh:
        return json.load(fh)["pairs"]


def battery_polarity_results(pairs, cues=None):
    """Run the DETERMINISTIC negation-cue check over the battery.

    Needs no models, so it runs in the production venv and in CI. Ground truth
    is by construction: a `negated` claim SHOULD be flagged, a `supported` claim
    should NOT.
    """
    rows = []
    for p in pairs:
        for kind in ("supported", "negated"):
            claim = p[kind]
            pc = polarity_check(claim, p["doc"], cues)
            fired = bool(pc and pc["mismatch"])
            rows.append({"id": p["id"], "category": p["category"], "kind": kind,
                         "fired": fired, "correct": fired == (kind == "negated"),
                         "matched": pc is not None,
                         "claim_cues": (pc or {}).get("claim_cues", []),
                         "evidence_cues": (pc or {}).get("evidence_cues", [])})
    return rows


def summarise_polarity(rows):
    tp = sum(1 for r in rows if r["kind"] == "negated" and r["fired"])
    fn = sum(1 for r in rows if r["kind"] == "negated" and not r["fired"])
    fp = sum(1 for r in rows if r["kind"] == "supported" and r["fired"])
    tn = sum(1 for r in rows if r["kind"] == "supported" and not r["fired"])
    return {
        "true_positives": tp, "false_negatives": fn,
        "false_positives": fp, "true_negatives": tn,
        "recall_on_negations": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "false_positive_rate_on_true_claims": round(fp / (fp + tn), 4) if (fp + tn) else None,
        "accuracy": round((tp + tn) / len(rows), 4) if rows else None,
    }


def run_battery(scorers, pairs=None, verify_chunking=True, progress=print):
    """Score every battery pair with every scorer and report accuracy + disagreement.

    The load-bearing question this answers is NOT "how accurate is MiniCheck" --
    the spike already showed it is accurate enough to be tempting and wrong
    enough to be dangerous. It is: DOES CROSS-MODEL DISAGREEMENT PREDICT ERROR?
    The whole two-model design rests on the answer.
    """
    pairs = pairs or load_battery()
    claims = []
    for p in pairs:
        for kind in ("supported", "negated"):
            claims.append({"id": p["id"], "category": p["category"], "kind": kind,
                           "doc": p["doc"], "claim": p[kind],
                           "truth": "supported" if kind == "supported" else "unsupported"})

    results = {}
    chunking_mismatches = []
    for sc in scorers:
        progress(f"[battery] {sc.name}: preflight + score {len(claims)} claims")
        t0 = time.time()
        rows = []
        to_score, refused = [], {}
        for i, c in enumerate(claims):
            pf = sc.preflight(c["doc"], c["claim"])
            if pf.ok:
                to_score.append(i)
            else:
                refused[i] = pf
        scored = sc.score([(claims[i]["doc"], claims[i]["claim"]) for i in to_score])
        by_index = dict(zip(to_score, scored))
        for i, c in enumerate(claims):
            if i in refused:
                rows.append(dict(c, label=None, support_prob=None, correct=None,
                                 unscoreable=True, reason=refused[i].reason))
                continue
            r = by_index[i]
            if verify_chunking and hasattr(sc, "evidence_units"):
                mine = sc.evidence_units(c["doc"])
                theirs = r.get("used_chunks") or []
                if [m.strip() for m in mine] != [t.strip() for t in theirs]:
                    chunking_mismatches.append(
                        {"model": sc.name, "id": c["id"], "kind": c["kind"],
                         "mine": mine, "library": theirs})
            rows.append(dict(c, label=r["label"], support_prob=round(r["support_prob"], 4),
                             correct=(r["label"] == c["truth"]), unscoreable=False))
        results[sc.name] = rows
        progress(f"[battery] {sc.name}: {time.time() - t0:.1f}s")

    return analyse_battery(results, claims, chunking_mismatches)


def analyse_battery(results, claims, chunking_mismatches=()):
    names = list(results)
    per_model = {}
    for name, rows in results.items():
        usable = [r for r in rows if not r["unscoreable"]]
        correct = [r for r in usable if r["correct"]]
        by_cat, by_kind = {}, {}
        for r in usable:
            for bucket, key in ((by_cat, r["category"]), (by_kind, r["kind"])):
                b = bucket.setdefault(key, {"n": 0, "correct": 0})
                b["n"] += 1
                b["correct"] += 1 if r["correct"] else 0
        per_model[name] = {
            "n": len(usable),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(usable), 4) if usable else None,
            "by_category": {k: dict(v, accuracy=round(v["correct"] / v["n"], 4))
                            for k, v in sorted(by_cat.items())},
            "by_claim_kind": {k: dict(v, accuracy=round(v["correct"] / v["n"], 4))
                              for k, v in sorted(by_kind.items())},
            "errors": [{"id": r["id"], "category": r["category"], "kind": r["kind"],
                        "support_prob": r["support_prob"], "got": r["label"],
                        "expected": r["truth"]}
                       for r in usable if not r["correct"]],
        }

    # The load-bearing cross-tabulation: does disagreement predict error?
    disagreement = {"disagree_and_at_least_one_wrong": 0, "disagree_and_both_right": 0,
                    "agree_and_at_least_one_wrong": 0, "agree_and_both_right": 0,
                    "disagree_pairs": [], "agreed_errors": []}
    if len(names) >= 2:
        a, b = names[0], names[1]
        for ra, rb in zip(results[a], results[b]):
            if ra["unscoreable"] or rb["unscoreable"]:
                continue
            gap = abs(ra["support_prob"] - rb["support_prob"])
            disagree = (ra["label"] != rb["label"]) or gap >= DISAGREEMENT_GAP
            any_wrong = (not ra["correct"]) or (not rb["correct"])
            key = ("disagree" if disagree else "agree") + \
                  ("_and_at_least_one_wrong" if any_wrong else "_and_both_right")
            disagreement[key] += 1
            if disagree:
                disagreement["disagree_pairs"].append(
                    {"id": ra["id"], "category": ra["category"], "kind": ra["kind"],
                     a: ra["support_prob"], b: rb["support_prob"],
                     "gap": round(gap, 4), "any_wrong": any_wrong})
            elif any_wrong:
                disagreement["agreed_errors"].append(
                    {"id": ra["id"], "category": ra["category"], "kind": ra["kind"],
                     a: ra["support_prob"], b: rb["support_prob"],
                     a + "_correct": ra["correct"], b + "_correct": rb["correct"]})

        d_wrong = disagreement["disagree_and_at_least_one_wrong"]
        d_total = d_wrong + disagreement["disagree_and_both_right"]
        a_wrong = disagreement["agree_and_at_least_one_wrong"]
        a_total = a_wrong + disagreement["agree_and_both_right"]
        errors_total = d_wrong + a_wrong
        disagreement["precision_of_disagreement_as_error_signal"] = (
            round(d_wrong / d_total, 4) if d_total else None)
        disagreement["recall_errors_caught_by_disagreement"] = (
            round(d_wrong / errors_total, 4) if errors_total else None)
        disagreement["errors_that_BOTH_models_made"] = a_wrong
        disagreement["escalation_rate"] = (
            round(d_total / (d_total + a_total), 4) if (d_total + a_total) else None)

        # An ensemble that escalates on disagreement and otherwise takes the
        # agreed label: how often would the agreed label have been wrong?
        ens_wrong = sum(1 for e in disagreement["agreed_errors"])
        disagreement["silent_failures_if_agreement_is_trusted"] = ens_wrong

    return {
        "n_pairs": len(claims) // 2,
        "n_claims": len(claims),
        "models": names,
        "per_model": per_model,
        "disagreement": disagreement,
        "chunking_replication_mismatches": list(chunking_mismatches),
        "raw": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_MODELS = ["flan-t5-large", "roberta-large"]


def _build_scorers(names, cache_dir):
    return [MiniCheckScorer(n, cache_dir=cache_dir) for n in names]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m missing_link.audit",
        description="Faithfulness audit ledger. Flags for review; never certifies.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="MiniCheck models. Two are required by design.")
    common.add_argument("--cache-dir", default=os.environ.get("MINICHECK_CACHE_DIR"),
                        help="Model checkpoint cache directory.")
    common.add_argument("--out", help="Write JSON here instead of stdout.")

    j = sub.add_parser("job", parents=[common],
                       help="Audit one job from the jobs database (read-only).")
    j.add_argument("--db", default="/opt/missing-link/jobs.sqlite")
    j.add_argument("--job-id", required=True)
    j.add_argument("--polarity", action="store_true",
                   help="Also run the deterministic negation-cue check. OFF by "
                        "default: measured to fire on 36%% of sentences while "
                        "catching nothing the model ensemble missed.")
    j.add_argument("--include-passing", action="store_true",
                   help="Also record every sentence that raised no flag.")

    b = sub.add_parser("battery", parents=[common],
                       help="Run the negation battery. This is the experiment.")
    b.add_argument("--fixture", default=None)
    b.add_argument("--polarity-only", action="store_true",
                   help="Run only the deterministic check (no models, no torch).")
    b.add_argument("--wide-cues", action="store_true",
                   help="Include the held-back 'beyond'/'only' cues.")

    args = ap.parse_args(argv)
    cues = None

    if args.cmd == "battery":
        pairs = load_battery(args.fixture)
        cues = WIDE_NEGATION_CUES if args.wide_cues else NEGATION_CUES
        rows = battery_polarity_results(pairs, cues)
        report = {"polarity_check": {"cue_set": "wide" if args.wide_cues else "default",
                                     "summary": summarise_polarity(rows),
                                     "rows": rows}}
        if not args.polarity_only:
            report["minicheck"] = run_battery(
                _build_scorers(args.models, args.cache_dir), pairs,
                progress=lambda m: print(m, file=sys.stderr))
    else:
        document, records, final = load_job(args.db, args.job_id)
        if not records:
            raise SystemExit(
                f"job {args.job_id} has no chunk_summaries rows. Without chunk "
                f"offsets there is no correctly-scoped evidence window to score "
                f"against, and auditing against the whole document is the thing "
                f"arXiv:2511.07689 measured as unreliable. Refusing.")
        report = build_ledger(
            document, records, final, _build_scorers(args.models, args.cache_dir),
            job_id=args.job_id, polarity=args.polarity,
            include_passing=args.include_passing,
            progress=lambda m: print(m, file=sys.stderr))

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

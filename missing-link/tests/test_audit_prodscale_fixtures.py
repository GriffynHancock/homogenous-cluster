"""Structural tests for the production-scale negation battery fixtures.

These fixtures (`negation_battery_prodscale.json`,
`negation_battery_position_effect.json`) exist to answer one question:
does cross-model disagreement still predict error when the evidence window is
a production-size (~4096-token) chunk rather than the original battery's
one-to-three-sentence documents? See `docs/audit-production-scale.md` for the
full write-up and results.

This file does NOT run MiniCheck (no torch in the production venv -- see
`test_audit.py::test_no_heavy_imports`). It checks the thing that has to be
right before any model result can be trusted: that ground truth is exactly
the ORIGINAL battery's ground truth, that the embedded clause is verbatim and
at the offset the fixture claims, and that the excluded retention memo did
not leak into filler for pairs it does not belong to. `python -m
missing_link.audit battery --fixture <path>` is what actually scores these;
this file guards its input.
"""

import json
import os

import pytest

from missing_link import audit

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "missing_link", "fixtures")
ORIGINAL = os.path.join(FIXTURES, "negation_battery.json")
PRODSCALE = os.path.join(FIXTURES, "negation_battery_prodscale.json")
POSITION = os.path.join(FIXTURES, "negation_battery_position_effect.json")

# Production's own chunk size (missing_link/worker.py CHUNK_TOKENS=4096,
# WORDS_PER_TOKEN=0.70) -> ~2867 target words. Fixture generation pads to at
# least this many words; allow a modest band either side for the last filler
# paragraph pushing a document over target and for the very short clauses.
TARGET_WORDS = int(4096 * 0.70)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def original_pairs():
    return {p["id"]: p for p in _load(ORIGINAL)["pairs"]}


@pytest.fixture(scope="module")
def prodscale_pairs():
    return _load(PRODSCALE)["pairs"]


@pytest.fixture(scope="module")
def position_pairs():
    return _load(POSITION)["pairs"]


# ---------------------------------------------------------------------------
# Both fixtures load via the same loader the battery CLI uses
# ---------------------------------------------------------------------------

def test_loads_via_audit_load_battery():
    assert len(audit.load_battery(PRODSCALE)) == 36
    assert len(audit.load_battery(POSITION)) == 27


# ---------------------------------------------------------------------------
# Ground truth is UNCHANGED from the original battery -- this is the whole
# point of the comparison being valid
# ---------------------------------------------------------------------------

def test_prodscale_same_36_ids_as_original(original_pairs, prodscale_pairs):
    assert {p["id"] for p in prodscale_pairs} == set(original_pairs)


def test_prodscale_ground_truth_matches_original(original_pairs, prodscale_pairs):
    for p in prodscale_pairs:
        orig = original_pairs[p["id"]]
        assert p["supported"] == orig["supported"], p["id"]
        assert p["negated"] == orig["negated"], p["id"]
        assert p["category"] == orig["category"], p["id"]


def test_position_pairs_cover_9_bases_at_3_positions(original_pairs, position_pairs):
    assert len(position_pairs) == 27
    bases = {p["_meta"]["short_doc_id"] for p in position_pairs}
    assert len(bases) == 9
    for base in bases:
        positions = {p["_meta"]["position"] for p in position_pairs
                     if p["_meta"]["short_doc_id"] == base}
        assert positions == {"begin", "middle", "end"}, base


def test_position_pairs_ground_truth_matches_original(original_pairs, position_pairs):
    for p in position_pairs:
        orig = original_pairs[p["_meta"]["short_doc_id"]]
        assert p["supported"] == orig["supported"], p["id"]
        assert p["negated"] == orig["negated"], p["id"]


# ---------------------------------------------------------------------------
# The embedded clause is verbatim and where the fixture says it is
# ---------------------------------------------------------------------------

def test_prodscale_clause_verbatim_at_recorded_offset(original_pairs, prodscale_pairs):
    for p in prodscale_pairs:
        cs, ce = p["_meta"]["clause_start"], p["_meta"]["clause_end"]
        assert p["doc"][cs:ce] == original_pairs[p["id"]]["doc"], p["id"]


def test_position_clause_verbatim_at_recorded_offset(original_pairs, position_pairs):
    for p in position_pairs:
        cs, ce = p["_meta"]["clause_start"], p["_meta"]["clause_end"]
        base = p["_meta"]["short_doc_id"]
        assert p["doc"][cs:ce] == original_pairs[base]["doc"], p["id"]


# ---------------------------------------------------------------------------
# Position actually is where the metadata claims (begin/middle/end thirds)
# ---------------------------------------------------------------------------

def test_position_label_matches_actual_offset_fraction(prodscale_pairs, position_pairs):
    for p in prodscale_pairs + position_pairs:
        frac = p["_meta"]["clause_start"] / len(p["doc"])
        pos = p["_meta"]["position"]
        if pos == "begin":
            assert frac < 0.25, (p["id"], frac)
        elif pos == "end":
            assert frac > 0.75, (p["id"], frac)
        else:
            assert 0.25 <= frac <= 0.75, (p["id"], frac)


# ---------------------------------------------------------------------------
# Documents are actually production-scale, not short-battery scale
# ---------------------------------------------------------------------------

def test_prodscale_documents_are_near_target_chunk_size(prodscale_pairs):
    for p in prodscale_pairs:
        words = len(p["doc"].split())
        # generous band: generation over-shoots target by at most one filler
        # paragraph (~150 words) and never undershoots it
        assert TARGET_WORDS <= words <= TARGET_WORDS + 400, (p["id"], words)


def test_position_documents_are_near_target_chunk_size(position_pairs):
    for p in position_pairs:
        words = len(p["doc"].split())
        assert TARGET_WORDS <= words <= TARGET_WORDS + 400, (p["id"], words)


# ---------------------------------------------------------------------------
# The excluded retention memo (job 2b4c926a799a) must not have leaked into
# filler for any pair other than the two it would trivially corroborate
# ---------------------------------------------------------------------------

def test_excluded_memo_did_not_leak_into_unrelated_filler(prodscale_pairs, position_pairs):
    # "41 staff who had" is the memo's own phrasing (and access_41_staff's
    # clause phrasing, which is legitimately present in that one pair only).
    fingerprint = "41 staff who had"
    for p in prodscale_pairs:
        if fingerprint in p["doc"]:
            assert p["id"] == "access_41_staff", p["id"]
    for p in position_pairs:
        if fingerprint in p["doc"]:
            assert p["_meta"]["short_doc_id"] == "access_41_staff", p["id"]


# ---------------------------------------------------------------------------
# Every document is otherwise well-formed input for MiniCheckScorer.preflight
# ---------------------------------------------------------------------------

def test_documents_are_nonempty_scoreable_text(prodscale_pairs, position_pairs):
    for p in prodscale_pairs + position_pairs:
        assert audit.sentence_spans(p["doc"]), p["id"]

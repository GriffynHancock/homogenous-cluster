"""Tests for the faithfulness cascade.

WHAT THESE DO AND DO NOT SHOW. Per F34/F38 these are synthetic fixtures and
therefore test the half we wrote. They pin the NORMALISATION rules -- which are
the part that decides whether the checker is useful or a false-positive machine
-- and the routing contract. They are not evidence about real model output;
that lives in the corpus runs reported in docs/faithfulness-cascade.md.
"""

import re

import pytest

from missing_link import cascade
from missing_link.audit import StaticScorer


# ---------------------------------------------------------------------------
# Number normalisation. This is the load-bearing part of the hard tier.
# ---------------------------------------------------------------------------

def values(text):
    return {str(n.value) for n in cascade.extract_numbers(text)}


@pytest.mark.parametrize("claim,evidence", [
    # words vs digits
    ("Records are kept for seven years.", "must be retained for 7 years"),
    ("There are 41 staff.", "forty-one staff are employed"),
    ("forty-one staff", "41 staff"),
    # compound / hyphenated forms
    ("a seven-year retention period", "retained for 7 years"),
    ("kept for 7 years", "a seven-year period"),
    ("twenty-five years old", "until the client turns 25 years"),
    # money
    ("The fee was $1.2m.", "a payment of 1,200,000 was made"),
    ("The fee was 1,200,000.", "a payment of $1.2m was made"),
    ("It cost $1.2 million.", "1200000 in total"),
    # percentages
    ("3.5% of files", "3.5 per cent of files"),
    # dates
    ("issued in March 2026", "issued on 12/03/2026"),
    ("issued on 3 February 2026", "issued 2026-02-03"),
    # ordinals
    ("the seventh year", "in year 7"),
    ("the 1st schedule", "schedule one"),
    # large written numbers
    ("one hundred and twelve staff", "112 staff"),
])
def test_equivalent_forms_match(claim, evidence):
    r = cascade.check_numbers(claim, evidence)
    assert r["status"] == "pass", (r["unmatched"], r["derived_or_uncountable"])


def test_seven_and_eight_is_not_fifteen():
    """A permissive parser reads "seven and eight" as 15, which occurs nowhere
    in the source and would HARD FAIL a faithful sentence. The strict parser
    must recover 7 and 8 separately instead."""
    assert values("seven and eight apples") == {"7", "8"}
    assert "15" not in values("seven and eight apples")


def test_one_hundred_and_twelve_is_still_a_compound():
    assert values("one hundred and twelve") == {"112"}


def test_bare_magnitude_words_are_not_quantities():
    assert values("millions of records") == set()
    assert values("hundreds of pages") == set()


def test_range_endpoints_are_both_extracted():
    assert values("between 7 and 10 years") == {"7", "10"}


# ---------------------------------------------------------------------------
# The hard fail: a fabricated figure
# ---------------------------------------------------------------------------

def test_fabricated_figure_is_a_hard_fail_and_is_not_escalated():
    ev = "Clinical records must be retained for seven years from last service."
    claim = "Clinical records must be retained for nine years from last service."
    decision, signals = cascade.route(claim, ev)
    assert decision == "hard_fail_number"
    numbers = signals[0]
    assert numbers["kind"] == cascade.KIND_HARD
    assert numbers["status"] == "fail"
    assert [u["claim"]["value"] for u in numbers["unmatched"]] == ["9"]


def test_hard_fail_detail_names_the_offending_figure():
    d = cascade.check_numbers("kept for 9 years", "kept for seven years")["detail"]
    assert "9" in d and "HARD FAIL" in d


def test_measurement_units_are_strict_even_when_small():
    """A retention period is the project's flagship figure. It must not be
    treated as a possibly-derived count merely because 7 is a small number."""
    r = cascade.check_numbers("retained for 7 years", "retained for a long time")
    assert r["status"] == "fail"


def test_a_correct_year_passes_but_a_wrong_one_fails():
    ev = "The breach was corrected in March 2026."
    assert cascade.check_numbers("corrected in March 2026", ev)["status"] == "pass"
    assert cascade.check_numbers("corrected in March 2025", ev)["status"] == "fail"


# ---------------------------------------------------------------------------
# Derived numbers: the case that must NOT hard fail
# ---------------------------------------------------------------------------

DERIVED_EV = ("The audit identified deficiencies: (1) incomplete indexing, "
              "(2) an unlocked store room, (3) a permission breach.")


def test_derived_count_is_resolved_by_counting_enumerated_items():
    r = cascade.check_numbers("The audit found three deficiencies.", DERIVED_EV)
    assert r["status"] == "pass"
    assert r["evidence_enumeration_count"] == 3


def test_unresolved_small_count_escalates_rather_than_failing():
    """Biased LAX here on purpose: a small count with no enumeration to back it
    may still be faithful, so it is handed to the next tier, never failed."""
    r = cascade.check_numbers("There were three findings.", "Several findings.")
    assert r["status"] == "warn"
    assert not r["unmatched"]
    decision, _ = cascade.route("There were three findings.", "Several findings.")
    assert decision == "escalate"


def test_a_large_count_with_no_counterpart_still_fails():
    r = cascade.check_numbers("There were 47 findings.", "Several findings.")
    assert r["status"] == "fail"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def test_absent_proper_noun_is_flagged():
    r = cascade.check_entities("Riverside Health was audited.",
                               "Brookside Clinic was audited in March.")
    assert r["status"] == "fail"
    assert any("Riverside" in m["term"] for m in r["missing"])


def test_diacritics_are_folded_before_matching():
    """The model normalises diacritics that the source carries. Matching must
    fold them or every accented name in the document becomes a false alarm."""
    r = cascade.check_entities("Sri Chaitanya Saraswat Math published it.",
                               "published by Śrī Chaitanya Sāraswat Math")
    assert r["status"] == "pass", r["missing"]


def test_mojibake_beyond_folding_is_a_known_false_positive():
    """DOCUMENTING A LIMIT, not asserting desired behaviour.

    The bench corpus is mis-decoded rather than merely accented: `Śrī` arrives
    as `ßrÈ`, which no amount of Unicode folding recovers. The entity tier
    therefore FLAGS such a term, and that flag is a FALSE POSITIVE. It is
    pinned here so the behaviour is visible and measured rather than
    discovered later; the measured rate on the real corpus is reported in
    docs/faithfulness-cascade.md, and it is the main reason the entity signal
    is weaker than the number signal."""
    r = cascade.check_entities("Sri Chaitanya published it.",
                               "published by ßrÈ Chaitanya")
    assert r["status"] == "fail"
    assert any(m["term"] == "Sri" for m in r["missing"])


def test_acronyms_and_identifiers_are_checked():
    assert cascade.check_entities("Invoice INV-4471 was paid.",
                                  "invoice INV-4471 paid in full")["status"] == "pass"
    assert cascade.check_entities("Invoice INV-9999 was paid.",
                                  "invoice INV-4471 paid in full")["status"] == "fail"


def test_sentence_initial_capital_is_not_treated_as_a_name():
    r = cascade.check_entities("Records must be kept.", "all files must be kept")
    assert r["status"] == "pass", r["missing"]


# ---------------------------------------------------------------------------
# Soft signals route, they never decide
# ---------------------------------------------------------------------------

def test_soft_signals_are_labelled_soft_and_carry_no_fail_status():
    sigs = cascade.soft_signals("Something entirely different.",
                                "The policy was revised in March.")
    assert sigs
    for s in sigs:
        assert s["kind"] == cascade.KIND_SOFT
        assert s["status"] in ("pass", "warn")   # never "fail"


def test_low_overlap_escalates_but_does_not_fail():
    decision, signals = cascade.route("Entirely unrelated prose about boats.",
                                      "The retention policy was revised.")
    assert decision == "escalate"
    assert all(s["status"] != "fail" for s in signals if s["kind"] == cascade.KIND_SOFT)


def test_matching_numbers_and_words_pass_without_escalation():
    """The operator's rule: if the numbers and words match, leave it."""
    ev = ("Clinical records must be retained for seven years from the date of "
          "last service. The permission breach was corrected in March 2026.")
    decision, _ = cascade.route(
        "Clinical records must be retained for seven years from the date of "
        "last service.", ev)
    assert decision == "pass"


def test_every_signal_carries_a_kind():
    _, signals = cascade.route("Records kept for 7 years.", "kept for seven years")
    assert signals
    for s in signals:
        assert s["kind"] in (cascade.KIND_HARD, cascade.KIND_SOFT)
        assert "name" in s


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

# Evidence in these ledger tests is a realistic paragraph, not a fragment. A
# two-word "evidence" string drives lexical overlap below the routing threshold
# and every claim escalates, which would make the routing assertions below test
# nothing.
EV = ("Clinical records are kept for 7 years from the date of last service. "
      "The register of destroyed records is retained indefinitely by the "
      "records officer.")


def unit(claim, evidence, idx=0, s_i=0):
    return {"hop": cascade.HOP_CHUNK, "claim_text": claim,
            "evidence_text": evidence,
            "claim": {"unit": "chunk_summary", "chunk_index": idx,
                      "sentence_index": s_i, "start_char": 0,
                      "end_char": len(claim), "line": 1},
            "evidence": {"unit": "source_document", "chunk_index": idx,
                         "start_char": 0, "end_char": len(evidence),
                         "location_confidence": "direct"}}


def test_ledger_reports_escalation_rate_and_cost_saving():
    units = [
        unit("Records are kept for seven years.", EV, 0, 0),
        unit("Unrelated prose about boats entirely.", EV, 0, 1),
    ]
    led = cascade.build_cascade_ledger(units)
    c = led["cascade"]
    assert c["claims_checkable"] == 2
    assert c["passed_cheaply"] == 1
    assert c["escalated"] == 1
    assert c["escalation_rate"] == 0.5
    assert c["cost_model"]["saving_fraction"] == 0.5


def test_hard_failures_never_reach_the_classifier():
    units = [unit("Kept for 9 years.", "kept for seven years")]
    scorers = [StaticScorer("a", {}), StaticScorer("b", {})]
    led = cascade.build_cascade_ledger(units, classifier=scorers)
    assert led["cascade"]["hard_fail_number"] == 1
    assert led["cascade"]["escalated"] == 0
    assert led["findings"][0]["category"] == cascade.CAT_NUMBER
    assert led["findings"][0]["tier"] == cascade.TIER_HARD
    assert led["findings"][0]["scores"] == []


def test_without_a_classifier_ambiguous_claims_are_needs_classifier_not_passing():
    """Refuse, do not degrade: an unresolved claim is reported as unchecked."""
    units = [unit("Entirely unrelated prose about boats.", "retention policy")]
    led = cascade.build_cascade_ledger(units)
    f = led["findings"][0]
    assert f["category"] == cascade.CAT_NEEDS_CLASSIFIER
    assert "HAS NOT BEEN CHECKED" in f["detail"]


def test_classifier_tier_requires_two_scorers():
    with pytest.raises(ValueError, match="TWO scorers"):
        cascade.build_cascade_ledger([], classifier=[StaticScorer("solo", {})])


def test_classifier_only_sees_escalated_claims():
    seen = []

    class Spy(StaticScorer):
        def score(self, pairs):
            seen.append(len(pairs))
            return super().score(pairs)

    units = [
        unit("Records are kept for seven years.", EV, 0, 0),
        unit("Unrelated prose about boats entirely.", EV, 0, 1),
    ]
    cascade.build_cascade_ledger(
        units, classifier=[Spy("a", {}), Spy("b", {})])
    assert seen == [1, 1]      # one escalated claim, not two, per model


def test_classifier_disagreement_is_still_a_first_class_category():
    units = [unit("Unrelated prose about boats entirely.", "retention policy")]
    led = cascade.build_cascade_ledger(units, classifier=[
        StaticScorer("a", {"Unrelated prose about boats entirely.": 0.95}),
        StaticScorer("b", {"Unrelated prose about boats entirely.": 0.05})])
    cats = {f["category"] for f in led["findings"]}
    assert cascade.CAT_DISAGREEMENT in cats


def test_by_category_is_a_map_and_priorities_are_published():
    units = [unit("Kept for 9 years.", "kept for seven years")]
    led = cascade.build_cascade_ledger(units)
    assert isinstance(led["totals"]["by_category"], dict)
    assert led["config"]["category_priority"][cascade.CAT_NUMBER] == 1
    assert led["config"]["classifier_enabled"] is False


def test_findings_are_ordered_unscoreable_then_hard_then_softer():
    units = [
        unit("Unrelated prose about boats entirely.", "retention policy", 0, 1),
        unit("Kept for 9 years.", "kept for seven years", 0, 0),
    ]
    led = cascade.build_cascade_ledger(units)
    prios = [f["priority"] for f in led["findings"]]
    assert prios == sorted(prios)
    assert led["findings"][0]["category"] == cascade.CAT_NUMBER


def test_unscoreable_unit_is_recorded_never_scored():
    u = unit("A claim.", "")
    u["unscoreable"] = "no citation on this paragraph"
    led = cascade.build_cascade_ledger([u])
    assert led["cascade"]["unscoreable"] == 1
    assert led["findings"][0]["category"] == cascade.CAT_UNSCOREABLE
    assert "NOT CHECKED" in led["findings"][0]["detail"]


def test_verdict_never_says_pass():
    units = [unit("Records are kept for seven years.", EV)]
    led = cascade.build_cascade_ledger(units)
    assert led["verdict"] == "no_flags_raised"
    assert "never certifies" in led["disclaimer"]


# ---------------------------------------------------------------------------
# Citation-driven units: consuming what worker.py actually produces
# ---------------------------------------------------------------------------

DOC = ("Clinical records must be retained for seven years from last service. "
       "The store room was left unlocked overnight on 2 March 2026.")
RECORDS = [
    {"index": 0, "start_char": 0, "end_char": 70, "summary": "retention"},
    {"index": 1, "start_char": 70, "end_char": len(DOC), "summary": "store room"},
]


def test_citation_units_resolve_a_marker_to_its_persisted_span():
    final = "Records are kept for seven years. [Section 1]"
    units, parsed = cascade.citation_units(DOC, RECORDS, final)
    assert parsed["valid_count"] == 1
    assert len(units) == 1
    u = units[0]
    assert u["evidence_text"] == DOC[0:70]
    assert u["evidence"]["cited_sections"] == [1]
    assert u["evidence"]["location_confidence"] == "direct"


def test_uncited_paragraph_is_unscoreable_not_guessed():
    units, _ = cascade.citation_units(DOC, RECORDS, "Something with no marker.")
    assert len(units) == 1
    assert units[0]["unscoreable"]
    assert "no [Section N] citation" in units[0]["unscoreable"]


def test_a_cited_paragraph_with_a_fabricated_figure_hard_fails():
    final = "Records are kept for nine years. [Section 1]"
    units, _ = cascade.citation_units(DOC, RECORDS, final)
    led = cascade.build_cascade_ledger(units)
    assert led["cascade"]["hard_fail_number"] == 1
    assert led["findings"][0]["category"] == cascade.CAT_NUMBER


def test_multiple_cited_sections_become_one_evidence_window():
    final = "Both matters. [Section 1][Section 2]"
    units, _ = cascade.citation_units(DOC, RECORDS, final)
    assert units[0]["evidence"]["cited_sections"] == [1, 2]
    assert "store room" in units[0]["evidence_text"]


def test_invented_marker_leaves_the_paragraph_uncited_not_mislocated():
    """worker.parse_section_citations drops an out-of-range marker. The cascade
    must then treat the paragraph as unchecked, never attach it to some other
    section's span."""
    units, parsed = cascade.citation_units(DOC, RECORDS,
                                           "A claim. [Section 47]")
    assert parsed["dropped_count"] == 1
    assert units[0]["unscoreable"]


# ---------------------------------------------------------------------------
# Splitter hardening -- every case below was observed on REAL model output
# ---------------------------------------------------------------------------

def test_a_verse_reference_is_not_split_into_orphan_numbers():
    """"Bhagavad-gita 18.55" became three sentences, each carrying an orphaned
    number, and every one hard failed against a source containing it intact."""
    text = "Scriptures are quoted: Bhagavad-gita 18.55 and the Bhagavatam 11.34."
    assert len(cascade.sentence_units(text)) == 1


def test_initials_do_not_end_a_sentence():
    """F41 recorded nltk splitting "K.P. Dutt" and emitting a fragment "P."."""
    units = cascade.sentence_units("Compiled by K.P. Dutt in 1896.")
    assert len(units) == 1
    assert "Dutt" in units[0][2]


def test_abbreviations_do_not_end_a_sentence():
    assert len(cascade.sentence_units("Owned by Acme Pty Ltd. since 2019.")) == 1


def test_punctuation_debris_is_dropped_not_scored():
    got = [s for _, _, s in cascade.sentence_units("Real sentence here. ---")]
    assert all(re.search(r"[0-9A-Za-z]", s) for s in got)


# ---------------------------------------------------------------------------
# Clause decomposition (F41: 55.6% of real sentences carry >1 claim)
# ---------------------------------------------------------------------------

def test_semicolons_split_into_separate_claims():
    text = "Records are kept for 7 years; mailboxes are archived for 3 years."
    units = cascade.claim_spans(text)
    assert len(units) == 2
    assert "7 years" in units[0][2] and "3 years" in units[1][2]


def test_clause_offsets_are_exact():
    text = ("The audit found deficiencies: (1) incomplete indexing of files, "
            "(2) an unlocked store room, and (3) a permission breach.")
    for s, e, frag, _s_i, _c_i in cascade.claim_spans(text):
        assert text[s:e] == frag


def test_a_short_coordination_is_not_split():
    """"apples, and pears" is one claim; splitting it would attribute a figure
    to the wrong half."""
    units = cascade.claim_spans("She bought apples, and pears.")
    assert len(units) == 1


def test_decomposition_attributes_a_figure_to_its_own_clause():
    ev = "Records are kept for 7 years. Mailboxes are archived for 3 years."
    claim = "Records are kept for 7 years; mailboxes are archived for 9 years."
    units = [unit(t, ev, 0, s_i) for _s, _e, t, s_i, _c in
             cascade.claim_spans(claim)]
    led = cascade.build_cascade_ledger(units)
    fails = [f for f in led["findings"] if f["category"] == cascade.CAT_NUMBER]
    assert len(fails) == 1
    assert "9 years" in fails[0]["claim"]["text"]
    assert "7 years" not in fails[0]["claim"]["text"]


def test_finding_id_carries_the_clause_index():
    units = [unit("Kept for 9 years.", "kept for seven years")]
    led = cascade.build_cascade_ledger(units)
    assert led["findings"][0]["id"] == "h1:0:0.0:number_unsupported"


# ---------------------------------------------------------------------------
# Derived numbers: centuries
# ---------------------------------------------------------------------------

def test_a_century_is_derivable_from_a_year():
    """Observed on the real 5-chunk job: the model wrote "a 15th-century saint"
    from a source that says 1485. Correct summarising, no matching token."""
    assert cascade.check_numbers("a 15th-century saint",
                                 "born in 1485 CE")["status"] == "pass"


def test_the_wrong_century_still_fails():
    assert cascade.check_numbers("a 16th-century saint",
                                 "born in 1485 CE")["status"] == "fail"


# ---------------------------------------------------------------------------
# Entity mode: measured down from a hard failure to a routing signal
# ---------------------------------------------------------------------------

def test_entity_absence_routes_by_default_and_never_hard_fails():
    """Measured: even at its best threshold this signal flags 15% of faithful
    real sentences, so it may escalate a claim but must never fail one."""
    decision, signals = cascade.route("Riverside Health was audited in March.",
                                      "Brookside Clinic was audited in March.")
    assert decision == "escalate"
    ent = [s for s in signals if s["name"] == "entities"][0]
    assert ent["kind"] == cascade.KIND_HARD     # deterministic...
    assert ent["status"] == "warn"              # ...but not a verdict
    assert ent["mode"] == "route"


def test_entity_hard_mode_is_available_for_clean_corpora():
    decision, _ = cascade.route("Riverside Health was audited in March.",
                                "Brookside Clinic was audited in March.",
                                entity_mode="hard")
    assert decision == "hard_fail_entity"


def test_entity_off_mode_does_not_route():
    ev = ("Brookside Clinic was audited in March and the report was accepted "
          "by the board without amendment.")
    _decision, signals = cascade.route("Brookside Clinic was audited in March.",
                                       ev, entity_mode="off")
    ent = [s for s in signals if s["name"] == "entities"][0]
    assert ent["status"] in ("pass", "info")


def test_status_fail_always_means_a_hard_failure():
    """A ledger consumer must be able to trust that reading."""
    _d, signals = cascade.route("Riverside Health was audited in March.",
                                "Brookside Clinic was audited in March.")
    for s in signals:
        if s.get("status") == "fail":
            assert s["name"] == "numbers"


# ---------------------------------------------------------------------------
# Scope signals: the measured blind spot of pure token matching
# ---------------------------------------------------------------------------

def test_universal_quantifier_over_a_partitive_source_escalates():
    """Every number and name is present; only the SCOPE changed."""
    ev = "Three of twelve sampled files contained a signed consent form."
    decision, signals = cascade.route(
        "All twelve sampled files contained a signed consent form.", ev)
    assert decision == "escalate"
    q = [s for s in signals if s["name"] == "quantifier_scope"][0]
    assert q["status"] == "warn"
    assert q["kind"] == cascade.KIND_SOFT


def test_a_dropped_carve_out_escalates():
    ev = ("Records may not be transferred to a third party unless the client "
          "has given written consent.")
    decision, signals = cascade.route(
        "Records may be transferred to a third party without consent.", ev)
    assert decision == "escalate"
    d = [s for s in signals if s["name"] == "dropped_qualifier"][0]
    assert "unless" in d["dropped"]


def test_scope_signals_never_fail_a_claim():
    ev = "Three of twelve sampled files contained a signed consent form."
    for sig in cascade.scope_signals("All twelve files were signed.", ev):
        assert sig["kind"] == cascade.KIND_SOFT
        assert sig["status"] in ("pass", "warn")

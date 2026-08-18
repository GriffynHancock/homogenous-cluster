"""Two-scope hard checking, and the canonical entity index.

WHAT THESE DO AND DO NOT SHOW. Per F34/F38 these are fixtures and therefore
test the half we wrote. They pin the two things a fixture CAN pin:

  * the CONTRACT -- that a figure present elsewhere in the document is reported
    as a citation error, that a figure present nowhere is reported as a
    fabrication, that neither is ever downgraded to a pass, and that the wider
    scope is not consulted at all when the cited span already accounts for the
    figure;
  * the MATCHING RULES -- which forms of a name resolve to which, and
    specifically the ones that must NOT resolve, because an over-permissive
    matcher passes the exact failure this module exists to catch.

They are not evidence about real model output. That lives in
`docs/entity-index.md`, measured on 1,014 real claims and two mutation
batteries.
"""

import pytest

from missing_link import cascade, entity_index

# A three-chunk "document". Chunk 1 is the cited span in most tests below;
# chunk 2 holds figures and names the cited span does not, which is what makes
# the citation-error case constructible at all.
CHUNK0 = ("The Riverside Health service was audited in March. Records are "
          "kept for seven years under the current policy.")
CHUNK1 = ("The audit was conducted by Kedarnath Prasad Dutt and covered "
          "1,240 client files across the district.")
CHUNK2 = ("Brookside Clinic reported 38 incidents in 2024 and referred nine "
          "of them to the regulator.")

DOC_PARTS = [("chunk 0", CHUNK0), ("chunk 1", CHUNK1), ("chunk 2", CHUNK2)]


def scope():
    return cascade.DocumentScope(DOC_PARTS)


def unit(claim, evidence, idx=0, s_i=0):
    return {"hop": cascade.HOP_CHUNK, "claim_text": claim,
            "evidence_text": evidence,
            "claim": {"unit": "chunk_summary", "chunk_index": idx,
                      "sentence_index": s_i, "clause_index": 0,
                      "start_char": 0, "end_char": len(claim), "line": 1},
            "evidence": {"unit": "source_document", "chunk_index": idx,
                         "start_char": 0, "end_char": len(evidence),
                         "location_confidence": "direct"}}


# ---------------------------------------------------------------------------
# CHANGE 1 -- the two scopes are different findings
# ---------------------------------------------------------------------------

def test_figure_in_another_chunk_is_a_citation_error_not_a_fabrication():
    """The figure is right; the section it was attributed to is wrong."""
    r = cascade.check_numbers("The audit covered 38 incidents.", CHUNK1,
                              scope=scope())
    assert r["status"] == "fail"
    assert [e["found_in"] for e in r["elsewhere_in_document"]] == ["chunk 2"]
    assert r["absent_from_document"] == []
    assert "CITATION ERROR" in r["detail"]


def test_figure_in_no_chunk_at_all_is_a_fabrication():
    r = cascade.check_numbers("The audit covered 9,999 incidents.", CHUNK1,
                              scope=scope())
    assert r["status"] == "fail"
    assert [f["claim"]["text"] for f in r["absent_from_document"]] == ["9,999"]
    assert r["elsewhere_in_document"] == []
    assert "FABRICATION" in r["detail"]


def test_the_two_outcomes_are_different_categories_in_the_ledger():
    led = cascade.build_cascade_ledger(
        [unit("The audit covered 38 incidents.", CHUNK1, 1, 0),
         unit("The audit covered 9,999 incidents.", CHUNK1, 1, 1)],
        scope=scope())
    cats = [f["category"] for f in led["findings"]]
    assert cascade.CAT_NUMBER_FABRICATED in cats
    assert cascade.CAT_NUMBER_ELSEWHERE in cats
    # A fabrication must sort ABOVE a misattribution: a reader told "citation
    # error" will not go looking for an invention.
    assert led["findings"][0]["category"] == cascade.CAT_NUMBER_FABRICATED


def test_widening_never_turns_a_failure_into_a_pass():
    """A figure supported by chunk 2 is still not supported by chunk 1."""
    with_scope = cascade.build_cascade_ledger(
        [unit("The audit covered 38 incidents.", CHUNK1)], scope=scope())
    without = cascade.build_cascade_ledger(
        [unit("The audit covered 38 incidents.", CHUNK1)])
    assert with_scope["cascade"]["hard_fail_number"] == 1
    assert without["cascade"]["hard_fail_number"] == 1
    assert with_scope["cascade"]["passed_cheaply"] == 0


def test_a_figure_present_in_its_own_span_never_triggers_the_wider_search():
    """ORDERING IS THE COST STORY. The second pass must not run on the common
    path, so a scope that would explode if touched proves it was not."""

    class Exploding(cascade.DocumentScope):
        def find_number(self, num):
            raise AssertionError("the wider scope was searched for a figure "
                                 "the cited span already accounts for")

        def find_entity(self, term):
            raise AssertionError("the wider scope was searched for a term the "
                                 "cited span already resolves")

    led = cascade.build_cascade_ledger(
        [unit("Records are kept for seven years.", CHUNK0)],
        scope=Exploding(DOC_PARTS))
    assert led["cascade"]["passed_cheaply"] == 1


def test_without_a_scope_the_verdict_stays_undivided():
    """A checker given nothing wider to look in must not claim to know which
    of the two failures it found."""
    led = cascade.build_cascade_ledger(
        [unit("The audit covered 9,999 incidents.", CHUNK1)])
    assert led["findings"][0]["category"] == cascade.CAT_NUMBER
    assert led["findings"][0]["scope_checked"] == "cited span only"


def test_status_fail_still_only_ever_means_a_hard_failure():
    for claim in ("The audit covered 38 incidents.",
                  "The audit covered 9,999 incidents."):
        _d, signals = cascade.route(claim, CHUNK1, scope=scope())
        for s in signals:
            if s.get("status") == "fail":
                assert s["name"] == "numbers"


def test_the_scope_split_is_reported_on_the_finding_not_buried_in_signals():
    led = cascade.build_cascade_ledger(
        [unit("The audit covered 9,999 incidents.", CHUNK1)], scope=scope())
    f = led["findings"][0]
    assert f["scope_checked"] == "cited span, then the document"
    assert f["absent_from_document"] == ["9,999"]


def test_entities_get_the_same_treatment_and_soft_signals_do_not():
    """WHICH SIGNALS WERE EXTENDED. Numbers and entities carry a scope verdict;
    the similarity and scope signals deliberately do not, because a whole
    document trivially covers any claim's vocabulary and finding SOME sentence
    with agreeable polarity is the laundering this exists to expose."""
    _d, signals = cascade.route("Brookside Clinic covered 9,999 files.",
                                CHUNK1, scope=scope())
    by_name = {s["name"]: s for s in signals}
    assert "document_scope" in by_name["numbers"]
    assert "document_scope" in by_name["entities"]
    for name in ("lexical_overlap", "trigram_similarity", "quantifier_scope",
                 "dropped_qualifier"):
        assert "document_scope" not in by_name[name]


def test_an_entity_from_another_chunk_is_reported_as_a_citation_error():
    r = cascade.check_entities("Brookside Clinic was audited.", CHUNK1,
                               scope=scope())
    assert "Brookside Clinic" in [m["term"] for m in r["elsewhere_in_document"]]
    assert r["absent_from_document"] == []


def test_an_entity_in_no_chunk_is_reported_as_absent_from_the_document():
    r = cascade.check_entities("Fenwick Partners was audited.", CHUNK1,
                               scope=scope())
    assert "Fenwick Partners" in [m["term"] for m in r["absent_from_document"]]
    assert r["elsewhere_in_document"] == []


def test_entity_scope_split_does_not_promote_the_entity_signal():
    """MEASURED, not cautious: even with the index the entity check flags 8.5%
    of faithful real claims, so it still routes and never fails."""
    decision, signals = cascade.route("Fenwick Partners was audited.", CHUNK1,
                                      scope=scope())
    assert decision == "escalate"
    ent = [s for s in signals if s["name"] == "entities"][0]
    assert ent["status"] == "warn"


# ---------------------------------------------------------------------------
# CHANGE 2 -- the canonical entity index
# ---------------------------------------------------------------------------

def resolve(name, text):
    return entity_index.EntityIndex.from_text(text).resolve(name)


@pytest.mark.parametrize("name,text", [
    # diacritics and case
    ("Srila Kedarnath", "written by Śrīla Kedārnāth"),
    ("Śrīla Kedārnāth", "written by Srila Kedarnath"),
    # hyphen and non-breaking-character normalisation
    ("Bhakti-Vinod", "the author Bhakti Vinod wrote"),
    ("Sri Chaitanya", "by Sri Chaitanya Saraswat Math"),
    ("Bhaktivinod‑Thakur", "by Bhaktivinod Thakur"),
    # initials against the full name, and back
    ("K.P. Dutt", "introduced by Kedarnath Prasad Dutt in Calcutta"),
    ("Kedarnath Prasad Dutt", "introduced by K.P. Dutt in Calcutta"),
    # OCR damage of a multi-word name -- the case that produced the measured
    # false-positive rate
    ("Bhakti Sundar Govinda Dev-Gosvami",
     "by Bhakti Sundar Govinda Dev-Goswåmè Mahåråj"),
])
def test_forms_that_must_resolve(name, text):
    assert resolve(name, text) is not None, name


@pytest.mark.parametrize("name,text", [
    # THE DANGEROUS DIRECTION. Each of these is a different person or thing and
    # must NOT be resolved onto the real one; a matcher that passes any of them
    # would silently launder the fabrication it exists to catch.
    ("K.P. Smith", "introduced by Kedarnath Prasad Dutt in Calcutta"),
    ("Riverside Health", "Brookside Clinic was audited in March"),
    ("Fenwick Partners", "audited by Kedarnath Prasad Dutt"),
    ("INV-9999", "invoice INV-4471 was paid in full"),
    # ANY-part matching is rejected: a real given name must not carry a
    # fabricated surname
    ("Kedarnath Fenwick", "introduced by Kedarnath Prasad Dutt"),
])
def test_forms_that_must_not_resolve(name, text):
    assert resolve(name, text) is None, name


def test_a_bare_initial_pair_is_too_short_to_carry_a_match_either_way():
    """'K.P.' compacts to two characters. Resolution reports `trivial` -- the
    lax direction, stated: a two-character token is not a distinctive term and
    flagging it would be noise, but it is labelled so nobody reads it as
    support."""
    r = resolve("K.P.", "introduced by Kedarnath Prasad Dutt")
    assert r is not None and r.rule == "trivial"
    # And an initials match still may not be anchored on an initial.
    assert entity_index._initials_align(("k", "p"),
                                        ("kedarnath", "prasad")) is False


def test_part_fuzzy_is_implemented_and_deliberately_off():
    """It is off because it was MEASURED, not because it is unwritten: it
    removed 0.9 points of false positives and cost 5.4 points of catch on
    1-character name corruptions."""
    assert entity_index.R_PART_FUZZY not in entity_index.RULES
    assert "part_fuzzy" in entity_index.REJECTED_RULES
    # A REAL given name carrying a FABRICATED surname, where the surname is one
    # character from a different real person's. No containment rule resolves
    # it, and whole-name fuzzy does not either -- only per-token fuzzy does,
    # which is exactly why per-token fuzzy is off.
    text = "audited by Kedarnath Dutt and reviewed by Prasad Sharma"
    permissive = entity_index.EntityIndex.from_text(
        text, rules=entity_index.RULES + (entity_index.R_PART_FUZZY,))
    assert permissive.resolve("Kedarnath Sharmb") is not None
    assert resolve("Kedarnath Sharmb", text) is None


def test_strict_rules_drop_fuzzy_entirely():
    strict = entity_index.EntityIndex.from_text(
        "by Bhakti Sundar Govinda Dev-Goswåmè Mahåråj",
        rules=entity_index.STRICT_RULES)
    assert strict.resolve("Bhakti Sundar Govinda Dev-Gosvami") is None
    assert entity_index.R_WHOLE_FUZZY not in entity_index.STRICT_RULES


def test_an_approximate_resolution_is_disclosed_not_hidden():
    """A fuzzy match is the only kind that could be absorbing a corrupted name,
    so it is named in the output rather than reported as plain support."""
    r = cascade.check_entities(
        "The book is by Bhakti Sundar Govinda Dev-Gosvami.",
        "published by Bhakti Sundar Govinda Dev-Goswåmè Mahåråj in 1985")
    assert r["status"] == "pass"
    assert r["resolved_approximately"]
    assert any(m.get("approximate") for m in r["matched"])


def test_a_sentence_dash_is_not_a_word_hyphen():
    """MEASURED FALSE POSITIVE, same class as the non-breaking hyphen that read
    'twenty-four' as 20 and 4: an em dash normalised to '-' glued the next word
    onto a proper noun, inventing a name that occurs in no source."""
    terms = [s for s, _c in cascade.extract_entities(
        "Krishna-lila and Gaura-lila—are described here.")]
    assert "Gaura-lila" in terms
    assert not any("-are" in t for t in terms)


def test_and_does_not_span_a_proper_noun_phrase():
    terms = [s for s, _c in cascade.extract_entities(
        "He cites Bhagavad-Gita and Srimad-Bhagavatam throughout.")]
    assert "Bhagavad-Gita" in terms
    assert "Srimad-Bhagavatam" in terms
    assert not any(" and " in t for t in terms)


def test_number_word_hyphens_are_still_joins_not_splits():
    """The dash change must not leak into the NUMBER scanner, which needs
    'twenty-four' to be 24 and '1485-1534' to be a range."""
    assert {str(n.value) for n in cascade.extract_numbers("twenty‑four")} \
        == {"24"}
    assert "1534" in {str(n.value)
                      for n in cascade.extract_numbers("1485–1534 CE")}


# ---------------------------------------------------------------------------
# The corpus scope: exposed, and permanently unable to grant support
# ---------------------------------------------------------------------------

def test_a_name_from_another_document_never_supports_a_claim():
    other = entity_index.EntityIndex.from_text(
        "Fenwick Partners audited the trust in 2019.", label="other.pdf")
    led = cascade.build_cascade_ledger(
        [unit("Fenwick Partners was audited.", CHUNK1)],
        scope=scope(), corpus_index=other)
    f = led["findings"][0]
    # Annotated...
    assert f["also_in_other_documents"]["Fenwick Partners"] == ["other.pdf"]
    # ...and NOT supported: the claim is still flagged, and the entity is still
    # recorded as absent from the document the summary is supposed to describe.
    ent = [s for s in f["signals"] if s["name"] == "entities"][0]
    assert "Fenwick Partners" in [m["term"] for m in ent["absent_from_document"]]
    assert f["category"] != "pass"


def test_the_corpus_annotation_cannot_create_or_remove_a_finding():
    units = [unit("Fenwick Partners was audited.", CHUNK1)]
    plain = cascade.build_cascade_ledger(units, scope=scope())
    annotated = cascade.build_cascade_ledger(
        units, scope=scope(),
        corpus_index=entity_index.EntityIndex.from_text(
            "Fenwick Partners audited the trust.", label="other.pdf"))
    assert [f["category"] for f in plain["findings"]] == \
        [f["category"] for f in annotated["findings"]]


# ---------------------------------------------------------------------------
# Scope construction
# ---------------------------------------------------------------------------

def test_scope_indexes_are_built_lazily():
    sc = scope()
    assert sc.stats()["number_index_built"] is False
    assert sc.stats()["entity_index_built"] is False
    sc.find_number(cascade.extract_numbers("38 incidents")[0])
    assert sc.stats()["number_index_built"] is True
    assert sc.stats()["entity_index_built"] is False


def test_scope_from_chunks_names_the_section_a_reader_can_navigate_to():
    document = CHUNK0 + CHUNK1
    records = [{"index": 0, "start_char": 0, "end_char": len(CHUNK0)},
               {"index": 1, "start_char": len(CHUNK0),
                "end_char": len(document)}]
    sc = cascade.document_scope_from_chunks(document, records)
    assert [lbl for lbl, _t in sc.parts] == ["chunk 0", "chunk 1"]
    found = sc.find_number(cascade.extract_numbers("1,240 files")[0])
    assert found[0] == "chunk 1"

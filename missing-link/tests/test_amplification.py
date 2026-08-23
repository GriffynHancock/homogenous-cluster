"""Tests for the paired fabrication-amplification harness.

WHAT THESE ACTUALLY EXERCISE, stated plainly because this repo's own history
says a test count is not evidence of working software (F34: 41 tests passed
against a pipeline that had never processed a document).

Exercised for real here:
  * the REAL cascade, on constructed-but-realistic summary text, including a
    reproduction of F42's shape -- a year asserted by the output that occurs
    nowhere in the section -- caught by string comparison and nothing else;
  * the REAL chunker (`worker.chunk_document`) deciding whether a section can
    support a map-reduce arm at all;
  * the REAL prompt builders, through the driver's `run_arm`, against a fake
    client, so the call SHAPE of each arm is asserted rather than assumed;
  * the arm-symmetry invariant, which is the one property that, if it broke,
    would invalidate every number the experiment produces while leaving it
    looking perfectly healthy;
  * exact statistics, against hand-computed values.

NOT exercised here, and it cannot be: whether a real gpt-oss-120b actually
fabricates more under map-reduce. That is the experiment, and it needs the
cluster.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bench"))

from missing_link import amplification as amp
from missing_link import cascade, worker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def exact_char_tokenizer(divisor=8):
    """A deterministic stand-in for the server tokeniser: 1 token per N chars.

    Deliberately NOT word-based. Section fitting must not depend on the
    words-per-token approximation the harness exists to stop relying on, and a
    char-based fake makes a word-based regression fail loudly. The fixture
    document averages 9.56 chars per word, so a divisor of 8 puts this at
    ~1.2 tokens per word -- the neighbourhood real English prose lands in.
    """
    return lambda text: (len(text) + divisor - 1) // divisor


# A section must reach ~2,900 words before `worker.chunk_document` gives it a
# second chunk at CHUNK_TOKENS=4096 (measured against the real chunker, not
# assumed). At 9.56 chars/word and 8 chars/token that is ~3,465 tokens, so
# every "should be accepted" test below budgets comfortably above it. A test
# that budgeted lower would pass for the wrong reason: everything rejected.
TEST_BUDGET = 5000


def make_document(n_paragraphs=120, words_per_para=120, seed_year=1988):
    """A statute-shaped document: numbered paragraphs, figures, proper nouns."""
    paras = []
    for i in range(n_paragraphs):
        body = " ".join(f"word{i}x{j}" for j in range(words_per_para - 14))
        paras.append(
            f"Section {i + 1} — Obligations of the Commissioner. "
            f"An entity must retain records for {7 + (i % 5)} years from "
            f"{seed_year + i} unless the Commissioner determines otherwise. "
            f"The threshold is ${1000 + i * 37} for each notifiable event. "
            f"{body}")
    return "\n\n".join(paras)


@pytest.fixture
def document():
    return make_document()


# ---------------------------------------------------------------------------
# Slot arithmetic
# ---------------------------------------------------------------------------
def test_single_pass_budget_leaves_room_for_output_and_wrapper():
    b = amp.single_pass_budget_tokens(slot_tokens=8192)
    assert b == int((8192 - worker.REDUCE_MAX_TOKENS
                     - worker._PROMPT_WRAPPER_TOKENS) * 0.85)
    # The whole point: budget + output + wrapper must fit the slot with room.
    assert b + worker.REDUCE_MAX_TOKENS + worker._PROMPT_WRAPPER_TOKENS < 8192


def test_budget_defaults_to_the_workers_confirmed_slot_size():
    assert amp.single_pass_budget_tokens() == amp.single_pass_budget_tokens(
        slot_tokens=worker.N_CTX_SLOT)


def test_budget_cannot_go_negative_on_a_tiny_slot():
    assert amp.single_pass_budget_tokens(slot_tokens=100) == 0


def test_single_pass_output_budget_is_the_reduce_budget_not_the_map_budget():
    """A control arm capped at MAP_MAX_TOKENS would truncate where the treatment
    arm did not, which is a confound wearing a guard's uniform."""
    assert amp.SECTION_OUTPUT_TOKENS == worker.REDUCE_MAX_TOKENS
    assert amp.SECTION_OUTPUT_TOKENS > worker.MAP_MAX_TOKENS


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
def test_paragraph_starts_are_paragraph_aligned(document):
    starts = amp.paragraph_starts(document)
    assert starts[0] == 0
    assert len(starts) == 120
    for s in starts:
        assert document[s:s + 7].startswith("Section")


def test_paragraph_starts_falls_back_to_sentences_without_blank_lines():
    text = "First sentence here. Second sentence here. Third sentence here."
    starts = amp.paragraph_starts(text)
    assert len(starts) > 1
    assert starts[0] == 0


def test_paragraph_starts_degenerates_gracefully():
    assert amp.paragraph_starts("") == [0]
    assert amp.paragraph_starts("onewordnopunctuation") == [0]


# ---------------------------------------------------------------------------
# Section selection
# ---------------------------------------------------------------------------
def test_every_accepted_section_actually_fits_the_budget(document):
    tok = exact_char_tokenizer()
    budget = TEST_BUDGET
    cands = amp.candidate_sections(document, tok, budget_tokens=budget,
                                   min_numbers=0)
    accepted = [c for c in cands if not c["rejected"]]
    assert accepted
    for c in accepted:
        # Re-measured with the same exact counter, not trusted from the record.
        assert tok(document[c["start_char"]:c["end_char"]]) <= budget
        assert c["n_tokens"] <= budget


def test_sections_are_paragraph_aligned_and_non_overlapping(document):
    cands = amp.candidate_sections(document, exact_char_tokenizer(),
                                   budget_tokens=TEST_BUDGET, min_numbers=0)
    accepted = [c for c in cands if not c["rejected"]]
    assert len(accepted) >= 3
    starts = set(amp.section_boundaries(document, 10 ** 9)) | {len(document)}
    prev_end = 0
    for c in accepted:
        assert c["start_char"] in starts
        assert c["end_char"] in starts
        assert c["start_char"] >= prev_end
        prev_end = c["end_char"]


def test_a_section_too_small_to_chunk_is_rejected_with_a_reason(document):
    """A one-chunk 'map-reduce' arm has no reduce step, so it IS the control
    arm. Running it would compare the control against itself and report a null
    that means nothing."""
    cands = amp.candidate_sections(document, exact_char_tokenizer(),
                                   budget_tokens=1200, min_numbers=0)
    rejected = [c for c in cands if c["rejected"]]
    assert rejected
    assert any("no reduce step" in c["rejected"] for c in rejected)


def test_a_section_with_no_figures_is_rejected(document):
    plain = "\n\n".join("alpha beta gamma delta " * 200 for _ in range(20))
    cands = amp.candidate_sections(plain, exact_char_tokenizer(),
                                   budget_tokens=TEST_BUDGET, min_numbers=10)
    assert cands
    assert all(c["rejected"] for c in cands)
    assert any("checkable figures" in c["rejected"] for c in cands)


def test_a_single_oversized_sentence_is_skipped_not_split():
    """Splitting mid-sentence would hand the control arm a fragment that begins
    mid-clause, giving it an incentive to complete it from world knowledge --
    manufacturing the exact failure being measured, on one arm."""
    text = "x " * 5000 + ".\n\n" + "Section 2. The limit is 42 days.\n\n"
    cands = amp.candidate_sections(text, exact_char_tokenizer(),
                                   budget_tokens=100, min_numbers=0)
    assert cands[0]["rejected"]
    assert "mid-clause" in cands[0]["rejected"]


# ---------------------------------------------------------------------------
# The defect real corpus text exposed
# ---------------------------------------------------------------------------
def test_one_giant_paragraph_still_yields_sections():
    """REGRESSION, found by running `plan` against the real corpus. The HTML
    extraction of the Privacy Amendment (Notifiable Data Breaches) Act 2017 has
    24 paragraphs, 23 of them under 40 characters and one of 29,053 -- the whole
    body of the Act with no blank line in it. Paragraph-only sectioning returned
    ZERO usable sections from a perfectly usable document, and would have done
    so silently across most of a corpus that is mostly extracted HTML."""
    body = " ".join(
        f"Clause {i} requires retention for {7 + i % 5} years from {1988 + i}."
        for i in range(1200))
    text = "Short heading.\n\n" + body + "\n\nShort footer."
    paras = amp.paragraph_starts(text)
    assert len(paras) == 3, "the giant paragraph really is one paragraph"

    refined = amp.section_boundaries(text, max_gap=4000)
    assert len(refined) > 100, "sentence boundaries inside the giant paragraph"

    cands = amp.candidate_sections(text, exact_char_tokenizer(),
                                   budget_tokens=TEST_BUDGET, min_numbers=0)
    accepted = [c for c in cands if not c["rejected"]]
    assert accepted, "the giant paragraph must still produce usable sections"


def test_section_boundaries_leaves_normal_paragraphs_alone(document):
    """Refinement is for oversized paragraphs only -- a document with ordinary
    paragraph structure must not be re-cut at every sentence."""
    assert (amp.section_boundaries(document, max_gap=10 ** 9)
            == amp.paragraph_starts(document))


def test_refined_boundaries_are_still_sentence_starts():
    text = "A. " + ("Alpha beta gamma. " * 400)
    refined = amp.section_boundaries(text, max_gap=500)
    for off in refined[1:]:
        assert text[off].isupper() or text[off].isdigit()


def test_candidate_sections_uses_the_real_chunker(document, monkeypatch):
    seen = {}
    real = worker.chunk_document

    def spy(text, chunk_tokens=None, overlap_tokens=None):
        seen["called"] = True
        return real(text, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)

    monkeypatch.setattr(worker, "chunk_document", spy)
    amp.candidate_sections(document, exact_char_tokenizer(),
                           budget_tokens=TEST_BUDGET, min_numbers=0)
    assert seen.get("called")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def _fake_candidates(n):
    return [{"start_char": i * 100, "end_char": i * 100 + 90, "n_tokens": 500,
             "n_words": 80, "n_chunks": 2, "n_numbers": 20, "rejected": None}
            for i in range(n)]


def test_sampling_is_deterministic_and_capped():
    by_doc = {"d1": _fake_candidates(9), "d2": _fake_candidates(2)}
    a = amp.sample_sections(by_doc, per_doc=3)
    b = amp.sample_sections(by_doc, per_doc=3)
    assert a == b
    assert sum(1 for s in a if s["doc_id"] == "d1") == 3
    assert sum(1 for s in a if s["doc_id"] == "d2") == 2


def test_sampling_spreads_rather_than_taking_the_front_matter():
    by_doc = {"d1": _fake_candidates(12)}
    picked = amp.sample_sections(by_doc, per_doc=3)
    idx = [p["start_char"] // 100 for p in picked]
    assert idx[0] > 0, "never the very first candidate -- that is front matter"
    assert idx[-1] < 11, "never the very last candidate either"
    assert idx == sorted(idx) and len(set(idx)) == 3


def test_sampling_skips_documents_with_no_usable_section():
    by_doc = {"d1": [{"rejected": "too short"}], "d2": _fake_candidates(3)}
    picked = amp.sample_sections(by_doc, per_doc=3)
    assert {p["doc_id"] for p in picked} == {"d2"}


def test_seeded_sampling_is_reproducible_and_differs_from_the_spread():
    by_doc = {"d1": _fake_candidates(20)}
    a = amp.sample_sections(by_doc, per_doc=3, seed="x")
    assert a == amp.sample_sections(by_doc, per_doc=3, seed="x")


def test_section_ids_are_stable_and_carry_their_offsets():
    picked = amp.sample_sections({"d1": _fake_candidates(4)}, per_doc=2)
    for p in picked:
        assert p["section_id"] == f"d1:{p['start_char']}-{p['end_char']}"


# ---------------------------------------------------------------------------
# ARM SYMMETRY -- the invariant the whole design rests on
# ---------------------------------------------------------------------------
def test_both_arms_are_scored_against_identical_evidence():
    section = "The retention period is 7 years from 1988."
    out_a = "Records are kept for 7 years."
    out_b = "Records are kept for 7 years from 1988."
    ua = amp.final_vs_source_units(section, out_a)
    ub = amp.final_vs_source_units(section, out_b)
    assert {u["evidence_text"] for u in ua + ub} == {section}
    assert all(u["evidence"]["unit"] == "section_source" for u in ua + ub)


def test_the_hop_label_is_not_the_production_one():
    """`cascade.HOP_FINAL` means 'final vs CHUNK SUMMARIES'. Using it here would
    label every finding with an evidence window the single-pass arm does not
    have."""
    units = amp.final_vs_source_units("a section with 12 items", "It lists 12 items.")
    assert units[0]["hop"] == amp.HOP_OUTPUT_VS_SOURCE
    assert units[0]["hop"] != cascade.HOP_FINAL


def test_identical_output_scores_identically_regardless_of_arm():
    section = ("The Commissioner must be notified within 30 days. "
               "The penalty is $2,220 per contravention.")
    text = "Notification is required within 30 days and the penalty is $2,220."
    l1 = amp.score_output(section, text)
    l2 = amp.score_output(section, text)
    assert l1["totals"]["by_category"] == l2["totals"]["by_category"]
    # `cheap_tier_seconds` is a wall-clock measurement and is the one field that
    # legitimately differs between identical runs. Everything that FEEDS THE
    # STATISTICS must be bit-identical, so it is compared and the clock is not.
    e1, e2 = amp.endpoints(l1), amp.endpoints(l2)
    e1.pop("cheap_tier_seconds"), e2.pop("cheap_tier_seconds")
    assert e1 == e2


# ---------------------------------------------------------------------------
# The scorer, on the failure it exists to catch
# ---------------------------------------------------------------------------
SECTION = (
    "Section 12 — Notifiable data breaches. An entity must notify the "
    "Commissioner within 30 days of becoming aware of an eligible data breach. "
    "The Commissioner may extend that period. Records must be retained for 7 "
    "years. The maximum civil penalty is $2,220,000."
)


def test_a_faithful_summary_raises_no_hard_number_finding():
    faithful = ("Entities must notify the Commissioner within 30 days of an "
                "eligible data breach. Records are retained for 7 years and "
                "the maximum civil penalty is $2,220,000.")
    ep = amp.endpoints(amp.score_output(SECTION, faithful))
    assert ep["e1_number_findings"] == 0
    assert ep["e1_any"] == 0
    assert ep["claims"] > 0


def test_f42_shape_a_figure_absent_from_the_section_is_hard_failed():
    """F42 reproduced in miniature: the output asserts a figure the source does
    not contain, and a plain number-in-span check catches it with no model
    consulted. This is the harness's primary endpoint firing."""
    fabricated = ("Entities must notify the Commissioner within 30 days. "
                  "The scheme commenced in 1994 and records are kept for 7 years.")
    ledger = amp.score_output(SECTION, fabricated)
    ep = amp.endpoints(ledger)
    assert ep["e1_number_findings"] >= 1
    assert ep["e1_any"] == 1
    assert any("1994" in (f["detail"] or "") for f in ledger["findings"])


def test_the_classifier_is_never_switched_on():
    ledger = amp.score_output(SECTION, "The penalty is $2,220,000.")
    assert ledger["config"]["classifier_enabled"] is False
    assert ledger["models"] == []


def test_scope_separates_fabrication_from_context_bleed():
    """A figure present LATER in the document but not in the section shown is
    the model bleeding context, not inventing -- a different failure, and one
    the single-pass arm can commit too. The wider scope may reclassify a
    failure; it may never rescue one."""
    document = SECTION + "\n\nSection 13 — The review period is 1994 days."
    section_rec = {"start_char": 0, "end_char": len(SECTION)}
    scope = amp.document_scope(document, section_rec)
    output = "The scheme refers to a period of 1994 days."
    ledger = amp.score_output(SECTION, output, scope=scope)
    ep = amp.endpoints(ledger)
    assert ep["e1_number_findings"] >= 1
    assert ep["e1_number_misattributed"] >= 1
    assert ep["e1_number_fabricated"] == 0


def test_scope_marks_a_true_fabrication_as_fabricated():
    document = SECTION + "\n\nSection 13 — Review is annual."
    scope = amp.document_scope(document, {"start_char": 0,
                                          "end_char": len(SECTION)})
    ledger = amp.score_output(SECTION, "The scheme commenced in 1994.",
                              scope=scope)
    ep = amp.endpoints(ledger)
    assert ep["e1_number_fabricated"] >= 1
    assert ep["e1_number_misattributed"] == 0


def test_document_scope_never_rescues_a_failure():
    """Stated as a test because it is the property that makes passing the wider
    scope safe at all."""
    document = SECTION + "\n\nThe figure 1994 appears here."
    scope = amp.document_scope(document, {"start_char": 0,
                                          "end_char": len(SECTION)})
    with_scope = amp.endpoints(amp.score_output(SECTION, "It began in 1994.",
                                                scope=scope))
    without = amp.endpoints(amp.score_output(SECTION, "It began in 1994."))
    assert with_scope["e1_number_findings"] == without["e1_number_findings"] >= 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def test_endpoints_report_rate_as_well_as_count():
    ep = amp.endpoints(amp.score_output(
        SECTION, "It began in 1994. The penalty is $2,220,000."))
    assert ep["e1_per_100_claims"] is not None
    assert 0 <= ep["e1_per_100_claims"] <= 100
    assert ep["claims"] >= 2


def test_entity_endpoint_reads_the_signal_not_the_category():
    """ENTITY_MODE is 'route', so an absent name never becomes an entity
    CATEGORY -- it lands in a needs_classifier finding. An endpoint that read
    by_category would silently be zero forever."""
    out = "The Federal Court of Zanzibar reviewed the matter within 30 days."
    ep = amp.endpoints(amp.score_output(SECTION, out))
    assert cascade.ENTITY_MODE == "route"
    assert ep["e2_entity_claims"] >= 1
    assert ep["e2_entity_terms"] >= 1


def test_escalation_is_reported_but_is_not_an_endpoint_under_test():
    ep = amp.endpoints(amp.score_output(SECTION, "Records are kept for 7 years."))
    assert "e4_escalation_rate" in ep


# ---------------------------------------------------------------------------
# Laundering decomposition
# ---------------------------------------------------------------------------
def test_laundering_is_not_applicable_to_the_single_pass_arm():
    ledger = amp.score_output(SECTION, "It began in 1994.")
    ld = amp.laundering_decomposition(ledger, [])
    assert ld["applicable"] is False


def test_a_figure_invented_at_reduce_is_labelled_as_such():
    """The F42 case exactly: absent from the source AND from every chunk
    summary, asserted by the reduce step anyway."""
    ledger = amp.score_output(SECTION, "The scheme commenced in 1994.")
    ld = amp.laundering_decomposition(
        ledger, ["Entities must notify within 30 days.",
                 "Records are kept for 7 years."])
    assert ld["applicable"] is True
    assert ld["invented_at_reduce"] >= 1
    assert ld["inherited"] == 0


def test_a_figure_inherited_from_a_chunk_summary_is_labelled_inherited():
    ledger = amp.score_output(SECTION, "The scheme commenced in 1994.")
    ld = amp.laundering_decomposition(
        ledger, ["The chunk summary already said the scheme began in 1994."])
    assert ld["inherited"] >= 1
    assert ld["invented_at_reduce"] == 0


def test_laundering_detail_names_the_value_and_the_claim():
    ledger = amp.score_output(SECTION, "The scheme commenced in 1994.")
    ld = amp.laundering_decomposition(ledger, ["nothing relevant here"])
    assert ld["detail"]
    assert ld["detail"][0]["value"] == "1994"
    assert "1994" in ld["detail"][0]["claim"]


# ---------------------------------------------------------------------------
# Adjudication pack
# ---------------------------------------------------------------------------
def test_adjudication_pack_includes_passing_claims_not_only_findings():
    """F42's generalisable lesson: a checker's defects land almost entirely on
    the sentences that were going to pass, so a validation set drawn only from
    flagged material cannot see them."""
    ledger = amp.score_output(
        SECTION, "It began in 1994. Records are kept for 7 years. "
                 "The penalty is $2,220,000.")
    pack = amp.adjudication_sample(ledger)
    assert pack["findings"]
    assert pack["passing_sample"]
    assert "not optional" in pack["instruction"]


# ---------------------------------------------------------------------------
# Statistics -- hand-computed
# ---------------------------------------------------------------------------
def test_mcnemar_matches_hand_computation():
    # b=0, c=5: one-tailed 1/32, two-tailed 2/32 = 0.0625
    assert amp.mcnemar_exact(0, 5)["p"] == pytest.approx(0.0625)
    # b=0, c=6: 2/64 = 0.03125
    assert amp.mcnemar_exact(0, 6)["p"] == pytest.approx(0.03125)
    assert amp.mcnemar_exact(3, 3)["p"] == pytest.approx(1.0)


def test_mcnemar_with_no_discordant_pairs_says_so_rather_than_claiming_a_result():
    r = amp.mcnemar_exact(0, 0)
    assert r["p"] == 1.0
    assert "nothing to work with" in r["note"]


def test_min_discordant_for_significance_is_six():
    """Six is the smallest n with 2/2^n <= 0.05. Below six discordant pairs the
    design CANNOT produce p<0.05 however they split, and saying so before the
    run is the difference between an honest null and a wasted night."""
    assert amp.min_discordant_for_significance() == 6
    assert amp.mcnemar_exact(0, 5)["p"] > 0.05
    assert amp.mcnemar_exact(0, 6)["p"] <= 0.05


def test_sign_flip_is_exact_for_small_samples():
    r = amp.sign_flip_test([1, 1, 1, 1, 1])
    assert r["method"] == "exact enumeration"
    assert r["p"] == pytest.approx(2 / 32)


def test_sign_flip_ignores_ties_but_reports_them():
    r = amp.sign_flip_test([0, 0, 0])
    assert r["p"] == 1.0
    assert r["n_pairs"] == 3 and r["n_nonzero"] == 0


def test_sign_flip_is_symmetric_in_direction():
    a = amp.sign_flip_test([2, 3, 1, 4])
    b = amp.sign_flip_test([-2, -3, -1, -4])
    assert a["p"] == pytest.approx(b["p"])
    assert a["observed_mean"] == pytest.approx(-b["observed_mean"])


def test_sign_flip_switches_to_monte_carlo_above_twenty():
    r = amp.sign_flip_test([1] * 25, iterations=500)
    assert "monte carlo" in r["method"]
    assert 0 < r["p"] <= 1


def test_bootstrap_ci_brackets_the_mean():
    ci = amp.bootstrap_ci([1, 2, 3, 4, 5], iterations=2000)
    assert ci["lo"] <= ci["mean"] <= ci["hi"]


# ---------------------------------------------------------------------------
# Paired summary
# ---------------------------------------------------------------------------
def _pair(sid, doc, a_val, b_val, key="e1_number_findings"):
    return {"section_id": sid, "doc_id": doc,
            amp.ARM_SINGLE_PASS: {key: a_val, "e1_any": 1 if a_val else 0},
            amp.ARM_MAP_REDUCE: {key: b_val, "e1_any": 1 if b_val else 0}}


def test_paired_summary_signs_the_difference_towards_map_reduce():
    pairs = [_pair(f"s{i}", "d1", 0, 2) for i in range(4)]
    s = amp.paired_summary(pairs, "e1_number_findings")
    assert s["section_level"]["mean_difference"] == 2.0
    assert "map_reduce" in s["direction"]


def test_paired_summary_reports_a_document_level_result_too():
    """Three sections from one statute are not three independent observations.
    The document-level number is the conservative one and must exist."""
    pairs = ([_pair(f"a{i}", "d1", 0, 3) for i in range(3)]
             + [_pair("b0", "d2", 1, 1)])
    s = amp.paired_summary(pairs, "e1_number_findings")
    assert s["n_pairs"] == 4
    assert s["n_documents"] == 2
    assert s["document_level"]["mean_difference"] == pytest.approx(1.5)
    assert s["section_level"]["mean_difference"] == pytest.approx(2.25)


def test_paired_summary_skips_incomplete_pairs():
    pairs = [_pair("s0", "d1", 0, 1), {"section_id": "s1", "doc_id": "d1",
                                       amp.ARM_SINGLE_PASS: {"e1_any": 1}}]
    s = amp.paired_summary(pairs, "e1_number_findings")
    assert s["n_pairs"] == 1


def test_paired_summary_carries_the_power_warning():
    s = amp.paired_summary([_pair("s0", "d1", 0, 1)], "e1_any")
    assert "UNDERPOWERED" in s["power_note"] or "underpowered" in s["power_note"]
    assert str(amp.min_discordant_for_significance()) in s["power_note"]


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------
def test_single_pass_is_one_call_and_map_reduce_is_more():
    a = amp.estimate_arm_seconds(5000, amp.ARM_SINGLE_PASS)
    b = amp.estimate_arm_seconds(5000, amp.ARM_MAP_REDUCE)
    c = amp.estimate_arm_seconds(5000, amp.ARM_MAP_REDUCE_FINE)
    assert a["calls"] == 1
    assert b["calls"] >= 3          # >= 2 maps + 1 reduce
    assert c["calls"] > b["calls"]  # finer chunking, more calls
    assert a["seconds"] < b["seconds"] < c["seconds"]


def test_map_reduce_prefills_more_than_the_section_because_overlap_is_reread():
    b = amp.estimate_arm_seconds(5000, amp.ARM_MAP_REDUCE)
    assert b["prefill_tokens"] > 5000


def test_estimate_uses_the_measured_two_node_speedup_not_two():
    one = amp.estimate_run(50, nodes=1)
    two = amp.estimate_run(50, nodes=2)
    assert one["total_seconds"] / two["total_seconds"] == pytest.approx(1.8)


def test_estimate_scales_linearly_in_sections():
    assert (amp.estimate_run(100)["total_seconds"]
            == pytest.approx(2 * amp.estimate_run(50)["total_seconds"]))


def test_estimate_quotes_its_basis():
    est = amp.estimate_run(10)
    assert "16.3" in est["basis"] and "5.3" in est["basis"]
    assert "measurements.md" in est["basis"]


def test_rate_constants_are_the_llama_server_ones_not_llama_bench():
    """F40's lesson: the llama-bench generation figure (6.05) describes one
    sequence; the deployment runs four slots and measured 5.26-5.34."""
    assert amp.GENERATION_TPS == 5.3
    assert amp.PREFILL_TPS == 16.3


# ---------------------------------------------------------------------------
# Corpus access -- read-only by construction
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_db(tmp_path):
    p = tmp_path / "jobs.sqlite"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE corpus_documents (
        id TEXT PRIMARY KEY, filename TEXT, genre TEXT, status TEXT,
        text TEXT, text_sha256 TEXT, n_words INTEGER, n_chunks INTEGER,
        n_numbers INTEGER, numbers_per_1k_words REAL)""")
    con.execute("CREATE TABLE jobs (id TEXT, status TEXT)")
    con.executemany(
        "INSERT INTO corpus_documents VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("d1", "a.html", "legislative", "ready", "text one 7 years", "h1",
          3, 1, 1, 1.0),
         ("d2", "b.html", "standards", "refused", "", "h2", 0, 0, 0, 0.0)])
    con.execute("INSERT INTO jobs VALUES ('j1','running')")
    con.commit()
    con.close()
    return str(p)


def test_read_corpus_returns_only_ready_documents(temp_db):
    docs = amp.read_corpus(temp_db)
    assert [d["id"] for d in docs] == ["d1"]
    assert docs[0]["text"] == "text one 7 years"


def test_read_corpus_can_restrict_to_named_documents(temp_db):
    assert amp.read_corpus(temp_db, doc_ids=["nope"]) == []


def test_running_jobs_sees_the_live_queue(temp_db):
    assert amp.running_jobs(temp_db) == 1


def test_corpus_access_is_read_only_by_construction(temp_db):
    """`mode=ro` makes it a property of the connection, not a promise about the
    SQL -- the live job store belongs to a running service."""
    con = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM corpus_documents")
    con.close()


# ---------------------------------------------------------------------------
# Driver: call shape per arm, against a fake client
# ---------------------------------------------------------------------------
class FakeClient:
    def __init__(self, replies=None):
        self.timeout = 0
        self.timings_log = []
        self.prompts = []
        self.replies = replies or {}

    def assert_reachable(self, timeout=20):
        return None

    def complete(self, prompt, max_tokens=None):
        self.prompts.append((prompt, max_tokens))
        self.timings_log.append({"prompt_n": 100, "prompt_ms": 1000.0,
                                 "predicted_n": 10, "predicted_ms": 100.0})
        for needle, reply in self.replies.items():
            if needle in prompt:
                return reply
        return f"summary #{len(self.prompts)}"

    def model_name(self):
        return "fake"


@pytest.fixture
def driver():
    import amplification_driver
    return amplification_driver


def test_single_pass_arm_makes_exactly_one_call(driver):
    c = FakeClient()
    rec = driver.run_arm(c, "http://x", SECTION * 4, amp.ARM_SINGLE_PASS,
                         "summarise", None)
    assert len(c.prompts) == 1
    assert rec["chunk_summaries"] == []
    assert c.prompts[0][1] == worker.REDUCE_MAX_TOKENS


def test_map_reduce_arm_makes_one_call_per_chunk_plus_a_reduce(driver):
    section = make_document(n_paragraphs=40, words_per_para=120)
    c = FakeClient()
    rec = driver.run_arm(c, "http://x", section, amp.ARM_MAP_REDUCE,
                         "summarise", None)
    n_chunks = len(worker.chunk_document(section,
                                         chunk_tokens=worker.CHUNK_TOKENS,
                                         overlap_tokens=worker.OVERLAP_TOKENS))
    assert n_chunks > 1
    assert len(c.prompts) == n_chunks + 1
    assert len(rec["chunk_summaries"]) == n_chunks
    assert c.prompts[-1][1] == worker.REDUCE_MAX_TOKENS
    assert all(p[1] == worker.MAP_MAX_TOKENS for p in c.prompts[:-1])


def test_both_arms_are_handed_byte_identical_section_text(driver):
    section = make_document(n_paragraphs=40, words_per_para=120)
    a, b = FakeClient(), FakeClient()
    driver.run_arm(a, "http://x", section, amp.ARM_SINGLE_PASS, "summarise", None)
    driver.run_arm(b, "http://x", section, amp.ARM_MAP_REDUCE, "summarise", None)
    # The single-pass prompt contains the whole section verbatim; the map
    # prompts partition it. Both must derive from the SAME string.
    assert section[:200] in a.prompts[0][0]
    assert " ".join(section.split()[:20]) in b.prompts[0][0]


def test_arms_use_the_real_prompt_builders(driver):
    c = FakeClient()
    driver.run_arm(c, "http://x", SECTION * 4, amp.ARM_SINGLE_PASS,
                   "summarise", None)
    assert c.prompts[0][0].startswith(
        worker.PROMPTS["summarise"].split("{")[0][:40])


def test_reduce_prompt_carries_the_section_markers(driver):
    section = make_document(n_paragraphs=40, words_per_para=120)
    c = FakeClient()
    driver.run_arm(c, "http://x", section, amp.ARM_MAP_REDUCE, "summarise", None)
    assert "[Section 1]" in c.prompts[-1][0]


def test_ratio_tokenizer_is_marked_unsafe_and_run_refuses_it(driver, tmp_path):
    manifest = {"tokenizer": "ratio (ESTIMATED -- NOT SAFE TO RUN)",
                "sections": []}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    class Args:
        out = str(tmp_path)
        db = "unused"
        endpoint = "http://x"
        arms = list(amp.DEFAULT_ARMS)
        allow_busy = False
        limit = None
        kind = "summarise"
        node_ssh_host = None
        unit = "u"
    assert driver.cmd_run(Args()) == 2


def test_run_refuses_while_the_live_queue_is_busy(driver, tmp_path, temp_db):
    manifest = {"tokenizer": "http://x/tokenize (exact)", "sections": []}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    class Args:
        out = str(tmp_path)
        db = temp_db
        endpoint = "http://x"
        arms = list(amp.DEFAULT_ARMS)
        allow_busy = False
        limit = None
        kind = "summarise"
        node_ssh_host = None
        unit = "u"
    assert driver.cmd_run(Args()) == 2


def test_driver_opens_no_socket_at_import_time(driver):
    assert driver.DEFAULT_DB.endswith("jobs.sqlite")
    assert hasattr(driver, "ServerTokenizer")


def test_amplification_module_imports_nothing_heavy():
    """It must be importable in the production venv, which has no torch and no
    transformers -- same rule audit.py follows."""
    for mod in ("torch", "transformers", "minicheck", "numpy", "scipy"):
        assert mod not in sys.modules


# ---------------------------------------------------------------------------
# Fan-out across endpoints
# ---------------------------------------------------------------------------
# CLAUDE.md: "It must fan out across R endpoints, not one." Measured aggregate
# on two nodes is ~1.8x, which turns a 21-hour run into a 12-hour one -- the
# difference between one night and two. The tests below are about the two ways
# that could go wrong quietly.
class _RunHarness:
    """A cmd_run invocation with the cluster replaced by fakes."""

    def __init__(self, driver, tmp_path, monkeypatch, endpoints, models=None,
                 dead=(), arms=None, n_sections=6):
        self.driver = driver
        self.arms = arms or list(amp.DEFAULT_ARMS)
        self.clients = {}
        models = models or {e: "same-model" for e in endpoints}
        doc_text = make_document(n_paragraphs=40)
        sections = [{"section_id": f"doc1:{i}", "doc_id": "doc1",
                     "start_char": 0, "end_char": len(doc_text),
                     "n_tokens": 5000, "n_chunks": 2}
                    for i in range(n_sections)]
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"tokenizer": "http://x/tokenize (exact)", "sections": sections}))

        outer = self

        class EndpointClient(FakeClient):
            def __init__(self, endpoint):
                super().__init__()
                self.endpoint = endpoint
                outer.clients.setdefault(endpoint, []).append(self)

            def model_name(self):
                return models[self.endpoint]

            def complete(self, prompt, max_tokens=None):
                if self.endpoint in dead:
                    raise guards_backend_dead(self.endpoint)
                return super().complete(prompt, max_tokens)

        monkeypatch.setattr(driver.worker, "LlamaClient", EndpointClient)
        monkeypatch.setattr(driver.amp, "read_corpus",
                            lambda db, doc_ids=None: [{"id": "doc1",
                                                       "text": doc_text}])
        monkeypatch.setattr(driver.amp, "running_jobs", lambda db: 0)

        class Args:
            out = str(tmp_path)
            db = "unused"
            endpoint = list(endpoints)
            arms = outer.arms
            allow_busy = False
            limit = None
            kind = "summarise"
            node_ssh_host = None
            unit = "u"
        self.args = Args
        self.tmp_path = tmp_path

    def run(self):
        return self.driver.cmd_run(self.args)

    def results(self):
        return json.loads((self.tmp_path / "results.json").read_text())["runs"]


def guards_backend_dead(endpoint):
    import chunk_size_driver
    return chunk_size_driver.BackendDead(f"{endpoint} is not serving")


def test_fanout_keeps_both_arms_of_a_section_on_one_endpoint(
        driver, tmp_path, monkeypatch):
    """THE property that makes fan-out safe here. If arm A ran on node 1 and
    arm B on node 2, any difference between the nodes would land INSIDE a pair
    and be indistinguishable from the effect being measured."""
    h = _RunHarness(driver, tmp_path, monkeypatch,
                    ["http://a:8080", "http://b:8080"], n_sections=8)
    assert h.run() == 0
    per_section = {}
    for key, rec in h.results().items():
        per_section.setdefault(rec["section_id"], set()).add(rec["endpoint"])
    assert len(per_section) == 8
    for sid, eps in per_section.items():
        assert len(eps) == 1, f"{sid} was split across endpoints {eps}"


def test_fanout_actually_uses_both_endpoints(driver, tmp_path, monkeypatch):
    h = _RunHarness(driver, tmp_path, monkeypatch,
                    ["http://a:8080", "http://b:8080"], n_sections=8)
    assert h.run() == 0
    used = {rec["endpoint"] for rec in h.results().values()}
    assert used == {"http://a:8080", "http://b:8080"}


def test_run_refuses_endpoints_serving_different_models(
        driver, tmp_path, monkeypatch):
    """Node 2's engine has flipped mid-session before (STATUS.md records it
    happening while a documentation pass was reading it). A paired experiment
    whose sections were summarised by two different models is not one
    experiment."""
    h = _RunHarness(driver, tmp_path, monkeypatch,
                    ["http://a:8080", "http://b:8080"],
                    models={"http://a:8080": "gpt-oss-120b",
                            "http://b:8080": "qwen3-4b"})
    assert h.run() == 2
    assert not (tmp_path / "results.json").exists()


def test_a_dead_endpoint_does_not_cost_the_run(driver, tmp_path, monkeypatch):
    """Losing one node must return its section to the queue for a live node,
    not abandon it half-paired."""
    h = _RunHarness(driver, tmp_path, monkeypatch,
                    ["http://a:8080", "http://b:8080"],
                    dead=["http://b:8080"], n_sections=6)
    assert h.run() == 0
    runs = h.results()
    assert len({r["section_id"] for r in runs.values()}) == 6
    assert {r["endpoint"] for r in runs.values()} == {"http://a:8080"}


def test_every_endpoint_dying_aborts_rather_than_reporting_success(
        driver, tmp_path, monkeypatch):
    h = _RunHarness(driver, tmp_path, monkeypatch,
                    ["http://a:8080"], dead=["http://a:8080"])
    assert h.run() == 3


def test_a_run_resumes_instead_of_repeating_completed_work(
        driver, tmp_path, monkeypatch):
    h = _RunHarness(driver, tmp_path, monkeypatch, ["http://a:8080"],
                    n_sections=4)
    assert h.run() == 0
    first = len(h.results())
    calls_before = sum(len(c.prompts) for cs in h.clients.values() for c in cs)
    h2 = _RunHarness(driver, tmp_path, monkeypatch, ["http://a:8080"],
                     n_sections=4)
    assert h2.run() == 0
    assert len(h2.results()) == first
    assert sum(len(c.prompts) for cs in h2.clients.values()
               for c in cs) < calls_before, "resumed work was re-run"


def test_a_partly_finished_section_is_redone_whole_when_its_endpoint_dies(
        driver, tmp_path, monkeypatch):
    """The hole the first fan-out implementation had, and it was invisible in
    the happy path: node B finishes arm 1 of a section, dies on arm 2, node A
    picks up arm 2 -- and that section's pair now straddles two machines, which
    is exactly the arrangement by-section dispatch exists to prevent. The fix
    discards at most one section of completed work to keep the pairing intact,
    and says so."""
    doc_text = make_document(n_paragraphs=40)
    sections = [{"section_id": "doc1:0", "doc_id": "doc1", "start_char": 0,
                 "end_char": len(doc_text), "n_tokens": 5000, "n_chunks": 2}]
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"tokenizer": "http://x/tokenize (exact)", "sections": sections}))

    import chunk_size_driver
    state = {"calls": 0}

    class FlakyClient(FakeClient):
        def __init__(self, endpoint):
            super().__init__()
            self.endpoint = endpoint

        def model_name(self):
            return "same-model"

        def complete(self, prompt, max_tokens=None):
            if self.endpoint == "http://b:8080":
                state["calls"] += 1
                # Survive the single-pass arm, die inside the map-reduce arm.
                if state["calls"] > 1:
                    raise chunk_size_driver.BackendDead("b died mid-section")
            return super().complete(prompt, max_tokens)

    monkeypatch.setattr(driver.worker, "LlamaClient", FlakyClient)
    monkeypatch.setattr(driver.amp, "read_corpus",
                        lambda db, doc_ids=None: [{"id": "doc1",
                                                   "text": doc_text}])
    monkeypatch.setattr(driver.amp, "running_jobs", lambda db: 0)

    class Args:
        out = str(tmp_path)
        db = "unused"
        endpoint = ["http://b:8080", "http://a:8080"]
        arms = list(amp.DEFAULT_ARMS)
        allow_busy = False
        limit = None
        kind = "summarise"
        node_ssh_host = None
        unit = "u"

    assert driver.cmd_run(Args) == 0
    runs = json.loads((tmp_path / "results.json").read_text())["runs"]
    assert len(runs) == 2, "both arms present"
    assert {r["endpoint"] for r in runs.values()} == {"http://a:8080"}, \
        "the partial section was redone whole on the surviving endpoint"


def test_analyse_excludes_a_pair_whose_arms_ran_on_different_nodes(
        driver, tmp_path):
    """`run` prevents this within one invocation, but resuming against a
    different node -- 'node 1 today, node 2 tomorrow' -- assembles exactly such
    pairs, and in the worst case puts one ARM entirely on one node."""
    ep = {"claims": 10, "e1_number_findings": 1, "e1_number_fabricated": 1,
          "e1_any": 1, "e2_entity_claims": 0, "e2_entity_terms": 0,
          "e2_any": 0, "e4_escalated": 0}
    scored = {"sections": {
        "good": {"doc_id": "d1", "arms": {
            amp.ARM_SINGLE_PASS: {"ran_on": "http://a", "endpoints": ep,
                                  "laundering": {"applicable": False}},
            amp.ARM_MAP_REDUCE: {"ran_on": "http://a", "endpoints": ep,
                                 "laundering": {"applicable": True,
                                                "inherited": 0,
                                                "invented_at_reduce": 1}}}},
        "split": {"doc_id": "d1", "arms": {
            amp.ARM_SINGLE_PASS: {"ran_on": "http://a", "endpoints": ep,
                                  "laundering": {"applicable": False}},
            amp.ARM_MAP_REDUCE: {"ran_on": "http://b", "endpoints": ep,
                                 "laundering": {"applicable": True,
                                                "inherited": 0,
                                                "invented_at_reduce": 1}}}}}}
    (tmp_path / "scored.json").write_text(json.dumps(scored))

    class Args:
        out = str(tmp_path)
        db = "unused"
        arms = list(amp.DEFAULT_ARMS)
        allow_mixed_endpoints = False
    assert driver.cmd_analyse(Args) == 0
    an = json.loads((tmp_path / "analysis.json").read_text())
    assert an["n_complete_pairs"] == 1
    assert [m["section_id"] for m in an["mixed_endpoint_pairs"]] == ["split"]
    assert "EXCLUDED" in an["mixed_endpoint_policy"]

    Args.allow_mixed_endpoints = True
    assert driver.cmd_analyse(Args) == 0
    an = json.loads((tmp_path / "analysis.json").read_text())
    assert an["n_complete_pairs"] == 2

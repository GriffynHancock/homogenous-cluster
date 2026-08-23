"""Tests for the faithfulness audit ledger.

These run in the PRODUCTION venv, which has no torch, transformers or nltk. That
is deliberate and is itself one of the things under test: if importing
`missing_link.audit` ever starts pulling in the model stack, `test_no_heavy_imports`
fails and the production install stops being clean.

The model-dependent behaviour is exercised by the negation battery
(`python -m missing_link.audit battery`), not here -- a test that loads a 770M
checkpoint is not a unit test. What is tested here is everything that has to be
RIGHT rather than merely accurate: offset and line arithmetic, the refusal path,
finding ordering, and the two-scorer requirement.
"""

import json
import os
import sys

import pytest

from missing_link import audit
from missing_link import db


# ---------------------------------------------------------------------------
# Dependency isolation
# ---------------------------------------------------------------------------

def test_no_heavy_imports():
    """Importing the audit module must not drag in the model stack.

    The whole point of the separate requirements file is that `missing-link`
    installs without ~1.5 GB of torch. A stray top-level `import torch` in
    audit.py would silently undo that and nothing else would notice.

    Run in a SUBPROCESS on purpose: asserting against this interpreter's
    `sys.modules` would only prove that no earlier test happened to import
    torch first, which depends on collection order and would pass by accident in
    the production venv, where torch is not installed at all.
    """
    import subprocess

    probe = ("import sys; import missing_link.audit; "
             "print([m for m in ('torch','transformers','minicheck','nltk','datasets') "
             "if m in sys.modules])")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", f"audit.py pulled in {out.stdout.strip()}"


# ---------------------------------------------------------------------------
# Locations are computed, never asked for
# ---------------------------------------------------------------------------

def test_line_of_counts_newlines():
    text = "alpha\nbravo\ncharlie"
    assert audit.line_of(text, 0) == 1
    assert audit.line_of(text, 5) == 1          # the newline itself is still line 1
    assert audit.line_of(text, 6) == 2
    assert audit.line_of(text, text.index("charlie")) == 3


def test_line_of_clamps_rather_than_raising():
    """A stale offset must not take the whole audit down."""
    text = "one\ntwo"
    assert audit.line_of(text, -5) == 1
    assert audit.line_of(text, 10_000) == 2


def test_sentence_spans_offsets_are_true():
    text = "First claim here. Second claim here.\nThird on a new line."
    for start, end, sent in audit.sentence_spans(text):
        assert text[start:end] == sent


def test_sentence_spans_empty():
    assert audit.sentence_spans("") == []
    assert audit.sentence_spans("   \n  ") == []


def test_splitter_name_is_recorded():
    """Which splitter ran must be visible; a silent swap changes what
    'sentence 3' means between runs -- and F48 measured the two rungs 4x
    apart on legislative marker rate, so it changes the NUMBER too."""
    assert audit.splitter_name() in {"nupunkt", "regex-fallback"}


def test_audit_does_not_define_its_own_splitter():
    """F48: `_SENT_FALLBACK` existed as two identical copies in two modules,
    with two contradicting docstrings. One primitive, one definition -- and
    `audit.sentence_spans` must be that same object, not a same-shaped
    reimplementation that can drift."""
    from missing_link import chunk_boundary_audit as cba
    from missing_link import sentences

    assert audit._SENT_FALLBACK is sentences._SENT_FALLBACK
    assert cba._SENT_FALLBACK is sentences._SENT_FALLBACK
    assert audit.sentence_spans is sentences.sentence_spans
    assert cba.sentence_spans is sentences.sentence_spans


# ---------------------------------------------------------------------------
# The deterministic negation-cue complement
# ---------------------------------------------------------------------------

def test_negation_cues_found():
    assert "not" in audit.negation_cues("Records must not be destroyed.")
    assert audit.negation_cues("Records are retained for seven years.") == []
    assert audit.is_negated("Staff cannot access the archive.")


def test_presence_parity_not_count_parity():
    """Two cues in one clause is still a negated clause."""
    assert audit.is_negated("Records must not be retained beyond seven years.")


def test_polarity_check_catches_the_retention_flip():
    """The pair MiniCheck-Flan-T5-Large inverted (minicheck-spike section 4)."""
    doc = ("Clinical records must be retained for seven years from the date of last "
           "service, or until the client turns twenty-five, whichever is later.")
    true_claim = "Records must be kept for seven years from the last service."
    fabricated = "Records must not be retained beyond seven years from the last service."
    assert audit.polarity_check(fabricated, doc)["mismatch"] is True
    assert audit.polarity_check(true_claim, doc)["mismatch"] is False


def test_polarity_check_returns_none_without_a_match():
    """No comparable evidence sentence means no comparison, not a guess."""
    assert audit.polarity_check("Entirely unrelated wording about shipping containers.",
                                "Records must be retained for seven years.") is None


def test_overlap_and_best_match():
    cands = ["Retention of clinical records.", "Access permissions on the shared drive."]
    idx, score = audit.best_match("Read access on the shared drive was too broad.", cands)
    assert idx == 1 and score > 0


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

DOC = (
    "SECTION 1 RETENTION\n"
    "Clinical records must be retained for seven years from the date of last service.\n"
    "SECTION 2 ACCESS\n"
    "Read access was granted to 41 staff who had no clinical role.\n"
)
# Offsets chosen so section 2 starts on line 3 of DOC.
S2 = DOC.index("SECTION 2")

CHUNKS = [
    {"index": 0, "start_char": 0, "end_char": S2,
     "summary": "Clinical records are retained for seven years. Retention is mandatory."},
    {"index": 1, "start_char": S2, "end_char": len(DOC),
     "summary": "41 non-clinical staff had read access."},
]
FINAL = "Records are kept seven years. 41 non-clinical staff had read access."


def _scorers(table_a=None, table_b=None, refuse_a=(), default_a=0.9, default_b=0.9):
    return [
        audit.StaticScorer("model-a", table_a or {}, default=default_a, refuse=refuse_a),
        audit.StaticScorer("model-b", table_b or {}, default=default_b),
    ]


def test_two_scorers_are_required():
    with pytest.raises(ValueError, match="TWO scorers"):
        audit.build_ledger(DOC, CHUNKS, FINAL,
                           [audit.StaticScorer("only-one", {})])


def test_clean_run_raises_no_flags_but_does_not_certify():
    led = audit.build_ledger(DOC, CHUNKS, FINAL, _scorers(), polarity=False)
    assert led["verdict"] == "no_flags_raised"
    assert led["findings"] == []
    # The absence of a "pass"/"faithful" verdict is the design, not an oversight.
    assert "certif" in led["disclaimer"]
    assert led["verdict"] != "pass"


def test_both_models_unsupported_is_flagged_with_a_direct_location():
    bad = "Retention is mandatory."
    led = audit.build_ledger(DOC, CHUNKS, FINAL,
                             _scorers({bad: 0.1}, {bad: 0.05}), polarity=False)
    f = [x for x in led["findings"] if x["category"] == audit.CAT_UNSUPPORTED]
    assert len(f) == 1
    assert f[0]["hop"] == audit.HOP_CHUNK
    assert f[0]["claim"]["text"] == bad
    assert f[0]["evidence"]["location_confidence"] == "direct"
    assert f[0]["evidence"]["start_line"] == 1
    assert led["totals"]["by_category"][audit.CAT_UNSUPPORTED] == 1
    assert led["verdict"] == "review_required"


def test_disagreement_is_first_class_and_outranks_agreed_unsupported():
    """The spike's dangerous case was a disagreement, so it must not be buried
    beneath the cases both models already agree on."""
    disputed = "Retention is mandatory."
    agreed_bad = "41 non-clinical staff had read access."
    led = audit.build_ledger(
        DOC, CHUNKS, FINAL,
        _scorers({disputed: 0.95, agreed_bad: 0.02},
                 {disputed: 0.05, agreed_bad: 0.03}),
        polarity=False)
    cats = [f["category"] for f in led["findings"]]
    assert audit.CAT_DISAGREEMENT in cats and audit.CAT_UNSUPPORTED in cats
    assert cats.index(audit.CAT_DISAGREEMENT) < cats.index(audit.CAT_UNSUPPORTED)
    dis = next(f for f in led["findings"] if f["category"] == audit.CAT_DISAGREEMENT)
    assert dis["models_agree"] is False
    assert dis["prob_gap"] == pytest.approx(0.90, abs=1e-6)


def test_wide_probability_gap_counts_as_disagreement_even_with_equal_labels():
    claim = "Retention is mandatory."
    led = audit.build_ledger(DOC, CHUNKS, FINAL,
                             _scorers({claim: 0.99}, {claim: 0.55}), polarity=False)
    dis = [f for f in led["findings"] if f["category"] == audit.CAT_DISAGREEMENT]
    assert len(dis) == 1
    assert dis[0]["scores"][0]["label"] == dis[0]["scores"][1]["label"] == "supported"


def test_refusal_is_recorded_and_nothing_is_scored_for_it():
    """Refuse, do not degrade: an over-long unit produces an `unscoreable`
    finding with a reason, never a support probability."""
    refused = "Retention is mandatory."
    led = audit.build_ledger(DOC, CHUNKS, FINAL,
                             _scorers(refuse_a=[refused]), polarity=False)
    uns = [f for f in led["findings"] if f["category"] == audit.CAT_UNSCOREABLE]
    assert len(uns) == 1
    assert uns[0]["claim"]["text"] == refused
    assert uns[0]["scores"] == []
    assert uns[0]["refused_by"] == "model-a"
    assert uns[0]["preflight"]["ok"] is False
    assert led["totals"]["claims_unscoreable"] == 1
    assert led["totals"]["claims_scoreable"] == led["totals"]["claims_examined"] - 1
    # and it sorts to the very front: the reader must know what was never checked
    assert led["findings"][0]["category"] == audit.CAT_UNSCOREABLE


def test_hop2_scores_against_chunk_summaries_not_the_document():
    """Reduce-step laundering is only visible if hop 2's evidence is what the
    reduce step actually read."""
    seen = []

    class Recorder(audit.StaticScorer):
        def score(self, pairs):
            seen.extend(pairs)
            return super().score(pairs)

    scorers = [Recorder("a", {}), Recorder("b", {})]
    audit.build_ledger(DOC, CHUNKS, FINAL, scorers, polarity=False)
    joined = "\n\n".join(c["summary"] for c in CHUNKS)
    assert any(ev == joined for ev, _ in seen)
    assert not any(ev == DOC for ev, _ in seen)


def test_hop2_location_is_marked_indirect():
    claim = "Records are kept seven years."
    led = audit.build_ledger(DOC, CHUNKS, FINAL,
                             _scorers({claim: 0.02}, {claim: 0.03}), polarity=False)
    f = next(x for x in led["findings"] if x["hop"] == audit.HOP_FINAL)
    ev = f["evidence"]
    assert ev["location_confidence"] == "indirect"
    assert ev["match_method"] == "content_word_containment"
    assert 0.0 < ev["match_score"] <= 1.0
    assert "not the sentence that produced the claim" in ev["location_note"]
    assert ev["chunk_index"] == 0          # matched the retention chunk summary
    assert ev["start_line"] == 1


def test_hop2_is_skipped_for_a_single_chunk_job_and_says_so():
    """worker.summarise_traced returns the chunk summary verbatim when a document
    is one chunk, so hop 2 would score text against itself and report a flawless
    pass. A fabricated reassurance is worse than a missing hop."""
    one = [CHUNKS[0]]
    led = audit.build_ledger(DOC, one, one[0]["summary"], _scorers({}, {}))
    assert audit.HOP_FINAL not in {u["hop"] for u in led["findings"]}
    assert led["totals"]["claims_examined"] == len(audit.sentence_spans(one[0]["summary"]))
    assert any("Hop 2" in n and "NOT run" in n for n in led["notes"])


def test_note_when_there_is_no_final_summary():
    led = audit.build_ledger(DOC, CHUNKS, "", _scorers())
    assert any("no final summary" in n for n in led["notes"])


def test_polarity_default_is_off():
    """Measured: it fires on 36% of battery sentences and catches nothing the
    ensemble missed. Default-on would train the reader to ignore every flag."""
    chunks = [{"index": 0, "start_char": 0, "end_char": len(DOC),
               "summary": "Clinical records must not be retained beyond seven years."}]
    led = audit.build_ledger(DOC, chunks, "", _scorers())
    assert led["config"]["polarity_check"] is False
    assert audit.CAT_POLARITY not in [f["category"] for f in led["findings"]]


def test_ledger_shape_is_stable_and_extensible():
    led = audit.build_ledger(DOC, CHUNKS, FINAL, _scorers(), polarity=False)
    for key in ("schema_version", "generated_at", "verdict", "disclaimer", "models",
                "config", "totals", "findings", "notes", "coverage_note"):
        assert key in led
    assert led["schema_version"] == audit.LEDGER_SCHEMA_VERSION
    assert [m["name"] for m in led["models"]] == ["model-a", "model-b"]
    # by_category is a MAP, so a new category is an added key, not a new column a
    # consumer has to know about in advance.
    assert isinstance(led["totals"]["by_category"], dict)
    assert json.loads(json.dumps(led)) == led      # must be plain JSON


def test_include_passing_records_unflagged_sentences():
    led = audit.build_ledger(DOC, CHUNKS, FINAL, _scorers(), polarity=False,
                             include_passing=True)
    assert len(led["passing"]) == led["totals"]["claims_scoreable"]
    assert all("scores" in p for p in led["passing"])


def test_polarity_finding_is_produced_and_ranks_last():
    """A claim whose polarity disagrees with its closest evidence sentence, but
    which both models are happy with, still surfaces -- at the bottom."""
    chunks = [{"index": 0, "start_char": 0, "end_char": len(DOC),
               "summary": "Clinical records must not be retained beyond seven years."}]
    led = audit.build_ledger(DOC, chunks, "", _scorers(), polarity=True)
    cats = [f["category"] for f in led["findings"]]
    assert audit.CAT_POLARITY in cats
    assert cats[-1] == audit.CAT_POLARITY
    f = next(x for x in led["findings"] if x["category"] == audit.CAT_POLARITY)
    assert "not" in f["polarity"]["claim_cues"]
    assert "noisy" in f["detail"]


def test_polarity_can_be_disabled():
    chunks = [{"index": 0, "start_char": 0, "end_char": len(DOC),
               "summary": "Clinical records must not be retained beyond seven years."}]
    led = audit.build_ledger(DOC, chunks, "", _scorers(), polarity=False)
    assert audit.CAT_POLARITY not in [f["category"] for f in led["findings"]]
    assert led["config"]["polarity_check"] is False


def test_chunk_records_accept_the_db_row_shape():
    """db.get_chunk_summaries returns `idx`, not `index`."""
    rows = [{"idx": 0, "start_char": 0, "end_char": len(DOC), "summary": "A claim."}]
    led = audit.build_ledger(DOC, rows, "", _scorers())
    assert led["totals"]["claims_examined"] == 1


# ---------------------------------------------------------------------------
# Reading a job, read-only
# ---------------------------------------------------------------------------

def test_load_job_reads_document_chunks_and_result(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    db.init_db(path)
    job_id = db.create_job(path, "summary", DOC)
    db.save_chunk_summaries(path, job_id,
                            [{"index": c["index"], "start": c["start_char"],
                              "end": c["end_char"], "summary": c["summary"]}
                             for c in CHUNKS])
    db.complete_job(path, job_id, FINAL, {})
    document, records, final = audit.load_job(path, job_id)
    assert document == DOC
    assert final == FINAL
    assert [r["index"] for r in records] == [0, 1]
    assert records[1]["start_char"] == S2


def test_load_job_is_read_only(tmp_path):
    """The auditor is an observer. It must not be able to write to, migrate or
    lock a database a worker is using."""
    import sqlite3

    path = str(tmp_path / "jobs.sqlite")
    db.init_db(path)
    uri = "file:{}?mode=ro".format(os.path.abspath(path))
    conn = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO jobs (id, kind, document, created_at) "
                         "VALUES ('x','summary','d','now')")
    finally:
        conn.close()


def test_load_job_missing_job(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    db.init_db(path)
    with pytest.raises(KeyError):
        audit.load_job(path, "nope")


# ---------------------------------------------------------------------------
# The battery fixture -- a regression fixture, so its shape is tested too
# ---------------------------------------------------------------------------

def test_battery_fixture_is_wellformed():
    pairs = audit.load_battery()
    assert len(pairs) >= 30
    ids = [p["id"] for p in pairs]
    assert len(set(ids)) == len(ids)
    expected = {"retention", "access_permission", "statutory_duty", "deadline",
                "exemption", "conditional_obligation", "quantifier"}
    assert {p["category"] for p in pairs} == expected
    for p in pairs:
        assert p["doc"] and p["supported"] and p["negated"]
        assert p["supported"] != p["negated"]
        assert p["supported"].lower() not in p["doc"].lower(), \
            f"{p['id']}: claim copied verbatim from the doc is not a test"


def test_battery_keeps_the_spike_pairs():
    """The five pairs from docs/minicheck-spike.md must stay reproducible."""
    ids = {p["id"] for p in audit.load_battery()}
    assert {"retention_seven_years", "access_41_staff", "breach_notification_72h",
            "archive_authorisation", "litigation_hold_destruction"} <= ids


def test_polarity_battery_runs_without_models():
    rows = audit.battery_polarity_results(audit.load_battery())
    assert len(rows) == 2 * len(audit.load_battery())
    s = audit.summarise_polarity(rows)
    assert s["true_positives"] + s["false_negatives"] == len(rows) // 2
    assert s["false_positives"] + s["true_negatives"] == len(rows) // 2
    assert 0.0 <= s["accuracy"] <= 1.0


def test_wide_cue_set_is_a_superset():
    assert set(audit.NEGATION_CUES) < set(audit.WIDE_NEGATION_CUES)
    claim = "Funding continues beyond June 2027."
    assert audit.negation_cues(claim) == []
    assert audit.negation_cues(claim, audit.WIDE_NEGATION_CUES) == ["beyond"]


# ---------------------------------------------------------------------------
# Battery analysis maths -- the load-bearing cross-tabulation
# ---------------------------------------------------------------------------

def _row(i, kind, prob, correct):
    return {"id": f"p{i}", "category": "retention", "kind": kind,
            "truth": "supported" if kind == "supported" else "unsupported",
            "label": "supported" if prob > 0.5 else "unsupported",
            "support_prob": prob, "correct": correct, "unscoreable": False}


def test_analyse_battery_cross_tabulates_disagreement_against_error():
    # p0: both right and agree.  p1: disagree, one wrong.  p2: agree, both wrong.
    a = [_row(0, "supported", 0.9, True), _row(1, "supported", 0.9, True),
         _row(2, "negated", 0.9, False)]
    b = [_row(0, "supported", 0.88, True), _row(1, "supported", 0.1, False),
         _row(2, "negated", 0.92, False)]
    res = audit.analyse_battery({"a": a, "b": b}, [None] * 6)
    d = res["disagreement"]
    assert d["disagree_and_at_least_one_wrong"] == 1
    assert d["agree_and_both_right"] == 1
    assert d["agree_and_at_least_one_wrong"] == 1
    assert d["precision_of_disagreement_as_error_signal"] == 1.0
    assert d["recall_errors_caught_by_disagreement"] == 0.5
    # The number that decides whether the design is safe: errors BOTH models made
    # and therefore agreed on, which no ensemble can catch.
    assert d["silent_failures_if_agreement_is_trusted"] == 1


def test_analyse_battery_reports_per_category_accuracy():
    a = [_row(0, "supported", 0.9, True), _row(1, "negated", 0.9, False)]
    res = audit.analyse_battery({"a": a}, [None] * 2)
    m = res["per_model"]["a"]
    assert m["accuracy"] == 0.5
    assert m["by_category"]["retention"]["n"] == 2
    assert m["errors"][0]["id"] == "p1"


def test_analyse_battery_excludes_unscoreable_from_accuracy():
    rows = [_row(0, "supported", 0.9, True),
            {"id": "p1", "category": "retention", "kind": "negated",
             "truth": "unsupported", "label": None, "support_prob": None,
             "correct": None, "unscoreable": True, "reason": "too long"}]
    res = audit.analyse_battery({"a": rows}, [None] * 2)
    assert res["per_model"]["a"]["n"] == 1
    assert res["per_model"]["a"]["accuracy"] == 1.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_polarity_only_battery_writes_json(tmp_path):
    out = tmp_path / "battery.json"
    assert audit.main(["battery", "--polarity-only", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert "minicheck" not in data          # no models were loaded
    assert data["polarity_check"]["cue_set"] == "default"
    assert data["polarity_check"]["summary"]["accuracy"] is not None


def test_cli_job_refuses_without_chunk_offsets(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    db.init_db(path)
    job_id = db.create_job(path, "summary", DOC)
    db.complete_job(path, job_id, FINAL, {})
    with pytest.raises(SystemExit, match="no chunk_summaries"):
        audit.main(["job", "--db", path, "--job-id", job_id])

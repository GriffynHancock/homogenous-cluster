"""Section-level citation (Tier B, docs/citation-research.md).

WHAT THESE TESTS DO AND DO NOT PROVE. They prove the parser, the label binding
and the page. They CANNOT prove the model will emit the markers at all -- that
needs an inference run, and the one-job test to confirm it is written up in the
report accompanying this change. Read every assertion below as being about
Missing Link's half of the seam, per F34's lesson that a test count is not
evidence of working software.
"""
import os
import re
import sqlite3
import uuid

import pytest

from missing_link import db, worker


# --- prompt ------------------------------------------------------------------

def test_reduce_prompt_asks_for_section_markers_by_default():
    p = worker.build_reduce_prompt("summarise", ["alpha", "beta"])
    assert "[Section 1]" in p and "[Section 2]" in p
    assert "never invent a section number" in p


def test_reduce_prompt_off_is_byte_identical_to_pre_citation_text():
    """CITE_SECTIONS=False must restore exactly the old prompt.

    This is the escape hatch CLAUDE.md's "prefer reversible changes" asks for:
    if the coherence check on real output goes badly, one constant reverts the
    prompt with no other edit. The literal below is the pre-change template.
    """
    off = worker.build_reduce_prompt("summarise", ["alpha", "beta"],
                                     cite_sections=False)
    expected = (
        "Below are summaries of consecutive sections of one document. Combine "
        "them into a single coherent summary. Remove repetition caused by "
        "overlapping sections. Do not add anything not present in the "
        "sections.\n\n---\n[Section 1]\nalpha\n\n[Section 2]\nbeta\n---\n\n"
        "Combined summary:")
    assert off == expected


def test_citation_clause_does_not_disturb_the_instruction_slot():
    """Operator guidance keeps its exact text and its exact position.

    Guidance is rendered into both map and reduce prompts, and the reduce
    template gained a second slot next to it. The map prompt must be untouched,
    and the guidance clause must still appear verbatim in the reduce prompt.
    """
    guidance = "Focus on the retention schedule."
    clause = " Additional instructions from the operator for this job: " + guidance

    # Map side: completely unaffected by citations.
    assert clause in worker.build_prompt("summarise", "x", instruction=guidance)
    assert worker.build_prompt("summarise", "x", instruction=None) == \
        worker.build_prompt("summarise", "x")

    # Reduce side: guidance verbatim, and still ahead of the citation clause.
    p = worker.build_reduce_prompt("report", ["a", "b"], instruction=guidance)
    assert clause in p
    assert p.index(clause) < p.index("never invent a section number")

    # And with citations off, the reduce prompt is what it always was.
    off = worker.build_reduce_prompt("report", ["a", "b"], instruction=guidance,
                                     cite_sections=False)
    assert clause in off
    assert "Section" not in off.split("---")[0]


@pytest.mark.parametrize("kind", sorted(worker.REDUCE_PROMPTS))
def test_every_kind_carries_both_slots(kind):
    """A new task profile that forgets {citation} would silently lose citations
    for that kind only -- the kind of divergence nobody notices for months."""
    tmpl = worker.REDUCE_PROMPTS[kind]
    assert "{instruction}" in tmpl and "{citation}" in tmpl and "{summaries}" in tmpl
    # And it renders both ways without a KeyError.
    worker.build_reduce_prompt(kind, ["a"], cite_sections=True)
    worker.build_reduce_prompt(kind, ["a"], cite_sections=False)


# --- the label is bound to the chunk, not to list position -------------------

def test_section_label_comes_from_chunk_index_not_position():
    """THE OFF-BY-ONE THIS FEATURE MUST NOT HAVE.

    A record list whose positions do not match its chunk indices (what a resume
    path reusing persisted rows could hand us) must still be labelled by chunk
    index. If this regressed, every citation on a resumed job would point one
    section off -- confidently, at the wrong span.
    """
    records = [{"index": 3, "start": 0, "end": 9, "summary": "fourth"},
               {"index": 7, "start": 9, "end": 18, "summary": "eighth"}]
    p = worker.build_reduce_prompt("summarise", records)
    assert "[Section 4]\nfourth" in p
    assert "[Section 8]\neighth" in p
    assert "[Section 1]\nfourth" not in p
    assert "[Section 2]\neighth" not in p


def test_db_row_spelling_is_accepted_too():
    """db.get_chunk_summaries says idx/start_char/end_char; worker records say
    index/start/end. Both are the same three facts and both must resolve."""
    rows = [{"idx": 0, "start_char": 0, "end_char": 5, "summary": "a"},
            {"idx": 1, "start_char": 5, "end_char": 9, "summary": "b"}]
    assert "[Section 2]\nb" in worker.build_reduce_prompt("summarise", rows)
    out = worker.parse_section_citations("x [Section 2]", rows)
    assert out["segments"][-1] == {"kind": "cite", "section": 2, "index": 1,
                                   "start": 5, "end": 9}


def test_record_without_an_index_is_refused_not_guessed():
    with pytest.raises(ValueError, match="index"):
        worker.build_reduce_prompt("summarise", [{"summary": "a"}])


def test_summarise_traced_labels_by_index_end_to_end():
    """The call site used to do [r["summary"] for r in records], throwing the
    identity away one line before it mattered. Assert the reduce prompt the
    client actually receives is labelled and section-numbered."""
    class Client:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt, max_tokens=None):
            self.prompts.append(prompt)
            return f"summary {len(self.prompts)}"

    client = Client()
    doc = "word " * 20000  # long enough to force map + reduce
    final, records = worker.summarise_traced("summarise", doc, client)
    reduce_prompt = client.prompts[-1]
    assert len(records) > 1
    for r in records:
        assert f"[Section {r['index'] + 1}]" in reduce_prompt
    assert "never invent a section number" in reduce_prompt


# --- parsing: the three outcomes ---------------------------------------------

RECORDS = [
    {"index": 0, "start": 0, "end": 100, "summary": "s0"},
    {"index": 1, "start": 90, "end": 190, "summary": "s1"},
    {"index": 2, "start": 180, "end": 260, "summary": "s2"},
]


def _plain(out):
    return "".join(s["text"] for s in out["segments"] if s["kind"] == "text")


def test_valid_marker_resolves_to_the_persisted_span():
    out = worker.parse_section_citations("The policy was revised. [Section 2]", RECORDS)
    assert out["has_citations"] is True
    assert out["valid_count"] == 1 and out["marker_count"] == 1
    assert out["cited_sections"] == [2]
    cite = [s for s in out["segments"] if s["kind"] == "cite"][0]
    # Exact lookup, NOT a fuzzy match: section 2 is records[1], offsets 90-190.
    assert (cite["index"], cite["start"], cite["end"]) == (1, 90, 190)
    assert out["dropped_count"] == 0 and out["unparsed_count"] == 0


def test_multiple_markers_on_one_paragraph():
    out = worker.parse_section_citations("Both apply. [Section 1][Section 3]", RECORDS)
    assert out["valid_count"] == 2
    assert out["cited_sections"] == [1, 3]
    assert [s["index"] for s in out["segments"] if s["kind"] == "cite"] == [0, 2]


def test_case_and_whitespace_tolerance():
    out = worker.parse_section_citations("a [section 1] b [Section  2] c", RECORDS)
    assert out["valid_count"] == 2


def test_invented_marker_is_dropped_and_counted_never_rendered():
    """A section index with no record behind it must not become a link.

    This is the codebase's refuse-don't-degrade rule (F21/F34/F36/F38). A link
    to section 47 of a 3-section document would look exactly like a working
    citation and point at nothing.
    """
    text = "Claim one. [Section 2] Claim two. [Section 47] Claim three. [Section 0]"
    out = worker.parse_section_citations(text, RECORDS)
    assert out["marker_count"] == 3
    assert out["valid_count"] == 1
    assert out["dropped_count"] == 2
    assert {d["marker"] for d in out["dropped"]} == {"[Section 47]", "[Section 0]"}
    assert {d["section"] for d in out["dropped"]} == {47, 0}
    # Nothing points at a section that does not exist.
    assert all(s["section"] in (1, 2, 3)
               for s in out["segments"] if s["kind"] == "cite")
    # And the invented markers are gone from the prose, not left looking cited.
    assert "[Section 47]" not in _plain(out)
    assert "[Section 0]" not in _plain(out)
    assert "Claim three." in _plain(out)


def test_repeated_invented_marker_is_aggregated_with_a_count():
    text = "a [Section 9] b [Section 9] c [Section 9]"
    out = worker.parse_section_citations(text, RECORDS)
    assert out["dropped"] == [{"marker": "[Section 9]", "section": 9, "count": 3}]
    assert out["dropped_count"] == 3


def test_no_markers_is_a_first_class_outcome_not_an_error():
    text = "A perfectly good summary with no tags at all."
    out = worker.parse_section_citations(text, RECORDS)
    assert out["has_citations"] is False
    assert out["marker_count"] == 0
    assert out["dropped_count"] == 0 and out["unparsed_count"] == 0
    # The text survives byte-for-byte, and nothing was inferred on its behalf.
    assert _plain(out) == text
    assert out["segments"] == [{"kind": "text", "text": text}]
    assert out["cited_sections"] == []


def test_no_fuzzy_fallback_is_produced():
    """An untagged summary whose words overlap a chunk summary must NOT acquire
    a citation. A guessed location rendered like a claimed one is the exact
    thing docs/citation-research.md and this project's own rules forbid."""
    out = worker.parse_section_citations("s1 s1 s1 s1 s1", RECORDS)
    assert out["has_citations"] is False
    assert not [s for s in out["segments"] if s["kind"] == "cite"]


def test_marker_shaped_but_unparseable_is_kept_as_text_and_reported():
    text = "a [Sections 1 and 3] b [Section four] c [Section] d"
    out = worker.parse_section_citations(text, RECORDS)
    assert out["marker_count"] == 0 and out["valid_count"] == 0
    assert out["unparsed_count"] == 3
    assert {u["marker"] for u in out["unparsed"]} == {
        "[Sections 1 and 3]", "[Section four]", "[Section]"}
    # Left in place -- deleting the model's own text is worse than not linking it.
    assert _plain(out) == text


def test_bracketed_text_that_is_not_a_marker_is_untouched():
    text = "See [1] and [note] and [Sec 2] and [Sectional] plans."
    out = worker.parse_section_citations(text, RECORDS)
    assert out["marker_count"] == 0 and out["unparsed_count"] == 0
    assert _plain(out) == text


def test_partial_marker_from_a_cut_off_generation_is_left_alone():
    """Truncation raises TruncatedCompletion long before this, but a half-written
    marker must degrade to plain text rather than to a wrong link."""
    out = worker.parse_section_citations("The last thought was [Sect", RECORDS)
    assert out["marker_count"] == 0 and out["valid_count"] == 0
    assert _plain(out) == "The last thought was [Sect"


def test_empty_records_drops_every_marker():
    out = worker.parse_section_citations("a [Section 1]", [])
    assert out["valid_count"] == 0 and out["dropped_count"] == 1
    assert out["has_citations"] is False


def test_empty_or_none_result_does_not_explode():
    for value in ("", None):
        out = worker.parse_section_citations(value, RECORDS)
        assert out["has_citations"] is False and out["segments"] == []


def test_dropped_marker_does_not_leave_a_double_gap():
    out = worker.parse_section_citations("one. [Section 9] two.", RECORDS)
    assert _plain(out) == "one. two."


def test_dropping_a_marker_does_not_destroy_the_paragraph_break():
    """Caught by running this over a real 7-chunk document rather than a fixture.

    A marker at the end of a paragraph has a space before it and a blank line
    after it. Naively stripping the following whitespace welded two paragraphs
    into one -- silently reflowing the model's own prose to hide a refusal.
    """
    out = worker.parse_section_citations(
        "First para. [Section 9]\n\nSecond para. [Section 2]", RECORDS)
    assert _plain(out) == "First para.\n\nSecond para. "
    assert out["dropped_count"] == 1 and out["valid_count"] == 1


def test_drop_at_the_very_start_and_very_end():
    out = worker.parse_section_citations("[Section 9] body [Section 9]", RECORDS)
    assert _plain(out) == "body"
    assert out["dropped_count"] == 2


def test_prose_around_a_valid_marker_is_preserved_exactly():
    out = worker.parse_section_citations("before. [Section 1] after.", RECORDS)
    kinds = [s["kind"] for s in out["segments"]]
    assert kinds == ["text", "cite", "text"]
    assert out["segments"][0]["text"] == "before. "
    assert out["segments"][2]["text"] == " after."


# --- the budget guard still fires --------------------------------------------

def test_truncated_reduce_still_refuses_even_with_citations():
    """F34's guard is untouched. Markers spend the same 2048-token budget, so a
    reduce that runs out must still FAIL the job rather than store a summary
    whose last paragraphs quietly lost their citations."""
    choice = {"message": {"content": "Some summary text. [Section 1] More text"},
              "finish_reason": "length"}
    with pytest.raises(worker.TruncatedCompletion):
        worker.extract_content(choice, worker.REDUCE_MAX_TOKENS)


def test_reduce_budget_was_not_raised_to_make_room():
    """If someone raises this to fit markers, F34 says say why first."""
    assert worker.REDUCE_MAX_TOKENS == 2048


def test_citation_clause_is_a_small_fraction_of_the_prompt_budget():
    """Prompt-side cost of the clause, in the project's own words-per-token
    units. It is charged once per job, not once per chunk (unlike guidance)."""
    words = len(worker._CITATION_CLAUSE.split())
    tokens = words / worker.WORDS_PER_TOKEN
    assert tokens < 150, f"citation clause is ~{tokens:.0f} tokens"


# --- the page -----------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    path = str(tmp_path / "jobs.sqlite")
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_module
    importlib.reload(app_module)
    db.init_db(path)
    with TestClient(app_module.app) as c:
        c.db_path = path
        yield c


def _finished_job(path, result, document="x " * 400, n_sections=3):
    job_id = db.create_job(path, "summarise", document)
    records = [{"index": i, "start": i * 100, "end": (i + 1) * 100,
                "summary": f"section {i + 1} summary"}
               for i in range(n_sections)]
    db.save_chunk_summaries(path, job_id, records, model="test-model")
    db.complete_job(path, job_id, result, {"chunks": n_sections})
    return job_id


def test_job_page_renders_valid_citations_as_links_to_the_section(client):
    job_id = _finished_job(
        client.db_path,
        "The retention period changed. [Section 2]\n\nCosts rose. [Section 3]")
    html = client.get(f"/jobs/{job_id}").text
    assert 'href="#section-2"' in html
    assert 'href="#section-3"' in html
    assert 'id="section-2"' in html
    # The link's own tooltip must state the limit of the claim.
    assert "Nothing has checked that the section supports it." in html


def test_job_page_states_what_a_citation_does_not_mean(client):
    """The honesty requirement. Citations raise perceived trust even when random
    (arXiv:2501.01303) and readers verify under 25% of the time
    (arXiv:2512.12207), so the page must say plainly that a number is a claim
    about where the model looked, not a guarantee that the section supports it."""
    job_id = _finished_job(client.db_path, "A claim. [Section 1]")
    html = client.get(f"/jobs/{job_id}").text
    assert "not a check" in html
    assert "where the model looked" in html


def test_job_page_drops_invented_markers_and_says_so(client):
    job_id = _finished_job(client.db_path, "Real. [Section 1] Invented. [Section 47]")
    html = client.get(f"/jobs/{job_id}").text
    assert 'href="#section-1"' in html
    assert 'href="#section-47"' not in html
    assert "dropped" in html
    assert "[Section 47]" in html          # named, so the reader can see it happened
    assert "1 marker dropped" in html


def test_job_page_handles_a_result_with_no_markers(client):
    job_id = _finished_job(client.db_path, "An unattributed but perfectly fine summary.")
    html = client.get(f"/jobs/{job_id}").text
    assert "No citations in this result." in html
    assert "An unattributed but perfectly fine summary." in html
    assert 'class="cite"' not in html


def test_single_chunk_job_is_not_reported_as_uncited(client):
    """A one-chunk document never runs a reduce step, so it was never asked for
    markers. Saying "no citations" there would blame the model for a question
    nobody put to it."""
    job_id = _finished_job(client.db_path, "Short summary.", n_sections=1)
    html = client.get(f"/jobs/{job_id}").text
    assert "No citations in this result." not in html
    assert "Short summary." in html


def test_raw_result_endpoints_keep_the_markers_verbatim(client):
    """The page shows what can be stood behind; the exports show what the model
    actually wrote, invented markers included. Both must be available."""
    raw = "Real. [Section 1] Invented. [Section 47]"
    job_id = _finished_job(client.db_path, raw)
    assert client.get(f"/jobs/{job_id}/result").text == raw
    assert client.get(f"/api/jobs/{job_id}").json()["result"] == raw


def test_running_job_page_still_renders(client):
    """Citations are computed only for a done job with a result; a running one
    must not trip over the None."""
    job_id = db.create_job(client.db_path, "summarise", "x " * 400)
    assert client.get(f"/jobs/{job_id}").status_code == 200

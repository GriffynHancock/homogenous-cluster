"""The one sentence splitter: the offset contract, the ladder, the provenance.

WHAT THESE TESTS ARE ACTUALLY FOR
---------------------------------
`text[start:end] == sentence` is not a nicety. F46 demonstrated the citation
resolution path working end to end on real model output by resolving a label
to a span IN CODE rather than asking the model where it came from, and the
audit ledger locates every finding the same way. A splitter that returns the
right sentences at the wrong offsets would send a reviewer to the wrong half
of a legal clause, confidently. So the round trip is asserted per span, on
real production-scale text, not just on a couple of fixtures.

The second thing under test is that the FALLBACK IS VISIBLE. F45 was a
fallback quietly producing distorted numbers; F48 was nobody being able to
tell which splitter had been in play. A silent degrade here is the bug.
"""
import json
import os
import re

import pytest

from missing_link import sentences

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "missing_link", "fixtures")


# ---------------------------------------------------------------------------
# Real text. The production-scale battery documents are the longest real-shaped
# prose in the repo (~2867 words each, the size of an actual production chunk),
# and they carry the retention/exception clause shapes this project exists to
# process. Using them here rather than a hand-written paragraph is the point:
# F34 is 41 tests passing against a pipeline that had never seen a document.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_documents():
    path = os.path.join(FIXTURES, "negation_battery_prodscale.json")
    with open(path, encoding="utf-8") as fh:
        pairs = json.load(fh)["pairs"]
    docs = [p["doc"] for p in pairs if p.get("doc")]
    assert docs, "fixture must actually carry documents"
    assert max(len(d) for d in docs) > 10_000, "must be production-scale, not a snippet"
    return docs


# ---------------------------------------------------------------------------
# 1. THE OFFSET CONTRACT -- asserted on both rungs, per span, on real text
# ---------------------------------------------------------------------------

def _assert_offset_contract(text, spans):
    prev_end = -1
    for start, end, sent in spans:
        # the round trip itself
        assert text[start:end] == sent, (start, end, repr(sent))
        # offsets are inside the string that was passed in
        assert 0 <= start < end <= len(text)
        # no leading/trailing whitespace was left in or trimmed off-by-one
        assert sent == sent.strip()
        assert sent != ""
        # units do not overlap and do not go backwards -- an overlapping span
        # would make "sentence 3" ambiguous, which is the thing last_splitter
        # exists to prevent at the other scale
        assert start >= prev_end
        prev_end = end


def test_offset_round_trip_on_real_documents_nupunkt(nupunkt_splitter, real_documents):
    total = 0
    for text in real_documents:
        spans = sentences.sentence_spans(text)
        assert spans, "a production-scale document must yield sentences"
        _assert_offset_contract(text, spans)
        total += len(spans)
    assert sentences.sentence_spans.last_splitter == "nupunkt"
    assert total > 100, "the fixtures must exercise more than a handful of spans"


def test_offset_round_trip_on_real_documents_regex(regex_splitter, real_documents):
    for text in real_documents:
        spans = sentences.sentence_spans(text)
        assert spans
        _assert_offset_contract(text, spans)
    assert sentences.sentence_spans.last_splitter == "regex-fallback"


@pytest.mark.parametrize("text", [
    # the shapes that break naive offset recovery: repeated identical
    # sentences (a str.find()-based wrapper would return the first one every
    # time), runs of whitespace, unicode quotes, no trailing newline
    "Same. Same. Same.",
    "A.\n\n\n   B.",
    "“Quoted sentence.” Another one.",
    "Section 5(2) applies. Section 5(3) does not.",
    "  leading space and no terminator",
    "K.P. Dutt Pty Ltd holds the records. It must retain them.",
    "The rate is 18.55 per cent. It does not vary.",
    "one\ntwo\nthree",
])
def test_offset_round_trip_on_awkward_shapes(text):
    """Both rungs, same string, same contract. Parametrised over the specific
    shapes that a `sent_tokenize`-plus-`str.find` wrapper gets wrong -- the
    wrapper this is deliberately NOT."""
    for pinned in ("regex", "nupunkt"):
        os.environ["MISSING_LINK_SPLITTER"] = pinned
        try:
            try:
                spans = sentences.sentence_spans(text)
            except sentences.SplitterUnavailable:
                # pinning an absent splitter RAISES rather than degrading --
                # that is the contract under test elsewhere, and it means this
                # rung simply is not present on this interpreter.
                continue
            _assert_offset_contract(text, spans)
        finally:
            os.environ.pop("MISSING_LINK_SPLITTER", None)


def test_duplicate_sentences_get_distinct_offsets(nupunkt_splitter):
    """The failure a string-matching wrapper would ship: three identical
    sentences all resolving to offset 0, so every citation points at the first
    occurrence."""
    text = "The record is retained. The record is retained. The record is retained."
    spans = sentences.sentence_spans(text)
    assert len(spans) == 3
    assert [s for s, _, _ in spans] == sorted({s for s, _, _ in spans})
    assert len({s for s, _, _ in spans}) == 3
    _assert_offset_contract(text, spans)


def test_offsets_survive_a_leading_offset_slice(nupunkt_splitter, real_documents):
    """Offsets must be relative to the string PASSED IN, not to some canonical
    form of it. The audit ledger slices a chunk out of a document and splits
    that; if the wrapper normalised or re-based anything, this diverges."""
    text = real_documents[0]
    cut = text.index(" ", 5000) + 1
    tail = text[cut:]
    for start, end, sent in sentences.sentence_spans(tail):
        assert tail[start:end] == sent
        assert text[cut + start:cut + end] == sent


# ---------------------------------------------------------------------------
# 2. THE LADDER AND THE PROVENANCE RECORD
# ---------------------------------------------------------------------------

def test_nupunkt_is_the_default_rung_when_installed():
    pytest.importorskip("nupunkt")
    os.environ.pop("MISSING_LINK_SPLITTER", None)
    assert sentences.splitter_name() == "nupunkt"


def test_last_splitter_records_which_rung_ran():
    for pinned, expected in (("regex", "regex-fallback"), ("nupunkt", "nupunkt")):
        os.environ["MISSING_LINK_SPLITTER"] = pinned
        try:
            if pinned == "nupunkt":
                try:
                    sentences.require("nupunkt")
                except sentences.SplitterUnavailable:
                    continue
            sentences.sentence_spans("A sentence. And another.")
            assert sentences.sentence_spans.last_splitter == expected
        finally:
            os.environ.pop("MISSING_LINK_SPLITTER", None)


def test_the_two_rungs_actually_disagree(real_documents):
    """If they agreed, none of this provenance machinery would be needed --
    and F48's whole result would be noise. Asserted so that a future change
    collapsing the two into the same behaviour is caught rather than
    celebrated."""
    pytest.importorskip("nupunkt")
    text = real_documents[0]
    os.environ["MISSING_LINK_SPLITTER"] = "regex"
    try:
        n_regex = len(sentences.sentence_spans(text))
    finally:
        os.environ["MISSING_LINK_SPLITTER"] = "nupunkt"
    try:
        n_nupunkt = len(sentences.sentence_spans(text))
    finally:
        os.environ.pop("MISSING_LINK_SPLITTER", None)
    assert n_regex != n_nupunkt


def test_nupunkt_emits_no_fragment_without_terminal_punctuation(
        nupunkt_splitter, real_documents):
    """F48 measured 0.0% no-terminal-punctuation units under nupunkt against
    the regex rung's structural fragments. Re-asserted here on the repo's own
    real text so the claim is not only in a document."""
    terminal = re.compile(r"[.!?][\"'’”)\]]*$")
    text = real_documents[0]
    spans = sentences.sentence_spans(text)
    unterminated = [s for _, _, s in spans if not terminal.search(s)]
    # The last unit of a document legitimately may not be terminated.
    assert len(unterminated) <= 1, unterminated[:5]


# ---------------------------------------------------------------------------
# 3. THE FALLBACK IS LOUD -- F45 was a fallback that was not
# ---------------------------------------------------------------------------

def test_fallback_warns_on_stderr_and_records_the_reason(monkeypatch, capsys):
    def boom(text):
        raise ImportError("No module named 'nupunkt'")

    monkeypatch.setattr(sentences, "_nupunkt_spans", boom)
    monkeypatch.setattr(sentences, "_warned", False)
    monkeypatch.delenv("MISSING_LINK_SPLITTER", raising=False)

    spans = sentences.sentence_spans("A sentence. And another.")

    assert spans, "the fallback must still WORK, not just complain"
    assert sentences.sentence_spans.last_splitter == "regex-fallback"
    assert "No module named 'nupunkt'" in sentences.sentence_spans.last_fallback_reason

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "nupunkt unavailable" in err
    assert "pip install nupunkt" in err, "a warning that does not name the fix is noise"
    assert "NOT comparable" in err, "the warning must say what it costs, not just what happened"


def test_fallback_warning_is_logged_too(monkeypatch, caplog):
    def boom(text):
        raise ImportError("No module named 'nupunkt'")

    monkeypatch.setattr(sentences, "_nupunkt_spans", boom)
    monkeypatch.delenv("MISSING_LINK_SPLITTER", raising=False)
    with caplog.at_level("WARNING", logger="missing_link.sentences"):
        sentences.sentence_spans("A sentence.")
    assert any("nupunkt unavailable" in r.getMessage() for r in caplog.records)


def test_pinning_nupunkt_raises_rather_than_degrading(monkeypatch):
    """A script whose output is a number must not silently get the other
    instrument. This is the guard `reprofile_corpus.py` runs on."""
    def boom(text):
        raise ImportError("No module named 'nupunkt'")

    monkeypatch.setattr(sentences, "_nupunkt_spans", boom)
    monkeypatch.setenv("MISSING_LINK_SPLITTER", "nupunkt")
    with pytest.raises(sentences.SplitterUnavailable) as exc:
        sentences.sentence_spans("A sentence.")
    assert "nupunkt" in str(exc.value)


def test_require_raises_when_the_wrong_rung_would_run(monkeypatch):
    monkeypatch.setenv("MISSING_LINK_SPLITTER", "regex")
    with pytest.raises(sentences.SplitterUnavailable):
        sentences.require("nupunkt")


def test_require_returns_the_name_when_satisfied(nupunkt_splitter):
    assert sentences.require("nupunkt") == "nupunkt"


def test_unknown_splitter_setting_is_refused(monkeypatch):
    """A typo in the env var must not fall through to a default. The whole
    point of the variable is to make the instrument explicit."""
    monkeypatch.setenv("MISSING_LINK_SPLITTER", "pysbd")
    with pytest.raises(ValueError):
        sentences.sentence_spans("A sentence.")


# ---------------------------------------------------------------------------
# 4. Degenerate input, both rungs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\n\t", None])
def test_empty_input_yields_no_spans(text):
    assert sentences.sentence_spans(text) == []


def test_import_is_cheap(monkeypatch):
    """`worker.py` and `app.py` import this transitively and neither splits on
    the hot path, so the ~2.1 s model load must be lazy. Asserted structurally
    rather than by timing: nupunkt must not be a module-level import."""
    import inspect
    src = inspect.getsource(sentences)
    module_level = [ln for ln in src.splitlines()
                    if ln.startswith("import ") or ln.startswith("from ")]
    assert not any("nupunkt" in ln for ln in module_level), module_level

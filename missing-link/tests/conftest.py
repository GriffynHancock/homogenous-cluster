"""Shared fixtures.

Currently one job: pinning the sentence splitter.

`missing_link.sentences` runs a two-rung ladder -- nupunkt if installed, a
regex fallback if not -- and F48 measured the two disagreeing 4x on
legislative clause-marker rate. A test that asserts a sentence COUNT, or the
shape of a specific splitter's output, is therefore a test about one rung and
has to say which, or it passes or fails according to what happens to be
installed in the venv it ran in. That is the same defect as F45 at test scope.
"""
import pytest

from missing_link import sentences


def _pin(monkeypatch, value):
    monkeypatch.setenv("MISSING_LINK_SPLITTER", value)
    # last_splitter is process-global by design (it is a provenance record, not
    # state); reset it so a leaked value from an earlier test cannot be read as
    # this test's answer.
    monkeypatch.setattr(sentences.sentence_spans, "last_splitter", "unknown",
                        raising=False)


@pytest.fixture
def regex_splitter(monkeypatch):
    """Force the dependency-free regex rung, whatever is installed."""
    _pin(monkeypatch, "regex")
    return sentences.REGEX_FALLBACK


@pytest.fixture
def nupunkt_splitter(monkeypatch):
    """Force nupunkt, skipping if this interpreter cannot provide it.

    Skip rather than fall back: a nupunkt assertion that silently ran on the
    regex rung is exactly the class of result this project keeps getting
    burned by.
    """
    _pin(monkeypatch, "nupunkt")
    try:
        sentences.require(sentences.NUPUNKT)
    except sentences.SplitterUnavailable as exc:
        pytest.skip(f"nupunkt not available here: {exc}")
    return sentences.NUPUNKT


@pytest.fixture(autouse=True)
def _no_ambient_auth_token(monkeypatch):
    """No test inherits a shared credential from the shell that ran pytest.

    `missing_link.app` reads ML_AUTH_TOKEN once at import (like DB_PATH and
    LLAMA_URLS), and every web fixture in this suite reloads that module. So an
    operator who had exported the production token -- entirely plausible on the
    coordinator, where it lives in /etc/default/missing-link -- would otherwise
    turn every existing web test into a 401 for reasons that have nothing to do
    with what the test is about. Tests that WANT the gate set it themselves,
    after this has run.
    """
    monkeypatch.delenv("ML_AUTH_TOKEN", raising=False)

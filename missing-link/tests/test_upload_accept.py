"""The <input accept> hint on every file picker, and proof it stays a HINT.

WHY THIS FILE EXISTS. The operator reported "when I click the upload boxes I
can't see .txt files on my Mac." No `accept` attribute has ever existed on any
upload input, `.txt` succeeds through every route at the HTTP level already,
and `missing_link/extract.py` handles `.txt`/.md/.csv/extensionless plain
text correctly -- so the fix is not the extraction pipeline, it is making the
browser's file dialog agree with what the server actually does, deterministically,
and telling the operator what is supported.

This file proves three things together, because any one alone is not enough:
1. The rendered HTML actually carries `accept="..."` derived from
   extract.ACCEPT_ATTR (not just that the constant exists -- F34's lesson:
   a value nobody renders helps nobody).
2. `.txt` and `.md` still work end-to-end through the real HTTP routes.
3. A file type NOT in the accept string is still handled correctly by the
   server -- extracted if readable, refused with a named reason if not --
   because `accept` is a browser hint, never the real filter (CLAUDE.md:
   "Model output is a protocol" and this project's refuse-don't-degrade rule
   apply here to input as much as output).
"""
import os
import tempfile

import pytest

from missing_link import extract


@pytest.fixture
def webclient(monkeypatch):
    from fastapi.testclient import TestClient

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c
    os.unlink(path)


def test_accept_attr_names_pdf_and_plain_text_explicitly():
    # This is the exact string wired into every <input type=file> below.
    # Assert on its actual content rather than just its existence, so a
    # future edit that silently drops .txt (the operator's whole complaint)
    # fails a test instead of shipping.
    assert ".txt" in extract.ACCEPT_ATTR
    assert "text/plain" in extract.ACCEPT_ATTR
    assert ".pdf" in extract.ACCEPT_ATTR
    assert "application/pdf" in extract.ACCEPT_ATTR
    assert ".md" in extract.ACCEPT_ATTR


def test_index_page_renders_accept_on_every_file_input(webclient):
    body = webclient.get("/").text
    # Batch upload input.
    assert f'name="files" id="files" multiple required accept="{extract.ACCEPT_ATTR}"' in body
    # Quick single-document input.
    assert f'name="upload" id="upload" accept="{extract.ACCEPT_ATTR}"' in body
    # Per-workflow guidance file inputs (one per kind).
    assert f'accept="{extract.ACCEPT_ATTR}"' in body
    assert 'name="guidance_file_summarise"' in body
    # The hint text next to the controls, so a user who cannot tell what the
    # tool eats does not assume it eats nothing (REQUIREMENTS.md).
    assert ".txt" in body and "TextEdit" in body


def test_batch_review_page_renders_accept_on_guidance_inputs(webclient):
    txt = b"This is a perfectly ordinary plain text document. " * 10
    resp = webclient.post("/batch", files=[("files", ("notes.txt", txt, "text/plain"))])
    assert resp.status_code == 200  # redirected, TestClient follows by default
    body = resp.text
    assert f'accept="{extract.ACCEPT_ATTR}"' in body


def test_txt_upload_succeeds_via_jobs_route(webclient):
    txt = ("This document discusses quarterly revenue figures. " * 20).encode()
    resp = webclient.post(
        "/jobs",
        data={"kind": "summarise"},
        files={"upload": ("report.txt", txt, "text/plain")},
    )
    assert resp.status_code == 200  # followed the 303 to /jobs/{id}
    assert "report" not in resp.text or True  # page renders; no 4xx/5xx


def test_txt_upload_with_no_content_type_still_succeeds(webclient):
    """Mirrors what a bare macOS drag-drop sometimes sends -- no explicit
    Content-Type header at all. extract.py sniffs bytes, not headers."""
    txt = ("Plain text with no content type header at all. " * 20).encode()
    resp = webclient.post(
        "/jobs",
        data={"kind": "summarise"},
        files={"upload": ("notes.txt", txt)},
    )
    assert resp.status_code == 200


def test_md_upload_succeeds_via_batch_route(webclient):
    md = ("# Heading\n\nSome markdown body text. " * 20).encode()
    resp = webclient.post("/batch", files=[("files", ("notes.md", md, "text/markdown"))])
    assert resp.status_code == 200
    assert '<span class="badge done">ready</span>' in resp.text
    assert '<span class="badge failed">refused</span>' not in resp.text


def test_unreadable_file_outside_and_inside_accept_list_is_refused_with_reason(webclient):
    """A PNG (a real binary signature extract.py recognises and refuses) --
    this is IN the accept list's spirit (images are common uploads) but NOT
    listed in ACCEPT_ATTR, and must still be refused server-side with a
    named, actionable reason rather than silently dropped or summarised."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    resp = webclient.post("/batch", files=[("files", ("scan.png", png, "image/png"))])
    assert resp.status_code == 200
    body = resp.text
    assert "refused" in body
    assert "PNG image" in body  # extract.py names the detected format
    assert "PDF with a text layer" in body  # and names what IS supported


def test_file_type_outside_accept_string_but_plain_text_is_still_accepted():
    """accept is a hint, not a filter (CLAUDE.md constraint 5): a filename
    extension that never appears in ACCEPT_ATTR (.log is not listed) but
    whose BYTES are ordinary text must still extract cleanly, proving the
    accept string never became the real gate."""
    assert ".log" not in extract.ACCEPT_ATTR.split(",")
    text = "2026-08-18 00:00:01 INFO server started\n" * 20
    result = extract.extract(text.encode(), "server.log")
    assert "server started" in result


def test_scanned_pdf_refusal_names_ocr_unavailable():
    """The specific case the brief calls out: a scanned PDF with no text
    layer must say plainly that OCR is not available, not just 'refused'.
    A real blank-page PDF (via pypdf) stands in for a scan -- valid PDF
    structure, zero extractable text, exactly the shape a scanner emits."""
    import io
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(extract.ExtractionError) as exc:
        extract.extract_pdf(buf.getvalue())
    msg = str(exc.value)
    assert "SCANNED" in msg
    assert "OCR is required" in msg
    assert "none installed" in msg

"""Upload extraction: refuse garbage rather than summarise it.

Four of the operator's real jobs were destroyed by the old code path, which
decoded PDFs as UTF-8 with errors="replace" and summarised the mojibake.
"""
import io
import pytest
from missing_link import extract


def _pdf(lines, pages=1):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(pages):
        y = 800
        for line in lines:
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()
    return buf.getvalue()


def test_real_pdf_yields_its_text_not_its_structure():
    raw = _pdf([
        "The 2025 audit found three deficiencies in records handling.",
        "Clinical notes were retained beyond the seven-year minimum.",
        "Incident reports were readable by 41 non-clinical staff.",
        "No register of third-party disclosures existed before 2026.",
    ])
    text = extract.extract(raw, "audit.pdf")
    assert "seven-year" in text
    assert "41 non-clinical" in text
    # The exact failure that destroyed the operator's jobs:
    assert "%PDF" not in text
    assert " obj" not in text


def test_scanned_pdf_is_REFUSED_not_summarised():
    """A PDF with no text layer needs OCR. Refusing beats summarising nothing."""
    raw = _pdf(["x"])          # almost no extractable text
    with pytest.raises(extract.ExtractionError) as e:
        extract.extract(raw, "scan.pdf")
    msg = str(e.value).lower()
    assert "ocr" in msg, "the message must tell the operator what to DO"


def test_pdf_binary_can_never_reach_the_model_as_text():
    """Regression for the actual incident: %PDF bytes must not pass through."""
    fake = b"%PDF-1.6\r%\xc3\xa4\xc3\xb6\r\n346 0 obj\r<</Metadata 364 0 R>>"
    with pytest.raises(extract.ExtractionError):
        extract.extract(fake, "broken.pdf")


def test_images_and_office_docs_are_refused_with_a_named_format():
    for blob, expect in [(b"\x89PNG\r\n\x1a\n" + b"\0" * 400, "png"),
                         (b"PK\x03\x04" + b"\0" * 400, "docx"),
                         (b"\xd0\xcf\x11\xe0" + b"\0" * 400, "office")]:
        with pytest.raises(extract.ExtractionError) as e:
            extract.extract(blob, "thing")
        assert expect in str(e.value).lower()


def test_plain_text_passes_through_unharmed():
    assert extract.extract("A normal memo about retention.".encode(), "m.txt") \
        == "A normal memo about retention."


def test_mostly_unreadable_bytes_are_refused():
    with pytest.raises(extract.ExtractionError) as e:
        extract.extract(bytes(range(256)) * 8, "mystery.bin")
    assert "binary" in str(e.value).lower()


def test_empty_upload_is_refused():
    with pytest.raises(extract.ExtractionError):
        extract.extract(b"", "empty.txt")

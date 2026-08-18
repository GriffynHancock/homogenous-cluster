"""Turn an uploaded file into text, or fail loudly.

WHY THIS EXISTS. `app.py` used to do `raw.decode("utf-8", errors="replace")` on
every upload. For a PDF -- the single most common document format in the sectors
this project targets -- that yields mojibake beginning `%PDF-1.6 %...346 0 obj`,
which was then chunked and summarised. The model dutifully described object
tables and stream keywords, and the job was marked **done**.

Four of the operator's own jobs were destroyed this way on 2026-08-17 before
anyone noticed, which is the same failure shape as F21 (empty summary stored as
success) and F34 (truncated summary stored as success): **a plausible-looking
result that is actually worthless.** The fix follows the same principle as those
two -- refuse, with an actionable message, rather than produce garbage.

DELIBERATELY NOT SILENT. A scanned PDF (images, no text layer) cannot be read
without OCR. We do not have OCR, and guessing is not an option for legally
sensitive documents, so that case raises. An operator reading "this PDF has no
text layer, it needs OCR" can act; an operator reading a summary of nothing
cannot.
"""

MIN_TEXT_CHARS = 200

# What extract() actually accepts, expressed as an HTML <input accept="...">
# value: PDF (with a text layer) or plain text -- any extension or none,
# since plain text is recognised by the ABSENCE of a binary signature, not by
# filename (see sniff()). .txt/.md/.csv are the extensions named in this
# module's own refusal message below; listed here so the browser's file
# picker and the refusal message agree on the same set.
#
# Extensions AND MIME types are both listed deliberately: per MDN, macOS and
# Windows file pickers do not honour the same half of the attribute
# consistently, and some Safari/WebKit versions have been reported to filter
# inconsistently on extension alone (Apple Developer Forums thread 761110,
# accept=".pdf,.hwp" filtered correctly on Mac but not iPad). Combining both
# is the documented mitigation.
#
# This is a browser HINT ONLY -- see the caveat in app.py where this is
# wired into templates. extract() itself performs no filename or
# accept-based check; it sniffs bytes, so nothing here can become the real
# filter by construction.
ACCEPT_ATTR = ".pdf,application/pdf,.txt,text/plain,.md,text/markdown,.csv,text/csv"

# Magic bytes. Cheap, and far more reliable than trusting a filename extension
# from a Windows desktop.
SIGNATURES = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip-container (docx/xlsx/odt)",
    b"\xd0\xcf\x11\xe0": "legacy MS Office (.doc/.xls)",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"\x1f\x8b": "gzip",
}


class ExtractionError(RuntimeError):
    """The upload could not be turned into text. Message must be actionable."""


def sniff(raw):
    for sig, name in SIGNATURES.items():
        if raw.startswith(sig):
            return name
    return None


def extract_pdf(raw):
    try:
        import io
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ExtractionError(
            "this is a PDF but pypdf is not installed: pip install pypdf"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"could not read this PDF: {type(exc).__name__}: {exc}")

    text = "\n\n".join(pages).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ExtractionError(
            f"this PDF has {len(reader.pages)} page(s) but only "
            f"{len(text)} characters of extractable text. It is almost certainly "
            "SCANNED (images, no text layer). OCR is required and this cluster has "
            "none installed -- run it through OCR first, or paste the text "
            "directly. Refusing rather than summarising an empty document."
        )
    return text


def extract(raw, filename=""):
    """Bytes -> text. Raises ExtractionError with an actionable message."""
    if not raw:
        raise ExtractionError("the uploaded file was empty")

    kind = sniff(raw)
    if kind == "pdf":
        return extract_pdf(raw)
    if kind is not None:
        raise ExtractionError(
            f"{filename or 'this file'} looks like {kind}, which cannot be read as "
            "text and is not supported. Supported: PDF with a text layer, or plain "
            "text (.txt/.md/.csv). Convert it, or paste the text directly."
        )

    # No known binary signature: treat as text, but insist it decodes cleanly
    # enough to be real prose rather than silently replacing half the bytes.
    text = raw.decode("utf-8", errors="replace")
    replaced = text.count("�")
    if replaced > max(10, len(text) // 100):
        raise ExtractionError(
            f"{filename or 'this file'} does not decode as text -- {replaced} of "
            f"{len(text)} characters were unreadable, so it is probably a binary "
            "format this cluster does not recognise. Convert it to PDF or plain "
            "text. (Refusing: summarising mojibake produces a confident, wrong "
            "answer.)"
        )
    return text

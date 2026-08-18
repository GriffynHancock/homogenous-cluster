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

HTML, ADDED LATER (found before it bit, but the same shape as F38). PDF has a
magic byte; HTML does not, so `raw.startswith(sig)` sniffing let it sail
straight through and get treated as plain text WITH THE MARKUP STILL IN IT --
tags counted as words, an attribute like `id="47"` counted as a number by
`cascade.extract_numbers`, marker density computed over navigation chrome and
`<style>` rules instead of prose. legislation.gov.au -- the exact source this
project's corpus work targets -- serves HTML as its primary format, so this
was not hypothetical. `looks_like_html()` below sniffs CONTENT, not bytes or
filename (a `.txt` saved from a browser is still HTML), and `extract_html()`
strips it with the standard library only (`html.parser`) -- no new
dependency; requirements.txt's own comment explains why this must stay
wheelhouse-light.

RTF is the same class of problem -- no reliable "is this text" signal short
of actually parsing it -- but it DOES have a magic byte (`{\rtf`), and macOS
TextEdit saves RTF by default, so it is a realistic accidental upload. It is
REFUSED rather than stripped: a partial control-word stripper would leave
some control words looking like prose, which is exactly the "plausible-
looking, actually wrong" failure this module exists to prevent.
"""
import re
from html.parser import HTMLParser

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
# accept-based check; it sniffs bytes/content, so nothing here can become the
# real filter by construction. HTML is listed here even though it is detected
# by CONTENT (looks_like_html), not by this attribute or by extension -- a
# .txt file that is actually HTML is still recognised (see extract()).
ACCEPT_ATTR = (
    ".pdf,application/pdf,.txt,text/plain,.md,text/markdown,.csv,text/csv,"
    ".html,text/html,.htm"
)

# Magic bytes. Cheap, and far more reliable than trusting a filename extension
# from a Windows desktop. RTF's is here because it is refused outright (see
# module docstring) rather than routed through the content-based HTML path.
SIGNATURES = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip-container (docx/xlsx/odt)",
    b"\xd0\xcf\x11\xe0": "legacy MS Office (.doc/.xls)",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"\x1f\x8b": "gzip",
    b"{\\rtf": "rtf",
}


class ExtractionError(RuntimeError):
    """The upload could not be turned into text. Message must be actionable."""


def sniff(raw):
    for sig, name in SIGNATURES.items():
        if raw.startswith(sig):
            return name
    return None


# --- HTML: detected by CONTENT, never by filename or extension -------------
#
# DETECTION RULE (read this before changing the thresholds below):
#   1. An explicit `<!DOCTYPE html` (any case) in the first 4KB is decisive on
#      its own -- nothing else produces that exact string by accident.
#   2. Otherwise, an opening `<html` tag near the top, PLUS a handful of other
#      recognised structural tags anywhere in the document, counts. A bare
#      `<html` with no supporting structure is NOT enough by itself.
#   3. Absent either of the above (a browser-saved HTML *fragment*, with no
#      <html>/<!DOCTYPE> wrapper), a document is still treated as HTML if it
#      contains a RUN of recognised tags: at least MIN_HTML_TAG_HITS matches
#      across at least MIN_HTML_DISTINCT_TAGS distinct tag names.
#
# FALSE-POSITIVE BEHAVIOUR, stated because it is the risk that actually
# matters here: the regex only fires on `<` immediately followed by one of a
# fixed list of real HTML tag names. Prose that uses `<` as a comparison
# ("assets < $10,000") or an angle-bracketed email ("<jane.doe@example.com>")
# never matches any of those names, so it can never cross rule 3's threshold,
# and neither rule 1 nor rule 2 fires without an actual `<!doctype`/`<html`
# token. A legislative or standards document that literally QUOTES real HTML
# tags many times over is the one case that could still cross the threshold;
# that is treated as the correct call, since such a document would need the
# same stripping for its numeric/marker density to mean anything.
_HTML_DOCTYPE_RE = re.compile(r"<!doctype\s+html", re.IGNORECASE)
_HTML_OPEN_TAG_RE = re.compile(r"<html[\s>]", re.IGNORECASE)
_HTML_STRUCT_TAG_RE = re.compile(
    r"</?(?:html|head|body|div|p|span|table|thead|tbody|tr|td|th|ul|ol|li|"
    r"h[1-6]|br|hr|a|img|script|style|title|meta|section|article|nav|"
    r"header|footer|blockquote|pre|strong|em)\b", re.IGNORECASE)
# Rule 2: a real `<html` tag is already strong evidence, so it needs less
# supporting structure than rule 3, which has no wrapper tag to lean on.
MIN_HTML_TAG_HITS_WITH_HTML_TAG = 4
MIN_HTML_TAG_HITS = 8
MIN_HTML_DISTINCT_TAGS = 3
# Cap the scan: this decides whether a document is EVEN TEXT, so it must stay
# cheap regardless of how large the upload is.
_HTML_SNIFF_WINDOW = 500_000


def looks_like_html(text):
    """Content-based HTML sniff -- see the rule and its false-positive
    behaviour in the comment block directly above."""
    if not text:
        return False
    head = text[:4096]
    if _HTML_DOCTYPE_RE.search(head):
        return True
    sample = text if len(text) <= _HTML_SNIFF_WINDOW else text[:_HTML_SNIFF_WINDOW]
    tag_hits = _HTML_STRUCT_TAG_RE.findall(sample)
    if _HTML_OPEN_TAG_RE.search(head) and len(tag_hits) >= MIN_HTML_TAG_HITS_WITH_HTML_TAG:
        return True
    distinct = {t.lower() for t in tag_hits}
    return len(tag_hits) >= MIN_HTML_TAG_HITS and len(distinct) >= MIN_HTML_DISTINCT_TAGS


_HTML_SKIP_CONTENT_TAGS = {"script", "style"}
# Closing one of these ends a block: a paragraph, heading, list item, table
# row/cell, or an explicit line break. Preserving these as newlines (rather
# than collapsing everything to spaces) is deliberate -- both the sentence
# splitter (chunk_boundary_audit.sentence_spans) and worker.chunk_spans treat
# a newline as a real boundary, which is what keeps a converted HTML document
# reading like real prose instead of hard-line-wrapped PDF text (see
# docs/chunk-boundary-measurement.md's 75.00% -> 53.85% marker-density
# distortion from exactly that kind of line damage).
_HTML_BLOCK_BREAK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th",
    "table", "thead", "tbody", "ul", "ol", "section", "article", "header",
    "footer", "blockquote", "pre", "br", "hr",
}


_HTML_DATA_WS_RE = re.compile(r"[ \t\r\n\f\v]+")


class _HTMLTextExtractor(HTMLParser):
    """Markup -> prose. Drops <script>/<style> CONTENT (not just their tags),
    and turns block-tag boundaries into newlines. Entity decoding (&amp;,
    &nbsp;, &sect;, numeric refs like &#167;) is handled by HTMLParser itself
    via convert_charrefs=True (the default) -- no separate unescape step.

    THE ONE SUBTLETY THAT MATTERS HERE: real HTML source is routinely hand-
    wrapped or pretty-printed at ~80 columns, so a single sentence inside one
    <p> often arrives across several handle_data() calls -- or one call whose
    text contains literal newlines from the SOURCE FILE's own line wrapping,
    which has nothing to do with paragraph structure. Treating those as real
    line breaks would reproduce, on HTML, exactly the "hard line-wrapped,
    PDF-extracted prose" distortion chunk_boundary_audit.py's own docstring
    and docs/chunk-boundary-measurement.md warn about (75.00% -> 53.85%
    marker density). So every whitespace run WITHIN one data chunk -- spaces,
    tabs, and any newline that is source formatting rather than a tag
    boundary -- collapses to a single space here. The ONLY newlines this
    class ever emits are the explicit ones _break() inserts when a block-
    level tag closes; those are the real, load-bearing boundaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0

    def _break(self):
        if self._skip_depth == 0:
            self._parts.append("\n")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _HTML_SKIP_CONTENT_TAGS:
            self._skip_depth += 1
        elif tag in ("br", "hr"):
            self._break()

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <br/>.
        if tag.lower() in ("br", "hr"):
            self._break()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _HTML_SKIP_CONTENT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _HTML_BLOCK_BREAK_TAGS:
            self._break()

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(_HTML_DATA_WS_RE.sub(" ", data))

    def text(self):
        return "".join(self._parts)


_BLANK_RUN_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_INLINE_WS_RUN_RE = re.compile(r"[ \t]{2,}")


def extract_html(text):
    """HTML markup -> clean prose with clause/paragraph structure intact.

    Does NOT collapse all whitespace to single spaces -- see
    _HTMLTextExtractor's docstring for why the line breaks it inserts are
    load-bearing for the sentence splitter and chunker downstream. It only:
      - drops <script>/<style> content,
      - decodes entities,
      - turns block-tag closes into newlines,
      - collapses RUNS of blank lines to one, and repeated inline whitespace
        (indentation from pretty-printed markup) to one space -- leaving
        genuine single line breaks and paragraph spacing alone.
    """
    parser = _HTMLTextExtractor()
    parser.feed(text)
    parser.close()
    out = parser.text()
    out = out.replace("\xa0", " ")            # non-breaking space -> space
    out = _INLINE_WS_RUN_RE.sub(" ", out)      # pretty-print indentation
    out = "\n".join(line.strip() for line in out.split("\n"))
    out = _BLANK_RUN_RE.sub("\n\n", out)       # runs of blank lines -> one
    return out.strip()


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


def _extract(raw, filename=""):
    """Bytes -> (text, method). method is one of "pdf", "html", "plain" --
    HOW the text was obtained, for callers that need to record provenance
    (the corpus page's extraction_method column, see corpus.py/db.py). Raises
    ExtractionError with an actionable message on any refusal. `extract()`
    below is a thin wrapper over this for callers that only need the text.
    """
    if not raw:
        raise ExtractionError("the uploaded file was empty")

    kind = sniff(raw)
    if kind == "pdf":
        return extract_pdf(raw), "pdf"
    if kind == "rtf":
        raise ExtractionError(
            f"{filename or 'this file'} is RTF (Rich Text Format), which this "
            "cluster does not parse. RTF control words look enough like plain "
            "text that a partial parser would silently leave some of them in "
            "the extracted prose -- this project refuses rather than degrades "
            "(see F38 in docs/FINDINGS.md). Open it and use 'Save As' / "
            "'Export' to plain text (.txt) -- on macOS TextEdit, which saves "
            "RTF by default, use Format > Make Plain Text first -- or paste "
            "the text directly."
        )
    if kind is not None:
        raise ExtractionError(
            f"{filename or 'this file'} looks like {kind}, which cannot be read as "
            "text and is not supported. Supported: PDF with a text layer, HTML, or "
            "plain text (.txt/.md/.csv). Convert it, or paste the text directly."
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

    # Detected by CONTENT, not filename -- a .txt saved from a browser is
    # still HTML. See looks_like_html()'s docstring for the rule and its
    # false-positive behaviour.
    if looks_like_html(text):
        stripped = extract_html(text)
        if len(stripped) < MIN_TEXT_CHARS:
            raise ExtractionError(
                f"{filename or 'this file'} looks like HTML but only "
                f"{len(stripped)} characters of prose remained after "
                "stripping markup, script and style content -- almost "
                "certainly a near-empty page shell, or a page whose content "
                "is rendered by JavaScript rather than present in the HTML "
                "itself. Save the rendered page's text directly, or paste it."
            )
        return stripped, "html"

    return text, "plain"


def extract(raw, filename=""):
    """Bytes -> text. Raises ExtractionError with an actionable message."""
    text, _method = _extract(raw, filename)
    return text


def extract_with_method(raw, filename=""):
    """Bytes -> (text, method). See _extract's docstring for `method`."""
    return _extract(raw, filename)

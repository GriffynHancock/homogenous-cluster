"""HTML and RTF uploads: F38's exact shape, found before it bit for real.

`extract.extract()` sniffs magic bytes and refuses what it cannot read -- that
guard exists because a PDF was once decoded as UTF-8 and summarised as `%PDF`
object tables while being stored `done` (F38, see test_extract.py). HTML has
no binary signature, so before this fix it sailed straight through with
markup intact: tags counted as words, an attribute value counted as a number
by `cascade.extract_numbers`, marker density computed over markup instead of
prose. legislation.gov.au -- the exact source this project's corpus work
targets -- serves HTML as its primary format, so this was not hypothetical.

These tests check three things together, because any one alone would miss the
real risk:
  1. Realistic legislative HTML round-trips to clean prose with clause
     structure intact, script/style content gone, entities decoded.
  2. The DETECTION rule -- content-based, not filename-based -- does not fire
     on prose that merely contains "<" (a comparison, an angle-bracketed
     email). A false positive here would mangle ordinary legal prose, which
     is worse than the defect being fixed.
  3. RTF is refused, not degraded, with a message naming the format.
"""
import pytest

from missing_link import cascade, chunk_boundary_audit, extract


LEGIS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Records Retention Act 2019</title>
<style>
body { font-family: sans-serif; color: #222; }
.section-number { font-weight: bold; color: #900; }
nav.breadcrumbs { display: none; }
</style>
<script>
var _ga = _ga || [];
function trackPageview() { _ga.push(['_trackPageview']); }
window.onload = trackPageview;
</script>
</head>
<body>
<nav class="breadcrumbs"><a href="/">Home</a> &gt; <a href="/acts">Acts</a></nav>
<div id="content" class="act-body">
<h1>Records Retention Act 2019</h1>
<div class="part" id="part-3">
<h2>Part 3&nbsp;&mdash;&nbsp;Retention of Records</h2>
<div class="section" id="sect-47">
<p><span class="section-number">47</span> Retention period&sect;47</p>
<p>(1)&nbsp;Subject to subsection (2), a record to which this Part applies
must be retained for a period of not less than seven years after the
date on which it was created or last modified, whichever is later.</p>
<p>(2)&nbsp;Subsection (1) does not apply where the record has been
transferred to the State Archives under section 52, or where the
Minister has, by notice in writing, exempted the record from this
requirement.</p>
<table>
<tr><th>Record class</th><th>Minimum period</th></tr>
<tr><td>Clinical notes</td><td>7 years</td></tr>
<tr><td>Incident reports</td><td>10 years</td></tr>
</table>
</div>
<div class="section" id="sect-48">
<p><span class="section-number">48</span> Exceptions</p>
<p>A record described in section 47 may be destroyed earlier than the
period specified, unless the record is the subject of a current
legal proceeding, or is otherwise required to be retained under
another written law.</p>
<ul>
<li>Health records: see also section 22 of the Health Records Act.</li>
<li>Financial records: minimum period is twenty-four&nbsp;months
under the Financial Administration Regulations.</li>
</ul>
</div>
</div>
</div>
<footer>Copyright the State of Nowhere. Last updated 2026-01-01.</footer>
</body>
</html>"""


# --- 1. Realistic legislative HTML round-trips to clean prose --------------

def test_legislative_html_round_trips_to_clean_prose_with_clause_structure():
    text, method = extract.extract_with_method(LEGIS_HTML.encode(), "act.html")
    assert method == "html"

    # Script and style CONTENT is gone, not just their tags.
    assert "_ga" not in text
    assert "trackPageview" not in text
    assert "font-family" not in text
    assert "display: none" not in text

    # No markup survives. (The lone literal ">" this document contains is
    # legitimate decoded content -- the breadcrumb "Home &gt; Acts" -- so the
    # check is for actual TAGS, not the bare character.)
    assert "<div" not in text and "<p>" not in text and "<script" not in text
    assert "</html>" not in text and "<table" not in text
    assert "Home > Acts" in text  # the decoded entity IS real content

    # Entities decoded: &sect; -> section sign, &nbsp; -> real space (not
    # \xa0), a numeric/named entity did not leave the literal escape behind.
    assert "§" in text          # section sign, from &sect;
    assert "&sect;" not in text
    assert "&nbsp;" not in text
    assert "\xa0" not in text
    # The nbsp-joined compound survives as ONE readable phrase, not fused
    # or split -- this is the cascade.py non-breaking-hyphen-style hazard
    # named in the task brief, checked on the nbsp analogue.
    assert "twenty-four months" in text

    # Clause structure survived as real prose, not one markup blob.
    assert "must be retained for a period of not less than seven years" in text
    assert "unless the record is the subject of a current" in text

    # Block boundaries became line breaks, not spaces -- paragraphs are
    # still separable, which is what the sentence splitter needs.
    assert "\n" in text
    lines = [l for l in text.split("\n") if l.strip()]
    assert any("Retention period" in l for l in lines)
    assert any(l.strip().startswith("(1)") for l in lines)


def test_html_extraction_does_not_flatten_all_whitespace_to_single_spaces():
    """The task's own warning: collapsing every run of whitespace to one
    space would destroy the block structure the previous test checks for.
    Confirm real paragraph line breaks survive as MULTIPLE lines, not one
    long space-joined blob."""
    text, _ = extract.extract_with_method(LEGIS_HTML.encode(), "act.html")
    assert text.count("\n") >= 5
    # If everything had been collapsed to single spaces, these two clauses
    # -- which are in different <p> elements -- would have run together on
    # one line with no line break between them.
    idx_47 = text.index("Retention period")
    idx_1 = text.index("Subject to subsection (2)")
    assert "\n" in text[idx_47:idx_1]


# --- 2. Measured improvement: marker density and numeric density -----------

def test_html_extraction_measurably_improves_marker_and_numeric_density():
    """The headline comparison: what the OLD code path (decode raw bytes,
    treat as plain text, markup intact) measured vs. what HTML extraction
    measures on the identical document. This is not just "looks better" --
    the old numbers include garbage manufactured by markup, and the new ones
    do not.
    """
    raw = LEGIS_HTML.encode("utf-8")

    old_text = raw.decode("utf-8", errors="replace")  # pre-fix behaviour
    new_text, method = extract.extract_with_method(raw, "act.html")
    assert method == "html"

    old_density = chunk_boundary_audit.marker_density(old_text)
    new_density = chunk_boundary_audit.marker_density(new_text)

    old_numbers = cascade.extract_numbers(old_text)
    new_numbers = cascade.extract_numbers(new_text)

    # The CSS rule `color: #222` and `color: #900` manufacture numeric
    # mentions (222, 900) that have nothing to do with the Act's content.
    # Confirm they are present in the old measurement and gone in the new.
    old_number_texts = {n.text for n in old_numbers}
    new_number_texts = {n.text for n in new_numbers}
    assert "222" in old_number_texts, "fixture must actually exercise the CSS-number hazard"
    assert "222" not in new_number_texts
    assert "900" not in new_number_texts

    # Markup-inflated pseudo-sentences (tag soup treated as line-fragments by
    # the regex sentence splitter) push the sentence count up and the marker
    # RATE down relative to the cleaned text -- fewer, more real sentences,
    # same marker occurrences, so the rate should not be lower after cleanup.
    assert new_density["n_sentences"] < old_density["n_sentences"]
    assert new_density["rate"] >= old_density["rate"]

    # Numeric density: stripping markup must not manufacture new numbers, and
    # must remove at least the CSS-derived ones counted above.
    assert len(new_numbers) < len(old_numbers)


# --- 3. Detection rule: content-based, and its false-positive behaviour ----

def test_doctype_alone_is_decisive():
    assert extract.looks_like_html("<!DOCTYPE html>\n<html><body><p>Hi</p></body></html>")


def test_html_tag_with_supporting_structure_is_detected():
    assert extract.looks_like_html(
        "<html><head><title>x</title></head><body><div><p>a</p><p>b</p>"
        "<table><tr><td>c</td></tr></table></div></body></html>")


def test_fragment_with_no_wrapper_but_enough_structure_is_detected():
    """A browser-saved fragment (copy-pasted body content, no <html>/
    <!DOCTYPE>) must still be recognised -- the operator may well save a page
    from a browser without the wrapper surviving."""
    frag = ("<div class='c'><p>Section 5. A person must not disclose "
            "personal information unless authorised by law.</p>"
            "<p>Section 6. Records must be kept for seven years, except "
            "where a shorter period is fixed by regulation.</p>"
            "<table><tr><td>Type</td><td>Period</td></tr></table>"
            "<ul><li>Note A</li><li>Note B</li></ul></div>")
    assert extract.looks_like_html(frag)


def test_single_stray_angle_bracket_is_not_detected_as_html():
    assert not extract.looks_like_html("assets < liabilities is a breach.")


def test_prose_with_comparisons_and_bracketed_email_is_not_html():
    """The exact false-positive risk named in the brief: a legislative
    document quoting '<' as a comparison operator, and an angle-bracketed
    email address, must NOT flip the detector."""
    prose = (
        "For enquiries about this determination, contact the Registrar, "
        "Jane Doe <jane.doe@example.gov.au>, before the closing date. Under "
        "clause 12, total liabilities must be less than total assets, "
        "expressed as liabilities < assets, and the ratio must remain < "
        "0.75 at all times. This memorandum sets out the retention "
        "schedule referred to above and should be read together with the "
        "enclosed spreadsheet.") * 3
    assert not extract.looks_like_html(prose)
    # And it must round-trip through extract() completely unchanged.
    text = extract.extract(prose.encode(), "memo.txt")
    assert text == prose


def test_html_file_saved_with_txt_extension_is_still_detected():
    """Detection is by CONTENT, not by extension -- the operator's file
    picker or a browser's Save As can easily produce a .txt that is actually
    HTML."""
    text, method = extract.extract_with_method(LEGIS_HTML.encode(), "act.txt")
    assert method == "html"
    assert "must be retained for a period" in text


def test_near_empty_html_shell_is_refused_not_summarised_as_nothing():
    tiny = "<html><body><h1>Retention</h1><p>ok</p></body></html>"
    assert extract.looks_like_html(tiny)
    with pytest.raises(extract.ExtractionError) as e:
        extract.extract(tiny.encode(), "act.html")
    assert "html" in str(e.value).lower()


# --- 4. RTF: refused, not degraded ------------------------------------------

def test_rtf_is_refused_with_an_actionable_message():
    rtf = (rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Times New Roman;}} "
           rb"This is a retention memo about seven years.}")
    with pytest.raises(extract.ExtractionError) as e:
        extract.extract(rtf, "memo.rtf")
    msg = str(e.value).lower()
    assert "rtf" in msg
    assert "plain text" in msg or "save" in msg  # actionable, not just "no"


def test_rtf_control_words_never_reach_extracted_text():
    """Regression for the failure mode refusing avoids: a half-working
    stripper leaving control words in the prose."""
    rtf = rb"{\rtf1\ansi This looks like a memo but is not.}"
    with pytest.raises(extract.ExtractionError):
        extract.extract(rtf, "memo.rtf")


# --- 5. Existing guards must not have weakened ------------------------------

def test_plain_text_still_passes_through_byte_for_byte():
    body = "A normal memo about retention of clinical records for seven years."
    assert extract.extract(body.encode(), "m.txt") == body


def test_zip_and_image_signatures_still_refuse():
    for blob, expect in [(b"PK\x03\x04" + b"\0" * 400, "docx"),
                         (b"\x89PNG\r\n\x1a\n" + b"\0" * 400, "png")]:
        with pytest.raises(extract.ExtractionError) as e:
            extract.extract(blob, "thing")
        assert expect in str(e.value).lower()


def test_extract_with_method_reports_plain_for_ordinary_text():
    text, method = extract.extract_with_method(b"Just an ordinary memo.", "m.txt")
    assert method == "plain"
    assert text == "Just an ordinary memo."

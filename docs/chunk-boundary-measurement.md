# Chunk boundary measurement: how often does a cut sever a qualifying clause pair?

**Measured 2026-08-18 on node 1**, answering the one question
`docs/chunking-research.md` named as cheapest and most decisive: *"How often
does a chunk boundary actually fall inside a qualifying clause pair on the
real corpus?"* String analysis only -- no model, no cluster time.

Reads with `docs/chunking-research.md` (the research this follows up),
`docs/audit-ledger.md` / F41 (the failure shape this is checking for: *"a
clause with a second, competing clause attached"*).

Code: `missing-link/missing_link/chunk_boundary_audit.py` (new, standalone,
same posture as `audit.py` -- not wired into the job flow), tests
`missing-link/tests/test_chunk_boundary_audit.py`. Neither `worker.py` nor
`audit.py` was modified; the new module imports `worker.chunk_spans` directly
and re-uses the same dependency-free sentence-splitter shape `audit.py`'s
`_SENT_FALLBACK` uses (a test asserts the two are behaviourally identical).

Labels: **CONFIRMED** (run here, output read), **INFERRED** (computed from
CONFIRMED numbers).

---

## HEADLINE NUMBER

**Across 84 pooled chunk-boundary events (5 chunk sizes x the 2 real
documents long enough to have any internal boundary at all), exactly ONE
boundary landed inside a sentence carrying a qualifying marker, and it was
fully repaired by the 10% overlap. Zero "stranded" cases -- a boundary that
severs a qualifying clause pair AND leaves the chunk's own text holding only
one half -- were found at any chunk size, with or without `snap_boundaries`.**

| chunk_tokens | internal boundaries | mid-sentence | qualifying | repaired by overlap | **STRANDED** |
|---:|---:|---:|---:|---:|---:|
| 1024 | 43 | 38 | 1 | 1 | **0** |
| 2048 | 21 | 18 | 0 | 0 | **0** |
| 3072 | 14 | 12 | 0 | 0 | **0** |
| 4096 (current default) | 10 | 10 | 0 | 0 | **0** |
| 6144 | 7 | 6 | 0 | 0 | **0** |

With `snap_boundaries=True`, mid-sentence cuts collapse from 88-100% of
boundaries down to 7% at the smallest size and **0% at every size 2048 and
up** -- the flag works exactly as designed, mechanically. But since the
stranded count was already zero without it on this corpus, snapping could
not improve on a floor that was already at zero: **0 -> 0 stranded at every
size.**

**But read the corpus-composition section before treating "0 stranded" as
"severing doesn't happen." It very likely means something narrower: this
project's available long real documents happen not to be legal text.** See
below.

---

## 1. What was measured, and on what

Three real documents were read read-only from `/opt/missing-link/jobs.sqlite`
(`sqlite3.connect("file:...?mode=ro", uri=True)`), deduplicated by content
hash, excluding rows whose `document` column held raw PDF bytes (a
failed-extraction artefact -- four rows in the store start `%PDF-1.6`, not
text, and were excluded on that basis):

| job | chars | what it is |
|---|---:|---|
| `cd206b8f7347` | 2,202 | the health-records retention memo from `docs/audit-ledger.md` section 4 -- the one document in the store with genuinely legal-styled clause language |
| `06af2911d7fc` | 97,299 | the document `bench/out/chunk-size-bench/`'s chunk-size sweep already ran at these same 5 sizes |
| `6c0358825609` | 71,407 | a second long document, same general prose register as the 97K one |

`worker.chunk_spans` -- the real production function, imported, not
reimplemented -- was run at the same 5 sizes and ~10% overlap
`bench/chunk_size_driver.py` swept (1024/2048/3072/4096/6144 tokens, overlap =
`round(chunk_tokens * 0.1)`), both with `snap_boundaries=False` (today's
default) and `True`.

**Sentence splitter.** The dependency-free regex fallback `audit.py` uses when
nltk is unavailable (`_SENT_FALLBACK`), used **unconditionally** here rather
than `audit.py`'s own nltk-preferring `sentence_spans()` -- F41 already
confirmed nltk degenerates on real material (9.1% garbage fragments,
`"K.P. Dutt"` split apart) and the production venv has no nltk anyway, so this
is the splitter that actually matters for a production default.

**Qualifying-marker definition, fixed before measuring:** `unless`, `except`
(covers "except where"/"except that"/"except for"), `provided that`,
`or until`, `whichever (is/the) later/earlier`, `subject to`, `other than`,
`apart from` -- word-boundaried, case-insensitive. Drawn directly from the
failure shapes `docs/audit-ledger.md` / F41 actually found: retention plus
disjunction, exemption plus carve-out, obligation plus condition.

**Counting method, per chunk's own END offset** (the only real internal cut
point a stride-and-overlap chunker produces -- see `chunk_spans`'s own
docstring, and `docs/chunking-research.md` section 3, which frames the
question in exactly these terms: *"a sentence severed at the END of chunk N
is very likely to reappear WHOLE near the START of chunk N+1"*):

1. does it fall **strictly inside** a detected sentence? -> mid-sentence
2. does that sentence carry a **qualifying marker**? -> qualifying
3. does the **whole sentence, verbatim**, also appear in the next chunk's own
   text? -> repaired
4. qualifying **and not** repaired -> **stranded, the headline case**

---

## 2. Sanity-checking the instrument -- and it found a real limitation

The task brief warned this is exactly the kind of measurement whose
instrument can be broken (F40, F41). It was checked, and **it is partly
broken, in a specific, explainable, and bounded way.**

**False-positive rate on the population that matters (60-sentence sample of
the 84 pooled boundary-detected "mid-sentence" events, seed 42):
30/60 = 50%.** Manual inspection of the flagged cases showed **every one** was
tagged `no_terminal_punct`, and every single one, read in context, was a
**genuine fragment of continuous flowing prose that a hard PDF/book line-wrap
cut before any period appeared** -- e.g. a sentence like *"...he had only
dealt with one question; but he left his paper and..."* ending exactly at the
line's right margin. These are not nonsense text; they are real words, but
they are **line fragments, not complete sentences**, because the regex
fallback's non-punctuated branch (`\S[^.!?\n]*$`) treats every un-punctuated
*line* as its own pseudo-sentence when the true sentence runs on for several
more lines before its first period. `docs/chunking-research.md` section 4
already warned a bare `\n` is not a real paragraph boundary in PDF-extracted
text; this is that exact warning showing up **inside the sentence splitter
itself**, not only in `snap_boundaries`'s own (correctly written, and
unaffected) logic.

Two consequences, checked rather than assumed:

- **Inflated mid-sentence counts on this corpus.** Since a "sentence" is
  frequently just a ~40-90-character line, almost any boundary position that
  isn't exactly at a line break will trivially land "inside" one -- explaining
  the 86-100% mid-sentence rate at every chunk size. This does **not** bias
  the **qualifying** or **stranded** counts upward, though, because a marker
  still has to be *present* in that (possibly truncated) fragment to count,
  and the manual sample found no markers among the flagged fragments.
- **A genuine false-negative risk, checked and found empty.** If a true
  clause pair straddles a hard line-wrap with no period between the two
  halves (main clause on one line, its `unless`-exception on the next), the
  splitter reports two separate "clean" sentence-fragments rather than one
  severed sentence -- a boundary landing exactly there would be invisible to
  the headline count. This was checked directly
  (`clean_boundary_linewrap_check`, exercised in
  `test_clean_boundary_linewrap_check_flags_marker_adjacent_bare_linewrap`
  against a constructed positive-control document that reproduces the shape):
  of the corpus's 11 "clean" boundaries, **6 were bare line-wraps rather than
  real full stops, and 0 of those 6 had a qualifying marker in the first few
  words of the following fragment.** So the risk mechanism is real and was
  specifically checked for, but it did not manifest on this corpus.

**Net read: the headline "0 stranded" is a plausible lower bound, not a
guaranteed exact count, but the specific failure mode that could hide a
stranded case from it was checked directly and came up empty too.**

For context (not the number that matters, but informative): a random sample
of 40 sentences from **anywhere** in the largest document -- not just
boundary-adjacent ones -- had a suspect rate of 34/40 = 85%, because that
population also includes short verse lines, transliterated headers and
page-furniture that the boundary-adjacent population mostly avoids (chunk
boundaries land inside ordinary flowing prose, which is most of the
document's bulk). This is a caution about the corpus's formatting overall,
not a correction to the headline number.

---

## 3. Corpus composition -- the reason to not over-read the zero

**This is the more important caveat than the false-positive rate.** Marker
density (fraction of all sentences in the whole document carrying a
qualifying marker, independent of chunking) was measured per document:

| job | sentences | with a marker | rate |
|---|---:|---:|---:|
| `cd206b8f7347` (the health-records memo -- legal-styled) | 39 | 6 | **15.4%** |
| `06af2911d7fc` (97K, narrative prose) | 3,080 | 1 | **0.03%** |
| `6c0358825609` (71K, narrative prose) | 733 | 10 | **1.4%** |

**The one document that actually resembles this project's target workload
(legal/records language) is 82 sentences per qualifying marker, roughly
50-500x denser than the two long documents that are actually long enough to
produce chunk boundaries.** But at 2,202 characters it never reaches even one
whole chunk at the smallest tested size (1024 tokens = ~717 words = several
thousand characters) -- **it produced zero internal boundaries at every size,
snap or no snap.** So the only document in this project's real corpus that
carries the clause density the whole worry is about contributed **zero data
points** to the headline count. The two documents that did contribute data
are long, but are narrative prose (public-domain devotional texts used as
long-document benchmark material, not legal text), where a qualifying-marker
event is rare almost regardless of where the chunker cuts.

**So the honest statement of the result is narrower than "severing essentially
never happens":** on the real, long documents currently available to measure
against, qualifying clauses are too sparse for a fixed-word cut to have much
chance of hitting one, so it mostly didn't. **This corpus cannot yet answer
whether severing matters on this project's actual document type**, because it
doesn't contain a long real legal/records document to test against.

---

## 4. Overlap-repair rate

The research's arithmetic argument (~287 words of overlap at the 4096-token
default, "generous against a typical clause length") was checked empirically
rather than trusted. At every chunk size that produced a qualifying event
(only 1024 did, with exactly one qualifying case), **the repair rate was
100% (1/1)** -- the severed sentence reappeared verbatim in the next chunk's
own text. This is consistent with, but far too small an n to independently
confirm, the research's arithmetic. **This is not enough data to validate the
repair-rate claim in general** -- it is one observation, not a rate.

---

## 5. What `snap_boundaries` measurably buys, independent of the headline count

Even though it could not move a stranded count that was already at floor on
this corpus, `snap_boundaries=True` measurably does what it was built to do:
mid-sentence cuts fall from 86-100% of boundaries (no-snap) to **7% at the
smallest chunk size and 0% at every size 2048 and above** (snap). A
constructed positive-control test
(`test_snap_boundaries_reduces_midsentence_count_on_a_constructed_document`)
confirms the mechanism holds on documents with normal sentence-ending
punctuation throughout, independent of this corpus's line-wrap quirks. That
is a real, measured citation/trust-UX improvement (chunks now start/end at or
very near real sentence boundaries) at negligible cost (≤120-char tolerance
against a multi-thousand-character chunk) -- it is just not, on this
evidence, a fix for a stranded-clause problem this corpus could not
demonstrate exists.

---

## 6. Recommendation

**Do not flip the default on the strength of this measurement, and do not
conclude the naive splitter is proven fine either -- the honest state is "not
enough of the right kind of data yet," which is itself the useful answer.**

Specifically:

1. **Leave `snap_boundaries` default OFF for now.** Nothing here shows the
   naive splitter is causing a stranded-clause problem, but nothing here can
   rule it out on the workload that matters, because the corpus available to
   check against does not contain a long legal/records document.
2. **The next cheap thing to measure, before either changing the default or
   dropping this line of inquiry, is getting a genuinely long (tens of
   thousands of characters) real legal/records-style document into the
   corpus** -- redacted or synthetic-but-realistic if a real one isn't
   available -- and re-running exactly this same sweep against it. That
   single addition would let the headline number actually speak to the
   question that motivated it, rather than to a corpus of public-domain
   narrative prose.
3. **`snap_boundaries` is still worth turning on for its independently
   measured citation/trust benefit** (section 5) even absent proof it
   prevents stranded clauses -- that is a separate, already-adequate
   justification the research document raised (section 5 of
   `docs/chunking-research.md`, "a materially worse thing to show a human who
   clicked to verify a claim") and this measurement neither strengthens nor
   weakens it either way.
4. **Do not read the false-positive-checked instrument limitation (section
   2) as invalidating the headline number** -- the specific failure mode that
   could have hidden a stranded case was checked directly and found empty,
   which is a real (if bounded) check, not an assumption.

---

## Not established -- do not cite as settled

- **Whether chunk-boundary severing of a qualifying clause pair is common on
  this project's actual document type (legal/records text).** The one
  available document with that character is too short to have ever produced
  a chunk boundary. This is the single biggest open item from this
  measurement.
- **The overlap-repair rate**, beyond one observation (1/1).
- **Whether the false-negative risk identified in section 2 (a clause pair
  straddling a bare line-wrap, reported "clean") occurs on a genuinely
  legal-styled long document** -- checked and found empty on the two
  documents available, neither of which is legal text.

# The faithfulness cascade: deterministic checks that decide, a classifier that does not

**Built and measured 2026-08-18 on node 1.** Implements the operator's design:
*"if it has numbers then check that the numbers it said are the same numbers in
the document, and that the things it attributes the numbers to are similes or
within the same natural entity or semantic similarity, with some of those being
soft signals and some being hard signals (if the numbers and words match leave
it)."*

Labels: **CONFIRMED** (run here, output read), **REPORTED** (someone else's
number), **INFERRED** (computed from CONFIRMED numbers).

Code: `missing-link/missing_link/cascade.py`, tests
`missing-link/tests/test_cascade.py` (68 tests). Imports from `audit.py`;
**does not modify it.**

Reads with: **F41** and `docs/audit-production-scale.md` (which inverted this
design mid-build), `docs/audit-ledger.md`, `docs/citation-research.md` Q4
(reference is not faithfulness), `docs/market-research.md` §6 item 8.

**No inference was run. Nothing here calls llama-server.** All measurement is
CPU string work against already-generated output, read-only.

---

## VERDICT FIRST

**The deterministic number check works, and it is the only part of the audit
stack with a reliability story that survives production scale.**

On **978 real model-generated claims** it produced **3 findings. All three were
manually verified as genuine fabrications. Zero false positives.** On the same
material it caught **100%** of injected fabricated figures whose replacement was
absent from the source, and **92.6%** when the replacement was unconstrained.

It costs **~5 seconds for a whole 26-chunk document** and needs no model.

**One of the three findings is a real hallucination caught in the wild.** The
reduce step of a completed 5-chunk job asserted a death year for its subject.
That figure appears **nowhere in the source document and in none of the five
chunk summaries** — the model inserted world knowledge that its input did not
contain. It is historically correct, which is precisely what makes it dangerous:
a reader spot-checking it against their own knowledge would have confirmed it.
This is F25's reduce-step laundering, observed rather than theorised, and it was
caught by `in`, not by inference.

**The classifier tier is built, gated, and NOT recommended.** F41 measured the
ensemble's disagreement signal collapsing at production evidence size
(precision 1.00 → 0.75, recall 1.00 → 0.43, 5.6% of errors silent to both
models) at a cost that exceeds the summarisation job it audits. This module
therefore defaults to `classifier=None` and is fully useful in that
configuration. **If the question is "should the classifier ship", the answer
from this work is no.**

**What the cheap tier cannot do, and does not pretend to:** it resolves **35%**
of real claims positively and hands the other **65%** on as explicitly
unresolved. With no classifier configured those are reported as
`needs_classifier` — **not checked**, not passed. That is a coverage limit
stated honestly, not a verdict.

---

## 1. The cascade

| tier | signals | what it may do |
|---|---|---|
| **1 — hard** | numbers; distinctive terms | numbers may **FAIL** a claim; terms may only route |
| **2 — soft** | lexical overlap, trigram similarity, unit agreement, negation polarity, quantifier scope, dropped qualifiers | **route only — never decide** |
| **3 — classifier** | the two-model MiniCheck ensemble | optional, **off by default** |

Routing:

- any claim number absent from the cited span → **`number_unsupported`**, and it
  is **not escalated**. A classifier cannot make a missing figure present, and
  spending 18 s to add a probability to a certainty is waste.
- every number present and no soft signal objects → **pass**, no escalation.
  This is the operator's "if the numbers and words match leave it", and it is
  what makes the cascade affordable.
- anything else → escalate; with no classifier that is a `needs_classifier`
  finding naming which signal was unhappy.

**Every signal carries a `kind`** (`hard` / `soft` / `classifier`) and its own
sub-result. They are never merged into one score. A `status` of `"fail"`
anywhere in a ledger always means a hard, deterministic failure — a consumer can
rely on that.

---

## 2. Normalisation, and which way it is biased

Matching is on **normalised value**, not surface form. All CONFIRMED by test:

| written | also matches |
|---|---|
| `seven` | `7`, `seven-year`, `7 years` |
| `forty-one` | `41` |
| `one hundred and twelve` | `112` |
| `$1.2m` | `1,200,000`, `1.2 million` |
| `3.5%` | `3.5 per cent` |
| `March 2026` | `12/03/2026`, `2026-03-12` |
| `the seventh` | `7` |
| `15th century` | a source containing `1485` |

### The bias, stated

**STRICT — a missing counterpart is a HARD FAIL:** money, percentages, years,
dates, anything with a fractional part, any integer above 10, **and any number
carrying a measurement unit whatever its size**. That last clause is
load-bearing: `7 years` is a retention period, this project's flagship figure,
and an earlier version treated it as a possibly-derived small count. The unit
decides, not the magnitude.

**LAX — unmatched escalates, never fails:** a bare integer ≤ 10 with no
measurement unit. This is the derived-count case: a source enumerating
`(1)… (2)… (3)…` fully supports *"three deficiencies"* while containing no token
`three`. `enumeration_count` counts the markers and resolves it; only the
residue escalates.

**Why this way round.** Strict where fabrication is dangerous and cannot be
derived by counting; lax where derivation is plausible — and the lax branch
never passes silently, it hands the claim on. Getting it wrong in the strict
direction floods the ledger and teaches the reader to skim; getting it wrong in
the lax direction misses a small fabricated count. The measured false-positive
rate below says the balance is right.

**Two derivations are computed rather than guessed:** enumeration counts, and
centuries (`1485 → 15th`, because `1485 // 100 + 1 == 15`). Both can only ever
say *supported*; neither can turn a matched number into a failure.

---

## 3. Measured: real model output, unmutated (CONFIRMED)

759 claims from the chunk-size sweep corpus (gpt-oss-120b, five chunk sizes),
plus 219 from the completed 5-chunk pipeline job. **A hard finding here is a
candidate false positive — this is untampered real output.**

| corpus | claims | passed cheaply | number findings | escalated | escalation rate |
|---|---:|---:|---:|---:|---:|
| chunk 1024 | 281 | 88 | 0 | 193 | 68.7% |
| chunk 2048 | 178 | 59 | **1** | 118 | 66.3% |
| chunk 3072 | 126 | 51 | 0 | 75 | 59.5% |
| chunk 4096 | 123 | 43 | 0 | 80 | 65.0% |
| chunk 6144 | 51 | 23 | 0 | 28 | 54.9% |
| **corpus total** | **759** | **264** | **1** | **494** | **65.1%** |
| pipeline job (both hops) | 219 | 106 | **2** | 111 | 50.7% |
| **ALL REAL CLAIMS** | **978** | **370** | **3** | **605** | — |

**Number false-positive rate: 0 of 978 = 0.0%.** All three findings were
verified by hand against the source:

1. **A fabricated year in a chunk summary.** The summary states a date range
   ending in a year the source does not contain; the source states a different
   year. A genuine one-digit fabrication.
2. **A fabricated year in the reduce output.** Verified absent from the source
   document *and* from all five chunk summaries. Described above.
3. **A century unsupported by its own chunk.** The chunk-1 summary asserts a
   century; **chunk 1's source span contains no years at all** (CONFIRMED by
   scanning each span). Correct for the document as a whole, unsupported by the
   evidence window it was scored against — which is exactly what hop 1 is for.

**Cheap-tier cost: 2.7–5.6 s per corpus** (CONFIRMED), i.e. a whole document
audited for less than the time one classifier claim takes.

---

## 4. Measured: catch rate on known fabrications (CONFIRMED)

Real summary sentences with one figure mutated, replacement chosen so the
fabrication is unambiguous.

| mutation | caught | rate |
|---|---:|---:|
| replacement absent from source | 27/27 | **100%** |
| replacement unconstrained | 25/27 | **92.6%** |
| fabricated proper names (entity signal) | 258/258 | **100%** |

Per kind, collision-avoiding: years 5/5, plain integers 18/18, ordinals 4/4.

**The 7.4% gap is collision, not blindness.** When a mutated figure happens to
coincide with some other number elsewhere in a 4096-token chunk, a
value-matching check cannot see it. That is an inherent ceiling of the method
and it is the honest limit of the number tier.

---

## 5. Measured: the negation battery, at both scales (CONFIRMED)

36 constructed pairs, ground truth true by construction, run at the original
short-document size and at F41's production-scale fixture.

| | short doc | **production scale** |
|---|---:|---:|
| true claims falsely hard-failed | **0/36** | **0/36** |
| fabrications hard-caught | 1/36 | 1/36 |
| fabrications escalated | 33/36 | 32/36 |
| **fabrications passed silently** | **2/36** | **3/36** |
| caught-or-escalated | 94.4% | **91.7%** |
| true claims escalated | 14/36 | 17/36 |

**Read this correctly.** The cheap tier catches almost no negations *by design*
— a polarity flip preserves every number and every name, so a token check
cannot see it. What matters is the two outer columns: **zero false alarms**, and
**91.7% of fabrications at least escalated** rather than passed.

**And note what does not happen here: these numbers barely move between the two
scales.** F41's ensemble lost half its recall going from short documents to
4096-token ones. A deterministic check does not care how long the evidence is —
`in` is `in`. That property, not the raw accuracy, is the argument for this
tier.

The 3 silent misses are all quantifier/permission inversions where every token
is genuinely present. Two cheap signals were added specifically for that
measured blind spot — **quantifier scope** (universal claim over a partitive
source: *"all twelve files"* against *"three of twelve"*) and **dropped
qualifier** (an `unless`/`except` in the evidence the claim does not repeat).
They took silent misses from 9/36 to 3/36. Both are SOFT: they escalate, never
fail.

---

## 6. What was demoted, and on what evidence

### The entity check is NOT a hard failure. Measured, then demoted.

Fabricated names are caught 258/258. But on real output the same check flags
faithful sentences constantly, because the source is OCR mojibake
(`Rådhåråò^`) and the model correctly reconstructs the transliteration
(`Rādhārāṇī`). The term *is* supported; the checker cannot see it.

Threshold sweep, 592 real claims against 238 injected fabricated names:

| fuzz | false positives on real output | catch rate |
|---:|---:|---:|
| 0.88 | 22.5% | 99.6% |
| 0.85 | 19.8% | 99.6% |
| 0.80 | 17.4% | 99.6% |
| **0.75** | **15.2%** | **99.6%** |
| 0.70 | 14.9% | 99.6% |
| 0.65 | 12.5% | 72.7% |

0.75 is the knee. **But the whole curve says the same thing: even at its best
this signal flags one faithful sentence in seven.** A hard failure at that rate
teaches the reader to skim the flags — the identical failure that disqualified
the polarity check in `docs/audit-ledger.md` §2, measured the same way and
demoted for the same reason.

**So `ENTITY_MODE = "route"` by default:** the signal is computed, reported and
may escalate a claim, but may never fail one. `"hard"` is available for an
operator who knows their corpus is clean; `"off"` reports without routing. It is
kept rather than deleted because its catch rate is excellent and its false
positives are an artefact of *this* corpus, not of the idea.

---

## 7. Sub-sentence decomposition (F41 prerequisite)

F41: **55.6% of real summary sentences carry more than one claim**, so a
sentence-level verdict cannot say which part is wrong.

Claim units are therefore **clauses**, split on bullets, enumeration markers,
semicolons and coordinating conjunctions — and only where both sides look like
real claims, so *"apples, and pears"* stays whole. Offsets remain exact
(asserted by test), because every location in this stack is computed from them.

| | measured |
|---|---:|
| real summary sentences | 565 |
| sentences yielding >1 clause | 169 (**29.9%**) |
| claim units after decomposition | 759 (**1.34×**) |
| degenerate fragments (<8 alnum chars) | 36 (**6.4%**) |

The 29.9% is lower than F41's 55.6% because this split is deliberately
conservative — it uses punctuation and enumeration structure only, with no
parser and no POS tagger. **An over-eager split attributes a figure to the wrong
clause, which is worse than not splitting**: the reader is then pointed
confidently at the wrong half of the sentence.

### The splitter was hardened against real markdown

F41 measured nltk producing **9.1%** garbage fragments on this material,
including splitting `"K.P. Dutt"` into `"P."`. This module's splitter measures
**6.4%**, with three repairs, each fixing a defect **observed on real output**:

- a period between two digits is a decimal point or a verse reference —
  `"Bhagavad-gita 18.55"` was becoming three "sentences", each with an orphaned
  number, **each of which hard failed** against a source containing it intact;
- a period after an initial or an abbreviation does not end a sentence;
- a fragment with no letters or digits is debris and is dropped, not scored.

---

## 8. Other defects found by running it on real material

Every one of these was invisible to fixtures and appeared on first contact with
real output — the F34/F38 pattern again.

| defect | effect | fix |
|---|---|---|
| `may` matched case-insensitively as a month | *"beginners **may** need to follow"* → month 5 → strict → **hard fail on a faithful sentence** | months must be capitalised; ambiguous ones need an adjacent day or year |
| non-breaking hyphen | `"twenty‑four"` read as 20 and 4; 20 is strict → **hard fail** | length-preserving lookalike normalisation (offsets must not move) |
| `"seven and eight"` parsed as 15 | 15 occurs nowhere → **hard fail on faithful text** | place-value-aware parser that **rejects** malformed numerals, then rescans token by token |
| markdown bullets read as proper nouns | `"* **Spiritual** evolution…"` → *Spiritual*, *Ultimate*, *Several*, *Using* flagged as absent names | sentence-initial position measured after list/emphasis markers |
| possessives and trailing connectors | *"Mahārāja's"*, *"Discussion of"* flagged | clitics stripped; phrases may not end on a connector |

**The pattern is worth naming: every single one produced a FALSE HARD FAILURE on
correct output.** A checker's defects are asymmetric — they land almost entirely
on the faithful sentences, because those are the ones that were going to pass.

---

## 9. Cost

| | measured |
|---|---:|
| cheap tier, whole 26-chunk corpus | **3.5 s** (CONFIRMED) |
| classifier, per claim, Flan-T5 @ production size | 17.74 s (CONFIRMED, F41) |
| classifier, per claim, RoBERTa | 8.81 s (CONFIRMED, F41) |
| 978 real claims, every claim classified | **3.86 h** (INFERRED) |
| 978 real claims, cascade routing only | **2.51 h** (INFERRED) |
| **saving from the cascade** | **35%** |

**A 35% saving on a cost that should not be paid at all is not the point.** The
point is the first row: the deterministic tier audits an entire document in
seconds, with a measured 0% false-positive rate, and F41 says the thing it would
have escalated to is not trustworthy at this scale anyway. **Run tier 1 and 2;
do not run tier 3.**

---

## 10. Is this ready to wire in?

**Not yet, and deliberately not wired.** It is a standalone tool
(`python -m missing_link.cascade job|corpus`), read-only against the job store.

What is settled and should not be re-litigated:

- **numbers are the primary signal**, and the only one permitted to fail a claim
- **entity absence routes, never fails** — measured, §6
- **soft signals never decide**
- **`status: "fail"` always means a hard deterministic failure**
- **refuse rather than degrade** — `needs_classifier` and `unscoreable` are
  first-class outputs and neither is a pass
- **the classifier stays off by default** (F41)

What would have to be true before wiring in:

1. **A false-positive measurement on this project's actual target material.**
   Everything in §3 is one document — a devotional text with heavy OCR damage.
   It is a *hostile* corpus for entity matching and a *neutral* one for numbers,
   but it is not a retention policy or a statutory instrument. **The 0/978
   number result is the strongest claim here and it still rests on one
   document.**
2. **A decision about the 65% that escalates.** With no classifier those are
   `needs_classifier` findings. Rendering 65% of a summary as "unchecked" may be
   honest and useless at the same time; the UI question is unanswered.
3. **Citation-mode has never run on real data.** `citation_units` is tested and
   consumes `worker.parse_section_citations` correctly, but the only completed
   multi-chunk job predates the citation prompt and carries **no `[Section N]`
   markers**. Until a job runs with citations on, that path is fixture-tested
   only — and this repo's own history says that is not evidence.

---

## Not established — do not cite as settled

- **That the 0% number false-positive rate generalises.** One document, 978
  claims. The mechanism (`value in evidence`) is scale-invariant in a way the
  classifier is not, which is an argument, not a measurement.
- **The 92.6% unconstrained catch rate as a general figure.** 27 mutations.
- **That clause decomposition improves anything downstream.** It makes
  attribution precise and that is verified; whether it changes a reader's
  outcome is unmeasured.
- **Any of it on clean, non-OCR source.** Entity behaviour in particular should
  be substantially better and is untested there.
- **`ENTITY_FUZZ = 0.75` beyond this corpus.** It is a knee on one curve.
- **That escalation rate is a meaningful quality metric.** With the classifier
  off it is a coverage statistic, nothing more.

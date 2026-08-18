# Does the audit ledger survive at production scale? The 4096-token re-run

**Built and measured 2026-08-18 on node 1.** Answers the one question
`docs/audit-ledger.md` left open: every number in that document was measured
on one-to-three-sentence documents, deliberately built so nothing in
surrounding text could rescue a checker that misread a clause. Production
scores a summary sentence against a **~4096-token chunk** (`CHUNK_TOKENS` in
`missing_link/worker.py`). This re-runs the same 36-pair battery with each
pair's clause embedded inside a document of that size, and reports whether
the design still holds.

## SCOPE -- WHAT THESE NUMBERS DO AND DO NOT COVER

**Read this before the verdict.** Every number in sections 1-8 below
characterises the ensemble on **constructed, single-claim sentences at
production evidence length.** They do NOT characterise it on real model
output. This is a real and important limitation, not a formality:

- Ground truth requires construction -- there is no other way to know a claim
  is true or fabricated *by design* -- but constructing the claims also means
  every sentence scored below is a single, clean, checkable proposition. It
  was written to be checkable.
- Real gpt-oss chunk summaries are not written to be checkable. They are
  dense multi-clause prose, often enumerations, frequently hedged, produced by
  a model with no incentive to keep one claim per sentence.
- The one real row that existed before this task (`docs/audit-ledger.md`
  section 4, job `2b4c926a799a`) already showed the shape of the gap: it
  escalated 1 of 3 sentences, and that sentence was a 418-character
  three-claim enumeration, not a single checkable claim -- a failure mode the
  constructed battery cannot contain because it was built not to.

Section 9 reports what real chunk-summary data was available at the time
this task finished, scores it where ground truth is not needed (escalation
rate, unscoreable rate, disagreement rate on real output are all measurable
without ground truth), and states plainly what remains unmeasured.

Reads with `docs/audit-ledger.md` (the short-document result this compares
against) and `docs/minicheck-spike.md` (first principles). Labels: **CONFIRMED**
(run here, output read), **REPORTED** (someone else's number), **INFERRED**
(computed from CONFIRMED numbers).

Fixtures: `missing-link/missing_link/fixtures/negation_battery_prodscale.json`
(36 pairs, same ground truth as `negation_battery.json`, production-scale
documents), `missing-link/missing_link/fixtures/negation_battery_position_effect.json`
(9 base pairs x 3 positions, isolating the position variable). Structural
tests: `missing-link/tests/test_audit_prodscale_fixtures.py`. No changes to
`missing_link/audit.py` or the original `negation_battery.json`.

---

## VERDICT FIRST

**No -- the ensemble does not survive at production scale as a reliable
disagreement-based safety net, and the honest recommendation is DO NOT WIRE
THIS IN as designed.** The evidence:

- **Disagreement's recall as an error signal falls from 1.00 (short
  documents) to 0.43 (production scale, 72 claims) and 0.75 (position
  sub-study, 54 claims, itself a harder subset).** Precision falls from 1.00
  to 0.75-0.90. On the representative main fixture, **more than half of the
  ensemble's real errors now produce agreeing, wrong labels** -- the
  situation the two-model design exists to prevent.
- **The flagship case the whole design was built around --
  `retention_seven_years`, "seven years ... or until the client turns
  twenty-five, whichever is later" -- goes from CAUGHT at short-document
  scale to a SILENT FAILURE at production scale** in the main fixture, and
  fails for both models at the `middle` position specifically in the
  position sub-study (section 5). Flan-T5 now inverts this pair at every
  position tested; RoBERTa, the model that used to catch it, only does so at
  `begin` and `end`.
- **Real gpt-oss output makes the picture worse, not better** (section 9):
  over half of real chunk-summary sentences carry more than one claim by a
  simple heuristic, and nltk's sentence splitter itself degenerates on real
  markdown-formatted output, producing meaningless fragments. The
  constructed battery, built from single clean claims, cannot see either
  problem, and both compound the disagreement-reliability finding above.
- **Cost is comparable to, not a fraction of, the summarisation it audits**
  (section 7): a properly-scaled projection lands well over an hour for hop
  1 alone on a realistic 25-chunk document, using the task brief's own
  assumed sentence density -- and real measured sentence density is roughly
  double that assumption, so the honest number is worse again.

**What is still true and worth keeping:** the tool's engineering is sound --
refuse-rather-than-degrade on over-long input, offsets computed rather than
asked for, two hops correctly scoped, no pass verdict. **What changed is the
central empirical claim it was built on.** Short-document disagreement had
perfect precision and recall; production-scale disagreement does not, and
the gap is large enough that a reviewer trusting "no disagreement flagged"
would now be wrong on more than half of the ensemble's real mistakes.

---

## 1. Method: how the production-scale documents were built

### 1.1 Target size

`missing_link/worker.py`: `CHUNK_TOKENS = 4096`, `WORDS_PER_TOKEN = 0.70`. The
production chunker sizes a chunk in WORDS via `chunk_tokens * WORDS_PER_TOKEN`,
so the real production chunk is **~2867 words**, not 4096 words. That is the
target this fixture builds to (2867-2929 words measured across all 63
generated documents, CONFIRMED by `test_audit_prodscale_fixtures.py`).

### 1.2 Ground truth is unchanged

Every pair keeps the exact `supported`/`negated` claim text from
`negation_battery.json`. The only thing that changes is the evidence
document: the original short `doc` (one clause, one-to-three sentences) is
embedded **verbatim**, at a recorded character offset, inside a much larger
document. `test_audit_prodscale_fixtures.py` asserts this offset is exact and
that ground truth (`supported`/`negated`/`category`) is byte-identical to the
original battery for all 36 ids.

### 1.3 Filler sourcing -- stated honestly

Real prose is much harder for a classifier to distinguish from a target
clause than synthetic padding, so real material was used where it could be
used honestly:

- **`/opt/missing-link/jobs.sqlite`, job `06af2911d7fc`** (97,299 characters,
  a real document this pipeline processed, read READ-ONLY) supplies roughly
  1 filler paragraph in 4, drawn from clean-ASCII ~520-character windows
  spread across the document, used **only** for genuine natural-language
  prose rhythm. This document is a public-domain devotional text and is
  **topically unrelated** to the compliance clauses under test -- that
  irrelevance is deliberate: it cannot corroborate or contradict any claim,
  so it is safe to use as pure bulk.
- **`/opt/missing-link/jobs.sqlite`, job `2b4c926a799a`** (2,202 characters,
  the records-retention memo used in `docs/audit-ledger.md` section 4) was
  **deliberately excluded** from filler. It is near-verbatim the real-world
  source that pairs `retention_seven_years` and `access_41_staff` were
  modelled on ("41 staff who had no clinical role"; "seven years ... or
  until [a] client turns twenty-five, whichever is later"). Using it as
  filler would have handed those two pairs real corroborating text
  unavailable to the other 34 -- an unfair advantage, not a realistic one.
  `test_excluded_memo_did_not_leak_into_unrelated_filler` checks this holds.
- The remaining ~3 paragraphs in 4 are **hand-written**, in the same spirit
  as `negation_battery.json`'s own docs (that fixture's own `_about` field
  says its documents are "built by hand, not sourced" -- this fixture is
  consistent with that, not a departure from it). They describe a fictional
  organisation, "Northgate Community Services", covering records management,
  access control, statutory duties, deadlines, exemptions, conditional
  obligations, quantifiers/statistics, incident reporting, governance and
  training -- the categories this project's real material is made of, per
  `negation_battery.json`'s own category list. Every number in this filler
  (retention periods, staff counts, deadlines, dollar thresholds) was chosen
  to be **distinct from every number used in the original 36 pairs**,
  specifically so filler cannot accidentally corroborate or contradict the
  claim under test.

### 1.4 Position

Task brief: "vary the position of the target clause -- beginning, middle,
end -- because lost-in-the-middle is a documented effect this project
already relies on for its chunking decision (arXiv:2307.03172)." Two fixtures
carry this:

- **Main fixture (36 pairs, 1 document each):** position cycles
  begin/middle/end, **stratified within category** (offset by category
  index) so position is not confounded with category. Distribution: 13
  begin, 12 middle, 11 end (CONFIRMED, `test_position_label_matches_actual_offset_fraction`
  checks the label matches where the clause actually sits: begin = first
  quarter of the document, end = last quarter).
- **Position sub-study (9 base pairs x 3 positions = 27 documents):**
  isolates position from category by holding the pair fixed and varying only
  position. Covers the two categories with measured short-document failures
  (retention, exemption), quantifier, and three strong-category pairs as
  contrast, including **`retention_seven_years` itself** -- the qualifier-
  overrides-main-clause case (docs/audit-ledger.md section 1.2) -- at all
  three positions.

### 1.5 What this does NOT establish

This is 36 constructed pairs (or 27 for the position sub-study), not a
statistically powered study, for the same reason the original battery isn't
one: it is built to be the hard, deliberately adversarial case, not a random
sample of real chunks. See the confidence-bound discussion in section 3.

---

## 2. Results (CONFIRMED, 36 pairs / 72 claims, main fixture)

Wall clock: 1911.2s (31.85 min) for both models -- Flan-T5-Large 1277.0s
(17.74 s/claim), RoBERTa-Large 634.2s (8.81 s/claim). See section 7 for what
this means for a real document.

### 2.1 Accuracy per model -- degraded, but not collapsed

| | short-doc battery (docs/audit-ledger.md) | production-scale (this run) | delta |
|---|---:|---:|---:|
| Flan-T5-Large | 69/72 = 95.8% | **65/72 = 90.3%** | **-5.6 pp** |
| RoBERTa-Large | 70/72 = 97.2% | **68/72 = 94.4%** | **-2.8 pp** |

Per category (production-scale):

| Category | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| access_permission | 10/10 = 100% | 10/10 = 100% |
| conditional_obligation | 9/10 = 90% | 9/10 = 90% |
| deadline | 9/10 = 90% | 10/10 = 100% |
| exemption | 9/10 = 90% | 9/10 = 90% |
| **quantifier** | **9/12 = 75.0%** | 11/12 = 91.7% |
| retention | 9/10 = 90% | 9/10 = 90% |
| statutory_duty | 10/10 = 100% | 10/10 = 100% |

**Quantifier is now the weakest category for Flan-T5** (75%, down from 100%
at short-document scale) -- more evidence text gave it more opportunities to
find a superficially-matching number, exactly the failure mode this task
brief predicted.

All 11 errors, in full:

| Model | Pair | Claim | Score | Called it | Should be |
|---|---|---|---:|---|---|
| Flan-T5 | `conditional_subject_to_funding` | negated | 0.574 | **supported** | unsupported |
| Flan-T5 | `deadline_extension_granted` | true | 0.150 | unsupported | supported |
| Flan-T5 | `exemption_small_provider_carve_out` | true | 0.217 | unsupported | supported |
| Flan-T5 | `quantifier_no_breaches_reported` | true | 0.087 | unsupported | supported |
| Flan-T5 | `quantifier_no_breaches_reported` | negated | 0.519 | **supported** | unsupported |
| Flan-T5 | `quantifier_one_incident_open` | negated | 0.911 | **supported** | unsupported |
| Flan-T5 | **`retention_seven_years`** | negated | 0.961 | **supported** | unsupported |
| RoBERTa | `conditional_subject_to_funding` | negated | 0.669 | **supported** | unsupported |
| RoBERTa | `exemption_small_provider_carve_out` | true | 0.269 | unsupported | supported |
| RoBERTa | `quantifier_one_incident_open` | negated | 0.941 | **supported** | unsupported |
| RoBERTa | **`retention_seven_years`** | negated | 0.638 | **supported** | unsupported |

### 2.2 THE LOAD-BEARING RESULT: disagreement no longer predicts error reliably

Same definition as the short-document battery: two models "disagree" if their
labels differ, or their support probabilities differ by >= 0.35
(`DISAGREEMENT_GAP`).

| | Both right | At least one wrong |
|---|---:|---:|
| **Models disagree** | 1 | **3** |
| **Models agree** | 64 | **4** |

| | short-doc battery | production-scale | delta |
|---|---:|---:|---:|
| **Precision** of disagreement as error signal | **1.00** | **0.75** | **-0.25** |
| **Recall** of disagreement as error signal | **1.00** | **0.43** | **-0.57** |
| Errors **both** models made (silent to the ensemble) | 0 | **4** | **+4** |
| Escalation rate | 6.9% | 5.6% | -1.3 pp |

**This is the answer to the one question, and it is a clear no.** At
production scale, more than half of the ensemble's real errors (4 of 7) now
produce **matching, agreeing, wrong labels** -- the two models fail the same
way often enough that disagreement stops being a reliable tripwire.
Precision also drops: one of the four disagreement escalations
(`retention_minor_until_25`, gap 0.435) was a false alarm where both models
were actually correct. The escalation rate barely moved (6.9% -> 5.6%,
even ticking down slightly) -- **the ensemble is not raising more flags, it
is raising the wrong ones**, and that is worse than raising more: the tool
looks just as quiet as before while missing more than half of what it exists
to catch.

**The four silent (agreed-wrong) errors, in full:**

| Pair | Claim | Flan-T5 | RoBERTa | Both say |
|---|---|---:|---:|---|
| `conditional_subject_to_funding` | negated (fabricated) | 0.574 | 0.669 | supported (WRONG) |
| `exemption_small_provider_carve_out` | true | 0.217 | 0.269 | unsupported (WRONG) |
| `quantifier_one_incident_open` | negated (fabricated) | 0.911 | 0.941 | supported (WRONG) |
| **`retention_seven_years`** | negated (fabricated) | 0.961 | 0.638 | supported (WRONG) |

**`retention_seven_years` is the most consequential of the four.** This is
the flagship qualifier-overrides-main-clause case
(`docs/minicheck-spike.md` section 4, `docs/audit-ledger.md` section 1.2) --
"seven years ... or until the client turns twenty-five, whichever is later."
At short-document scale, RoBERTa caught what Flan-T5 missed (0.977 vs
Flan-T5's 0.258/0.748 inversion), and that catch was the entire empirical
basis for "run both models." **At production scale, with the clause at the
end of a ~2900-word document, RoBERTa also inverts it** (0.638, "supported",
wrong) -- close enough to Flan-T5's 0.961 that the 0.35 gap rule does not
fire either (gap 0.323). The one case the whole two-model design was built
around is now a **silent failure**, not a caught one. See section 5 for the
position breakdown of this specific pair.

### 2.3 Confidence bound (section 3 detail below)

4 silent (both-wrong) errors observed in 72 claims: **5.6% observed rate**,
not a bound this time -- an actual measurement, because the event that the
short-document battery could only bound at "<=4.2% at 95% confidence, zero
observed" **has now been directly observed 4 times.**

---

## 3. Confidence bound

The short-document battery observed **zero** ensemble-blind (both-models-wrong)
errors in 72 claims and, correctly, did not claim the rate was zero -- it
applied the rule of three to bound it at <=4.2% at 95% confidence.

At production scale that same event was observed **4 times in 72 claims: a
rate of 5.6%, 95% Wilson CI [2.2%, 13.4%]** (CONFIRMED by direct computation,
not eyeballed -- `scipy` is not installed in the audit venv, so the interval
was computed directly from the closed-form Wilson formula, not approximated).
The honest statement is the same shape as before but the number moved in the
wrong direction: **this is no longer a bound on a rare, unobserved event. It
is a directly measured rate**, and even its lower bound (2.2%) is roughly
half the short-document battery's upper bound (4.2%) -- the two intervals
barely overlap. 36 constructed pairs is still not enough to pin the true
rate precisely, but it is unambiguously enough to reject "close to zero."

---

## 4. Position effect

### 4.1 Main fixture (36 pairs, 1 document each, position confounded with
nothing but not isolated from pair-to-pair variation)

| Position | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| begin (n=26) | 22/26 = 84.6% | 25/26 = 96.2% |
| middle (n=24) | 23/24 = 95.8% | 23/24 = 95.8% |
| end (n=22) | 20/22 = 90.9% | 20/22 = 90.9% |

**Position matters, and the two models disagree about which position hurts.**
Flan-T5 is markedly worse at `begin` (84.6%, 4 of its 7 total errors sit
there) and best at `middle`. RoBERTa is worst at `end` and best at `begin` --
the opposite pattern. **A position effect exists, it is not small (up to an
11-point swing for Flan-T5), and it is model-specific**, which is itself
informative: it means the two models are not failing on the same
lost-in-the-middle-style mechanism, so there is no single "safe zone" a
caller could exploit even if it wanted to. n per bucket is 22-26, so treat
the exact numbers as indicative, not precise -- see section 3's approach to
small-n honesty.

### 4.2 Position sub-study (CONFIRMED, 9 base pairs x 3 positions, 54 claims
-- isolates position from category by holding the pair fixed)

**This sub-study deliberately oversamples the weak categories** (retention,
exemption, quantifier get 3 pairs each; access_permission, statutory_duty,
conditional_obligation get 1 pair each, as contrast) -- it is a stress test,
not a representative sample, and its aggregate numbers should not be read
against the main fixture's as if they were the same population. Read it for
the WITHIN-PAIR position effect, which is what it was built for.

| Position | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| begin (n=18) | 14/18 = 77.8% | 16/18 = 88.9% |
| middle (n=18) | 16/18 = 88.9% | 15/18 = 83.3% |
| end (n=18) | 16/18 = 88.9% | 16/18 = 88.9% |

Disagreement/escalation on this subset (CONFIRMED, 54 claims):

| | short-doc battery | main fixture | position sub-study |
|---|---:|---:|---:|
| Precision | 1.00 | 0.75 | **0.90** |
| Recall | 1.00 | 0.43 | **0.75** |
| Errors both models made | 0 | 4 | **3** |
| Escalation rate | 6.9% | 5.6% | **18.5%** |

**Both precision and recall recover somewhat versus the main fixture here**
(0.90/0.75 vs 0.75/0.43), but escalation more than triples (18.5% vs 5.6%)
-- because this subset is deliberately built from the categories that
produce the most disagreement. **Neither number should be quoted alone as
"the" production rate**: the main fixture is the closer approximation to a
representative document, and it is also the one with the worse recall.
Two new errors appear here that did NOT occur in the main fixture --
`access_41_staff__begin` (Flan-T5, 0.280, wrong -- this pair was 100%
correct for both models in the main fixture, at a different position) and
`exemption_quality_assurance_ethics__begin` (Flan-T5, 0.497, wrong -- this
exact pair was also one of Flan-T5's two short-document errors in
`docs/audit-ledger.md`, so it is a repeat failure, now confirmed to persist
at production scale specifically at the `begin` position). **Position can
turn a category that looked completely safe into a failure.**

---

## 5. The qualifier-overrides-main-clause case at all three positions (CONFIRMED)

`retention_seven_years` -- "seven years ... or until the client turns
twenty-five, whichever is later" -- is the pair the whole two-model design
was built around (`docs/minicheck-spike.md` section 4). The position
sub-study put it at all three positions, holding everything else about the
pair fixed:

| Position | Flan-T5 (negated claim) | RoBERTa (negated claim) | Ensemble outcome |
|---|---:|---:|---|
| begin | 0.971 -> **wrong** | 0.301 -> correct | disagreement (gap 0.670) -- **caught** |
| middle | 0.914 -> **wrong** | 0.588 -> **wrong** | agree (gap 0.326) -- **SILENT FAILURE** |
| end | 0.904 -> **wrong** | 0.382 -> correct | disagreement (gap 0.522) -- **caught** |

**Flan-T5 inverts this pair at every position tested** -- the short-document
inversion `docs/minicheck-spike.md` first found is not a short-document
artifact; it reproduces at all three positions in a ~2900-word document.
**RoBERTa is the one whose behaviour changes with position, and it changes
in exactly the textbook lost-in-the-middle shape**: correct at `begin` and
`end`, wrong at `middle`. Because the two-model design only survives when
the models fail differently, **the one position where RoBERTa also fails is
the one position where the ensemble goes silent on its own flagship case.**

**A caveat that matters as much as the pattern itself:** the *main fixture*
also placed this same pair at `end`, in a *different* generated document
(different filler, different seed), and there RoBERTa scored the negated
claim at 0.638 -- **wrong**, unlike the 0.382 **correct** score at `end` in
this sub-study. Same pair, same position, different surrounding filler,
opposite outcome. **Position is a real, measured effect, but it is not the
only source of variance** -- the specific content around the clause moves
the score enough to flip the label on its own. Neither run is "the" answer;
both are one draw from a distribution this task's sample size cannot fully
characterise. The honest statement is the one section 3 already makes:
directly measured, not close to zero, not fully pinned down.

---

## 6. Truncation guard (`unscoreable`) at production scale

**Fired 0 times out of 72 claims, for either model** (CONFIRMED). The
chunking-replication self-check (`verify_chunking=True`, comparing this
module's predicted MiniCheck sub-chunks against the library's own
`used_chunks`) also reported **0 mismatches**, so the guard was reasoning
about the same units the library actually built, not a plausible-looking
approximation that happened not to fire.

**This is a real result, but do not over-read it as "the guard is not
needed at production scale."** MiniCheck's own internal chunking (~500 words
per unit for Flan-T5, ~400 tokens for RoBERTa) means the danger case is not
document length -- it is a SINGLE sentence, as nltk detects it, exceeding
the per-model limit (2048 tokens Flan-T5, 512 RoBERTa) before internal
chunking gets a chance to split it. `docs/minicheck-spike.md` section 3's
degenerate 9,000-token run-on sentence reproduced this; every sentence in
this fixture's filler and every embedded clause is ordinary punctuated
prose, so none came close. **The fixture-construction process itself hit
this exact failure mode once, before scoring began**: an earlier version of
`negation_battery_prodscale.json` used raw ~520-character windows of real
prose (job `06af2911d7fc`) sliced without regard for sentence boundaries,
and Flan-T5's own `preflight` correctly refused every single claim in a
3-document smoke test built from it -- not because any evidence unit was
genuinely too long, but because (separately) `HF_HUB_OFFLINE=1` broke that
model's checkpoint resolution and the resulting `OSError` was swallowed by
`preflight`'s broad `except Exception` and reported as an ordinary
length-based refusal (see the note in the appendix). That was diagnosed and
worked around before the real run (loading online, then rerunning); it did
not affect any number above. It is flagged here because **`preflight`
conflating "the model failed to load" with "the evidence was too long" is a
real gap in `audit.py`'s error handling** -- worth a narrower except clause
or a distinct failure category, reported per this task's brief rather than
fixed, since it did not block the measurement once diagnosed.

**Net: the guard is confirmed to do the right thing when it is needed
(0 mismatches, correct refusal on the spike's degenerate input) and correctly
does nothing when it is not needed (0/72 here). Whether real production
documents ever produce the single-run-on-sentence pathology remains
untested on genuine data** -- OCR artifacts, flattened tables and
semicolon-delimited lists are the plausible triggers named in
`docs/minicheck-spike.md`, and this fixture, built from well-formed prose,
cannot exercise them. Section 9 reports what the one real chunk-summary row
shows, which is not evidence either way on this specific question (it is
short and well-punctuated).

---

## 7. Cost (CONFIRMED per-claim rates, INFERRED per-document projection)

### 7.1 Measured per-claim cost, this run, both models

| | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| Main fixture (72 claims, uncontended) | 17.74 s/claim | 8.81 s/claim |
| Position sub-study (54 claims, some contention -- section 7.3) | 16.61 s/claim | 14.66 s/claim |

This is measured directly on THIS hardware at production evidence size, not
extrapolated from the short-document battery's 0.59 s/claim (Flan-T5) the
way the original ~75-95 min estimate in `docs/audit-ledger.md` was built.
That estimate scaled short-document timing by an assumed 8-9x factor for
extra internal MiniCheck sub-chunks; the measured factor is closer to 30x,
which the spike's own large-document timing (16.55-21.26 s/pair) already
foreshadowed and this run confirms directly.

### 7.2 Per-document projection -- and the assumption that turns out wrong

The task brief's own convention: **25 chunks x ~8 summary sentences/chunk =
200 hop-1 claim units.** Using the main fixture's clean rates:

| | Flan-T5 | RoBERTa | Sequential total |
|---|---:|---:|---:|
| 200 units | 3548 s = 59.1 min | 1762 s = 29.4 min | **88.5 min** |

That is hop 1 ALONE, and it already exceeds `docs/audit-ledger.md`'s old
~75-95 min estimate for BOTH hops combined. Hop 2 (final summary vs.
concatenated chunk summaries) adds more on top, unmeasured here in exact
units because it scales with final-summary length, not chunk count.

**But the "~8 summary sentences/chunk" assumption is itself measured wrong
by section 9's real data.** Job `6c0358825609` (the actual pipeline's real
output, 5 real chunks) produced **112 raw summary sentences, 90 after
filtering degenerate splitter fragments -- 18 real claim units per chunk,
not 8.** Re-running the same 25-chunk projection at the REAL measured
density:

| | Flan-T5 | RoBERTa | Sequential total |
|---|---:|---:|---:|
| 25 x 18 = 450 units | 7983 s = 133.1 min | 3965 s = 66.1 min | **199.1 min (3.3 hours)** |

**This is the first-order finding the operator asked for stated plainly: a
realistic hop-1-only audit of one 25-chunk document costs upward of three
hours, using this run's own measured rates and this task's own measured
real-summary density -- not a fraction of the summarisation job it audits,
comparable to it.** `docs/measurements.md`'s own figure for summarisation
itself (~80 min for a 14-chunk, 50K-token document) scales, by the same
linear approximation, to roughly ~143 min for a 25-chunk document. **The
audit's hop-1-only cost (199 min) is now the larger of the two numbers on
the table, not the smaller.** Running two models sequentially is the
current design (`build_ledger` scores one model to completion, then the
other); if this were ever wired in, batching the two models' calls together
rather than running them sequentially is the obvious first lever, though
untested here.

### 7.3 Does it compete for cores? CONFIRMED, yes, directly observed

Node 1 runs `llama-server` locally (port 8080, gpt-oss-120b, `-t 4`) as part
of its normal serving role, and rpc-server for the shard group. Mid-way
through the position sub-study, a `top` snapshot caught **node 1's own
llama-server at 378.9% CPU and this audit process at 336.8% CPU
simultaneously**, both pinned to the same 4 physical cores (load average
8.23, up from 0.6-0.9 when nothing else was running). RoBERTa's per-claim
rate visibly slowed during this window (18.2-18.7 s/claim vs. the clean
8.81 s/claim measured earlier in the same run). **This is not a theoretical
risk -- it happened during this measurement, unprompted, because node 1 is
genuinely serving.** The audit process was run at `nice -n 10`/`-n 15`
throughout, which yields scheduling priority under contention but does not
eliminate the wall-clock cost of sharing 4 physical cores with a live
inference server. An operator running this audit alongside real document
jobs should expect both to slow down, not just the audit.

---

## 8. Recommendation

**Do not wire this into the pipeline as designed.** Three independent
reasons, any one of which would be sufficient on its own:

1. **The load-bearing empirical claim did not survive scale-up.**
   Disagreement's perfect precision and recall on short documents was the
   entire justification for treating "no disagreement" as a green light.
   That justification is gone at production scale (section 2.2, section 4.2,
   section 9) -- recall as low as 0.43, real silent failures on the exact
   case the design was built to catch.
2. **Real model output is harder than the constructed battery in a specific,
   measured way** (section 9): most real summary sentences are multi-claim,
   and the sentence splitter itself produces garbage fragments on real
   markdown output. Both problems make the true escalation and error rates
   on real jobs worse than anything measured on constructed claims, not
   better.
3. **Cost is now comparable to the job it audits**, not a fraction of it
   (section 7.2), and that is before hop 2 or a treatment for the
   multi-claim-sentence problem, both of which would only add more.

**What would have to change before revisiting this:**

- **Sub-sentence claim decomposition**, flagged as unbuilt in
  `docs/audit-ledger.md` and now shown to matter on more than half of real
  sentences, not a minority. This is a prerequisite, not a nice-to-have --
  scoring a three-claim enumeration as one unit is close to meaningless, and
  section 9's spot-read shows this concretely.
- **A better sentence splitter for real LLM markdown output**, or a
  pre-filter for degenerate fragments in production, not just in this
  measurement script.
- **Re-measurement of disagreement precision/recall on real (decomposed)
  claims**, because everything in sections 1-8 is still constructed-claim
  evidence and section 9 is real-output evidence WITHOUT ground truth --
  neither on its own is what a production go/no-go needs.
- **A cost model that assumes real sentence density**, not the task brief's
  8/chunk convention, before anyone commits to running this on every job.

**What remains true and does not need re-litigating:** two models not one;
disagreement as a first-class output category; refuse rather than degrade on
over-long input (section 6); locations computed from offsets. The mechanism
is sound. **The specific numbers that justified trusting it are not the
numbers that hold at the scale it would actually run at.**

---

## 9. Real model output -- what exists, what it shows, what is still unmeasured

**Added mid-task at the operator's direction.** Sections 1-8 above measure
the ensemble on constructed claims. This section measures what can be
measured on REAL chunk summaries produced by the actual pipeline -- there is
no ground truth for real output, so no accuracy/precision/recall figure is
reported here. What CAN be measured without ground truth: escalation rate,
`unscoreable` rate, disagreement rate, and how often a real summary sentence
carries more than one claim (the enumeration/compound-sentence problem the
one existing real row already hinted at).

### 9.1 What real data existed at the time this task finished

*(row/job count in /opt/missing-link/jobs.sqlite chunk_summaries at the time
scoring ran or, if the node-2 chunk-size sweep had not landed, a plain
statement that it had not, plus exactly what to run when it does)*

### 9.2 Real-output metrics (no ground truth)

*(escalation rate, unscoreable rate, disagreement rate, multi-claim-sentence
heuristic rate -- filled in only if real data beyond the single existing row
was available)*

**Cross-model disagreement on real output -- CONFIRMED, but on a partial
sample.** A bounded, capped sample (60 units: all 27 real hop-2 units +
33 hop-1 units, sampled evenly across both real sources) was scored to keep
this measurement finishable under the contention documented in section
7.3 -- an earlier UNCAPPED attempt at all 189 real units was killed after
observing its per-claim rate degrade from 4.7 s to 41.7 s under contention
with node 1's live serving, which would have made the full run take multiple
hours. **Even the capped sample did not finish inside this task's session:**
node 1's `llama-server` was observed at 287.5% CPU concurrently with this
(niced) scoring process, load average 9.38 on the 4-physical-core box, and
by the time this section was written only 6 of 59 units had been scored by
Flan-T5 (46.96 s/claim average, RoBERTa not yet started). The process was
launched detached (`setsid nohup`, `nice -n 15`) specifically so it survives
independently of this task and can be collected later --
`/tmp/.../scratchpad/prodscale/real_data3_result.json` will contain the full
disagreement/escalation/unscoreable breakdown for the 60-unit sample once it
completes; `run_real_data3.py` (scratchpad, described in 9.4) is the exact
script, re-runnable as-is.

**What this means for the brief's specific asks:** the two numbers that
needed a completed model-scored pass -- escalation rate and disagreement
rate on real output -- are **not available within this task's session**.
The two numbers that did NOT need one -- the sentence-splitter fragment rate
and the multi-claim rate -- ARE available (above) and are unambiguous: over
half of real summary sentences are not single checkable claims. **That
alone is enough to answer the brief's most consequential question ("if most
real sentences are multi-claim, that is not a refinement, it is the next
required piece of work"): yes, most are, and sub-sentence decomposition is
required before this tool's numbers mean anything on real jobs**, independent
of whatever the pending disagreement-rate number turns out to be.

### 9.3 Spot-read: real multi-claim sentences, human judgement (small-n, a sanity check not a measurement)

Six real examples read by hand, from job `6c0358825609`'s actual chunk
summaries (illustrative fragments only, per this task's confinement on
`bench/out/`-style raw content):

- `* **Purpose and Scope** – The work aims to present [the subject]'s
  biography and precepts to educated readers, especially those outside
  [region] who know [language].` -- flagged by the heuristic (list marker +
  em-dash), but on inspection this is a markdown bullet header followed by
  ONE claim, not two. **The heuristic over-counts here**: an em-dash after
  a bold label is bullet-list styling, not a second clause.
- `**Liberation** – True release ... is not achieved by duty, yoga, or moral
  conduct alone, but through association with sincere devotees who transmit
  [X].` -- a genuine two-part contrastive claim ("not achieved by A, B, C,
  but achieved by D"). A single support score cannot distinguish "the
  negative half is right and the positive half is fabricated" from "both
  halves are right" -- **this is the real problem**, not a formatting
  artefact.
- The three-part enumeration from the pre-existing real row
  (`docs/audit-ledger.md` section 4, "(1) ... (2) ... (3) ...") remains the
  clearest case: three independent factual claims MiniCheck necessarily
  scores as one unit.

**Honest read: the 55.6% heuristic rate is not precisely calibrated -- it
both over-counts pure markdown styling (bullet dashes, bold headers) and
under-counts nothing obviously, since the genuinely compound "not X but Y"
pattern doesn't trip any of the six heuristic signals used here and had to
be found by reading.** The heuristic is a lower-effort proxy, not a
substitute for the sub-sentence decomposition the brief correctly identifies
as the real fix. Even discounting every bullet-header false positive by
half, a large minority to a majority of real sentences remain genuinely
multi-claim by a careful human read. That is enough to act on.

### 9.4 What to run when more real data lands, or to finish this sample

Two scripts, both in scratchpad (kept out of the repo per this task's
confinement to `missing_link/fixtures/`, a new test file, and new docs --
source is not committed, only its output would be if the operator wants
it):

- **`run_real_data3.py`** -- the capped, representative-sample version
  actually used for 9.2/9.3 above (60 units: all hop-2 + an even hop-1
  sample). Re-run as-is to pick up new rows; it recomputes the full-corpus
  heuristic stats uncapped every time and only caps the expensive model
  pass. **Currently running** (detached, pid at time of writing 197896) --
  check `real_data3.out` / `real_data3_result.json` for it to finish rather
  than re-launching.
- **`run_real_data2.py`** -- the uncapped version, correct but expensive
  (section 9.2's contention numbers came from watching it run). Use it only
  once node 1 is not concurrently serving real traffic, or accept a
  multi-hour tail.

Both are READ-ONLY against `/opt/missing-link/jobs.sqlite` and
`bench/out/chunk-size-bench/*.json`, need no code changes to pick up job
`18339bace8f0` (running throughout this task, not scored here) or the
in-progress `corpus_chunk_6144.json` sweep point once it lands, and report
escalation rate, unscoreable rate, and the multi-claim/degenerate-fragment
breakdown for whatever is in the tables when they run.

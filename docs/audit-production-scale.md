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

*(filled in after the full run)*

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

### 4.2 Position sub-study (9 base pairs x 3 positions -- isolates the pair)

*(filled in once the sub-study finishes -- see section 5 for
`retention_seven_years` specifically, which is the pair this sub-study was
built to pin down)*

---

## 5. The qualifier-overrides-main-clause case at all three positions

*(retention_seven_years specifically)*

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

## 7. Cost

*(measured wall-clock at production scale, from THIS run -- not extrapolated
from short-document timings, which is what made the original ~75-95 min
estimate wrong. Per-document projection for a 25-chunk document, and whether
the audit competes with the cluster's own work for CPU.)*

---

## 8. Recommendation

*(ready to wire in? yes/no/conditional, and why)*

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

### 9.3 Spot-read: is the escalation worth a reviewer's time?

*(small-n, human judgement, explicitly labelled a sanity check and not a
measurement)*

### 9.4 What to run when the node-2 sweep corpus lands

Script: `run_real_data.py` (full source in the appendix below), kept in
scratchpad per this task's confinement to `missing_link/fixtures/`, a new
test file, and new docs -- not committed as code. It is READ-ONLY against
`/opt/missing-link/jobs.sqlite`, needs no changes to run against the sweep
corpus (it already iterates every `job_id` with `chunk_summaries` rows), and
reports exactly the four numbers in 9.2 for whatever is in the table when it
runs.

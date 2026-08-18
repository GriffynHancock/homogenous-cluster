# The audit ledger: mapping a summary against its source, and what the negation battery says about whether it can be trusted

**Built and measured 2026-08-18 on node 1.** Implements the operator's request
for a step after map-reduce that *"maps out the difference between this source
document and this summary"* and emits *"something that can be hooked into
programmatically like json ... and an amount of times each thing happened in
total and line number where it occurred."*

Labels: **CONFIRMED** (run here, output read), **REPORTED** (someone else's
number), **INFERRED** (computed from CONFIRMED numbers).

Reads with: `docs/minicheck-spike.md` (the empirical basis), `docs/EVALUATION.md`
§3 (why MiniCheck at all), `docs/DESIGN-NOTES.md` E (provenance), F25 (reduce-step
laundering), F21/F34/F38/F39 (refuse, do not degrade).

Code: `missing-link/missing_link/audit.py`, fixture
`missing-link/missing_link/fixtures/negation_battery.json`, deps
`missing-link/requirements-audit.txt`, tests `missing-link/tests/test_audit.py`.

---

> **UPDATE 2026-08-18, later the same day -- read `docs/audit-production-scale.md`
> before acting on anything below.** Everything in this file was measured on
> one-to-three-sentence constructed documents, which section 6 already flagged
> as the open question. That question has now been answered, at production
> scale (~4096-token evidence, the same 36 pairs, plus real gpt-oss chunk
> summaries): **disagreement's recall as an error signal falls from the 1.00
> measured here to 0.43-0.75, precision falls to 0.75-0.90, and the flagship
> `retention_seven_years` case that this file's VERDICT is built on goes from
> caught to a silent failure at the `middle` position.** The verdict below
> ("trustworthy enough to be a review aid") **does not survive at production
> scale** and must not be quoted on its own. This file's engineering
> observations (two hops, refuse-not-degrade, offsets computed not asked for)
> still stand; its headline accuracy and disagreement numbers do not
> generalise past the short-document regime they were measured in.

## VERDICT FIRST

**The two-model ensemble is trustworthy enough to be a review aid, and on this
battery it was better than that: cross-model disagreement caught 100% of the
errors either model made, at a 6.9% escalation rate, with zero errors that both
models made.** That is the result the whole design rests on, and it came out in
favour of the design.

**It is still not a gate, and the tool has no pass verdict.** The reason is not
the accuracy figure -- it is *which* claims failed. Both of Flan-T5-Large's
retention-clause errors are the same inversion the spike found, on the clause
type this project exists to process. A checker that scores *"records must be kept
for seven years"* as unsupported (0.258) and *"records must not be retained
beyond seven years"* as supported (0.748) is not a thing you let sign off a legal
summary, however good its aggregate number is.

**It is NOT yet ready to wire into the pipeline.** See "Is this ready to wire
in?" at the end -- the blocker is not quality, it is that every number below was
measured on one-to-three-sentence documents, and the production evidence window
is a 4096-token chunk.

---

## 1. The negation battery -- the experiment

36 pairs (72 claims), constructed so ground truth is true **by construction**:
each `supported` claim is entailed by its document and each `negated` claim
contradicts it. Seven categories drawn from what this project's real material is
made of: retention, access permission, statutory duty, deadline, exemption,
conditional obligation ("unless", "except where"), quantifier ("all staff" vs "no
staff", "41 staff" vs "no non-clinical staff"). The five pairs from
`docs/minicheck-spike.md` §4 are kept verbatim inside it, so the spike stays
reproducible.

It is a **re-runnable regression fixture**, not a throwaway:
`python -m missing_link.audit battery`.

### 1.1 Accuracy per model (CONFIRMED)

| | MiniCheck-Flan-T5-Large | MiniCheck-RoBERTa-Large |
|---|---:|---:|
| Overall | **69/72 = 95.8%** | **70/72 = 97.2%** |
| On true claims | 34/36 = 94.4% | 35/36 = 97.2% |
| On fabricated negations | 35/36 = 97.2% | 35/36 = 97.2% |
| Wall-clock, 72 claims | 42.4 s | 24.4 s |

Per category, correct / n:

| Category | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| **retention** | **8/10** | 10/10 |
| **exemption** | **9/10** | **9/10** |
| **quantifier** | 12/12 | **11/12** |
| access_permission | 10/10 | 10/10 |
| statutory_duty | 10/10 | 10/10 |
| deadline | 10/10 | 10/10 |
| conditional_obligation | 10/10 | 10/10 |

**Every error, in full (5 of 144 model-claim judgements):**

| Model | Pair | Claim | Score | Called it | Should be |
|---|---|---|---:|---|---|
| Flan-T5 | `retention_seven_years` | true | 0.258 | unsupported | supported |
| Flan-T5 | `retention_seven_years` | fabricated | 0.748 | **supported** | unsupported |
| Flan-T5 | `exemption_quality_assurance_ethics` | true | 0.023 | unsupported | supported |
| RoBERTa | `exemption_small_provider_carve_out` | true | 0.352 | unsupported | supported |
| RoBERTa | `quantifier_one_incident_open` | fabricated | 0.862 | **supported** | unsupported |

### 1.2 Which categories fail: the spike's hypothesis, refined

The spike suggested obligation clauses. **Partly confirmed, and narrower than
that.** Obligation clauses as a class are fine -- statutory duty, deadline,
access permission and conditional obligation were **40/40 for both models**,
including every "unless"/"except where"/"only if" construction in the battery.

What actually fails is **clauses with a second, competing clause attached**:

- `retention_seven_years` -- "seven years ... **or until the client turns
  twenty-five, whichever is later**". Flan-T5 inverts it. Remove the disjunction
  and its four sibling retention pairs are 8/8 across both models.
- `exemption_quality_assurance_ethics` -- two sentences setting up a
  requirement and then carving out an exception, where the claim is about the
  carve-out.
- `exemption_small_provider_carve_out` -- an exemption with an "unless" that
  cancels it.
- `quantifier_one_incident_open` -- "**every** incident was closed ... **apart
  from one** that remained open"; RoBERTa reads the universal and misses the
  exception, scoring the fabricated "all incidents were closed" at 0.862.

So: **exemption is the weakest category (9/10 for both models, and they fail
different pairs), and the failure signature is a qualifier that overrides the
main clause.** Simple polarity is not the problem -- 35/36 fabricated negations
were caught by each model. Legal drafting is full of overriding qualifiers, so
this is the shape to worry about, and it is a *sharper* warning than "negation is
hard" because it says where to look.

**Is either model systematically better?** RoBERTa-Large is ahead 70/72 to 69/72
and is 1.7× faster, but the margin is one claim and **they fail disjoint sets**:
RoBERTa gets both retention pairs Flan-T5 inverts; Flan-T5 gets both pairs
RoBERTa misses. **The honest reading is that neither dominates and that their
independence is the useful property, not their individual scores.** Which is the
argument for running both, arrived at from the data rather than from caution.

### 1.3 THE LOAD-BEARING RESULT: does disagreement predict error? (CONFIRMED)

The whole two-model design rests on disagreement being signal, not noise.

Two models "disagree" if their labels differ, or if their support probabilities
differ by ≥ 0.35.

| | Both right | At least one wrong |
|---|---:|---:|
| **Models disagree** | **0** | **5** |
| **Models agree** | 67 | **0** |

- **Precision of disagreement as an error signal: 1.00** -- every escalation was
  a real error. No false alarms at all.
- **Recall: 1.00** -- no error escaped the disagreement filter.
- **Errors both models made: 0.** There is no claim in this battery where a
  reader trusting the agreed label would have been misled.
- **Escalation rate: 5/72 = 6.9%** -- the human-review cost.

**Read this with the sample size firmly in mind.** Perfect precision and recall
are computed over **five** errors. The number that actually matters for safety is
the zero in the bottom-right cell, and zero events in 72 trials bounds the
ensemble-blind error rate at **≤4.2% at 95% confidence** (rule of three,
INFERRED). It does not establish that it is zero. What can be said CONFIRMED is
narrower and still worth having: **on 36 constructed obligation/permission pairs,
there was no case where both checkers were wrong together, and the one case the
spike identified as dangerous was caught.**

**All five escalations came from label divergence.** The 0.35 probability-gap
rule fired **zero** times on its own -- no pair had matching labels with a gap
that wide. So `DISAGREEMENT_GAP` is an unexercised belt-and-braces rule, not a
measured threshold, and must not be quoted as one. It is kept because 0.95/0.55
agrees on a label while plainly not agreeing, and on this battery it cost
nothing.

---

## 2. The deterministic negation check: it did NOT earn its place

A negation-cue polarity check is trivially cheap, has no model failure mode, and
plausibly catches exactly what MiniCheck inverts. It was built, and then
measured. **It is implemented, kept, and DEFAULT OFF.**

Standalone, as a fabrication detector (CONFIRMED):

| Cue set | Fires on | Recall on fabrications | False positives on true claims | Accuracy |
|---|---:|---:|---:|---:|
| default | 26/72 (36%) | 22/36 = 61.1% | 4/36 = 11.1% | 75.0% |
| wide (+ "beyond", "only") | 27/72 (38%) | 61.1% | 5/36 = 13.9% | 73.6% |

The wide cue set is strictly worse -- one more false positive, no extra catches
-- so "beyond" and "only" stay held back. They appear constantly in non-negated
legal prose ("funding **beyond** June 2027", "**only** two of seven sites").

**But standalone accuracy is the wrong question. The question is what it adds on
top of the ensemble, and the answer is nothing (CONFIRMED):**

- It fires on **26 of 72** claims.
- Of the **5** claims the ensemble got wrong, it catches **1**.
- **25 of its 26 firings** are on claims disagreement did not flag -- and **none
  of those 25 is an error**.

Projected onto a real job (INFERRED): a 200-sentence summary containing, say, 5
fabrications would draw **~72 polarity flags**, of which ~1 is a real problem.
The ensemble would draw ~14. A flag that fires on a third of every summary while
adding no detection the ensemble does not already provide is **worse than no
flag**, because it teaches the reader to skim past the flags that matter. That
is the failure mode the operator was warned about and it is exactly what the
measurement shows.

**Why it is kept rather than deleted:** it is the only check that caught the
flagship retention inversion **without a model** (it flags *"must not be retained
beyond seven years"* against a document that says *"must be retained for seven
years"*, correctly, in microseconds), and it costs nothing when a reviewer
specifically wants a polarity sweep. `--polarity` turns it on; the ledger records
`config.polarity_check` either way. It also fails completely on quantifier flips
("all twelve files" vs "three files"), which carry no negation cue at all --
worth knowing before anyone reaches for it again.

---

## 3. What the tool is

`missing-link/missing_link/audit.py`, standalone. **Not called from `worker.py`,
not in the job flow.** Run by hand:

```
python -m missing_link.audit job --db /opt/missing-link/jobs.sqlite --job-id XXXX --out ledger.json
python -m missing_link.audit battery --out battery.json
python -m missing_link.audit battery --polarity-only      # no models, no torch
```

### 3.1 Two models, always

`build_ledger` **raises** if given fewer than two scorers. Disagreement is a
first-class output category, not an internal detail, and it is ranked **above**
agreed-unsupported in the finding order -- because a single-model gate is
precisely what would have passed the spike's inverted retention clause silently.

### 3.2 Two hops, correctly scoped

Both windows follow arXiv:2511.07689: metrics are unreliable at whole-document
scope and improve markedly when the evidence window is correctly scoped,
*particularly for legal text*.

| Hop | Claim | Evidence | Catches |
|---|---|---|---|
| `chunk_vs_source` | each sentence of a chunk summary | `document[start_char:end_char]` for **its own chunk** | fabrication at the map step |
| `final_vs_chunk_summaries` | each sentence of the final summary | **concatenation of the chunk summaries** | **reduce-step laundering** |

Hop 2's evidence is deliberately *not* the raw document. The reduce step never
saw the document; it saw the chunk summaries. Scoring against what it actually
read is what separates "reduce invented this" from "reduce faithfully carried a
bad chunk summary" -- and the second shows up in hop 1 instead. That separation
is F25's and `DESIGN-NOTES.md` E's central worry made checkable.

**Hop 2 is SKIPPED, with a note, when a job has one chunk.**
`worker.summarise_traced` returns `records[0]["summary"]` unchanged for a
single-chunk document, so the final summary *is* the chunk summary and hop 2
would score text against itself and report a flawless pass. A fabricated
reassurance is worse than a missing hop.

### 3.3 Locations are computed, never asked for

No model is asked for a line number -- models are poor at it (38% accuracy on the
*easier* citation-validation task; off-by-10-to-50-line errors). Every line
number is derived by counting newlines up to a stored character offset.

For hop 1 the source location is **direct**: the chunk's own
`start_char`/`end_char`, which is what the `DESIGN-NOTES.md` E provenance change
exists for. For hop 2 a final-summary sentence has no source offset, so it is
located via the chunk summary it best matches by content-word containment, and
the schema **says so**: `location_confidence: "indirect"`, plus `match_method`,
`match_score`, and a `location_note` stating in words that the span names where
to start reading, not the sentence that produced the claim.

### 3.4 The ledger schema

```jsonc
{
  "schema_version": 1,
  "verdict": "review_required" | "no_flags_raised",   // never "pass"
  "disclaimer": "...flags for review... never certifies...",
  "models":  [{"name": "flan-t5-large", "max_model_len": 2048}, ...],
  "config":  {"support_threshold": 0.5, "disagreement_gap": 0.35,
              "sentence_splitter": "nltk-punkt" | "regex-fallback",
              "polarity_check": false, "hops": [...], "category_priority": {...}},
  "totals":  {"claims_examined": N, "claims_scoreable": N, "claims_unscoreable": N,
              "claims_flagged": N, "findings": N,
              "by_category": {"model_disagreement": 1, ...},   // COUNT PER CATEGORY
              "by_hop": {"chunk_vs_source": 1, ...}},
  "findings": [{
      "id": "h1:0:2:model_disagreement",
      "category": "model_disagreement" | "unsupported" | "polarity_mismatch" | "unscoreable",
      "priority": 1,
      "hop": "chunk_vs_source" | "final_vs_chunk_summaries",
      "detail": "plain-English sentence for the human",
      "claim":    {"unit": "chunk_summary", "chunk_index": 0, "sentence_index": 2,
                   "start_char": 419, "end_char": 526, "line": 1, "text": "..."},
      "evidence": {"unit": "source_document", "chunk_index": 0,
                   "start_char": 0, "end_char": 2202,
                   "start_line": 1, "end_line": 34,
                   "location_confidence": "direct" | "indirect" | "none"},
      "scores":   [{"model": "...", "support_prob": 0.44, "label": "unsupported",
                    "evidence_units_seen": 2}, ...],
      "models_agree": false, "prob_gap": 0.514
  }],
  "notes": ["Hop 2 was NOT run because ..."],
  "coverage_note": "..."
}
```

**Extensibility contract, stated so a consumer can rely on it:** `by_category` is
a **map**, so a new category is an added key, never a new column. A consumer
**must** iterate `totals.by_category` rather than reading a fixed set of keys, and
**must** ignore an unrecognised `category` rather than failing. Every finding
carries the same common fields (`id`, `category`, `priority`, `hop`, `detail`,
`claim`, `evidence`, `scores`) whatever its category, so a generic renderer works
for categories that did not exist when it was written.

**Finding order is part of the contract** and is argued, not arbitrary:

| priority | category | why here |
|---:|---|---|
| 0 | `unscoreable` | the reader must first know what was **never checked at all** |
| 1 | `model_disagreement` | where the measured failure lived; above agreed-unsupported deliberately |
| 2 | `unsupported` | both checkers flag it |
| 3 | `polarity_mismatch` | cheapest, noisiest, lowest |

**What it deliberately does not report.** The operator's phrasing was "present in
A, not present in B". The ledger reports claims present in the **summary** that
its evidence does not support -- the fabrication direction. It does **not** report
material present in the **source** but absent from the summary, because omission
is what summarising *is*: that flag would fire on nearly every sentence of every
document, which is the same "trains the reader to ignore it" failure that
disqualified the polarity check by measurement.

### 3.5 Refuse, do not degrade -- and do not trust the library's own diagnostic

`docs/minicheck-spike.md` §3 confirmed that when a single nltk-detected sentence
exceeds the model's limit, MiniCheck's tokenizer truncates silently, **can drop
the claim entirely**, and **`used_chunks` misreports it** -- it is built from the
pre-truncation string, so a caller checking it would wrongly believe nothing was
lost.

So the guard does not consult `used_chunks`. `MiniCheckScorer.preflight`
replicates MiniCheck's own sentence-splitting and chunk-grouping, then tokenizes
the **exact** string the model will be fed (`"predict: " + unit + eos + claim` for
Flan-T5) with the **model's own tokenizer**, and compares against
`max_model_len`. Over the limit → refuse, and record an `unscoreable` finding
naming the token count, the limit, and why. A unit any model refuses is scored by
**none** of them: a row backed by one model where the schema promises two is
exactly the quiet degradation this codebase keeps being bitten by.

**Verified on the spike's degenerate input (CONFIRMED, both models):**

| | Flan-T5-Large | RoBERTa-Large |
|---|---:|---:|
| limit | 2048 | 512 |
| measured tokens for the run-on unit | 9025 | 9542 |
| preflight | **refused** | **refused** |
| unguarded score, TRUE claim | 0.4553 unsupported | 0.4117 unsupported |
| unguarded score, FABRICATED claim | **0.4553** unsupported | **0.4117** unsupported |
| `used_chunks` reports | 24,970 chars | 24,970 chars |
| tokens the model was actually fed | 2048 | 512 |
| claim present in what the model saw | **False** | **False** |

Unguarded, a true claim and a fabricated one get **byte-identical scores** because
neither was in the input. That number is not wrong, it is meaningless, and
nothing in the library's output says so.

**New, not in the spike: this bites RoBERTa-Large four times harder.** Its limit
is 512, not 2048, so a merely long single "sentence" -- a flattened table row, a
semicolon-delimited list, an OCR paragraph with dropped periods, ~600 tokens --
is unscoreable for RoBERTa while Flan-T5 handles it fine. Because a refusal by
either model refuses the pair, **RoBERTa's 512-token limit sets the tool's real
input ceiling.**

The replication of MiniCheck's internal chunking is checked against the library
itself: `run_battery(..., verify_chunking=True)` compares our predicted units
with the library's returned `used_chunks` on every scored pair. **0 mismatches
across 144 scorings (CONFIRMED)**, so the guard is measuring the units the
library actually builds, not a plausible-looking approximation.

---

## 4. Run against real data (CONFIRMED, n=1)

Job `2b4c926a799a` in `/opt/missing-link/jobs.sqlite` -- the health-records
retention memo, the only job with chunk rows. Read **read-only**
(`sqlite3.connect("file:...?mode=ro", uri=True)`); the auditor is an observer and
must never be able to write to, migrate or lock a database a worker is using.

3 claim sentences, 1 chunk (2202 chars). Flan-T5 6.4 s, RoBERTa 5.8 s. Hop 2
skipped with a note (single chunk). Result: **1 finding, a `model_disagreement`.**

| sentence | Flan-T5 | RoBERTa | outcome |
|---|---:|---:|---|
| the 418-char three-part audit-findings enumeration | 0.962 | 0.448 | **flagged, gap 0.514** |
| "Records must be kept for seven years from the last service (or until a minor turns 25...)" | 0.943 | 0.801 | passing |
| "The permission breach was corrected in March 2026." | 0.984 | 0.982 | passing |

**And the flagged one is arguably a false escalation, which is worth stating
plainly.** The sentence is a single 418-character enumeration carrying three
distinct factual claims. MiniCheck's own documentation says sentence-level
prediction beats whole-response prediction; a three-claim "sentence" is closer to
the second. RoBERTa also had to split this evidence into 2 units against
Flan-T5's 1 (its 512-token limit again), so the two models were not even scoring
against the same windows.

**This is a real limitation of the tool as built:** the claim unit is a sentence,
and a compound sentence is not a single checkable claim. Sub-sentence claim
decomposition would fix it and is not implemented. On this one real row it cost
one escalation out of three sentences -- a 33% escalation rate against the
battery's 6.9%, on n=1, which is a warning and not a measurement.

---

## 5. Cost

| | measured |
|---|---|
| battery, 72 short claims, Flan-T5 | 42.4 s (CONFIRMED) |
| battery, 72 short claims, RoBERTa | 24.4 s (CONFIRMED) |
| real job, 3 claims vs a 2202-char chunk, both models | 12.2 s (CONFIRMED) |
| per pair against a **full 4096-token chunk**, Flan-T5 | 17-21 s (CONFIRMED, spike §2) |

Battery timings are **not** a guide to production cost: the battery's documents
are one to three sentences, so MiniCheck builds a single internal unit per pair.
A real chunk becomes 6-9 internal units and costs 8-9× more. The spike's
projection stands: **~55-71 minutes of audit for a 25-chunk document with
Flan-T5 alone**, and running two models roughly adds RoBERTa's share on top --
call it INFERRED ~75-95 min for the pair. That is a second real cost centre on an
overnight job, not a background nicety, and it is one of the reasons this is not
wired in yet.

---

## 6. Is this ready to wire into the pipeline?

**No -- not yet, and the blocker is evidence, not code.**

What would have to be true first:

1. **The battery is short-document evidence.** Every accuracy and disagreement
   number above was measured against one-to-three-sentence documents, chosen
   deliberately so nothing in the surrounding text could rescue a checker that
   misread the clause. The production evidence window is a **4096-token chunk**,
   and the spike already noted it could not tell whether length and redundancy
   were what saved its one real-data row. Disagreement's perfect precision on
   short documents is not evidence about its precision on long ones. **Re-run the
   battery with each pair embedded in a full-size chunk before trusting the
   escalation rate.**
2. **The one real-data run escalated 1 of 3 sentences**, on a compound sentence.
   If that rate holds on real summaries the ledger is too noisy to be useful, and
   the fix -- sub-sentence claim decomposition -- is unbuilt and unmeasured.
3. **The cost is ~75-95 min per 25-chunk document (INFERRED).** That is a
   scheduling decision for the operator, not a default.
4. **`unscoreable` has never fired on real material.** The truncation guard is
   confirmed against constructed degenerate input. Whether real PDFs on this
   fleet produce over-long units -- particularly against RoBERTa's 512-token
   limit, which is the binding one -- is unknown until real documents run through
   it.

**What is settled and should not be re-litigated:** two models not one;
disagreement as a first-class output; the tool flags and never certifies;
locations computed from offsets rather than asked for; refuse rather than degrade
on over-long input; and the polarity check staying off by default.

---

## Not established -- do not cite as settled

- **That disagreement predicts error on full-length chunks.** Measured only on
  short documents (§1.3, §6.1).
- **That ensemble-blind errors are rare.** Zero observed in 72 claims bounds it
  at ≤4.2% (95%, rule of three, INFERRED); it does not measure it.
- **The 0.35 `DISAGREEMENT_GAP`.** Never fired on its own. Not a measured
  threshold.
- **That either model is better.** One claim apart, on disjoint failures.
- **Whether silent truncation occurs on real project documents.** The mechanism
  is CONFIRMED; an occurrence on real material is not.
- **DeBERTa-v3-Large** as a third or replacement checker -- still untested, as in
  the spike.
- **Sub-sentence claim decomposition.** Identified as the fix for compound-claim
  escalations. Not built, not measured.

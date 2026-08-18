# Two scopes, and a canonical entity index

**Built and measured 2026-08-18 on node 1. No inference was run; nothing here
calls llama-server.** All measurement is CPU string work against
already-generated output, read-only against `bench/out/chunk-size-bench/` and a
copy of the job store.

Labels: **CONFIRMED** (run here, output read), **REPORTED** (someone else's
number), **INFERRED** (computed from CONFIRMED numbers).

Code: `missing-link/missing_link/cascade.py`,
`missing-link/missing_link/entity_index.py`, tests
`missing-link/tests/test_two_scope.py` (36 tests). Reads with
`docs/faithfulness-cascade.md`, which this extends, and F41/F42.

---

## VERDICT FIRST

**Change 1 (two scopes) is unambiguously good and should ship.** It costs
between **+4.7% and +16.4%** of the cheap tier's wall clock — 0.08 to 0.20
seconds on a whole document — and it separates the most serious thing the tool
can say from the least serious. It **cannot** turn a failure into a pass, by
construction, and the measured catch rates are unchanged.

**F42's fabrication is still caught and is now categorised as a fabrication.**
The reduce step's invented death year `1534` is reported as
`number_fabricated`, with the reason stated: *"occurs nowhere in the document
either, so it was not attributed to the wrong section — it is not in the source
at all."* The other finding on the same job, the `15th`-century claim, is now
`number_misattributed` and names the chunk that does contain the supporting
year — which is exactly what `docs/faithfulness-cascade.md` §3 had established
by hand.

**Change 2 (the entity index) removes a quarter of the false positives and does
not touch the established catch rate — but it IS a softer matcher and there is
a measured regression to report.** On 1,014 real claims the per-claim
false-positive rate falls **11.34% → 8.48%**, and flagged terms fall
**193 → 124**. Fabricated-name catch is **unchanged at 98.8%**, and its
*discrimination* improves (90.8% → 95.0%). But on a new, harder battery —
**1-character corruptions of real names** — catch falls **36.1% → 14.6%**.

**Said plainly, as the brief requires: the entity matcher got softer, and on
one measured battery it is worse.** The recommendation that follows from that
is in §6: keep the entity signal on ROUTE (it may never fail a claim), and use
`--entity-rules strict` on any corpus that is not OCR damage.

**One candidate rule was measured and rejected for exactly this reason.**
`part_fuzzy` removed 0.9 points of false positives and cost 5.4 points of
near-miss catch. Trading catch for quiet is the direction this checker must
never move in, so it is implemented, off, and documented in
`entity_index.REJECTED_RULES` with its numbers.

---

## 1. Change 1 — a citation error is not a fabrication

`cascade.py` checked every figure in a claim against **its cited span only**.
That scoping is right and is not being replaced: correctly-scoped evidence
windows are what make the number tier's 0/978 false-positive rate possible, and
widening the primary check to the whole document would make almost everything
"supported" by coincidence.

What one scope cannot do is tell these two apart:

| | what happened | how bad |
|---|---|---|
| **citation error** | figure absent from the cited span, **present in another chunk** | the model attributed correct information to the wrong section |
| **fabrication** | figure absent from **every chunk** | the model invented it |

Both surfaced as `number_unsupported`, so the most serious finding the tool can
make was indistinguishable from a misattribution.

### The fix is a second pass, not a replacement

```
check the cited span
  -> present?  done. The wider scope is never consulted.
  -> absent?   NOW check every other chunk of the same document.
                 found     -> number_misattributed  (names the chunk)
                 not found -> number_fabricated
                 no scope supplied -> number_unsupported, unchanged
```

Three properties, all load-bearing and all asserted by test:

- **It can never turn a failure into a pass.** A figure supported by chunk 12
  is still not supported by chunk 3, which is what the model cited.
- **It costs nothing on the common path.** `test_a_figure_present_in_its_own_
  span_never_triggers_the_wider_search` passes a `DocumentScope` subclass whose
  `find_number` raises, and a passing claim does not trip it.
- **`number_unsupported` is retained** for the case where no document scope was
  given. A checker with nothing wider to look in must not claim to know which
  of the two it found.

### Which signals were extended, and which deliberately were not

| signal | two-scope? | why |
|---|---|---|
| **numbers** | **yes** | a value is a value; `1534 in document` is the same `in` at either scope |
| **entities** | **yes**, via the index | this is what makes "is this a person the document talks about" answerable |
| lexical overlap, trigram similarity | **no** | they are proportions of the claim covered by the evidence, and a whole document trivially covers most of any claim's vocabulary. Widening them reports "supported" for everything |
| polarity, quantifier scope, dropped qualifier | **no, and this is the important refusal** | each works by finding the claim's *closest evidence sentence*. Searching the whole document would find *some* sentence whose polarity happens to agree, and would clear the claim on the strength of a passage the model never cited. That is the laundering the wider scope exists to expose |

---

## 2. Change 2 — a canonical entity index

The old entity check asked *do these exact characters occur in this
4096-token window*. `docs/faithfulness-cascade.md` §6 measured that flagging
one faithful sentence in seven, and inspection said the checker was wrong
nearly every time: the source is OCR mojibake and the model **correctly**
reconstructs the transliteration.

The operator's proposal, and it is a better mechanism:

> *"we need a system that can recognise a person out of a list of people in a
> corpus sometimes right? to make sure they are mentioned in the document?"*

`entity_index.EntityIndex` builds, per scope, the canonical set of entities
that scope actually contains (`cascade.extract_entities` plus every token), and
resolves a claim's name against **that**, reporting which scope matched.

### Why it works, in one sentence

**The old matcher compacted a whole multi-word name into one string and
fuzzy-matched it against individual SOURCE TOKENS.** `Śrīla Kedārnāth` compacts
to 14 characters; a source token is about 8; the length band rejected the
comparison before `SequenceMatcher` was ever called. Entity-to-entity
comparison is the fix, and it is where the whole improvement comes from.
Actual resolutions the index recovers, CONFIRMED on real output:

```
'Bhakti Sundar Govinda Dev-Gosvāmī Mahārāja' -> 'Bhakti Sundar Govinda Dev- Goswåm'   0.831
'Bhakti-rasā-mṛta-sindhu'                    -> 'Bhakti-rasåmùta-sindhuè'             0.927
'Gadadhar Pañait'                            -> 'Gadådhar Paòàit'                     0.929
'Vraja-mahala'                               -> 'Vraja-maòàala'                       0.870
```

### The rule ladder, strictest first

| rule | what it does | status |
|---|---|---|
| `exact` | compact form equals an indexed entity or token | on |
| `substring` | folded name occurs in the scope text | on (pre-existing) |
| `compact_substring` | punctuation-insensitive containment | on (pre-existing) |
| `all_parts` | every token of the name occurs in the scope | on (pre-existing) |
| `initials` | `K.P. Dutt` ↔ `Kedarnath Prasad Dutt` | **new**, on |
| `part_fuzzy` | every token fuzzy-matches some scope token | **implemented, REJECTED** |
| `whole_fuzzy` | whole name fuzzy-matches an indexed **entity** or token | on (now entity-aware) |

Resolution stops at the first rule that hits, and tries each rule across **all**
scopes before moving to the next — so a name exactly present in one scope is
never resolved by a fuzzy match in another.

### `initials`, and the three constraints that stop it laundering

Right-anchored on the surname, because the surname carries the identity and is
what a fabrication changes:

- the last token of the short form may not itself be an initial — a bare `K.P.`
  resolves to nothing;
- the anchor must match in full (`SURNAME_FUZZ = 0.90`), so one character of
  OCR damage survives but a different surname does not;
- every remaining token must be the same word or a single letter beginning the
  corresponding word, **in order** — no skipping.

`K.P. Smith` therefore does **not** resolve to `Kedarnath Prasad Dutt`
(asserted by test). CONFIRMED firing on real material: on job `6c0358825609`,
`Kedarnath Prasad Dutt` resolves to `K.P. Dutt` in chunk 0 by rule `initials`.
It fires rarely — this corpus has few initialised names, and the sweep shows it
moving none of the three headline rates — but it is what the brief asked for
and it is now checkable rather than assumed.

### Two extraction defects fixed on the way, both measured

Both produced a **false hard flag on correct output**, which is the pattern
`docs/faithfulness-cascade.md` §8 names: *a checker's bugs land almost entirely
on the sentences that were going to pass.*

- **A sentence dash is not a word hyphen.** `normalise_lookalikes` maps en and
  em dashes to `-` because the number scanner needs `1485–1534` to read as a
  range — but `_CAP_SEQ_RE`'s token class contains `-`, so real output like
  `Gaura-līlā—are` and `consciousness—lower` became the proper nouns
  `Gaura-līlā-are` and `Muslim-offers`, which occur in no source anywhere.
  **This is the same class of defect as the non-breaking hyphen that read
  `twenty‑four` as 20 and 4**, arriving from the opposite direction. Fixed with
  a separate, length-preserving `entity_text()` used only by entity
  extraction; the number scanner is untouched, and a test pins both.
- **`and` does not span a proper-noun phrase.** `Bhagavad-Gītā and
  Srimad-Bhāgavatam` was extracted as one "name". Both halves are still pushed
  individually, so nothing stopped being checked.

Together these account for 6 of the 115 baseline flags (11.34% → 10.75%); the
index accounts for the rest.

---

## 3. Measured: the false-positive rate on real, untampered output (CONFIRMED)

1,014 claim units — the five chunk-size sweep corpora plus both hops of job
`6c0358825609`. **Every flag here is a candidate false positive: this is
correct model output.** Same claims, same evidence windows, three matchers.

| population | claims | claims w/ entities | BEFORE | + extraction fixes | + index | before | after |
|---|---:|---:|---:|---:|---:|---:|---:|
| chunk 1024 | 281 | 112 | 35 | 33 | 26 | 12.5% | 9.3% |
| chunk 2048 | 178 | 76 | 22 | 21 | 15 | 12.4% | 8.4% |
| chunk 3072 | 126 | 55 | 13 | 12 | 10 | 10.3% | 7.9% |
| chunk 4096 | 123 | 53 | 17 | 16 | 12 | 13.8% | 9.8% |
| chunk 6144 | 87 | 47 | 12 | 11 | 7 | 13.8% | 8.0% |
| job `6c0358825609` | 219 | 107 | 16 | 16 | 16 | 7.3% | 7.3% |
| **all real claims** | **1014** | **450** | **115** | **109** | **86** | **11.34%** | **8.48%** |

- **per-claim false-positive rate 11.34% → 8.48%** — a quarter of the flags gone
- restricted to claims that contain any entity at all: **25.56% → 19.11%**
- **flagged TERMS 193 → 124** (of 1,423 extracted) — a 36% reduction
- of the 124 remaining, **85 are absent from the whole document** and **39 are
  present elsewhere in it**, i.e. a third of what is left is now correctly
  identified as a citation error rather than a possible invention

**This is not the 15.2% the earlier sweep reported**, and the difference is
population, not disagreement: that figure was 592 claims and this is 1,014, on
a splitter that has changed since. The delta is what matters and both columns
were computed by the same code on the same claims in the same run.

### What is still flagged, and why it is not fixable by matching

The residue is dominated by (a) capitalised religious adjectives whose source
spelling is too damaged for any threshold — `Vaishnava` against a source that
writes `Vaiùòava`, which folds to `vaiuoava` — and (b) markdown heading words
the model writes in its own summary (`Purpose and Scope`, `Overall Emphasis`,
`Highlights`). The second is a structural problem, not a matching one, and was
left alone deliberately: suppressing capitalised phrases in heading position
would be corpus-fitting, and the same suppression would hide a fabricated name
written in a heading.

---

## 4. Measured: did the catch survive? (CONFIRMED)

**Both directions, reported together, as the brief requires.**

### 4a. Fabricated proper names — the established battery

Real claims with a supported single-word proper noun replaced by an invented
surname absent from the document.

| | mutations | caught | rate |
|---|---:|---:|---:|
| OLD matcher | 400 | 395 | **98.8%** |
| NEW index | 400 | 395 | **98.8%** |

**Unchanged.** The five misses are three collisions of one generated fake
(`Marchetti` against the real word `marched`, exactly at the 0.75 threshold)
and two cases the *extractor* never proposed as a term at all — a single
capitalised word at the start of a sentence, which the extractor skips by
design. Neither is a matcher regression.

**Discrimination improved.** Running the *uncorrupted* name through the same
claim and span as a control:

| | flagged the CORRECT name anyway | discriminating catches |
|---|---:|---:|
| OLD | 32 / 400 = 8.0% | 363 = **90.8%** |
| NEW | 15 / 400 = 3.8% | 380 = **95.0%** |

### 4b. Near-miss names — a new and harder battery, and a REGRESSION

A 1-character substitution inside a real name. **This is the honest test of
permissiveness**, and it is the measurement that decides whether the matcher
became a laundering machine.

| | mutations | caught | rate | discriminating |
|---|---:|---:|---:|---:|
| OLD matcher | 316 | 114 | **36.1%** | 89 = 28.2% |
| NEW index | 316 | 46 | **14.6%** | 34 = 10.8% |

**This is a real loss and it is not an artefact of the control.** The old
matcher's extra catches are genuine discrimination (28.2% vs 10.8%), not the
same false positive counted as a win.

**The cause is the mechanism itself.** The old matcher flagged nearly every
multi-word name whose exact characters were not in the span — which is
simultaneously why it caught corrupted names and why it produced the false
positives being fixed. The two are the same behaviour. The sweep in §5 shows
they move together monotonically with no knee: **it is one dial, not a
tradeoff with a sweet spot.**

**And there is no string-level signal that separates the two cases on this
corpus.** Genuine OCR variants score 0.83–0.93 against the true name; a
1-character corruption scores 0.92–0.95. They overlap. Telling them apart would
need a character-confusion model fitted to this scanner's output, which is
corpus-fitting of exactly the kind this project rejects.

### 4c. Mutated figures — Change 1's effect on the number tier

| mutation | mutations | caught | rate | categorised |
|---|---:|---:|---:|---|
| replacement **absent from source** | 25 | 25 | **100%** | 25 fabrication, 0 citation error |
| replacement **unconstrained** | 29 | 24 | **82.8%** | 20 fabrication, 4 citation error |

**No catch was lost. Every mutation that was a hard failure before is a hard
failure now** — `measure_cost` asserts the `hard_fail_number` count is
identical with and without the scope, on every population. The five
unconstrained misses are all value collisions inside the cited span, the
inherent ceiling `docs/faithfulness-cascade.md` §4 already names.

**The one cost of Change 1, stated:** when an arbitrary fabricated value
happens to occur somewhere else in the document, it is now labelled
`number_misattributed` rather than `number_fabricated` — 4 of 24 in the
unconstrained battery. It is still a hard failure and still in the ledger; the
category is a weaker claim about it. That is the correct behaviour (the checker
genuinely cannot tell those apart) but it means **`number_misattributed` is a
floor on severity, not a verdict of innocence**, and the finding says so.

---

## 5. Measured: the rule and threshold sweep (CONFIRMED)

Same 1,014 claims and both mutation batteries, every row.
FP = flags on correct output (lower better); FAB = fabricated names caught;
NEAR = 1-character corruptions caught (both higher better).

| configuration | FP | FAB | NEAR |
|---|---:|---:|---:|
| **BEFORE — matcher + extractor as shipped** | **11.34%** | **98.8%** | **36.1%** |
| no fuzzy at all (`STRICT_RULES`) | 17.26% | 99.5% | **100.0%** |
| index, +`exact` +`initials`, no `part_fuzzy`, whole_fuzz 0.75 — **SHIPPED** | **8.48%** | **98.8%** | 14.6% |
| index, all rules, whole_fuzz 0.75, part_fuzz 0.80 | 7.59% | 98.8% | 9.2% |
| index, all rules, whole_fuzz 0.75, part_fuzz 0.86 | 7.99% | 98.8% | 13.9% |
| index, all rules, whole_fuzz 0.75, part_fuzz 0.90 | 8.09% | 98.8% | 14.6% |
| index, all rules, whole_fuzz 0.75, part_fuzz 0.93 | 8.48% | 98.8% | 14.6% |
| index, no `part_fuzzy`, whole_fuzz 0.80 | 9.96% | 99.5% | 15.1% |
| index, no `part_fuzzy`, whole_fuzz 0.85 | 12.92% | 99.5% | 43.9% |
| index, no `part_fuzzy`, whole_fuzz 0.90 | 15.88% | 99.5% | 87.5% |

**Read the whole-fuzz rows as one dial.** 0.75 → 0.90 moves FP 8.5% → 15.9%
and NEAR 14.6% → 87.5%, monotonically. There is no knee, so there is no
threshold that buys both — which is why `WHOLE_FUZZ` is left at **0.75**, the
value the project had already measured as `ENTITY_FUZZ`. Moving it is a
separate decision with its own evidence, not a side effect of this one.

**`part_fuzzy` is rejected on these numbers.** At 0.80 it buys 0.89 points of
FP and costs 5.4 points of NEAR; at 0.93 it is a no-op. Cheaper flags bought
with lost catches is the wrong direction, so it ships off.

---

## 6. Cost (CONFIRMED)

### The second pass

| population | claims | no second pass | two-scope | delta |
|---|---:|---:|---:|---:|
| chunk 1024 | 281 | 1.17 s | 1.37 s | **+16.4%** |
| chunk 2048 | 178 | 1.32 s | 1.52 s | +14.6% |
| chunk 3072 | 126 | 1.31 s | 1.44 s | +10.1% |
| chunk 4096 | 123 | 1.56 s | 1.64 s | +4.7% |
| chunk 6144 | 87 | 1.56 s | 1.64 s | +5.5% |
| job `6c0358825609` | 219 | 2.31 s | 2.50 s | +8.3% |

**+4.7% to +16.4%, i.e. 0.08 to 0.20 seconds on a whole document.** The
absolute figure barely moves with document size because the second pass runs
per *failure*, not per claim, and there are one or two of those in a document.

F41's ~17.7 s **per claim** for MiniCheck is why combinatorial checking was
unthinkable. String matching over a handful of chunks is three orders of
magnitude cheaper than one classifier call.

### The whole audit got faster, not slower

| population | BEFORE (code on `main`) | AFTER (two-scope ON) | change |
|---|---:|---:|---:|
| chunk 1024 | 2.41 s | 1.37 s | −43% |
| chunk 2048 | 2.92 s | 1.52 s | −48% |
| chunk 3072 | 2.99 s | 1.44 s | −52% |
| chunk 4096 | 3.73 s | 1.64 s | −56% |
| chunk 6144 | 3.76 s | 1.64 s | −56% |
| job `6c0358825609` | 5.81 s | 2.50 s | −57% |

Not a micro-optimisation: the old code re-folded and re-scanned the **whole
4096-token evidence span for every claim scored against it**, about eighteen
times per chunk. `build_cascade_ledger` now builds one `NumberEvidence` and one
`EntityIndex` per **distinct** evidence text. Building the entity index was the
thing that looked unaffordable on paper; caching it made the audit twice as
fast *while* adding a second scope.

---

## 7. The corpus scope: exposed, and permanently unable to grant support

`db.corpus_documents` holds other documents' text, so a cross-document index is
nearly free. `cascade.corpus_annotation_index(db_path)` builds one and
`--corpus-db` wires it in.

**It may never grant support, and that is the definition of the job, not
caution.** The summary is supposed to represent *this* document. A checker that
resolved a name from a different document and called the claim supported would
be a fabrication detector laundering across the corpus — and silently, because
the reader would see a pass and no mention of where the name came from.

So it only ever **adds a field to a finding that already exists**:

```json
"also_in_other_documents": {"Fenwick Partners": ["other-report.txt"]},
"also_in_other_documents_note": "ANNOTATION ONLY. ..."
```

Two tests pin the contract: the annotated claim is still flagged and still
records the term as absent from its own document, and the annotated ledger has
**the same categories in the same order** as the unannotated one.

It is genuinely useful triage — a name absent here and present next door is
more likely context bleed between documents than invention, and that is a
different thing for a reader to check — but it is triage, not evidence.

**Scope is always explicit in the output.** Every finding carries
`scope_checked` (`"cited span only"` or `"cited span, then the document"`), and
every entity resolution carries the scope labels it matched in.

---

## 8. Recommendation

1. **Ship Change 1.** Two-scope checking, on by default. It cannot lose a
   catch, it costs a fifth of a second, and it separates the finding the tool
   exists to make from the one nobody needs to panic about.
2. **Ship Change 2, but do not promote the entity signal.** `ENTITY_MODE`
   stays `"route"`. The false-positive rate is better and the established catch
   rate is unchanged, but §4b is a measured regression on a plausible
   fabrication mode, and a signal that has just been made softer is not a
   signal that has earned the right to fail a claim.
3. **Use `--entity-rules strict` on source that is not OCR output** — which is
   what this project's actual target material is (retention policies, statutory
   instruments). Fuzzy matching exists to forgive scanner damage; on clean text
   it has nothing to buy and, per the sweep, it is the only thing costing
   near-miss catch. **UNTESTED on clean source** — the flag is offered with its
   numbers on the hostile corpus, not recommended on evidence from a clean one.
4. **Do not lower `WHOLE_FUZZ` or enable `part_fuzzy`** to chase the remaining
   false positives. The sweep says both are paid for in catch.

---

## Not established — do not cite as settled

- **That 8.48% generalises.** One document, five chunkings, 1,014 claims, and a
  corpus chosen for being *hostile* to entity matching.
- **That `STRICT_RULES` is better on clean source.** It is an argument from the
  mechanism, not a measurement. Nobody has run this on a statutory instrument.
- **The near-miss battery as a fabrication model.** A 1-character substitution
  is one way a model could corrupt a name; it is not established that models
  fabricate this way, and on an OCR corpus it is indistinguishable from correct
  behaviour by construction.
- **The `initials` rule's value.** It fires, it is constrained, and it is
  tested — and it moved none of the three headline rates on this corpus,
  because this corpus has almost no initialised names.
- **Anything about the corpus annotation path at scale.** It is tested against
  a two-document sqlite; `corpus_documents` was empty in the live store when
  this was written.

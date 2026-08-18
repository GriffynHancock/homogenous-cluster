# Citation and attribution research: what to build, if anything

**Researched 2026-08-18.** Answers the operator's question of what an attributed
summary should look like for Missing Link, given the gap `docs/DESIGN-NOTES.md`
E identified: `chunk_summaries` already carries `start_char`/`end_char` per
chunk, but `build_reduce_prompt` discards it — the final summary is unattributed
prose. Reads with `docs/audit-ledger.md` (the alignment machinery that already
exists), `docs/EVALUATION.md`, and `docs/FINDINGS.md` F25/F37#4/F38.

**Also reads with `docs/market-research.md`**, a parallel product-survey pass
that landed while this document was being written and is engaged with
throughout (RECOMMENDATION, Q2, Q3, Q4, Q6) rather than only summarised —
particularly its two sharpest findings: mainstream tools converge on shipping
citations by default, and a citation number proves reference, not
faithfulness. This document does not edit that file; it is a separate,
citable input.

Labels: **CONFIRMED** (I fetched the primary source and quote it directly),
**REPORTED** (a secondary source states it, or an automated extraction of a
primary source that I did not read character-by-character myself — noted where
this applies), **INFERRED** (my own reasoning, usually combining two CONFIRMED
findings that were not tested together anywhere I could find).

---

## RECOMMENDATION

**A parallel market survey (`docs/market-research.md`) landed while this
research was in progress. It corroborates part of this document and sharpens
the rest — engaged with throughout, and summarised here first because it
changes the shape of the recommendation from "add citations" to a staged
two-tier one.**

The market survey found every mainstream deployed document-summary tool
checked (Copilot, Gemini, NotebookLM, Acrobat AI Assistant, Glean, ChatGPT file
search) ships numbered, clickable citations as a baseline feature, and —
importantly for the trap this project keeps checking for (F37#3) — **these are
delivered-document tools, not live chat**, so "citations belong in async
document summaries, not just interactive chat" is itself now evidence-backed,
not assumed. That corroborates half of Q6 below and changes it: the earlier
draft of this section argued citation-with-hover-verification was a shape
borrowed from chat products; the market survey shows it is also the shape of
Word's and Acrobat's *async* summary panels, which are structurally closer to
this project's job page than to a chat window. **Building citation into
Missing Link is not borrowing an ill-fitting shape — it is closing a real,
market-validated gap.** What the market survey does *not* validate, and
explicitly flags as a trap (`market-research.md` §6 item 8, its sharpest
finding): "a citation number proves the model *referenced* a passage, not that
the sentence attached to it is *faithful* to that passage" — confirmed
directly for Copilot, whose own Microsoft documentation admits it under-attends
the middle of long documents while still producing citations for what it does
cover. **A citation marker is proof of reference, not proof of faithfulness**,
and cosmetic parity with the market (numbers that link somewhere) would
satisfy the shape without the substance this project's whole faithfulness
argument depends on.

So the recommendation is now two tiers, not one, and the second tier is the
place this project can genuinely do better than every tool in the market
survey — because the substrate for it, uniquely, already exists here:

**Tier B (ship now): section-level, generation-time citation markers on the
reduce step — cheap, and now backed by two independent lines of evidence
(the granularity literature below, and market convergence on citation-as-a-
baseline-feature).**

Concretely:

1. **Change `REDUCE_PROMPTS`** to ask the model to tag each sentence (or short
   run of sentences) of the combined summary with the `[Section N]` label(s) it
   drew from — the same label already shown to it in `build_reduce_prompt`'s
   `[Section 1]…[Section N]` framing. This is not "ask the model to cite" in the
   hard sense the literature warns about (inventing a line number or a byte
   span from scratch, where models score ~38% and localise at 12.8 F1, see Q1).
   It is closer to a closed-set classification over a handful of already-visible
   labels — a structurally easier task that nothing in the literature tests
   directly (see the gap noted in Q1/Q6).
2. **Parse `[Section N]` back to `chunk_summaries.start_char/end_char`** in code
   — a dict lookup, `records[N-1]`, exact when the tag is valid. No fuzzy
   matching needed for this path, unlike `audit.py`'s existing `best_match`
   content-word heuristic for hop 2.
3. **Fall back to the alignment machinery that already exists** —
   `audit.py`'s `content_words`/`overlap_score`/`best_match` — for any sentence
   with no tag, or a tag outside `1..N`. Per this project's own rule (refuse,
   don't degrade), a fallback match below a confidence floor should render as
   "no located source," not a guess presented as fact.
4. **Render it as a span pointer, not a bibliography entry**: "this sentence
   summarises material starting near character X of the source (Section N)" —
   language `audit.py` already uses for its `location_confidence: "indirect"`
   findings. This is the format the evidence in Q2 supports, and it is also
   literally the format Anthropic's own Citations API uses
   (`document_index`/`start_char_index`/`end_char_index`, CONFIRMED below) —
   the project's `start_char`/`end_char` schema is already citation-shaped.
**Tier C (next, explicitly gated, not shipped now): a *checked* citation** — a
marker that asserts not just "this came from Section N" but "and two
independent classifiers agree Section N supports it," with disagreement
surfaced rather than hidden. This is the specific place the coordinator's
market-research feedback asked to be engaged directly, because **no tool in
the market survey does this** — every mainstream tool's citation is reference-
only (§2 of `market-research.md`, and Q4 below). It would be a genuine,
substantive differentiator, not a cosmetic one, and it is buildable from
existing code: `audit.py`'s `MiniCheckScorer` machinery already does exactly
this pairing (claim sentence vs. its evidence span, two models, disagreement as
a first-class category) for hop 1 (`chunk_vs_source`); Tier C is that same
mechanism re-run against whichever chunk a Tier-B tag actually points to,
instead of against chunk-summary sentences.

**Assessed honestly, this is not over-engineering as a design — but it is not
free, and it is not ready.** Cost: `docs/audit-ledger.md` §5 measures
**17–21 s per claim-evidence pair against a full 4096-token chunk, for
Flan-T5-Large alone** (CONFIRMED, spike §2) — running both models per the
project's own "two models, always" rule roughly doubles that. A 40–60-sentence
final summary, each sentence checked against its cited chunk, is therefore
**tens of minutes added per job**, the same order of magnitude as running
`audit.py`'s existing hop 2 today (`docs/audit-ledger.md` §5: ~75–95 min for a
full 25-chunk audit, both hops, both models) — not the ~30-second cost of Tier
B's tags. This is a real scheduling cost for an overnight job, not a rounding
error, and it should be priced to the operator as such rather than bundled
into "add citations" as if it were free.

**More importantly, per `docs/audit-ledger.md` §6 (its own "is this ready"
section) and the coordinator's caveat: the ensemble's reliability at
production chunk size is measured NOWHERE yet.** Every accuracy and
disagreement number in the audit ledger — including the "zero ensemble-blind
errors in 72 claims" result the whole two-model design rests on — was measured
against **one-to-three-sentence constructed documents**, explicitly chosen so
nothing in surrounding context could rescue a wrong reading. The production
evidence window for Tier C is a **4096-token chunk**, and `audit-ledger.md`
says plainly this has not been tested: *"Re-run the battery with each pair
embedded in a full-size chunk before trusting the escalation rate."* Building
Tier C before that re-run would mean shipping a "checked" badge whose check
has an unmeasured, possibly much higher false-negative rate at the size it
would actually run against — a checked-looking citation that is not
actually more trustworthy than an unchecked one, which is precisely the
cosmetic-parity trap the market survey warns against, just moved one layer
deeper.

**So: Tier C is the right eventual target — it is the one thing in this whole
document that would put Missing Link ahead of every tool in the market survey,
not just at parity with it — but it is explicitly conditional on work
`docs/audit-ledger.md` already queued for other reasons (the full-chunk battery
re-run), not new work this document is inventing. Do not build Tier C until
that re-run has happened and the escalation rate at production chunk size is
known.**

**On multi-document attribution: do not build it.** `create_job` takes one
document; there is no cross-document synthesis to cite across, and building it
is a real feature (conflict-flagging NLI, cross-document claim clustering) that
the one research system found closest to this project's needs gets right only
58–73% of the time even as a dedicated SOTA system (Q3). Nothing in this
project's current codebase or `STATUS.md` calls for it now. **The market survey
independently corroborates the sharpest part of this finding**: it found no
mainstream tool — not Copilot, Gemini, NotebookLM, Acrobat, or Glean — handles
the case where sources *disagree* either. This is a gap in the entire market,
not something competitors have solved and this project hasn't; it is exactly
the situation this project's own material produces (two versions of a policy,
a superseded retention schedule), and it remains unbuilt anywhere, here
included. When it is eventually built, it should be `document_id + span` per
claim, with disagreement **flagged explicitly**, never silently resolved to one
source — the CAMS-style design in Q3 is the shape to copy.

**Why not sentence-level citation, on the evidence, not on caution alone:** a
CONFIRMED 2026 granularity study (Q1) finds sentence-level citation is
*measurably worse* than paragraph/section-level on both attribution quality
**and** answer correctness — 16–276% degradation relative to the best
granularity, worst at the model scales this project's disk-bound quantised
models sit in. This is not "coarse is cheap but worse" — coarse is *better*, on
the model sizes this project runs, at the metric that matters. That is an
unusually clean result to build a recommendation on.

**Why this is affordable on this hardware, concretely (Q5):** `REDUCE_MAX_TOKENS
= 2048`. Tagging every sentence of a ~40–60 sentence combined summary with
`[Section N]` costs roughly 150–300 extra output tokens — comfortably inside the
existing budget, not a new one. At the measured **6.05 tok/s** generation rate
(`docs/measurements.md`, sparse-MoE reduce model), that is **~25–50 seconds**
added to a job whose wall-clock is dominated by prefill and the map passes
(CLAUDE.md: prefill ~79% of document wall-clock). It is a rounding error, not a
scheduling decision.

**What would change this recommendation:** if the cheap experiment below (part
6) shows the reduce model does not reliably emit parseable, in-range tags on
*this* hardware's quantised models — the project has direct precedent for
instruction-following degrading in ways synthetic tests don't catch (F21, F34,
F38) — fall back to pure post-hoc alignment (`audit.py`'s existing machinery,
already coded, zero prompt change, zero extra generation cost) and skip the
prompt change entirely. Either path is cheap; the experiment decides which.

---

## Q1. How do real systems attribute summary sentences to sources?

**Three mechanisms, and the project's prior belief holds up, with a twist at
coarse granularity.**

- **Generation-time citation** — the model emits citation markers as it writes
  (ALCE's setting; Anthropic's Citations API; "G-Cite" below). Cheapest at
  inference (no second pass) but asks the model to do two things — write and
  cite — at once.
- **Post-hoc alignment** — generate freely, then match sentences to source spans
  afterward, by retrieval/similarity/NLI. What `audit.py` hop 2 already does
  via `content_words`/`best_match`.
- **Retrieval-anchored citation** — cite only what was retrieved as evidence, so
  the citation is a byproduct of the retrieval step, not a separate task. Not
  applicable here per `DESIGN-NOTES.md` E — this project reads whole documents,
  it does not retrieve.

**On fine-grained (span/line) citation, post-hoc beats generation-time
decisively — CONFIRMED**, and it is a bigger effect than the project's earlier
research suggested it might be. Fetched directly from
[arXiv:2606.07130](https://arxiv.org/abs/2606.07130), "*Explicit Evidence
Grounding via Structured Inline Citation Generation*" (FullCite): on ASQA,
prompt-based generation-time citation scores **12.80 Snippet-F1**; posthoc span
alignment scores **61.87** — a near-5x gap. A third strategy, constrained
decoding over a citation grammar, does better than naive prompting but still
only **43.96** (BioASQ) / **33.55** (ExpertQA) Snippet-F1. This matches the
number already in `docs/audit-ledger.md` (12.8 → 61.9) — that citation is
correct, sourced, and I have now traced it to its primary paper directly rather
than taking it on trust.

**On the ~38% citation-validation figure already in `audit-ledger.md`**: I could
not find one paper with that exact number as a primary source. The closest
CONFIRMED match, [arXiv:2605.06635](https://arxiv.org/abs/2605.06635) "*Cited
but Not Verified: Parsing and Evaluating Source Attribution in LLM Deep
Research Agents*" (REPORTED via automated extraction, not read line-by-line):
frontier models achieve **39–77% factual accuracy** verifying whether a
citation actually supports a claim, despite **>94%** link-validity and **>80%**
relevance. Read this as: the 38% figure in this project's own docs is
consistent with, but not pinned to, this literature — it sits right at the
floor of the measured range. **Do not tighten the citation on that number
without reading the primary paper in full**, which I did not do here.

**Does this change for section-level citation, coarser than a line number? This
is the interesting finding.** [arXiv:2604.01432](https://arxiv.org/abs/2604.01432),
"*Are Finer Citations Always Better? Rethinking Granularity for Attributed
Generation*" (REPORTED via automated abstract extraction — the PDF body did not
render through the fetch tool, so treat the specific numbers as reported, not
independently re-derived): across 8B–120B models, **forcing sentence-level
citation degrades attribution quality by 16–276%** relative to the
best-performing granularity, and **quality peaks at paragraph-level** — coarser
than sentence, finer than whole-document. The stated mechanism: sentence-level
citation "disrupts necessary semantic dependencies," and **the penalty is worse
for larger models**, because atomic citation units interrupt the
multi-sentence synthesis those models are good at. Crucially: **citation-optimal
granularity improved attribution quality while preserving or improving answer
correctness** — the trade-off the project worried about (citation quality vs.
generation quality) is not universal; it is a fine-granularity-specific cost.

**INFERRED, combining the two CONFIRMED/REPORTED findings above, and this is
the gap no paper closes directly:** fine-grained generation-time citation is
hard because the model must *invent* a precise span or line number from
scratch. Section-level citation in this project's reduce step is not that task
— the model is choosing among `N` labels it has *already been shown verbatim*
in the prompt (`[Section 1]` … `[Section N]`), which is closer to closed-set
classification than span generation. If the granularity study's mechanism
(fine-grained citation fights the model's own synthesis) is right, this
project's section-tag task should sit closer to the "coarse, cheap, and
reliable" end than either paper's own experiments — but **nobody has tested
"ask a model to repeat a label it was already shown" as a citation mechanism**,
so this is a reasoned extrapolation, not a result. It is exactly what part 6's
cheap experiment is for.

Sources: [FullCite / 2606.07130](https://arxiv.org/abs/2606.07130) ·
[Cited but Not Verified / 2605.06635](https://arxiv.org/abs/2605.06635) ·
[Granularity study / 2604.01432](https://arxiv.org/abs/2604.01432) ·
[ALCE / 2305.14627](https://arxiv.org/abs/2305.14627) (CONFIRMED existence and
scope via GitHub/ACL Anthology, not fetched for numbers here) ·
[Generation-Time vs Post-hoc / 2509.21557](https://arxiv.org/abs/2509.21557)

---

## Q2. What does attributed-summary output actually look like, and what do
readers trust?

**Concrete formats found, roughly cheapest-to-richest:**

| Format | What it looks like | Who uses it |
|---|---|---|
| Inline numeric marker | `[1]`, superscript, linking to a source list | Perplexity, Bing/Copilot-style search |
| Footnote / "Sources" panel | citations collected at the end, separate from prose | many RAG chat UIs |
| Hover / expand-to-source | marker reveals the cited span on mouseover or click | Anthropic Citations API consumers, "Semantic Reader" |
| Sidebar | persistent source list alongside the generated text | some conversational-search UIs |
| Side-by-side highlighting | source document shown next to output, spans highlighted | document-review tools, less common in chat |
| Document-id + character span | machine-readable, not necessarily rendered directly | Anthropic's Citations API; this project's own `chunk_summaries` schema |

**Anthropic's own Citations API is a strong, directly-relevant CONFIRMED data
point** (fetched from
[platform.claude.com/docs/.../citations](https://platform.claude.com/docs/en/build-with-claude/citations)
directly). The citation object is:

```json
{"type": "char_location", "cited_text": "...", "document_index": 0,
 "start_char_index": 0, "end_char_index": 50}
```

— structurally almost identical to this project's own
`chunk_summaries(start_char, end_char)`. Two details worth carrying over
directly: **`cited_text` does not count toward output tokens** (the API parses
it out of a compact internal citation format, not a verbatim quote the model
had to generate token-by-token) — this project's llama-server completion
endpoint has no equivalent, so a `[Section N]` tag genuinely does cost output
tokens, which is why the cost estimate in the recommendation matters. And:
**default chunking is sentence-level, but "custom content documents" let the
caller set coarser granularity** — Anthropic's own docs implicitly endorse
controllable granularity rather than sentence-level-always, consistent with the
granularity finding in Q1.

**On what readers actually trust and check — the finding that should worry this
project most.** [arXiv:2501.01303](https://arxiv.org/abs/2501.01303), "*Citations
and Trust in LLM Generated Responses*" (AAAI; REPORTED via search summary, not
independently re-fetched — the PDF did not render through the fetch tool):
users trust a response more when it carries citations, **including when those
citations are random/irrelevant** — the citation's mere presence, not its
correctness, drove the trust increase. Trust dropped only among the subset of
users who actually clicked through and checked. Separately,
[arXiv:2512.12207](https://arxiv.org/abs/2512.12207), "*Not All Transparency Is
Equal: Source Presentation Effects on Attention, Interaction, and Persuasion in
Conversational Search*" (REPORTED via search/automated extraction — PDF body
did not render for exact tables): compared hover-card, footer, sidebar and
inline-number presentations; **hover and click rates stayed below 25% across
every format**, satisfaction stayed high (>4.0) regardless, and the paper's own
framing is that this is "a trust gap in which users accepted AI-generated
content without validation." **No format tested drove verification rates high**
— format choice affects *engagement style* (hover vs click), not whether people
actually check.

**Where this connects to the parallel market survey (`docs/market-research.md`
§2, §6 item 8):** the market survey found the same crack independently, from
product documentation rather than academic literature — Microsoft's own
Copilot docs admit the model "may focus on the beginning and end of a file and
give less attention to material in the middle" *even while producing citations
for the parts it does cover*. That is the trust-study finding and the
granularity-vs-correctness finding meeting from a completely different
direction: a shipped, market-leading tool visibly demonstrates that citation
presence and citation-target quality are separable, exactly as the academic
evidence above predicts. Two independent research passes landing on the same
crack from different evidence bases is a stronger signal than either alone.

**What this means for this project specifically:** citations here are not
primarily a UX device to make readers trust the summary more — per the evidence
above, that would happen almost regardless of whether the citation is any good,
which is close to the opposite of what "faithfulness is a security property"
should produce. Their value is **enabling the one person who has to defend the
summary** (per `CLAUDE.md`'s target user — "a professional who may need to
defend the summary," Q6) to go find the source passage fast, and **feeding
`audit.py`'s mechanised check**, which does not have the human trust-gap
problem because it is not a human. Format should optimise for "fast to jump to
the source when someone specifically decides to check," not for "makes the
summary look more credible at a glance" — the latter is the failure mode the
trust-gap research documents.

Sources: [Anthropic Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations) ·
[Citations and Trust / 2501.01303](https://arxiv.org/abs/2501.01303) ·
[Source Presentation / 2512.12207](https://arxiv.org/abs/2512.12207)

---

## Q3. Multi-document attribution specifically

**Not applicable to this codebase today, and I want to be precise about why.**
`create_job(path, kind, document, instruction=None)` — confirmed by reading
`missing_link/db.py` — takes exactly one `document` field. Batch upload
(per `CLAUDE.md`'s changelog, "batch upload, document table with per-workflow
tick boxes") creates **N independent jobs**, not one job over N documents.
There is no cross-document synthesis anywhere in `worker.py` or `db.py`, so
there is nothing today for a multi-document citation scheme to attach to.

**For when it is built, the closest real system found is directly relevant.**
[arXiv:2606.23989](https://arxiv.org/abs/2606.23989), "*Faithful by
Construction: Claim-Anchored Attribution for Multi-Document Summarization*"
(CAMS; REPORTED via automated fetch of the arXiv HTML — I did not verify every
number by re-deriving it, but the fetch quoted specific passages, so this is
close to primary-source confidence): decomposes a multi-doc summary into atomic
claims with token-level provenance, clusters claims across documents, and
**runs a three-class NLI model between clusters to detect contradiction**. Its
citation format is document-id-plus-span, rendered as e.g. `[d2, d5]` for a
multi-source-supported sentence.

**The disagreement question — does anything handle conflicting sources, or does
everything silently pick one — has a direct, if imperfect, answer.** CAMS does
not silently resolve conflicts: a contradiction-labelled pair above a
confidence threshold creates an explicit "conflict link," and the system either
surfaces both sides (attributed to their respective sources) or defers,
depending on policy. Measured on the DiverseSumm benchmark: it **surfaces 61%
of inter-source conflicts**, attributing each side correctly **73%** of the
time, against an **18%** conflict-surfacing rate for an end-to-end baseline with
no explicit conflict module. **So conflict-aware attribution is buildable and
meaningfully better than the default (silent pick-one) — but even the dedicated
system misses roughly 4 in 10 real conflicts**, which is the honest ceiling to
quote if this is ever proposed for this project's superseded-policy /
conflicting-retention-schedule case.

**The parallel market survey independently corroborates this being a real,
unsolved gap in the deployed market, not merely in the academic literature.**
`docs/market-research.md` checked Copilot, Gemini, NotebookLM, Acrobat AI
Assistant and Glean specifically for this and found **none** of them surface
disagreement between sources — every mainstream tool checked either doesn't
support true multi-source synthesis or silently resolves it. That two
independent research passes — one through academic attribution literature, one
through deployed-product documentation — arrive at the same "nobody handles
this" conclusion is a stronger basis for calling it a real gap than either
alone, and it sharpens the framing: this is not a case where competitors have
solved a problem this project hasn't; **CAMS-style conflict flagging (Q3 above)
is a research-grade proposal with no shipped-product precedent anywhere
surveyed**, and 61–73% coverage/accuracy is the honest state of the art, not a
market baseline this project is falling short of.

**Recommendation, restated: do not build this now.** It requires (a) a new job
type spanning multiple documents, which does not exist and is a real design
decision (`CLAUDE.md`'s task-profile seam is the right place for it, per
`DESIGN-NOTES.md` E's concession 2 — "retrieve which documents are relevant,
then read those completely" is a different architecture from single-document
map-reduce), (b) an NLI conflict-detection component this project does not have
(though MiniCheck, already vendored for `audit.py`, is architecturally similar
and could plausibly be repurposed — untested), and (c) accepting a real,
measured false-negative rate on the exact failure mode (silent conflicting
sources) that would matter most for this project's material. This is future
work behind the task-profile seam, not a citation-format decision to make now.

Sources: [CAMS / 2606.23989](https://arxiv.org/abs/2606.23989)

---

## Q4. Does adding citations change faithfulness, or just appearance?

**The honest answer, and it should not be overstated in either direction:
citations improve checkability; they do not, by themselves, improve accuracy.
Verification is a separate mechanism this project already built, and citations
and verification are complementary, not substitutes.**

**The sharpest, plainest statement of this comes from the parallel market
survey, not from the academic papers below, and it is worth putting first: a
citation marker proves REFERENCE, not FAITHFULNESS** (`docs/market-research.md`
§2, §6 item 8). "This sentence came from Section 4" is a claim about where the
model *looked*; it is not a claim about whether Section 4 *supports* the
sentence. Every piece of academic evidence gathered independently below says
the same thing in more technical language — see FullCite's own hedge, and
AttributionBench's ceiling, immediately following. This is a case where a
product-documentation survey and an academic-literature survey converged on
identical language for the same finding without either seeing the other's
work, which is about as strong as corroboration gets in this exercise.

Evidence for the "checkability, not accuracy" reading:

- **FullCite's own authors say so directly (CONFIRMED via fetch)**: their
  automatic metrics "do not fully capture whether a cited span genuinely
  entails the associated claim" — a citation can point at the right general
  neighbourhood of the source while the claim itself is still wrong, and
  nothing in the citation format itself catches that.
- **AttributionBench** ([arXiv:2402.15089](https://arxiv.org/abs/2402.15089),
  REPORTED via search summary): even a fine-tuned GPT-3.5 doing the *easier*
  job of judging whether an existing citation supports a claim (not generating
  one) reaches only ~80% macro-F1. That is the automatic-checking ceiling, and
  it is well short of what this project would need to trust a citation
  unreviewed — which is exactly why `audit.py` exists as a **separate,
  purpose-built, two-model ensemble** rather than "does the citation exist."
- **The Trust study (Q2)**: citations increase perceived trust independent of
  whether they are correct. A citation marker with no accuracy guarantee behind
  it is, if anything, a *risk* for a project whose stated failure mode is a
  fabricated fact treated as fact — it manufactures unearned confidence unless
  something else is checking the claim.

Evidence on the other side, for where citations genuinely do help beyond
appearance — worth stating so this section is not one-sided:

- **REPORTED, one search-summarised source** (MDPI, "Reducing Hallucinations in
  Medical AI Through Citation Enforced Prompting in RAG Systems"): forcing a
  model to justify every claim against a specific retrieved source identifier
  reduced ungrounded assertions in a medical RAG setting. Read this carefully —
  this is citation **as part of a retrieval-grounding constraint**
  (`DESIGN-NOTES.md` E already rejected retrieval as this project's
  architecture), not citation as a decoration on free generation. It is not
  strong evidence that adding a `[Section N]` tag to this project's existing
  map-reduce output would, by itself, reduce fabrication in the reduce step.
- The multi-model-consensus finding in
  [arXiv:2603.03299](https://arxiv.org/pdf/2603.03299) ("*How LLMs Cite and Why
  It Matters*"; REPORTED) — agreement across 3+ models raises citation accuracy
  from 16.5% to 95.6% — is about **fabricated bibliographic references in
  academic writing**, a materially different failure mode (inventing a
  citation to something that does not exist at all) from this project's task
  (pointing at a real span inside a document the model actually read). Flagged
  here specifically because it is easy to over-apply; **do not cite this number
  for this project's use case.**

**What this means concretely for Missing Link:** a `[Section N]` tag on the
reduce output does not, by itself, make the reduce step less likely to
fabricate. What it does — cheaply — is make a fabrication **findable**: a human
reviewer, or `audit.py`'s existing hop-2 check, now has an exact pointer instead
of having to search the whole source. That is still a real, strong argument for
this project, whose stated problem (`CLAUDE.md`) is that *"a human must be able
to verify a summary of a legal document"* — checkability is precisely what was
asked for. It is a different, narrower claim than "citations reduce
hallucination," and the recommendation above is built on the narrower claim,
not the broader one.

**This is also exactly why Tier C (the "checked citation," RECOMMENDATION
above) is the correct next step rather than a nice-to-have: it is the one
citation format that closes the reference-vs-faithfulness gap this section
identifies, rather than merely matching it.** A Tier-B tag alone would put
Missing Link at parity with the market's reference-only citations, unresolved
faithfulness gap included. Tier C's "and two classifiers agree" badge is a
genuine answer to "reference, not faithfulness" rather than a repetition of it
— which is also exactly why it must not ship until the ensemble's production-
chunk-size reliability is actually established (Q5/RECOMMENDATION); an
unvalidated "checked" badge would be worse than an honest "unchecked" one,
because it claims a guarantee the project cannot yet back.

Sources: [FullCite / 2606.07130](https://arxiv.org/abs/2606.07130) ·
[AttributionBench / 2402.15089](https://arxiv.org/abs/2402.15089) ·
[Citations and Trust / 2501.01303](https://arxiv.org/abs/2501.01303) ·
[Citation-Enforced Prompting, MDPI](https://www.mdpi.com/2076-3417/16/6/3013) (REPORTED, not independently fetched) ·
[How LLMs Cite / 2603.03299](https://arxiv.org/pdf/2603.03299) (REPORTED, flagged as a different task)

---

## Q5. What would this specifically cost us, given what already exists?

**Low, and lower than a from-scratch citation feature would suggest, because
most of the machinery already exists for a different purpose.** Confirmed by
reading the code directly:

- `chunk_summaries` (`missing_link/db.py`) already persists
  `job_id, idx, start_char, end_char, summary, model, instruction` per chunk —
  the exact fields a `document_index`/`start_char_index`/`end_char_index`
  citation scheme needs, already there for resumability (F37#4), not built for
  this.
- `build_reduce_prompt` (`missing_link/worker.py:286`) already numbers chunk
  summaries as `[Section {i+1}]` when constructing the reduce prompt — the
  label a citation tag would reuse is already generated and shown to the model
  on every reduce call.
- `audit.py` already has `content_words`/`overlap_score`/`best_match`
  (`missing_link/audit.py`) doing post-hoc alignment for hop 2
  (`final_vs_chunk_summaries`) — this **is** the post-hoc-alignment path from
  Q1, already built, already tested against real and synthetic data, and
  already labelled `location_confidence: "indirect"` with a `match_method` and
  `match_score` in the schema.

**What's genuinely new, in order of cost:**

1. **Cheapest — post-hoc only, prompt unchanged.** Run `audit.py`'s existing
   `best_match` over the final summary's sentences at render time (job page),
   not only when the operator explicitly runs an audit. Zero new inference
   calls, zero prompt changes, `location_confidence: "indirect"` throughout.
   This is buildable today from code that exists.
2. **The recommended option — add `[Section N]` tags to the reduce output.**
   One line change to `REDUCE_PROMPTS` (`missing_link/worker.py`), plus code to
   parse `[Section N]` and map it to `records[N-1]`'s stored offsets. Adds
   ~150–300 output tokens (est., see below) to a 2048-token budget that already
   has room, and ~25–50 s to a job whose wall-clock is dominated by prefill
   (CLAUDE.md: "prefill is ~79% of document wall-clock"). Gives **exact**
   attribution for any correctly-tagged sentence (a dict lookup, not a
   heuristic match), with the existing `best_match` machinery as a fallback for
   anything untagged or mistagged.
3. **The next step, explicitly gated — "checked" citation (Tier C).** Re-run
   `audit.py`'s existing `MiniCheckScorer` pairing (two models, disagreement as
   a category) against whichever chunk each Tier-B tag resolves to, instead of
   against chunk-summary sentences. No new model, no new scoring code — a new
   *pairing* of code that exists. Cost is **not** in the same league as option
   2: `docs/audit-ledger.md` §5 measures **17–21 s per claim-evidence pair at
   full 4096-token-chunk size, Flan-T5 alone** (CONFIRMED); both models on
   every sentence of a 40–60-sentence final summary is **tens of minutes**, the
   same order as running the existing hop-2 audit today. Gated on
   `audit-ledger.md` §6's own stated precondition — the full-chunk-size battery
   re-run — which has not happened. See RECOMMENDATION and Q4 for why this
   gating is load-bearing, not caution for its own sake.
4. **Not recommended yet — multi-document / CAMS-style.** A new job type, an
   NLI conflict detector, cross-document claim clustering. Real engineering, no
   existing hook in this codebase, and per Q3 not needed until a multi-document
   task profile exists at all.

**Cost math for option 2, worked through explicitly:** `REDUCE_MAX_TOKENS =
2048`, `MAP_MAX_TOKENS = 1024`, `CHUNK_TOKENS = 4096` (all confirmed constants
in `worker.py`). A combined summary of, say, 400–600 words is roughly 40–60
sentences at typical sentence length. A `[Section N]` tag is 3–5 tokens
(`[`, `Section`, ` `, digit(s), `]`), so tagging every sentence once costs
roughly 150–300 tokens — under 15% of the existing 2048-token reduce budget,
and the existing budget already has slack (F17/F19-era measurements show most
reduce outputs land well under their cap). At the **measured 6.05 tok/s**
generation rate for the sparse-MoE model on this hardware
(`docs/measurements.md`), 150–300 extra tokens is **~25–50 seconds** — a
rounding error against the document wall-clock the project is already
optimising in minutes, not seconds. This is the one number in this document I
consider solid enough to act on without the experiment below re-confirming it,
because it follows directly from constants already in the code and numbers
already in `docs/measurements.md`.

**What is NOT free, and is worth naming plainly**: this changes model output,
which per `CLAUDE.md`'s verification rule ("a faster build or config that
changes output is not a win... any performance change must be paired with a
coherence check on real output") means the reduce output format itself needs a
coherence check before shipping — does tagging every sentence make the prose
read worse for a human, independent of whether the tags are correct? That
check is cheap (read a handful of outputs) but it is real work, not zero.

---

## Q6. The trap: is per-sentence citation the right shape for THIS workload?

**Argued honestly, and the answer has two halves that must not be
collapsed into one.** `CLAUDE.md` names the trap explicitly (F37#3) — this
project has twice nearly adopted "the shape everyone else built" for a
different job. The inverse check must actually be run, not assumed, and the
parallel market survey changes what running it finds.

**Half one: is citation-as-a-feature itself a borrowed chat shape? On the
market survey's evidence, no — test the convergence rather than accept it, as
instructed, and it holds up.** The obvious objection to trusting cross-vendor
convergence is that it could mean everyone copied the same interface rather
than everyone independently discovering the same requirement — the exact
failure this project's F37#3 exists to catch. Two things argue against pure
copying here. First, **`docs/market-research.md` found citations in tools that
are not chat products and do not compete on chat UX** — Word's Copilot summary
panel and Acrobat's AI Assistant are both delivered-document, read-later
features, closer in shape to this project's job page than to a chat window,
and they ship citations anyway. If citation were purely a chat-interface
convention, it would be odd to find it equally in a document-summary panel
nobody talks to. Second, **the vendors give a consistent, independently
plausible *reason* for the feature** (per the market survey, "why should I
trust this") rather than a purely aesthetic pattern (contrast with something
like a hamburger-menu icon, which really is copied for no functional reason).
A shared answer to a shared, real question is weaker evidence of mimicry than
a shared decoration would be. **So: building *some* citation mechanism into
Missing Link is not the trap** — it is closing a gap real enough to show up
independently in async document tools this project resembles more than it
resembles chat products.

**Half two — and this is where the trap is actually hiding: per-sentence,
fine-grained, hover-to-verify citation specifically.** That narrower shape —
Perplexity, Bing Copilot, RAG-QA tools, where a person is at a prompt asking a
question and wants to confirm one specific claim *right now* — is squarely the
workload `DESIGN-NOTES.md` E already ruled out for this project's core task
("nobody is waiting at a prompt"). The market survey's own tools are less
uniform on *granularity* than on the presence of citation at all: Acrobat cites
by page/section, not necessarily single sentence; the survey did not establish
sentence-level granularity as the universal norm the way it established
citation-presence as one. So the trap is not "citations at all" — evidenced
against by half one — it is specifically **importing the fine-grained,
real-time-verification interaction pattern** onto a workload whose reader
checks later, deliberately, not continuously while reading.

**Missing Link's actual reader is different in a way that changes what
"citation" should mean.** Per `CLAUDE.md`'s target user, the summary is read
**overnight, later, by a professional who may need to defend it** — not
verified claim-by-claim in the moment it is produced. That reader does not need
"click here to see if this exact sentence is true" while reading; they need,
*if and when a specific claim is later questioned*, a fast way to find where in
the source document to start re-reading. That is a coarser, later-triggered
need than the fine-grained, in-the-moment verification chat products build for
— and it is, not coincidentally, exactly the language `audit.py`'s own schema
already uses for its `location_confidence: "indirect"` findings ("names where
to start reading, not the sentence that produced the claim").

**And this is not merely the right shape for this project's workflow — per Q1,
it appears to be the objectively better-performing shape at the model scale
this project runs.** The granularity study found sentence-level citation
*measurably degrades* both attribution quality and answer correctness relative
to paragraph-level, worse at larger model scales. So the chat-product shape
(fine-grained, per-sentence) is not just a mismatch for this project's
asynchronous, professional-reviewer workload — on the cited evidence, it would
likely be a worse citation *and* a worse summary if imported wholesale. Section-
level citation is the honest answer on both grounds at once, which is a
stronger position than "we are different" — it is "we are different, and the
different thing we need also happens to measure better."

---

## What the evidence does NOT support

Stated plainly, so this is not overstated in either direction:

- **It does not support "citations will reduce fabrication in the reduce
  step."** The strongest evidence found (Q4) is that citation-enforced,
  retrieval-grounded prompting reduces ungrounded assertions — a different
  architecture (grounding-by-retrieval) than this project's free-text
  map-reduce. A `[Section N]` tag on prose the model already decided to write
  does not, on this evidence, make that prose more likely to be true. It makes
  a false claim easier to *find*, not less likely to *occur*.
- **It does not support that this project's exact "ask for `[Section N]`"
  mechanism has been tested anywhere.** The central claim underpinning the
  recommendation — that citing a pre-shown coarse label is structurally easier
  than generating a fine span — is INFERRED from combining two separate
  studies (Q1). No paper tests it directly. This is the biggest unresolved
  uncertainty in this document, and it is what part of the cheap experiment
  below is for.
- **It does not support that any citation UI format meaningfully increases
  actual verification behaviour.** Every format measured in Q2 saw
  under-25%-of-the-time engagement with sources; satisfaction stayed high
  regardless of whether anyone checked. Do not sell a citation feature to the
  operator as something that will make readers verify more — the evidence says
  it mostly won't, for a human reader. Its real value is for the specific
  person who deliberately needs to check (per Q6), and for feeding `audit.py`'s
  mechanised checker, which does not suffer the same trust-without-verification
  gap.
- **It does not support building multi-document attribution now.** Nothing in
  the codebase calls for it (Q3), and even the best dedicated research system
  found gets conflict detection right only ~61–73% of the time.
- **The "38%" and "12.8 → 61.9" figures already in `docs/audit-ledger.md` are
  now more precisely sourced than before** (Q1), but the 38% figure specifically
  should be treated as REPORTED-and-approximate, not pinned to one paper, until
  someone reads the primary source in full rather than via automated
  extraction, as I did here.
- **It does not support building Tier C ("checked" citation) yet, under any
  framing.** `docs/audit-ledger.md` §6 is explicit that the two-model
  ensemble's disagreement-catches-errors result was measured only on
  one-to-three-sentence constructed documents, not the 4096-token chunks Tier C
  would run against in production, and says this measurement gap must close
  before the tool is wired into anything. This document adds a citation-shaped
  use case on top of that precondition; it does not remove the precondition.
  Treat any "checked citation" proposal that ships before that battery re-run
  as ahead of its own evidence.

---

## The cheapest experiment that would settle the biggest uncertainty

**The uncertainty that matters most: does this hardware's quantised reduce
model reliably emit parseable, in-range `[Section N]` tags, or does it degrade
the way this project's models have degraded before** (F21's empty content on
`max_tokens` cutoff mid-thought; F35's inert `enable_thinking`; F38's silent
garbage-in-garbage-out)?

**Proposed experiment, same-day, no new infrastructure:**

1. Take one existing multi-chunk job from `/opt/missing-link/jobs.sqlite`
   (`audit.py`'s `load_job` already reads this read-only — reuse it, do not
   write to the jobs DB from an experiment) or a small synthetic multi-chunk
   document.
2. Change `REDUCE_PROMPTS[kind]` locally (not committed) to add one sentence:
   *"After each sentence, note which section(s) it draws from in the form
   [Section N]."*
3. Re-run just the reduce call (`client.complete(build_reduce_prompt(...),
   max_tokens=reduce_max_tokens)`) against the already-persisted chunk
   summaries for that job — no need to re-run the map step.
4. Measure, and report the actual numbers rather than a pass/fail:
   - **Parse success rate**: fraction of sentences that got a tag at all.
   - **Range validity**: fraction of tags in `1..N` for that job's chunk count.
   - **Spot-check accuracy**: for ~10 tagged sentences, does the tagged chunk's
     stored `document[start_char:end_char]` actually plausibly support the
     sentence? (Cheap human read, not a model call.)
   - **Token/time cost**: actual tokens added and wall-clock added, against the
     ~150–300 token / ~25–50 s estimate in Q5 — confirm or correct that
     estimate on real output rather than the arithmetic above.
   - **`finish_reason`**: per this project's own standing rule, check it did
     not get cut off mid-tag the way F21's reasoning models did.
5. If parse success and range validity are both high (say, >90%) and spot-check
   accuracy looks sound on a small sample, ship option 2 from Q5. If not, ship
   option 1 (pure post-hoc, zero prompt change) instead — it costs nothing
   extra to fall back to, since `audit.py`'s `best_match` machinery already
   exists and needs no new measurement to justify using it as-is.

This directly tests the one claim in this document that is INFERRED rather than
CONFIRMED or REPORTED — whether "repeat a pre-shown coarse label" behaves like
the easy end of the citation-difficulty spectrum this literature describes, or
like the hard end. Nothing else in the recommendation depends on new
measurement; this single question does.

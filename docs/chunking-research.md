# Chunking research: does how we split documents matter for map-reduce summarisation?

**Researched 2026-08-18**, in response to the operator's question *"how are
they split up? that's an important thing as well."*

Labels: **CONFIRMED** (primary source read directly), **REPORTED** (secondary
source states it, primary not read), **INFERRED** (reasoning from CONFIRMED
facts). Several sources below could not be fetched in full (PDF-to-text
failures, a 403, a 10 MB size cap) and are marked accordingly — treat those as
weaker than a normal REPORTED.

---

## VERDICT FIRST

**The published chunking-strategy literature is almost entirely about
retrieval, and the strongest recent papers in that literature conclude that
fancier chunking is not clearly worth its cost even on retrieval's own turf.
None of it evaluates map-reduce summarisation, where the mechanism retrieval
research cares about — "does the right chunk get selected" — does not exist,
because every chunk is read regardless.** So there is no external evidence to
import here, in either direction, and reaching for a fancier splitter because
"chunking strategy" sounds like a solved, well-researched question would be
exactly the F37-item-3 trap this project has been burned by twice already
(RAG-QA tools adopted for summarisation; Open WebUI adopted for an async batch
workload) — a technique built for someone else's problem.

**There is, however, a real, project-specific reason to be uneasy about the
current fixed-word-count cut, and it does not come from outside literature —
it comes from `docs/audit-ledger.md`, our own measurement.** The negation
battery's one systematic failure shape, on both MiniCheck checkers, is *"a
clause with a second, competing clause attached"* — a retention period plus
its "unless" exception, an exemption plus its carve-out. A cut that lands
wherever the word counter happens to be will, with some regularity, sever a
clause from exactly the qualifier that changes its meaning. That is not a
transferred retrieval finding; it is this project's own worst-measured failure
mode, recreated by construction rather than by chance. That argument is
**INFERRED**, not measured, and section 3 below states exactly what would have
to be measured before it counts as more than a plausible mechanism.

**Given that: I did not replace the chunker.** I implemented a minimal,
reversible **boundary-snapping** option (default OFF) that nudges each chunk's
start/end to the nearest sentence or paragraph break within a small tolerance,
leaving the fixed-size word-count logic, the character-offset slicing, and
every existing test untouched. See "What was implemented" below.

---

## 1. What chunking strategies exist, and what they cost

| Strategy | What it needs | What it costs | What the evidence shows |
|---|---|---|---|
| **Fixed-size (what we do)** | nothing — a word count | ~free | the recurring baseline every paper compares against, and it keeps winning or tying on real (non-synthetic) data — see §2 |
| **Sentence-boundary aware** | a sentence splitter (regex, or nltk/spaCy) | regex: free. nltk/spaCy: a model + dependency | reduces mid-sentence cuts by construction; no evidence found that this alone measurably changes summarisation output quality (see §2, §4) |
| **Paragraph/newline-aware** | `\n\n` detection, or a real paragraph-structured input | ~free | **structurally unavailable on this project's actual input.** PDF extraction (`pypdf`) preserves no paragraph markers — see §4 |
| **Recursive character splitting** (LangChain `RecursiveCharacterTextSplitter`) | a separator hierarchy (`\n\n` → `\n` → sentence → word → char) | ~free, no model | it is LangChain's own recommended default and is popular, but that popularity is a RAG-community consensus, not a summarisation one — see the F37#3 warning above. It degrades gracefully to something close to what we already do when higher-level separators (paragraphs) are absent — which, per §4, is the common case for our PDF input |
| **Document-structure-aware** (headings, Markdown/HTML, section markers) | a structure parser | cheap if structure exists | **does not apply to plain-text/PDF input at all** — there is no structure to parse. Would matter for a future Markdown/HTML/DOCX-with-real-headings intake, which this project does not have |
| **Semantic chunking** (embedding-similarity boundaries) | an embedding model, run over the whole document | **real CPU cost, on the resource this project has already measured as the bottleneck** (cores; F7/F10/F11) | the two most careful recent evaluations found it is **not clearly worth the cost even for retrieval** — see §2 |
| **LLM-based chunking** (MoC, ACL 2025) | an LLM call per chunking decision | the most expensive option by far | outperforms semantic chunking on QA benchmarks, but at a compute cost this project's whole architecture exists to avoid paying per document. Not evaluated on summarisation. Not a candidate here |
| **Late chunking** (Jina AI) | a long-context **embedding** model; chunks are a pooling operation over token-level embeddings computed with full-document context | moderate — one embedding pass over the document | **does not apply to this project at all.** Its entire purpose is to fix a mismatch between where you cut the text and where you compute the embedding, for **retrieval** — nothing in this pipeline embeds anything. There is no embedding step for late chunking to improve. This is the clearest "does not transfer" case of the lot |

---

## 2. Does chunking strategy measurably change summarisation quality? — Be careful; most of this is retrieval

The direct answer to the operator's implicit question — "is this worth
worrying about?" — depends entirely on separating two different literatures
that use the same word.

### 2.1 The retrieval literature (the vast majority of what exists)

- **arXiv:2410.13070**, *"Is Semantic Chunking Worth the Computational
  Cost?"* (NAACL 2025 Findings). **REPORTED** (abstract and search-indexed
  summary read; full PDF not fetched). Evaluates **document retrieval,
  evidence retrieval, and retrieval-based answer generation only** — no
  summarisation. Conclusion, in the authors' own words as indexed: *"the
  computational costs associated with semantic chunking are not justified by
  consistent performance gains."* This is the paper the question in the title
  answers, and the answer is no, for retrieval.
- **arXiv:2607.01852**, an academic-thesis RAG evaluation comparing
  fixed-size, recursive, and cluster-based semantic chunking. **CONFIRMED**
  (HTML fetched and read). On non-synthetic, realistic documents, **fixed-size
  chunking matched or beat both alternatives**; the authors' own conclusion:
  *"Cluster-based semantic chunking did not yield any consistent improvement
  with the implemented configuration and adds computing complexity. Simpler
  chunking strategies were overall more reliable."* Also flags that their own
  faithfulness metric (RAGAs) failed in 44% of cases — a caution about
  trusting any single automated chunking-quality metric, including ones we
  might build.
- **MoC** (`arXiv:2503.09600`, ACL 2025). **CONFIRMED** (HTML fetched). Builds
  two chunking-quality metrics — **Boundary Clarity** (perplexity-ratio
  measure of how independent two adjacent chunks are) and **Chunk Stickiness**
  (graph-entropy measure of semantic cohesion across chunk pairs) — and shows
  their **LLM-based** chunker beats semantic and fixed chunking on QA
  benchmarks. **Evaluated exclusively on retrieval-augmented QA. No
  summarisation evaluation reported**, and no numeric correlation coefficient
  is given between the metrics and downstream score — the paper shows the
  LLM-chunker wins on both the metrics and the QA task, not that the metrics
  *predict* the task score.
- **arXiv:2606.00881**, another RAG chunking-methods survey/evaluation
  (title-only; not fetched in depth) — same genre, same retrieval framing.
- **Clinical RAG** (`PMC12649634`, adaptive chunking for a rhinoplasty
  knowledge base). **CONFIRMED** (fetched and read). The one domain-relevant
  finding: *"Fixed windows truncated timing and safety qualifiers; semantic
  clustering separated exception clauses,"* and an adaptive/structure-aware
  chunker that kept directives with their qualifiers intact roughly doubled
  retrieval **precision and recall** of the correct passage (F1 0.24 → 0.64)
  versus fixed-size. **This is the closest analogue to our "qualifying
  clause" worry found anywhere** — but it is still a **retrieval** metric:
  precision/recall of *which chunk gets selected* for a question. In
  map-reduce, every chunk is read and summarised regardless of whether it is
  "the right one" — there is no selection step for a severed qualifier to
  cause a miss on. What *would* transfer, and is reasoned about in §3, is a
  narrower thing: the map step's summary of a severed clause might be wrong,
  not merely retrieved-or-not.

### 2.2 The summarisation-specific literature — thin, and one source unreachable

- A ResearchGate-indexed paper, *"Performance Analysis of Text Chunking
  Methods for LLM-Based Document Summarization,"* looked directly on-topic but
  **could not be fetched (HTTP 403)** — the finding below is from the search
  engine's own indexed summary only, **not verified against the paper**, and
  should be weighted accordingly (weaker than normal REPORTED): chunking
  helped ROUGE-1/BERTScore for some models (Llama-2, Mixtral) and had
  "minimal or even slightly negative" effect for others (Mistral-7B,
  Llama-2-13B-chat) — i.e., **the one source that looked directly on-point
  reports a mixed, model-dependent result, not a clear win for smarter
  chunking.**
- No other summarisation-specific chunking-ablation paper was found in this
  search. `docs/EVALUATION.md` independently confirms the gap from the other
  direction: it names the map-reduce-vs-single-pass fabrication question as
  *"a novel contribution, not a replication"* because *"no published work runs
  it."* Chunk-boundary quality for summarisation looks like the same kind of
  gap — unstudied, not settled-against.

### 2.3 The honest conclusion for Q2

**The evidence does not obviously transfer, and the honest reading is closer
to "nobody has really studied this for our workload" than "here is what other
people found."** The retrieval literature's own newest, most careful entries
(§2.1) increasingly say smart chunking barely helps *retrieval*, which is the
one task it was built for. There is no basis here for adopting semantic,
late, or LLM-based chunking for map-reduce summarisation — not because they
were tried and failed on summarisation, but because they were tried and
found wanting even on the task their own literature is about, and nothing
tested the task we actually have. **This is a case for measuring it
ourselves if it turns out to matter, not for adopting a technique.**

---

## 3. Does splitting mid-sentence hurt a chunk summary specifically? — Mechanism, and why overlap probably (not certainly) repairs it

No study was found that directly measures this for map-reduce. What follows
is **INFERRED** reasoning from three things already measured in this repo,
laid out so the mechanism can be checked rather than asserted.

**The two ways it could go, both real:**

1. **A truncated leading/trailing clause is silently mis-summarised.** The
   model sees a sentence fragment and, being asked to summarise faithfully,
   may complete it plausibly rather than flag it as cut off — this is
   consistent with the general finding (search evidence, weak/blog-level)
   that LLMs are *"sensitive to input perturbations"* and behave differently
   on corrupted/incomplete input, though nothing found measures this for
   sentence-level truncation specifically in a summarisation context.
2. **The 10% overlap repairs it**, because the chunk boundary moves. Given
   `CHUNK_TOKENS=4096`, `OVERLAP_TOKENS=410`, `WORDS_PER_TOKEN=0.70`: the
   overlap is **~287 words** at the front of the next chunk (`worker.py`'s
   `chunk_spans`: `overlap = int(overlap_tokens * WORDS_PER_TOKEN)`,
   `stride = size - overlap`). A sentence severed at the end of chunk N is
   very likely to reappear **whole** near the start of chunk N+1, because
   287 words is generous against a typical clause length. **This is
   arithmetic on measured constants (CONFIRMED numbers), not a new
   measurement** — but it is the reason to believe overlap does the repair
   work it is there for.

**Why "the overlap repairs it" is not the whole story, though.** Overlap
repairs the *information* — chunk N+1's summary will very likely state the
clause correctly. It does **not** repair chunk N's own summary, which was
generated from the severed version and is **still fed into the reduce step
and into the audit's hop-1 evidence window** (see §5). The reduce step then
has two summaries of overlapping material to reconcile — one right, one
possibly subtly wrong — and reduce's job of "remove repetition caused by
overlapping sections" (the actual reduce prompt text) assumes near-duplicate
content to de-duplicate, not contradictory content to arbitrate. Nothing in
this repo tests whether the reduce step resolves a right/wrong pair toward
the right one, and that is exactly the kind of question this repo's own
standing rule (`CLAUDE.md`: *"Measure before standardising"*) says should be
run rather than assumed either way.

**Why this specific mechanism connects to something already measured, not
just plausible in general:** `docs/audit-ledger.md` §1.2 found the negation
battery's failure shape is **"clauses with a second, competing clause
attached"** — retention-plus-exception, exemption-plus-carve-out,
quantifier-plus-exception. A word-count cut has no notion of clause
boundaries, so across a large enough corpus it will periodically fall
*inside* exactly this shape — between "records must be kept for seven years"
and "unless the client requests earlier destruction," for instance. **That is
not a generic mid-sentence-cut worry; it is this project's own
worst-measured failure mode, produced by the chunker rather than merely
caught by the auditor.** This is the strongest argument in this whole
document, and it is entirely home-grown — it required reading
`docs/audit-ledger.md`, not the external literature.

**What would have to be measured before this counts as more than a
mechanism:** how often, on the actual corpus, does a chunk boundary fall
inside a qualifying-clause pair, and does the reduce step recover the correct
version when it does. That is a small, well-scoped experiment — nobody has
run it.

---

## 4. What does structure-aware chunking cost us specifically here?

Checked against the project's actual constraints, not in the abstract:

- **PDF-extracted text has no paragraph structure to be aware of.**
  **CONFIRMED** from `pypdf`'s own issue tracker (`py-pdf/pypdf#544`,
  `#2262`) and documentation: *"PyPDF cannot recognize the boundary of
  paragraphs and tables… each visual text line is parsed as a line ended with
  `\n`, with no special format at the end of a paragraph,"* and word-break
  hyphens at line ends are extracted as literal characters that split words.
  `missing_link/extract.py` (read directly, CONFIRMED) calls
  `page.extract_text()` with no de-hyphenation or line-rejoining
  post-processing — so a paragraph-aware splitter looking for `\n\n` would
  frequently find **none** in real PDF input, exactly as the task brief
  anticipated. A hard line-wrap in PDF text is not a paragraph break; treating
  it as one would be actively worse than the current word-count cut.
- **Sentence splitting on legal text is hard, and the standard tooling only
  partly solves it.** **CONFIRMED** (general NLP-tokenisation sources): the
  core difficulty is that a period simultaneously marks an abbreviation and a
  possible sentence end (e.g., "St." for Street vs. sentence-final), and
  abbreviation lists are inherently incomplete. This project's own material —
  "s. 12(3)(a)", "cl. 4.2", "No. 7" — is exactly the domain-specific
  abbreviation case generic tokenisers are weakest on. `docs/audit-ledger.md`
  independently confirms nltk's Punkt tokenizer is already in use in this
  codebase (for the audit tool) and still needed a documented regex fallback
  (`_SENT_FALLBACK` in `audit.py`) for when nltk is unavailable — i.e., **this
  project already has direct, measured experience that sentence splitting here
  is imperfect enough to need a fallback**, not just a theoretical worry.
- **An embedding model is a real cost on the bottleneck resource.**
  `CLAUDE.md`/F7/F10/F11 establish that **cores**, not RAM, are this fleet's
  binding constraint for compute-bound work, and prefill is already 79% of
  document wall-clock. Any chunking step that runs an embedding model over
  every chunk of every document adds CPU work to exactly the resource this
  project has spent the most effort optimising. Given §2's finding that
  semantic chunking barely helps even retrieval, this cost has no
  demonstrated payoff to justify it.
- **nltk is not currently a production dependency.** **CONFIRMED**
  (`missing-link/requirements.txt` vs `requirements-audit.txt`, both read
  directly): nltk/torch/transformers live in a **separate, ~1.5 GB,
  hand-installed venv** used only by `audit.py`, explicitly *not* pulled into
  the production install that runs on every node (`requirements-audit.txt`'s
  own comment: *"This file adds ~1.5 GB of model stack for a tool that is not
  wired into the pipeline"*). Adding real sentence-boundary detection (nltk or
  spaCy) to `worker.py` — which runs on every node, in the production venv,
  on every document — would cross that deliberate line for the whole fleet,
  not just the audit tool.

**Conclusion for Q4: paragraph-aware and semantic chunking are not merely
unproven here, they are close to unusable given this project's actual input
pipeline and dependency posture. A cheap regex-based sentence-boundary
approximation — no new dependency, no model — is the only structure-aware
option that fits the constraints, which is what was implemented (below).**

---

## 5. Interaction with citations, the audit, and resumability

**Citations.** A chunk that starts or ends mid-sentence is a materially worse
thing to show a human who clicked `[Section N]` to verify a claim — they land
mid-clause and have to read backward or forward to find the sentence's actual
start. This is a UX/trust cost distinct from any correctness question, and it
is real regardless of whether mid-sentence splitting turns out to affect
summary *accuracy*. Boundary snapping addresses this directly by construction
— a citation now points at (approximately) a sentence start, not an arbitrary
word count.

**The faithfulness audit (`audit.py`).** Two effects, in different
directions:

- **False positive risk (a correct summary flagged wrong).** If a chunk is
  truncated mid-sentence, the map step might correctly and cautiously *not*
  assert the cut-off content, but MiniCheck's hop-1 evidence window is the
  identical truncated span — so a claim scored against it has exactly the
  same missing information the summariser had. In this specific case
  truncation should not by itself create a false-positive mismatch, because
  claim and evidence are cut in the same place.
- **False negative / silent-error risk (a wrong summary that looks
  supported), the sharper concern.** If the map step *does* round a severed
  clause up to its unqualified form — asserting "records must be kept for
  seven years" when the document's next words are "unless the client
  requests earlier destruction" — the evidence chunk given to MiniCheck is
  the **same truncated span the model was fed**, which does not contain the
  qualifier either. §1.2 of `docs/audit-ledger.md` shows this is precisely
  the pattern the checkers already get wrong on short constructed examples
  (`retention_seven_years`: Flan-T5 scored the correct claim unsupported and
  the fabricated one supported). A chunk-boundary-induced version of this
  error could pass the audit rather than trip it, because the audit's
  evidence window inherits the same cut as the map step's input. **This is a
  reason the audit's own stated blocker — re-running the negation battery
  against full-size, realistically-cut chunks rather than one-to-three
  sentence constructed documents (`docs/audit-ledger.md` §6, item 1) — is
  directly relevant to this question too, not a separate piece of unfinished
  work.**

**Resumability.** `summarise_traced` re-checks each resumed record's
`start`/`end` against the *current* chunking (`worker.py`, `resume_records`
docstring) before reusing it. Turning `snap_boundaries` on changes every
chunk's offsets, so **any job resumed after that flag changes would correctly
fail the start/end match and restart from scratch** — this is the documented,
correct behaviour (a stale record at the same index is treated as wrong if
the chunking configuration changed), not a bug to work around. Worth stating
plainly per the task brief: flipping this flag on a fleet with jobs
in-flight would discard their resumable progress, the same way changing
`CHUNK_TOKENS`/`OVERLAP_TOKENS` already would.

---

## What was implemented

**A minimal, reversible boundary-snapping option on `chunk_spans`,
`missing_link/worker.py`, default OFF.** It:

- Keeps the existing fixed-size word-count window logic and character-offset
  slicing **exactly as they are** — the word-index bookkeeping (`stride`,
  `overlap`) that decides *where* each chunk starts is untouched.
- Adds `snap_boundaries=False` to `chunk_spans`. When `True`, each chunk's
  start (except the first chunk's, pinned at 0) and end (except the last
  chunk's, pinned at `len(text)`) is nudged to the nearest sentence-ending
  punctuation or blank-line paragraph break within `BOUNDARY_SNAP_TOLERANCE`
  (120 characters — small against a ~17,000-character chunk at
  `CHUNK_TOKENS=4096`). If nothing qualifies within tolerance, the position is
  left unchanged — it falls back to the naive cut rather than forcing a bad
  snap.
- Uses a plain regex (`_SENTENCE_END_RE`, `_PARA_BREAK_RE`), the same shape as
  `audit.py`'s existing `_SENT_FALLBACK` — **no new dependency**, no
  tokenizer, no embedding model. Deliberately does **not** treat a bare `\n`
  as a boundary, because PDF-extracted text line-wraps at a fixed width (§4)
  and a lone newline there is exactly as arbitrary as the word count it would
  replace.
- Is not wired into `summarise_traced`, `run_one`, or the app — it exists as
  a tested, opt-in function, per the brief ("do not replace the chunker").
  Wiring it up (a `snap_boundaries` kwarg threaded through `summarise_traced`,
  or a job-level/global setting) is a small follow-on, deliberately left
  undone here to keep this change reversible and the diff tight while other
  agents are active in `missing-link/`.
- Every invariant `chunk_spans` already promised still holds with the flag
  on: `text[start:end] == chunk["text"]`, the document is still fully
  covered, the first chunk starts at 0, the last ends at `len(text)`. 13 new
  tests in `missing-link/tests/test_chunk_boundary_snap.py` cover: default-off
  behaviour is byte-identical to before; the snap picks the nearer of two
  candidates; it recognises paragraph breaks; it falls back to the naive cut
  when nothing is nearby; it never produces a degenerate (empty or inverted)
  chunk even under pathologically dense punctuation; and it changes chunk
  count by at most one. Full suite: 349 passed (13 new + the pre-existing
  count, which had already grown from 324 by the time this ran, from other
  agents' concurrent work).

### What would have to be measured before this becomes the default

1. **Whether a chunk boundary actually falls inside a qualifying-clause pair
   often enough to matter**, on the real corpus in `bench/out/` — this
   document never quotes that corpus's content, per the constraints on this
   task, so this is unmeasured here and should be the first thing checked.
2. **Whether `docs/audit-ledger.md`'s negation-battery re-run against
   full-size chunks** (its own already-stated blocker, §6 item 1) changes
   differently with snapping on vs off — if snapping measurably reduces the
   audit's false-negative rate on the qualifying-clause category, that is the
   strongest possible argument for turning it on by default.
3. **A coherence check on real output**, per `CLAUDE.md`'s standing rule that
   any change touching what the model sees must be paired with one — even
   though this change does not alter prompts, it does alter exactly which
   text each chunk's prompt contains near the edges, so it is not exempt from
   that rule just because it "only" moves an offset by up to 120 characters.
4. **Whether the reduce step actually reconciles a duplicated clause toward
   the correct version** when overlap gives it both a truncated (chunk N) and
   whole (chunk N+1) copy — untested with or without snapping, and directly
   relevant to whether snapping's benefit (fewer truncated map-step
   summaries in the first place) is worth more than the reduce step's
   existing de-duplication already provides.

None of the above has been run. **The naive splitter may simply be fine** —
nothing here shows it is not — and given §2's conclusion that even the
retrieval literature is trending toward "simple chunking is competitive," the
money may be better spent on item 1 above (a direct measurement on our own
corpus) than on building anything more elaborate than the snap already
implemented.

---

## Sources

- [arXiv:2410.13070 — "Is Semantic Chunking Worth the Computational Cost?"](https://arxiv.org/abs/2410.13070) (NAACL 2025 Findings) — REPORTED, abstract/index only
- [arXiv:2607.01852 — chunking strategies for RAG on academic texts](https://arxiv.org/html/2607.01852v1) — CONFIRMED, fetched
- [arXiv:2503.09600 — MoC: Mixtures of Text Chunking Learners (ACL 2025)](https://arxiv.org/html/2503.09600v1) — CONFIRMED, fetched
- [PMC12649634 — adaptive chunking for clinical RAG](https://pmc.ncbi.nlm.nih.gov/articles/PMC12649634/) — CONFIRMED, fetched
- [ResearchGate — "Performance Analysis of Text Chunking Methods for LLM-Based Document Summarization"](https://www.researchgate.net/publication/396884810_Performance_Analysis_of_Text_Chunking_Methods_for_LLM-Based_Document_Summarization) — NOT fetchable (403); search-index summary only, weak
- [Jina AI — Late Chunking](https://jina.ai/news/late-chunking-in-long-context-embedding-models/) — background only, does not apply to this pipeline
- [py-pdf/pypdf#544](https://github.com/py-pdf/pypdf/issues/544), [#2262](https://github.com/py-pdf/pypdf/issues/2262) — CONFIRMED, issue text read via search
- `missing-link/missing_link/worker.py`, `missing-link/missing_link/audit.py`, `missing-link/missing_link/extract.py`, `missing-link/requirements.txt`, `missing-link/requirements-audit.txt` — CONFIRMED, read directly
- `docs/audit-ledger.md`, `docs/EVALUATION.md`, `docs/FINDINGS.md` (F19, F24, F37#3, F38) — CONFIRMED, read directly

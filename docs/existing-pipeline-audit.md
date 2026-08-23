# Why aren't we using an existing pipeline?

**The operator's question, verbatim, 2026-08-23.** It is a fair challenge and
this document answers it with evidence rather than with a defence of the code
that already exists.

**Scope.** Five hand-rolled components: the sentence splitter, the chunker, the
entity index, the faithfulness cascade, and the map-reduce pipeline as a whole.
For each: what already exists, what it costs, and a verdict.

**Trigger.** F45 — our sentence splitter's line-based fallback distorts
clause-marker density on real legislation badly enough that a corpus-selection
verdict built on it is a verdict about the instrument. That is exactly the class
of defect a mature library should not have, so the splitter is where this audit
starts and it is where it finds the most.

---

## VERDICT FIRST

**The audit's own headline finding is a measurement, not an argument.** Run on
this project's real corpus, on the exact metric F45 says is broken:

| splitter | "sentences" in Privacy Act c.104 | marker rate | % fragments < 80 chars | seconds |
|---|---:|---:|---:|---:|
| **our regex fallback (production)** | 8,455 | **2.85%** | 65.0% | 0.01 |
| pysbd | 8,722 | **2.76%** | 66.2% | 133.94 |
| syntok | 4,272 | 4.59% | 62.2% | 14.93 |
| blingfire | 1,386 | 13.28% | 10.4% | 0.04 |
| **nupunkt** | **1,861** | **10.53%** | **12.3%** | 2.15 |

**CONFIRMED, measured in this audit on node 1, 2026-08-23.** Read the caveat on
these numbers in §1.3 before quoting them, and note that **none of them belongs
in `docs/measurements.md`** — they are a research spike, not a project
measurement.

Three things fall out of that table and they decide most of this document:

1. **pysbd — the library most often recommended for this exact job, and the one
   with a peer-reviewed paper claiming 97.92% on the Golden Rules — does not fix
   F45.** It is 0.09 points *worse* than the regex we already have, on the same
   document, and it takes over two minutes to do it. **A mature library is not
   automatically a better instrument; it is only better at the thing it was
   evaluated on**, and pysbd was evaluated on well-formed prose, not on
   legislation whose HTML is one paragraph per clause.
2. **One library does fix it, decisively, and it is not one of the five the
   brief named.** `nupunkt` is purpose-built for legal text, pure Python, zero
   runtime dependencies, MIT, model bundled in the wheel. It cuts the structural
   fragments that F45 blames (65.0% → 12.3%) and lifts legislation's marker rate
   from 2.85% to 10.53% — which is the "dramatically higher than narrative" F45
   said should have been there and was not.
3. **So the answer to "why aren't we using an existing pipeline?" is not one
   answer.** For the splitter it is *we should be, and we were looking at the
   wrong shelf*. For the chunker and the faithfulness cascade it is *because the
   deployment's constraints genuinely differ from the libraries' assumptions,
   and we have the measurements to say which*. For the pipeline as a whole it is
   *untested, and STATUS 4b already says so*.

### Verdict table

| Component | Candidates assessed | **Verdict** | Reason in one line |
|---|---|---|---|
| **Sentence segmentation** | pysbd, spaCy (sentencizer/senter/parser), NLTK punkt, syntok, blingfire, **nupunkt**, CharBoundary | **ADOPT-WITH-WRAPPER — `nupunkt`** | Only candidate that both fixes F45's measured distortion and adds zero runtime dependencies; wrapper because the offset contract and the `last_splitter` provenance record must survive |
| **Chunking** | LangChain `RecursiveCharacterTextSplitter`, LlamaIndex `SentenceSplitter`, semchunk, chonkie | **KEEP OURS** (revisit only if the splitter lands) | Our chunker's contract is *character offsets into the source*, which the audit ledger and citations depend on; every library returns strings, not spans. semchunk is the one worth a second look if that contract ever loosens |
| **Entity extraction** | spaCy NER, GLiNER, flair | **KEEP OURS** | These solve *extraction*; our problem is *resolution against a scope*, which none of them do. Adopting NER would also shrink recall in the one direction that matters — a fabricated name must still be extracted |
| **Faithfulness scoring** | AlignScore, SummaC, MiniCheck, RAGAS, DeepEval | **KEEP OURS** (deterministic tier); classifier tier stays off | F41 kills the whole category on cost before accuracy is even reached. RAGAS/DeepEval are LLM-as-judge, which is the exact inversion of this project's build rule |
| **Whole pipeline** | LlamaIndex `tree_summarize`, LangChain `map_reduce`/`refine` | **NEEDS-A-MEASUREMENT-TO-DECIDE** | STATUS 4b already says the claim that Missing Link beats `tree_summarize` is untested. This audit does not change that; it does remove one excuse (LlamaIndex is offline-installable, §5.3) |

---

## 1. Sentence segmentation

### 1.1 What we actually have

Two copies of a regex, not one splitter:

- `missing_link/audit.py:168` — `_SENT_FALLBACK`, used only when NLTK is absent,
  which in the production venv it always is. Records which splitter ran under
  `config.sentence_splitter`, which is good discipline.
- `missing_link/chunk_boundary_audit.py:79` — **the identical regex, used
  unconditionally**, with a module docstring explaining that NLTK is avoided
  here deliberately because F41 measured it producing 9.1% degenerate fragments
  on real markdown.

```python
_SENT_FALLBACK = re.compile(r"[^.!?\n]*[.!?]+[\"')\]]*|\S[^.!?\n]*$", re.MULTILINE)
```

**The `\n` inside both character classes is the whole of F45.** The regex cannot
cross a newline, so every line of a document becomes at least one
"sentence". On legislation, whose HTML is one `<p>` per subsection and one line
per table-of-contents entry, that turns thousands of headings and list items into
denominator.

### 1.2 What exists, assessed

All package metadata below is **CONFIRMED** — read from the PyPI JSON API on
2026-08-23, not from documentation.

| Library | Latest release | Runtime deps | Wheel | Licence | Python | Maintained? |
|---|---|---|---:|---|---|---|
| **nupunkt** 0.6.0 | 2025-08-05 (last commit 2025-11-18) | **0** | 9.1 MB | MIT | ≥3.11 | Yes, but thin — ALEA Institute, 39 commits, 32 stars |
| pysbd 0.3.4 | **2021-02-11** | 0 | 71 KB | MIT | ≥3 | **No** — Snyk records no release in 12 months and no PR/issue activity |
| syntok 1.4.4 | **2022-03-12** | 1 (`regex`) | 24 KB | MIT | ≥3.6,<4 | **No** |
| blingfire 0.1.8 | **2021-09-24** | 0 declared (needs `numpy` at import — see below) | **42 MB** | MIT | any | **No** |
| spaCy 3.8.15 | 2026-08-07 | **47** | large + a **12.8 MB separate model download** | MIT | ≥3.9 | Yes |
| NLTK 3.10.3 | 2026-08-12 | 21 | + `punkt_tab` **data download** | Apache-2.0 | ≥3.10 | Yes |
| CharBoundary 0.5.0 | 2025-04-06 | scikit-learn, numpy, onnx, onnxruntime, skops | large | MIT | ≥3.11 | Thin |

**On evaluation against legal text specifically — this is the discriminator and
almost nothing has one.**

- **nupunkt and CharBoundary are the only two candidates evaluated on legal text
  as their primary target.** REPORTED (arXiv:2504.04131, Bommarito, Katz &
  Bommarito, 2025): evaluated on "five diverse legal datasets comprising over
  25,000 documents and 197,000 annotated sentence boundaries"; NUPunkt reports
  91.1% precision and a 29–32% precision improvement over general-purpose tools;
  CharBoundary-large reports the highest F1 (0.782) of the methods tested. Those
  are the authors' own numbers on their own benchmark and this audit did not
  reproduce them — but the *design intent* is what matters here, and it is the
  only one aimed at our document type.
- **pysbd's evaluation is the Golden Rules Set** — REPORTED, arXiv:2010.09657 —
  a set of English sentence-boundary exemplars. 97.92% on GRS is a real result
  about abbreviations and quotations. It says nothing about a document that is
  structurally a list, which is why §1.3 finds it does not help us.
- **spaCy's senter is reported at ~93–94% accuracy and ~10× faster than the
  parser** (REPORTED, secondary sources; spaCy's own docs describe the four
  components without publishing a head-to-head on legal text). No legal-domain
  evaluation found. `blackstone`, the spaCy legal pipeline, is unmaintained.
- **NLTK punkt is already disqualified by our own measurement.** F41: 9.1%
  degenerate fragments on real markdown, splitting `"K.P. Dutt"` into `"P."`.

### 1.3 The measurement — does any of them fix the F45 failure?

**This is the part of the audit that is not a literature review.** F45 describes
a specific failure: documents whose HTML gives one short paragraph per clause
produce thousands of non-sentence structural fragments, inflating the
denominator of marker density. So: run each candidate on the real corpus in the
job store (read-only, `corpus_documents`), compute exactly
`chunk_boundary_audit.marker_density`'s statistic with each, and add the raw
marker-phrase count as a denominator-free control.

**CONFIRMED — run on node 1, 2026-08-23.** `%<80ch` is the structural-fragment
proxy F45 itself uses; `%noterm` is the fraction of "sentences" not ending in
terminal punctuation.

**Privacy_Act_1988_compilation_104 (legislative, 640,485 chars). Raw marker
phrases in the text: 251.**

| splitter | n "sentences" | marked | **rate** | %<80ch | %noterm | sec |
|---|---:|---:|---:|---:|---:|---:|
| regex-fallback (production) | 8,455 | 241 | **2.85%** | 65.0% | 50.3% | 0.01 |
| pysbd | 8,722 | 241 | **2.76%** | 66.2% | 53.4% | 133.94 |
| syntok | 4,272 | 196 | 4.59% | 62.2% | 40.2% | 14.93 |
| blingfire | 1,386 | 184 | 13.28% | 10.4% | not captured | 0.04 |
| **nupunkt** | **1,861** | 196 | **10.53%** | **12.3%** | **0.0%** | 2.15 |

**ISM_June_2026.pdf (standards, PDF — the hard-line-wrap manifestation).**

| splitter | n | rate | %<80ch |
|---|---:|---:|---:|
| regex-fallback | 10,269 | 0.56% | 52.4% |
| pysbd | 10,119 | 0.57% | 51.4% |
| syntok | 7,705 | 0.75% | 59.1% |
| **nupunkt** | **3,462** | **1.68%** | **3.5%** |

**NIST_SP_800-63B (nist standards, HTML) / OAIC_Ashley_Madison (regulatory,
HTML).**

| splitter | 63B rate | 63B %<80ch | OAIC rate | OAIC %<80ch |
|---|---:|---:|---:|---:|
| regex-fallback | 1.07% | 59.0% | 1.30% | 39.9% |
| pysbd | 1.71% | 34.5% | 1.57% | 28.4% |
| syntok | 2.32% | 20.6% | 2.36% | 13.4% |
| **nupunkt** | **2.34%** | **14.0%** | **2.21%** | **8.6%** |

**What this shows.**

- **The genre separation F45 says is broken is restored.** On the production
  splitter, legislative (2.85%) against regulatory (1.30%) is a 2.2× gap — F45's
  "only 2–4×", which it correctly called not-comparable-across-genres. Under
  nupunkt it is 10.53% against 2.21%, a **4.8× gap**, and legislation becomes
  unambiguously the most clause-dense genre in the corpus rather than merely
  slightly ahead. That is the shape the corpus page needs in order for an
  operator's document-selection verdict to be about the document.
- **The marked *numerator* is stable and the denominator is what moves.** 241 →
  196 marked units on the Act: fewer, because nupunkt merges the fragments that
  the regex split, so several marked fragments become one marked sentence. The
  raw phrase count (251) does not move at all. **The instrument changed; the
  document did not.**
- **`%noterm` = 0.0% under nupunkt** on every document. It never emits a
  fragment lacking terminal punctuation, which is precisely the class F45 says
  "structurally cannot carry `unless`/`except`/`subject to`".
- **It handles the PDF manifestation too** — the first of F45's three — folding
  hard-wrapped lines back into sentences (52.4% → 3.5% short fragments on the
  ISM PDF).

**Qualitatively, on the same passage** (CONFIRMED, printed from the spike):

nupunkt attaches the marginal note to the clause it belongs to —
`'Matters covered by code\n(5) However, despite paragraph 26C(3)(b), the
temporary APP code must not cover an act or practice that is exempt...'` — and
folds a lead-in with its lettered paragraphs into one legal sentence. The
production regex emits, as three separate "sentences",
`'Matters covered by code'`, `'Consultation etc.'`, and
`'(a) make a draft of the code publicly available; and'`. **Those three are the
F45 denominator, seen directly.**

**The honest caveats on this measurement, and they matter.**

1. **The seconds column is one run, on a shared box, with `llama-server`
   possibly working.** It is indicative of *order of magnitude only* — pysbd is
   ~10⁴× the regex, nupunkt ~10²× — and it is **not a project measurement.**
   Nothing here goes into `docs/measurements.md`.
2. **This measures the denominator, not segmentation correctness.** A splitter
   that merged the entire document into one sentence would score 100% marker
   rate and be useless. nupunkt is not doing that (1,861 units on 640 KB is
   ~344 chars/sentence, plausible for legislation), and the qualitative sample
   above is correct, but **no annotated ground truth was scored.** That is the
   gap between this and a claim that nupunkt is *right*.
3. **blingfire scores best on the metric and is still not the recommendation.**
   Last release 2021; a 42 MB wheel of precompiled shared objects; and it
   `ModuleNotFoundError`s on `numpy` at import despite declaring zero
   dependencies (CONFIRMED — it failed that way three times in this spike before
   `numpy` was installed). An undeclared dependency in an unmaintained binary
   package is a bad thing to put on seven air-gapped nodes.
4. **nupunkt requires Python ≥3.11 and node 1 has 3.11.2** (CONFIRMED,
   `python3 -V`). That is *exactly* on the boundary. Any node on an older Debian
   would fail to install it, and the fleet is not yet characterised.
5. **nupunkt's bus factor is small** — one institute, 39 commits, 32 stars. The
   mitigation is that it is pure Python with zero runtime dependencies and MIT,
   so it can be vendored outright; that is a real answer, not a hand-wave.

### 1.4 Verdict: ADOPT-WITH-WRAPPER — `nupunkt`

Wrapper, not raw adoption, for three reasons that are all project constraints:

- **The offset contract.** `audit.py:sentence_spans` returns `(start, end,
  text)` with **true offsets into the source**, because the ledger resolves a
  claim to a span in code rather than asking the model where it came from.
  `nupunkt.sent_tokenize` returns strings. The wrapper must recover spans, and
  must fail loudly rather than silently misalign if it cannot.
- **The provenance record.** `sentence_spans.last_splitter` exists because "a
  silently different splitter would change what 'sentence 3' means between two
  runs". Adding a third splitter makes that record *more* necessary, not less.
- **The fallback stays.** nupunkt is a 9.1 MB wheel; the regex is free and
  works. Keep the ladder — nupunkt if importable, regex if not — and record
  which ran.

**Do not turn it on and re-run nothing.** Every number in
`docs/chunk-boundary-measurement.md` and every `marker_rate` in
`corpus_documents` was produced by the old instrument. Changing the splitter
invalidates them, and the point of F45 is that a number whose instrument changed
under it is worse than no number.

**Also: delete one of the two copies of `_SENT_FALLBACK`.** Two identical
regexes in two modules, with two different docstrings about when NLTK is
preferred, is a splitter that can drift into disagreeing with itself.

---

## 2. Chunking

### 2.1 What we actually have

`worker.chunk_document` (and `chunk_spans`, the offset-returning variant): split
on whitespace, `size = int(4096 * 0.70)` words, stride `size − int(410 * 0.70)`.
Plus `snap_boundaries`, an opt-in, default-off regex nudge to the nearest
sentence or paragraph break within 120 characters.

Deliberately does not tokenise. The comment says why: *"doing so would mean
shipping the model's tokeniser into the queue process"*.

### 2.2 What exists, assessed against the one constraint that decides it

The brief asks whether any library supports the token-budget-per-slot
constraint. **All four do. That is not the discriminator.**

- **LangChain `RecursiveCharacterTextSplitter`** — CONFIRMED from source
  (`langchain_text_splitters/base.py`): `chunk_size=4000`,
  `length_function=len`, i.e. **characters by default**. Token budgeting needs
  `.from_tiktoken_encoder()` or `.from_huggingface_tokenizer()`. Separators
  default to `["\n\n", "\n", " ", ""]` — and `docs/chunking-research.md` §4
  already established, from pypdf's own tracker, that PDF-extracted text has no
  `\n\n`, so on our commonest input it degrades to roughly what we already do.
  Depends on `langchain-core`.
- **LlamaIndex `SentenceSplitter`** — CONFIRMED from source
  (`node_parser/text/sentence.py`): `chunk_size` **is** tokens,
  `chunk_overlap=200` tokens, and `tokenizer` is an injectable `Callable`. Its
  sentence splitting is **NLTK punkt** (`split_by_sentence_tokenizer` →
  `globals_helper.punkt_tokenizer`), which F41 has already measured degenerating
  on our material. Its default token counter is tiktoken `cl100k_base`.
- **semchunk 4.1.1** — 19 KB wheel, 2 declared deps, MIT, released 2026-06-13.
  **CONFIRMED by running it**: `semchunk.chunkerify(token_counter, chunk_size=4096)`
  accepts a *plain callable*, so it honours a token budget with **no tokenizer
  and no download at all**. Fed our own `len(text.split())/0.70` approximation
  and the real OAIC document, it produced 9 chunks, max counted size 3,365
  against a 4,096 budget, and split at sentence boundaries.
- **chonkie 1.7.0** — MIT, actively released, but base install pulls `numpy`,
  `httpx`, `tenacity`, `tqdm` and a compiled `chonkie-core`, and its interesting
  chunkers live behind extras that pull `model2vec`/`sentence-transformers`.

### 2.3 Verdict: KEEP OURS

**Not sunk cost. Two specific constraints, both already measured.**

1. **Our chunker's real contract is character offsets, not chunks.**
   `chunk_spans` guarantees `text[start:end] == chunk["text"]`, full coverage,
   first chunk at 0, last at `len(text)`. The audit ledger, the citation
   resolution and F42's catch all depend on resolving a claim to a *span in the
   source*. Every library above returns strings. Recovering offsets from
   returned strings — with overlap, and with any normalisation applied — is a
   re-implementation of the thing we would be deleting, plus a new class of
   silent misalignment bug.
2. **`CHUNK_TOKENS=4096` and `-c 32768` are measured optima on this hardware**,
   not defaults, and the extended sweep found raising `-c` costs 33% more
   wall-clock on identical chunking (`docs/measurements.md`). Any library whose
   token accounting differs from our `WORDS_PER_TOKEN` approximation moves the
   chunk size off a measured optimum — and `n_ctx_slot` is a hard cliff, not a
   gradient.

**What would change this verdict:** if the splitter work in §1 lands and we want
sentence-aware chunk edges for real rather than via the 120-char snap,
**semchunk is the one to look at** — it is the only candidate that is small
enough (19 KB, 2 deps), offline by construction, and accepts our own token
counter rather than imposing one. It would still need the offset contract
rebuilt on top. **NEEDS-A-MEASUREMENT if that day comes; KEEP OURS today.**

---

## 3. Entity extraction and the canonical entity index

### 3.1 What we actually have

`cascade.extract_entities` — regex over identifiers, acronyms and runs of
capitalised words, with two measured corrections (leading capitalised function
words stripped; single sentence-initial capitals skipped). Then
`entity_index.py`: a rule ladder, strictest first — `exact`, `substring`,
`compact_substring`, `all_parts`, `initials`, `whole_fuzzy` — resolving a
summary entity against a *scope*, and reporting **which rule and which scope**
matched. Stdlib only: `re`, `difflib`, a dict.

### 3.2 What exists

- **spaCy NER** — `en_core_web_sm` is a 12.8 MB wheel published on **GitHub
  Releases, not PyPI** (CONFIRMED from the GitHub API). `spacy download` fetches
  it at runtime. Plus 47 spaCy dependencies.
- **GLiNER** — Apache-2.0, actively released (2026-07-24), 19 declared deps
  including **`torch` and `transformers`**, and it downloads model weights from
  Hugging Face on first use. REPORTED CPU latency of 130–208 ms per
  classification.
- **flair** — MIT, but last release **2025-02-05**, 27 deps, `torch`-based,
  model download at first use.

### 3.3 Verdict: KEEP OURS

**These libraries solve a different problem than the one we have.** They do
*extraction*: given text, find the entities. Our checker's hard part is
*resolution*: given an entity a summary asserts, is it one of the entities this
scope is about, and by which rule, and how confidently? `two-scope-and-entity-index.md`
is 400 lines about that question and none of it is available off the shelf.

Three concrete reasons, in order of strength:

1. **The recall profile is inverted from what NER is trained for.** The checker
   must extract a *fabricated* name from the summary in order to fail it. An NER
   model that has learned what real entities look like is under exactly the
   wrong incentive: the names it is least confident about are the ones we most
   need to see. A capitalised-run regex has no such bias.
2. **Both error directions are measured, per rule, and one rule is measured and
   rejected.** `REJECTED_RULES` records `part_fuzzy` removing 0.9 points of
   false positives at a cost of 5.4 points of near-miss catch, and
   `any_part_present` failing by construction. No library exposes a matching
   ladder you can sweep and disable per-rule with those numbers attached, and
   the project's standing bias — *a false positive is visible and a silent miss
   is not* — has to be enforceable at that granularity.
3. **Dependency weight buys nothing here.** `torch` + `transformers` +
   first-use model download, on seven possibly-air-gapped nodes, to replace ~40
   lines of regex — against a component whose current cost is part of a **3.5 s**
   whole-document cheap tier (`docs/faithfulness-cascade.md` §9).

**One qualification, stated honestly:** our extraction *is* weak on
lowercase-heavy and OCR-damaged text, and §3.4 of `faithfulness-cascade.md`
records the entity check being demoted from FAIL to ROUTE partly for that
reason. If a future corpus makes extraction the binding constraint rather than
resolution, **GLiNER's ONNX path is the candidate** — it is the only one that
avoids `torch` at inference. That is a NEEDS-A-MEASUREMENT branch, not today's
verdict.

---

## 4. Faithfulness scoring

### 4.1 What F41 does and does not disqualify

**Read this carefully, because F41 is easy to over-apply.** F41's finding is
that *a classifier's reliability degrades with evidence length*, measured on the
MiniCheck two-model ensemble at ~4096-token evidence, and that the cost
(17.74 s and 8.81 s per claim) exceeds the summarisation job being audited.

That is a finding about **whole-evidence classification**. Two of the candidates
below are architecturally designed *around* exactly that problem, so F41 does
not refute their design — it refutes their affordability, which turns out to be
worse, not better.

| Candidate | How it handles long evidence | Does F41 refute it? | Verdict |
|---|---|---|---|
| **MiniCheck** | one call, whole evidence | **Yes, directly** — this is what F41 measured | Built, gated, off by default. Correct as is |
| **SummaC** (`SummaCConv`) | REPORTED: splits the document into sentences and aggregates **pairwise** NLI over sentence pairs, explicitly to fix the granularity mismatch | **No — but cost is worse.** Pairwise over ~18 claim-sentences × hundreds of source sentences per chunk is orders of magnitude more NLI calls than the one-call-per-claim F41 already priced at 199 min for hop 1 alone | **KEEP OURS.** Right idea, unaffordable on 4 cores |
| **AlignScore** | REPORTED: splits context into ~350-token chunks, claim into sentences, scores each pair, takes max | **No — but cost is worse**, same reason | **KEEP OURS.** Also **not on PyPI** — install is `pip install git+https://github.com/yuh-zha/AlignScore.git`, which is a hard no on an air-gapped fleet |
| **RAGAS** | LLM decomposes the answer into claims, then an LLM judges each | **N/A — different failure mode** | **KEEP OURS**, and it is the clearest category error of the five (see below) |
| **DeepEval** | LLM-as-judge, plus telemetry | **N/A** | **KEEP OURS** |

### 4.2 RAGAS and DeepEval are the wrong shape, not merely the wrong cost

**CONFIRMED** from RAGAS's own documentation: faithfulness is computed by using
an LLM to decompose the response into statements and an LLM to verify each
against the context. **That is precisely the inversion of this project's build
rule** — *prefer deterministic code to model judgement wherever the work is
computable*, and *never ask the model where something came from*. Our cascade's
strongest result exists because it did the opposite: **978 real claims, 3
findings, all 3 manually verified genuine, zero false positives, ~3.5 s, no
model** (`docs/faithfulness-cascade.md`). Handing that to an LLM judge would
replace a certainty with a probability and pay for the privilege.

DeepEval additionally declares `posthog` twice in its dependency set and pulls
OpenTelemetry — telemetry on a machine holding legally-sensitive documents is a
conversation this project should not have to have.

**One genuinely interesting lead, recorded not recommended:** RAGAS offers
`FaithfulnessWithHHEM`, which swaps the LLM judge for Vectara's HHEM-2.1-Open —
a small T5 classifier, and **the model behind the very leaderboard
`docs/MODEL-SELECTION.md` already cites for hallucination rates**. It is much
smaller than Flan-T5-Large. Whether it survives 4096-token evidence any better
than MiniCheck is **exactly the F41 question and is unmeasured**. If anyone ever
re-opens the classifier tier, HHEM is the cheapest candidate to test and the
test is F41's, run again. **NEEDS-A-MEASUREMENT — and note that F41 is the
reason to be pessimistic, not a reason not to look.**

### 4.3 A note on the PyPI name `minicheck`

**CONFIRMED, and it is a trap worth writing down:** `pip install minicheck`
installs version 0.4.0, whose dependencies are `z3-solver` and `pytest` — a
completely unrelated model-checking package. Our `requirements-audit.txt`
correctly pins `minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main`.
Anyone "simplifying" that line to a PyPI name gets a silently wrong package.

---

## 5. The pipeline as a whole

### 5.1 What STATUS already says

STATUS 4b is explicit: nothing mature exists to adopt (private-gpt, Kotaemon,
localGPT are RAG-QA — top-k retrieval, the opposite of reading a whole
document), **but** LlamaIndex `tree_summarize` is the closest building block and
**"the project implicitly claims Missing Link is better than reaching for the
obvious library; that claim is currently untested."**

**This audit does not change that verdict and should not be read as changing
it.** It is still NEEDS-A-MEASUREMENT.

### 5.2 What Missing Link has that a library call does not

Listed so the baseline run, when it happens, compares like with like. These are
not features of `tree_summarize` and their absence is not a bug in
`tree_summarize` — they are the seams where every defect since F34 has lived:

- an async job store that survives a restart, with resumability per chunk
- fan-out across R endpoints (`LLAMA_URLS`)
- `extract_content` refusing on empty, on truncated, and on
  reasoning-exhausted-the-budget output — a protocol parser, not a return value
- `MAX_INSTRUCTION_WORDS`, sized against `n_ctx_slot` so a per-job instruction
  cannot silently overflow a slot
- `parse_section_citations` dropping an invented `[Section 47]` rather than
  rendering it
- the failure taxonomy: recognised failures retry, unrecognised are permanent
- `DEFAULT_TIMEOUT_S = 3600`

### 5.3 One excuse this audit removes

The timeout point in STATUS is right and stands. But there was a second implicit
worry — that a library would need the internet at run time. For LlamaIndex
specifically, **it does not**: CONFIRMED by unpacking the
`llama_index_core-0.14.24-py3-none-any.whl` (11.9 MB), which **bundles both**
`_static/nltk_cache/tokenizers/punkt_tab.zip` (4.3 MB) **and**
`_static/tiktoken_cache/` (two BPE blobs, 1.7 MB and 3.6 MB). Its
`GlobalsHelper` prefers the bundled cache and only falls back to
`nltk.download()` if it is missing. **So `tree_summarize` can be run air-gapped
from a wheelhouse**, and "it needs the internet" is not a reason to skip the
baseline.

(LangChain has no such bundling. `from_tiktoken_encoder()` calls tiktoken, which
**does** fetch `cl100k_base` from `openaipublic.blob.core.windows.net` on first
use unless `TIKTOKEN_CACHE_DIR` is pre-seeded — REPORTED, and the subject of
several open issues including `openai/tiktoken#369` and `#232`.)

---

## 6. What adoption costs, on this fleet specifically

`docs/DESIGN-NOTES.md` K settled the mechanism: no Docker, a **vendored
wheelhouse** — `pip download -r requirements.txt -d wheelhouse/` on a machine
with internet, `pip install --no-index --find-links=wheelhouse` on the node.
Production is currently **7 packages**. That is the bar every candidate is
measured against.

| Cost | Who pays it |
|---|---|
| **Runtime model/data download** — fatal air-gapped, and a wheelhouse does not fix it because pip is not what does the fetching | NLTK (`punkt_tab`), spaCy (`spacy download` hits GitHub Releases), GLiNER + flair (Hugging Face), tiktoken (Azure blob), MiniCheck, SummaC, AlignScore |
| **No download — model bundled in the wheel** | **nupunkt** (9.1 MB), **LlamaIndex** (11.9 MB, punkt + tiktoken both bundled) |
| **No model at all** | semchunk, pysbd, syntok, LangChain text-splitters |
| **Not installable from a wheelhouse at all** | **AlignScore** — GitHub-only, no PyPI release |
| **Python floor** | nupunkt and CharBoundary require **≥3.11**; node 1 has 3.11.2 — on the line. The rest of the fleet is uncharacterised |
| **Dependency count added to a 7-package production install** | nupunkt **+0**; semchunk **+2**; syntok +1; chonkie +5 incl. numpy and a compiled core; LangChain +`langchain-core`; LlamaIndex +29; spaCy +47; GLiNER/flair/SummaC/AlignScore + the whole torch stack (`requirements-audit.txt` records that stack at **1.5 GB even on the CPU-only wheel index**, and 5.2 GB if you follow the documented CUDA install) |
| **Licence** | Every candidate assessed is MIT or Apache-2.0. Not a differentiator |
| **Wheel bulk over a 100 Mb LAN (F28)** | Real but small against the 65 GB model budget. Only blingfire (42 MB, unmaintained, undeclared numpy dep) is objectionable, and it is objectionable for other reasons |

**The pattern:** the two adoptions this audit recommends or half-recommends
(nupunkt, semchunk) add **zero and two** dependencies respectively and download
nothing. That is not a coincidence — it is the filter that survived, and it is
worth stating as a rule for the skill later: **on an air-gapped fleet, "does it
fetch anything at first use?" eliminates more candidates than accuracy does.**

---

## 7. The uncomfortable part

**Three of the five verdicts are KEEP OURS, and a reader is entitled to suspect
that is what an audit written by the author of the code would conclude.** So,
plainly:

**Where the challenge lands.** The splitter should have been a library from the
start, and the reason it was not is visible in the record: `chunking-research.md`
§4 concluded *"a cheap regex-based sentence-boundary approximation — no new
dependency, no model — is the only structure-aware option that fits the
constraints"*, having assessed nltk and spaCy and correctly rejected both. **The
search was for a general-purpose splitter light enough to ship, and it never
asked whether a legal-domain splitter existed.** One does, it is lighter than
either rejected candidate, and F45 is the bill for not having asked. The
transferable lesson is not "use libraries" — it is that **the right query was
"what do people who process legislation use", not "what is the best sentence
splitter"**, and this project's own conventions already say to search for how
others solved *the same problem*, not the general one.

**Where the challenge does not land, and why these are constraints rather than
excuses.** The chunker's offset contract, the entity index's measured
per-rule error directions, and the cascade's refusal to hand judgement to a
model are each traceable to a specific finding (F42's catch, the
`REJECTED_RULES` sweep, F41's cost inversion) — not to preference. **The test of
that claim is falsifiable and it is written down**: if the `tree_summarize`
baseline in STATUS 4b beats Missing Link on wall-clock *and* faithfulness, most
of §5 was wrong. That measurement has not been run and this document is not a
substitute for it.

---

## Not established — do not cite as settled

- **That nupunkt segments legislation *correctly*.** What was measured is that
  it removes the structural fragments F45 blames and restores genre separation.
  No annotated ground truth was scored. Its reported 91.1% precision is the
  authors' number on the authors' benchmark.
- **Any timing figure in this document.** One run, one box, no isolation from
  `llama-server`. Indicative of order of magnitude, nothing more, and **not
  eligible for `docs/measurements.md`.**
- **That adopting nupunkt would change any downstream decision other than the
  corpus page's marker density.** The chunk-boundary sweep would have to be
  re-run to know.
- **That SummaC or AlignScore would be more accurate than MiniCheck at
  production evidence length.** Their designs address the granularity mismatch;
  neither was run here, and both were ruled out on cost and installability
  before accuracy was reached.
- **That HHEM-2.1-Open survives 4096-token evidence.** Unmeasured. F41 is the
  reason to expect it might not.
- **Anything about nodes 2–7.** The Python floor for nupunkt (≥3.11) was checked
  on node 1 only.

---

## Appendix — reproducing the §1.3 measurement

Read-only against the job store; no production code touched. Run in a scratch
venv, not the production one.

```bash
python3 -m venv /tmp/venv-sbd
/tmp/venv-sbd/bin/pip install nupunkt pysbd syntok blingfire numpy semchunk
```

The spike script splits each `corpus_documents.text` with each candidate,
applies `chunk_boundary_audit.MARKER_RE` unchanged, and reports
`n_sentences`, `n_marked`, `rate`, the fraction of units under 80 characters,
and the fraction not ending in terminal punctuation — plus
`len(MARKER_RE.findall(text))` as a denominator-free control. The production
regex is copied verbatim from `chunk_boundary_audit.py:79` so the comparison is
against what actually runs.

**Expect pysbd to take over two minutes on the 640 KB Privacy Act compilation**;
budget the run accordingly or exclude it.

---

## Sources

Primary, read directly (CONFIRMED):

- `missing_link/audit.py`, `chunk_boundary_audit.py`, `worker.py`,
  `cascade.py`, `entity_index.py`, `requirements.txt`, `requirements-audit.txt`
- `docs/FINDINGS.md` F41, F42, F45; `docs/faithfulness-cascade.md`;
  `docs/two-scope-and-entity-index.md`; `docs/chunking-research.md`;
  `docs/chunk-boundary-measurement.md`; `docs/minicheck-spike.md`;
  `docs/DESIGN-NOTES.md` K; `STATUS.md` §4b
- PyPI JSON API (`https://pypi.org/pypi/<name>/json`) for every package's
  version, upload date, licence, `requires_python` and `requires_dist`
- `llama_index_core-0.14.24-py3-none-any.whl`, unpacked
- <https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/text/sentence.py>
- <https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/utils.py>
- <https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/base.py>
- <https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/character.py>
- <https://api.github.com/repos/explosion/spacy-models/releases/tags/en_core_web_sm-3.8.0>

Secondary (REPORTED):

- Bommarito, Katz & Bommarito, *Precise Legal Sentence Boundary Detection for
  Retrieval at Scale: NUPunkt and CharBoundary*, arXiv:2504.04131 —
  <https://arxiv.org/abs/2504.04131>
- <https://github.com/alea-institute/nupunkt>
- Sadvilkar & Neumann, *PySBD: Pragmatic Sentence Boundary Disambiguation*,
  arXiv:2010.09657 — <https://arxiv.org/abs/2010.09657>
- <https://github.com/nipunsadvilkar/pySBD> and
  <https://snyk.io/advisor/python/pysbd> (inactivity)
- Laban et al., *SummaC: Re-Visiting NLI-based Models for Inconsistency
  Detection in Summarization*, TACL 2022 — <https://arxiv.org/abs/2111.09525>
- Zha et al., *AlignScore*, ACL 2023 — <https://github.com/yuh-zha/AlignScore>
- <https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/>
- <https://github.com/urchade/GLiNER>
- <https://spacy.io/models/> and <https://spacy.io/api/sentencizer>
- tiktoken offline behaviour: <https://github.com/openai/tiktoken/issues/369>,
  <https://github.com/openai/tiktoken/issues/232>

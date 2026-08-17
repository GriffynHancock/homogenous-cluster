# Evaluation: datasets, metrics, and what is actually worth measuring

**Researched 2026-08-17.** Supersedes the evaluation design in the original plan
(Task 14), which proposed BillSum + the SummEval rubric + ROUGE.

Labels: **CONFIRMED** (primary source read directly), **REPORTED** (secondary
source states it), **INFERRED** (reasoning). Nothing here has been run on this
hardware yet — this file records *what to run and why*, and every number in it is
someone else's until we produce our own.

---

## The reframing that comes first: do not try to reproduce the leaderboard

`F25` re-opened the Model B decision on **reported** hallucination rates from the
Vectara leaderboard (Kimi K2 17.9%, GLM-4.6 9.5%). The obvious response — measure
hallucination rates ourselves — **is statistically hopeless at our scale.**

To separate those two candidates at 80% power, α = 0.05:

```
n = (1.96 + 0.84)^2 x [0.095(0.905) + 0.179(0.821)] / (0.084)^2 ~= 259 per model
```

**~260 documents per model.** To separate GLM-4.6 (9.5%) from GLM-4.5-Air (9.3%)
would take tens of thousands. Vectara used 7,700+.

**So: use the leaderboard for ranking. It is a better instrument than anything we
can build.** Spend our cluster-nights on the question the leaderboard
structurally cannot answer — see "The experiment that is actually ours" below.

---

## 1. Summarisation datasets

| Dataset | Domain | Length | Licence | Contamination |
|---|---|---|---|---|
| **Multi-LexSum** | **US civil-rights litigation, expert-lawyer-authored** | source often 200+ pages; targets from 1 sentence to 500+ words | **ODC-By** (summaries CC BY-NC; source docs public domain) | **lower** — niche corpus (INFERRED, not confirmed) |
| GovReport | US federal CRS/GAO reports | ~9K tok avg, up to ~30K | not stated on the HF card (INFERRED public-domain US-gov text) | **HIGH** — circulates freely, mirrored in SCROLLS/ZeroSCROLLS |
| QMSum | meetings, incl. **parliamentary committee** | ~9K-word transcripts | MIT | moderate |
| BookSum | novels/plays (Gutenberg) | book-length | source public domain; scraped summaries have attribution issues | **HIGH** — Gutenberg is in every pretraining corpus |
| SQuALITY | short stories (Gutenberg) | 4–6K words | CONFIRMED via repo | lower than BookSum |
| MultiNews | news clusters | ~500–800 words/article | not confirmed | **HIGH** |
| SummScreenFD | TV transcripts | episode-length | unclear (fan-wiki derived) | **HIGH** |
| arXiv / PubMed | scientific literature | 3–10K words | per-paper | **HIGH** — staple pretraining corpora |
| ~~MENSA~~ | movie scenes → Wikipedia plots | — | — | **checked and ruled out** — not legal/gov/health |

**RECOMMENDATION: Multi-LexSum as primary.** It is the only option that is
domain-matched *and* expert-authored *and* of manageable contamination risk, and
its **multi-granularity targets** let us test whether faithfulness changes with
target summary length — which is directly relevant, because our map step produces
short summaries and the reduce step a long one. **GovReport secondary, with the
contamination caveat attached to every number**: a good GovReport score is not
evidence of faithfulness on novel real documents.

**Do not use** BookSum, MultiNews, SummScreen (contaminated and/or wrong domain).

Sources: [Multi-LexSum](https://arxiv.org/abs/2206.10883) ·
[repo](https://github.com/multilexsum/dataset) ·
[GovReport HF card](https://huggingface.co/datasets/ccdv/govreport-summarization) (CONFIRMED) ·
[QMSum](https://github.com/Yale-LILY/QMSum) · [SQuALITY](https://arxiv.org/abs/2205.11465) ·
[BookSum](https://arxiv.org/pdf/2105.08209)

### An unresolved domain gap

**No open-licensed health/clinical long-document summarisation or reasoning
benchmark was found.** PubMed/arXiv summarisation is biomedical *literature*, not
clinical or health-administrative material. `CLAUDE.md` names health as a target
sector, so **one of the three target domains has no benchmark at all.** This is a
real gap, not a citation gap. Options: proceed on legal/government evidence and
state the limitation, or build a small internal set from real (non-publishable)
documents and report only aggregate scores.

---

## 2. Reasoning benchmarks — and which ones to ignore

**Useful (genuine multi-hop / global reasoning over real documents):**

| Benchmark | Why it counts |
|---|---|
| **LooGLE v2** | 10 long-dependency task types incl. **law/finance**, real texts 16K–2M tokens, explicitly designed against needle-in-a-haystack artefacts. CC BY 4.0, NeurIPS 2025 |
| SCROLLS → **ContractNLI** | legal contract entailment. Structurally the *inverse* of our problem: correctly determining entailment is the opposite of fabricating a non-entailed claim |
| LongBench-v2 | 503 multi-choice, 8K–2M words. Human 53.7%, best direct model 50.1%, o1-preview 57.7% — the direct-vs-reasoning gap is itself evidence it is not lookup (CONFIRMED via [arXiv:2412.15204](https://arxiv.org/abs/2412.15204)) |
| NoCha | claim pairs on **recent** novels (so less contaminated); GPT-4o 55.8% vs human 96.9%, no open model beats chance ([arXiv:2406.16264](https://arxiv.org/abs/2406.16264)) |
| NovelHopQA, NovelQA | multi-hop over 200K+ token narrative |

**Near-useless here (retrieval, not reasoning):** RULER's base NIAH variants,
InfiniteBench passkey/number-retrieval, HELMET's "recall" category. RULER's
multi-hop-tracing and aggregation subtasks are a step up but still **synthetic**
haystacks. Lookup is not our bottleneck — **prefill throughput and faithfulness
are** — so these measure a capability we do not lack.

**RECOMMENDATION: LooGLE v2 (law subset) primary, ContractNLI as a cheap legal
probe.**

---

## 3. Faithfulness metrics that run on CPU

Scores are Balanced Accuracy on **LLM-AggreFact**, where GPT-4 = 75.3.

| Metric | Size | CPU | Open weights | BAcc |
|---|---:|---|---|---:|
| **MiniCheck-Flan-T5-Large** | 770M | **yes** | `lytang/MiniCheck-Flan-T5-Large` | **74.7** |
| MiniCheck-RoBERTa-Large | 355M | yes | `lytang/MiniCheck-RoBERTa-Large` | 72.7 |
| MiniCheck-DeBERTa-v3-Large | ~435M | yes | `lytang/MiniCheck-DeBERTa-v3-Large` | 72.6 |
| AlignScore | 125M / 355M | yes | `yzha/AlignScore` | 70.4 |
| SummaC-ZS / -Conv | ~355M | yes | yes | 67.9 / 62.1 |
| FENICE | 220M + 435M, two-stage | yes | `Babelscape/FENICE` | claims SOTA — **REPORTED, tables not read** |
| QAFactEval | 3 models (~1.1B total) | yes but slower | yes | "+14% over prior QA metrics" (REPORTED) |
| FactCC | BERT-base | yes | yes | 2019, superseded |
| Bespoke-MiniCheck-7B | 7B | **marginal** at hundreds of docs | yes | — |
| FACTS Grounding | 3 frontier LLM judges | **no** — API-based | no | — |

**RECOMMENDATION: MiniCheck-Flan-T5-Large primary, MiniCheck-RoBERTa-Large as a
cheap second opinion.** Note this **replaces** the plan's AlignScore/SummaC
choice: MiniCheck-Flan-T5 is **+4.3 BAcc over AlignScore** and within **0.6 of
GPT-4**, at 770M params on CPU.

**Avoid a 7B+ LLM-as-judge.** INFERRED: a judge pass per chunk at our measured
~6 tok/s would roughly double total cluster time, for a metric a 770M model
already delivers.

### The finding that changes our pipeline design

**CONFIRMED** via [arXiv:2511.07689](https://arxiv.org/abs/2511.07689), *"Stress
Testing Factual Consistency Metrics for Long-Document Summarization"* — tested
BARTScore, SummaC, AlignScore, UniEval and MiniCheck on long documents across
three domains **including legal (LexAbSumm)**:

- **No metric is consistently robust at whole-document scope.**
- Scores degrade on "information-dense claims semantically similar to many parts
  of the source document" — exactly what a legal document is.
- MiniCheck specifically struggles with **logical negations**, worst in the legal
  subset. Legal text is full of "must not", "may not be destroyed while".
- **But metrics improve markedly when the evidence window is correctly scoped**,
  *particularly for legal documents*.

**Therefore: score each chunk summary against ITS OWN source chunk, not the final
summary against the whole document.** Map-reduce already produces exactly that
pairing for free.

**This independently confirms the provenance fix in `DESIGN-NOTES.md` E
(concession 3),** which was argued on design grounds alone: chunk summaries must
carry their chunk id and source offsets. That change is what makes correctly-
scoped scoring mechanically possible. Two separate lines of reasoning arriving at
the same requirement is the strongest signal available that it is right.

---

## 4. The experiment that is actually ours

**Does map-reduce amplify fabrication relative to single-pass?** F25 flagged this
as INFERRED and it is the project's real exposure.

**No published work runs it.** The closest analogues:

| Paper | What it studies | Transfers? |
|---|---|---|
| [arXiv:2410.13961](https://arxiv.org/abs/2410.13961) (NAACL 2025 Findings) | combining **multiple independent documents** — reduce-like. Up to 75% hallucinated content adversarially; models fabricate 79%/44% of the time (GPT-3.5/GPT-4o) when asked about absent information | partial — a risk signal for a reduce step over sparse chunk summaries, not a same-document comparison |
| [arXiv:2606.07937](https://arxiv.org/abs/2606.07937) "Hallucination Cascade" | **sequential iterative refinement** across agents. Found net **attenuation** (0.422 → 0.272) alongside declining factual accuracy | architecturally different — sequential refine ≠ parallel map then combine. **Do not generalise its attenuation finding to us** |
| [arXiv:2602.08149](https://arxiv.org/abs/2602.08149) DIAL-SUMMER | error taxonomy at structural levels within one summary | "hierarchical" means something else there |

**So the experiment is a novel contribution, not a replication.** Design:

- Same documents, same model, **single-pass full-context vs map-reduce**.
- **Paired** — document-level variance cancels, so **~30–50 documents suffice**,
  against the ~260 needed to rank models. This is why the reframing matters.
- Score with MiniCheck at **chunk scope** per §3.
- Report the delta, not the absolute rate.

---

## Priority order

1. **Stand up MiniCheck as a per-chunk scorer** (Flan-T5-Large primary,
   RoBERTa-Large cross-check), scoring each chunk summary against its own chunk.
2. **Assemble the set**: Multi-LexSum primary, GovReport secondary (caveated),
   LooGLE v2 law subset + ContractNLI on the reasoning side.
3. **Run single-pass vs map-reduce.** Nothing existing answers it; it is both our
   internal QA gate and publishable on its own.

**Prerequisite for 1 and 3:** the provenance change in `DESIGN-NOTES.md` E.
Without chunk ids and offsets there is no correctly-scoped evidence window to
score against.

## Not verified — do not cite as settled

- FENICE's SOTA claim (abstract only; result tables unread).
- MiniCheck's maximum input length, and whether the released code splits long
  inputs automatically or expects the caller to.
- GovReport's exact licence string.
- TofuEval's correlation numbers for LLM-generated summaries.
- Whether Multi-LexSum's lower contamination risk is real or merely plausible.

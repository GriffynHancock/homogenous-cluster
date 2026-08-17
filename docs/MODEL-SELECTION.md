# Model selection

**Selection criteria derived from measurement on node 1.** Model names are
filled in from benchmark research; the *criteria* below are hardware facts and
do not depend on which models are current.

Target scenario: **every node holds a copy of each model in the working set on
disk, and loads one at a time into RAM.**

---

## The two budgets, which are different and often confused

| Budget | Per node | What it limits |
|---|---:|---|
| **Disk** | **368 GB free** (477 total, 2026-08-17) | how many models you can KEEP |
| **RAM** | 125 GB (~98 GB at the 75% margin) | how big the model you can RUN is |

**Disk holds many; RAM runs one.** A node can store gpt-oss-120b *and*
Qwen3-Next-80B *and* a small model — 188 GB of 431 — but cannot hold two large
ones resident simultaneously (61 + 93 = 154 GB > 125 GB). Switching models means
a reload, which is minutes on a 61 GB file, not seconds.

Only Model B breaks this: at 547 GB it exceeds even disk, must be sharded, and
lives on the coordinator alone.

---

## Criterion 1 — ACTIVE parameters set speed. This is the dominant filter.

Generation runs at **17.3 GB/s effective for sparse MoE** (61% of the 28.2 GB/s
STREAM ceiling — F24) and **28.2 GB/s for dense** (~99%, F11).

```
tok/s ≈ effective_bandwidth / (active_params × bytes_per_weight)
```

| Active params | Quant | GB/token | **tok/s (MoE)** | Verdict for overnight document work |
|---:|---|---:|---:|---|
| 3 B | Q8 | 3.2 | **5.4** | comfortable |
| 5.1 B | MXFP4 | 2.7 | **6.4** | comfortable (**measured 6.05**) |
| 12 B | Q6 | 9.8 | 1.8 | usable |
| 22 B | Q4 | 11.0 | 1.6 | usable |
| 32 B | Q4 | 16.0 | **1.08** | slow but clears the overnight bar |
| 37 B+ | Q4 | 18.5+ | <1.0 | **reject** — below one token/second |

**Rule: prefer ≤6B active for anything replicated per node. Accept up to ~32B
active only for the single sharded frontier model.**

Total parameter count is almost irrelevant to speed — it only sets RAM. A 1T
model with 32B active is *faster* than a 70B dense model, which reads all 70B
every token.

## Criterion 2 — Total size sets which topology is possible

| Total size | Topology | Consequence |
|---|---|---|
| **≤ 98 GB** | **replicate on every node** | 7 independent servers, ~7× aggregate throughput, no RPC overhead |
| 98 GB–free disk | shard across nodes | RPC overhead, 1/S utilisation |
| > free disk | shard; coordinator needs a bigger disk | Model B today (F16) |

**The ≤98 GB threshold is the single most consequential number in selection**,
because crossing it costs ~7× throughput (see `DESIGN-NOTES.md` section C).
A model at 95 GB and one at 105 GB are not 10% apart in practice — they are
almost an order of magnitude apart.

## Criterion 3 — Reject "thinking" variants unless the budget is explicit

Reasoning models emit chain-of-thought into `reasoning_content`, which is
**discarded**. At ~90 ms per token on this hardware, a 2,000-token reasoning
trace costs **3 minutes per chunk** and produces nothing that reaches the user.
Across 14 chunks that is 42 wasted minutes per document.

Worse, they fail unsafely: if `max_tokens` runs out mid-thought the model
returns **empty `content`** with HTTP 200 (F21) — observed directly on Qwen3-4B.

**For summarisation, prefer non-thinking variants, or disable thinking**
(`/no_think`, `--chat-template-kwargs '{"enable_thinking":false}'`). Reserve
reasoning models for multi-step Q&A where the reasoning genuinely improves the
answer, and budget `max_tokens` accordingly.

## Criterion 4 — Faithfulness over style

These are legally sensitive documents; a fabricated fact is a serious failure,
while clumsy prose is not. Selection should weight hallucination/faithfulness
benchmarks above general chat leaderboards, and the evaluation harness (Task 14)
must **score factual consistency separately from the SummEval rubric** rather
than blending them.

## Criterion 5 — A tokenizer-compatible small sibling, if speculative decoding is wanted

Speculative decoding needs a draft model sharing the target's tokenizer and
vocabulary (`-md`). Model families that ship a 0.5–2B variant alongside the
large one are worth preferring for this reason alone. See `DESIGN-NOTES.md`
section B — the expected gain is modest (~10% end-to-end, since prefill
dominates) but it is nearly free when a sibling exists.

## Criterion 6 — Licence must permit the actual use

The whole premise is organisations with statutory constraints. A licence with
usage restrictions, or one requiring data to be shared upstream, disqualifies a
model regardless of benchmark scores. Check the model card, not the family
reputation.

---

## Recommended working set (slots, not yet names)

| Slot | Size target | Active | Topology | Purpose |
|---|---|---|---|---|
| **Triage** | ≤5 GB | any | resident alongside others | routing, extraction, tests |
| **Workhorse** | ≤98 GB | ≤6 B | **replicated ×7** | the summarisation fleet — the workhorse of the whole system |
| **Frontier** | any | ≤32 B | sharded ×7 | the thesis: what no single machine could hold |
| **Draft** (optional) | ≤2 GB | — | with the workhorse | speculative decoding |

Disk cost per node for Triage + Workhorse + Draft is well under 200 GB of the
**368 GB currently free**, leaving room to hold two candidate workhorses and
A/B them without re-downloading. **Re-check `df -h /` before each fetch** — the
budget moves every time a model lands.

## Benchmark research (2026-08-17) — the shortlist, with faithfulness first

Hallucination rates are **REPORTED** from the Vectara leaderboard (7,700+
news/legal/medical/finance articles, updated 2026-05-11). See F25 for caveats.

| Model | Halluc. | Total | Active | tok/s | Disk @IQ4 | Licence | Slot |
|---|---:|---:|---:|---:|---:|---|---|
| **GLM-4.6** | **9.5%** | 357B | 32B | 1.02 | **189 GB** | MIT | **frontier — leading candidate** |
| **DeepSeek-V3.2** | **5.3–6.3%** | 685B | 37B | 0.88 | 363 GB | MIT | frontier — most faithful |
| Kimi K2-Instruct | **17.9% worst** | 1.03T | 32B | 1.02 | 546 GB | custom | **reconsider** |
| **GLM-4.5-Air** | **9.3%** | 110B | 12B | 2.72 | **58 GB** | MIT | **workhorse — faithful** |
| gpt-oss-120b | 14.2% | 117B | 5.1B | **6.40** | 61 GB | Apache | workhorse — fastest |
| Qwen3-Next-80B-A3B | unknown | 81B | **3B** | **10.9** | 93 GB (Q8) | Apache | workhorse — cheapest/token |
| Kimi K3 | — | 2.78T | 104B | ~0.3 | 1.5 TB | custom | **out of scope** (F26) |

### The workhorse trade-off is now explicit

| | gpt-oss-120b | GLM-4.5-Air |
|---|---:|---:|
| Speed | **6.40 tok/s** | 2.72 tok/s |
| Hallucination | 14.2% | **9.3%** |
| Licence | Apache-2.0 | MIT |

**2.4× slower for ~1.5× more faithful.** For legally sensitive documents run
overnight — where nobody is waiting and a fabricated fact is the failure that
matters — that trade looks worth taking. **Both fit on one node, so both can be
replicated ×7 and A/B'd on real documents.** Hold both on disk (119 GB of 431)
and let Task 14 decide on our own corpus rather than on a public leaderboard.

### Open gaps from the research

- **GLM-5 / 5.1 / 5.2** (753B, MIT, GPQA 91.2 — the strongest open reasoner)
  publishes **no active-parameter count anywhere found.** That single number
  decides whether it is usable here. Worth a dedicated check.
- **Finix S1 32B** has the best listed hallucination rate (**1.8%**) but was not
  characterised — architecture, active params and GGUF availability all unknown.
  If it is MoE and small, it could be ideal.
- **No public summarisation-specific open-vs-closed leaderboard exists.** The
  gap is demonstrated to have narrowed on *coding* benchmarks, **not** on
  faithful summarisation. On the one faithfulness comparison that does exist,
  closed models lead by ~4–7× (Gemini-2.0-Flash 0.7%, GPT-4o 1.5% against the
  best open model found at 5.3%). **Do not claim the gap has closed for our
  workload** — producing that comparison is precisely the contribution Task 14
  was scoped to make.

---

## Candidates already on disk or measured

| Model | Total | Active | tok/s | Fits one node? | Notes |
|---|---:|---:|---:|---|---|
| Qwen3-4B Q4_K_M | 2.4 GB | 4.0 B dense | 11.49 measured | yes | reasoning model; triage/test only |
| gpt-oss-120b MXFP4 | 61 GB | 5.1 B | **6.05 measured** | **yes** | strong workhorse candidate |
| Qwen3-Next-80B-A3B Q8 | 93 GB | 3.0 B | ~5.4 predicted | **yes, barely** | downloading; 3B active is excellent |
| Kimi K2 IQ4_XS | 547 GB | 32 B | ~1.08 predicted | no — shard | frontier slot; needs coordinator disk |

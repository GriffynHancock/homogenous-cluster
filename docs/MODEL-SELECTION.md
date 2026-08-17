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
| **Disk** | 431 GB free (477 total) | how many models you can KEEP |
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
| 98–431 GB | shard across nodes, fits every node's disk | RPC overhead, 1/7 utilisation |
| > 431 GB | shard; coordinator needs a bigger disk | Model B today (F16) |

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
431 available, leaving room to hold two candidate workhorses and A/B them
without re-downloading.

**Selection is currently blocked on benchmark research** covering summarisation
faithfulness, long-context performance, and open-vs-closed comparison. Criteria
above are settled; names are not.

---

## Candidates already on disk or measured

| Model | Total | Active | tok/s | Fits one node? | Notes |
|---|---:|---:|---:|---|---|
| Qwen3-4B Q4_K_M | 2.4 GB | 4.0 B dense | 11.49 measured | yes | reasoning model; triage/test only |
| gpt-oss-120b MXFP4 | 61 GB | 5.1 B | **6.05 measured** | **yes** | strong workhorse candidate |
| Qwen3-Next-80B-A3B Q8 | 93 GB | 3.0 B | ~5.4 predicted | **yes, barely** | downloading; 3B active is excellent |
| Kimi K2 IQ4_XS | 547 GB | 32 B | ~1.08 predicted | no — shard | frontier slot; needs coordinator disk |

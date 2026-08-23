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
| Qwen3-Next-80B-A3B | **9.3%†** | 81B | **3B** | **10.9** | 93 GB (Q8) | Apache | workhorse — cheapest/token |
| Kimi K3 | — | 2.78T | 104B | ~0.3 | 1.5 TB | custom | **out of scope** (F26) |

† **Added 2026-08-23.** `qwen/qwen3-next-80b-a3b-thinking` is listed at **9.3 %**
(REPORTED, Vectara, 2026-05-11) — the cell was "unknown" only because the earlier
search looked for the Instruct name. **We hold Instruct, not Thinking**, so the
number is suggestive and not transferable; Criterion 3 also says do not run the
thinking variant. Two other rows were added to the leaderboard since this table
was written: `zai-org/GLM-4.7-flash` at **9.3 %** and `zai-org/glm-5` at **10.1 %**.

**The `tok/s` column of this table is stale and internally inconsistent** — it
predates F24's 61 % sparse-MoE efficiency and disagrees with the "Candidates
already on disk" table below (10.9 here vs "~5.4 predicted" there for the same
model). Use the re-priced table further down, which is built from real file sizes
and reproduces the one measured point.

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

- ~~**GLM-5 / 5.1 / 5.2** publishes no active-parameter count anywhere found~~ —
  **CLOSED 2026-08-23: ~40.8 B active**, computed from `config.json` and validated
  against the published total. It is **more** active than DeepSeek-V3.2 and
  **less** faithful than GLM-4.6. See "The two research gaps, CLOSED" below.
- ~~**Finix S1 32B** has the best listed hallucination rate (**1.8%**)~~ —
  **CLOSED 2026-08-23: there are no weights.** `antgroup/finix_s1_32b` is HTTP 401
  on Hugging Face and the Finix line is API-only. See below.
- **No public summarisation-specific open-vs-closed leaderboard exists.** The
  gap is demonstrated to have narrowed on *coding* benchmarks, **not** on
  faithful summarisation. On the one faithfulness comparison that does exist,
  closed models lead by ~4–7× (Gemini-2.0-Flash 0.7%, GPT-4o 1.5% against the
  best open model found at 5.3%). **Do not claim the gap has closed for our
  workload** — producing that comparison is precisely the contribution Task 14
  was scoped to make.


---

## The two research gaps, CLOSED 2026-08-23 — and both close *against* the frontier model

`STATUS.md` §3 named two unknowns that could have changed the Model B answer.
Both are now resolved from primary sources, and neither rescues a large model.

### GAP 1 — GLM-5 / 5.1 / 5.2 active parameters: **~40.8 B. CONFIRMED by arithmetic.**

**The count is still published nowhere.** Not on the model cards
([GLM-5](https://huggingface.co/zai-org/GLM-5),
[GLM-5.1](https://huggingface.co/zai-org/GLM-5.1),
[GLM-5.2](https://huggingface.co/zai-org/GLM-5.2)), not in the GLM-5 technical
report abstract ([arXiv:2602.15763](https://arxiv.org/abs/2602.15763)), not in
the GGUF quantisers' cards. So it was computed from `config.json`, which all
three share.

**CONFIRMED inputs** (`https://huggingface.co/zai-org/GLM-5.2/raw/main/config.json`,
identical in the relevant fields for GLM-5 and GLM-5.1):

```
hidden_size 6144   num_hidden_layers 78   first_k_dense_replace 3
intermediate_size 12288      moe_intermediate_size 2048
n_routed_experts 256   num_experts_per_tok 8   n_shared_experts 1
num_attention_heads 64   q_lora_rank 2048   kv_lora_rank 512
qk_nope_head_dim 192   qk_rope_head_dim 64   v_head_dim 256
index_n_heads 32   index_head_dim 128   vocab_size 154880
num_nextn_predict_layers 1        architectures: GlmMoeDsaForCausalLM
```

**The arithmetic**, per layer:

| Component | Params | In the active set? |
|---|---:|---|
| MLA attention (`q_a`+`q_b`+`kv_a`+`kv_b`+`o_proj`) | 165.0 M | yes, all 78 layers |
| DSA lightning indexer (q from `q_lora`) | 9.4 M | yes, all 78 layers |
| Dense FFN, layers 0–2 | 226.5 M | yes, 3 layers |
| MoE block, all 257 experts + gate | 9,703.0 M | **no** |
| MoE block, 8 routed + 1 shared + gate | 341.3 M | yes, 75 layers |

```
active = 78 × (165.0 + 9.4) M            = 13.60 B   attention + indexer
       +  3 × 226.5 M                    =  0.68 B   dense FFN
       + 75 × 341.3 M                    = 25.60 B   9 of 257 experts
       +  1 × (154880 × 6144)            =  0.95 B   lm_head (read every token)
                                           -------
                                           40.83 B
```

**Why this is CONFIRMED and not INFERRED:** the same per-tensor model, run over
*all* 257 experts plus the MTP block, reconstructs **753.86 B total — the
published `safetensors` count to 0.00%**
(`https://huggingface.co/api/models/zai-org/GLM-5` reports
`{"BF16": 753864119552}`; GLM-5.2 reports 753.33 B). A reconstruction that
lands on the published total to five significant figures is not a guess about
the layer inventory. The one free choice — whether the indexer's query
projection reads `hidden_size` or `q_lora_rank` — is what closes the last 1.3 B,
and the `q_lora_rank` variant is the one that matches exactly.

**So GLM-5.x is ~40.8 B active: MORE than DeepSeek-V3.2's 37 B, and ~8× gpt-oss-120b's 5.1 B.**
Criterion 1's table already rejects 37 B+ at Q4. GLM-5 is past that line.

**Three further facts, each independently disqualifying:**

1. **Faithfulness is WORSE than GLM-4.6, not better.** `zai-org/glm-5` is on the
   Vectara leaderboard at **10.1 %** against GLM-4.6's **9.5 %** (REPORTED,
   `https://github.com/vectara/hallucination-leaderboard`, updated 2026-05-11).
   GLM-5.1 and GLM-5.2 have no entry. The strongest open reasoner is not the
   most faithful one, and this project ranks faithfulness first.
2. **It is a reasoning model with "thinking effort levels"** (model card). Criterion 3
   applies at full force: the chain-of-thought is discarded and, at ~40.8 B active,
   every discarded token is the most expensive token this fleet can emit.
3. **It does not fit the coordinator's disk.** GLM-5 IQ4_XS is **402.9 GB** real,
   against **368 GB free** (F16). Same blocker as Kimi K2, worse.

**Software support is, for once, not the problem.** GLM MoE DSA landed in
llama.cpp via [PR #19460](https://github.com/ggml-org/llama.cpp/pull/19460)
(merged 2026-02-13, indexer tensors loaded but *unused*), and the indexer itself
via [PR #25407](https://github.com/ggml-org/llama.cpp/pull/25407) (merged
2026-07-24). Our pin **b10369 is commit `6e62ba5`, dated 2026-08-11**, so both
are in it — CONFIRMED from the GitHub API, **not verified against the local
binary.** Support is not the reason to say no; 40.8 B active is.

### GAP 2 — Finix S1 32B: **not obtainable. There are no weights.**

- **CONFIRMED:** `https://huggingface.co/antgroup/finix_s1_32b` returns **HTTP 401**.
  It does not appear in Hugging Face model search, and the public `antgroup` org
  holds exactly two models, neither of them Finix
  (`https://huggingface.co/api/models?author=antgroup`).
- **REPORTED:** Ant Group's Finix line is API-only and proprietary — the
  catalogue entry for its sibling Finix-P1-32B lists availability as
  "proprietary", licence "unknown"
  ([genailist.net](https://genailist.net/model/ant-group/finix-p1-32b)). Finix-S1
  is described as a **domain-specific model fine-tuned on insurance business
  data for compliance and claims.**
- **Therefore: no GGUF, no licence we could rely on, no architecture disclosed,
  and nothing to download.** Criterion 6 disposes of it before Criterion 1 gets a
  turn. **Close it; do not re-open.**

**And the 1.8 % deserved the scepticism it was given.** Three things about that
number, all from the leaderboard's own README:

1. **Its summaries are the outlier, not just its score.** Finix S1's average
   summary is **172.4 words** against a **median of 106.9** across all 105 listed
   models — 1.6× the median, and sixth-longest on the whole board.
2. **The leaderboard states in its own FAQ that a copy-paste extractive
   summariser would score 0 % hallucination**, and that it is "**not** evaluating
   the quality of the summaries, only the factual consistency". Long, highly
   extractive output is exactly the shape that scores well here and is exactly
   the shape that is *useless* as a summary.
3. **HHEM is Vectara's own model scoring everyone else's.** A vendor whose model
   sits at the top of a single-vendor metric, with no weights to check, is a
   claim, not a measurement.

None of that proves Finix S1 is bad. It means **1.8 % is not evidence strong
enough to move a decision**, and in any case there is nothing to move it *to*.

---

## Re-pricing every candidate against fleet size — REAL file sizes, N=2 and N=7

`S = ceil(size / 98.9 GB)`; `R = floor(N / S)`; aggregate throughput ≈ R × per-copy
throughput. Sizes are **actual GGUF bytes summed from the Hugging Face file tree**,
per the UD-quant capacity trap in `DESIGN-NOTES.md` H — never a nominal quant label.

Predicted tok/s = 17.3 GB/s (F24 sparse-MoE effective) ÷ (active × bpw ÷ 8),
where bpw = file bytes × 8 ÷ total params. **INFERRED, not measured.** The method
reproduces the one point that *is* measured — it predicts **6.06 tok/s** for
gpt-oss-120b against **6.05 measured** (`docs/measurements.md`) — which is why
the other rows are worth reading, but they remain predictions.

| Model / quant | Real GB | Active | Halluc. | Pred. tok/s | **S** | **R@N=2** | **R@N=7** | Agg. @N=7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **gpt-oss-120b** F16/MXFP4 | **65.4** | 5.1 B | 14.2 % | **6.05 meas.** | **1** | **2** | **7** | **42.4** |
| **Qwen3-Next-80B-A3B** UD-Q8_K_XL | **93.1** | 3.0 B | 9.3 %† | 4.96 | **1** | **2** | **7** | **34.7** |
| **GLM-4.5-Air** IQ4_XS | **60.5** | 12 B | 9.3 % | 2.62 | **1** | **2** | **7** | 18.3 |
| Llama-4-Scout Q4_K_M | 65.4 | 17 B | 7.7 % | 1.70 | **1** | **2** | **7** | 11.9 |
| GLM-4.6 IQ4_XS | 190.7 | 32 B | 9.5 % | 1.01 | 2 | **1** | 3 | 3.0 |
| GLM-4.6 UD-IQ1_S (1-bit) | 96.9 | 32 B | 9.5 %‡ | 1.99 | **1** | 2 | 7 | 13.9 |
| DeepSeek-V3.2 IQ4_XS | 358.3 | 37 B | 6.3 % | 0.89 | 4 | **0** | 1 | 0.9 |
| Kimi K2-Instruct IQ4_XS | 546.5 | 32 B | 17.9 % | 1.01 | 6 | **0** | 1 | 1.0 |
| **GLM-5 IQ4_XS** | **402.9** | **40.8 B** | 10.1 % | **0.79** | **5** | **0** | **1** | **0.8** |
| GLM-5 UD-Q2_K_XL | 281.4 | 40.8 B | 10.1 %‡ | 1.14 | 3 | 0 | 2 | 2.3 |
| GLM-5 UD-TQ1_0 (~1.9 bpw) | 176.1 | 40.8 B | 10.1 %‡ | 1.82 | 2 | 1 | 3 | 5.5 |
| GLM-5.2 UD-IQ4_XS | 365.3 | 40.8 B | no entry | 0.87 | 4 | **0** | 1 | 0.9 |
| GLM-5.2 UD-IQ1_S (1-bit) | 216.7 | 40.8 B | no entry | 1.47 | 3 | 0 | 2 | 2.9 |

† `qwen/qwen3-next-80b-a3b-thinking`, i.e. the **thinking** variant; we hold the
**Instruct** variant, so the number is suggestive, not transferable. ‡ the
leaderboard entry is for the full-precision API model; a 1–2 bit quant of it is a
different artefact and its faithfulness is **unknown**.

Aggregate at N=7 assumes S=1 rows replicate freely and S>1 rows pay 1/S
utilisation with no RPC penalty — **generous to the sharded rows**, since RPC
measured −39 % prefill / −5 % generation and prefill is ~79 % of document
wall-clock.

**Read the last three columns, not the faithfulness column.** At N=2 every
candidate below 98.9 GB gives R=2 and everything else gives R≤1. At N=7 the S=1
tier delivers 12–47× the aggregate tokens of the GLM-5 tier. **No plausible
faithfulness gain is worth 40×**, and GLM-5 does not even offer a gain — it is
0.6 points *worse* than GLM-4.6 on the only faithfulness number either has.

**The download cost is the second veto.** At the measured 93.8 Mbit/s (F28,
≈11 MB/s) a copy costs **1.5 h for gpt-oss-120b, 4.5 h for GLM-4.6 IQ4_XS, and
9.5 h for GLM-5 IQ4_XS — per node.** Under replication every node needs its own
copy, over one shared 100 Mb switch.

### The conclusion for Model B

**Model B, as originally conceived — one frontier model too large for any single
machine — cannot be justified on this fleet at N=2 and is not obviously justified
at N=7 either.** Every frontier candidate is now priced, and each is dominated:

- **GLM-5 / 5.1 / 5.2 — REJECT.** 40.8 B active (worse than DeepSeek-V3.2),
  10.1 % hallucination (worse than GLM-4.6), a reasoning model whose discarded
  thinking is the most expensive output this hardware can produce, 402.9 GB
  against 368 GB of free disk, and S=5.
- **Finix S1 32B — REJECT.** No weights exist to run.
- **Kimi K2 — REJECT**, unchanged from F25: worst faithfulness of any model
  checked, largest file, custom licence.
- **DeepSeek-V3.2 — the only frontier model still standing on merit** (6.3 %,
  MIT), and it does not run at N=2 at all.
- **GLM-4.6 — the frontier candidate that becomes viable first**, at N≥4 for R=2
  and N≥6 for R=3.

**So the answer to "which Model B" is currently "none, and buy replication
instead."** That is a real answer, not a deferral: it is the "largest model with
S=1" rule in `CLAUDE.md` selecting for the fleet that exists.

**The one experiment that would change it** is not a frontier model at all —
it is whether a **1-bit UD quant of GLM-4.6 (UD-IQ1_S, 96.9 GB, S=1)** keeps
enough of the 9.5 % faithfulness to beat gpt-oss-120b's 14.2 % while staying
replicable. `DESIGN-NOTES.md` H already warns that UD's advantage shrinks at the
extreme low-bit end, so this is a genuine open question and a cheap one: 2.3 h to
fetch, and it either works on our own corpus or it does not.

### Flagged, not chased: a candidate this re-pricing turned up

**`zai-org/GLM-4.7-Flash` looks like it dominates gpt-oss-120b on every axis this
project ranks, and it was not on the shortlist.** Reported and computed, not
measured here:

| | gpt-oss-120b (incumbent) | **GLM-4.7-Flash** |
|---|---:|---:|
| Total params | 116.8 B | **31.2 B** (CONFIRMED, HF `safetensors`) |
| Active params | 5.1 B | **~3.6 B** (computed from `config.json`; reconstruction lands within 2 % of the published total) |
| Hallucination | 14.2 % | **9.3 %** (REPORTED, Vectara) |
| Licence | Apache-2.0 | **MIT** |
| Real GGUF (Q4_K_M) | 65.4 GB | **18.3 GB** |
| S / R@N=2 / R@N=7 | 1 / 2 / 7 | **1 / 2 / 7** |
| Predicted tok/s | 6.05 measured | **~8.2 (INFERRED)** |
| Fetch time @11 MB/s | 1.5 h | **0.5 h** |

Architecture is `Glm4MoeLiteForCausalLM` — 47 layers, 64 routed experts, top-4 plus
1 shared, MLA (`q_lora_rank` 768, `kv_lora_rank` 512), `hidden_size` 2048. llama.cpp
support landed in [PR #18936](https://github.com/ggml-org/llama.cpp/pull/18936),
before our pin.

**Caveats that stop this being a recommendation:** it is a *hybrid reasoning*
model (Criterion 3), 31 B total is far less stored knowledge than 117 B, and every
number above except the file sizes and the config is reported or inferred. But at
**18.3 GB and half an hour of link time** it is the cheapest A/B on the board, and
the shortlist should not be closed without it.

---

## Candidates already on disk or measured

| Model | Total | Active | tok/s | Fits one node? | Notes |
|---|---:|---:|---:|---|---|
| Qwen3-4B Q4_K_M | 2.4 GB | 4.0 B dense | 11.49 measured | yes | reasoning model; triage/test only |
| gpt-oss-120b MXFP4 | 61 GB | 5.1 B | **6.05 measured** | **yes** | strong workhorse candidate |
| Qwen3-Next-80B-A3B Q8 | 93 GB | 3.0 B | ~5.4 predicted | **yes, barely** | downloading; 3B active is excellent |
| Kimi K2 IQ4_XS | 547 GB | 32 B | ~1.08 predicted | no — shard | frontier slot; needs coordinator disk |

---

## Meta's open weights, researched 2026-08-17 — none of it beats gpt-oss here

**The verdict is arithmetic, not taste: every Meta option has 3–5× more ACTIVE
parameters than gpt-oss-120b, and active params set speed.**

| Model | Total / **active** | Size at usable quant | S @ 98.9 GB | Predicted tok/s | Vectara halluc. | Verdict |
|---|---|---:|---:|---:|---:|---|
| **gpt-oss-120b** (incumbent) | 117B / **5.1B** | 65 GB | **1** | **6.05 measured** | 14.2% | the baseline to beat |
| **Llama 4 Scout** | 109B / 17B, MoE top-1/16 | Q4_K_M **65.4 GB** | **1** | **~1.7** (INFERRED) | **7.7%** | best-evidenced Meta option; **~3.5× slower** |
| Llama 4 Maverick | 402B / 17B, MoE top-1/128 | 1.78-bit **122 GB** | **2** | — | 8.2% | **reject at N=2** — S=2 means R=1 |
| **Muse Glimmer-30B** (2026-08-10) | ~30B / **27.8B dense** | Q4_K_M **17.3 GB** | **1** | **~1.7** (INFERRED) | **no entry** | Apache-2.0, fits easily, but slow and faithfulness unproven |
| Muse Spark 1.x | undisclosed | **no weights** | — | — | — | **API-only — unusable here** |
| Llama 4 Behemoth | ~2T / 288B | never released | — | — | — | not obtainable |

**Key points:**

- **Llama 4 Scout is the one worth benching** if faithfulness is the priority: 7.7%
  reported vs gpt-oss's 14.2%, and it fits S=1. **But it costs ~3.5× throughput**,
  so it is a genuine trade rather than a free win.
- **Muse Glimmer is dense**, so all 27.8B params are read every token — the opposite
  of what this hardware wants. It has **no Vectara entry** (nine days old at time of
  research), and the one figure that exists (82% on AA-Omniscience, a *different*
  construct — open-book recall with abstention, not grounded summarisation) is poor
  on its own terms. **Do not adopt on the strength of any number here.**
- Note the two metrics **disagree sharply** — Maverick scores 8.2% on Vectara and
  87.6% on AA-Omniscience. That is itself the finding: they measure different failure
  modes and neither substitutes for the other.
- **Llama 4 top-1 routing** touches fewer experts per token than gpt-oss's top-4, so
  it *might* exceed the measured 61% sparse-MoE efficiency (F24) — **INFERRED, and
  unvalidated on this hardware.** Treat Scout/Maverick predictions as lower
  confidence than the validated gpt-oss/Qwen3 points.

---

## The agent-appliance model, researched 2026-08-17

The appliance needs to triage the queue, health-check endpoints, assemble batches and
report — **reliable tool calling matters more than capability**, since it issues real
operations. It runs off-cluster on modest hardware (see `REQUIREMENTS.md`).

BFCL scores are **version-specific and not comparable across v2/v3/v4** — a Qwen3-4B
figure of 61.9 (v3, own model card) appears elsewhere as 33.04 (v4). Below is BFCL-v3
from the Qwen3 technical report (arXiv:2505.09388), non-thinking / thinking:

| Model | BFCL-v3 | Predicted tok/s here | Note |
|---|---:|---:|---|
| **Qwen3-4B (ON DISK)** | **57.6** / 65.9 | **~11.5 measured** | zero download; **cheapest viable answer** |
| Qwen3-4B-Instruct-2507 | **61.9** | ~11.3 | beats Qwen3-14B non-thinking at 4B; small re-fetch |
| Qwen3-8B | 60.2 / 68.1 | ~5.6 | |
| Qwen3-14B | 61.5 / 70.4 | ~3.2 | |
| gpt-oss-20b | not determined | ~9.0 | harmony tool-calling needs its own verification |

**RECOMMENDATION: the Qwen3-4B already on disk is good enough, with thinking forced
off.** F35 makes that reliable (`enable_thinking: false` is verified on this family).
Run non-thinking: the +8–12 points from thinking cost latency directly, and this is
the one workload here where latency matters.

**Reliability lever worth knowing:** llama.cpp's `--jinja` machinery
**grammar-constrains tool-call JSON server-side**, so malformed arguments are largely
solved for any candidate. What it cannot fix is choosing the *wrong* tool — a
semantic failure no schema catches. So model quality still matters, just less than
capability leaderboards imply. **INFERRED** from how the grammar system works; no
controlled ablation was found.

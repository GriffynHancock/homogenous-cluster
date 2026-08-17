# Design notes: two proposed speedups

Analysis of two ideas raised 2026-08-17. Both are sound in principle; they differ
enormously in cost. Numbers use node 1's measured 28.2 GB/s and Kimi K2 IQ4_XS
(32B active, ~16 GB read per token).

---

## A. Expert parallelism — shard by expert, not by layer

**The idea:** instead of giving each node a contiguous range of layers, spread
each layer's *experts* across nodes. A router sends each token to the nodes
holding its selected experts, which compute **in parallel**.

### The insight is correct, and it identifies the single biggest waste

Today the cluster is **pipeline (layer) parallel**. For one token:

- node 1 reads the active experts of its layers, then node 2, then node 3 …
- **only one node's memory bus is in use at any instant**
- utilisation is 1/7, exactly as the spec says ("pooling buys capacity, not speed")

| | Value |
|---|---:|
| Fleet aggregate bandwidth (7 × 28.2) | **197 GB/s** |
| Actually used by layer-parallel | **28.2 GB/s (14%)** |
| Kimi K2 bytes/token | 16 GB |
| **Layer-parallel** | **1.76 tok/s** |
| **Expert-parallel ceiling** (perfect balance) | **~12.3 tok/s** |
| **Expert-parallel realistic** (see imbalance below) | **~5–7 tok/s** |

**That is a 3–4× speedup, and it comes from the one resource the current design
leaves idle: six other memory buses.**

Load imbalance sets the realistic figure. With `top_k = 8` spread over 7 nodes,
some node draws 2 of the 8 active experts, so it does 2/8 of that layer's read
while others do 1/8 and wait. Speedup is bounded by the busiest node — roughly
4×, not 7×.

### Communication would *not* kill it — which is the surprising part

Kimi K2 has 61 layers. Expert parallelism needs a scatter and a gather per layer:
**122 round-trips per token**. That sounds fatal on gigabit, but at batch 1 the
payload is one hidden state — 7168 dims × 2 bytes ≈ **14 KB**. This is
**latency-bound, not bandwidth-bound**:

```
122 round-trips × ~0.3 ms gigabit TCP RTT ≈ 37 ms/token → ~27 tok/s ceiling
```

**27 tok/s (comms ceiling) is comfortably above 5–7 tok/s (bandwidth target),**
so communication would not be the binding constraint. This is materially
different from the tensor-parallel all-reduce that got `distributed-llama`
rejected in the spec — that moves full activations every layer; this moves one
token's hidden state.

### Why we are still not doing it

**llama.cpp has no expert parallelism, and it is not a flag.** RPC is
layer-split only. `-ot` / `--override-tensor`, `-cmoe` / `--cpu-moe` and
`-ncmoe` control CPU-vs-GPU placement *within one machine* — they do not shard
experts across hosts.

Building this means writing a distributed MoE inference engine: custom router,
all-to-all collective, expert placement and rebalancing, plus fault handling.
That is a multi-month research project, it abandons the GGUF/Open WebUI stack,
and it directly contradicts this project's premise — *use what exists, measure
it, package the measurements as a skill.*

**Verdict: correct, valuable, and out of scope.** Worth recording as the
strongest known argument for a future engine, and worth watching upstream: if
llama.cpp ever gains expert parallelism, this cluster gets ~4× for free. It is
also the sharpest statement of what the current architecture gives up —
**"7 nodes ≈ 1 node with 7× the RAM" is a consequence of layer-splitting, not a
law of distributed inference.**

---

## B. Speculative decoding — a draft model predicting the target

**This is speculative decoding, and llama.cpp b10369 supports it today:**
`-md` / `--spec-draft-model`, `--spec-draft-n-max`, `--spec-draft-p-min`,
`-devd` / `--spec-draft-device`, plus separate thread and CPU-affinity controls
for the draft (`-td`, `-Crd`).

### Why it *should* fit this hardware perfectly

A draft model proposes K tokens; the target verifies all K in **one** forward
pass. Because that pass reads the weights **once**, a bandwidth-bound target
gets K tokens for roughly the price of 1. We measured generation at **~99% of
STREAM** — textbook conditions for this to pay off.

### Two reasons it will pay off less than it looks

**1. On a sparse MoE, verification is not free — this is the project's existing
open question wearing a different hat.** Verifying K tokens means K tokens each
routing to their own experts, touching ≈ `min(K × top_k, n_experts)` experts. So
bytes read grow with K, and the "free verification" assumption weakens exactly
as `STATUS.md` already predicts for batching. **Speculative decoding *is*
batching.** Partial mitigations are real — attention and shared-expert weights
are reused across the K tokens, and popular experts overlap — so expect *some*
gain, just well short of dense-model behaviour.

**Consequence: run `llama-batched-bench -np 1,2,4,8` first.** It answers the
batching question and the speculative-decoding question in one measurement. If
batching is flat, speculation will be too.

**2. It speeds up the half that is already healthy.** For document work the time
is dominated by prefill, which speculative decoding does not touch:

| Stage (50K-token doc, 14 chunks) | Time | Share |
|---|---:|---:|
| Prefill | ~38 min | **79%** |
| Generation | ~10 min | 21% |

A **2× generation speedup cuts total wall-clock by ~10%.** Worth having, not
transformative — and prefill remains the thing to attack.

### The heterogeneous-cluster idea is the best part of this

Putting the draft model on the fastest node is exactly right, and llama.cpp
supports the placement: `-devd` selects draft devices (RPC devices included) and
`-td` / `-Crd` give it its own threads and CPU affinity. A ~0.6B draft is small
enough to sit entirely in one node's RAM while the target shards across all
seven.

**Practical blocker: draft availability.** The draft must share the target's
tokenizer and vocabulary.

| Target | Draft | Viable? |
|---|---|---|
| Qwen3-Next-80B-A3B | Qwen3-0.6B / 1.7B | **yes** — same family and tokenizer |
| gpt-oss-120b | — | no small sibling exists |
| Kimi K2 | — | own tokenizer; no compatible small model |

**So Model B, the one that most needs the speedup, is the one with no draft
model.** Qwen3-Next is the natural place to test the technique.

**Verdict: cheap to test, worth testing on Qwen3-Next, but do
`llama-batched-bench` first** — it is the gating measurement for both, and it
runs single-node with no RPC involved.

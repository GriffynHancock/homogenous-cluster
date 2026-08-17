# Design notes: proposed speedups

---

## C. Replicate the model on every node (data parallelism) — the biggest available win

**The idea:** rather than sharding one model across seven nodes, put a **full
copy on each node** and run seven independent servers.

**This is only possible for models that fit on one node** — gpt-oss-120b is
61 GB against 125 GB of RAM, so it fits with 56 GB to spare. Kimi K2 at 547 GB
cannot, and must still be sharded. So this is not a replacement for Model B; it
is a third configuration.

### Why it wins so heavily on *this* workload

The spec says *"pooling buys capacity, not speed — 7 nodes ≈ 1 node with 7× the
RAM."* **That is true for one request, and the project's actual workload is not
one request.** Map-reduce over a 50K-token document is ~14 **independent** chunk
summaries — embarrassingly parallel. What matters is aggregate throughput, not
single-stream latency.

| | Sharded (Kimi K2 × 7) | **Replicated (gpt-oss on each)** |
|---|---|---|
| Per-node footprint | 78 GB share | full 61 GB copy |
| Generation | 1.08 tok/s total | 6.05 × 7 = **42 tok/s aggregate** |
| Prefill | ~2.3 tok/s total | 15.88 × 7 = **111 tok/s aggregate** |
| Concurrent seats | 1 | **7** |
| RPC overhead | −39.4% prefill, −5.2% gen | **none** |
| Node utilisation | 1/7 (sequential) | **7/7** |
| **50K-token document** | **~80 min** | **~12 min** |

**~7× on the workload that actually matters**, with no new hardware and no RPC
overhead at all — each node runs standalone at full speed.

### What it costs

Model quality. Kimi K2 is a 1T-parameter frontier-class model; gpt-oss-120b is
not. The real choice is:

- **one frontier model, slowly, one job at a time**, versus
- **seven good models, fast, in parallel.**

For overnight document processing at volume, replication looks clearly better.
For work that genuinely needs frontier reasoning, it does not.

### Recommendation

Add **"Model A replicated ×7"** as a third measured configuration rather than
replacing Model B. The A-vs-B comparison is the stated deliverable, but
A-replicated is plausibly the most *useful* configuration, and it does not
weaken the thesis — 7 × gpt-oss-120b is still work the organisation could not
otherwise do on its own hardware.

**It also simplifies operations enormously:** no RPC, no `--tensor-split`, no
version-lockstep across nodes, no exposure to upstream bug #26500 (F2), and a
node failure costs 1/7 of throughput instead of taking the whole cluster down.
Missing Link's queue would simply fan jobs out to seven independent endpoints —
a much smaller change than it sounds, since the worker already isolates the
task profile from the queue.

**Caveat to measure:** each node needs its own copy on disk (61 GB × 7). At
21 MB/s from HuggingFace that is prohibitive; `cluster/models.sh pull` over LAN
makes it ~10 min per node.

---

## Prefill: what is left after measurement

Prefill is 79% of document wall-clock, so it is the right thing to attack.
Most of the obvious levers are already eliminated **by measurement**:

| Lever | Status |
|---|---|
| `-t` thread count | **Saturated at 4 physical cores** (F10) |
| `-ub` ubatch size | **No effect** — 27.18 / 26.60 / 27.61 at 512/1024/2048 (F18) |
| Batching prefill | Dense CPU data shows prefill flat across batch sizes |
| BIOS / uncore / power | **Already at maximum** — uncore 2800/2800, EPB 0 (F12) |
| GPU offload | Quadro P600 has 2 GB; cannot hold meaningful layers |
| More RAM channels | **Already 4/4 populated at rated speed** (F12) |

What genuinely remains:

1. **Data parallelism** (section C) — ~7×, by far the largest.
2. **`-fa` flash attention** — untested on this build; may reduce attention
   memory traffic.
3. **KV cache quantisation** (`-ctk q8_0`) — helps at long context.
4. **`ik_llama.cpp`** — optimised CPU kernels, particularly for MoE and
   quantised types. Prefill is compute-bound, which is exactly what it targets.
   Still the open A/B in `STATUS.md`, and now the only remaining *software*
   lever with real upside.
5. **Smaller chunks** — prefill cost is roughly linear in tokens, so this does
   not reduce total work, but it does improve parallel granularity across a
   replicated fleet.

---

## Two proposed speedups (analysed 2026-08-17)

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

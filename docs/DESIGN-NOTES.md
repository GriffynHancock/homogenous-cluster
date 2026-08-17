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

#### CAVEAT ADDED 2026-08-17: that conclusion is GIGABIT-DEPENDENT, and this fleet is not on gigabit

**The network was measured on 2026-08-17 and it is 100 Mb/s, not gigabit**
(93.8 Mbit/s = 11.7 MB/s; see `docs/measurements.md`). Both NICs support
1000baseT/Full, so the cap is the switch, but the analysis above assumed a link
the cluster does not currently have. Re-running it on measured numbers:

| Ceiling | On gigabit (assumed above) | **On the measured 100 Mb link** |
|---|---:|---:|
| Latency-bound (122 RT × RTT) | ~27 tok/s (RTT ~0.3 ms) | **~9.9 tok/s** (RTT **0.827 ms** measured, idle) |
| Bandwidth-bound (122 × 14 KB = 1.71 MB/token) | ~68 tok/s | **~6.8 tok/s** |
| **Binding comms ceiling** | **~27 tok/s** | **~6.8 tok/s** |
| Bandwidth *target* to beat | 5–7 tok/s | 5–7 tok/s |

**On gigabit the comms ceiling sits ~4–5× above the target, so it is ignorable.
On this 100 Mb link it lands directly on top of it.** The headroom that made the
"communication would not kill it" conclusion safe does not exist here.

Two further reasons the 100 Mb figure above is optimistic, not pessimistic:

1. **Fan-out is not counted.** "122 round-trips" counts one scatter and one
   gather per layer, each carrying a single 14 KB hidden state. But with
   `top_k = 8` spread across 7 nodes, a layer's scatter goes to *several* peers
   and gathers *several* partials, so per-layer volume is a multiple of 14 KB.
   The true byte count needs deriving properly before anyone relies on it.
2. **Latency collapses under load.** RTT was measured twice: **0.827 ms on an
   idle link, 9.544 ms while a single rsync saturated it** (min 6.78, max 11.89)
   — an 11.5× degradation from ordinary bufferbloat. A latency-bound design is
   exactly the design that cannot tolerate this, and a real cluster always has
   concurrent traffic (job dispatch, model distribution, monitoring).

**Consequence for the recommendation, and it is a cheap one:** a ~$20–30
gigabit switch is a *precondition* for expert parallelism ever being viable on
this fleet, and it also helps sharding today (measured: two-node sharded
generation is −47% vs single-node, against the −5.2% localhost floor in F14).
It does not change the verdict below — llama.cpp still has no expert
parallelism — but it does mean **"comms is fine" must not be carried forward as
a settled fact.** It is settled only for a network this cluster does not have.

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

---

## D. "Two pipelines": a speed-optimised one and a memory-optimised one

**Raised 2026-08-17.** The proposal: run **two** distinct pipelines — a
*speed-optimised* one (a smaller-but-still-large MoE, with active experts spread
across the cluster) and a *memory-optimised* one (layer sharding, to fit a
gigantic model however slowly) — framed as the classic time/space trade-off.

**The framing is right, and it is already the S/R model in `CLAUDE.md` — but the
axis is not preference, it is whether the model fits one node.** You do not
choose between these two pipelines; the model's size chooses for you.

| Regime | S | R | What wins | Does expert parallelism help? |
|---|---:|---:|---|---|
| Model fits one node | 1 | N | **Replication — linear N×** | **No. It would make things worse.** |
| Model needs the whole fleet | N | 1 | Sharding, capacity only, 1/S utilisation | **Yes — this is the only place it pays (~4×)** |

### The correction that matters: the "speed pipeline" describes two mutually exclusive things

The proposal has a smaller MoE **loaded on each machine** *and* **active experts
spread across the cluster**. Those cannot both hold. If every machine already
has the whole model, there is nothing to distribute — that **is** replication.
Expert parallelism only means anything when each node holds a *subset* of
experts, i.e. when the model does **not** fit one node.

And in the S = 1 regime, expert parallelism is not merely unnecessary, it is
**strictly worse**:

- Replication gives **R × single-node throughput**, linearly, with **zero
  network on the hot path**.
- Expert parallelism gives ~4× on **one request's latency** while adding **122
  round-trips per token** — on a link measured at 100 Mb/s with 0.827 ms idle
  RTT, degrading to 9.5 ms under load.
- **This workload is asynchronous.** `CLAUDE.md`: *"submit overnight, read in the
  morning"*, *"slow is fine; nobody is waiting at a prompt."* The metric is
  documents per night — **throughput**, not per-request latency.

**So expert parallelism optimises the one metric this project explicitly does not
care about, at the cost of building a new inference engine, in the regime where a
free linear win is already available.** That is the sharpest reason to leave it
alone — sharper than the scope argument in A.

### Where the trade-off is real

It is a **threshold, not a smooth curve**. Crossing S = 1 → 2 costs a factor of
N (you go from R = N to R = N/2 *and* pay RPC overhead). That is why `CLAUDE.md`
calls the size at which S goes 1 → 2 the most consequential number in model
selection, and why the correct move is **the largest model with S = 1**, not the
largest model that fits at all.

The genuinely interesting consequence: a *smaller* model that keeps S = 1 can
beat a *larger* one at S = 2 on total useful work, even though the larger one is
more capable per token. Faithfulness (F25) then decides between the S = 1
candidates. This is the real "time vs space" decision in this project, and it is
made at **model selection**, not at pipeline architecture.

### What to do instead, in the S > 1 case

Do not build an engine. Recover the idle nodes with what already exists:

- **Batching** — measured **1.79× at batch 4** on sparse MoE. Under sharding the
  nodes are idle 1−1/S of the time; concurrent requests partially refill them.
  **Never batch 8** (worse than 1).
- **Watch upstream** — async/pipelined RPC (#18626) would attack the same idle
  time directly. Stalled 7+ months (F5), but it is the cheap version of this idea.
- **Get gigabit** (~$20–30 switch). Precondition for expert parallelism ever
  being viable here, and it helps sharding today.

**Verdict: no separate pipelines. One decision rule — pick the largest model with
S = 1 and replicate it — with sharding reserved for the single frontier model
that genuinely cannot fit.** Which is the architecture already recorded; this
note exists so the question is not re-opened a third time.

---

## E. "Every other repo does RAG. Why don't we?"

**Asked 2026-08-17, and it deserves a real answer** — "we are different" is not
an argument, and the question is the right one to ask of any design that departs
from what everyone else built.

### The short answer: they are not doing our task, and our cost model is inverted

`private-gpt`, `Kotaemon`, `localGPT` and the rest are **RAG question-answering**
systems: embed a corpus, retrieve top-k chunks for a query, answer from those.
Two things drive that design, and **neither holds here**:

| | Typical RAG-QA tool | **This project** |
|---|---|---|
| Task | "answer a question about my documents" | **summarise / draft from the whole document** |
| Who is waiting | a user at a prompt | **nobody — overnight batch** |
| Marginal cost of tokens | real money (API) or rented GPU-hours | **~zero: owned, depreciated, idle hardware** |
| What the design optimises | **reading less** | **completeness** |
| Recall failure | a slightly worse answer | **a silently incomplete summary** |

**Retrieval presupposes you know what you are looking for.** For a *question*
that is fine — the question names the target. For a *summary* it is circular: if
you already knew which passages mattered, you would not need the summary. There
is no top-k that yields a faithful summary of a document you have not read.

And the economics invert cleanly. RAG's entire value proposition is reading
fewer tokens, because tokens cost money or GPU time. Here the machines are
already bought, already depreciated, and otherwise idle, and the work is
explicitly *"submit overnight, read in the morning."* **So RAG optimises the one
resource we have in surplus and sacrifices the one we cannot afford to lose.**
For legally sensitive material a retriever that silently fails to rank a relevant
section is not a degraded answer, it is a wrong one.

### This is not only reasoning — the literature agrees for THIS task

- **BooookScore** (arXiv:2310.00785): map-reduce beats refine decisively on
  book-length summarisation (Mixtral 81.5 vs 64.5; LLaMA-2 failed refine
  outright). Refine is also strictly sequential, so far worse in wall-clock.
- **arXiv:2307.03172**: a bigger context window does *not* fix "lost in the
  middle" — extended-context variants show near-identical position bias. So
  chunking is right even where a large window exists.

### Three concessions — places where the RAG crowd is right and we are not

**1. For one of our own stated workloads, RAG is the correct primitive, and we
must not rebuild it.** `CLAUDE.md` lists *"medium-horizon multi-step search and
Q&A"* as a target workload. That **is** what those tools do. Writing our own
retriever for it would be exactly the reinvention this section is accusing others
of not needing. It should arrive as a **task profile** with retrieval inside it,
plugged into the seam `CLAUDE.md` already says to preserve — not as a competing
architecture.

**2. Map-reduce is right WITHIN a document; retrieval may be right ACROSS a
corpus.** We have been conflating two levels. "Read every chunk" is correct for
one document you have been asked to summarise. It is **not** obviously correct
for "answer this from a 10,000-document archive" — reading all of it per question
is absurd, and that is precisely the case retrieval was invented for. **A hybrid
is the honest answer at corpus scale:** retrieve which *documents* are relevant,
then read those **completely**. We have never considered this and should.

**3. We should steal RAG's provenance discipline — this is a real weakness in our
design.** RAG systems keep a citation or span per claim, which makes verification
cheap and grounding checkable. Our map-reduce currently turns each chunk into
**prose**, and the reduce step consumes prose. **Provenance is destroyed at the
map step**, so the reduce step cannot check any claim against source text — which
is exactly the fabrication-laundering risk in F25 and in the reframed eval
(`STATUS.md` 4). A retrieval system would not have this problem.

**Concrete fix, cheap now and expensive to retrofit:** chunk summaries should
carry their **chunk id and source offsets**, so every sentence in the final
output can be traced to the span it came from. That makes the paired
map-reduce-vs-single-pass faithfulness experiment mechanically checkable instead
of requiring a human to re-read the source, and it is the difference between
"the summary looks right" and "every claim in the summary is locatable."

### Verdict

**The departure is justified for the summarisation workload and is supported by
the literature — but it was stated too broadly.** The defensible claim is
narrower than "RAG is the wrong shape":

> Reading the whole document is right for summarising a document. Retrieval is
> right for finding which documents to read, and for question-answering. We need
> both, at different levels, and we should borrow RAG's provenance tracking
> regardless of which one is running.

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

---

## F. The user interface was never researched, and "Open WebUI" may be the RAG mistake again

**Raised by the operator, 2026-08-17: "what is the actual user interface for
Missing Link and did we even research that?"** The answer is **no.**

Research to date covered summarisation pipelines, benchmarks, faithfulness
metrics, engine internals, model selection and tool-calling models. **Nothing
covered the interface.** What exists was built from the plan's spec, and the plan
did not research it either.

### What exists today

A single-page job console: a task dropdown (`summarise`/`report`/`qa`), a file
upload or paste-text area, a "Queue it" button, and a table of jobs
(id / task / status / chunks / time / submitted) with a result page per job.
Minimal CSS, dark mode, no JavaScript. It works — verified end-to-end — and it is
plainly **a developer's job console.**

### The bigger problem: "Open WebUI" is a settled decision that was never examined

`CLAUDE.md` lists **Open WebUI, lightly skinned** as the settled chat frontend.
**Open WebUI is a CHAT interface**, and this workload is explicitly not
interactive: *"submit overnight, read in the morning"*, *"slow is fine; nobody is
waiting at a prompt."*

**This is the same category error as section E.** There, popular self-hosted tools
were RAG-QA systems adopted for a summarisation problem. Here, the popular
self-hosted frontend is a chat UI adopted for a batch problem. In both cases the
pull is toward the shape everyone else built, for a different job. **A chat window
is the interface whose central affordance — type, wait, read — is the exact thing
this project cannot offer.**

### What UI research would actually have to answer

The users are non-specialists in health, legal, education and community services.
Against that, the current UI has four concrete gaps:

1. **Batch submission.** The form takes **one** document. The real task is "here
   are 40 case files." One-at-a-time is not a workflow.
2. **When will it be done?** No estimate at all — and **we can compute one**, from
   `docs/measurements.md`: chunks x measured prefill rate + expected output x
   generation rate, divided by R replicas. An ETA built from our own measured
   throughput is exactly the kind of thing this project should be able to do and
   currently does not.
3. **Why should the reader trust it?** No provenance. A summary of a legal
   document with no way back to the source is not usable evidence — which is the
   same conclusion the faithfulness work reached from the other direction
   (`docs/EVALUATION.md`: score each chunk summary against its own chunk;
   section E concession 3: carry chunk ids and offsets). **Three independent
   lines now converge on provenance.**
4. **Failure has to be loud.** A job that fails overnight must be obvious in the
   morning, not a row in a table nobody reloaded. Notification-on-completion is
   arguably the single most important feature of an async tool, and there is none.

### Recommendation

**Do not build more UI yet, and do not adopt Open WebUI by default.** Research the
interface the way the summarisation pipeline was researched: find what exists for
**asynchronous batch document work** (not chat, not RAG-QA), and check whether
anything fits before writing more Jinja. Then treat the four gaps above as the
requirements list.

**Keep the current console.** It is genuinely useful for the operator — submitting
test jobs, watching the queue, reading results — and that is a different user from
the eventual end user. **Do not skin it and call it the product.**

---

## G. Fan-out has two granularities, and different tasks want different ones

**Raised by the operator 2026-08-17, and it is the right decomposition.** "I
imagine you want the whole cluster working on intermediate parts of a summary...
so some tasks parallelize differently."

There are **two** independent fan-out axes, and the project had been treating
fan-out as one thing:

| | **Job-level** | **Chunk-level** |
|---|---|---|
| Unit distributed | whole documents | one document's chunks |
| What it buys | **throughput** (docs/night) | **latency** (one doc sooner) |
| Coordination | none — embarrassingly parallel | a barrier before reduce |
| Aggregate throughput | **R×** | **1×** (same total work) |
| Right when | queue depth >= R | queue depth == 1 |

**The key asymmetry: chunk-level fan-out does NOT increase aggregate throughput.**
It is the same total work spread wider, so it only reduces the wall-clock of one
document. Job-level fan-out is what multiplies throughput.

So the rule follows from queue depth, not preference:

- **Queue depth >= R** -> job-level. Simplest, no coordination, full R× throughput.
  This is the overnight-batch case and the common one.
- **Queue depth == 1** -> chunk-level, or R−1 nodes sit idle. This is the "I threw
  in one huge PDF and want it tonight" case, which is a real user need and is
  exactly what happens with a single 40-chunk document today.
- **Mixed** -> job-level first, then chunk-split whatever is left when nodes go
  idle. Work-stealing rather than a static plan.

`STATUS.md` task 2 already anticipated half of this ("chunks within one document
should fan out across endpoints too, not just whole jobs"), but not that the two
modes optimise **different metrics** and should be selected by queue state.

**The optimisation the operator suspected does exist**, in the reduce step. With
chunk-level fan-out the reduce is a barrier: it waits for the slowest map. Options,
none measured here: a **tree reduce** (combine pairs of chunk summaries in
parallel, log depth instead of one big join) which also sidesteps the reduce
prompt growing with chunk count; or **streaming reduce**, folding each map result
in as it lands. Tree reduce is the more interesting one because a 40-chunk reduce
prompt is itself a long-context prefill, and long-context prefill is what this
hardware is worst at.

**Do not build either yet.** Job-level fan-out is strictly simpler, delivers the R×
that the measurement validated, and is what the current queue already almost
supports (`INFERENCE_ENDPOINTS` exists; `run_forever` needs to become R workers).
Chunk-level is the second step, and the tree reduce is a third.

---

## H. Does quantisation format change speed on THIS CPU? Almost certainly, and it is unmeasured.

**Asked by the operator 2026-08-17: "maybe different quantisations of a model will
run better or worse because of this CPU architecture and instruction set?"**
**Yes — and this is a genuine gap, not a settled question.**

Why it is not simply "smaller = faster":

- **Generation is bandwidth-bound**, so fewer bytes per weight really is faster,
  roughly linearly. That part is settled (F11, F24).
- **Prefill is compute-bound**, and quant formats differ enormously in how
  expensive they are to *dequantise* per weight. Legacy formats (`Q4_0`), K-quants
  (`Q4_K_M`) and i-quants (`IQ4_XS`) use different unpacking and codebook lookups.
  **i-quants are widely reported slower on CPU** for exactly this reason.
- **This CPU has AVX2 and NO AVX-512** (F7). Several quant kernels are tuned
  hardest for AVX-512, so the ranking on this hardware need not match published
  benchmarks from newer chips.
- **`ik_llama.cpp` exists largely to optimise quantised CPU matmuls**, and offers
  `-rtr` (run-time tensor repacking) plus interleaved layouts. Its +52% prefill
  (F27) is evidence that the quant/kernel path is where CPU headroom lives — which
  makes it very likely that quant *choice* also matters, and by a similar order.

**So the two effects can pull in opposite directions:** a smaller quant reads
fewer bytes (faster generation) but may dequantise more expensively (slower
prefill). Since **prefill is ~79% of document wall-clock**, a quant that wins on
generation could still lose overall. Nobody has measured this here.

**The experiment, and it is cheap.** Qwen3-4B is 2.4 GB, so several quants cost
minutes to fetch and one `llama-bench` run each:

```bash
# same model, same -t 4, same -p/-n, one row per quant
llama-bench -m qwen3-4b-Q4_0.gguf   -t 4 -p 512,2048 -n 128 -r 2
llama-bench -m qwen3-4b-Q4_K_M.gguf -t 4 -p 512,2048 -n 128 -r 2   # have this
llama-bench -m qwen3-4b-IQ4_XS.gguf -t 4 -p 512,2048 -n 128 -r 2
llama-bench -m qwen3-4b-Q8_0.gguf   -t 4 -p 512,2048 -n 128 -r 2
# then repeat the winner under ik_llama.cpp, and with -rtr
```

**Report pp and tg separately** — a single "tok/s" number would hide the whole
effect. If the ranking differs between mainline and ik_llama, that is itself the
finding.

**Operator's stated position, recorded:** *Q8 is the maximum worth holding;
anything larger is for show.* That is defensible — Q8 is near-lossless on
published KLD comparisons — and it happens to match what a previous session
already chose: the in-flight Qwen3-Next-80B download is **UD-Q8_K_XL**. Note it
also interacts with the S=1 threshold: at Q8 that model is **87 GB**, which fits
one node with ~12 GB spare. At any larger quant it would not.

---

## I. DeepSeek Harness — researched 2026-08-17, and it is a mismatch

`deepseek-ai/deepseek-harness` ("dsh"), released 2026-08-13, MIT, TypeScript, plugin
("Cordis") architecture, launched via `npx @deepseek-ai/dsh web`. It is an
**interactive agent runtime in the Claude Code shape** — tool loop, sandboxing,
session UI — not a scheduler.

**Verdict: do not adopt.** Three independent reasons:

1. **It solves a different problem.** Nothing in it addresses triaging a queue,
   health-checking R endpoints, routing around a dead node, or assembling batches.
   It targets a single interactive session against a single configured model.
2. **It brings no local inference.** It always calls out to an API — DeepSeek's own,
   or any custom OpenAI-compatible endpoint. Pointing it at `llama-server` is
   *plausible* but **nobody has demonstrated it** (INFERRED, not confirmed).
3. **It is four days old and in developer preview with breaking changes promised.**
   Replacing a working, tested SQLite+FastAPI queue with that does not clear the bar
   this project sets for adopting a framework over working code.

**Revisit only if** it matures past preview *and* someone demonstrates multi-endpoint
fan-out. Not before.

---

## H (addendum). Unsloth `UD-` dynamic quants: real, with one capacity trap

Researched 2026-08-17. We already depend on these files (`UD-IQ2_M`, `UD-Q3_K_XL`,
and the in-flight `UD-Q8_K_XL`) and had never checked the claim behind them.

**What they are (CONFIRMED from Unsloth's docs):** per-*layer*, per-*tensor* bit
allocation chosen individually per model — important tensors bumped to 4–8 bit,
unimportant ones as low as 1-bit — using an imatrix built from a curated 300K–1.5M
token calibration set chosen because plain text calibration is a poor proxy for
instruct behaviour. **The selection heuristic is not published.**

**Evidence it beats same-size standard quants:** an *independent* Aider Polyglot run
(not Unsloth staff) gave UD-IQ2_M **64.3%** vs community IQ2_M **56.6%**, and
UD-IQ4_XS 69.2% vs 66.3%. Unsloth's own KLD/MMLU tables agree. **But** an independent
DeepSeek-R1 perplexity thread found UD-IQ2_XXS/UD-Q2_K_XL "in the range of the usual
Q2_K" — i.e. **the advantage may shrink at the extreme low-bit end**, which is exactly
where we would be tempted to use it.

**THE TRAP, and it is ours specifically:** a UD quant of a given letter is **a size
tier heavier** than the plain quant, because more tensors get more bits. **So S=1
capacity maths must use the ACTUAL file size on disk, never the nominal
quant-letter size from a sizing table.** This is how a model that "fits at Q4" turns
out not to.

**Ruled out from Unsloth for an inference-only CPU fleet:** fine-tuning/LoRA/RL (its
core product), Unsloth Desktop (GUI), Dynamic NVFP4 (needs Blackwell-class GPU
tensor cores), and the GGUF export/tokeniser-patch pipeline (fires only when
exporting a fine-tune). No merged Unsloth PRs into `ggml-org/llama.cpp` core were
found.

---

## J. Prompt compilation: an org's report specs, compiled into a task profile by an LLM

**Raised by the operator 2026-08-17**, following from the per-workflow instruction
boxes: free text like *"make it a board report"* is not enough to make the model
write the right *kind* of report.

> *"which implies needing an inference step to guide the model (which itself needs a
> prompt and a hook for debug logging) so that it is writing the right kind of
> report. an org would probably can have an offline set of specs for reports figured
> out and formatted by an LLM."*

**The idea, and it is a good one:** an organisation already has report conventions —
templates, headings, required sections, house style, statutory fields. Rather than
expecting an operator to re-type that into a text box each time, **capture it once,
offline, and compile it into a task profile.**

Three stages, and the middle one is new:

1. **Spec capture (offline, one-off, per organisation).** An LLM reads the org's
   existing report examples and produces a structured spec: required sections,
   ordering, tone, what must never be inferred. Cheap because it happens once and
   nobody is waiting.
2. **Prompt compilation (the new inference step).** Spec + the operator's free-text
   instruction → the actual map and reduce prompts for this job. **This step needs
   its own prompt**, and it is a *meta*-prompt: a prompt that writes prompts.
3. **Execution** — the existing map-reduce, unchanged.

**Why this fits the architecture rather than fighting it.** `CLAUDE.md` already
requires prompts, chunking and evaluation to stay separable from the queue, because
that seam becomes the skill's task-profile interface. **This is that seam being used
for what it was reserved for.** `worker.PROMPTS` / `REDUCE_PROMPTS` are already data;
compilation just means generating those entries instead of hand-writing them.

**The operator's two requirements on it are correct and non-obvious:**

- **It needs its own prompt**, versioned and reviewable like any other. A
  prompt-writing prompt that nobody can inspect is the least debuggable component
  imaginable.
- **It needs a debug-logging hook.** When a report comes out wrong, the first
  question is "what prompt actually ran?" — and with compilation in the path, that is
  no longer visible in the source. **Log the compiled prompt against the job**, the
  same way `chunk_summaries` now records what each map step actually produced. Cheap
  now; near-impossible to retrofit once compilation is live.

**Cost note:** compilation is one short inference per job (or per spec change if
cached), against a document workload of minutes to hours. Negligible — and it can run
on the small appliance model rather than the big one.

**Not built. Do not start it until the merge lands** — but reserve the log field when
touching the schema next, because that is the expensive part to add later.

---

## K. Docker — two questions the operator actually asked, not one

**Raised 2026-08-18: "I'm smelling docker in our future."** Scoped by the operator to
exactly two targets plus "research and report only": (1) packaging Missing Link for
the Claude Skill's setup phase, and (2) sandboxing Claude Code / agent operations
against a repeat of the evening's error log. **They have different answers.**

**Recommendation, up front:**

| Target | Verdict | Confidence |
|---|---|---|
| **1. Package Missing Link in Docker** | **No.** Ship a vendored wheelhouse instead. | High — the venv is 117 MB and has 7 dependencies; Docker solves a portability problem this project pinned away in `CLAUDE.md` ("Debian 12 headless, scripted provisioning"). |
| **1b. (raised by the cost section, not a named target) Containerize `llama-server` itself** | **No.** | High — no citable evidence for near-zero overhead on *this* workload class, and two concrete new failure surfaces (thread-affinity, cgroup-memory-vs-mmap) that bare metal does not have. |
| **2. Sandbox Claude Code / agent operations** | **No, not with a container — use `PreToolUse` hooks.** A container would have stopped **zero** of the five logged slips, and the two most serious of them are exactly what a hook is designed to block. | High — verified against Anthropic's own devcontainer documentation, which states the bind-mounted workspace is *not* protected. |

Neither "no" is "do nothing." Each has a concrete, cheap next action below, and each
is closeable rather than merely deferred.

**What is on this box right now (checked 2026-08-18):** neither `docker` nor `podman`
is installed (`which docker podman` — empty; `systemctl status docker` —
"could not be found"; `dpkg -l | grep -iE 'docker|podman|containerd|runc'` — no
rows). The kernel is 6.1.0-52 with cgroup v2 controllers available
(`cpuset cpu io memory hugetlb pids rdma misc`), so nothing here is a blocker to
adopting containers if the evidence pointed that way — it is a genuinely open
choice, not a foregone one. It doesn't.

### Target 1 — packaging Missing Link

#### What exists

Missing Link is FastAPI + `uvicorn[standard]` + SQLite (stdlib) + `pypdf`, run today
from `missing-link/.venv` via `missing-link.service` (systemd, `Restart=always`,
`User=debian1`). Measured on this box:

- **`du -sh missing-link/.venv` → 117 MB.** Not a heavy stack — no torch, no numpy,
  no compiled ML dependencies. `requirements.txt` lists seven packages: fastapi,
  uvicorn, httpx, jinja2, pytest, python-multipart, pypdf.
- The systemd unit already encodes the crash-recovery reasoning this project cares
  about (`requeue_running()` on restart, per F20) in its own comment block — it is
  not a naive unit.
- The install path today is `pip install -r requirements.txt` against **live PyPI**.
  That is the one real gap: it does not work air-gapped, and this fleet may be.

#### The alternatives, assessed honestly

| Option | What it needs at install time | Fits "non-technical operator, air-gapped, disk-poor, 100 Mb LAN"? |
|---|---|---|
| **.deb package** | A packaging pipeline (control file, postinst, changelog), and either a local apt repo or manual `dpkg -i` | High effort to build and maintain for a 7-dependency in-house tool with one deployment target OS. The tooling to *build* the package is more machinery than the thing being shipped. |
| **pipx / uv install** | Still needs the wheels from *somewhere* — PyPI by default | Does not solve the offline problem by itself. Same failure as plain `pip install` on an air-gapped LAN: the network call fails, just with a different tool's error message. |
| **Vendored wheelhouse** | Nothing at install time — wheels are already on disk | **Fits.** `pip download -r requirements.txt -d wheelhouse/` once, from any machine with internet (the same "generate" step that already assembles provisioning scripts), then `pip install --no-index --find-links=wheelhouse -r requirements.txt` on the node. Wheelhouse for these seven packages is on the order of tens of MB — trivial against the 65 GB model-file budget this fleet actually worries about (F16, F28). |
| **Docker** | An image, either pulled (needs a registry reachable from the node) or `docker load`-ed from a `.tar` built elsewhere and copied over | Solves a problem this project does not have (see below) and re-introduces the exact offline-transfer problem it's meant to fix, one layer up. |
| **Current venv + systemd, unmodified** | Same as wheelhouse, minus the one fix | Everything except the offline-install gap already works and is already in the "deterministic bash script" shape `REQUIREMENTS.md` demands. |

#### Why Docker doesn't serve this goal

**Docker's central value proposition is portability across heterogeneous host
environments and OS/library version drift.** This project deliberately does not
have that problem: `CLAUDE.md` already settles on **one OS** ("Debian 12 headless,
scripted provisioning ... not `dd` cloning — disks vary") provisioned from **one
preseed**, so every node already gets the same Python 3.11 from the same base
image. There is no host-Python-version drift to contain, because the fleet's own
design already eliminated it before Docker would get a chance to.

What containerizing Missing Link *would* cost, concretely, on this fleet's stated
constraints:

- **A new runtime dependency to provision and patch fleet-wide** (`dockerd` or
  `podman`), on top of the provisioning surface `setup.sh` / `distribute.sh`
  already own — more attack surface and more moving parts for a non-technical
  operator to eventually keep patched, which is precisely the ongoing-cost problem
  `CLAUDE.md`'s "on-prem rarely survives a budget" argument is about.
- **A new failure mode for the exact user this is being built for.** The
  operator's own framing of the problem: *"now the operator must debug Docker
  instead of pip."* A `docker pull` failure against an unreachable registry is a
  network/registry error; a `pip install --no-index --find-links=wheelhouse`
  failure against a missing wheel is `ls wheelhouse/` away from being obvious.
  The second is more legible to someone with no specialist on staff.
- **Volumes for exactly the state that must survive a restart.** The SQLite jobs
  DB and uploaded documents would need a bind mount or named volume to persist
  across container recreation — solvable, but it is new configuration surface
  for something the current systemd unit already gets right by writing to a plain
  directory on disk.
- **It does not remove the offline-install problem — it relocates it one layer
  up**, from `pip install` needing PyPI to `docker pull` needing a registry
  (Docker Hub's own anonymous rate limit is 100 pulls/6h, 200 authenticated,
  REPORTED — [github.com/concourse/concourse#6039](https://github.com/concourse/concourse/discussions/6039)
  — moot on a genuinely air-gapped network anyway, where Docker Hub is simply
  unreachable and a private registry or mirror becomes yet another thing to stand
  up before the first `docker pull` succeeds).

#### Recommendation and next action

**Do not containerize Missing Link.** Keep venv + systemd — it already matches the
shape `REQUIREMENTS.md` asks for — and close the one real gap with a wheelhouse:

```bash
# once, on any machine with internet (part of the "generate" phase):
pip download -r missing-link/requirements.txt -d missing-link/wheelhouse/
# on the node, offline:
pip install --no-index --find-links=missing-link/wheelhouse -r missing-link/requirements.txt
```

Ship `wheelhouse/` the same way `distribute.sh` already ships binaries and models
over the LAN. This is a same-shape, same-day change; it does not touch the systemd
unit, the app, or the tests.

**What would change this answer:** if the skill's hardware-profile extensibility
(`CLAUDE.md`: "GPU clusters, high-CPU/low-RAM, GPU MoE offloading") ever means
Missing Link must run on a **different host OS** than Debian 12 — the one axis
where Docker's actual value proposition (environment reproducibility across
heterogeneous hosts) would apply — revisit then, not now.

### Target 1b — containerizing the inference engine itself (raised by the cost section, not a named target, but worth answering explicitly)

**Does containerizing `llama-server` / `rpc-server` cost measurable throughput on
this CPU-bound, memory-bandwidth-bound workload? Short answer: nobody has measured
the case that matters, and two concrete, citable risks exist that bare metal
does not have.**

#### The throughput evidence is real but does not transfer

- **General container-vs-bare-metal STREAM comparisons disagree with each other.**
  One HPC benchmarking study reports Docker at **~36% average overhead** vs.
  native throughput (REPORTED —
  [hps.vi4io.org container-vs-native HPC benchmark](https://hps.vi4io.org/_media/teaching/autumn_term_2025/stud/hpcsa/mohamed-basuony.pdf)).
  A separate empirical comparison of Docker/OpenStack VM/bare metal reports Docker
  "almost equals" bare metal while VMs lose 28–30% (REPORTED —
  [ResearchGate: An Empirical Performance Evaluation of Docker Container,
  Openstack Virtual Machine and Bare Metal Server](https://www.researchgate.net/publication/347135744_An_Empirical_Performance_Evaluation_of_Docker_Container_Openstack_Virtual_Machine_and_Bare_Metal_Server)).
  These contradict each other and neither is on hardware or a workload resembling
  ours.
- **The llama.cpp-specific Docker numbers that exist are GPU-passthrough
  benchmarks** — within ~3% of bare metal on an RTX 4090 (REPORTED —
  [markaicode.com Docker llama.cpp integration guide](https://markaicode.com/integrate/docker-with-llamacpp/)).
  That measures container/GPU driver passthrough overhead, a different mechanism
  entirely from cgroup CPU/memory accounting on a 4-core CPU-only node already
  running generation at **~99% of its measured 28.2 GB/s memory-bandwidth roof**
  (F11). No source found tests CPU-only, memory-bandwidth-bound LLM inference
  under Docker specifically.
- **Per this project's own citation discipline** (`CLAUDE.md`: *"Performance claims
  must come from measurement on the hardware. If a number is not in
  `docs/measurements.md`, it may not be quoted"*), the honest position is: **no
  throughput-overhead number for this exact case can be asserted, in either
  direction.** If this question ever becomes load-bearing, it needs a real
  `llama-bench` run inside vs. outside a container on node 1 — cheap, and exactly
  the kind of experiment this project already runs before standardising.

#### Two concrete risks that do not need a benchmark to state

**1. Default Docker CPU scheduling would re-expose the exact bug F10 already
found and fixed.** `-t 4` only works because it is paired with the fact that the
kernel scheduler puts those 4 threads on physical cores, not SMT siblings —
`rpc-server -t 8` on this CPU is **26% slower at generation** than `-t 4` (F10),
purely from siblings contending for the same memory pipe. Docker does not pin CPU
affinity by default; that requires an explicit `--cpuset-cpus` matching the
physical-core list derived from `lscpu -p=Core,Socket` (the same command
`CLAUDE.md` already mandates). A forgotten `--cpuset-cpus` fails exactly like a
forgotten `-t` value did — silently, and only visible in the numbers.

**2. A container memory limit interacts badly with the exact OOM history this
project already mitigates.** `vm.overcommit_memory` is a **host-wide, non-namespaced
sysctl** — it cannot be set per-container, and setting it inside a container
requires privileged mode, which defeats the isolation (REPORTED, aggregated from
Docker's own forum guidance on non-namespaced sysctls,
[forums.docker.com](https://forums.docker.com/t/how-to-set-the-vm-overcommit-memory-parameter-when-running-docker-desktop-on-macos/139029)).
So `setup.sh`'s existing `vm.overcommit_memory=1` mitigation for #22629 is **not
weakened** by containerizing — it still applies, because it's the host kernel's
setting regardless of namespace. **But** if a container is *also* given a cgroup
memory limit (`docker run -m`, and there is little reason to containerize a
memory-hungry process without one) that limit is enforced by an **entirely
separate mechanism**: the cgroup memory controller counts page cache — including
`mmap`-backed file reads — against the container's `memory.max`, and the cgroup
OOM killer fires independently of host-level overcommit policy when that limit is
hit (REPORTED — [Netdata: Docker memory usage explained](https://www.netdata.cloud/guides/docker/docker-memory-usage-explained/);
[cr0x.net: Docker OOM in containers](https://cr0x.net/en/docker-oom-memory-limits/)).
llama.cpp loads GGUFs via `mmap` by default — F23 confirms the RAM-resident
behaviour by direct RSS measurement — so **a large model load is precisely the
page-cache-heavy pattern that trips this**, on top of, not instead of, the
overcommit history #22629 already describes. This is a genuinely new failure
surface bare metal does not have, not merely the old one relocated.

**Also checked and ruled out as a differentiator:** THP. Docker containers do not
get separate THP control — they inherit the host's setting (REPORTED, aggregated
from container/THP interaction sources). Node 1's THP is `madvise` (F9), and that
does not change under a container either way.

**And the ISA-lockstep argument does not go away — it relocates.** An image built
with `GGML_NATIVE=ON` on the coordinator and shipped to an older-CPU node would
still pass the RPC version handshake, load the model, and SIGILL mid-graph (F8),
exactly as a bare binary would. `distribute.sh`'s version/libc/ISA assertions
(F13, F32) would still be needed — just re-implemented against `docker run --rm
<image> lscpu` instead of a plain SSH + `ldd` check that already exists and is
already tested. **An image does not make those assertions redundant; it adds a
layer they have to be re-derived through.**

#### Verdict

**Do not containerize the inference engine.** No evidence supports a throughput
claim either way for this exact workload, and two concrete new risks exist
(thread-affinity regression, cgroup-memory-vs-mmap interaction) that bare metal
does not carry. If GPU nodes ever join the fleet (`CLAUDE.md`'s GPU hardware
profile), NVIDIA Docker's driver-passthrough case is the one scenario in the
literature that actually looks close to free — revisit *then*, on *that*
hardware, not now.

### Target 2 — sandboxing Claude Code / agent operations

#### What actually exists (checked against Anthropic's own docs, 2026-08-18)

Claude Code offers a spectrum, not a binary choice
([code.claude.com/docs/en/sandbox-environments](https://code.claude.com/docs/en/sandbox-environments)):

| Approach | What it isolates | Needs Docker |
|---|---|---|
| **Sandboxed Bash tool** (`/sandbox`) | Bash commands and children only — not file tools, MCP, or hooks | No |
| **Sandbox runtime** (`@anthropic-ai/sandbox-runtime`, beta) | The whole process (bubblewrap/Seatbelt) | No |
| **Dev container** | Full environment, via Docker | Yes |
| **Custom container** | Full environment, your own image | Yes |
| **VM** | Full OS | No (but heavy) |

**The load-bearing fact, stated by Anthropic's own devcontainer documentation, is
this warning (CONFIRMED — [code.claude.com/docs/en/devcontainer](https://code.claude.com/docs/en/devcontainer)):**

> "Claude can still modify any file in the bind-mounted workspace, which appears
> directly on your host, and reach anything the container's network policy
> allows."

A dev container's actual protection model is: **restrict outbound network egress**
(the reference implementation's `init-firewall.sh` is a default-deny iptables
allowlist for GitHub/Anthropic API/npm) and **contain what a compromised
*dependency or MCP server* can do**. It is explicitly **not** a boundary around the
project directory itself, which is bind-mounted and therefore as writable as if
there were no container at all.

#### Why this project's agents cannot be sandboxed away from the cluster anyway

This is not a hypothetical trade-off — `CLAUDE.md` states outright that the agent
is "the operator, not an advisor," runs on the coordinator with "real hardware
under it and root via sudo," and reaches other nodes over "plain SSH on LAN IPs."
A container whose entire value is restricting network egress and host reach would
have to be reconfigured to punch back open: SSH keys for six other nodes, the
raw LAN IPs the RPC mesh runs on (Tailscale is deliberately excluded from that
path — `CLAUDE.md`: *"Never pass `--advertise-routes` ... RPC runs on raw LAN
IPs"*), `/opt/models`, and `systemctl`/`journalctl` access to services the
container does not own. **At that point the isolation boundary protects nothing
it was built to protect, because everything it was built to fence off is exactly
what the agent is contractually required to reach to do its job.** An agent
sandboxed away from the cluster cannot operate the cluster, which is the premise
the task brief already states and this research confirms rather than
contradicts.

#### The five slips, checked one by one against "would a container have stopped this?"

| # | Slip | Where it happened | Would a container have stopped it? | Would a hook stop it? |
|---|---|---|---|---|
| 1 | `git add -A` swept 3 agent worktrees in as embedded git repos, pushed | Inside the **bind-mounted project directory** — a container does not protect this (see quote above) | **No** | **Yes** — `PreToolUse` matcher on `Bash(git add -A*)` |
| 2 | `pkill -f` matched its own command line, killed the shell 3×| The agent's own shell **inside whatever namespace it runs in** — a container relocates the namespace, it does not remove the process from `pkill -f`'s pattern match within it | **No** | **Yes** — `PreToolUse` matcher on `Bash(pkill -f*)`, replaced with pidfile/systemd-unit targeting |
| 3 | f-string with escaped quotes, invalid Python, twice | Ordinary code, caught by the traceback | **N/A** — not an isolation problem at all | **N/A** — self-correcting already |
| 4 | Destructive SQL ran without checking row counts, off-by-one on `%PDF` matched 0 rows | Against the **real jobs database** — an agent that needs to operate Missing Link needs to reach this DB regardless of container boundary | **No** — the DB is exactly what the agent must legitimately touch | **Partially** — a hook can flag `DELETE`/`UPDATE` patterns for a preceding count-check; harder to make airtight than #1/#2, but still closer than a container |
| 5 | Wrong form-field names guessed instead of read from the API | Against the **real Missing Link HTTP API** the agent must call to do its job | **No** | **No** — already self-corrected by the loud HTTP 422/405; the actual fix is "read the API before calling it," a `REQUIREMENTS.md` convention rather than a technical control |

**Zero of five would have been stopped by a container.** Two happened inside the
part of the filesystem a container does not protect (bind mount). Two required
real access to live state (the actual jobs DB, the actual API) that a sandboxed
agent would need to be reconfigured to reach anyway, at which point the sandbox
is not sandboxing that path. One was a plain syntax error that any execution
environment surfaces via traceback.

**The two most serious slips (#1 and #2) are precisely what `PreToolUse` hooks
are built for** — CONFIRMED against
[code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks): a hook
receives the tool call before it executes, can match a Bash pattern via the
`if: "Bash(pattern)"` syntax, and blocks it with `exit 2` or a JSON
`permissionDecision: "deny"`, deterministically, before the command ever touches
disk. This is a same-day, zero-infrastructure change:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [
        { "type": "command", "if": "Bash(git add -A*)",
          "command": "echo 'enumerate paths explicitly; git add -A can sweep in worktrees' >&2; exit 2" } ] },
      { "matcher": "Bash", "hooks": [
        { "type": "command", "if": "Bash(pkill -f*)",
          "command": "echo 'pkill -f can match the caller; use a pidfile or systemctl unit' >&2; exit 2" } ] }
    ]
  }
}
```

(Documented limitation, CONFIRMED: the `if` matcher does best-effort Bash
parsing and fails open — runs the hook — on parse failure, so it is a strong
deterrent, not a mathematically airtight guarantee. It is still strictly better
than a container for these two patterns, which a container does not address at
all.)

#### Git worktrees as isolation — what they actually protect, and what they didn't

The repo already uses `isolation: "worktree"` for spawned agents (three worktrees
existed under `.claude/worktrees/` at the time of the incident). Worktrees are a
real and useful isolation mechanism — **for concurrent agents editing files
without colliding with each other or with `main`.** They are not a boundary
around the *coordinating* session's own git hygiene: the incident happened
because the top-level session ran `git add -A` from the repo root, which swept
in the worktree directories as embedded-repo gitlinks. Worktree isolation
protects agent-vs-agent; it did not and structurally cannot protect
coordinator-vs-itself. Same conclusion as containers: the fix for this class of
slip is a rule enforced before the command runs (a hook), not another isolation
boundary around content the mistake originates from, not outside.

#### Verdict and next action

**Do not containerize agent operations for this problem. Add the hardening pass
`REQUIREMENTS.md` already calls for, as `PreToolUse` hooks in
`.claude/settings.json`:** block `git add -A` (require enumerated paths), block
`pkill -f` against patterns that can match the caller (require pidfile or
`systemctl` targeting instead), and add a hook or convention requiring a
row-count check before any `DELETE`/`UPDATE` against the jobs DB. This is
strictly better than a container for the specific failure log that motivated the
question, costs nothing to provision, and does not compromise the agent's need
to reach the real cluster — because it does not touch what the agent can reach
at all, only what it is allowed to *do* before doing it.

**What would change this answer:** if the threat model shifts from "agent makes
an honest mistake with real access" (this evening's log) to "an untrusted
prompt or a compromised dependency tries to exfiltrate credentials or pivot
across the network" — a genuinely different problem, closer to what dev
containers and the sandbox runtime are actually built for. Nothing in the
logged evening's incidents was that; if a future incident is, re-open this with
the sandbox runtime (no Docker needed) as the first thing to try, since it
constrains file tools, MCP servers and hooks together without carrying Docker's
own new failure modes onto a fleet that doesn't otherwise need it.

### Summary for whoever folds this in

- **Missing Link:** keep venv + systemd, add a vendored wheelhouse for offline
  install. Docker solves cross-OS portability this project already gave up by
  pinning Debian 12.
- **The inference engine:** don't containerize it either. No throughput evidence
  exists for this exact workload in either direction, and two concrete new
  failure modes (thread-affinity, cgroup-memory-vs-mmap) are real and citable.
- **Agent sandboxing:** `PreToolUse` hooks, not containers. A container's actual
  protection (network egress + host isolation) is aimed at a threat this
  evening's incident wasn't, and the project's agents structurally cannot be
  isolated from the cluster they exist to operate. The hooks above are the
  concrete next action; write them into `.claude/settings.json` and call the
  hardening pass in `REQUIREMENTS.md` (2026-08-17 entry) done for these two
  patterns.

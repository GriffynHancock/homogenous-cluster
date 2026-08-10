# Homogenous Inference Cluster — Design

**Date:** 2026-08-03
**Status:** Approved for planning
**Supersedes:** GPU-sharded design (commits 1892703, 3d54141)

## Purpose

Build a 7-node CPU-only LLM inference cluster from surplus school hardware,
running a frontier-class open-weights MoE model with no data leaving the
building, fronted by **Missing Link** — an asynchronous long-workload runner.

The cluster is a working demonstrator for a blog post arguing that
organisations with data-sovereignty constraints can repurpose idle compute for
private inference. See `CLAUDE.md` for the argument this build exists to make.

This spec covers the **cluster build and Missing Link**. Blog prose and
publishable artifacts are out of scope.

## Hardware

| Component | Per node | × 7 nodes |
|---|---|---|
| CPU | 5th-gen Intel i5 (Broadwell), AVX2, no AVX-512 | 7 |
| System RAM | DDR3-1600 dual channel, ~25 GB/s | see ladder |
| Storage | Mixed SSD + HDD, varying sizes | — |
| Network | Gigabit ethernet, same switch | — |
| GPU | 1× Quadro P600 2 GB — **unused** | — |

### The GPUs are dropped

The P600 (Pascal GP107) has 2 GB of VRAM, no FP8, no BF16 and crippled FP16.
Fourteen gigabytes pooled is too little to hold meaningful layers of a
100 GB-class model, and using them requires per-node hybrid CUDA/CPU RPC
topology whose feasibility is unproven.

Removing them eliminates: CUDA toolkit and driver management, architecture-flag
build constraints, the hybrid-RPC gate test, and an entire fork in the
architecture. The build gets materially simpler and the blog story gets cleaner.

**Revisit condition:** GPUs help *prefill*, not generation — prompt processing
is compute-bound where generation is bandwidth-bound. If measured
time-to-first-token on realistic documents proves unbearable, reconsider them
for prefill only. Decide on evidence, not in advance.

## Why this works: the two facts

**1. Active parameters determine speed; total parameters determine capability.**

Bytes read per token ≈ active params × bits-per-weight. For an MoE, only routed
experts are read. Everything else is storage. This is why a 1T-param model with
32B active is tractable on DDR3 and a 70B dense model is not.

**2. Pipeline sharding multiplies seats, not speed.**

For a single request, nodes execute sequentially — 7 nodes performs like one
node with 7× the RAM. With *concurrent* requests, each node processes a
different request's layers simultaneously, so aggregate throughput scales with
node count while per-seat speed stays flat.

The design target is therefore **a small number of slow seats**, not one fast
one. Missing Link exists to make that framing useful rather than apologetic.

## Memory budget

**Usable model budget = (pooled RAM − 1 GB/node for OS) × 0.85.**

The 15% headroom is a hard convention, not a guideline — it covers KV cache
growth, activation buffers, RPC transfer buffers, and page-cache pressure.
Configurations that fit only marginally are not specced.

| RAM/node | Pooled | Usable budget |
|---|---|---|
| 8 GB (current) | 56 GB | **~41 GB** |
| 16 GB | 112 GB | **~89 GB** |
| 32 GB (max for Broadwell) | 224 GB | **~184 GB** |

### RAM is the highest-leverage upgrade

Broadwell desktops take 32 GB (4 × 8 GB DDR3). Used DDR3 is near-worthless —
roughly **$150–250 for the whole fleet** moves pooled RAM from 56 GB to 224 GB.
Nothing else available comes close to that return, and it is a strong point for
the blog: the bottleneck is the cheapest component in the machine.

## Target model

**Qwen3-30B-A3B-Instruct-2507, Q8_0 (32.5 GB)** — 30B total, ~3.3B active,
48 layers, GQA with 32 query / 4 KV heads, 32k native context.

Fits the current ~41.6 GB pooled budget with ~9 GB spare, and requires no RAM
purchase. Per node that is only ~4.6 GB of model weights against ~7 GB
available — comfortable, not marginal.

The pooling is what buys the quant tier. On any single node, Q8_0 would be
impossible and Q4_K_M (18.6 GB) would be the ceiling. This is the design's
central claim in miniature.

Quant sizes for reference, should the budget move:

| Quant | Size | Notes |
|---|---|---|
| Q4_K_M | 18.6 GB | Single-node ceiling if pooling failed |
| Q5_K_M | 21.7 GB | Conservative fallback |
| Q6_K | 25.1 GB | Fallback if Q8_0 proves tight in practice |
| **Q8_0** | **32.5 GB** | **Target** |

**KV cache is not a constraint.** GQA at 4 KV heads gives ~96 KB/token: ~0.75 GB
at 8k, ~3 GB at 32k (fp16). Long context is limited by prefill *time*, not
memory — which is the opposite of the usual situation and worth stating in the
blog.

### Expected speed

Bytes read per token ≈ 3.3B active × ~1 byte (Q8_0) ≈ **3.3 GB/token**. Against
~25 GB/s DDR3 that is a ~7.5 tok/s theoretical ceiling; at the 50–70% bandwidth
efficiency typical of old hardware, expect **4–5 tok/s per seat**.

This is arithmetic on datasheets and must be replaced by measurement. **No
public benchmark exists for this model class on Broadwell/DDR3/AVX2** — the
research turned up nothing closer than DDR4 Xeons. Prefill on AVX2-without-
AVX-512 is entirely unmeasured, which matters because prefill dominates
time-to-first-token on document workloads.

### Model lineage note

Qwen has since shipped Qwen3.5-35B-A3B and Qwen3.6-35B-A3B, which supersede
30B-A3B as the small-MoE flagship. Different total parameter count, so not a
drop-in, and quant sizes shift accordingly. Worth evaluating before the build
starts, but 30B-A3B is the specced target because its sizes are known.

### Stepping stone

**Qwen3-4B Q4_K_M (~2.5 GB), single node.** Validates OS → llama.cpp →
provisioning → Tailscale before any distribution. Success: coherent multi-turn
chat via `curl`.

### Larger models are deferred, not designed for

If RAM is added later (Broadwell takes 32 GB/node → 224 GB pooled → ~184 GB
usable), gpt-oss-120b and Qwen3-235B-A22B come into range and the blog's
extrapolation to Kimi K3-class models becomes demonstrable rather than argued.
**Do not build for this now.** The architecture does not change — only the GGUF
path and the layer split do — so there is nothing to design in advance.

## Architecture

```
   Tailscale ────▶┌──────────────────────────────┐
   (SSH + web)    │  MASTER (node 1)             │
                  │  ├─ Missing Link (job queue) │
                  │  ├─ Open WebUI               │
                  │  ├─ llama-server (OpenAI API)│
                  │  └─ rpc-server               │
                  └──────────┬───────────────────┘
                             │ raw LAN IPs, gigabit
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │  node 2  │   │  node 3  │ … │  node 7  │
        │rpc-server│   │rpc-server│   │rpc-server│
        │ CPU + RAM│   │ CPU + RAM│   │ CPU + RAM│
        └──────────┘   └──────────┘   └──────────┘
```

### Components

| Unit | Does | Depends on |
|---|---|---|
| `rpc-server` (×7) | Holds a layer range in system RAM, runs forward pass | Local GGUF cache |
| `llama-server` (master) | Loads GGUF, assigns layers, serves OpenAI API | RPC endpoints |
| **Missing Link** (master) | Accepts long jobs, queues, executes, stores results | llama-server API |
| Open WebUI (master) | Interactive chat frontend | llama-server API |
| `setup.sh` + preseed | Bare node → cluster-ready | Debian netinst media |

Each is independently testable: `rpc-server` via `llama-bench` against one
endpoint; `llama-server` via `curl` to `/v1/chat/completions`; Missing Link
against a stub API; provisioning via a clean VM run.

### Why llama.cpp RPC, not Exo

Exo's value is heterogeneous device discovery and MLX/Apple support. Neither
applies to 7 identical Debian boxes. llama.cpp is already proven on this
hardware.

**`ik_llama.cpp` is a maybe, not a recommendation.** The fork is optimised for
CPU-side MoE inference, but the evidence is genuinely split:

- On a Xeon E5-2683 v4 — **Broadwell, AVX2, no AVX-512, the closest available
  hardware analog** — it showed a 1.7–1.9× speedup on Mixtral-8x7B.
- On a Ryzen 7 5800X with Qwen3 MoE it *regressed*, running ~1.5× slower
  generation and ~2× slower prefill than mainline (ik_llama.cpp issue #1699).
- Its stated optimisations lean on AVX-512, which Broadwell lacks.

**Blocking unknown: does `ik_llama.cpp` support `rpc-server`?** Unconfirmed by
research. If it does not, the fork is disqualified regardless of speed, because
the whole architecture depends on RPC. Check this against its source before
spending any time benchmarking it.

Default to mainline. Treat the fork as an optimisation to test after the cluster
works, not a choice to make up front.

### Networking

RPC mesh runs on **raw LAN IPs** over the local switch. Tailscale carries SSH
and the web frontends only. WireGuard encryption and reduced MTU on the
per-token hot path is pure loss with no security benefit on an isolated LAN.

Per-hop activation payloads are single-digit KB, so 6 hops cost well under a
millisecond. **The network is not the bottleneck** — memory bandwidth is.

### OS and provisioning

**Debian 12 headless.** No GUI, no display manager, no CUDA. Build llama.cpp
once with AVX2 enabled; distribute binaries. Versions must match exactly across
nodes or the RPC protocol mismatches — never build per-node.

Disks vary in size and type, which breaks block-level `dd` cloning. Use a Debian
preseed for unattended base install plus one idempotent `setup.sh`.

**Identity hygiene** — on first boot every node must regenerate `/etc/machine-id`,
regenerate SSH host keys, and clear `/var/lib/tailscale` before joining. Cloned
auth state collides.

## Missing Link

An async job runner that makes slow inference useful. Submit work, collect
results later.

**Workloads: document summarisation and report drafting.** Both are tasks where
the document was going to sit in a queue anyway, so a multi-minute or overnight
turnaround costs nothing. Summarising a folder of sensitive records overnight is
worth real staff hours, and no data leaves the building to do it.

The work happens entirely on hardware the organisation already owns. Cloud is
not part of this design — no hybrid architectures, no framing local inference as
a preprocessing step for something else.

### Scope

Deliberately minimal — Missing Link is a demonstrator, not a product.

- Submit a job (document + task template), receive an ID
- Persistent queue surviving restart (SQLite; jobs outlive any single run)
- Sequential execution against llama-server, with progress visible
- Retrieve results by ID; simple web view listing jobs and states
- Per-job record of wall-clock time, tokens generated, and time-to-first-token

**Out of scope:** authentication, multi-tenancy, job priorities, retries with
backoff, distributed queue workers.

## Validation

Measured on hardware, not estimated. Every performance claim in the blog must
trace to one of these.

From the first node up:
- `lscpu | grep -E 'avx2|Model name'` — confirm AVX2
- `free -h` and `dmidecode -t memory` — actual RAM and free DIMM slots
- `llama-bench` single-node with a small Q4 model → real tok/s and effective GB/s

**Thread count must be tuned separately for prefill and generation.** They are
bounded by different things: prefill is compute-bound and wants every core;
generation is bandwidth-bound and typically peaks *well below* core count,
because past a point extra threads add memory-controller contention rather than
bandwidth. One report found generation optimal at 24 threads on a 96-thread
machine. Sweep both independently rather than setting one thread count.

Success criteria:

| Stage | Passes when |
|---|---|
| Single node | Qwen3-4B holds a coherent multi-turn conversation via `curl` |
| Sharded | Qwen3-30B-A3B serves from all 7 nodes; TTFT and tok/s recorded |
| Missing Link | A real document is summarised end-to-end through the queue |

**Both metrics always.** Time-to-first-token (prefill, compute-bound) and
tokens/sec (generation, bandwidth-bound) are separate numbers with separate
causes. TTFT on a realistic multi-thousand-token document is the number that
decides whether the GPUs come back.

Also measure **concurrent seats**: throughput at 1, 2, 4 and 7 simultaneous
requests. This is the claim that pipeline sharding multiplies seats, and it
needs evidence.

## Risks

1. **CPU prefill may be worse than expected.** Broadwell without AVX-512 on a
   4,000-token document could exceed a minute before first token. Measure early;
   it drives the GPU revisit decision and possibly the choice of workload.
2. **RPC pushes weights over the wire on every start.** 32 GB over gigabit is
   several minutes per restart, which makes iteration painful. `rpc-server -c`
   enables a local file cache — verify on the built version before accepting
   slow iteration as normal.
3. **Swap death.** With models sized to pooled RAM, any overshoot pushes a node
   into swap and collapses throughput. The 15% headroom exists for this; disable
   swap on worker nodes so failures are loud rather than silent.

## Out of scope

- Security hardening (isolated LAN behind Tailscale)
- High availability, node failure recovery
- Fine-tuning or training
- Blog prose and publishable artifacts
- GPU acceleration (unless the TTFT revisit condition triggers)

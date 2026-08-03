# Homogenous Inference Cluster — Design

**Date:** 2026-08-03
**Status:** Approved for planning

## Purpose

Build a 7-node LLM inference cluster from surplus school hardware, running a
genuinely capable model with no data leaving the building. The cluster is a
working demonstrator for a blog post arguing that organisations with
data-sovereignty constraints can repurpose idle compute for private inference
instead of sending sensitive data to hosted APIs.

This spec covers the **cluster build only**. Blog prose, benchmark tables, and
publishable artifacts are explicitly out of scope.

## Hardware

| Component | Per node | × 7 nodes |
|---|---|---|
| CPU | 5th-gen Intel i5 (Broadwell) | 7 |
| GPU | 1× NVIDIA Quadro P600, 2 GB GDDR5 | **14 GB VRAM** |
| System RAM | 6–8 GB DDR3 | **42–56 GB** |
| Storage | Mixed SSD + HDD, varying sizes | — |
| Network | Gigabit ethernet, same switch | — |

**Pooled memory: ~56–70 GB.**

### Bandwidth is the binding constraint

The P600 (GP107, compute capability 6.1) has **64 GB/s** memory bandwidth on a
128-bit bus. DDR3 system RAM is roughly 25 GB/s. Token generation speed is
bounded almost entirely by how many bytes must be read per token, not by compute.

Two consequences drive every decision below:

1. **Pipeline sharding buys capacity, not speed.** For a single request, nodes
   execute sequentially — time-per-token ≈ the time for one GPU to read the
   entire model. Splitting a dense model across 7 nodes does not make it faster.
2. **MoE decouples capability from bandwidth.** An MoE model must *store* all
   parameters but only *reads* the routed experts per token. This is the entire
   reason the cluster is viable, and the core of the blog's argument.

### Quantisation note

Quantised GGUF does **not** double in size on Pascal. llama.cpp's CUDA backend
keeps Q4/MXFP4 weights quantised in VRAM and dequantises per-tile during the
matmul, using DP4A integer instructions (present on CC 6.1). The "fp16 only"
constraint applies to FP8 tensor-core paths in vLLM/TensorRT, not llama.cpp.

## Model ladder

Three rungs, built in sequence. Each is independently demoable.

### Rung 1 — Proof (single node)

**Model:** Qwen3-4B Q4_K_M (~2.5 GB)

Validates the full OS → driver → llama.cpp → clone → Tailscale pipeline on one
machine before any distributed complexity. Fits in 2 GB VRAM with modest CPU
offload. Success criterion: coherent multi-turn chat from a single node.

### Rung 2 — The demo (VRAM-sharded)

**Model:** gpt-oss-20b, MXFP4 (~12.9 GB, 21B total / 3.6B active)

Sharded across all 14 GB of pooled VRAM via llama.cpp RPC. Only 3.6B active
parameters means genuinely usable generation speed. This is what gets demoed
live so the audience is not watching a spinner.

**The fit is tight and the fallback must not be CPU offload.** 14 GB nominal,
minus ~250 MB CUDA context per GPU (~1.75 GB), leaves ~12.25 GB. A 12.9 GB model
plus KV cache does not fit. KV cache is not free on 2 GB cards: gpt-oss-20b at
8k context costs roughly 0.4 GB, and it lives on the node holding those layers,
not spread evenly.

Fallback is therefore **a smaller quant, not CPU offload** — CPU offload depends
on the same unproven RPC mechanism as rung 3 (see Risks). In order of preference:

1. gpt-oss-20b at 8k context, reduced KV via `-ctk q8_0 -ctv q8_0`
2. Qwen3-30B-A3B at IQ3_XXS (~12 GB)
3. Qwen3-14B Q4_K_M (~9 GB) — dense, slower, but certain to fit

### Rung 3 — The thesis (VRAM + system RAM)

**Model:** gpt-oss-120b, MXFP4 (~63 GB, 117B total / 5.1B active)

Attention, KV cache, routers and norms in the 14 GB of VRAM; routed expert FFN
tensors in the ~49 GB of pooled system RAM. Expected to be slow (single-digit
tok/s) but this is the point: frontier-class open-weights capability on hardware
with zero acquisition cost. Extrapolates directly to the blog's Kimi K3 /
550 GB argument.

Success criterion is **capability, not speed** — the output should be visibly
better than anything a single node can produce.

#### This rung has two possible architectures, and a test decides which

`--n-cpu-moe` is applied by the process that loads the model, against *its own*
CPU backend. Under RPC the master does not own the workers' system RAM — it sees
a set of opaque remote backends. There is no path for the master's
`--n-cpu-moe` to mean "put layer 40's experts in node 5's DDR3."

**Architecture A (hybrid, preferred).** Run *two* `rpc-server` instances per
node — one bound to CUDA, one to CPU — giving the master 14 backends. Then use
`--override-tensor` regexes to force attention onto the CUDA backends and expert
tensors onto the CPU backends. Viable only if `-ot` patterns can name an RPC
device. **This is unproven and is the gate test below.**

**Architecture B (CPU-only RPC, fallback).** If `-ot` cannot target RPC devices,
drop the GPUs from rung 3 entirely and run 7 CPU-backend `rpc-server`s over the
~49 GB of pooled system RAM. Slower, but it still makes the argument and still
extrapolates to Kimi K3. Model target shrinks to a ~40 GB-class MoE:
GLM-4.5-Air Q4, or Qwen3-30B-A3B at Q8.

gpt-oss-120b (~63 GB) is only reachable under Architecture A. Do not commit to
it as the rung 3 model until the gate test passes.

## Architecture

### Why llama.cpp RPC, not Exo

Exo's value proposition is heterogeneous device discovery and MLX/Apple support.
This cluster is 7 identical Debian + CUDA boxes, so neither applies. Forking Exo
would be work spent arriving where llama.cpp already is — and llama.cpp is the
stack already proven to work on this hardware.

### Components

```
                    ┌─────────────────────────────┐
   Tailscale  ─────▶│  MASTER (node 1)            │
   (SSH + GUI)      │  ├─ llama-server            │
                    │  │   (OpenAI-compatible API)│
                    │  ├─ Open WebUI              │
                    │  └─ rpc-server (local GPU)  │
                    └──────────┬──────────────────┘
                               │ raw LAN IPs, gigabit
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
            ┌─────────┐  ┌─────────┐   ┌─────────┐
            │ node 2  │  │ node 3  │ … │ node 7  │
            │rpc-server│ │rpc-server│  │rpc-server│
            │ P600 2GB│  │ P600 2GB│   │ P600 2GB│
            └─────────┘  └─────────┘   └─────────┘
```

**Unit boundaries:**

| Unit | Does | Depends on |
|---|---|---|
| `rpc-server` (×7) | Holds a layer range, executes forward pass on its shard | CUDA driver, local GGUF cache |
| `llama-server` (master) | Loads GGUF, assigns layers, serves OpenAI API | RPC endpoints |
| Open WebUI (master) | Chat frontend | llama-server API |
| `setup.sh` + preseed | Provisions a bare node to cluster-ready | Debian netinst media |

Each is independently testable: `rpc-server` via `llama-bench` against a single
endpoint; `llama-server` via `curl` to `/v1/chat/completions`; provisioning via a
clean VM run.

### Networking

**RPC mesh uses raw LAN IPs on the local switch.** Tailscale carries SSH access
and the Open WebUI endpoint only. Putting WireGuard encryption and reduced MTU on
the per-token hot path is pure loss with no security benefit — the cluster is
already on an isolated LAN.

Activation payloads per hop are small (hidden_dim × 2 bytes, single-digit KB), so
6 hops cost well under a millisecond. The network is **not** the bottleneck for
batch-1 generation; memory bandwidth is.

### OS and drivers

**Debian 12 headless, NVIDIA 535 branch** (Debian's `nvidia-driver`).

Pascal support is being wound down, but not yet: the **580** branch is the last
to support Maxwell/Pascal/Volta, and support only drops at **590**. The 470
legacy branch is for *Kepler* and is the wrong choice here. Debian 12's 535 is
comfortably within Pascal's support window with headroom to spare — pin it and
do not chase newer.

llama.cpp must be built with `CMAKE_CUDA_ARCHITECTURES=61` (GP107 is compute
capability 6.1). Since binaries are built once and distributed fleet-wide,
getting this wrong costs all seven nodes at once — verify the built binary sees
the GPU before distributing.

No GUI, no display manager: every megabyte of VRAM consumed by a desktop is a
megabyte unavailable to the model.

### Provisioning: scripted, not imaged

Disks vary in size and type across the fleet, which breaks block-level `dd`
cloning. Instead: a Debian preseed for unattended base install, then a single
idempotent `setup.sh` that installs drivers, builds nothing (see below), and
registers the node.

**Identity hygiene** — any cloned or scripted node must, on first boot:
- regenerate `/etc/machine-id`
- regenerate SSH host keys
- clear `/var/lib/tailscale` before joining (cloned auth state collides)

**Build once, distribute binaries.** llama.cpp version must match *exactly*
across all nodes or the RPC protocol mismatches. Build on one machine, ship the
binaries via the provisioning script. Never build per-node.

### Frontend

Open WebUI on the master, pointed at llama-server's OpenAI-compatible endpoint,
lightly skinned. Zero build effort for a polished result. A cluster-status panel
showing which node holds which layer range is the screenshot the blog needs.

## Risks to retire early

Ordered by likelihood of derailing the build.

### Gate test — run before provisioning anything else

**Can `--override-tensor` patterns name an RPC device?**

Two nodes, two `rpc-server` instances each (one CUDA, one CPU), a small MoE, and
an `-ot` regex attempting to route expert tensors to a specific remote CPU
backend. This single question decides whether rung 3 is Architecture A or B, and
therefore which model the whole cluster is built around.

Running it costs two machines and an afternoon. Skipping it risks imaging seven
machines for the wrong architecture.

### Remaining risks

1. **RPC pushes weights over the wire on every start.** The master distributes
   tensors to backends; 63 GB over gigabit is minutes per restart. `rpc-server -c`
   enables a local file cache — verify it works on the built version before
   accepting long iteration cycles as normal.
2. **CUDA architecture flag.** llama.cpp built without
   `CMAKE_CUDA_ARCHITECTURES=61` will not use the P600. Confirm on the build
   machine before distributing binaries fleet-wide.
3. **Pascal driver install.** Confirm Debian 12's `nvidia-driver` (535) installs
   cleanly and `nvidia-smi` sees the P600 before anything else is attempted.
   Pascal is supported through the 580 branch, so this should be uneventful — but
   it is the cheapest possible thing to verify first.
4. **Rung 2 VRAM fit.** ~12.25 GB usable against a 12.9 GB model. Fallbacks are
   quant reductions, not CPU offload — see rung 2.

## Validation

Measured on hardware, not estimated. The first node up produces:

- `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv`
- `lspci | grep -i vga` — confirm card count per node
- `llama-bench` single-GPU with a small Q4 model — yields real tok/s and
  effective GB/s, which calibrates every performance claim

Per-rung success criteria:

| Rung | Passes when |
|---|---|
| 1 | Single node holds a coherent multi-turn conversation via `curl` |
| 2 | gpt-oss-20b serves from all 7 nodes at usable interactive speed through Open WebUI |
| 3 | gpt-oss-120b generates coherent output; speed recorded, not constrained |

## Out of scope

- Security hardening (cluster sits behind Tailscale on an isolated LAN)
- High availability / node failure recovery
- Multi-user concurrency and request queueing
- Blog prose, benchmark tables, publishable artifacts
- Fine-tuning or training of any kind

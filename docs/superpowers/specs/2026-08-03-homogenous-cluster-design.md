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

**Revised 2026-08-10 — the hardware is far better than originally assumed.**
Machines are ECC-equipped (so likely Xeon, not i5) with **4 × 32 GB DDR4-2400
per node**. Two nodes confirmed by inspection; the rest are unverified.

| Component | Per node | × 7 nodes |
|---|---|---|
| CPU | Unconfirmed — ECC implies Xeon. Verify. | 7 |
| System RAM | **128 GB DDR4-2400 ECC** | **~896 GB** |
| Storage | Mixed SSD + HDD, varying sizes | — |
| Network | Gigabit ethernet, same switch | — |
| GPU | 1× Quadro P — display only, unused for compute | — |

**Unverified and load-bearing:**
- CPU model, core count, and channel count. Bandwidth is the binding constraint
  and quad-channel DDR4-2400 (~76.8 GB/s) is roughly **3× the original DDR3
  estimate**. Hex-channel would be ~115 GB/s.
- Whether all 7 nodes carry 128 GB, or only the two inspected.
- Whether the CPU has AVX-512 (Xeon often does; it would materially help
  prefill, which is compute-bound).

Run on the first node and record before any model decision is final:

```bash
lscpu | grep -E 'Model name|^CPU\(s\)|Socket|Thread|Core|avx512'
sudo dmidecode -t memory | grep -E 'Size|Speed|Locator|Rank' | head -60
sudo dmidecode -t processor | grep -E 'Version|Core Count|Thread Count'
```

**Headless is still correct**, but for a different reason than originally
written. The GPU is present for display, so VRAM is no longer the argument —
running headless simply avoids spending RAM and CPU cycles on a desktop that
nothing will ever look at.

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
32B active is tractable on system RAM and a 70B dense model is not.

**2. Pipeline sharding multiplies seats, not speed.**

For a single request, nodes execute sequentially — 7 nodes performs like one
node with 7× the RAM. With *concurrent* requests, each node processes a
different request's layers simultaneously, so aggregate throughput scales with
node count while per-seat speed stays flat.

The design target is therefore **a small number of slow seats**, not one fast
one. Missing Link exists to make that framing useful rather than apologetic.

## Memory budget

Two constraints bind independently. **Both must hold.**

**1. Pooled headroom: usable = (pooled RAM − 1 GB/node for OS) × 0.85.**

The 15% headroom covers KV cache growth, activation buffers, RPC transfer
buffers and page-cache pressure. Configurations that fit only marginally are not
specced.

**2. Per-node hard ceiling: no node may hold more than ~75% of its physical RAM.**

Exceeding this aborts the client with `"Remote RPC server crashed or returned
malformed response"`; the server logs `"Null buffer for tensor passed to
init_tensor function"`. This is an unfixed llama.cpp RPC limitation
(ggml-org/llama.cpp #15055) with no workaround other than staying under it.

This is a *per-node* limit and does not compose with the pooled figure — it
binds separately and more tightly. Check any candidate model against both.

At 128 GB/node × 7:

| Constraint | Limit |
|---|---|
| Pooled: (896 − 7) × 0.85 | ~756 GB |
| Per node: 128 × 0.75 × 7 | **~672 GB** ← binds |

**~672 GB of usable model budget.** That is a different project from the one
originally specced against 41 GB.

### RAM is already the win

The fleet arrived with 4 × 32 GB DDR4-2400 ECC per node. No upgrade is needed —
these machines were decommissioned with server-grade memory still installed,
which is the blog's point in miniature: the expensive component was already
bought, and it is sitting idle.

## Target model

### The cluster now has to earn its existence

With 128 GB per node, **a single node holds 96 GB of model.** That is enough for
gpt-oss-120b (63 GB) with room to spare. The seven-node cluster is not justified
by anything under ~96 GB — one machine would do it, faster, with no RPC overhead
at all.

This sharpens the argument rather than weakening it. The cluster's purpose is
now unambiguous: **run models that no single machine in the building could hold,
at any speed.** That is a much cleaner claim than "run a medium model slowly."

### Candidate ladder

Estimates assume quad-channel DDR4-2400 (~76.8 GB/s) at 55% real efficiency.
**All figures are arithmetic pending measurement.** Bytes/token ≈ active params
× bytes-per-weight.

| Model | Size | Active | Nodes needed | Est. tok/s | Role |
|---|---|---|---|---|---|
| gpt-oss-120b MXFP4 | 63 GB | 5.1B | **1** | ~15–20 | Single-node baseline; the speed reference |
| Qwen3-235B-A22B Q4 | ~140 GB | 22B | 2 | ~3–4 | First model needing the cluster |
| DeepSeek-V3 Q4 | ~400 GB | 37B | 5 | ~2 | Frontier-class, genuinely slow |
| **Kimi K2 Q4** | **~550 GB** | **32B** | **7** | **~2** | **The thesis** |

Kimi K2 at ~550 GB fits the ~672 GB budget with real headroom. This is the model
originally described as the aspiration, and it is now reachable rather than
extrapolated.

### The comparison that makes the blog

Running **gpt-oss-120b on one node** and **Kimi K2 across seven** on the same
hardware gives a direct, measured statement: *this is what one salvaged desktop
does, and this is what seven do together — and the second is a model class the
organisation could not otherwise touch at all.* Both numbers come from the same
room, the same afternoon, and no cloud account.

### KV cache

GQA keeps this small. For Qwen3-class models, ~96 KB/token: ~3 GB at 32k. Even
across the larger models KV cache is not the constraint — **prefill time is.**
With 128 GB/node there is ample room to raise context substantially.

### What must be measured before committing

1. **Actual memory bandwidth** — `llama-bench` on one node yields effective
   GB/s, which recalculates every row above.
2. **Whether all 7 nodes have 128 GB.** Two are confirmed; five are not.
3. **Prefill throughput**, especially if the CPU has AVX-512.

### Stepping stone

**Qwen3-4B Q4_K_M (~2.5 GB), single node.** Validates OS → llama.cpp →
provisioning → Tailscale before any distribution. Success: coherent multi-turn
chat via `curl`.

### Changing the model later costs nothing structural

The architecture does not depend on which model runs. Swapping targets changes
only the GGUF path and the `--tensor-split` values, so there is nothing to
design in advance for a different choice — pick on measured evidence once the
hardware facts are in.

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

Exo is actively maintained (v1 rewrite Dec 2025, 16 releases through Apr 2026)
and could *structurally* do this job — it pools RAM across nodes, splits layers
memory-proportionally, and has native Qwen3-MoE support including a model card
for this exact model. It is not a capability gap. It is the wrong tool anyway:

- **MLX is now the only inference engine.** tinygrad, the former non-Apple
  fallback, was deleted entirely in the v1 rewrite. Linux CPU exists as an
  `MlxCpu` backend, but the project's own `PLATFORMS.md` files it under
  **"Planned"**, not Tier 1. Tier 1 is Apple Silicon only.
- **No CPU optimisation work exists.** Searching 2,353 commits for AVX or NUMA
  returns nothing, and the repo publishes no x86 or CPU-cluster benchmarks.
- **It cannot consume GGUF.** It requires MLX-format safetensors, so the chosen
  Q8_0 asset would need re-sourcing at a different quant with a different
  accuracy and speed profile.
- **Its strongest feature is Mac-only hardware.** RDMA tensor parallelism needs
  Thunderbolt 5 on macOS 26.2+. On gigabit Ethernet it falls back to the same
  untested socket-ring path.

Revisit only if Linux CPU moves to a tested tier with published non-Apple
benchmarks, or a documented CPU-optimised path ships. Neither is underway.

#### What Exo teaches us about the synchrony problem

**Exo has not solved decode-time synchrony either.** It queues sends and flushes
them asynchronously during *prefill*, letting compute run ahead of transmission.
But during generation it calls `mx.eval()` immediately around every send and
receive, per layer, per token — fully synchronous, exactly llama.cpp's
constraint.

A well-resourced project with a full rewrite and custom RDMA transport still
eats synchronous per-layer round trips during generation. This is a hard
problem, not a llama.cpp deficiency — and it tempers expectations for PR #18626:
prefill gains look likely, decode gains much less certain.

#### Techniques worth borrowing

- **Separate control plane from data plane.** Exo runs discovery and
  orchestration over Zenoh while per-token tensors go over a raw TCP ring. If
  Missing Link adds coordination traffic, keep it off the inference hot path.
- **Prefill send-queueing.** Defer sends during prefill and flush them
  asynchronously so compute overlaps transmission. Prior art for #18626.
- **Memory-proportional layer allocation** (largest-remainder rounding, floor of
  one layer per node) — a clean reference for computing `--tensor-split` should
  the fleet ever become memory-heterogeneous.
- **Bandwidth-aware placement is unsolved.** Exo's own code carries a
  `TODO: Profile and get actual connection speeds`; splits are capacity-
  proportional only. Do not expect quick wins there.

**RPC is immature, and upstream says so.** Its README states the backend is "in
a proof-of-concept development stage… fragile and insecure. Never run the RPC
server on an open network or in a sensitive environment!" This independently
validates keeping the mesh on raw LAN IPs — it is a security requirement, not
only a latency optimisation.

It remains the right choice: it is actively maintained, and it is the only
option for homogeneous CPU-only llama.cpp clustering. But treat it as fragile.

**Pin to a known-good build; do not track `master`.** `--tensor-split` over RPC
has broken across releases before (#21006, fixed in #21030/#26500). Since
binaries are built once and distributed fleet-wide, a bad pin costs all seven
nodes at once.

**Watch PR #18626** (async/pipelined RPC). It is maintainer-authored and active.
When it lands, re-benchmark and consider moving the pin — it is the one upstream
change likely to materially improve this cluster's throughput.

**RPC pools memory; it does not scale compute.** Maintainer-confirmed. Nodes
wait strictly sequentially — thread count controls intra-node parallelism only
and does nothing for the inter-node wait. This is the same point CLAUDE.md makes
as "sharding multiplies seats, not speed," now corroborated upstream.

#### Key operational details

- **`--tensor-split`** works over RPC and is the supported mechanism for uneven
  node RAM. Weights are relative, ordered as the `--rpc host:port,...` list plus
  local devices. Default auto-split allocates by *self-reported* free memory,
  which has known reporting bugs — **set `--tensor-split` explicitly** against
  measured RAM rather than trusting auto-split.
- **`--device` flags must come after `--rpc`** or the device list misresolves.
- **`--split-mode row` has no effect over RPC.** Pipeline layer-splitting only;
  true tensor parallelism is local-multi-GPU only.
- **`rpc-server -t` defaults to half the logical cores**, not all of them. On a
  node this silently halves throughput. Set it from `nproc`.
- **Version mismatch fails loudly**, not silently: `negotiate_hello()` rejects
  the connection and logs `"RPC server version mismatch"`. Good — it means a
  mismatched node cannot silently corrupt output.

#### Protocol overhead

A figure of "30–55% RPC overhead" circulates (issue #22850) and **should not be
treated as measurement.** The issue was LLM-authored by the submitter's own
admission, and a maintainer closed it in under two hours for violating the
repository's AI-usage policy — with no technical review either way. Its
benchmark also compared against a deliberately crippled local baseline (a GPU on
a single PCIe v1 lane), which inflates the apparent RPC penalty.

Critically, it was measured on build `b9033`, which **predates the fix for the
main problem it identified by 32 minutes.**

The three mechanisms it named, assessed against current source:

| Mechanism | Real? | Status |
|---|---|---|
| Graph metadata reserialised each execution | Yes | **Fixed** — #22701 (May 2026) adds a `graph_uid` fast path; unchanged graph shapes send a tiny `GRAPH_RECOMPUTE` instead. Hardened by #23273. |
| Synchronous blocking calls, no pipelining | Yes | **Unfixed in mainline, fix open and active** — PR #18626 by rgerganov (the RPC author), `mergeable`, last updated Aug 2026. A community PR (#24675) measured CPU-only pipelined prefill improving 37.96 → 59.84 t/s across two workers. |
| `HASH_THRESHOLD` = 10 MiB | Yes | **Likely a misdiagnosis.** During generation, `SET_TENSOR` carries KV-cache writes and fresh activations — new bytes every token. Hashing would almost never dedup while adding a round-trip and a full hash pass. Lowering it would plausibly make generation *slower*. Do not patch it. |

**The overhead is probably much smaller here than the GPU-derived numbers
suggest.** Those came from a setup where per-token compute is microseconds, so a
millisecond-scale round-trip dominates. On CPU-bound MoE inference, per-token
compute is tens to hundreds of milliseconds against a sub-millisecond LAN
round-trip — a far smaller ratio. This is reasoning, not measurement.

**Measure it directly, before provisioning anything.** Run `llama-bench`
locally, then again against `--rpc localhost:PORT` on the same machine with the
same model. That isolates pure protocol cost with the network removed entirely,
needs one machine, and converts this whole section from inference to fact.

Prefill may fare worse than generation: varying prefill chunk sizes change the
graph shape and so defeat the `graph_uid` fast path. Measure both.

**Keep batch shapes constant across steps** where possible — that is what keeps
`graph_uid` stable and the fast path active. This is usage discipline, not a
flag.

#### Cache behaviour

`rpc-server -c` caches to `$LLAMA_CACHE` or `~/.cache/llama.cpp/rpc`. Tensors
≥10 MiB are content-hashed and skipped on retransfer; anything smaller is always
re-sent. Known rough edges: cache files are bare content hashes with no model
association, so selective cleanup is impossible — wipe the whole directory.
Loading is also not parallel across nodes even when every node has a full cache
(#16434, open).

There is a report of `rpc-server` going `<defunct>` when run as a background
service *without* `-c` (#13185). Run with `-c` regardless.

**`ik_llama.cpp` is a maybe, not a recommendation.** It **does** support
`rpc-server` (confirmed: `examples/rpc/rpc-server.cpp` present, same TCP
architecture as upstream), and it is actively maintained. But the speed evidence
is genuinely split:

- On a Xeon E5-2683 v4 — **Broadwell, AVX2, no AVX-512, the closest available
  hardware analog** — it showed a 1.7–1.9× speedup on Mixtral-8x7B.
- On a Ryzen 7 5800X with Qwen3 MoE it *regressed*, running ~1.5× slower
  generation and ~2× slower prefill than mainline (ik_llama.cpp issue #1699).
- Its stated optimisations lean on AVX-512, which Broadwell lacks.

Its RPC code is at rough parity with mainline — it carries the same `graph_uid`
fast path and the same synchronous blocking limitation, with no independent work
on the protocol. Its investment is in CPU compute kernels, which is where any
benefit would come from.

Default to mainline. Treat the fork as an optimisation to A/B after the cluster
works, not a choice to make up front.

**Rejected: prima.cpp and distributed-llama.** prima.cpp's original repo is gone
(404); the live continuation is `OpenCPIL/prima.cpp`, but it shows **no MoE
support** — disqualifying — and its ring/prefetch design targets disk-bound
consumer devices, solving a different problem. distributed-llama is maintained
and CPU-focused but uses tensor parallelism with its own weight format, meaning
an all-reduce per layer per token across 7 nodes on gigabit — plausibly *worse*
than pipeline sharding here — plus abandoning the GGUF/Open WebUI stack.

### Networking

RPC mesh runs on **raw LAN IPs** over the local switch. Tailscale carries SSH
and the web frontends only. WireGuard encryption and reduced MTU on the
per-token hot path is pure loss with no security benefit on an isolated LAN.

Per-hop activation payloads are single-digit KB, so 6 hops cost well under a
millisecond. **The network is not the bottleneck** — memory bandwidth is.

### OS and provisioning

**Debian 12 headless.** No GUI, no display manager, no CUDA.

#### Preseed

Disks vary in size and type, which breaks `dd` cloning. Use a preseed for
unattended install. The idiomatic way to handle varying disks is to **leave
`partman-auto/disk` unset** — with one disk the installer uses it whatever it is
called. For nodes with more than one disk, Debian's own dynamic pattern:

```
d-i partman/early_command \
    string debconf-set partman-auto/disk "$(list-devices disk | head -n1)"
```

Never hardcode `/dev/sda`; kernel disk naming is not deterministic.

**Set `d-i apt-setup/non-free-firmware boolean true`.** This is a new apt
component in Bookworm, and on recycled hardware missing NIC firmware is a
realistic way to end up with a node that installs but cannot reach the network.

Bake the preseed into a remastered netinst ISO (zero typing per machine, no LAN
dependency during install). Fallback if the repack tooling misbehaves: stock USB
plus one typed line per node —
`auto=true priority=critical preseed/url=http://<host>:8000/preseed.cfg`.

#### Identity hygiene

Every node must, before joining anything:

```bash
# machine-id
truncate -s 0 /etc/machine-id && rm -f /var/lib/dbus/machine-id
systemd-machine-id-setup
ln -sf /etc/machine-id /var/lib/dbus/machine-id

# SSH host keys
rm -f /etc/ssh/ssh_host_* && ssh-keygen -A && systemctl restart ssh

# Tailscale state
systemctl stop tailscaled
rm -rf /var/lib/tailscale/tailscaled.state /var/cache/tailscale
systemctl start tailscaled
```

**Why machine-id matters specifically:** systemd-networkd derives its DHCP
client-ID from it. Duplicate machine-ids across the fleet cause nodes to collide
on a single DHCP lease, presenting as intermittent network flapping — invisible
when testing one node, miserable to debug across seven. (systemd #9609, #9228.)

Duplicate SSH host keys let any node cryptographically impersonate any other.
Duplicate Tailscale state makes nodes race on one tailnet identity.

Use `ssh-keygen -A` rather than `dpkg-reconfigure openssh-server` — the latter
can fail in headless contexts.

#### Tailscale

Reusable, pre-approved, **non-ephemeral** auth key (ephemeral keys remove nodes
on disconnect, which is wrong for permanent servers):

```bash
tailscale up --auth-key=file:/path/to/key --hostname="$(hostname)" \
             --ssh --accept-dns=false
```

**Never pass `--advertise-routes` on any node.** Tailscale only owns
`100.64.0.0/10` and does not touch existing LAN routing, so RPC traffic stays on
raw LAN IPs automatically. Advertising the LAN subnet is the one way to
accidentally pull the hot path onto WireGuard.

Pass the key via `file:` so it does not land in shell history or the process
list.

#### Memory tuning

```bash
swapoff -a
sed -i '/\sswap\s/s/^/#/' /etc/fstab
systemctl --type swap          # then mask each unit found
```

- **`vm.overcommit_memory=1`** — llama.cpp has hit overcommit-related OOM kills
  directly (ggml-org/llama.cpp #22629).
- **`--mlock`** plus `* soft/hard memlock unlimited` in
  `/etc/security/limits.conf` to pin weights in RAM.
- **Transparent hugepages: leave alone.** Debian 12 defaults to `madvise`, and
  benchmarking found `madvise` slightly *faster* than `always` for llama.cpp CPU
  inference. Assert the default; do not build tooling to change it.

#### Building and distributing llama.cpp

Build once on a fleet node, `rsync` binaries to the rest. Versions must match
exactly across nodes or the RPC protocol mismatches — never build per-node.

Build with `-DLLAMA_CURL=OFF` (models are provisioned by script, not downloaded
by llama.cpp).

**Build on a fleet node, not in a newer container or chroot.** glibc is
forward-incompatible: binaries built against a newer glibc fail with
`GLIBC_2.XX not found` on older systems. Building natively on identical Debian 12
makes this a non-issue — add an `apt-cache policy libc6` version assertion to
`setup.sh` as cheap insurance.

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

### Chunking: map-reduce, decided on evidence

Documents are chunked and summarised hierarchically — summarise each chunk, then
summarise the summaries — rather than stuffed into one large context.

**Why not a bigger context window:**

- **"Lost in the middle" is not fixed by more context.** Liu et al.
  (arXiv:2307.03172) measured accuracy falling from 75.8% to 53.8% as relevant
  material moved to the middle of context, at times *below* the no-context
  baseline. The 16K extended-context model showed nearly identical position
  bias to the base model. It is a separate failure mode from context capacity.
- **CPU prefill degrades badly with length.** Measured on a Ryzen 7950X,
  prefill fell from 394 t/s at 512 tokens to 163 t/s at 32K — a ~58% loss, as
  attention becomes memory-bandwidth-bound. This cluster lives in exactly that
  regime, so long contexts are doubly expensive here.
- **Stuffing does not measurably win.** SummHay (arXiv:2407.01370) found
  full-context 37.8 vs chunked 36.0 on ~100K-token sets — no decisive gap.

**Why map-reduce and not refine:** BooookScore (arXiv:2310.00785) tested both on
book-length text. Hierarchical beat incremental for every model (Claude 2:
91.1 vs 78.6; Mixtral-8x7B: 81.5 vs 64.5; LLaMA 2 failed refine entirely).
Refine is also strictly sequential, so on a slow cluster it is far slower in
wall-clock — one report showed hours versus ~27 minutes on comparable input.

**Chunk size is not worth tuning.** BooookScore found map-reduce quality largely
insensitive to it, unlike refine. Start at ~4K tokens with ~10% overlap and
leave it alone — this keeps prefill in its efficient range.

The large context window remains configured (KV cache is cheap at 128 GB/node)
but is *capacity for headroom*, not the summarisation strategy.

### Evaluating output quality

The blog needs a defensible quality claim, and **no existing leaderboard
compares locally-run llama.cpp models against frontier models on
summarisation.** Producing one is a genuine contribution — worth stating as a
gap being filled rather than implying precedent.

- **Datasets:** GovReport (19,463 US government reports with expert summaries —
  thematically ideal) and BillSum (23,455 legislative bills, **CC0**, so example
  outputs can be republished freely).
- **Do not use ROUGE alone.** Lexical overlap only, swings up to 40 points on
  reference choice, poor correlation with human judgement.
- **Score two independent axes, unblended:** factual consistency (reference-free,
  QAFactEval/SummaC style — does the summary claim anything absent from the
  source) and a quality rubric (SummEval's coherence / consistency / fluency /
  relevance, scored G-Eval style).
- **Report spread, not just means** — a sample of 15–30 documents does not
  support a bare average.
- **Report TTFT and generation time alongside quality.** The argument is
  quality-parity *at an honest latency cost*, not quality-parity alone.
- Cite the Vectara Hallucination Leaderboard for context — ~7,700 documents,
  100+ models including open-weight, scored on factual consistency.

**Caveat worth stating in the post:** these datasets are old and widely mirrored,
so frontier models may have memorised the reference summaries. Reference-free
factual-consistency metrics sidestep this; overlap metrics do not.

### Scope

Deliberately minimal — Missing Link is a demonstrator, not a product.

Research confirmed **no mature self-hosted project already does this** — point
at llama.cpp, queue overnight, summarise long documents. The nearest standalone
tool has 624 stars and is semi-stale. Building it is filling a real gap, not
reinventing something.

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

**Set thread count from `nproc`, then sweep if the CPU turns out many-core.**

`rpc-server -t` defaults to *half* the logical cores, so leaving it unset
silently halves the fleet's compute. Always set it explicitly. This is the most
likely thread-related mistake here by far.

Whether *all* cores is optimal depends on the CPU, which is still unconfirmed.
There is a documented case of generation peaking at **24 threads on a 96-thread
machine** — past some point, extra threads contend for bandwidth the memory
controller cannot deliver, adding coordination cost without throughput. That
effect requires enough cores to saturate the controller in the first place.

So:
- **Under ~24 threads** — use all of them and move on.
- **Over ~24 threads** (plausible on a Xeon) — sweep thread count for
  *generation* specifically. Prefill is compute-bound and will still want every
  core, so the two may want different values.

Success criteria:

| Stage | Passes when |
|---|---|
| Single node | Model A (gpt-oss-120b) holds a coherent conversation via `curl` |
| Sharded | Model B serves from all 7 nodes; TTFT and tok/s recorded |
| Missing Link | A real document is summarised end-to-end through the queue |

**Both metrics always.** Time-to-first-token (prefill, compute-bound) and
tokens/sec (generation, bandwidth-bound) are separate numbers with separate
causes. TTFT on a realistic multi-thousand-token document is the number that
decides whether the GPUs come back.

Also measure **concurrent seats**: throughput at 1, 2, 4 and 7 simultaneous
requests. This is the claim that pipeline sharding multiplies seats, and it
needs evidence.

### Do not cite the 0.06 tok/s figure

The only public CPU-cluster RPC throughput number — 20 nodes, 390 GB RAM,
DeepSeek-R1 at **0.06 tok/s** (#12974) — measures RPC *misapplied*. The model
already fitted in a single host's RAM, so RPC contributed pure coordination
overhead for no benefit. The maintainer's own explanation is that adding RPC
nodes to a model that already fits is expected to make things worse.

Quoted without that context it would badly misrepresent the architecture. **No
credible CPU-only RPC throughput figure exists in public sources** for any model
in this class — which is precisely why measuring on this hardware is worth
publishing.

## Risks

1. **CPU prefill may be worse than expected.** A 4,000-token document could
   exceed a minute before first token, and worse if the CPU lacks AVX-512.
   Measure early; it drives the GPU revisit decision and possibly the choice of
   workload. This is also why documents are chunked rather than stuffed.
2. **RPC pushes weights over the wire on every start.** ~550 GB over gigabit is
   a very long first load — plan for it. `rpc-server -c` caches tensors ≥10 MiB,
   which should make subsequent restarts fast — verify on the built version before
   accepting slow iteration as normal.
3. **RPC protocol overhead exists but its size is unknown.** See "Protocol
   overhead" below — the widely-cited 30–55% figure does not survive scrutiny,
   and the real magnitude for this configuration has never been measured.
4. **Concurrent requests had a KV-cache corruption bug.** With Flash Attention
   disabled, concurrent arrivals could leak output between conversations
   (#14893). Maintainer-confirmed fixed on master as of Aug 2025, but this is
   directly relevant if Missing Link ever runs jobs concurrently — verify on the
   pinned build before enabling `--parallel`.
5. **Large-model loads can hang.** Models over ~100 GiB have hung indefinitely in
   `load_tensors` where `llama-bench` succeeded on identical config; `-dio`
   (direct I/O, bypassing mmap) resolves it (#19745). Not expected at 32 GB, but
   worth knowing if the model grows.
6. **Swap death.** With models sized to pooled RAM, any overshoot pushes a node
   into swap and collapses throughput. The 15% headroom exists for this; disable
   swap on worker nodes so failures are loud rather than silent.

## Out of scope

- Security hardening (isolated LAN behind Tailscale)
- High availability, node failure recovery
- Fine-tuning or training
- Blog prose and publishable artifacts
- GPU acceleration (unless the TTFT revisit condition triggers)

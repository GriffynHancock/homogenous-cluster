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

| Constraint | Limit | Q8_0 target |
|---|---|---|
| Pooled (56 GB → ×0.85) | ~41.6 GB | 32.5 GB ✓ |
| Per node (8 GB → 75%) | ~6 GB | ~4.64 GB (58%) ✓ |

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
  4-core node that means 2 threads unless set explicitly. Set `-t 4`.
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

**Use all cores on this hardware. Do not reduce thread count.**

There is a documented case of generation peaking at 24 threads on a 96-thread
machine — but that is a many-core memory-controller contention effect, and it
does not apply here. A 5th-gen desktop i5 has 4 cores and no hyperthreading.
With so few threads there is nothing to contend: the memory controller is not
saturated by 4 cores. Set threads to core count for both prefill and generation.

**`rpc-server -t` defaults to half the logical cores**, so on a 4-core node it
will use 2 unless told otherwise. Set it explicitly to 4 — this is the more
likely mistake on this fleet by far.

Revisit thread reduction only if a node with **24+ threads** ever joins the
fleet. Below that the contention effect should not appear.

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

1. **CPU prefill may be worse than expected.** Broadwell without AVX-512 on a
   4,000-token document could exceed a minute before first token. Measure early;
   it drives the GPU revisit decision and possibly the choice of workload.
2. **RPC pushes weights over the wire on every start.** 32 GB over gigabit is
   several minutes per restart. `rpc-server -c` caches tensors ≥10 MiB, which
   should make subsequent restarts fast — verify on the built version before
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

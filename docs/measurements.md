# Measurements

Every performance number cited anywhere must appear here first, with the date
and the hardware it was measured on. Arithmetic estimates do not belong in this
file — where an estimate is unavoidable it is labelled **ESTIMATE** inline and
must be replaced by a measurement before it is cited.

---

## Hardware baseline — node 1

**Date:** 2026-08-12
**Node:** node1 (hostname `debian1`), LAN `10.10.0.34` (Tailscale address in
`network.md` — gitignored, site-specific)

| Fact | Value | Source |
|---|---|---|
| CPU model | Intel Xeon **E5-1620 v4** @ 3.50 GHz (Broadwell-EP, LGA2011-3) | `lscpu` |
| Sockets / cores / threads | **1 / 4 / 8** | `lscpu` |
| NUMA nodes | **1** — single socket, no thread pinning needed | `lscpu` |
| Max / min clock | 3800 / 1200 MHz | `lscpu` |
| AVX2 | **yes** | `/proc/cpuinfo` |
| **AVX-512** | **NO** — Broadwell predates it | `/proc/cpuinfo` |
| Other ISA | `avx`, `fma`, `f16c`, `sse4_2`, `aes`, `bmi2` | `/proc/cpuinfo` |
| L1d / L1i | 128 KiB / 128 KiB (4 instances each) | `lscpu` |
| L2 / L3 | 1 MiB (4 instances) / **10 MiB** shared | `lscpu` |
| RAM total | **125 GiB usable** (`MemTotal` 131798676 kB ≈ 131.8 GB / 122.7 GiB) | `free -h`, `/proc/meminfo` |
| Swap | 975 MiB **present and enabled** — `setup.sh` must disable it | `free -h` |
| Disk | NVMe **476.9 GB** Intel SSDPEKKF512G7L (non-rotational) | `lsblk` |
| Card reader | `sda` Multi-Card, 0 B, ignore | `lsblk` |
| OS | Debian GNU/Linux 12 (bookworm), kernel 6.1.0-52-amd64 | `/etc/os-release` |
| NIC | `eno1`, LAN 10.10.0.34/24 | `ip -br addr` |

**DIMM layout / memory channels:** pending — `dmidecode` was not installed.
See "Open hardware questions" below.

### What this hardware changes about the plan

Three facts here differ from what the plan and spec assumed, and two of them
matter a great deal.

**1. Four physical cores, not "a Xeon, so probably many".** The spec derives
thread counts from `nproc`, which reports **8** here — but 4 of those are
SMT siblings. llama.cpp generally does not benefit from SMT on
memory-bandwidth-bound generation, and can be slower with it. `-t 4` vs `-t 8`
must be measured, not assumed, and the answer feeds directly into
`rpc-server -t` fleet-wide.

**2. No AVX-512.** Prefill is compute-bound, and this is the instruction set
that would have accelerated it. Combined with 4 cores, **time-to-first-token is
the metric at risk here, not tokens/sec.** The plan's GPU-revisit threshold
(TTFT > 90 s at ~2000 tokens) is now much more likely to be hit than assumed.
This strengthens, rather than weakens, the case for map-reduce chunking — small
chunks keep prefill in its efficient range.

**3. RAM is 125 GiB usable, not 128 GB.** The distinction matters because both
memory constraints are percentages of physical RAM:

| Constraint | Value |
|---|---|
| `MemTotal` | 131.80 GB (122.75 GiB) |
| Per-node 75% ceiling (llama.cpp #15055) | **98.8 GB** |
| × 7 nodes | **691.9 GB** |
| Pooled `(RAM − 1 GB/node) × 0.85` | 106.7 GB/node → 746.8 GB |

The per-node rule still binds first, as the plan predicted. Budget is
**~692 GB**, slightly *more* than the ~672 GB the plan assumed, because the plan
computed from a nominal 128 GB while `dmidecode`-reported physical RAM is
131.8 GB. **Do not spend this margin** — it is inside the noise of what the 75%
figure is even known to within (see open question below).

### Open hardware questions

- [ ] **Memory channel count.** E5-1620 v4 is quad-channel DDR4-2400 by
      specification, giving a theoretical **76.8 GB/s** (4 × 2400 MT/s × 8 B).
      But 125 GiB across 4 channels implies 4 × 32 GB DIMMs, and this must be
      confirmed from `dmidecode` `Locator` labels — if the DIMMs are populated
      across fewer channels the bandwidth, and therefore tokens/sec, is
      proportionally lower. **This is the single number that most determines
      generation speed.** Marked ESTIMATE until confirmed.
- [ ] **Configured vs rated DIMM speed.** DDR4-2400 parts can be clocked down by
      the board. `dmidecode` reports both `Speed` and `Configured Memory Speed`;
      the latter is the one that matters.
- [ ] **`-t 4` vs `-t 8`** — measure, do not assume. Feeds `rpc-server -t`.
- [ ] **Confirm node 2 matches.** Node 2 is being installed now and is reported
      to be near-identical hardware. Nodes 3–7 unverified.

---

## Build

**Date:** 2026-08-12 | **Node:** node1

| Fact | Value |
|---|---|
| llama.cpp tag | **b10369** |
| Commit | `6e62ba538478202094edc6c100c782719e310aa3` |
| Reported version | `version: 10369 (6e62ba538)` |
| Compiler | GNU 12.2.0 |
| Target ISA | `-march=haswell -mtune=native` (**not** `native` — see F8) |
| libc6 | `2.36-9+deb12u14` |
| Build time | ~12 min wall, 8 jobs on 4 cores |

**Tag reasoning (F6):** the `b8492`-and-later warning in the plan is stale. The
`--tensor-split`-over-RPC regression (#20908 → issue #21006) was fixed by
PR #21030, merged 2026-03-27, commit `ba38f3b` — verified an ancestor of b10369.
There is no stable channel to retreat to; b-tags are cut several times daily off
`master`.

### `rpc-server --help` (b10369)

```
  -t, --threads N                  number of threads for the CPU device (default: 4)
  -d, --device <dev1,dev2,...>     comma-separated list of devices
  -H, --host HOST                  host to bind to (default: 127.0.0.1)
  -p, --port PORT                  port to bind to (default: 50052)
  -c, --cache                      enable local file cache
```

Both `-c` and `-t` present, as the plan requires. **`-t` default is 4 on this
box, which is `nproc/2` = half the logical cores — the plan's warning is
confirmed on real hardware.**

### Two packaging problems found and fixed

**1. The binary is `ggml-rpc-server`, not `rpc-server`.** Upstream renamed the
target (`tools/rpc/CMakeLists.txt: set(TARGET ggml-rpc-server)`). Every script
and systemd unit in this repo, and every guide online, says `rpc-server`.
`build-llama.sh` now installs a `rpc-server -> ggml-rpc-server` symlink.

**2. Default RPATH pointed at the build tree — binaries were not
relocatable.** Confirmed by `ldd`:

```
libggml.so.0 => /opt/llama.cpp/src/build/bin/libggml.so.0
```

ggml builds as **shared** libraries, and `llama-server` is only 18 KB — a stub
against `libllama-server-impl.so`. So the binaries are not self-contained *and*
they resolved their libraries from a path that exists only on the build machine.
This would have passed every test on node 1 and failed on all six workers, with
a loader error resembling nothing in the plan's troubleshooting.

Fixed with `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`
and verified:

```
RUNPATH: [$ORIGIN]
# copied to /tmp/rpath-test, source tree not referenced:
libggml.so.0 => /tmp/rpath-test/./libggml.so.0
RUNS STANDALONE: OK
```

`build-llama.sh` now asserts this and **fails the build** if any binary
references the source tree. `distribute.sh` must ship `*.so*` alongside the
executables.

---

## Thread count sweep — settles `rpc-server -t` fleet-wide

**Date:** 2026-08-12 | **Node:** node1 | **Model:** Qwen3-4B Q4_K_M | `-r 2`

| Threads | pp512 (t/s) | tg128 (t/s) |
|---:|---:|---:|
| 1 | 8.51 ± 0.29 | 4.12 ± 0.02 |
| 2 | 17.32 ± 0.01 | 7.31 ± 0.16 |
| **4** | **32.39 ± 0.21** | **11.19 ± 0.06** |
| 6 | 32.19 ± 0.29 | 11.38 ± 0.02 |
| 8 | 32.58 ± 0.41 | **8.31 ± 0.13** |

**Verdict: use `-t 4` — the physical core count. Never `nproc`.**

- **Prefill scales linearly to 4 threads, then stops dead.** 8.51 → 17.32 →
  32.39, then flat at 6 and 8. SMT contributes nothing to prefill.
- **Generation is 26% SLOWER at `-t 8` than at `-t 4`** (8.31 vs 11.19). SMT
  siblings contend for the same memory pipe and actively hurt.

**This contradicts the plan.** `cluster/install-services.sh` sets
`RPC_THREADS=$(nproc)`, which is 8 here — the worst of the five values tested.
It must be set from the **physical core count**
(`lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l`), not `nproc`.

---

## Memory bandwidth — the ceiling on generation, measured directly

**Date:** 2026-08-12 | **Node:** node1 | STREAM triad, 4.8 GB working set

| Threads | GB/s |
|---:|---:|
| 1 | 13.7 |
| 2 | 24.2 |
| **4** | **28.2 ← peak** |
| 8 | 27.3 |

### Cross-validation: llama.cpp is already at the memory roof

| Quantity | Value |
|---|---|
| Measured tg128, `-t 4` | 11.19 tok/s |
| Model bytes read per token (2.32 GiB, dense) | 2.49 GB |
| **Implied bandwidth** `11.19 × 2.49` | **27.9 GB/s** |
| **Measured STREAM peak** | **28.2 GB/s** |
| **llama.cpp efficiency vs achievable bandwidth** | **~99%** |

**This is the most important number measured so far.** Generation is running at
essentially 100% of what the memory subsystem can deliver. **No software tuning
— thread counts, quantisation format, NUMA flags, `ik_llama.cpp` — will move
generation speed on this hardware.** The only lever is memory bandwidth itself.

### ~~The DIMMs are probably only in 2 of 4 channels~~ — RETRACTED

**This hypothesis was WRONG and is retracted.** It is left here, struck
through, because it is a worked example of the failure mode this file exists to
prevent: a plausible inference from a single number, which would have sent
someone to open four machines for nothing.

It said: 28.2 GB/s is 37% of quad-channel theoretical but 73% of dual-channel,
73% is a textbook STREAM result, therefore the board is half-populated and
rebalancing the DIMMs is a free doubling of throughput.

`dmidecode` refuted it on **both** nodes (2026-08-17):

```
DIMM_1     32 GB    @ 2400 MT/s
DIMM_2     32 GB    @ 2400 MT/s
DIMM_3     32 GB    @ 2400 MT/s
DIMM_4     32 GB    @ 2400 MT/s
```

All four channels populated, at full rated speed, on node 1 **and** node 2. The
real cause is core count — 4 cores cannot generate enough memory-level
parallelism to saturate a quad-channel bus, which needs ~8–14 cores on
Broadwell. See **F12**. There is no free doubling and no BIOS lever.

**The lesson worth keeping:** "73% is a textbook result" was pattern-matching,
not evidence. The distinguishing measurement — a single-core STREAM run
reaching 13.7 GB/s, i.e. ~49% of the *dual*-channel figure all by itself — was
already available and would have killed the hypothesis immediately.

---

## RPC protocol overhead (localhost isolation test)

**Date:** 2026-08-12 | **Node:** node1 | **Model:** Qwen3-4B Q4_K_M
**Threads:** 4 | **Build:** b10369 | `-r 3`

Network removed entirely — one `rpc-server` on 127.0.0.1. This is a **floor**:
the real cluster adds gigabit on top.

| Metric | Local | Via RPC (localhost) | Overhead |
|---|---:|---:|---:|
| pp512 (prefill, t/s) | 33.18 ± 0.16 | 20.11 ± 0.01 | **−39.4%** |
| tg128 (generation, t/s) | 11.55 ± 0.06 | 10.95 ± 0.01 | **−5.2%** |

### Verdict: PROCEED

The plan's decision rule is on **generation**: under 15% → proceed as planned.
**5.2% clears it comfortably.** This also vindicates the earlier retraction of
the "30–55% RPC overhead" claim — for bandwidth-bound CPU generation, per-token
compute dwarfs a loopback round-trip, exactly as predicted.

### But prefill is a different story, and it lands on the weak spot

**−39.4% on prefill is large, and it compounds with hardware that is already
weak at prefill** (4 cores, no AVX-512 — F7). Prefill is compute-bound, so the
RPC serialisation cost is a much larger fraction of a smaller number.

Crude projection, **ESTIMATE only**: at 20.11 t/s a 2000-token prompt implies
~100 s TTFT — already past the plan's 90 s GPU-revisit threshold. Do not act on
this yet. It is measured on a 4B dense model where the *entire* model sits on
the RPC device, which is not the cluster's topology, and Model A runs on one
node with no RPC at all. **Task 3 measures the real thing.**

What this does justify now:

- **Raising `-ub`** (default 512) for CPU prefill, to amortise weight loading
  across more tokens per RPC round-trip. Worth testing explicitly.
- **Map-reduce chunking is further reinforced** — a third independent reason,
  alongside "lost in the middle" and prefill degradation with context length.
- **TTFT must be reported separately everywhere.** A single "tok/s" number
  would hide a 39% prefill penalty behind a 5% generation one.

### Note on the `threads` column

The RPC rows report `threads = -1`. `llama-bench -t` does not propagate to an
RPC device — the **`rpc-server`'s own `-t` governs**, which is why it must be
set correctly in the systemd unit (F10). The `-t 4` above is the server's.

---

## Single-node baseline — Qwen3-4B Q4_K_M

**Date:** 2026-08-12 | **Node:** node1 | **Threads:** 4 | **Build:** b10369

| Metric | Value |
|---|---:|
| pp512 (t/s) | 33.04 ± 0.22 |
| pp2048 (t/s) | 28.33 ± 0.23 |
| tg128 (t/s) | 11.49 ± 0.03 |
| **TTFT @ 2214-token prompt** | **89.1 s** |
| Prefill rate at that length (server-reported) | 24.84 t/s |
| Generation rate (server-reported) | 6.31 t/s |

**Prefill degrades with length**, as the spec predicted: 33.04 t/s at 512 tokens
→ 28.33 at 2048 → 24.84 at 2214 in the server. An independent hardware reason
to chunk, on top of "lost in the middle".

### TTFT: 89 s on a 4B model — the plan's threshold is 90 s

**This is the headline result and it is bad.** See F17. The plan's own
GPU-revisit trigger is *TTFT > 90 s at ~2000 tokens*; the **smallest model in
the project sits within 1% of it**, before any RPC overhead (a further −39.4%
on prefill, F14) and before scaling to a 100× larger model.

Generation, by contrast, is fine and behaves exactly as theory predicts. The
asymmetry is the whole story of this hardware: **4 cores and no AVX-512 make
prefill the binding constraint, not bandwidth.**

### The measurement method in the plan was wrong

`curl -w %{time_starttransfer}` reported **0.015 s** for the same request that
actually took 89 s to first token — it times the HTTP headers, which
`llama-server` sends immediately. Runs 2 and 3 were also invalid, having hit the
prompt cache (`prompt eval time = ... / 1 tokens`). `bench/node-bench.sh` now
parses the SSE stream for the first content-bearing chunk and varies the prompt
per run. **Full detail in F17 — the same bug is in the plan's Task 8
concurrency test, which measures the project's central claim.**

---

## Memory subsystem — resolved

**Date:** 2026-08-17 | `dmidecode` confirms the DIMM layout is **correct**:

```
DIMM_1..DIMM_4:  32 GB  DDR4  Rank 2
Speed: 2400 MT/s     Configured Memory Speed: 2400 MT/s
Board: LENOVO 30B2S2E800 (ThinkStation P510) — 8 slots, 4 populated
```

All four channels populated at full rated speed. **An earlier hypothesis in this
file that the board was half-populated was wrong and has been retracted (F12).**

So 28.2 GB/s is 37% of the 76.8 GB/s quad-channel DDR4-2400 can deliver, with
nothing wrong with the memory. **The limit is the 4-core CPU**, which cannot
generate enough memory-level parallelism to saturate its own bus (1 core =
13.7 GB/s, 4 cores = only 28.2). Saturating quad-channel Broadwell needs ~8–14
cores.

CPU frequency is ruled out: under load all cores hold 3592 MHz, turbo enabled,
`max_perf_pct` = 100.

**Implication for `--tensor-split`:** if nodes 3–7 have higher-core Xeons, they
will be faster at generation despite identical RAM. The split should then be
weighted by **measured bandwidth**, not RAM as `nodes.env` currently assumes.

---

## Model B disk blocker

**Date:** 2026-08-12. Master has **431 GB free**; Kimi K2 IQ4_XS is **547 GB**.
Disk — not RAM — is the binding constraint (F16). A drive is being added
2026-08-17 week. Interim single-node target: **Qwen3-Next-80B-A3B UD-Q8_K_XL,
93 GB, 3B active.**

## Batching on sparse MoE — the "few seats" question, answered

**Date:** 2026-08-17 | **Node:** node1 | **Model:** gpt-oss-120b F16 | `-t 4`
`llama-batched-bench -npp 512 -ntg 128 -npl 1,2,4,8 -c 16384`

| Batch | Prefill t/s | Generation t/s | Gen speedup | **Total t/s** |
|---:|---:|---:|---:|---:|
| 1 | 15.96 | 5.43 | 1.00× | 11.50 |
| 2 | 16.63 | 7.99 | 1.47× | 13.67 |
| **4** | 16.58 | 9.73 | **1.79×** | **14.54 ← peak** |
| 8 | **7.34** | 10.96 | 2.02× | **7.86 ← worse than batch 1** |

### `--parallel 8` is worse than `--parallel 1`. Set it to 4.

**At batch 8 prefill collapses 56%** (16.58 → 7.34 t/s) and total system
throughput falls to **7.86 t/s — below the 11.50 of batch 1.** Prefill wall-time
went from 123.5 s to 557.8 s: **4.5× longer for 2× the work**, i.e. sharply
superlinear degradation.

The likely cause is working-set pressure: at batch 8, `N_KV` is 5120 and the
combined KV cache plus activations stop streaming efficiently through a memory
subsystem that is already saturated at 28.2 GB/s by four cores. Generation keeps
improving slightly (10.96 t/s) because it is a smaller working set, but prefill
dominates total throughput and drags the system under.

**Operational rule: `--parallel 4` on this hardware. Never 8.** And because the
plan leaves `--parallel` unset — which silently means **4 slots** — the default
happens to be correct here, but only by accident. Set it explicitly.

**Verdict: batching helps, but far less than on a dense model.** The reference
dense CPU measurement (Intel Ultra 9 285K, discussion #18030) scaled **5.75×**
from batch 1→32. Here batch 1→4 yields **1.79×**, where a dense model would give
roughly 3×.

This is the predicted MoE effect, and it is real but not total: batch B touches
≈ `min(B × top_k, n_experts)` experts, so routed-expert reads grow with B, while
attention and shared-expert weights *are* reused across the batch. The result
sits between "neutral" and "linear", as anticipated — closer to neutral.

**Prefill is completely flat** (15.96 → 16.63 → 16.58, +4% then −0.3%),
confirming it is compute-saturated and independently corroborating the `-ub`
result (F18). **No batching strategy will improve prefill on this hardware.**

### The strategic consequence

**The "a few seats" claim in `CLAUDE.md` survives — but weakly, and it is now
the *inferior* way to get concurrency.**

| Route to concurrency | Speedup | Cost |
|---|---:|---|
| Batching on one node (`--parallel 4`) | **1.79×** | free, but MoE-limited |
| **Replicating the model on 7 nodes** | **~7×** | needs a node-sized model |

Replication dominates batching by ~4×, and the two compose. For a workload made
of independent chunk summaries, **replication is the primary concurrency
mechanism and batching is a secondary bonus** — the reverse of what the plan
assumed. See `DESIGN-NOTES.md` section C.

Note also F4: the server prompt cache is reported to assert with `-np > 1`
against the **RPC** backend. This measurement is single-node with no RPC, so it
sidesteps that — another argument for the replicated topology.

---

## Power and clock state — all levers already maxed

**Date:** 2026-08-17. Read from MSRs under load, so these are the values that
actually apply during inference:

| Register | Value | Meaning |
|---|---|---|
| `UNCORE_RATIO_LIMIT` (0x620) max | 0x1c = 28 | 2800 MHz ceiling |
| `UNCORE_PERF_STATUS` (0x621) current | 0x1c = 28 | **2800 MHz — at the ceiling** |
| `IA32_ENERGY_PERF_BIAS` (0x1B0) | 0 | **maximum performance** |
| Core frequency under load | 3592 MHz | turbo active, `max_perf_pct` 100 |

The uncore contains the memory controller, so a throttled uncore is the usual
explanation for low bandwidth on Xeon E5 v4. **It is not throttled here.**
`intel_pstate` has already driven every knob to maximum regardless of the BIOS
profile, so changing BIOS from Balanced to Performance should not be expected to
help. **28.2 GB/s is the chip's genuine ceiling.**

---

## Predicted generation speed — now a usable planning tool

Because generation measures at **~99% of achievable bandwidth** (F11), the
relation below is predictive rather than a datasheet estimate:

```
tok/s  ≈  bandwidth / (active_params × bytes_per_weight)
       ≈  28.2 GB/s / bytes_read_per_token
```

Per node, and — because RPC nodes compute sequentially, so the cluster reads the
same total bytes per token — **the same for the 7-node cluster**:

| Model | Active | Bytes/token | **Predicted tok/s** |
|---|---:|---:|---:|
| Qwen3-4B Q4_K_M (dense, **measured**) | 4.0 B | 2.49 GB | **11.2 measured / 11.3 predicted** |
| gpt-oss-120b MXFP4 | 5.1 B | ~2.7 GB | ~10.4 |
| Qwen3-Next-80B-A3B Q8 | 3.0 B | ~3.2 GB | ~8.8 |
| Kimi K2 IQ4_XS (7 nodes) | 32 B | ~16 GB | **~1.8** |
| Kimi K2 UD-IQ2_M (7 nodes) | 32 B | ~10 GB | ~2.8 |

**Caveat, and it is the point of the next two measurements.** The 99% figure
was measured on a **dense** model, where every weight is read in one contiguous
sweep. A sparse MoE reads scattered experts with far worse locality, so
achieved bandwidth may fall short. **gpt-oss-120b and Qwen3-Next are the first
MoE models on this node and will show directly how much sparsity costs.** If
they hit their predicted rates, the table above is trustworthy and Kimi K2 can
be sized without downloading it. If they fall short, the shortfall is the MoE
locality penalty and every MoE prediction needs that correction factor.

**~1.8 tok/s for Kimi K2 is slow but not disqualifying** for the target
workload — an overnight summary at 1.8 tok/s produces roughly 65,000 tokens in
10 hours. The thesis was never "fast"; it was "possible at all". The number that
threatens the project is TTFT (89 s on a *4B* model), not this one.

---

## Model A: gpt-oss-120b, single node — AND the MoE efficiency answer

**Date:** 2026-08-17 | **Node:** node1 | **Threads:** 4 | **Build:** b10369
**Model:** gpt-oss-120b F16 (native MXFP4), 60.87 GiB, 116.83B total / 5.1B active

| Metric | Value |
|---|---:|
| pp512 (t/s) | 16.03 ± 0.55 |
| pp2048 (t/s) | 15.88 ± 0.01 |
| tg128 (t/s) | **6.05 ± 0.00** |

### Sparse MoE runs at 61% of memory bandwidth, not 99%

This settles the top open question in `STATUS.md`, and it is a **downgrade**.

| Quantity | Value |
|---|---:|
| Model size / params | 65.36 GB / 116.8B → 0.559 bytes/param |
| Bytes read per token (5.1B active) | 2.85 GB |
| **Predicted** at dense efficiency (28.2 GB/s) | 9.88 tok/s |
| **Measured** | **6.05 tok/s** |
| **Implied bandwidth** | **17.3 GB/s** |
| **Efficiency vs STREAM** | **61%** (dense Qwen3-4B was ~99%) |

**Sparsity costs ~39% of achievable bandwidth.** The cause is locality: a dense
model reads its weights as one contiguous sweep, whereas an MoE gathers a
scattered subset of experts per token, defeating prefetch and wasting part of
every cache line and DRAM burst.

**So the `tok/s ≈ bandwidth / bytes_per_token` rule needs an architecture
factor:**

```
dense MoE-free : tok/s ≈ 28.2 / bytes_per_token     (measured 99% efficient)
sparse MoE     : tok/s ≈ 17.3 / bytes_per_token     (measured 61% efficient)
```

### Revised predictions — every earlier MoE figure was ~1.6× optimistic

| Model | GB/token | Old (dense-eff) | **Revised (MoE-adjusted)** |
|---|---:|---:|---:|
| gpt-oss-120b MXFP4 | 2.85 | 9.9 | **6.05 ← measured** |
| Qwen3-Next-80B-A3B Q8 | 3.18 | 8.9 | **~5.4** |
| **Kimi K2 IQ4_XS (7 nodes)** | 16.0 | 1.76 | **~1.08** |
| Kimi K2 UD-IQ2_M (7 nodes) | 9.9 | 2.84 | **~1.74** |

**Kimi K2 at ~1.08 tok/s** still clears the overnight bar — ~39,000 tokens in
10 hours — but the margin is thinner than the earlier estimate implied. Treat
this as provisional across models: gpt-oss has 128 experts with `top_k=4`,
while Kimi K2 has 384 with `top_k=8`, and a more scattered gather could be
worse. **Re-measure once Model B is on disk; do not quote 1.08 as measured.**

### Prefill: worse in absolute terms, but flat with length

15.88 t/s at 2048 tokens versus 24.84 for the 4B model — more active params to
compute, and prefill is compute-bound. But note it barely degrades with length
(16.03 → 15.88, −1%), where the dense 4B fell 33.0 → 28.3 (−14%). For a large
MoE, weight reading dominates attention, so long contexts cost comparatively
less than the spec's "58% loss from 512 to 32K" warning suggests.

**Practical consequence:** a 4K-token chunk costs ~258 s of prefill (4.3 min).
A 50K-token document (14 chunks) is **~60 min of prefill plus ~20 min of
generation — roughly 80 minutes single-node.** Comfortably inside an overnight
window, which is the gate that matters (F19).

---

## Hardware baseline — node 2 (joined 2026-08-17)

**Date:** 2026-08-17
**Node:** node2 (hostname was `admin`, set to `node2`), LAN `10.10.0.39`

**Characterised BEFORE anything was assumed about it.** It turns out to be a
genuine twin of node 1 — but that is a measured result, not a starting premise,
and the `nodes.env` comment warning against copying node 1's values stands.

| Fact | node 1 | **node 2** | Same? |
|---|---|---|---|
| CPU | Xeon E5-1620 v4 @ 3.50 GHz | **Xeon E5-1620 v4 @ 3.50 GHz** | yes |
| Physical cores / threads | 4 / 8 | **4 / 8** | yes |
| Sockets / NUMA nodes | 1 / 1 | **1 / 1** | yes |
| ISA | avx2, fma, f16c, **no AVX-512** | **avx2, fma, f16c, no AVX-512** | yes |
| `MemTotal` | 131798676 kB | **131798676 kB** | yes |
| `free -m` total | 128709 MB | **128709 MB** | yes |
| DIMM layout | 4 × 32 GB @ 2400 MT/s | **4 × 32 GB @ 2400 MT/s** | yes |
| Board | LENOVO 30B2S2E800 (ThinkStation P510) | **LENOVO 30B2S2E800** | yes |
| Disk | NVMe 476.9 GB, 367 GB free | **NVMe 476.9 GB, 437 GB free** | same model |
| L3 cache | 10 MiB | **10 MiB** | yes |
| OS / kernel | Debian 12, 6.1.0-52-amd64 | **Debian 12, 6.1.0-52-amd64** | yes |
| libc6 | 2.36-9+deb12u14 | **2.36-9+deb12u14** | yes |

**Both nodes' DIMM layout confirmed by `dmidecode`** (this is what retracted the
half-population hypothesis earlier in this file):

```
DIMM_1     32 GB    @ 2400 MT/s
DIMM_2     32 GB    @ 2400 MT/s
DIMM_3     32 GB    @ 2400 MT/s
DIMM_4     32 GB    @ 2400 MT/s
```

Also present on node 2, and harmless: a 0 B `Multi-Card` reader at `/dev/sda`
(same as node 1 — the trap `preseed.cfg` filters for) and a 59.8 GB USB flash
drive at `/dev/sdb`.

### STREAM triad — node 2, with a same-day node 1 control

Identical source and method to the node 1 baseline (4.8 GB working set,
`gcc -O2 -fopenmp`, `OMP_PROC_BIND=spread`, best of 5 reps). Node 1 was re-run
the same day so the comparison is not against a 5-day-old number.

| Threads | node 1 (2026-08-12) | **node 1 (2026-08-17 control)** | **node 2 (2026-08-17)** |
|---:|---:|---:|---:|
| 1 | 13.7 | 13.6 | **13.6** |
| 2 | 24.2 | 24.4 | **24.0** |
| **4** | **28.2 ← peak** | **28.4 ← peak** | **27.9 ← peak** |
| 6 | — | 27.0 | 26.3 |
| 8 | 27.3 | 27.8 | **27.6** |

**Node 2 peaks at 27.9 GB/s against node 1's 28.4 — a 1.8% difference, i.e.
within run-to-run noise.** Three conclusions:

1. **The fleet is homogeneous in bandwidth**, so `--tensor-split` weighted by RAM
   is also correct by bandwidth. F12 warned this might not hold; on these two
   nodes it does. It must be re-checked per node, not assumed for nodes 3–7.
2. **The 4-thread peak and the SMT penalty reproduce exactly** on independent
   hardware — `-t` = physical cores is not an artefact of one machine (F10).
3. **Node 1's 28.2 → 28.4 re-run confirms the measurement is stable**, which is
   what makes the 1.8% gap readable as noise rather than as a real difference.

---

## Network — the link is 100 Mb/s, NOT gigabit

**Date:** 2026-08-17 | **Nodes:** node1 ↔ node2 | **Measured, not assumed**

`STATUS.md` and `network.md` both described the fleet as gigabit. It is not.

| Measurement | Result | Method |
|---|---:|---|
| `eno1` negotiated speed, both nodes | **100 Mb/s, full duplex** | `ethtool` |
| NIC *supported* link modes | **includes 1000baseT/Full** | `ethtool` |
| NIC *advertised* link modes | **includes 1000baseT/Full** | `ethtool` |
| TCP throughput node1 → node2 | **93.8 Mbit/s (11.7 MB/s)** | `iperf3 -t 10` |
| TCP throughput node2 → node1 | **90.1 Mbit/s (11.3 MB/s)** | `iperf3 -t 10 -R` |
| Sustained file transfer | **11.18 MB/s** | `rsync` of a 65 GB GGUF |
| ICMP RTT, **idle** link | **0.827 ms** | `ping -c 2` |
| ICMP RTT, link **saturated** by one rsync | **9.544 ms** (min 6.78 / max 11.89) | `ping -c 20` |

**Both NICs are gigabit silicon (Intel I218-LM) and both advertise
1000baseT/Full, so the 100 Mb cap is the cable or switch port, not the
hardware.** Confirmed independently by the operator inspecting the switch. The
link runs at ~94% of 100 Mb line rate, so it is *healthy* — just capped.

### Consequences, in order of how much they matter

1. **Peer-to-peer model pull is now SLOWER than the internet.** F23 justified
   `cluster/models.sh pull` preferring a peer over HuggingFace on "21 MB/s from
   HuggingFace vs ~110 MB/s on gigabit LAN". At **11.7 MB/s the LAN is ~1.8×
   slower than the measured HuggingFace download.** That preference is inverted
   on this network and `models.sh` should be re-checked against it.
2. **Model distribution is slow.** 65 GB gpt-oss-120b = **~97 min** at 11.18
   MB/s (measured, not estimated). A 189 GB GLM-4.6 would be ~4.7 h *per node*.
   On gigabit these become ~9 min and ~27 min.
3. **RPC sharding pays a much larger penalty than the localhost floor.** See
   below.
4. **Replication is entirely unaffected**, because independent `llama-server`s
   share no hot path. This is a further argument for replication-first.
5. **Latency-bound designs are not viable here.** RTT degrades 11.5× under a
   single bulk transfer (0.827 → 9.544 ms) — ordinary bufferbloat. This is what
   qualifies the expert-parallelism comms analysis in `DESIGN-NOTES.md` A.

**Cheapest fix: a ~$20–30 gigabit switch**, uplinked to the existing 100 Mb
port. Node↔node traffic is then switched locally at gigabit and never touches
the uplink, while each node keeps internet on one cable. Preferred over
daisy-chaining two nodes, which needs N−1 ports per node and does not scale to
nodes 3–7.

---

## Two-node RPC sharding across REAL MACHINES

**Date:** 2026-08-17 | **Model:** Qwen3-4B Q4_K_M | **Build:** b10369
**Topology:** `llama-server` on node 1, `--rpc 127.0.0.1:50052,10.10.0.39:50052`,
`--tensor-split 1,1`, `-t 4`, each `rpc-server -t 4`

**This is the first time RPC has run across two physical machines.** F14's
overhead figures were a localhost isolation test with the network removed.

| Metric | Local (no RPC) | RPC on localhost (F14) | **Across 2 real machines** |
|---|---:|---:|---:|
| Prefill | 33.18 t/s | 20.11 t/s (−39.4%) | **17.76 t/s** |
| Generation | 11.55 t/s | 10.95 t/s (−5.2%) | **5.89 t/s** |

**⚠ INDICATIVE, NOT RIGOROUS, AND MEASURED ON AN UNREPRESENTATIVE MODEL.**
They come from a single short chat request in `bench/two-node-smoke.sh`
(33 prompt tokens, 54 generated), whereas the comparison columns are
`llama-bench pp512`/`tg128`. Different workload, far fewer tokens, no repeats.
**A proper `llama-bench` run over the RPC devices is still owed**, and must be
done on an idle link — measuring it while a 65 GB transfer saturates the network
would be meaningless.

**The model was 2.4 GB, and that biases the result — probably pessimistically.**
Qwen3-4B needs no sharding whatsoever (it fits one node ~50 times over). Splitting
it exists only to exercise the #26500 gate, whose trigger is worker COUNT, not
model size (F22). But a tiny model **maximises the relative weight of RPC's fixed
per-layer overhead**, because there is almost no per-layer compute to amortise the
round trip against. On a large MoE, per-layer compute is far larger against the
same round trip, so the relative penalty should be **smaller**. Treat −49% as a
**pessimistic bound for large models, not an estimate.**

**What is nonetheless clear:** generation across two real machines is roughly
**half** the single-node rate, against the −5.2% localhost floor. The difference
between −5.2% and roughly −49% is the network, and on a 100 Mb link that is
unsurprising. It reinforces the existing conclusion — **sharding buys capacity,
never speed** — and sharpens it: on a 100 Mb link, sharding costs about half of
generation, not 5%.

### Upstream bug #26500 — the gate, and it PASSES on real hardware

The reason this test exists (F2). Trigger is worker *count* ≥ 2, reproduced
upstream on a CPU-only cluster running sparse MoE, fixed in **no released tag**.

| Check | Result |
|---|---|
| Server reached healthy | **yes** |
| `[create_node] invalid data ptr` in any log | **0 occurrences** |
| Abort / SIGILL / malformed-response | **0** |
| Generation completed | **yes** — coherent, on-topic prose |
| Server-reported prompt eval | 17.76 t/s / 33 tokens |
| Server-reported eval | 5.89 t/s / 54 tokens |

F22 had already cleared this with two `rpc-server` processes on one box. **It now
also clears with two separate machines**, i.e. with real TCP, distinct
`machine-id`s, distinct host keys and a real NIC in the path.

**Still model-dependent.** The public reproductions involve MoE graphs with
unusual constant/view nodes; this was a **dense 4B**. Re-run with the actual
Model B GGUF before committing to a 7-node launch.

---

## THE REPLICATION MEASUREMENT — aggregate throughput across 2 independent nodes

**Date:** 2026-08-17 | **Nodes:** node1 + node2 | **Model:** gpt-oss-120b F16
(65.4 GB, verified byte-identical on both nodes, md5 `c859460f5dab…`)
**Engine:** ik_llama.cpp `8337e4cd` | `-t 4` | `-c 16384` | `--parallel 4` | `--jinja`
**Topology:** one INDEPENDENT `llama-server` per node. **No `--rpc`, no
`--tensor-split`** — verified from the live process command lines. `rpc-server`
was stopped on both nodes for the duration.

**This is the measurement the whole architecture rests on**, and it had never been
run. Both phases put an identical load on each endpoint (4 concurrent requests),
so linear scaling means unchanged wall time for twice the work.

| | Phase A — 1 node | Phase B — 2 nodes |
|---|---:|---:|
| Requests ok / total | **4 / 4** | **7 / 8** |
| Wall time | 425.51 s | 459.15 s |
| Prompt tokens | 6469 | 11333 |
| Completion tokens | 744 | 1246 |
| Prompt tok/s (per total wall clock) | 15.20 | **24.68** |
| Completion tok/s (per total wall clock) | 1.75 | **2.71** |
| Per-request latency (min/med/max) | 409 / 422 / 426 s | 391 / 405 / 459 s |

### Result

| Metric | Raw | **Adjusted for the failed request** |
|---|---:|---:|
| Prefill scaling | 1.62× (81% of linear) | **1.86× (93% of linear)** |
| Completion scaling | 1.55× (78% of linear) | **1.77× (89% of linear)** |
| Wall time | +7.9% for 2× the work | — |

**One request of eight failed**, so the raw figures understate the result: the
failed request consumed prefill and generation but contributed zero counted
tokens. The adjusted column scales Phase B by its own per-request means to
estimate 8 successful requests. **The adjustment is arithmetic, not measurement —
cite the raw figures, and the adjusted ones only with this caveat attached.**

**Verdict: replication delivers roughly 1.8× on two nodes, ~90% of linear.** The
replication-first architecture is **validated on real hardware.** Set against the
alternatives measured on this same fleet:

| Route to throughput | Measured | Notes |
|---|---:|---|
| **Replication (this measurement)** | **~1.8× at N=2** | no RPC, no shared hot path |
| Batching (`--parallel 4`) | 1.79× | within one node; collapses at 8 |
| Sharding (2 nodes, one copy) | **1×**, and −49% generation | capacity only; pessimistic bound |

**Prefill scaled better than generation (1.86× vs 1.77×), which is the favourable
direction** — prefill is ~79% of document wall-clock (F27), so the metric that
dominates real work is the one that scales best.

### Why it is 90% and not 100%

- **Wall time grew 7.9%.** Independent nodes should finish in identical time, so
  this is the gap. Phase B's slowest request took 459 s against Phase A's 426 s.
- **Per-slot generation rates varied widely** — server-reported: 0.58, 0.85 t/s
  on node1; 0.76, 2.01 t/s on node2. With 4 concurrent slots each getting ~1/4 of
  a 4-core machine, scheduling noise is large.
- **n = 1.** One run, 4 requests per node. Run-to-run variance at this sample size
  plausibly covers most of the 10% shortfall.
- Node 2 is 1.8% slower on STREAM (F29), which accounts for a small part of it.

**Owed: a clean re-run** with `max_tokens` high enough to avoid the F21 failure,
and more requests per endpoint, before treating 1.8× as a settled constant.

### The failure is itself a finding: F21 fires on gpt-oss-120b

The one failed request returned **`EMPTY content` with `reasoning_content`
populated** — F21 exactly, on **Model A**, the model this project actually intends
to run for the document workload.

**And `chat_template_kwargs {"enable_thinking": false}` did not prevent it**,
despite the server running with `--jinja`. gpt-oss uses the harmony format with
its own reasoning channel, and `enable_thinking` appears to be a Qwen-family
knob it does not honour. 7 of 8 requests returned content normally, so at
`max_tokens: 200` this is marginal rather than systematic — the budget simply runs
out mid-reasoning some of the time.

**Actions:** raise `max_tokens` for gpt-oss, and investigate
`reasoning_effort` (harmony's own control) instead of `enable_thinking`. **Do not
assume a thinking-suppression flag works because it works on another model** —
verify per model, per F27's warning about re-verifying coherence per model.

### Coherence verified

A real completion, verbatim, from the run:

> Because intake forms contain highly sensitive personal and safety-related
> information, the service must keep that data under its own strict
> confidentiality and privacy controls to meet legal (e.g., privacy-act,
> mandatory-reporting) and ethical obligations. Running a language model on
> hardware it already controls eliminates the risk of transmitting protected data
> to a third-party cloud, reduces exposure to breaches, and gives the organisation
> full auditability and cost predictability while still gaining the benefits of AI
> assistance.

On-topic, accurate, and coherent — so the throughput above was not bought by
degrading output, per the standing rule in `CLAUDE.md`.

---

## ik_llama.cpp vs mainline through `llama-server` — the RELIABILITY A/B, and why F27's numbers do not transfer

**Node 2 (10.10.0.39), 2026-08-18, 07:30–08:00.** Same machine, same model file
(`gpt-oss-120b-F16.gguf`), same flags (`-t 4 -c 32768 --parallel 4 --host 0.0.0.0
--port 8080 --no-warmup --jinja`), same `/v1/chat/completions` client, and — the
point of the exercise — **the same prompts in the same order**, taken from the real
document in job `06af2911d7fc` chunked at `chunk_tokens=1024`. `max_tokens=64` so
the comparison is prefill-dominated and cheap to repeat.

**This measurement exists because F27's does not describe the deployment.** F27 used
`llama-bench`, which issues one sequence. The server runs four slots. See F40.

### The headline is not a speed number, it is a survival number

| Request | prompt tokens | ik_llama.cpp `8337e4cd` | mainline `b10369` |
|---|---:|---|---|
| 1 | 1339 | ok | ok |
| 2 | 1410 | ok | ok |
| 3 | 1126 | ok | ok |
| 4 | 1060 | ok | ok |
| **5** | 995 | **FATAL — `iqk_flash_attn.cpp:347`, server wedged** | **ok** |

Reproduced on ik **twice from a clean restart**, both times on request 5, both times
on the slot-0 wrap-around. Mainline logged `kv_unified = 'false'` at startup — each
slot owns its own KV cache — and **completed all 9 requests of the run, two full
slot cycles**, ending `ALL REQUESTS SURVIVED`.

### Speed, on the requests both engines completed

Read from the servers' own `slot print_timing` lines, never from client-side
timing (F17).

| prompt tokens | ik prefill (tok/s) | mainline prefill (tok/s) | ik advantage | ik gen (tok/s) | mainline gen (tok/s) |
|---:|---:|---:|---:|---:|---:|
| 1339 | 23.25 | 16.29 | **+42.7%** | 5.21 | 5.26 |
| 1410 | 21.46 | 16.26 | **+32.0%** | 5.22 | 5.26 |
| 1126 | 19.64 | 16.27 | **+20.7%** | 5.24 | 5.34 |
| 1060 | 18.76 | 16.36 | **+14.7%** | 5.24 | 5.34 |

End-to-end wall clock for the same four requests: ik **69.9 / 78.0 / 69.5 / 68.7 s**
against mainline **94.4 / 98.9 / 81.2 / 76.8 s** — ik ahead by 26% / 21% / 14% / 11%.

**Three corrections to what F27 led this project to expect:**

1. **The prefill advantage is not a flat +52%, and it decays.** Through the server on
   real harmony-templated requests it ran **+43% down to +15%**, because ik's own
   prefill rate falls monotonically as the KV cache fills (23.25 → 21.46 → 19.64 →
   18.76 tok/s) while **mainline's does not move** (16.29, 16.26, 16.27, 16.36 —
   a spread of 0.6%, and if anything trending up). F27's `pp512`/`pp2048` pair could
   not show this: it measures a cold cache twice.
2. **The −14% generation penalty did not appear.** Both engines generated at
   **~5.2–5.3 tok/s**, indistinguishable. F27's mainline figure of 6.04 tok/s came
   from `llama-bench`; through `llama-server` with `--jinja` and four slots, mainline
   generates at 5.26. The trade F27 described — buy prefill, pay generation — is not
   the trade actually on offer.
3. **So the honest end-to-end figure for adopting ik is ~+11% to +26% on short
   requests, not +22% guaranteed** — and it is only collectable for four requests.

### What this costs, and what it buys

Moving node 2 to mainline costs roughly **20% of prefill throughput** on
document work. It buys a server that finishes the document. Given that prefill is
79% of wall clock, ~20% of prefill is ~16% of end-to-end — **the price of the
mitigation is about one sixth of the time, against a current failure rate of 100%
of jobs longer than four chunks.**

Untested, and both must be measured before either is adopted (F40): ik at
`--parallel 1`, and ik with flash attention explicitly off.
---

## Watchdog liveness signals — what separates BUSY from WEDGED (2026-08-18)

Measured on node 1, `llama-server@8080.service`, ik_llama.cpp, gpt-oss-120b-F16,
`-t 4 -c 32768 --parallel 4`, `n_threads_http=7`, `n_ctx_slot=8192`. These are the
numbers `cluster/llama-watchdog.sh` is built on.

### Cgroup CPU (`CPUUsageNSec`) — the discriminator

| Server state | 3 s window | 10 s window | 20 s window | % of one core |
|---|---:|---:|---:|---:|
| idle | **0 ms** | **0 ms** | **0 ms** | 0% |
| ONE real completion in flight | 12,028 ms | 40,046 ms | — | **400%** |
| 4 concurrent completions | — | 39,827–40,161 ms | — | **398–401%** |
| SIGSTOPped (simulated wedge) | — | 0 ms | 0 ms | 0% |

Exactly zero against exactly 400%, holding across six consecutive 10 s windows
spanning both prefill and generation. **The 3 s window separates them as cleanly
as the 20 s window**, so the 10 s default is margin, not necessity.

Machine-wide **load average was 2.27–3.09 throughout**, from unrelated agent
processes — which is why load average is not usable here (F39: node 1 read 2.30
while llama-server was completely idle).

### `/health` latency, and why its timeout is not diagnostic

On an idle (CPU-flat) server, 20 consecutive samples:

| endpoint | min | max | codes |
|---|---:|---:|---|
| `/health` (loopback) | 0.736 ms | 0.912 ms | 200 × 20 |
| `/health` (via 10.10.0.34) | 0.712 ms | 1.211 ms | 200 × 10 |
| `/props` | 0.649 ms | 0.710 ms | 200 × 5 |

Because CPU is checked first, `/health` is only ever asked when the unit burned
no CPU — i.e. when a healthy server is idle. In that branch it answers in under
a millisecond or never. **Any timeout above a few hundred ms yields the same
verdict**; 5 s is used purely for transport headroom (~520× F28's 9.544 ms
saturated RTT).

### The probe's own footprint on a busy server

One `curl --max-time 5 /health` issued during four concurrent completions:
curl gave up at 5.001 s; the server logged the request **75 seconds later** with
`status=200`. That is 75 s of one of seven http workers pinned in
`queue_results.recv()`, per probe. Across a **ten-tick busy run the CPU-first
watchdog issued zero HTTP requests** (`log_server_request` count over the whole
window: **0**).

### Per-service signals — the CPU predicate does NOT generalise

| service | idle CPU over the window | idle is… | predicate used |
|---|---:|---|---|
| `llama-server@8080` | 0 ms | a **fault** when silent | cgroup CPU, then `/health` |
| `rpc-server@50052` | **0 ms while perfectly healthy** | **NORMAL** | unit active + RPC port accepting *on the node* |
| `missing-link` | 5,061 ms / 3 s = **0.17% of a core** | **NORMAL** | HTTP + read-only job-store counters |

Applying the llama-server predicate to either of the other two would restart
healthy services on an idle cluster.

### `/proc/<MainPID>/stat` is unusable on this fleet

`llama-server@.service` runs `ExecStart=/bin/sh -c '...'` and the shell does not
exec away: `MainPID=72777` is `comm=sh`, 1 thread, `utime=0 stime=0`, while its
child `72781` (`comm=llama-server`) burns 400% of a core. The `utime+stime`
signal recommended in `docs/watchdog-research.md` reads **identically zero** here
and would classify every busy server as wedged. Cgroup accounting covers both
processes; `CPUUsageNSec` 6,569,646,000 ns == `cpu.stat usage_usec` 6,569,646.

### Missing Link's job store is in WAL mode

`pragma journal_mode` → `wal`. A read-only SQLite connection to a WAL database
needs **write** access to the `-shm` index, so `mode=ro` as the unprivileged
watchdog user returns `-1` for every count while succeeding for the owner. The
watchdog is not given write access to the job store; the query is re-run as the
store's own user through one narrow `sudoers` rule.

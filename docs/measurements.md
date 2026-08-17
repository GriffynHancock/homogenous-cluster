# Measurements

Every performance number cited anywhere must appear here first, with the date
and the hardware it was measured on. Arithmetic estimates do not belong in this
file — where an estimate is unavoidable it is labelled **ESTIMATE** inline and
must be replaced by a measurement before it is cited.

---

## Hardware baseline — node 1

**Date:** 2026-08-12
**Node:** node1 (hostname `debian1`), LAN `10.10.0.34`, Tailscale `100.92.186.88`

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

### The DIMMs are probably only in 2 of 4 channels

**INFERRED, pending `dmidecode` confirmation.**

| Configuration | Theoretical | Measured / theoretical |
|---|---:|---:|
| Quad-channel DDR4-2400 | 76.8 GB/s | 28.2 → **37%** |
| **Dual-channel DDR4-2400** | **38.4 GB/s** | 28.2 → **73%** |

37% of theoretical is implausibly low for a healthy system; **73% is a textbook
STREAM result.** The Xeon E5-1620 v4 supports quad-channel, so the board is
very likely **half-populated** — e.g. 2 × 64 GB rather than 4 × 32 GB.

**If confirmed, redistributing the DIMMs across all four channels is close to a
free doubling of generation throughput, fleet-wide, at zero hardware cost.**
That is worth checking on every node before any of them are racked, and it is
exactly the kind of advice the eventual skill should give.

**Action:** confirm with `sudo dmidecode -t memory`. If dual-channel, physically
rebalance and re-run this measurement before accepting any generation number as
a baseline.

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

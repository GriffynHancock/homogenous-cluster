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

## Single-node baseline

Pending — Task 3.

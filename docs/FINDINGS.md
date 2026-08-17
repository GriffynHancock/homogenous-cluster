# Findings

Things learned by actually running this on hardware, that were not known — or
were known *wrongly* — when the plan was written. Intended to be folded back
into `CLAUDE.md`, `STATUS.md` and the spec.

Each entry is labelled **CONFIRMED** (verified against primary source or
observed directly), **REPORTED** (someone else states it), or **INFERRED**.

---

## F1. The "75% per-node RAM ceiling" is folklore. The citation is wrong.

**CONFIRMED.** This project has treated *"hard per-node ≤75% of physical RAM
(llama.cpp #15055, unfixed)"* as a binding architectural constraint. It appears
in `CLAUDE.md`, in the plan's Global Constraints, and it is what sets the
~672 GB model budget. It does not survive checking.

What issue [#15055](https://github.com/ggml-org/llama.cpp/issues/15055)
actually says:

- It was **not a percentage-of-RAM rule.** It was an **OS limit on a single
  `send()`/`recv()` syscall buffer** (somewhere between 1–2.5 GB). Tensors
  larger than that failed with `EINVAL`, which the client misreported as
  `"Remote RPC server crashed or returned malformed response"` — the exact
  string this repo has been treating as the signature of the 75% rule.
- The reporter's 75% correlation (378.58 GB worked, 379.55 GB failed on a
  512 GB machine) was **coincidental.** It tracked individual tensor sizes
  crossing the syscall limit as the model grew, not any guard rail in the code.
- **It is CLOSED, not "unfixed".** Fixed by PR
  [#15188](https://github.com/ggml-org/llama.cpp/pull/15188) ("chunk
  send()/recv() to avoid EINVAL for very large tensors over RPC"), merged
  2025-08-13, commit `e71d48e`. That is long before any tag we would consider —
  it is in every candidate build.

**What to do about it.** Do not simply delete the 75% number and reclaim the
memory. Two separate things were conflated, and only the *citation* is
disproven:

1. The stated mechanism is wrong and the issue is fixed → **correct the
   citation, stop calling it "unfixed", stop predicting that error string.**
2. Whether a node can safely be filled past 75% is now **an open empirical
   question, not a known constraint.** There are real reasons to keep headroom
   that have nothing to do with #15055 — page cache for the GGUF, the KV cache
   growing with context, and llama.cpp's history of overcommit-related OOM
   kills (#22629, which `setup.sh` already mitigates via
   `vm.overcommit_memory=1`).

**Action:** keep 75% as a deliberate *safety margin* while measuring, and test
the real ceiling empirically on node 1 before sizing Model B. If nodes can
safely run at 85%, the pooled budget goes from ~692 GB to ~784 GB, which
changes which Kimi K2 quant is reachable. That is worth an hour of measurement.

**This is exactly the failure mode `docs/measurements.md` exists to prevent** —
a number with a citation that nobody re-read, propagating into architecture.

---

## F2. An open, unmerged upstream bug specifically hits multi-worker RPC clusters

**CONFIRMED (issue exists and is open); INFERRED (whether it hits our models).**

PR [#26500](https://github.com/ggml-org/llama.cpp/pull/26500) — *"rpc: avoid
serializing buffers from other servers"* — open and unmerged as of 2026-08-12,
zero formal reviews.

- **Symptom:** when a graph tensor's buffer belongs to a *different* RPC server
  than the one computing the node, the pointer is serialized anyway. The worker
  aborts with `[create_node] invalid data ptr` and the server is unusable.
- **Trigger is worker *count*, not backend.** Reported to reproduce with **2 or
  more** RPC workers and never with one, across CUDA, Vulkan **and CPU**.
- Linked issue [#26820](https://github.com/ggml-org/llama.cpp/issues/26820)
  reproduces it on a **9-node, CPU-only cluster** (1 coordinator + 8 workers)
  running a **sparse MoE** model. That is our topology and our model class.
- It is fixed in **no released tag**, so no pin avoids it.
- The related CPU-only case *was* fixed in #21030; this is the remaining
  RPC-to-RPC case.

**Action — this changes the plan's task order.** The plan goes straight from
provisioning the fleet (Task 5) to a 7-node Kimi K2 launch (Task 7). Insert a
**2-worker smoke test as soon as node 2 exists**, before fetching 550 GB or
provisioning nodes 3–7. If it fails with `[create_node] invalid data ptr`,
the options are to cherry-pick #26500 onto the pinned tag (small, self-
contained) or wait for merge. Finding this out at node 2 costs an afternoon;
finding it out at node 7 costs a week.

---

## F3. Model load is single-core serialized — a ~550 GB load will be slow

**REPORTED.** Issue
[#25890](https://github.com/ggml-org/llama.cpp/issues/25890): a 535 GB load took
~15 minutes with the NIC and 95 cores idle, because read + hash + dispatch is
serialized on one core. Open PR #26291 ("parallelize cached tensor hashing
during model load") targets it, unmerged as of 2026-08-09.

Directly relevant: Model B is ~550 GB. Expect slow first load, and note that
**node 1 has only 4 cores**, so the serialized path has no fast core to hide
behind. Budget for it in Task 7 rather than treating a long load as a hang.
Not a correctness problem.

---

## F4. `--parallel > 1` asserts against the RPC backend (prompt cache)

**REPORTED.** Issue
[#26128](https://github.com/ggml-org/llama.cpp/issues/26128): the server prompt
cache is incompatible with the RPC backend and asserts as soon as `-np > 1`.

This lands right on the project's top open question — whether MoE batching
buys seats at all. It may mean the `llama-batched-bench -np 1,2,4,8` test
cannot even run over RPC on the pinned tag. **Run the batching measurement
single-node against gpt-oss-120b first** (Model A, no RPC involved), where the
question is answerable cleanly, before trying it on the cluster.

---

## F5. Async/pipelined RPC is not coming soon

**CONFIRMED.** PR
[#18626](https://github.com/ggml-org/llama.cpp/pull/18626), by the RPC
maintainer, is **still open, not merged, `mergeable_state: dirty`**, 7+ months
in, with `cpy_tensor_async()` still incomplete (maintainer called it "a hard
one", 2026-02-21).

`STATUS.md` says "re-benchmark and re-pin when it lands." Fine — but nothing in
any released tag has it, so **no current performance expectation should assume
pipelining.** Sequential per-layer execution is the reality to design against.

---

## F6. Pin recommendation: b10369

**CONFIRMED reasoning.** The #21006 / #20908 `--tensor-split` regression that
`STATUS.md` warns about was fixed by PR
[#21030](https://github.com/ggml-org/llama.cpp/pull/21030), merged 2026-03-27,
commit `ba38f3b`. Verified via the GitHub compare API to be an ancestor of
b10369 (1817 commits behind head, 0 ahead). **The `b8492`-and-later warning in
`STATUS.md` is stale and should be removed** — it has been safe since March.

There is no "stable" channel to retreat to: b-tags are cut several times a day
straight off `master` (20 tags in the four days to 2026-08-12). An older pin
buys nothing, because the bugs found recently (F2, F3, F4) are long-standing
latent bugs *newly discovered*, present across the whole range.

---

## F7. Node 1 is a 4-core Broadwell with no AVX-512 — prefill is the risk

**CONFIRMED by direct observation.** See `docs/measurements.md` for the full
table. The plan assumed "ECC implies Xeon" and derived thread counts from
`nproc`. Both need care:

- **Xeon E5-1620 v4: 4 physical cores, 8 threads.** `nproc` reports 8, but half
  are SMT siblings. `rpc-server -t $(nproc)` — which the plan's
  `install-services.sh` does — may therefore be the *wrong* value fleet-wide.
  Measure `-t 4` against `-t 8` before baking it into the systemd unit.
- **No AVX-512** (Broadwell predates it). Prefill is compute-bound and this is
  the ISA that would have carried it.

Together these make **TTFT, not tokens/sec, the metric most at risk**, and make
the plan's GPU-revisit threshold (TTFT > 90 s at ~2000 tokens) considerably more
likely to trigger than assumed. It also independently strengthens the
map-reduce chunking decision: small chunks keep prefill in its efficient range.

---

## F8. `GGML_NATIVE=ON` is a trap on a salvaged fleet

**INFERRED**, but the failure mode is well understood and the cost of avoiding
it is near zero.

The plan's `build-llama.sh` used `-DGGML_NATIVE=ON`, which bakes `-march=native`
for the *build* machine. Binaries are then distributed fleet-wide. On genuinely
identical hardware that is free performance; on salvaged hardware it is a trap,
because an older-CPU node will:

1. accept the binary,
2. **pass the RPC version handshake** (which compares version strings, not ISA),
3. load the model,
4. then die with SIGILL partway into a graph, with nothing in the logs pointing
   at the cause.

`distribute.sh` asserts on version and libc but **cannot catch this.**

**Action taken:** `build-llama.sh` now builds `-march=haswell -mtune=native`
by default — keeps AVX2/FMA/F16C (everything node 1 has), safe on anything from
2013 onward. `LLAMA_MARCH=native` remains available once every node's ISA is
confirmed identical. Worth adding an ISA assertion to `distribute.sh`.

---

## F10. `-t $(nproc)` is the wrong value, and the plan bakes it in fleet-wide

**CONFIRMED by measurement.** Full table in `docs/measurements.md`.

On node 1 (4 cores / 8 threads), sweeping `llama-bench -t 1,2,4,6,8`:

- Prefill scales linearly to 4 threads (8.51 → 17.32 → 32.39 t/s) then **stops
  dead** — 32.19 at 6, 32.58 at 8. SMT adds nothing.
- Generation peaks at 4 threads (11.19 t/s) and is **26% slower at 8** (8.31).
  SMT siblings contend for the same memory pipe and actively hurt.

`cluster/install-services.sh` writes `RPC_THREADS=$(nproc)` to every node —
which here is 8, **the worst of the five values tested**. The plan correctly
warns that `rpc-server -t` *defaults* to half the cores and must be set
explicitly; it then sets it to the wrong number.

**Action:** derive from physical cores, not logical:

```bash
lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l
```

Generalise for the skill: **on a bandwidth-bound workload, threads should equal
physical cores.** This is not an artefact of this CPU — it follows from
generation being memory-bound, which F11 establishes directly.

---

## F11. Generation already runs at ~99% of memory bandwidth. Software tuning is exhausted.

**CONFIRMED by two independent measurements that agree to within 1%.**

A STREAM triad over a 4.8 GB working set gives **28.2 GB/s peak** (at 4
threads; 13.7 at 1, 24.2 at 2, 27.3 at 8 — the same SMT penalty as F10).

Independently, from llama.cpp's own throughput: Qwen3-4B Q4_K_M is 2.32 GiB
= 2.49 GB, dense, so every token reads the whole model.

```
11.19 tok/s × 2.49 GB = 27.9 GB/s
```

**27.9 GB/s implied vs 28.2 GB/s achievable — llama.cpp is at ~99% of the
memory roof.**

Two consequences, both significant:

1. **Generation speed cannot be improved in software on this hardware.** Thread
   tuning, quant format, NUMA flags, `ik_llama.cpp` — all bounded by the same
   ceiling. The open question in `STATUS.md` about whether `ik_llama.cpp` beats
   mainline is, for *generation*, answerable in advance: it cannot, by more
   than ~1%. It may still help prefill, which is compute-bound.
2. **`tok/s ≈ achievable_bandwidth / bytes_per_token` is a reliable predictor
   here**, because the efficiency factor is ~1. That makes it a legitimate
   sizing tool for the skill — *provided* the bandwidth term is measured with
   STREAM rather than taken from a datasheet, which is the whole point.

---

## F12. Bandwidth is core-limited, not channel-limited. The CPU cannot saturate its own memory.

**CONFIRMED by `dmidecode`.** An earlier version of this finding hypothesised a
half-populated board. **That was wrong** — the DIMM layout is correct and
optimal:

```
DIMM_1..DIMM_4:  32 GB, DDR4, Rank 2
Speed: 2400 MT/s      Configured Memory Speed: 2400 MT/s
Board: LENOVO 30B2S2E800 (ThinkStation P510), 8 slots, 4 populated
```

All four channels populated, running at full rated speed. So the 28.2 GB/s
measured is **37% of the 76.8 GB/s a quad-channel DDR4-2400 bus can deliver**,
with nothing wrong with the memory.

**The explanation is the core count.** From the STREAM thread sweep:

| Threads | GB/s | Scaling vs 1 core |
|---:|---:|---:|
| 1 | 13.7 | — |
| 2 | 24.2 | 1.77× |
| 4 | **28.2** | **2.06×** |
| 8 | 27.3 | 1.99× |

A single core already reaches 13.7 GB/s; four cores add only ~2×, not 4×.
**A core can only keep ~10–12 cache-line fills in flight, so saturating
quad-channel DDR4 needs roughly 8–14 cores on Broadwell.** The E5-1620 v4 has
four. It is a workstation part with server-grade memory attached, and it cannot
generate enough memory-level parallelism to use it.

**Consequences:**

- **Neither DIMM rearrangement nor a memory clock change will help.** The
  bottleneck is upstream of the DRAM.
- **The one BIOS lever worth trying** is the power/uncore profile. On Xeon E5 v4
  the uncore — which contains the memory controller — scales its own frequency,
  and an "Energy Efficient" or "Balanced" profile holds it down. Set **Maximum
  Performance**, disable **C-states**, pin **Uncore Frequency** to max if
  exposed, and try **Home Snoop** vs **Early Snoop**. Expect ~10–20%, not 2×.
  Re-run the STREAM measurement after any change.
- **CPU frequency is already ruled out** — under load all cores sit at 3592 MHz
  with turbo on and `max_perf_pct` 100.
- **Core count is a bandwidth spec, not just a compute spec.** This is the real
  lesson. If nodes 3–7 turn out to have higher-core Xeons (E5-2600 v4 series,
  8–14 cores), **those nodes will be meaningfully faster at generation** despite
  identical RAM. Heterogeneous core counts imply heterogeneous bandwidth, and
  therefore that `--tensor-split` should weight by **measured bandwidth**, not
  by RAM as `nodes.env` currently assumes.

**For the skill:** "how much RAM" and even "how many channels" are both
insufficient. The assessment must **measure achievable bandwidth with STREAM**
and compare it against `channels × MT/s × 8`. A large gap means the CPU is the
limit, and the advice is *more machines*, not *more RAM* — which is exactly the
opposite of what a capacity-driven inventory would conclude.

---

## F13. `rpc-server` was renamed, and the binaries were not relocatable

**CONFIRMED by direct observation.** Both would have failed only on the
workers, i.e. after six machines were committed.

**1. The target is now `ggml-rpc-server`.** `tools/rpc/CMakeLists.txt` in
b10369 reads `set(TARGET ggml-rpc-server)`. Every script and systemd unit in
this repo says `rpc-server`, as does every guide online. `build-llama.sh` now
installs a `rpc-server -> ggml-rpc-server` symlink rather than chasing the
rename through the fleet.

**2. Default RPATH pointed into the build tree.** ggml builds as **shared**
libraries — `llama-server` is an 18 KB stub against `libllama-server-impl.so` —
and `ldd` showed:

```
libggml.so.0 => /opt/llama.cpp/src/build/bin/libggml.so.0
```

A worker has no `/opt/llama.cpp/src`. The binaries would have passed every
check on the build machine and failed on all six workers with a loader error
resembling nothing in the plan's troubleshooting notes — and `distribute.sh`'s
version and libc assertions cannot catch it.

**Fixed** with `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`,
verified by copying `bin/` elsewhere and running it (`RUNPATH: [$ORIGIN]`,
resolves relative, runs standalone). `build-llama.sh` now **fails the build**
if any binary still references the source tree, and copies `*.so*` alongside
the executables.

---

## F14. RPC costs 5% on generation but 39% on prefill

**CONFIRMED by measurement.** Localhost isolation test, network removed
entirely — so this is a **floor**, not an estimate.

| Metric | Local | Via RPC (localhost) | Overhead |
|---|---:|---:|---:|
| pp512 (prefill) | 33.18 t/s | 20.11 t/s | **−39.4%** |
| tg128 (generation) | 11.55 t/s | 10.95 t/s | **−5.2%** |

**The gate passes** — the plan's rule is on generation, under 15%. And the 5.2%
vindicates this repo's earlier retraction of the "30–55% RPC overhead" claim:
for bandwidth-bound CPU generation, per-token compute dwarfs a loopback
round-trip, exactly as predicted.

**But the plan only ever specified a threshold for generation, and prefill is
where the cost actually landed.** −39.4% compounds with hardware already weak
at prefill (4 cores, no AVX-512). Prefill is compute-bound, so a fixed
serialisation cost is a far larger fraction of a smaller number.

**Recommendation:** the decision rule in the plan should gate on **both**
metrics, not just generation. A future run of this test on different hardware
could pass the generation gate while prefill quietly becomes unusable — which
is very close to what happened here.

Also worth noting: `llama-bench -t` does **not** propagate to an RPC device
(the rows report `threads = -1`). The `rpc-server`'s own `-t` governs, which is
why F10 matters so much — a wrong `-t` in the systemd unit is invisible to
every client-side benchmark flag.

---

## F15. `llama-cli` is now conversation-first with a TUI — `-no-cnv` is not enough for scripting

**CONFIRMED by direct observation.** In b10369 `llama-cli` launches an
interactive TUI (banner, `/exit`, `/regen`, `/read` commands) and **blocks
waiting for input**, even with `-p` supplied. The plan's Task 3 Step 1 command
hangs indefinitely — it ran past 600 s producing nothing before being killed.

Use **`-st` / `--single-turn`** for any scripted invocation:

```bash
llama-cli -m MODEL -t 4 -st --no-warmup --no-display-prompt -n 120 -p "..."
```

It prints a `[ Prompt: X t/s | Generation: Y t/s ]` summary line on exit, which
is a convenient cross-check against `llama-bench`. Verified coherent output on
node 1 (31.2 t/s prompt, 11.3 t/s generation — consistent with the bench).

Anything in Missing Link or the skill that shells out to `llama-cli` must use
`-st`, or it will hang forever in a queue worker with no diagnostic.

---

## F16. Model B does not fit on the master's DISK. Disk, not RAM, is the binding constraint.

**CONFIRMED by direct observation.** This blocks Task 7 as written.

The plan reasons carefully about two memory constraints and never checks disk.
On node 1:

```
/dev/nvme0n1p2  467G  13G  431G  3%  /
```

**431 GB free when first measured on 2026-08-12 — now 368 GB**, because
gpt-oss-120b (61 GB) has since landed. **Re-run `df -h /` before choosing a
quant**; the table below is against the original 431 GB and two rows have
already fallen over the line. Model B as specified — Kimi K2 Q4 — is **547 GB** (IQ4_XS,
12 files). The master must hold the entire GGUF locally: `llama-server` loads it
and pushes tensors to the workers, so there is no version of this where the
master gets away with a partial copy.

Measured quant ladder (`unsloth/Kimi-K2-Instruct-GGUF`, sizes summed per quant):

| Quant | Size | Fits in 431 GB? | Fits alongside Model A (~366 GB free)? |
|---|---:|---|---|
| IQ4_NL | 578 GB | no | no |
| **IQ4_XS** (the plan's "Q4") | **547 GB** | **no** | no |
| Q3_K_M | 489 GB | no | no |
| UD-Q3_K_XL | 452 GB | no | no |
| Q3_K_S | 442 GB | no | no |
| UD-IQ3_XXS | 417 GB | **no — was "yes" at 431 GB free** | no |
| UD-Q2_K_XL | 382 GB | **no — was "yes" at 431 GB free** | no |
| UD-IQ2_M | 347 GB | yes, ~21 GB spare | yes |
| UD-IQ1_S | 280 GB | yes | yes |
| UD-TQ1_0 | 244 GB | yes | yes |

**The constraint ordering in the plan is now wrong.** It says the per-node 75%
RAM rule binds first (~692 GB). In reality:

| Constraint | Budget |
|---|---:|
| Pooled RAM (7 × 131.8 GB, 15% headroom) | ~747 GB |
| Per-node 75% × 7 | ~692 GB |
| **Master local disk** | **431 GB ← binds, by a wide margin** |

**The thesis survives at every quant on this list.** Even UD-TQ1_0 at 244 GB is
roughly double what one node's 125 GB of RAM can hold, so "run what no single
machine could hold" still holds. What changes is *how much* headroom the
argument has.

**Two ways out, and they are genuinely different decisions:**

1. **Add storage to the master.** A 2 TB SSD is cheap, removes the constraint
   permanently, and restores the full-fat Q4 comparison. Model load is a
   sequential read and happens once per boot (workers cache via `rpc-server -c`),
   so even a spinning disk would serve. This is the option that keeps the
   deliverable as designed.
2. **Step down the quant.** UD-IQ3_XXS (417 GB) is the largest that fits today,
   and cannot coexist with Model A. UD-IQ2_M (347 GB) leaves room for both.

**For the skill, this generalises into an assessment question the plan never
asked:** salvaged desktops are usually RAM-rich and disk-poor, because disks
get pulled or wiped on decommission while DIMMs stay in. **The coordinator's
free disk space is a first-class constraint and should be checked before any
model is chosen** — it is cheap to fix, but only if you find it before
downloading half a terabyte.

---

## F17. The plan's TTFT measurement is wrong and reports ~0.015 s. Real TTFT was 89 s.

**CONFIRMED by measurement.** This is the most consequential methodological bug
found so far, because it silently reports the project's most at-risk metric as
essentially zero.

The plan measures TTFT with:

```bash
curl -w "TTFT %{time_starttransfer}s" ... -d '{"stream": true, ...}'
```

Measured output on node 1:

```
run 1: TTFT 0.015462s   total 99.311079s
run 2: TTFT 0.016888s   total 11.306179s
run 3: TTFT 0.012565s   total 10.265632s
```

**`time_starttransfer` is when the HTTP response *headers* arrive, not when the
first token does.** `llama-server` sends headers immediately on accepting a
streaming request, so this measures connection setup — ~15 ms — and is
completely insensitive to prefill.

The truth is in the server's own log for the same request:

```
prompt eval time = 89147.95 ms / 2214 tokens ( 40.27 ms per token, 24.84 t/s)
       eval time = 10147.46 ms /   64 tokens (158.55 ms per token,  6.31 t/s)
```

**Real TTFT ≈ 89 seconds** for a 2214-token prompt — a factor of ~5,800 off
what the plan's method reported.

### Two further traps in the same measurement

**1. Prompt caching makes runs 2 and 3 meaningless.** They report
`prompt eval time = ... / 1 tokens` — the server reused the cached prefix
because the prompt was byte-identical. Only **run 1** is a real cold-cache
measurement. Any benchmark that loops the same prompt measures the cache, not
the model. Vary the prompt per iteration, or restart the server.

**2. This affects Task 8 too.** The plan's concurrency test uses the same
`time_starttransfer` metric and the same repeated prompt, so it would have
produced meaningless numbers for the "seats vs speed" claim — the project's
central thesis.

### How to actually measure TTFT

Either parse the SSE stream for the first chunk carrying content, or read
`prompt eval time` from the server log (authoritative, and it also gives the
real token count). `bench/node-bench.sh` now does both.

### The number itself is bad news

**89 s TTFT at 2214 tokens, on a 4-billion-parameter model.** The plan's
GPU-revisit threshold is 90 s at ~2000 tokens. The *smallest* model in the
project sits right on it.

This confirms the F7 prediction from the hardware: with 4 cores and no AVX-512,
**prefill is the binding problem, not generation.** Note prefill measures
24.84 t/s here against `llama-bench`'s 28.33 t/s at pp2048 — consistent, and
both far below what this workload needs.

Implications, none of which are optional now:

- **Map-reduce chunking is not merely preferable, it is required.** A 4K chunk
  is ~160 s of prefill on this hardware. Feeding whole documents is not viable.
- **`-ub` tuning matters** and should be measured, not assumed.
- **The GPU-for-prefill question is live.** The spec defers it until TTFT proves
  unbearable; on this evidence it is close to unbearable already, and the
  cluster's own RPC layer adds a further −39.4% to prefill (F14). A single cheap
  GPU doing prefill only, with generation staying on CPU, deserves measurement
  rather than continued deferral.

---

## F18. Raising `-ub` does not help CPU prefill. The spec's expectation was wrong.

**CONFIRMED by measurement.** `STATUS.md` states: *"`-ub` (default 512) is worth
raising for CPU prefill — a larger ubatch amortises weight loading across more
tokens, targeting TTFT directly."* Measured on node 1, pp2048, `-t 4`:

| `-ub` | pp2048 (t/s) |
|---:|---:|
| 512 (default) | 27.18 ± 0.12 |
| 1024 | 26.60 ± 0.77 |
| 2048 | 27.61 ± 0.07 |

**All three within noise of each other.** No gain.

**Why the reasoning failed, and it is diagnostic.** Amortising weight loading
only helps if weight loading is the bottleneck. It is not: prefill is
**compute**-bound, and this CPU has 4 cores and no AVX-512, so the GEMM itself
is the limit. There is no idle memory bandwidth for a bigger ubatch to exploit —
generation already proves the bus is saturated at 28.2 GB/s by 4 cores (F12),
and prefill uses less bandwidth than generation, not more.

**Consequence: there is no software lever left for TTFT on this hardware.**
`-t` is saturated at 4 (F10), `-ub` does nothing, AVX-512 is absent, and the
Quadro P600's 2 GB cannot hold enough layers of a 93 GB model to matter. The
only remaining levers are *architectural* — smaller chunks, or accepting the
latency. Which leads directly to F19.

---

## F19. The 90 s TTFT threshold is the wrong gate for this project's actual workload

**Analysis, following from measured numbers.** Worth resolving explicitly
because F17 makes it look like the project is in trouble when it may not be.

The plan says: *"If TTFT at ~2000 tokens exceeds 90 seconds, note it prominently
and raise it with the user"*, with GPUs as the remedy. Node 1 measured **89 s on
a 4B model** — apparently right at the wall.

**But that threshold is an interactive-chat instinct, and this project is
explicitly not interactive.** `CLAUDE.md` is unambiguous: *"submit overnight,
read in the morning"*, *"slow is fine; nobody is waiting at a prompt"*, and
Missing Link exists precisely to convert *"too slow to be useful"* into *"fast
enough for this class of work"*.

For an async batch workload, **TTFT per chunk is not a user-facing metric at
all.** The user-facing metric is **wall-clock time for a whole document**, and
that is dominated by total prefill throughput, not by the latency of any one
chunk.

Worked example, node 1, Qwen3-4B, a ~50,000-token document, map-reduce at 4K
chunks with 10% overlap (≈14 chunks) — all inputs measured, the arithmetic is
composition, not extrapolation:

| Stage | Rate | Time |
|---|---|---|
| Prefill, 4K tokens × 14 chunks | 24.8 t/s | ~38 min |
| Generation, ~500 tok summary × 14 | 11.2 t/s | ~10 min |
| Reduce step | — | ~2 min |
| **Total** | | **~50 min** |

**Fifty minutes for a 50,000-token document, unattended, is a perfectly good
result for the stated use case.** Nobody is watching.

**Recommendation: replace the TTFT gate with a throughput gate.** Something like
*"a 50K-token document must complete within one overnight window (≈10 h)"* is
the decision-relevant question. Keep measuring TTFT — it is diagnostic, and
`CLAUDE.md` is right that it must be reported separately — but **stop treating
90 s as a stop-the-line threshold**, because it will fire on every model in the
project while the actual workload remains comfortably viable.

**This also settles the GPU question for now.** The Quadro P600 has 2 GB and
cannot hold meaningful layers of any target model, so "GPUs for prefill" is not
executable with the hardware on hand regardless. Revisit only if the throughput
gate fails.

---

## F20. The plan's job-claim logic races and runs jobs twice

**CONFIRMED by reproduction.** The plan's `claim_next_pending` (Task 10, Step 4)
is a `SELECT` followed by an `UPDATE` inside a `with conn:` block. Python's
sqlite3 driver opens a **deferred** transaction there, so the write lock is not
taken until the `UPDATE` — leaving a window in which two workers read the same
pending row and both claim it.

Reproduced with 8 threads draining a 20-job queue, three consecutive runs:

```
claims: 26  unique: 20   DOUBLE-CLAIMED
claims: 24  unique: 20   DOUBLE-CLAIMED
claims: 23  unique: 20   DOUBLE-CLAIMED
```

**It fails every time, not occasionally.** On a queue whose jobs are
multi-hour document summarisations, running one twice is an expensive way to
discover a race — and it would present as "the cluster is mysteriously slow"
rather than as a bug.

The plan's own test suite passes against this implementation, because every
test in it is single-threaded.

**Fixed** by taking the write lock up front:

```python
conn.execute("BEGIN IMMEDIATE")
row = conn.execute("SELECT ... WHERE status='pending' ... LIMIT 1").fetchone()
conn.execute("UPDATE jobs SET status='running' ... WHERE id=?", ...)
conn.execute("COMMIT")
```

with `isolation_level=None` so the explicit `BEGIN IMMEDIATE` is honoured rather
than overridden by the driver's own transaction handling. Added
`test_claim_is_atomic_under_concurrency` to `tests/test_db.py`, which fails
against the original and passes against the fix.

**Two related hardenings added at the same time:**

- **`requeue_running()`** — returns stranded `running` jobs to `pending` at
  worker startup. Jobs here take hours, so an OOM kill or power cut mid-job is
  routine, and the plan had no recovery path: a job stuck in `running` with no
  process behind it is invisible work that never finishes and never errors.
- **WAL journal mode + `busy_timeout`** — so the web process can read job status
  while the worker holds a write transaction. Without it the status page blocks
  behind the queue, which on a multi-hour job means the UI appears hung.

---

## F21. Reasoning models return EMPTY content when max_tokens runs out mid-thought

**CONFIRMED by direct observation on node 1.** This is the most dangerous
failure mode found so far, because it produces a **successful-looking job with
no output**.

Qwen3-4B, `max_tokens: 120`, ordinary summarisation request. The HTTP response
was `200 OK` and contained:

```
finish_reason : "length"
content       : 0 characters
reasoning_content : 659 characters
```

Reasoning models (Qwen3, DeepSeek-R1 and relatives) emit their chain of thought
into a **separate `reasoning_content` field**. If the token budget is exhausted
before they finish thinking, `content` comes back as an **empty string** — a
200 OK carrying nothing.

The plan's worker does `payload["choices"][0]["message"]["content"]`, so it
would have stored `""` as the summary and called `complete_job` — marking the
job **done**. On an overnight queue that means **discovering empty summaries the
following morning, with nothing logged as an error.** For a project whose entire
value proposition is "submit overnight, read in the morning", this is the worst
possible failure.

**Fixed** with `extract_content()`, which refuses to return empty text and
raises `EmptyCompletion` with an actionable message naming the token budget and
the fix. `run_one` records it as a *failed* job, so it is visible. Five
regression tests added.

**Operational consequences:**

- **Budget `max_tokens` for thinking, not just for the answer.** A reasoning
  model needs several times the tokens its output implies.
- **Or disable thinking** — `/no_think`, or
  `--chat-template-kwargs '{"enable_thinking":false}'`. For map-reduce
  summarisation the chain of thought is pure cost: it is discarded, and on this
  hardware every token costs ~90 ms.
- **Check this per model.** gpt-oss-120b and Qwen3-Next are both affected
  classes. Any evaluation harness (Task 14) must treat empty output as a
  failure rather than scoring it as a zero-quality summary.

---

## F22. Two-worker RPC sharding WORKS on b10369 — bug #26500 does not fire here

**CONFIRMED by measurement**, and it partially retires the F2 risk earlier than
expected.

F2 warned that upstream PR #26500 (open, unmerged) breaks clusters with **2 or
more** RPC workers. The key detail is that the trigger is worker *count*, not
machines — so it can be tested on **one box** with two `rpc-server` processes on
different ports, without waiting for node 2.

Run on node 1, b10369, two `rpc-server` instances (ports 50052/50053),
`--tensor-split 1,1` to force tensors onto both:

| Check | Result |
|---|---|
| Server reached healthy | **yes** |
| `[create_node] invalid data ptr` in any log | **0 occurrences** |
| Generation completed | **yes** — 150 tokens |
| Abort / malformed-response errors | **0** |
| prompt eval | 9.72 t/s |
| generation | 6.45 t/s |

**Two-worker RPC graph compute works on the pinned build.**

**Do not over-read this.** The public reproductions of #26500 involve MoE graphs
with unusual constant/view nodes (DeepSeek-V4, Qwen3-VL), and this test used a
**dense 4B** model. The bug may well still fire on Kimi K2. So:

- **Re-run `bench/two-node-smoke.sh` with the actual Model B GGUF** before
  committing to the 7-node launch. That check is still mandatory.
- But the *architecture* is not dead on arrival, and nodes 3–7 are no longer
  blocked on this question for dense models.

**Method worth keeping for the skill:** multi-worker RPC bugs are reproducible
on a single machine with multiple `rpc-server` processes. That is a much cheaper
gate than provisioning hardware, and it should be a standard pre-flight check.

**Caveat on the numbers:** each `rpc-server` got `-t 2` here (4 cores split two
ways), so 6.45 t/s is not comparable to the 10.95 t/s single-RPC figure. This
run was a correctness test, not a performance one.

---

## F23. Workers never read model files. Only the coordinator needs the GGUF.

**CONFIRMED from source and measurement.** This substantially simplifies the
fleet's storage design, and it is not obvious from the plan.

`tools/rpc/rpc-server.cpp` contains **zero** references to GGUF loading,
`llama_model_load`, or any model file. Per `tools/rpc/README.md`, the RPC server
"allows exposing `ggml` devices on a remote host" — the coordinator reads the
model and pushes **tensors** over TCP.

What each role actually needs on disk:

| Role | Needs | Kimi K2 (547 GB, 7 nodes) |
|---|---|---|
| **Coordinator** (`llama-server`) | the entire GGUF on **disk** | **547 GB disk** |
| **Each worker** | its layer share in **RAM** | **~78 GB RAM** |
| **Each worker** | `-c` tensor cache on **disk** | **~78 GB disk** |

No worker ever needs the whole model, on disk or in RAM.

**Weights are RAM-resident, not paged from disk.** Worth stating explicitly
because the natural assumption — that a MoE keeps all experts on disk and pages
in whichever are needed — describes a *different* technique (llama.cpp's
`--override-tensor` offloading, or mmap paging), not RPC sharding.

RPC splits by **layer**, not by expert. Each node receives a contiguous layer
range and holds **every expert of those layers** resident. Measured by RSS on
two workers, `--tensor-split 1,1`:

| | Worker A | Worker B |
|---|---:|---:|
| RSS before load | 5 MB | 5 MB |
| **RSS after load** | **1268 MB** | **1449 MB** |

Combined 2717 MB against a 2382 MB model file — each worker holds its full
share resident. Only the **selected** experts are *read* per token, and that
read-volume reduction is the entire speed benefit.

**Total params set RAM. Active params set speed.** This is why a 547 GB model
needs ~692 GB of pooled RAM, and it is the reason the cluster exists at all: if
experts were paged from disk, one machine with a large disk could run Kimi K2
and there would be no project.

**Worker disk (the `-c` cache) — corrected.** Two `rpc-server -c` instances with
**separate `LLAMA_CACHE` dirs** cached 817 MB and 1.1 GB for the 2.4 GB model:
1.9 GB combined, ~76%. An earlier version of this finding measured a *shared*
cache directory and divided, which happened to give the same ratio but did not
actually demonstrate that each worker stores only its own share. The separate
dirs do.

**However, 0.76 does not generalise.** Qwen3-4B is small and dense, so sub-10 MiB
tensors (embeddings, norms) are a meaningful fraction. In a large MoE nearly
every expert tensor is far above the 10 MiB threshold, so the ratio approaches
**1.0**. **Plan worker disk at the full layer share** — 78 GB per node for
Kimi K2, not 59.

**Consequences for the storage question:**

1. **No shared storage is needed for inference.** Workers already have 477 GB
   disks against a ~78 GB requirement.
2. **The coordinator role should follow the disk, not the other way round.**
   Whichever node gets the large drive should run `llama-server`. Shipping
   547 GB to a fixed coordinator solves a problem that does not exist.
3. **A NAS/NFS mount for the model is a poor trade.** The coordinator would read
   547 GB over gigabit (~73 min at best) on every cold start, on top of a load
   path that is already single-core serialised (F3), and `mmap` over NFS is
   fragile. Local disk on the coordinator is strictly better.
4. **Workers still need their cache checked.** `-c` writes to `$LLAMA_CACHE` or
   `~/.cache/llama.cpp/rpc`, which is on `/` by default. A node with a small
   root filesystem will fill it silently during first load.

**What a manifest is actually good for** — not shared storage, but avoiding
re-downloads. Measured 21 MB/s from HuggingFace vs ~110 MB/s on gigabit LAN:
for a 547 GB model that is **7.2 hours against 1.4**. So `cluster/models.json`
records which node holds which model, and `cluster/models.sh pull` prefers a
peer over the internet. The **index** replicates to every node via git; the
**bytes** stay wherever the disk is. That is "shared storage without a NAS", and
it is the part that pays for itself.

`cluster/models.sh` also exposes `plan <id>`, which converts the manifest's
`active_params` and `bytes_per_weight` into predicted tok/s and per-node disk,
using measured bandwidth (F11).

---

## F24. Sparse MoE reaches only 61% of memory bandwidth. Every MoE estimate was ~1.6× optimistic.

**CONFIRMED by measurement.** This answers the top open question in `STATUS.md`
and revises every prediction in this repo downward.

gpt-oss-120b F16 (native MXFP4), single node, `-t 4`:

| Metric | Value |
|---|---:|
| pp512 / pp2048 | 16.03 / 15.88 t/s |
| tg128 | **6.05 t/s** |
| Bytes read per token (5.1B active × 0.559 B/param) | 2.85 GB |
| Predicted at dense efficiency | 9.88 t/s |
| **Implied bandwidth** | **17.3 GB/s** |
| **Efficiency vs STREAM (28.2)** | **61%** |

F11 measured **~99%** efficiency on a dense 4B model. **Sparsity costs ~39%.**
The mechanism is locality: a dense model sweeps its weights contiguously, while
an MoE gathers a scattered subset of experts per token — defeating hardware
prefetch and wasting part of every cache line and DRAM burst. The bytes that
*matter* are fewer, but the bytes actually *moved* per useful byte are more.

**The sizing rule now needs an architecture term:**

```
dense       : tok/s ≈ 28.2 / bytes_per_token    (99% of STREAM)
sparse MoE  : tok/s ≈ 17.3 / bytes_per_token    (61% of STREAM)
```

Validated both ways by `cluster/models.sh plan`: Qwen3-4B predicted 11.31 vs
11.49 measured (1.6% error); gpt-oss predicted 6.4 vs 6.05 measured (6%).

**Revised predictions:**

| Model | Old | **Revised** |
|---|---:|---:|
| Qwen3-Next-80B-A3B Q8 | 8.9 | **~5.4** |
| **Kimi K2 IQ4_XS (7 nodes)** | 1.76 | **~1.08** |
| Kimi K2 UD-IQ2_M (7 nodes) | 2.84 | **~1.74** |

**Kimi K2 at ~1.08 tok/s still clears the overnight bar** — ~39,000 tokens in
10 hours — but with less margin than assumed. **Provisional across models:**
gpt-oss has 128 experts at `top_k=4`; Kimi K2 has 384 at `top_k=8`, and a more
scattered gather could be worse still. Re-measure when Model B lands; do not
quote 1.08 as measured.

### A genuinely good surprise: MoE prefill barely degrades with context

| Model | pp512 | pp2048 | Change |
|---|---:|---:|---:|
| Qwen3-4B (dense) | 33.04 | 28.33 | **−14%** |
| gpt-oss-120b (MoE) | 16.03 | 15.88 | **−1%** |

The spec warns of "~58% prefill loss from 512 to 32K context" as a reason to
chunk. **On a large MoE that effect is far weaker**, because weight reading
dominates attention. Chunking is still right — for "lost in the middle" (F19)
and because prefill is 79% of document wall-clock — but *this particular*
argument for it is much weaker on the models we actually intend to run.

Composed from these rates, a 50K-token document is **~60 min prefill +
~20 min generation ≈ 80 min single-node** — well inside an overnight window.

---

## F25. Kimi K2 has the WORST measured hallucination rate of any model checked. Model B needs reconsidering.

**REPORTED** (Vectara hallucination leaderboard, `vectara/hallucination-leaderboard`,
updated 2026-05-11, 7,700+ articles across news/legal/medical/finance/education).
Not independently verified here — but decision-relevant enough to raise before
further investment.

The project's stated requirement is unambiguous: *"legally sensitive documents
where a hallucinated fact is a serious failure"*, faithfulness over style.
Against that requirement, the chosen Model B is the worst available option:

| Model | Hallucination | Active | tok/s (est) | Disk @IQ4_XS | Licence |
|---|---:|---:|---:|---:|---|
| **Kimi K2-Instruct** | **17.9% — worst of all** | 32B | 1.02 | 546 GB (needs new drive) | custom |
| **DeepSeek-V3.2** | **5.3–6.3%** | 37B | 0.88 | **363 GB — fits today** | **MIT** |
| **GLM-4.6** | **9.5%** | 32B | **1.02** | **189 GB — fits easily** | **MIT** |
| GLM-4.5-Air | 9.3% | 12B | 2.72 | 58 GB | MIT |
| gpt-oss-120b | 14.2% | 5.1B | 6.40 | 61 GB | Apache-2.0 |
| Mistral-large-2411 | 4.5% | — | — | — | — |
| Finix S1 32B | **1.8% (best listed)** | ? | ? | ? | ? |

**Kimi K2 is dominated on every axis except raw capability benchmarks:**

- **GLM-4.6** matches its speed exactly (32B active), has **half** the
  hallucination rate, is **MIT** rather than a custom licence, and needs
  **189 GB instead of 546 GB** — it fits the existing disk with room to spare,
  which would make the whole F16 disk blocker disappear.
- **DeepSeek-V3.2** has **~3× better faithfulness**, is MIT, and also fits
  today at 363 GB, for a ~14% speed cost (0.88 vs 1.02 tok/s).

### Why this matters more for us than the leaderboard implies

The Vectara benchmark measures **single-document** grounded summarisation. Our
workload is **map-reduce**, and that likely **amplifies** the problem: a
fabricated fact in a chunk summary becomes *source material* for the reduce
step, where it is indistinguishable from genuine content. Errors do not just
persist, they get laundered into the final output. **Faithfulness matters more
in our pipeline than in the benchmark that produced these numbers, not less.**

### Caveats, stated honestly

- Figures are **REPORTED** from the leaderboard, not measured here.
- The benchmark is single-doc; transfer to map-reduce is **inferred**, not shown.
- Only **Kimi K2-Instruct** is listed. `K2-Instruct-0905` and `K2-Thinking` are
  not, and may differ.
- **There is a real capability/faithfulness trade-off.** Kimi K2-Thinking scores
  84.5 GPQA-Diamond; the smaller alternatives score lower on reasoning. If the
  workload needs frontier reasoning, that argues back toward K2.
- Task 14's evaluation harness exists precisely to settle this **on our own
  documents**. These numbers should redirect the shortlist, not replace the
  measurement.

### Recommendation

**Do not fetch Kimi K2 yet.** Re-open the Model B decision with GLM-4.6 and
DeepSeek-V3.2 as the leading candidates. GLM-4.6 in particular looks strictly
better for this project: same speed, half the hallucination, permissive licence,
and **it removes the need for the new coordinator drive entirely.**

---

## F26. Kimi K3 exists, and is firmly out of scope

**CONFIRMED** (`moonshotai/Kimi-K3` on HuggingFace, weights pushed 2026-07-27):
**2.78T total, 104B active**, 1M context, custom licence. GGUF quants exist
(`unsloth/Kimi-K3-GGUF`) at **1.51 TB** for UD-Q4_K_XL; the smallest 1-bit quant
is still 466 GB.

**104B active is ~3.25× Kimi K2's 32B**, so on this hardware it would read
~3.25× the bytes per token — roughly **0.3 tok/s**, before the size problem.
At 1.51 TB it also exceeds the ~692 GB pooled RAM budget by more than 2×.

**Not a candidate.** Worth recording so the question is not re-opened: newer and
larger is actively worse here, because active parameters — not capability — set
speed.

---

## F27. ik_llama.cpp is 52% faster at prefill and 14% slower at generation — net +22% for document work

**CONFIRMED by measurement**, same model file, same flags, same machine. This
settles the last open software question in `STATUS.md` and it is the only
remaining lever that delivered.

`llama-bench -m gpt-oss-120b-F16.gguf -t 4 -p 512,2048 -n 128 -r 2`

| Metric | mainline b10369 | **ik_llama.cpp 8337e4cd** | Delta |
|---|---:|---:|---:|
| pp512 | 16.57 | **25.24** | **+52%** |
| pp2048 | 16.08 | **24.49** | **+52%** |
| tg128 | **6.04** | 5.17 | **−14%** |

**Output verified coherent** — a hospital/data-sovereignty prompt produced
accurate, on-topic prose citing HIPAA, availability and existing IT investment.
A faster fork that degraded output would be worthless; this one does not.

### Why the trade lands in our favour

The two effects pull in opposite directions, and **prefill is 79% of document
wall-clock**, so the prefill win dominates:

| Build | Prefill | Generation | **Total (50K-token doc, 14 chunks)** |
|---|---:|---:|---:|
| mainline | 59.4 min | 19.3 min | **78.8 min** |
| **ik_llama** | **39.0 min** | 22.6 min | **61.6 min** |

**~22% faster end-to-end.** Combined with replication across 7 nodes, that is
**~8.8 minutes per document equivalent.**

This also explains the "evidence split" the spec noted: people benchmarking
**generation** find ik_llama slower and conclude it is not worth it; people
benchmarking **prefill** find it much faster. Both are right. **Which one you
care about depends entirely on your workload**, and for long-document
summarisation it is unambiguously prefill.

### Adoption caveats

- **It is a fork of an older llama.cpp base.** Its CLI differs — `-no-cnv` does
  not exist, for one — so any script must be adapted, not just re-pointed.
- **Do not mix builds across the fleet.** The RPC protocol will not match
  mainline. All-ik or all-mainline.
- **This concern largely evaporates under the replicated topology**
  (`DESIGN-NOTES.md` C), where nodes are independent and no RPC handshake
  happens at all — another argument for replication-first.
- It reports the model as `gpt-oss ?B` rather than `120B`, i.e. it does not
  fully parse this architecture's metadata. Output is correct regardless, but
  **re-verify coherence per model** before adopting it for a new one.

### Recommendation

**Adopt ik_llama.cpp for the document-summarisation workload**, keeping mainline
built alongside for comparison and for anything RPC-sharded. The two prefixes
(`/opt/llama.cpp`, `/opt/ik_llama.cpp`) coexist cleanly, so this costs nothing
to keep reversible.

---

## F9. Operational notes for the bring-up scripts

**CONFIRMED by direct observation on node 1.**

- **`dmidecode` is not in the Debian 12 base install.** `bootstrap.sh` installs
  it, but the plan's Task 1 Step 2 calls it before that is guaranteed. The
  preseed now includes it directly so hardware facts are gatherable on first
  boot.
- **`list-devices disk` sees the card reader.** Node 1 has a 0 B "Multi-Card"
  reader at `/dev/sda` with the real NVMe at `/dev/nvme0n1`. The plan's preseed
  took `list-devices disk | head -n1`, which would have picked the reader and
  failed the install. `preseed.cfg` now filters to devices over 8 GB.
- **Swap is enabled by default** (975 MiB on node 1) — as expected;
  `setup.sh` disables and masks it.
- **THP is `madvise`** on Debian 12 as the spec predicted. Assert, do not tune.
- **Sudoers ordering gotcha.** Dropping `NOPASSWD` into `/etc/sudoers.d/` was
  silently overridden by a later `(ALL : ALL) ALL` match — sudo is
  **last-match-wins**, and `sudo -l` showed *both* rules while the password was
  still demanded. Anything scripting unattended sudo across the fleet must
  append after the `@includedir` line, not rely on a `sudoers.d` drop-in.

---

## F28. The fleet network is 100 Mb/s, not gigabit — and that inverts a decision in F23

**CONFIRMED by measurement, 2026-08-17.** `STATUS.md`, `network.md` and F23 all
described the fleet as gigabit. Measured: **93.8 Mbit/s = 11.7 MB/s**.

Both nodes' NICs are Intel I218-LM and both **support and advertise
1000baseT/Full**, so the cap is the cable or switch port, not the hardware —
confirmed independently by the operator checking the switch. The link achieves
~94% of 100 Mb line rate, so it is healthy; it is simply a 100 Mb link.

**What it changes, in order of consequence:**

1. **F23's peer-pull preference is inverted.** F23 justified
   `cluster/models.sh pull` preferring a peer over HuggingFace with "21 MB/s from
   HuggingFace vs ~110 MB/s on gigabit LAN". At **11.7 MB/s the LAN is ~1.8×
   slower than the internet download.** On this network, pulling from a peer is
   the *wrong* default. Note the `~110` was never measured — it was inferred
   from "gigabit", which is precisely the kind of unmeasured number this project
   keeps getting caught by.
2. **RPC sharding costs far more than F14's floor.** F14's −5.2% generation
   penalty was localhost with the network removed. Across two real machines
   generation was roughly **half** the single-node rate (indicative; see
   `measurements.md`). "Sharding buys capacity, never speed" is now
   "…and on a 100 Mb link it costs about half your generation."
3. **Latency-bound designs are not viable here.** RTT is **0.827 ms idle but
   9.544 ms while a single rsync saturates the link** — an 11.5× bufferbloat
   collapse. This is what qualifies the expert-parallelism analysis in
   `DESIGN-NOTES.md` A, whose "communication would not kill it" conclusion
   assumed gigabit and does **not** survive on this link.
4. **Replication is completely unaffected** — independent `llama-server`s share
   no hot path. **Another argument for replication-first.**
5. **Model distribution is slow but one-time:** 65 GB = ~97 min measured.

**Cheapest fix: a ~$20–30 gigabit switch** uplinked to the existing 100 Mb port.
Node↔node traffic is then switched locally at gigabit; each node keeps internet
on one cable. **Preferred over daisy-chaining two nodes**, which needs N−1 ports
per node and does not scale to nodes 3–7.

**For the skill:** add link speed to the assessment's *measured* list, alongside
physical cores, STREAM bandwidth and coordinator disk. `ethtool` reporting
`1000baseT/Full` under *Supported* proves only what the NIC can do — the
negotiated `Speed:` line and an `iperf3` run are the facts. A cluster whose
switch silently caps at 100 Mb looks fine in an inventory.

---

## F29. Node 2 is a bandwidth twin — homogeneity is a MEASURED result, and it validates F10 independently

**CONFIRMED by measurement.** Node 2 was characterised before anything was
assumed, per the F12 warning that core count is a bandwidth spec.

| | node 1 | node 2 |
|---|---:|---:|
| STREAM peak (4 threads) | 28.4 GB/s | **27.9 GB/s** |
| Peak thread count | 4 | **4** |
| 8-thread penalty | yes | **yes** |
| DIMM layout | 4 × 32 GB @ 2400 | **4 × 32 GB @ 2400** |

Node 1 was re-run the same day (28.2 → 28.4) so the comparison is not against a
five-day-old figure; the **1.8%** gap is within that noise.

**Three things this buys:**

1. **`--tensor-split` by RAM is also correct by bandwidth** on these two nodes.
   F12 warned it might not be. Re-check per node; do not assume it for 3–7.
2. **F10 reproduces on independent hardware.** The 4-thread peak and the SMT
   penalty are not an artefact of one machine, which is what licenses
   generalising "threads = physical cores" into the skill.
3. **`nodes.env` had node 1's RAM as 125629 MB, which `free -m` does not
   report** (both nodes: **128709**). Origin unknown; corrected. It mattered
   because RAM_MB sets `--tensor-split` ratios, so a 2.4% error would misweight
   the split between physically identical nodes.

---

## F30. Five latent bugs sat in the bring-up path, and ALL of them fire only at N=2

**CONFIRMED by hitting every one of them, 2026-08-17.** `CLAUDE.md` predicts
that everything which can go wrong appears at the 1 → 2 transition. That was
correct, and this is the concrete list. Each would have been found only after
committing hardware.

1. **`User=cluster` in `rpc-server@.service` referenced a user nothing created.**
   Not `setup.sh`, not the preseed, nowhere in the repo. `install-services.sh`
   does `enable --now`, so the unit would have died instantly with
   **status=217/USER** — on every worker. **Fixed:** `setup.sh` now creates a
   `cluster` system account (nologin, home `/var/lib/cluster`) and pre-creates
   the `-c` tensor-cache directory, reporting free space on that filesystem
   (F23's silent-fill trap).
2. **The coordinator could not SSH to itself.** `install-services.sh` iterates
   *all* nodes including the master (it runs a worker too), unlike
   `distribute.sh` which skips it. Node 1 had **no `authorized_keys` at all**, so
   the run aborted at the first `scp` with `Connection closed`. **Fixed:**
   authorised the coordinator's own key to itself.
3. **`setup.sh` regenerates SSH host keys, invalidating the coordinator's
   `known_hosts`.** Every later script uses plain `ssh`, so the next one aborts
   with `REMOTE HOST IDENTIFICATION HAS CHANGED` — which reads as an attack, not
   as a provisioning step. **Fixed:** `setup.sh` now prints the exact
   `ssh-keygen -R` remedy. Note the ordering trap: `harden-ssh.sh` accepted the
   host key *before* `setup.sh` replaced it.
4. **Regenerating `machine-id` orphaned the journal.** journald keys its
   persistent directory by machine-id, so `/var/log/journal/<old-id>/` was
   stranded and **`journalctl -u rpc-server@50052` returned "No journal files
   were found"** — no logs at all on the new worker, exactly when a smoke test
   might need them. Fixed by restarting `systemd-journald`.
5. **Node 2 arrived on the wrong timezone.** `US/Eastern` against node 1's
   `Australia/Melbourne`. The **clocks agreed** (both NTP-synced, identical UTC
   epoch) but `journalctl` on the two nodes read **14 hours apart**, which makes
   cross-node correlation actively misleading during a failure. **Fixed:**
   `setup.sh` now aligns to `CLUSTER_TZ` (default `Australia/Melbourne`).

**Also found and fixed: `setup.sh`'s DIMM reporter printed an empty label.**
`dmidecode` prints `Size:` *before* `Locator:` within each Memory Device block,
so stashing the locator and printing on size reported the *previous* block's
label; and `Bank Locator:` also matches `/Locator:/`, so filtering `Bank`
afterwards discarded all but one row. Net effect: a single line reading
`-> 32GB` with **no slot label** — the one fact F12 says determines generation
speed, silently blank. Now prints all four slots with size and clocked speed.

**None of these are exotic.** They are the ordinary consequences of a script set
that had only ever run against one machine, which is exactly why the project
insists on testing at N=2 before N=7.

---

## F31. #26500 also clears across REAL machines — but the F21 empty-content bug made a passing cluster look broken

**CONFIRMED by measurement.** Two distinct results.

**1. The gate passes.** F22 cleared #26500 with two `rpc-server` processes on one
box. It now clears with **two separate machines** — real TCP, distinct
`machine-id`s, distinct host keys, a real NIC. Zero
`[create_node] invalid data ptr`, zero aborts, coherent on-topic output.
Still **model-dependent** (this was a dense 4B; the public reproductions are MoE
graphs with unusual constant/view nodes), so re-run with the real Model B GGUF.

**2. `bench/two-node-smoke.sh` reported FAIL while the cluster was working
perfectly.** The script requested `max_tokens: 150` from Qwen3-4B and read
`choices[0].message.content`. The budget was exhausted **mid-reasoning**, so
`content` came back an **empty string** with `reasoning_content` populated — and
the script printed `FAIL: no usable response`. The server log showed 150 tokens
generated at 5.73 t/s with no errors at all.

**This is F21 recurring in a different file.** F21 found and fixed it in Missing
Link's worker; the same latent bug was sitting in the bench script, where its
consequence is worse in kind: **it does not lose output, it produces a false
negative on the project's own go/no-go gate.** A team following the plan would
have concluded multi-worker RPC was broken and started cherry-picking an
upstream patch to fix a bug they did not have.

**Fixed** by sending `chat_template_kwargs: {"enable_thinking": false}`, raising
`max_tokens` to 400, and — most importantly — making the failure path
**distinguish a cluster fault from a parsing fault**: it now greps the server log
for graph-compute aborts and, finding none, says so explicitly rather than
implying #26500.

**Generalisation worth carrying into the skill:** any health check that reads a
single JSON field from a reasoning model can fail open or fail closed for reasons
that have nothing to do with what it is testing. **A gate must report *which*
thing failed**, and empty output must never be conflated with a broken backend.

---

## F32. ik_llama.cpp was on the coordinator only, and `distribute.sh` could never have shipped it

**CONFIRMED by direct observation.** F27 adopted `ik_llama.cpp` for the document
workload (+52% prefill, net +22% end-to-end). But `distribute.sh` hardcoded
`SRC=/opt/llama.cpp`, so **the fork existed on node 1 and nowhere else**, with no
code path capable of distributing it.

Under the **replicated** topology this is not cosmetic: every node runs its own
independent `llama-server`, so every node needs the engine the workload is
actually supposed to use. A "fully distributed" fleet would have been quietly
running mainline — i.e. **22% slower than the measured best** — on every node but
the coordinator.

**Fixed:** `distribute.sh` now takes the engine prefix as an argument
(`./provisioning/distribute.sh /opt/ik_llama.cpp`) and defaults to mainline. Two
wrinkles handled:

- ik_llama.cpp is a fork with **no upstream b-tag**, so it ships a `COMMIT` but
  no `VERSION`, which `set -euo pipefail` turned into a hard failure. A version
  string is now synthesised from the prefix and commit
  (`ik_llama.cpp-8337e4cd`); what matters is that master and worker *agree*, not
  that the string looks like a release tag.
- Both engines are now installed on both nodes and verified executable there.
  **Never mix engines within one RPC shard group** — the protocols differ. Under
  replication the nodes are independent, so holding both is safe and is exactly
  the side-by-side A/B the north star asks for.

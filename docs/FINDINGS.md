# Findings

Things learned by actually running this on hardware, that were not known — or
were known *wrongly* — when the plan was written. Intended to be folded back
into `CLAUDE.md`, `STATUS.md` and the spec.

Each entry is labelled **CONFIRMED** (verified against primary source or
observed directly), **REPORTED** (someone else states it), or **INFERRED**.

---

## Index

Every finding below, in **the order they appear in this file** — which is not
numeric order: **F9 sits between F27 and F28**, where it was written, and an
**Addendum to F40** sits between F42 and F43. Left as-is deliberately; the
numbers are cited from other documents and must not move.

| # | Finding | Label |
|---|---|---|
| **F1** | The "75% per-node RAM ceiling" is folklore — the citation is wrong and the issue is closed | CONFIRMED |
| **F2** | An open, unmerged upstream bug specifically hits multi-worker RPC clusters | CONFIRMED / INFERRED |
| **F3** | Model load is single-core serialized — a ~550 GB load will be slow | REPORTED |
| **F4** | `--parallel > 1` asserts against the RPC backend (prompt cache) | REPORTED |
| **F5** | Async/pipelined RPC is not coming soon | CONFIRMED |
| **F6** | Pin recommendation: b10369 | CONFIRMED |
| **F7** | Node 1 is a 4-core Broadwell with no AVX-512 — prefill is the risk | CONFIRMED |
| **F8** | `GGML_NATIVE=ON` is a trap on a salvaged fleet | INFERRED |
| **F10** | `-t $(nproc)` is the wrong value, and the plan bakes it in fleet-wide | CONFIRMED |
| **F11** | Generation already runs at ~99% of memory bandwidth — software tuning is exhausted | CONFIRMED |
| **F12** | Bandwidth is core-limited, not channel-limited — the CPU cannot saturate its own memory | CONFIRMED |
| **F13** | `rpc-server` was renamed, and the binaries were not relocatable | CONFIRMED |
| **F14** | RPC costs 5% on generation but 39% on prefill | CONFIRMED |
| **F15** | `llama-cli` is conversation-first with a TUI — `-no-cnv` is not enough for scripting | CONFIRMED |
| **F16** | Model B does not fit on the coordinator's DISK — disk, not RAM, is the binding constraint | CONFIRMED |
| **F17** | The plan's TTFT measurement is wrong and reports ~0.015 s; real TTFT was 89 s | CONFIRMED |
| **F18** | Raising `-ub` does not help CPU prefill — the spec's expectation was wrong | CONFIRMED |
| **F19** | The 90 s TTFT threshold is the wrong gate for this project's actual workload | analysis from measured numbers |
| **F20** | The plan's job-claim logic races and runs jobs twice | CONFIRMED |
| **F21** | Reasoning models return EMPTY content when max_tokens runs out mid-thought | CONFIRMED |
| **F22** | Two-worker RPC sharding WORKS on b10369 — bug #26500 does not fire here | CONFIRMED |
| **F23** | Workers never read model files — only the coordinator needs the GGUF | CONFIRMED |
| **F24** | Sparse MoE reaches only 61% of memory bandwidth — every MoE estimate was ~1.6x optimistic | CONFIRMED |
| **F25** | Kimi K2 has the worst hallucination rate of any model checked — Model B needs reconsidering | REPORTED |
| **F26** | Kimi K3 exists, and is firmly out of scope | CONFIRMED |
| **F27** | ik_llama.cpp is 52% faster at prefill and 14% slower at generation — net +22% (later unsettled by F40) | CONFIRMED |
| **F9** | Operational notes for the bring-up scripts *(out of numeric order — it sits here, after F27)* | CONFIRMED |
| **F28** | The fleet network is 100 Mb/s, not gigabit — and that inverts a decision in F23 | CONFIRMED |
| **F29** | Node 2 is a bandwidth twin — homogeneity is a MEASURED result, and it validates F10 independently | CONFIRMED |
| **F30** | Five latent bugs sat in the bring-up path, and ALL of them fire only at N=2 | CONFIRMED |
| **F31** | #26500 also clears across REAL machines — but the F21 empty-content bug made a passing cluster look broken | CONFIRMED |
| **F32** | ik_llama.cpp was on the coordinator only, and `distribute.sh` could never have shipped it | CONFIRMED |
| **F33** | ik_llama.cpp's `-sm graph` gives no cross-machine parallelism on CPU — the gate is a missing op | CONFIRMED / REPORTED |
| **F34** | Missing Link had never been run — "41 tests passing" hid a silent truncation bug | CONFIRMED |
| **F35** | There is no universal thinking-off switch; `enable_thinking` is INERT on gpt-oss and unknown kwargs are dropped silently | CONFIRMED |
| **F36** | llama-server can hang ALIVE, and `Restart=always` cannot see it | CONFIRMED |
| **F37** | How this project's concepts changed on 2026-08-17 — read before trusting older docs | record of conceptual drift |
| **F38** | Uploaded PDFs were decoded as UTF-8 and summarised as binary — the first real input broke it | CONFIRMED |
| **F39** | The F36 watchdog killed a healthy job in 79 minutes — `/health` shares the queue it is meant to probe | CONFIRMED |
| **F40** | ik_llama.cpp fatal-errors on the 5th request of any multi-slot job and hangs the server — F36's real cause, and it unsettles F27 | CONFIRMED |
| **F41** | A faithfulness classifier's reliability DEGRADES WITH EVIDENCE LENGTH; cross-model agreement stops being a safety net | CONFIRMED |
| **F42** | The reduce step launders a fabrication into the final summary — caught by string comparison, not by a model | CONFIRMED |
| **Addendum to F40** | Mainline shares the exact fork/waitpid abort path, so dropping ik_llama.cpp does NOT retire the forked-abort hang hazard | CONFIRMED |
| **F43** | The fleet-wide watchdog was silently non-functional on node 2 from install | CONFIRMED |
| **F44** | Even niced, a CPU-bound sidecar measurably starves `llama-server` on a 4-core node | CONFIRMED |
| **F45** | The sentence splitter's line-based fallback distorts clause-marker density — and it now gates corpus decisions | CONFIRMED |
| **F46** | A citation can be CORRECT and its sentence still false; and a stricter regex would have destroyed all 7 valid citations | CONFIRMED |
| **F47** | GLM-5 is 40.8 B active (computed, not published) — and Model B closes in the NEGATIVE | CONFIRMED |
| **F48** | pysbd is worse than our regex; the legal-domain splitter nobody searched for fixes F45 | CONFIRMED |
| **F49** | Whole-document single-pass is impossible at `n_ctx_slot=8192`; `WORDS_PER_TOKEN` is wrong by 2x and `/tokenize` is free | CONFIRMED |
| **Addendum to F48** | Swap implemented; nupunkt INVERTS the raw-HTML manifestation, so HTML extraction stays load-bearing | CONFIRMED |
| **F50** | The −47% and F14's −5% are both right; RPC's generation penalty scales with `n_vocab`, not model size | CONFIRMED |
| **F51** | The watchdog makes large-model sharding impossible — a busy `rpc-server` refuses connections and gets restarted | CONFIRMED |
| **F52** | Corpus re-profiled on the new splitter — and the SQL safety gate did not fire on the write that did it | CONFIRMED |
| **F53** | Node 3 is a three-way bandwidth twin — but its "1 TB" is a spinning HDD and it shipped as a GNOME desktop | CONFIRMED |
| **Addendum to F51** | Fixed with a BYTES progress signal — CPU alone reads 0% during a real shard upload | CONFIRMED |
| **F54** | ComfyUI is compute-bound the wrong way; n8n fits; and ComfyUI, n8n AND Missing Link all have no auth | CONFIRMED / REPORTED |
| **F55** | The agent-hardening hooks have NEVER been enforced — and it corrects F52's diagnosis | CONFIRMED |
| **F56** | The 2 → 3 transition found three MORE latent bugs, none about the username — and it corrects F53 | CONFIRMED |
| **F57** | Distributing ComfyUI cannot help; nginx's 60 s default would kill every real request; corrects F54 on n8n | CONFIRMED / REPORTED |
| **F58** | A token-budget formula decides the syllabus; a small model buys only ~2x; `--api-key` takes a LIST | CONFIRMED |
| **F59** | Node 3 vanished at layer 2 (likely GNOME idle suspend) — and tracked `nodes.env` publishes the IPs the docs hide | CONFIRMED |
| **F60** | No lab targets on this LAN — the cluster sits on a production cyber-range MANAGEMENT segment | CONFIRMED |
| **Addendum to F59** | Suspend cause CONFIRMED (1200 s = the GNOME idle timeout); the fix had been hand-applied twice and never propagated | CONFIRMED |
| **F61** | n8n's `Execute Command` is disabled by default; the LLM is measurably useless at cryptanalysis | CONFIRMED / REPORTED |

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

---

## F33. ik_llama.cpp's `-sm graph` does NOT give cross-machine parallelism on CPU. The gate is a missing op, not a CUDA `#ifdef`.

**CONFIRMED in local source, and by the upstream maintainer.** This closes a
genuinely promising lead, and it is worth recording *why* it looked promising —
the failure mode is instructive.

`ik_llama.cpp`'s `llama-server --help` advertises a `-sm graph` split mode
described as *"split model tensors and computation graph across GPUs"*, plus
`-smgs` (graph scheduling), `-sas --scheduler-async` (*"async evaluation of
compute graphs"*), `-ger --grouped-expert-routing`, `-ser`, `-muge`. Mainline
offers `-sm {none,layer,row,tensor}`. On the face of it, one of these might give
**intra-layer** parallelism — two nodes working the *same* layer simultaneously
— which is exactly the 1/S utilisation waste identified in `DESIGN-NOTES.md` A,
and would deliver it **from a flag** rather than from building an engine.

**It does not, and here is the proof.**

| Mechanism | Requires | Registered by | CPU / RPC? |
|---|---|---|---|
| ik `-sm graph` / `-sm attn` | `GGML_OP_REDUCE` compute | **CUDA only** | **NO** |
| mainline `-sm row` | `ggml_backend_split_buffer_type` | CUDA / SYCL only | **NO** |
| mainline `-sm tensor` | `ggml_backend_comm_init`, `..._allreduce_tensor` | CUDA / SYCL only | **NO** |

Verified directly on the trees on disk:

```
# ik_llama.cpp @ 8337e4cd -- GGML_OP_REDUCE
ggml/src/ggml-cuda.cu:3655   case GGML_OP_REDUCE:      <- the only compute path
ggml/src/ggml-cuda.cu:4995   case GGML_OP_REDUCE:      <- supports_op
grep GGML_OP_REDUCE ggml/src/ggml-cpu.c ggml/src/ggml-cpu/   -> ZERO matches
grep GGML_OP_REDUCE ggml/src/ggml-rpc.cpp                    -> ZERO matches

# mainline b10369
src/llama-model.cpp:989  throw ... "device %s does not support split buffers"
grep -rl ggml_backend_comm ggml/src/ggml-rpc.cpp ggml/src/ggml-cpu*  -> ZERO matches
```

**REPORTED, and decisive:** maintainer `ikawrakow`, in GitHub Discussion
[#1247](https://github.com/ikawrakow/ik_llama.cpp/discussions/1247) ("New tensor
parallel in llama.cpp", 2026-02-23): *"RPC-connected devices currently cannot be
used for graph parallel."*

### Why it looked promising, which is the transferable lesson

**The dispatch code IS backend-generic and RPC devices ARE enumerated.** Device
enumeration, `model.devices`, and the per-expert tensor-splitting code carry no
CUDA `#ifdef`, so reading the CLI parser or the device-setup path suggests the
feature is backend-agnostic. **The gate is one level lower: an unimplemented
op.** `GGML_OP_REDUCE` is declared generically in `ggml.c` and has exactly one
compute implementation, in CUDA.

So this would have failed **at graph-compute time, not at argument-parse time** —
after loading a 65 GB model across two machines. There is no friendly "unsupported
on this backend" error to find, which is why `--help` and the parser both look
encouraging. **An absent op fails identically to a hard guard, but is far harder
to find by reading.**

Two other flags clarified while here:

- **`-ger --grouped-expert-routing` is NOT expert parallelism.** It is a
  routing/gating change scoped to the **BailingMoeV2 architecture only**
  (`common/common.h:428`). Not a device-distribution mechanism.
- **`-sas --scheduler-async` is NOT the async RPC of PR #18626.** It is OMP
  thread-barrier concurrency across multiple **CUDA** devices inside one process,
  wired only into split-mode-graph. It does not overlap RPC round-trips, so F5's
  "assume sequential RPC execution" stands.

**Verdict: `DESIGN-NOTES.md` A's conclusion is unchanged — expert/tensor
parallelism across machines does not exist in either engine today.** But the
reason is now *proven* rather than assumed, and it is a sharper reason: not "no
one has written the feature" but "the cross-device combine primitive has no CPU
kernel." **If `GGML_OP_REDUCE` ever gains a CPU implementation, re-open this
immediately** — the surrounding machinery is already generic, which makes that a
much smaller change than writing a new inference engine.

**Caveat:** checked against the tree on disk (`8337e4cd`) and a 2026-02-23
maintainer comment. A newer ik_llama.cpp commit could have added a CPU path;
re-grep `GGML_OP_REDUCE` before relying on this.

---

## F34. Missing Link had never been run. "41 tests passing" hid a silent truncation bug.

**CONFIRMED by running it, 2026-08-17.** Prompted by the question "have you ever
even tried Missing Link with a small model?" The answer was **no** — and the
evidence was sitting in plain sight:

- Every worker test constructs a `FakeClient`. **`LlamaClient` — the class that
  actually speaks HTTP to llama-server — had never executed.**
- There was **no jobs database anywhere on the box.** The pipeline had never
  processed a document, not once.
- `STATUS.md` said "41 tests passing", which was true and misleading in the same
  breath.

### It does work

A 2057-char health-service records memo went in and a faithful summary came out
through the real path (`create_job` → `claim_next_pending` → `LlamaClient` → HTTP
→ `extract_content` → `complete_job`). Every claim checked against the source:
the 41 non-clinical staff, the 7/3/15-year retention tiers, the March 2026
remediation, the incomplete pre-January-2026 reconstruction. **No fabrication.**
It also correctly *omitted* detail rather than inventing any.

### But the first run found a defect mocked tests structurally cannot find

`max_tokens` defaulted to **512**. The server reported `eval time = 512 tokens` —
**exactly the ceiling** — and the stored summary ended mid-sentence on
*"Recommendations include implementing automated archival for clinical"*. The job
was recorded **`status='done'`**.

`extract_content` *did* inspect `finish_reason`. But only to improve the error
message when `content` was **empty**. Non-empty-but-truncated returned as a
success.

**This is the F21 family, and worse in one specific way.** An empty summary is
obviously broken. A truncated one **looks finished** until you read the last
sentence. And under map-reduce a truncated *chunk* summary becomes source
material for the reduce step, where the missing content is indistinguishable from
content the document never contained — the same laundering mechanism F25 and
`DESIGN-NOTES.md` E worry about, arriving through a different door.

**Fixed:** `TruncatedCompletion` on `finish_reason='length'` with non-empty text,
naming the budget and quoting the final characters. Inline `<think>` stripping
with a hard failure if nothing but thought came back (some servers emit reasoning
in `content` rather than `reasoning_content`, which slips past the F21 guard while
carrying no answer). `enable_thinking: false` sent on every request. Budgets split
**map 1024 / reduce 2048**, because the reduce output legitimately exceeds any
single map output and one shared value is how a long document ends truncated even
though every chunk succeeded.

**Measured effect of disabling thinking:** the same document went **70.6 s →
32.2 s**, and generation stopped naturally at **175 tokens** instead of jamming
into the 512 ceiling. Over half the tokens had been chain-of-thought that was
discarded. On this hardware every token costs ~110 ms, so that is not a rounding
error.

### The generalisable lessons

1. **A mocked test suite cannot discover what the protocol actually does.** Every
   defect in this file's Missing Link entries — F20's race, F21's empty content,
   F34's truncation — lived in the seam between our code and something real
   (SQLite's locking, the model's output shape, the server's `finish_reason`).
   The tests were not bad; they were testing the half we wrote.
2. **"N tests passing" is not a claim about working software** and should not be
   reported as one. `STATUS.md` now says what was and was not exercised.
3. **Any completion guard must check finish_reason even when content is
   non-empty.** Empty output is the *obvious* failure; truncated output is the
   dangerous one.
4. **Run the thing once, end to end, before believing anything about it.** This
   is the same lesson as the rest of this file, applied to our own code rather
   than to upstream's.

### Also fixed while here

- **`ttft_s` was never populated**, though the schema reserved the column and
  `complete_job` read it. llama-server returns a `timings` object on its
  OpenAI-compatible endpoint (`prompt_n`, `prompt_ms`, `predicted_ms` — verified
  in `tools/server/server-task.cpp`), so the authoritative TTFT was already in the
  response we parse. Now recorded, and **`None` rather than `0.0`** when
  unavailable — a missing measurement must look missing, which is precisely how
  F17's bug survived.
- **Dependencies were 18 months stale** (fastapi 0.115.6, starlette 0.41.3,
  jinja2 3.1.5, pytest 8.3.4 — Dec 2024/Jan 2025), with 9 Dependabot advisories
  (3 high) on the default branch. **They came from the original plan**, written
  against whatever versions its author knew about, and were never reviewed — the
  same inherited-unmeasured-number failure mode as the 75% RAM rule (F1) and the
  "~110 MB/s gigabit LAN" figure (F28). Bumped to current; **50 tests pass**,
  including a major starlette 0.41 → 1.6 jump.

---

## F35. There is no universal thinking-off switch. `enable_thinking` is INERT on gpt-oss, and llama-server drops unknown kwargs silently.

**CONFIRMED by controlled measurement on node 1, and independently by mechanism.**
This is what cost the replication benchmark a request.

Same model (gpt-oss-120b), same prompt, same `--jinja` server:

| Variant | max_tokens | Completion tokens | Result |
|---|---:|---:|---|
| no kwargs | 300 | 89 | OK |
| `{"enable_thinking": false}` | 300 | **129** | OK — but MORE than baseline, i.e. **did nothing** |
| `{"reasoning_effort": "low"}` | 300 | **61** | OK — **31% fewer than baseline** |
| `{"reasoning_effort": "low"}` | **80** | **49** | **OK, clean stop** |
| **no kwargs (control)** | **80** | **80** | ***EMPTY CONTENT — F21 failure*** |

**The last two rows are the proof.** Identical budget, identical prompt; the only
difference is the kwarg, and it converts a guaranteed F21 failure into a clean
success.

**Mechanism, confirmed two independent ways:**

1. **From the template on our own disk.** `GET /props` returns the chat template
   embedded in the GGUF. It mentions `reasoning_effort` **4 times** and
   `enable_thinking` **zero times**. (It also opens with
   `{# Chat template fixes by Unsloth #}` — Unsloth's template work is already in
   our stack, unremarked.)
2. **`chat_template_kwargs` is a generic pass-through into the Jinja template, and
   llama-server SILENTLY DROPS keys the template does not reference — no error, no
   warning.** So sending the wrong family's knob is indistinguishable from sending
   the right one. llama.cpp's own server README uses `{"enable_thinking": false}`
   as its generic example, which is exactly how the wrong knob spreads.

**Consequences:**

- **`enable_thinking` is a Qwen/ChatML-family variable, not a standard.** gpt-oss
  (harmony) uses `reasoning_effort` with values low/medium/high — **there is no
  "off"**.
- **A silently-ignored flag is worse than no flag**, because it produces false
  confidence while the token budget drains into reasoning. `worker.py` now maps
  model family → kwargs and returns **`{}` for an unknown model** rather than
  guessing, and detects the model from `/props` rather than assuming.
- **REPORTED, untested here:** a harmony-native alternative is putting
  `Reasoning: low` in the system prompt, which works regardless of whether the
  kwargs plumbing round-trips. Also `--reasoning-format` (none/deepseek) controls
  whether analysis text lands in `content` or `reasoning_content`, and Qwen3 may
  need `--reasoning-budget 0` alongside `enable_thinking:false` on recent builds —
  one report of an eval regression from that, so **coherence-check before
  adopting.**
- **Verify the knob per model family, by measurement.** F27 said re-verify
  coherence per model; this extends it to control flags. The test is cheap: one
  tight-budget request with and without the kwarg.

---

## F36. llama-server can hang ALIVE, and `Restart=always` cannot see it. A client disconnecting mid-generation is enough.

**CONFIRMED by direct observation, 2026-08-17.** Found while checking whether a
PDF job had completed.

**Symptoms:** the job sat in `running` for seven minutes with the model server at
**load 0.11** — idle. No error in any log. `GET /slots` returned **HTTP 000** (no
response). A trivial new completion request **timed out after 85 s with HTTP 000**.
The process was alive, `systemctl is-active` said `active`, and it was accepting
TCP connections — it simply never answered. `systemctl restart llama-server@8080`
fixed it instantly (healthy in ~10 s, then HTTP 200 in 5.7 s), which proves it was
**wedged rather than slow**.

**Cause:** a `systemctl restart missing-link` while a job was mid-generation. The
server logged `srv stop: cancel task, id_task = 220` and a `CLOSE-WAIT` socket was
left behind. So **the trigger is an ordinary operational action** — a deploy, a
config reload, a worker OOM — not an exotic fault.

**Why this is worse than a crash, and it compounds three ways:**

1. **`Restart=always` is useless here.** The process never exited. systemd cannot
   distinguish "serving" from "hung" without an explicit liveness probe.
2. **The worker would have waited an hour.** `DEFAULT_TIMEOUT_S = 3600`, correctly
   set high because real jobs take minutes — but against a dead backend that is
   sixty minutes of a job frozen in `running`, which is exactly the invisible work
   F20's `requeue_running()` exists to prevent.
3. **`_worker_loop` had NO exception handling at all.** Any exception escaping
   `run_one` would kill the asyncio task **silently** — an unretrieved task
   exception prints nothing — stopping the queue forever with no log line. That
   was a separate latent bug found in the same investigation.

**Fixes, all three layers:**

- **`_worker_loop` guards every iteration**, logs the failure, backs off
  progressively, and **never exits**. A queue that can die silently is not a queue.
- **`LlamaClient.assert_reachable()`** probes `/health` with a 20 s timeout before
  the worker commits to a job, raising `BackendUnavailable` with the remedy in the
  message. A wedged backend now produces a **failed job in 20 s** instead of a
  frozen one for an hour.
- **`cluster/llama-watchdog.sh` + a systemd timer** probe `/health` every minute
  and restart the unit after **two** consecutive failures. Two, not one, because
  restarting a healthy server costs a multi-minute 65 GB reload (F3). It no-ops
  when the unit is already inactive, so it never fights `Restart=always`.

**This is the "agent appliance" argument in miniature, and it validates the
constraint `CLAUDE.md` states:** liveness cannot be judged from inside the thing
being judged. The watchdog is a separate process precisely because the failure
mode was "the server is up and lying." Note the operator has relaxed the appliance
to on-node hosting, which is fine for *triage* — but this finding is the concrete
reason the *liveness* half still wants an outside observer.

**Generalisation for the skill:** any health check that only asks "is the process
running?" will report green through this failure. **Probe the API, with a timeout,
from outside the process.**

---

## F37. How this project's concepts changed on 2026-08-17. Read this before trusting older docs.

**Not a bug — a record of conceptual drift.** Several things the repo asserts
confidently were reframed in one session. Anything written before this date should
be read with these in mind.

### 1. "Sharding vs replication" became "S decides, and S is a model-selection input"

The repo already had `S` (nodes per copy) and `R` (copies). What changed is that
**S is now understood as a property of the MODEL CHOICE, not of the cluster** — and
crossing S = 1 → 2 costs a factor of R. At N = 2:

| Model | S | R | Result |
|---|---:|---:|---|
| gpt-oss-120b (65 GB) | 1 | 2 | ~1.8× measured |
| GLM-4.6 (189 GB) | 2 | 1 | no replication, plus RPC over 100 Mb |
| DeepSeek-V3.2 (363 GB) | 4 | 0 | does not run |

**So a faithfulness-led model choice has a throughput price tag that only appears
when you divide by node count.** F25 compared candidates on faithfulness, disk and
active params and never crossed any of it against N. The Qwen3-Next-80B download
in flight is `UD-Q8_K_XL` at **87 GB — S = 1 with ~12 GB spare**, which is why it
is a good choice and a larger quant would not be.

### 2. Fan-out is two things, not one

Job-level (whole documents) buys **throughput**; chunk-level (one document's
chunks) buys **latency only**, because it is the same work spread wider. Queue
depth selects the mode. `DESIGN-NOTES.md` G.

### 3. "Popular self-hosted tool" is a repeated trap, now twice identified

- RAG-QA tools were nearly adopted for a **summarisation** problem (`DESIGN-NOTES`
  E).
- Open WebUI sits in `CLAUDE.md` as *settled* and is a **chat** frontend for an
  explicitly **asynchronous batch** workload (`DESIGN-NOTES` F).

**Both are the same error: adopting the shape everyone else built, for a different
job.** Treat "X is the standard choice" as a prompt to check whether X's users have
our problem.

### 4. Provenance moved from nicety to prerequisite

Three independent lines demanded the same change — the design argument (E3), the
metrics literature (`EVALUATION.md`: metrics are unreliable at whole-document
scope, better against a correctly-scoped evidence window, especially for legal
text), and the UI question of why a reader should trust output (F). **When three
unrelated paths demand one change, that is the strongest available signal.** Built
the same day; it also turns out to be the substrate for **resumability**.

### 5. Evaluation stopped trying to reproduce the leaderboard

Ranking models locally needs ~260 documents per model to separate 9.5% from 17.9%
hallucination. **Use the published leaderboard for ranking**; spend cluster nights
on the question it cannot answer — whether **map-reduce amplifies fabrication** —
which is *paired*, so a few dozen documents suffice. `EVALUATION.md`.

### 6. Setup is no longer "provision N identical nodes"

What actually happened to node 2, and what the setup process must therefore include:

- **Characterise before assuming.** Node 2 turned out to be a twin — but that was a
  *measured result* (F29), and the file that claimed node 1's RAM was wrong.
- **The 1 → 2 transition is where the bugs are, and there were five** (F30), none
  exotic: a service account nothing created, the coordinator unable to SSH to
  itself, host-key regeneration breaking `known_hosts`, `machine-id` regeneration
  orphaning the journal, a 14-hour timezone divergence.
- **Measure the network, do not read it off a switch label.** It was 100 Mb, not
  gigabit (F28), which inverted a decision.
- **Services, not processes.** Everything must survive a reboot, because the
  operator is an hour away behind three locked doors.
- **Something outside the cluster must judge liveness** (F36 + `REQUIREMENTS.md`).

**So the setup deliverable is not an image or a script run — it is: characterise,
measure, provision idempotently, distribute, gate at N=2, then verify by output.**

### 7. "Tests passing" stopped being evidence of working software

41 tests passed against a pipeline that had **never processed a document** (F34).
Every defect since — the claim race, empty completions, truncation, PDF mojibake,
the wedged backend — lived in the seam between our code and something real. **Report
what was exercised, not how many assertions ran.**

---

## F38. Uploaded PDFs were decoded as UTF-8 and summarised as binary. The first real input broke it.

**CONFIRMED by the operator uploading a PDF, 2026-08-17.** Recorded with an F-number
because it was previously only in commit messages and a module docstring, and it is
the clearest example in this repo of the difference between "tested" and "used".

`app.py` did `raw.decode("utf-8", errors="replace")` on **every** upload. A PDF became
mojibake beginning `%PDF-1.6 %...346 0 obj <</Metadata...`, was chunked, and was
summarised — the model would have described object tables and stream keywords, and the
job would have been stored **`done`**. Four of the operator's jobs were destroyed this
way before anyone looked at the `document` column.

**There was no PDF text extraction anywhere in the codebase**, for the most common
document format in health, legal, government and education — the exact sectors this
project targets. The code comment even said *"documents come from scanners and Windows
desktops"*, which is where PDFs come from, while handling only text encodings.

**Fixed** in `missing_link/extract.py`: sniff **magic bytes** (not filename
extensions), extract PDF text with `pypdf`, and **refuse** everything it cannot read —
naming the format, and telling the operator what to do. A PDF with almost no
extractable text is **scanned**, needs OCR we do not have, and raises rather than
summarising an empty document.

**The pattern this completes.** Four defects in one component, all the same shape — a
plausible-looking result that is actually worthless:

| Finding | Stored as | Actually |
|---|---|---|
| F21 | `done` | empty summary |
| F34 | `done` | truncated mid-sentence |
| **F38** | `done` | a summary of PDF structure |
| F36 | `running` | nothing happening at all |

**So the rule for this codebase is now explicit: a completion path must refuse, not
degrade.** Every guard in `worker.py` and `extract.py` exists because the alternative
was output that looked fine.

**And the process lesson, which is F34's restated one level up:** the pipeline had 41
passing tests, then 66, and still fell over on the first *real* file. Synthetic
fixtures test the half we wrote. **Exercise it with the operator's actual inputs before
believing anything about it.**

---

## F39. The F36 watchdog killed a healthy job in 79 minutes. `/health` shares the queue it is meant to be probing, so it cannot tell BUSY from WEDGED.

**CONFIRMED by forensics on the destroyed job, by reading `server.cpp`, and by
reproduction under real load on node 1, 2026-08-18.**

`cluster/llama-watchdog.sh` was installed at **22:24 on 2026-08-17** as F36's fix:
probe `/health` every 60 s with `--max-time 25`, restart after **two** consecutive
failures. At **23:43:57 the same evening** it restarted a perfectly healthy server
and destroyed Missing Link job `06af2911d7fc` — a 97,299-character document,
**10m55s** of completed work, chunk 1 finished and chunk 2 in flight. The client
saw `RemoteDisconnected`. **Zero chunk summaries were persisted.**

### The server was not even loaded — and its own slot counters said so

At no point was more than **one of four slots** occupied. Worse, and correcting a
detail that was believed during triage: `/health`'s slot counters reported
`n_idle_slots=4 n_processing_slots=0` **while the server was prefilling**, and
`3/1` only during the generation phase:

```
23:33:02  launch_slot_with_task  id_slot=0 id_task=51
23:34:32  slot data  n_idle_slots=4 n_processing_slots=0   <- mid-prefill of task 51
23:36:11  slot data  n_idle_slots=4 n_processing_slots=0   <- mid-prefill of task 51
23:36:53  slot data  n_idle_slots=3 n_processing_slots=1   <- now generating
23:39:16  release_slots  id_task=51
23:39:17  launch_slot_with_task  id_slot=1 id_task=718
23:41:16  slot data  n_idle_slots=4 n_processing_slots=0   <- mid-prefill of task 718
23:43:26  slot data  n_idle_slots=4 n_processing_slots=0   <- mid-prefill of task 718
23:43:57  RESTART
```

**So a "smarter" watchdog that read `slots_processing` from `/health` would have
concluded the server had nothing to do at the exact moment it was working hardest.**
The payload of the in-band probe is as misleading as its latency. The watchdog log
shows flapping, not failure:

```
23:35:47  did not answer /health within 25s (failure 1/2)
23:36:53  healthy again after 1 failure(s)
23:40:27  did not answer /health within 25s (failure 1/2)
23:41:16  healthy again after 1 failure(s)
23:42:47  did not answer /health within 25s (failure 1/2)
23:43:57  did not answer /health within 25s (failure 2/2)
23:43:57  restarting llama-server@8080 -- hung but alive
```

### Mechanism: `/health` is not an out-of-band probe

`/opt/ik_llama.cpp/src/examples/server/server.cpp`, `handle_health` (lines 726–765),
builds a `SERVER_TASK_TYPE_METRICS` task, calls `ctx_server.queue_tasks.post(...)`
onto **the same single task queue `update_slots()` drains for token generation**,
then blocks on `ctx_server.queue_results.recv(task.id)`. `handle_slots` (line 798)
and `handle_metrics` (line 822) use the **identical** pattern. So none of the three
is an independent signal — **all of them go quiet exactly when the server is
busiest.**

**How long can they legitimately be quiet? Measured from the incident log itself.**
During prefill the queue is drained only once per `n_batch` (2048 tokens) step:

```
23:33:02  kv cache rm [p0, end) ... p0=0
23:34:32  kv cache rm [p0, end) ... p0=2048     <- 90 s later
23:36:11  kv cache rm [p0, end) ... p0=4096     <- 99 s later
23:39:16  prompt eval time = 231281.07 ms / 4890 tokens (21.14 tokens per second)
```

2048 tokens ÷ 21.14 t/s = **~97 s between task-queue service points.** The second
document's prefill (task 718) was served at 23:41:16 and 23:43:26 — gaps of **120 s
and 130 s.** The same log shows a probe issued at ~23:35:22 being answered at
**23:36:11 — 49 s later — with `status=200`**, long after `curl` had given up.

**So `/health` has a legitimate worst-case latency of 97–130 s on a perfectly
healthy server — twice the 60 s timer period. No `--max-time` value separates busy
from wedged.** A proposal to raise the timeout to 45 s and require an unbroken
480 s failure streak was evaluated and **rejected on this evidence**: at 45 s the
probe still times out through most of every prefill step, so the streak would
accumulate on a fully healthy server and the design would rest entirely on the
grace period being longer than the document, which is not a property anyone can
guarantee for the next document.

**The 503 path is a red herring.** `handle_health` does return 503 when
`n_idle_slots == 0`, but only `if (req.has_param("fail_on_no_slot"))`, which the
watchdog never sent. Every observed failure was a bare timeout (`HTTP 000`), never
a 503. "Accept 503 as healthy" would have changed nothing.

### What actually separates them: the unit's own CPU time

F36's genuine wedge answered **zero probes for 7+ minutes at load 0.11** — silent
*and* doing nothing. A busy server is silent *but burning CPU*. That distinction is
genuinely out of band: it touches neither the task queue, nor the HTTP layer, nor
the model.

**Measured on node 1, `CPUUsageNSec` for `llama-server@8080.service`:**

| Server state | CPU over a 20 s probe window | `/health` |
|---|---:|---|
| idle | **0 ms** (exactly) | 200 in 0.99 ms |
| 4 concurrent real completions | **80,086 ms — 400% of one core** | 000 (timeout) |
| SIGSTOPped (simulated wedge) | **0 ms** | 000 (timeout) |

**Two orders of magnitude of separation, in seconds rather than minutes.** The
rewritten `cluster/llama-watchdog.sh` therefore decides:

- `/health` returns **any** HTTP code → alive, clear the streak. (503 "Loading
  model" counts: it is talking, and `TimeoutStartSec` owns startup.)
- silent **and** CPU advancing ≥ 5% of one core → **BUSY**, never restart.
- silent **and** CPU flat → start/extend a wall-clock streak; restart only after
  **300 s of unbroken** silent-and-idle evidence.
- CPU accounting unavailable → log loudly and **take no action**.

`/props` is probed as corroboration only. It is the one endpoint that does **not**
post to the task queue — it reads model metadata directly — so it distinguishes
"HTTP layer alive, inference dead" (F36's signature) from "whole process gone". It
never decides anything by itself.

### Two signals that were considered and are worse

- **Load average — rejected, and this box proves why.** It is machine-wide. During
  this investigation node 1 read `load average: 2.30` while `llama-server` was
  completely idle, because other agents were running a pytest suite, a `pip
  install` and a 65 GB model download. `CPUUsageNSec` is scoped to the unit's own
  cgroup and was `0` throughout. **A coordinator is never a quiet machine.**
- **Journal-line advance — rejected.** `llama-server` logs per *request*, not per
  ubatch. Across the 97 s prefill step above, its only journal output was the line
  the watchdog's own probe provoked. Journal advance would largely have measured
  the watchdog.

### Verification actually run (not a test count)

- **Healthy idle server:** silent no-op, exit 0, no state file, `ActiveEnterTimestamp`
  unchanged.
- **Four concurrent real completions, ~2000-word prompts, `max_tokens: 1024`, eight
  60 s ticks:** six probes returned `HTTP 000` — *the old script would have restarted
  at tick 2* — and every one was classified `BUSY, not wedged` at 399–400% of one
  core. `ActiveEnterTimestamp` never moved.
- **Simulated wedge (`SIGSTOP`):** `connect=0.000157s` but `starttransfer=0.000000s,
  code=000` — **the kernel completes the TCP handshake from the listen backlog while
  the process is frozen**, reproducing F36's "accepting TCP, answering nothing"
  exactly. CPU delta `0ms/20010ms (0%)`. The script held at `streak=0s/300s`,
  `65s/300s`, `131s/300s` without acting. `SIGCONT` → `/health` 200 in 1.5 ms, and
  the next tick logged `answered /health (HTTP 200) -- clearing fault streak`.
- **Restart path, `WATCHDOG_GRACE=60`:** `SUSPECT` at tick 1, `RESTARTING ... WEDGED:
  71s of unbroken silence` at tick 2, `systemctl rc=0`, new MainPID, `/health` 200
  within 15 s, `n_ctx_slot=8192` intact.

**Where SIGSTOP differs from F36, honestly:** freezing the process stops the HTTP
accept threads too, so `/props` also times out and the script takes the "whole
process unresponsive" branch. F36's real wedge had the HTTP layer alive with the
task queue dead — the other branch. Both branches feed the same streak and the same
decision, so the difference is only in the logged evidence string, but the
"HTTP-alive, queue-dead" branch has **not** been exercised against a real wedge.

### Cost of being wrong, in both directions

- **A false restart** now requires **300 s of unbroken evidence that the unit's own
  cgroup consumed no CPU while answering nothing on two independent endpoints.** No
  amount of load produces that.
- **A genuine wedge** is now caught in **300–380 s** (five to six ticks), against
  the old design's ~2 minutes. That is slower, and it is the right trade: the job
  is already lost when the server wedges, and `worker.py`'s `DEFAULT_TIMEOUT_S =
  3600` means nothing else is waiting on the watchdog.
- **One case is knowingly weaker.** With `--rpc` backends the coordinator
  legitimately sits idle on a socket while a remote shard computes, so local CPU is
  *not* proof of life. The script detects `--rpc` in the unit's cmdline and raises
  the grace to 900 s. **That number is a safety margin, not a measurement** — it has
  not been tested against a wedge in a sharded topology.

### Making the failure legible

Today nothing tells an operator that a job died *because the watchdog fired*; the
job just says `RemoteDisconnected`. The script now (a) logs the restart with the
evidence inline and the explicit sentence **"ANY IN-FLIGHT JOB ON PORT 8080 DIED AT
THIS TIMESTAMP"**, and (b) appends a machine-readable line to
`/var/lib/llama-watchdog/restarts.jsonl`:

```json
{"ts":"2026-08-18T06:55:48+10:00","epoch":1787000148,"unit":"llama-server@8080",
 "port":"8080","action":"restart","reason":"wedged","streak_s":71,"grace_s":60,
 "cpu_delta_ms":0,"window_ms":20013,"cpu_pct_of_core":0,"health":"000",
 "props":"000","probe_timeout_s":20,"server_active_since":"Mon 2026-08-17 23:43:57 AEST"}
```

That file is the correlation key: a job that failed with `RemoteDisconnected` within
seconds of an `epoch` in this file was killed by the watchdog, not by the model.

### What this generalises to for the skill

**A liveness probe that shares the resource it is probing cannot distinguish
saturation from failure.** `/health` looked like the obvious out-of-band check and
was in fact the single most in-band thing available — it rides the same queue as the
work. Under load it reports *dead*; the recovery action for *dead* is a restart; and
the restart destroys the work that caused the load. **The check does not merely fail
to detect the fault, it manufactures one, and it does so preferentially against the
longest-running jobs — which on this cluster are the only jobs that matter.**

The rule for the skill, in three parts:

1. **Read the health endpoint's implementation before trusting it.** In llama.cpp
   and ik_llama.cpp, `/health`, `/slots` and `/metrics` all post to
   `queue_tasks`. Any monitoring built on them inherits the queue's latency.
2. **A liveness signal must be observable when the service is fully saturated.**
   Process CPU time, cgroup accounting, `/proc`, file mtimes — things the kernel
   maintains whether or not the application can answer. Prefer per-unit accounting
   over machine-wide load: a coordinator runs other work.
3. **Recovery must be gated on positive evidence of death, not on absence of
   evidence of life** — because the recovery action is destructive and the absence
   of evidence is exactly what a busy server produces.

**This is the second time this project has been bitten by a health check failing for
reasons unrelated to what it was testing.** F31: `bench/two-node-smoke.sh` reported
`FAIL: no usable response` on a fully working two-node cluster, because it read
`choices[0].message.content` from a reasoning model that had spent its budget in
`reasoning_content` (F21). There the false negative would have sent a team
cherry-picking an upstream patch for bug #26500, which they did not have. **Here the
false positive did not mislead anyone — it deleted eleven minutes of work.** F31's
generalisation was "a gate must report *which* thing failed"; F39 extends it: **a
gate that can also *act* must first prove it is not the load itself it is
detecting.**

**And a note on the watchdog's own footprint.** `n_threads_http=7` on this server.
Every timed-out `/health` probe leaves one of those seven worker threads blocked in
`queue_results.recv()` until the queue drains — the monitor can exhaust the pool it
is monitoring. This is why the rewritten probe timeout is *short* (20 s) rather than
long: there is no benefit to waiting, because the CPU signal has already answered
the question.

---

## F40. ik_llama.cpp fatal-errors on the 5th request of any multi-slot job, and takes the whole server down into a hang that `Restart=always` cannot see. This is F36's real cause, and it unsettles F27.

**CONFIRMED by reproduction on node 2, twice, deterministically; by the source at
`iqk_flash_attn.cpp:317-350`; by process forensics (`/proc/<pid>/syscall`); and by
node 1's own journal**, which shows the same fatal error at 22:01:53 on 2026-08-17
— **ten minutes before the log line F36 blamed for the same hang.** It was noticed
then; it was diagnosed wrongly.

```
iqk_flash_attn_noalibi: found empty attention mask: nek1 = 512, first_k = 512
/opt/ik_llama.cpp/src/ggml/src/iqk/iqk_flash_attn.cpp:347: Fatal error
```

### It is not rare, not chunk-size dependent, and not something the benchmark did

`-fa` was **never passed**. Node 2's command line was byte-identical to node 1's —
`-t 4 -c 32768 --parallel 4 --host 0.0.0.0 --port 8080 --no-warmup --jinja` — and
the server logged `llama_init_from_model: flash_attn = 1` anyway. **ik_llama.cpp
turns flash attention on by default for this model**, so this is the default path,
not an opt-in one.

Reproduced twice, from a clean `systemctl restart`, with nothing but sequential
`/v1/chat/completions` calls:

| | request 1 | 2 | 3 | 4 | **5** |
|---|---|---|---|---|---|
| original run (06:27–06:37) | slot 0, `p0=0` | slot 1, `p0=0` | slot 2, `p0=0` | slot 3, `p0=0` | **slot 0, `p0=67` → FATAL** |
| deliberate repro (07:33–07:38) | slot 0, `p0=0` | slot 1, `p0=0` | slot 2, `p0=0` | slot 3, `p0=0` | **slot 0, `p0=103` → FATAL** |

Slot assignment read from `launch_slot_with_task` in the server's own journal, not
inferred. In the original run request 1 was an unrelated smoke prompt, so the
benchmark's own **chunk 4** was request 5 — which is why the sweep log stops after
three chunks.

**The trigger is the first request that reuses a slot after the other slots have
written KV cells** — i.e. request `--parallel + 1`, which for the standard
configuration is **request 5**. Prompt lengths were 1060–1410 tokens throughout and
the four survivors were indistinguishable from the killer. It would have died at
**every** chunk size in the sweep, and **any Missing Link job longer than four
chunks kills the server it is running on.**

### The mechanism, from the source on disk

`ggml/src/iqk/iqk_flash_attn.cpp`, guarded by `if (n_swa > 0 && mask)`:

```cpp
constexpr int kMinBatch = 256;
int ntokens = std::max(kMinBatch, neq1);
int nblock  = (ntokens + n_swa + kMinBatch - 1)/kMinBatch;
int first   = nek1 - nblock*kMinBatch;      // keep only the LAST nblock*256 cells
```

gpt-oss-120b sets `n_swa = 128` (`gpt-oss.attention.sliding_window = 128` in the
GGUF), so `nblock*kMinBatch` is **512** — exactly the `nek1 = 512` in the error. The
optimisation keeps only the last 512 KV cells on the assumption that they are a
superset of every query's sliding window. **That holds for one sequence, whose cells
are appended in position order. It does not hold for four interleaved slots**, where
the last 512 cells can belong entirely to *other* sequences. The mask for the
current sequence is then wholly masked, `first_k` runs to `last_k`, and the guard
added by upstream PR #1923 aborts.

The abort fires only during **decode** (`neq1 == 1`), which is why every crash
landed 50–85 s into a request — right after prefill finished.

### Upstream status: the CPU twin of an OPEN, unfixed bug

- **CONFIRMED.** The abort itself was *added deliberately* by
  [PR #1923, "CPU FA: Check for empty attention mask"](https://github.com/ikawrakow/ik_llama.cpp/pull/1923)
  (merged `b50b091`, 2026-06-08), to replace the confusing
  `GGML_ASSERT(S > 0)` of
  [issue #1910](https://github.com/ikawrakow/ik_llama.cpp/issues/1910). **It is a
  diagnostic, not a fix.** Our tree is `8337e4cd` (2026-08-15), two months *after*
  it — so we are not behind on this; there is nothing to pull.
- **REPORTED, and it is the same defect.**
  [Issue #2186](https://github.com/ikawrakow/ik_llama.cpp/issues/2186), *"CUDA
  flash-attention SWA tail slice is unsound with `--parallel > 1` (silently drops
  in-window cells)"*, filed 2026-07-25 and **still open**, describes the identical
  reasoning error in the CUDA path: *"That is sound for ONE sequence: cells are
  appended in position order… It is not sound for several sequences."* Its
  suggested fix — only apply the slice when `n_seq_max == 1` — would fix ours too.
- **INFERRED but well supported:** on CUDA this bug **silently corrupts output**
  (2 of 4 slots produced different text, max logprob delta 7.11 vs a 0.31 noise
  floor). On CPU, post-#1923, it **aborts loudly instead**. Given this project's
  faithfulness requirement, the loud version is the better of the two — but see
  below for what it does while aborting.
- No issue reports the CPU path at `--parallel > 1`. **This is worth reporting
  upstream**: it is a one-line reproduction against a stock `llama-server` on a
  stock gpt-oss GGUF, and #2186 already contains the diagnosis.

### Why the crash produced a HANG instead of a restart — the forked-abort deadlock

This is the part that matters more than the crash, and it is now **mechanically
established rather than hypothesised**.

`ggml_abort` calls `ggml_print_backtrace`, which in `src/ggml/src/ggml.c:216-219`
does:

```c
int pid = fork();
if (pid == 0) { execlp("gdb", ...); execlp("lldb", ...); exit(EXIT_FAILURE); }
else { waitpid(pid, &wstatus, 0); }
```

**`fork()` from a multithreaded process gives the child exactly one thread and every
lock the other threads were holding.** The child then needs the allocator to reach
`execlp` and deadlocks on an inherited mutex. Four compute threads hit the assert
simultaneously, so there were four such children. Measured on node 2 while it was
wedged:

| | pid | `/proc/<pid>/syscall` | `wchan` | CPU over 5 s |
|---|---|---|---|---|
| `sh -c` wrapper (systemd's `MainPID`) | 16040 | — | `do_wait` | 0 |
| **llama-server** | 16041 | **61 = `wait4`** | `do_wait` | **0 ticks** |
| forked abort children ×4 | 16451–4 | **202 = `futex`** | `futex_wait_queue` | 0 |

So the parent blocks in `wait4()` forever, never reaches `abort()`, and **never
exits**. `systemctl show` reported `ActiveState=active`, `SubState=running`,
**`NRestarts=0`** for 48 minutes. Two further consequences worth knowing:

- **The listening socket survives.** `ss -ltnp` showed *all five* processes holding
  `fd=3` on `:8080`, because the children inherited it. TCP connects therefore still
  succeed and nothing answers — **a port-open check reports green through this.**
- **`MainPID` is the `sh -c` wrapper, not the server.** Even a clean crash of the
  server is one indirection away from systemd, though in this case it made no
  difference: neither process exited.

### This is F36, and F36's diagnosed cause was wrong

F36 recorded a wedge on node 1 on 2026-08-17 and attributed it to *"a client
disconnecting mid-generation"*, citing `srv stop: cancel task, id_task = 220`.
Node 1's journal says otherwise:

```
21:58:40  srv stop: cancel task, id_task = 36        <- a REAL client disconnect
21:58:40  launch_slot_with_task id_slot=0 id_task=215  <- server carries on fine
22:00:35  kv cache rm [p0, end) id_slot=0 id_task=215 p0=2048
22:01:53  iqk_flash_attn_noalibi: found empty attention mask   <- THE FATAL ERROR
22:07:40  srv stop: cancel task, id_task = 215       <- client gives up on a corpse
22:11:43  srv stop: cancel task, id_task = 220       <- the line F36 blamed
22:21:47  systemd: Stopping llama-server@8080        <- the operator's restart
```

**A client disconnect happened at 21:58:40 and the server kept serving.** The fatal
error came ten minutes before the line F36 identified as the cause, and everything
after it was clients timing out. So:

- **F36's symptom description is correct and its three fixes remain right.** The
  wedge is real, `Restart=always` is blind to it, `_worker_loop` needed guarding.
- **F36's *cause* is superseded.** It was not an ordinary operational action. It was
  this bug. Correct `CLAUDE.md`'s "a client disconnecting mid-generation is enough"
  when the plan is next folded back.
- **Both known "up and lying" incidents on this cluster are now the same bug**, and
  this is the first one with a definite cause — established by reproduction rather
  than by correlation.

### Blast radius: mainline is unaffected

`kv_unified = 'false'` — mainline b10369 gives each slot its **own** KV cache, so
the interleaving that breaks ik's tail slice cannot arise. Verified, not assumed:
the identical reproducer against `/opt/llama.cpp/bin/llama-server` on the same node,
same model file, same flags, **completed all nine requests — two full slot cycles,
four past the point where ik dies twice out of twice.** Prefill and generation
figures for both engines are in `docs/measurements.md`.

Node 1 runs `LLAMA_BIN=/opt/ik_llama.cpp/bin` with `--parallel 4` and has the
identical exposure; it has already hit this once (22:01:53, above).

### What this does to F27 — stated plainly

**F27's recommendation ("adopt ik_llama.cpp for the document-summarisation
workload") does not survive this finding in its current form.**

F27 measured `llama-bench`, which issues **one** sequence. It could not have seen
this: the defect needs `--parallel > 1` and a slot reuse, and `llama-bench` does
neither. The +52% prefill number is not wrong — it is measured on a configuration
we cannot actually serve documents from.

**A 22% speedup that aborts on the 5th request of every job is not a speedup, it is
a 100% failure rate on real work.** Missing Link's map-reduce sends one request per
chunk; the reference document is 26 chunks. Every job would kill its server, and —
because of the forked-abort hang — kill it in the one way that neither
`Restart=always` nor a port check nor `/health` can report.

So, in order of confidence:

1. **ik_llama.cpp at `--parallel 4` is NOT safe for unattended document work.** Not
   "needs care" — it fails deterministically, on request 5, every time.
2. **Mainline is the safe default until this is fixed**, and it costs what
   `docs/measurements.md` records, not what F27 predicted from `llama-bench`.
3. **Two ik configurations are worth measuring before abandoning it**, and neither
   has been tested yet: `--parallel 1` (removes the interleaving that #2186
   identifies, at the cost of F24's 1.79× batching gain), and flash attention
   explicitly disabled (removes the tail-slice path entirely, at the cost of most of
   ik's prefill advantage). **Neither may be adopted on reasoning alone** — the
   whole point of this finding is that a plausible configuration was standardised
   from a benchmark that could not exercise the failure.

**The transferable lesson, and it is the same one as F34.** `llama-bench` is a
single-sequence microbenchmark. Every number in F27 came from it, and the decision
it drove was about a four-slot server under a map-reduce workload. **The benchmark
did not resemble the deployment in the one dimension that mattered.** For the skill:
an engine may not be adopted on synthetic-benchmark evidence alone — it must survive
`--parallel` × (chunks per document) real requests through the real client first.

### Node 2 has no watchdog, and the watchdog merged tonight would have caught this

Confirmed: node 2 has **no** `llama-watchdog` unit, timer or script — only node 1
does. Node 2 was therefore dead and silent for 48 minutes with nobody looking, and
the blocked benchmark driver on node 1 waited on it the whole time.

**F39's rewritten CPU-progress watchdog matches this failure exactly.** Its rule is
*silent AND the unit's own CPU flat → restart after 300 s of unbroken evidence*, and
this wedge is the purest possible instance: `/health` timed out, `CPUUsageNSec` was
**bit-for-bit unchanged over a 5 s sample** (`utime` 211087 ticks, twice), and load
average was 0.02. It would have restarted node 2 within ~5 minutes instead of 48,
and — unlike the `/health`-only version F39 retired — it could not have been fooled
into calling a busy server dead. **This is direct evidence for the fleet-wide
watchdog rollout, not merely an argument for it.**

It is also a reminder that the watchdog is a *mitigation*, not a fix: restarting
into the same engine means the next job dies on its 5th chunk too.

---

## F41. A faithfulness classifier's reliability DEGRADES WITH EVIDENCE LENGTH, and cross-model agreement stops being a safety net

**CONFIRMED by measurement, 2026-08-18.** Full write-up in
`docs/audit-production-scale.md`. This kills a design that had looked strong,
and the reason it looked strong is the transferable part.

A two-model MiniCheck ensemble (Flan-T5-Large + RoBERTa-Large) was validated on a
36-pair negation battery and scored 95.8% / 97.2%, with **cross-model
disagreement predicting error at precision 1.00 and recall 1.00** — every error
one model made, the other caught. That result justified the whole "flag for
review" design.

**Every one of those pairs was a one-to-three-sentence document.** Re-run with
each pair's identical clause embedded verbatim in a ~4096-token document — the
size production actually scores against:

| | short doc | production scale |
|---|---:|---:|
| Precision of disagreement | 1.00 | **0.75** |
| Recall of disagreement | 1.00 | **0.43** |
| Errors BOTH models made | 0 | **4/72 = 5.6%** (95% Wilson CI 2.2–13.4%) |
| Escalation rate | 6.9% | 5.6% |

**Note the escalation rate went DOWN.** The ensemble did not get noisier, it got
**quieter and less reliable at the same time** — the worst possible combination,
because a shrinking flag list reads as improving quality.

**The flagship case flipped from caught to silent.** `retention_seven_years`
("seven years... or until the client turns twenty-five, whichever is later") is
the exact clause the two-model design was built around, and at production scale
both models agree and are wrong. A 27-document position sub-study isolates why:
**RoBERTa fails on that pair specifically at the MIDDLE position.** That is
lost-in-the-middle (arXiv:2307.03172) striking the *classifier* — the same effect
that justifies chunking the *generator*. Re-running the same pair at the same
position with different filler flipped the answer, so position is a real factor
but not the only source of variance.

### Real model output is worse ground than the fixtures

Scored against 208 real gpt-oss summary sentences (a real pipeline job plus the
chunk-size sweep corpus):

- **55.6% of real summary sentences carry more than one claim.** A classifier
  scores a sentence as one unit, so a sentence with two supported claims and one
  fabricated one yields a middling score that identifies nothing. **Sub-sentence
  decomposition is therefore a prerequisite, not a refinement.**
- **nltk's sentence splitter degenerates on real markdown**, producing 9.1%
  garbage fragments — `"P."` split out of `"K.P. Dutt"`. The tokeniser everyone
  reaches for is not safe on this material.

### Cost inverts the whole proposition

Measured: 17.74 s/claim (Flan-T5) and 8.81 s/claim (RoBERTa) at production
evidence size. The earlier ~75–95 min estimate assumed 8 sentences per chunk;
the **real measured density is 18**. For a 25-chunk document, **hop 1 alone is
~199 minutes — longer than the ~143-minute summarisation job it audits.**

### What this means, and what survives

- **Do not wire the ensemble in as a safety net.** The engineering is sound —
  refuse-rather-than-degrade, offsets computed rather than asked for, two
  correctly-scoped hops. The *empirical claim* that justified trusting it did not
  survive scale-up.
- **Deterministic hard signals become the primary path, not an optimisation.**
  A number either appears in the cited span or it does not, and that check does
  not degrade with evidence length, does not need a GPU, and is explainable.
- **The generalisable lesson, and it is the same one this project keeps
  relearning: a metric validated at one scale tells you nothing about another.**
  F40 was a benchmark that did not reproduce the deployment's *concurrency*;
  this is a validation that did not reproduce the deployment's *evidence length*.
  Both looked rigorous. Both measured the wrong configuration.

**Also found, not fixed:** `audit.py`'s `preflight()` has a broad
`except Exception` that reports "the model failed to load" as an ordinary
length-based refusal. That is a degrade-instead-of-refuse path in the very
function written to prevent one.

---

## F42. The reduce step launders a fabrication into the final summary. Observed, not theorised — and caught by string comparison, not by a model.

**CONFIRMED by measurement, 2026-08-18.** Full detail in
`docs/faithfulness-cascade.md`. This is the finding F25 predicted and
`DESIGN-NOTES.md` E worried about, finally seen in production output.

**What happened.** The reduce step of a completed 5-chunk job asserted a **death
year that appears nowhere in the source document and in none of the five chunk
summaries.** The model inserted world knowledge its input did not contain, at
the exact step where chunk summaries stop being checkable against the source.

**The dangerous part is that the fabricated fact is historically CORRECT.** A
human reading the summary would find it plausible, and a human spot-checking it
against the world — rather than against the document — would confirm it. It is
wrong only in the sense that matters here: **the document does not say it.** For
summaries of legal and health records, "true but not in the source" is not a
lesser failure than a falsehood; it is the same failure, because the summary is
supposed to represent the document.

F25 reasoned that map-reduce would amplify fabrication, because a chunk summary
becomes *source material* for the reduce step where invention is
indistinguishable from genuine content. That was labelled INFERRED for months.
**It is now observed.** Note it did not even require the predicted path — nothing
was fabricated in a chunk and then laundered upward. The reduce step invented it
directly, from a context containing only the five chunk summaries.

### What caught it, and what did not

**A deterministic number check caught it.** The year was not in the cited span,
so it was not in the cited span. No model was consulted.

The two-model MiniCheck ensemble is **off by default and was not used** (F41).
This is the argument for the cascade stated as compactly as it can be:

| | short docs | production scale |
|---|---|---|
| MiniCheck ensemble (F41) | precision 1.00 / recall 1.00 | **0.75 / 0.43**, silent agreeing errors |
| Deterministic cascade | 0/36 false alarms, ~92% caught-or-escalated | **unchanged at both scales** |

**`in` does not care how long the evidence is.** That scale-invariance, not the
raw accuracy, is why the deterministic tier is the primary path.

### The numbers

- **Zero false positives on 978 real claims.** Three findings total; all three
  manually verified as genuine fabrications.
- **100% catch (27/27)** on mutated figures whose replacement is absent from the
  source; 92.6% unconstrained — the gap is value collision inside a 4096-token
  chunk, an inherent ceiling rather than a bug. Fabricated names 258/258.
- **~3.5 seconds to audit a whole 26-chunk document.** Against F41's measured
  ~199 minutes for the classifier's hop 1 alone. The saving is not 35%, it is
  three orders of magnitude, because the expensive question is mostly not asked.

### Two things demoted by measurement rather than argument

- **Entity absence is NOT a hard failure.** A threshold sweep (592 real claims ×
  238 injected names) showed it flagging **one faithful sentence in seven** even
  at its optimum, because the source is OCR mojibake and the model correctly
  reconstructs transliterations. Demoted to routing.
- **Sub-sentence decomposition splits 29.9% of sentences**, deliberately below
  F41's measured 55.6% multi-claim rate: an over-eager split misattributes a
  figure to the wrong clause, which manufactures exactly the false failure the
  checker exists to avoid.

### The generalisable lesson about checkers

**Five defects were found only by running on real output, and every one produced
a FALSE HARD FAILURE on correct text.** `may` matched case-insensitively as a
month. A non-breaking hyphen split `twenty‑four` into 20 and 4.

**A checker's bugs land almost entirely on the sentences that were going to
pass.** Its failure mode is not missing fabrications, it is crying wolf on
correct work — and a reviewer who learns to ignore the flag list has been made
worse off than one with no checker at all. So a checker must be validated on
material known to be CORRECT, not only on material known to be wrong.

---

## Addendum to F40 (2026-08-18): mainline shares the exact fork/waitpid abort path, so switching off ik_llama.cpp does NOT retire the forked-abort hang hazard

**CONFIRMED**, two ways: directly from `commit 5f7e1a1` ("feat(watchdog): node 2
coverage, nprocs tripwire, and a synthetic transaction"), and independently by
reading `/opt/llama.cpp/src/ggml/src/ggml.c` on node 1 in this session — lines
196–234 contain the identical `fork()` → `execlp("gdb", ...)` /
`execlp("lldb", ...)` → `waitpid()` sequence that F40 traced as the mechanism
of the forked-abort deadlock in ik_llama.cpp.

F40 concluded "mainline is the safe default" on the strength of one specific
trigger (`iqk_flash_attn`'s SWA tail-slice bug) being absent from mainline
because `kv_unified = 'false'` there. That conclusion about the *trigger*
still stands. But the *hang mechanism* — `ggml_abort()` forking a
crash-reporter child from a multithreaded process, the child deadlocking on
an inherited lock before it can `exec`, and the parent blocking in `wait4()`
forever while the listening socket survives in the zombie children — lives in
shared `ggml.c`, not in ik-specific code. **Any future `GGML_ASSERT` failure
in mainline, triggered by anything else, would hang the server the identical
way**, invisible to `Restart=always` and to a port check, for exactly the
reasons F40 documented.

**Mitigation added, not a fix.** `cluster/llama-watchdog.sh` (per the same
commit) now tracks a per-service process-count baseline — measured live on
both real busy nodes at `llama-server=2 procs, rpc-server=1` — and treats a
sustained count above baseline (confirmed on a second sample after a 90 s
grace) as `WEDGED` directly, without waiting for the CPU-flat streak alone.
This catches the forked-zombie signature specifically, on either engine,
which is the point: the watchdog no longer implicitly assumes the hazard is
ik-only.

---

## F43. The fleet-wide watchdog was silently non-functional on node 2 from install — a monitor that looks installed and evaluates nothing

**CONFIRMED**, `commit 5f7e1a1`. Node 2's watchdog timer was `active` and
firing every 60 s, and **failed on every single tick** with `line 240: HOME:
unbound variable` — systemd service units do not set `$HOME`, and the script
runs under `set -u`. The failure happened before the script reached any of
its liveness predicates, so node 2 was unmonitored for the entire period
between install and this fix being found, despite every operational signal
(`systemctl status`, the timer's own `active` state) saying otherwise. Fixed
with a `${HOME:-/root}` fallback.

**This is the same class of defect as the thing the watchdog exists to
catch.** F39 and F40 are both about a server that looks alive — process
running, port open — while doing nothing. This is a monitor that looks
installed and scheduled — timer active, correct cadence — while evaluating
nothing. Both defeat every check built on "is the unit present and
running," and neither surfaces without reading the monitor's own execution
log, not just its scheduling state.

**Validated in production in both directions once fixed.** Per the same
commit and independently confirmed this session from
`/var/lib/llama-watchdog/restarts.jsonl` on node 1: the corrected watchdog
declined to act across a multi-hour busy sweep on node 2 (no false
restarts), and it genuinely fired twice on node 1 against real wedges —
`2026-08-18T09:13:23+10:00` and `2026-08-18T11:05:43+10:00`, both logged
with `"reason":"wedged"`, `streak_s` at the 300 s threshold (355 s of
unbroken silent-and-CPU-flat evidence), `/health` and `/props` both silent.
Two correct restarts and zero false ones is the outcome F39's design was
built to produce, and this is the first production evidence of it working
end to end — but only after a second, unrelated bug in the monitor itself
was found and fixed. **A watchdog is itself a piece of software that needs
its own liveness check** — "the timer is active" is exactly the same kind
of in-band-looking signal that F39 rejected for the server it watches.

---

## F44. Even niced, a CPU-bound sidecar measurably starves `llama-server` on a 4-core node — co-location is not free

**CONFIRMED**, `docs/audit-production-scale.md` section 7.3 and 9.2 (a
faithfulness-classifier scoring pass run alongside live document work on
node 1). Run at `nice -n 10` / `-n 15` throughout, a `top` snapshot during
the position sub-study caught `llama-server` at **378.9% CPU** and the
audit process at **336.8% CPU** simultaneously on the same 4 physical
cores (load average 8.23, up from 0.6–0.9 with nothing else running).
RoBERTa's own per-claim rate visibly slowed during that window — 18.2–18.7
s/claim against a clean 8.81 s/claim measured earlier in the same run. A
later, separate pass observed `llama-server` at **287.5% CPU** concurrently
with the (still niced) scoring process, load average 9.38, and the
scoring process's rate degraded from 4.7 s/claim to 41.7 s/claim under that
contention — nearly 9× slower.

**`nice` sets scheduling priority, not an exemption from sharing 4 physical
cores.** It measurably helps — the sidecar does not stall completely — but
it does not eliminate the wall-clock cost to either process, and on this
hardware (already bandwidth- and core-constrained per F10/F12) the effect
is large enough to be visible in a single ad-hoc snapshot, not just in
aggregate statistics.

**Relevant to the agent-appliance design in `CLAUDE.md`**, which allows
on-node placement for "the ACTIVE work — queue triage, requeueing stranded
jobs, batch assembly, endpoint health-checking" on the reasoning that it
"only needs to run when the coordinator is up anyway." That remains true for
correctness, but this finding qualifies it for *performance*: any on-node
CPU-bound sidecar that happens to run while a document job is prefilling or
generating will slow that job down, and vice versa, regardless of `nice`
level. Not a reason to move triage off-node — the appliance-relaxation
decision was about availability, not throughput — but a real cost to note
if triage work is ever scheduled to run concurrently with inference rather
than between jobs.

---

## F45. The sentence splitter's line-based fallback distorts clause-marker density — and it is now the metric that gates corpus decisions

**CONFIRMED by measurement, 2026-08-18**, on the first real legislative corpus.
Third independent manifestation of one root cause.

**What was expected and what happened.** A corpus of Australian legislation was
loaded to answer the chunk-boundary question, which had returned 0 of 84 events
because the only legal-styled document in the store was too short to produce a
boundary. Legislation should have shown *dramatically* higher clause-marker
density than the narrative texts. It showed **1.8–4.9% against 0.00–1.4%** — only
2–4×.

**The diagnosis, and it is not an extraction failure.** The extracted text is
clean and correctly ordered. But **63.8% of extracted lines are under 80
characters**: legislation's HTML is one `<p>` per subsection and one line per
table-of-contents entry, `extract.py` correctly turns each block-tag close into a
newline, and `chunk_boundary_audit.sentence_spans`' regex fallback then treats
every newline-delimited fragment as its own pseudo-sentence. Thousands of short
headings and list items enter the denominator and structurally cannot carry
`unless` / `except` / `subject to`. A raw grep found **123 marker phrases** in
the current Privacy Act compilation.

### The same root cause, now seen three ways

| Manifestation | Measured effect on marker density |
|---|---|
| PDF hard line wraps | 75.00% → 53.85% |
| Raw HTML markup passed through unextracted | 0.1176 → 0.0455 |
| **Legislation's paragraph-per-clause HTML** | **denominator inflated by structural lines** |

The splitter is the common factor. nltk is deliberately excluded from the
production venv (~1.5 GB, audit-only) and its own fallback is no better — F41
measured nltk producing 9.1% degenerate fragments on real markdown, splitting
`"K.P. Dutt"` into `"P."`.

### Why this one matters more than the other two

The first two distorted a research measurement. **This one distorts the number
the corpus page shows an operator to decide whether a document is worth
using at all** — and marker density is not comparable across genres while the
denominator counts structural lines. A document-selection verdict built on it is
built on the instrument, not the document.

**Consequence:** treat marker density as **within-genre comparable only** until
the denominator excludes non-sentence fragments. A legislative document scoring
2% and a narrative one scoring 1.4% are not two points on one scale.

### The lesson, which is this project's most repeated

`docs/measurements.md` exists because a number with an unexamined provenance
propagates into architecture. This is the same failure at one remove: the number
was measured correctly, by an instrument nobody had characterised on the material
it was about to be pointed at. **F40 was a benchmark that did not reproduce the
deployment's concurrency; F41 a validation that did not reproduce its evidence
length; this is a metric that does not survive a change of document genre.**

**Also worth recording from the same fetch:**

- **`ombudsman.gov.au` sits behind a Cloudflare JS challenge** and returns 403 to
  plain HTTP. The agent skipped it rather than scripting around a bot wall —
  correct, and a real constraint on corpus assembly from government sources.
- **`legislation.gov.au` site chrome can be avoided entirely** by fetching the
  epub-internal HTML rendering, which is self-contained legislative text. OAIC
  pages have no such alternative and carry a **5–9% mega-menu** before the report
  body. Real content, correctly extracted, but it dilutes every density figure.
- **All four government PDFs extracted cleanly — but they were single-column.**
  The multi-column failure mode pypdf is known for was therefore *not* tested.
  Reported honestly rather than concluding PDF extraction is safe.

---

## F46. A citation can be perfectly CORRECT and the sentence it anchors still false — and a stricter regex would have destroyed all seven valid citations

**CONFIRMED.** The citation-*accuracy* audit STATUS.md had recorded as owed is
done, deterministically, on real output. No model was consulted at any point —
labels were resolved to spans in code, per the standing rule that asking a model
where something came from scores ~38% on the *easier* task of merely validating
one.

**The headline result is good, and its sample size is 1.** Job `18339bace8f0`:
**7 markers, 7 CORRECT, 0 WRONG-CHUNK, 0 UNSUPPORTED-ANYWHERE.** Three
independent deterministic lines agree — 15/15 numbers matched inside their own
cited span (the F42 check, run in citation scope, finding nothing); the cited
chunk is argmax for 6/7 by trigram containment; and every distinctive rare term
localises to its cited chunk. The one non-argmax marker, `[Section 7]`, is a
**length artifact not a wrong chunk**: chunk 6 is 7,692 chars against ~16,400
for the others, which depresses containment, and chunk 6 still wins on word
overlap (0.534 vs 0.479).

**Why n is honestly 1, established rather than assumed.** Of the other three
completed jobs, two are single-chunk (`fbac801d306a`, `2b4c926a799a`) and **no
reduce step runs on a single-chunk job**, so markers were never requested. The
third, `6c0358825609`, ran the reduce step and emitted zero markers — and that
is not a model failure: `git reflog` puts the repo root reaching `7c1266b` (the
citations commit) at **08:46:48**, while `missing-link.service` last restarted
at **08:46:19** — **29 seconds earlier**. The feature arrived by fast-forward
from a separate worktree, so the running process provably did not have it. One
document, one genre, a known-mojibake source. **A rate may not be quoted off
this.**

### The near-miss, and it is the sharpest part of this finding

**The model emitted its markers with U+202F NARROW NO-BREAK SPACE, not ASCII
space** — `'[Section 1]'`. `_SECTION_MARKER_RE` uses a tolerant `\s`, which
is the only reason all seven resolved. **A stricter literal-space regex would
have dropped 7 of 7 valid citations and reported "the model ignored the
citation instruction"** — a total, confident, and completely wrong conclusion
about the model, caused entirely by one invisible character in our own parser.

This is the same shape as F45 (a hand-rolled text primitive silently corrupting
the measurement that depends on it) and the same shape as F40's lesson: the
instrument fails quietly and the failure is legible as a result. **When output
parsing reports 0% compliance, suspect the parser before the model.**

### The defect the audit found, which is a NEW shape

The first sentence of the summary asserts *"Swami B.R. Bṛdhar (Śrī
Bhaktisiddhānta Sarasvat)"* — **conflating the author with his guru.** The
source at char 109 reads `SwÅmÈ B.R. ßrÈdhar`; separately, at char 3599, `His
own Guru-Mahåråj çr^la Bhaktisiddhånta Saraswat^ ëhakura`. Both facts sit in
chunk 0, which is exactly what `[Section 1]` cites.

**So the citation is correct, the span is correct, and the claim is false.**
This is distinct from F42, where a fabricated number was laundered through the
reduce step; here nothing is fabricated and nothing is misattributed — two true
facts from the cited span are *merged into a false one*. `cascade.py`'s own
header already states the principle (**"an attribution is not a verification"**);
this is the first observation of it on real output. The deterministic tier did
route the sentence for escalation, flagging a distinctive term absent from the
whole document, but it flagged 15 of 43 sentences overall — **a routing signal,
not a pointer.**

### The invented-marker guard works, verified rather than assumed

Substituting `[Section 47]` into the real job output: `markers 7, valid 6,
dropped 1`, and `'47'` appears nowhere in the rendered prose. `[Section 0]` and
`[Section 8]` also drop. `[ section  7 ]` resolves (case/whitespace tolerant).
`[Sections 1 and 4]` and `[Section four]` land in `unparsed` and stay in the
prose verbatim rather than becoming links. `templates/job.html` renders only
`segments`, and a dropped marker is in neither list, so it is *structurally*
incapable of becoming a link — not merely filtered.

### Two gaps this leaves open

- **The deterministic citation audit has no regression harness.** It was run by
  hand via the `cascade job --mode citations` CLI. Nothing would catch a
  regression in it — including a re-tightening of that `\s`.
- **20 of 43 claims ended at `needs_classifier` and are permanently
  unchecked.** They are correctly *labelled* as such, but **F41 says the
  classifier cannot close that gap at production chunk length**, so this residue
  is not a backlog item awaiting a classifier run — on current evidence it does
  not have a solution. Every entity flag raised (5) was adjudicated an artifact
  of the mojibake source, independently vindicating F42's demotion of entity
  absence from a hard failure to a routing signal.

---

## F47. GLM-5's active-parameter count is 40.8 B, computed not published — and it closes the Model B question in the negative

**CONFIRMED.** Two research gaps blocked the Model B decision. Both are now
closed, and together they retire the decision itself rather than answering it.

### GLM-5 / 5.1 / 5.2 active params = ~40.8 B

The number is published nowhere — model cards, the arXiv abstract and every
GGUF quantiser card are silent. It was **computed from `config.json`**, which
all three releases share in every relevant field:

```
78 x (165.0 M attn + 9.4 M DSA indexer) = 13.60 B
 3 x 226.5 M dense FFN                  =  0.68 B
75 x 341.3 M (9 of 257 experts + gate)  = 25.60 B
     154880 x 6144 lm_head              =  0.95 B
                                          40.83 B
```

**Labelled CONFIRMED rather than INFERRED because the arithmetic validates
itself:** the same per-tensor inventory run over all 257 experts plus the MTP
block reconstructs **753.86 B against the published `safetensors` total of
753,864,119,552 — 0.00% error.** The single free choice (whether the DSA
indexer's query projection reads `hidden_size` or `q_lora_rank`) is decided by
which variant closes the last 1.3 B exactly.

**Three independent facts each reject it:**

- **40.8 B active is MORE than DeepSeek-V3.2's 37 B**, and ~8x gpt-oss-120b's
  5.1 B. On this fleet active params *are* generation speed.
- **It is LESS faithful than GLM-4.6** — `zai-org/glm-5` sits at **10.1%** on
  the Vectara board against GLM-4.6's **9.5%** (REPORTED; 5.1/5.2 have no
  entry). **The strongest open reasoner is not the most faithful one**, which
  is the assumption that put GLM on this shortlist in the first place.
- **IQ4_XS is 402.9 GB real against 368 GB free** — the F16 disk blocker
  again, worse than Kimi K2. Plus it is a thinking model, and F35 established
  there is no universal thinking-off switch.

**Software support was NOT the blocker**, contrary to expectation: llama.cpp
PRs #19460 (GLM MoE DSA arch) and #25407 (DSA indexer) both merged before our
pin b10369 (`6e62ba5`, 2026-08-11).

### Finix S1 32B does not exist as weights, and its 1.8% is an artifact

`huggingface.co/antgroup/finix_s1_32b` returns **HTTP 401**; the public
`antgroup` org holds two models, neither Finix. REPORTED as API-only and
proprietary, and domain-specific to insurance claims.

**The scepticism the 1.8% deserved was warranted, and the mechanism is worth
keeping.** Its average summary is **172.4 words against a 106.9-word median**
across 105 listed models — sixth-longest on the board. **The leaderboard's own
FAQ states that a copy-paste extractive summariser would score 0% hallucination
and that it is "not evaluating the quality of the summaries."** Long extractive
output is exactly what scores well there and exactly what is useless as a
summary. **A hallucination score cannot be read without reading the summary
length beside it.**

### The consequence: Model B is closed in the NEGATIVE

**"One frontier model too large for any single machine" cannot be justified on
this fleet.** This is a decision, not a deferral — every candidate is now
priced and each is dominated. GLM-5 REJECT; Finix S1 REJECT (no weights);
Kimi K2 REJECT (unchanged, F25); DeepSeek-V3.2 stands on merit alone and does
not run at N=2. GLM-4.6 becomes viable only at N>=4.

**At N=7 the S=1 tier delivers 12-47x the aggregate tokens of the GLM-5 tier**
— and GLM-5 offers no faithfulness gain to weigh against that. Fetch cost at
93.8 Mbit/s (F28) is **9.5 h per node** for GLM-5 IQ4_XS, against 1.5 h for
gpt-oss.

**The one experiment that reopens it:** GLM-4.6 **UD-IQ1_S at 96.9 GB is
S=1**, hence replicable at R=7. Whether 1-bit retains enough of the 9.5% to
beat gpt-oss's 14.2% is genuinely open — DESIGN-NOTES H warns UD's edge
shrinks at the low-bit end — and it costs 2.3 h to fetch.

### Flagged, not chased: GLM-4.7-Flash may dominate the incumbent

`zai-org/GLM-4.7-Flash` was never on the shortlist and appears to beat
gpt-oss-120b on every axis this project ranks: **31.2 B total** (CONFIRMED from
HF safetensors), **~3.6 B active** (computed, reconstruction within 2%),
**9.3% Vectara** vs gpt-oss's 14.2%, **MIT**, **18.3 GB at Q4_K_M**, S=1,
predicted ~8.2 tok/s, and **half an hour of link time**. llama.cpp support via
PR #18936, before our pin.

**Do not adopt it on this paragraph.** It is a hybrid reasoning model
(F35 again), 31 B total is far less stored knowledge than 120 B, and everything
but the file sizes and config is REPORTED or INFERRED. It is cheap enough to
settle by measurement, which is the only way this project is allowed to settle
it.

### Method note worth reusing

The predicted-throughput column is INFERRED from F24's 17.3 GB/s effective MoE
bandwidth, and **the method reproduces 6.06 predicted against 6.05 measured for
gpt-oss-120b.** That is one validation point, not a validated model, but it is
enough to rank candidates that differ by 8x. Real GGUF byte counts were used
throughout, per DESIGN-NOTES H's UD-quant capacity trap; this corrected three
sizes carried in the docs (GLM-4.5-Air 60.5 not 58, DeepSeek-V3.2 358.3 not
363, GLM-4.6 190.7 not 189).

---

## F48. The most-recommended sentence splitter is WORSE than our regex — and the legal-domain one nobody searched for fixes F45 outright

**CONFIRMED.** Prompted by the operator's challenge — *"why aren't we using an
existing pipeline?"* — every hand-rolled text component was audited against
what already exists. The answer is **yes for exactly one component, no for
three, and unmeasured for the fourth** — and the one adoption is not the
library anybody would have reached for.

### pysbd is worse than the code it would replace

Five candidate splitters were run **against the real corpus in the job store,
on the exact metric F45 says is broken.** pysbd is the most-recommended library
for this job and carries a peer-reviewed **97.92% Golden Rules** claim. On the
Privacy Act compilation it scored **2.76% marker density against our regex
fallback's 2.85%** — worse than the thing it would replace — **and took 133.94 s
to be worse.**

**The mechanism is the entire lesson: pysbd was evaluated on well-formed prose,
and our failure case is a document that is structurally a list.** A benchmark
score earned on one text genre says nothing about another. This is F40's lesson
in a different domain — *a benchmark that does not reproduce the deployment is
not a benchmark of the deployment* — and it is why "adopt the well-cited
library" would have been the wrong call made for good-sounding reasons.

### nupunkt fixes it, and nobody had looked for it

`nupunkt` is a **legal-domain** sentence splitter: pure Python, **zero runtime
dependencies**, MIT, model bundled in a 9.1 MB wheel. Measured on the same
corpus:

| Metric | Before | After |
|---|---:|---:|
| Structural fragments | 65.0% | **12.3%** |
| Fragments with no terminal punctuation | — | **0.0%** |
| Legislative marker rate | 2.85% | **10.53%** |
| Legislative : regulatory separation | 2.2x | **4.8x** |
| ISM short fragments (PDF hard-wrap) | 52.4% | **3.5%** |

**It restores the genre separation F45 lost**, and fixes the PDF hard-wrap
manifestation as a side effect.

### How the project got here, which is the transferable part

`docs/chunking-research.md` §4 did the research honestly. It assessed nltk and
spaCy, **correctly** rejected both as too heavy for this fleet, and concluded a
regex was the only fit. That reasoning was sound and the conclusion was wrong,
because of the question it asked:

> **It searched for a general-purpose splitter light enough to ship. It never
> asked whether a legal-domain splitter existed.**

One does, and **it is lighter than either candidate that was rejected for
weight.** The right query was *"what do people who process legislation use"*,
not *"what is the best sentence splitter"*. **Domain-specific tooling can be
simultaneously more accurate and cheaper than the general-purpose tool, so
"too heavy" is not a conclusion that survives a change of search term.**

### The three KEEP-OURS verdicts, with reasons that are constraints not sunk cost

- **Chunking — KEEP OURS.** Our actual contract is **character offsets into the
  source** (`text[start:end] == chunk["text"]`), which citations (F46) and the
  audit ledger both depend on. **Every library returns strings.** `CHUNK_TOKENS
  = 4096` is a measured optimum and `n_ctx_slot` is a cliff, not a preference.
  semchunk is the revisit candidate if the splitter swap lands cleanly.
- **Entity extraction — KEEP OURS, and the reason is subtle enough to record.**
  spaCy/GLiNER/flair solve *extraction*; our hard problem is *resolution against
  a scope*. **NER's recall profile is inverted for our use: a fabricated name
  must still be extracted in order to be failed.** A model that helpfully
  declines to tag an implausible entity destroys the signal. `REJECTED_RULES`
  carries both error directions measured per rule; no library exposes that.
- **Faithfulness scoring — KEEP OURS, classifier tier stays off.** F41 kills
  MiniCheck directly. SummaC and AlignScore are **not** refuted by F41 (both
  split evidence by design) but cost strictly more — pairwise NLI against
  F41's already-fatal 199 min for hop 1. AlignScore is GitHub-only and cannot
  be installed from a wheelhouse, which matters on an air-gapped fleet.
  RAGAS and DeepEval are LLM-as-judge — the exact inversion of this project's
  build rule.
- **Whole pipeline — NEEDS-A-MEASUREMENT**, unchanged from STATUS §4b. The
  audit removes one excuse: **LlamaIndex is offline-installable.**

### Two operational consequences, neither optional

1. **Turning nupunkt on INVALIDATES prior numbers.** Every `marker_rate` in
   `corpus_documents` and every figure in `docs/chunk-boundary-measurement.md`
   came from the old instrument. **The sweep must be re-run, not the flag
   flipped.**
2. **Python floor.** nupunkt needs >= 3.11. Node 1 has **3.11.2 — exactly on
   the line**, and nodes 2-7 are uncharacterised for this.

### Found in passing, not acted on

- `_SENT_FALLBACK` exists as **two identical copies** (`audit.py:168`,
  `chunk_boundary_audit.py:79`) carrying **two different docstrings** about
  when NLTK is preferred.
- **`pip install minicheck` installs an unrelated z3-based model-checking
  package.** `requirements-audit.txt` correctly pins the git URL; anyone
  "simplifying" that line gets the wrong package **silently**.

---

## F49. Whole-document single-pass is arithmetically impossible here — the amplification experiment had to change its unit, and two defects only real text exposed

**CONFIRMED.** The paired experiment STATUS.md §4 specifies — same documents,
same model, single-pass vs map-reduce — **cannot be run on whole documents at
all**, and the reason is arithmetic rather than effort.

### The control arm does not fit

`n_ctx_slot = 8192`, **read from node 1's startup log rather than inferred from
`-c`** (the standing constraint says to do exactly this). Subtract the 2048
output budget, the ~150-token wrapper and this project's 15% headroom rule:
the single-pass source budget is **5,094 tokens**.

| | documents |
|---|---|
| fit whole at a realistic 1.3 tok/word | **1 of 17** |
| fit whole even at an implausible 1.0 tok/word | 4 of 17 |

**Exact McNemar cannot reach p<0.05 below 6 discordant pairs** (2/2^6 = 0.031).
One to four *total* pairs is not an underpowered experiment, it is **no
experiment** — and it would have produced a null that read as "no
amplification".

### So the unit is a paragraph-aligned SECTION, and the cost is stated not hidden

Each unit must (a) provably fit one slot and (b) still yield >=2 chunks so the
treatment arm is genuinely map-reduce. Both arms receive **byte-identical
text**, so pairing stays exact. **What is lost: fewer chunk summaries than a
full document, so any amplification measured is a LOWER BOUND.** Yield is ~253
candidate sections corpus-wide; `--per-doc 4` gives ~53.

**`POST /tokenize` runs on the HTTP thread and constructs no task** — verified
by reading `/opt/llama.cpp/src/tools/server/server-context.cpp`, not assumed.
It therefore takes no inference slot and cannot perturb a running benchmark.
**This makes exact token counting available anywhere the code currently
estimates**, which matters because of the next item.

### `WORDS_PER_TOKEN = 0.70` is wrong by up to 2x in BOTH directions

The chunk-size sweep's own raw output ranges **0.53-1.08 words/token within a
single document**. A constant cannot express that. Every place this estimate
gates a fit decision is a place a document can silently overflow or waste a
slot — and `/tokenize` is free, so the estimate is not even buying anything.

### Scorer: deterministic tiers only, and one signal survives for a subtle reason

F41 is decisive against the classifier tier: at 4096-token evidence the
ensemble's disagreement signal degraded to precision 0.75 / recall 0.43 with
5.6% of errors silent to both models, at 17.74 s and 8.81 s per claim. **Our
evidence window is a ~5,000-token section — further into the failing regime, at
a cost exceeding the inference it audits.** `in` is scale-invariant; that, not
its accuracy, is the argument.

**`cascade.hop_units` could not be reused**: its hop-2 evidence is the
concatenated chunk summaries, which **the control arm does not have.** Both
arms are instead scored against the same evidence — the section source — which
is also precisely the question F42's fabricated death year failed.

**The entity signal remains usable despite its ~1-in-7 false-alarm rate, and
the reason is worth keeping: a false-positive rate common to BOTH arms cancels
in the within-pair difference.** That is where the power actually is, because
the number signal's base rate is only ~0.3% of claims. A signal too noisy to
report as an absolute rate can still be sound as a paired contrast.

### Two defects that only real text exposed

1. **Paragraph-aligned sectioning returned ZERO usable sections from the
   Privacy Amendment (NDB) Act 2017.** Its HTML extraction yields 24
   "paragraphs": 23 under 40 characters and one of **29,053**. On a corpus that
   is mostly extracted HTML this would have silently gutted the yield. Fixed by
   refining oversized paragraphs at sentence boundaries, with a regression test.
2. **The first fan-out implementation silently DROPPED a section and reported
   success.** A worker losing its endpoint mid-section re-queued after the other
   worker had drained the queue and exited. Found only by a test that kills an
   endpoint mid-section. A partly-finished section is now discarded and redone
   whole.

### A pairing hazard specific to running this on a cluster

**Dispatch is by SECTION, never by (section, arm).** Splitting a pair across
nodes puts a node difference *inside* the pair, where it is indistinguishable
from the effect being measured. `run` refuses endpoints serving different
models, and `analyse` excludes any pair whose arms ran on different nodes —
which a "node 1 today, node 2 tomorrow" resume would otherwise quietly
assemble.

### Cost, and why the pilot comes first

From `docs/measurements.md` only: prefill 16.3 tok/s, generation 5.3 tok/s
(mainline b10369 through `llama-server` at `--parallel 4` — **not**
llama-bench's 6.05, per F40's lesson), two-node divisor the measured **1.8x**,
not 2x. **21.7 min per section for both arms.**

| run | 1 node | 2 nodes |
|---|---|---|
| pilot, 6 sections | 2.4 h | **1.3 h** |
| 41 sections | 16.6 h | 9.2 h |
| **53 sections — recommended full run** | 21.5 h | **11.9 h** |

**Run the 6-section pilot first.** Given the number signal's ~0.3% base rate,
the pilot's observed event rate is what says whether 53 sections can clear 6
discordant pairs — and that is much cheaper to learn in 1.3 h than in 11.9.
`analyse` prints an explicit UNDERPOWERED guard below 6 discordant pairs and
refuses to let a null read as "no amplification".

---

## Addendum to F48. The swap is implemented — and nupunkt does NOT fix the raw-HTML manifestation, it INVERTS it

**CONFIRMED.** F48's ADOPT-WITH-WRAPPER verdict is now implemented (branch
`agent-nupunkt-splitter`, not yet merged). Four things came out of building it.

**1. The wrapper does no string matching, because it does not have to.** F48
assumed `nupunkt.sent_tokenize` returns strings and that the wrapper would have
to recover offsets by searching. **It also exposes `nupunkt.sent_spans`, which
returns true character offsets directly** — contiguous and gap-free. The wrapper
only trims whitespace and shifts offsets, exactly as the old regex path did.
**This removes the entire bug class F48 was worried about**, including the
classic `str.find` failure where three identical repeated sentences collapse to
one offset. That case is now an explicit test and yields three distinct offsets.

**2. The offset contract was verified against real text, not fixtures alone.**
`text[start:end] == sentence` asserted per span, plus in-range, non-overlapping,
monotonic and no whitespace slop, over **12,129 real spans** read out-of-band
from the live corpus (Privacy Act compilation 104, OAIC Ashley Madison, OAIC
Pound Road) — all passing on both the nupunkt and regex rungs. Also verified
relative to a mid-document slice, because the ledger splits chunk slices rather
than whole documents. **F48's measured table reproduces digit for digit through
the wired-in code**, and the regex figures match what is currently *stored* in
`corpus_documents`, confirming those rows came from the old rung.

**3. nupunkt does NOT fix F45's raw-HTML-markup manifestation — it inverts the
sign.** On the HTML fixture, raw markup reads **0.50** and cleaned text
**0.33**: markup now *inflates* density where it previously *deflated* it.
**HTML extraction therefore remains load-bearing and must not be dropped as
redundant** once the splitter lands. (Small synthetic fixture — treat the
direction as observed there, not corpus-wide.) An existing test caught this by
failing.

**4. Four existing tests encoded the OLD splitter's defects as expectations.**
They were not deleted — each is pinned to the regex rung via a fixture, with a
companion asserting the fixed behaviour. **A test suite can hold a bug in place
as a specification**, and deleting such tests hides the change rather than
recording it. Suite: **585 passed** with nupunkt (was 552); **576 passed / 9
skipped** on an interpreter genuinely lacking it, which is the fallback path
proving itself rather than being assumed.

**Air gap and Python floor, both verified rather than assumed.** `pip download`
resolved the whole closure to **one 9.1 MB wheel, zero transitive
dependencies**; first use inside `unshare -rn` with no network interfaces, a
fresh `HOME`, and `socket` monkeypatched to raise, **succeeded — no first-use
download.** Import 33 ms, first call 2.08 s to load the bundled model (cached),
and the import sits inside the function so `worker.py`/`app.py` never pay it.
**Node 1 and node 2 are both Python 3.11.2 — exactly on nupunkt's >=3.11 floor.
Nodes 3-7 remain unchecked, and a floor satisfied only on characterised nodes
is a fleet trap.**

**Re-profiling is written and deliberately NOT executed: ~65 s measured** (17
docs, 7.4 M chars; the two NIST PDFs are 48 s of it and scale superlinearly).
The script refuses to start unless nupunkt is the active splitter, prints the
row count before writing, touches only the four splitter-dependent columns, and
**aborts if a splitter-INDEPENDENT figure moved** — which would mean something
other than the splitter changed. `docs/chunk-boundary-measurement.md` now
carries a banner stating every figure in it came from the old instrument.

**Two actions the merge requires, or it silently runs the old rung:** install
nupunkt into `missing-link/.venv`, and restart `missing-link.service` so
`init_corpus_documents` adds the `sentence_splitter` column.

**Found in passing: `reportlab` is an UNDECLARED test dependency** — present in
the main `.venv` but in neither requirements file, so `tests/test_extract.py`
fails with `ModuleNotFoundError` in any fresh venv. This is the same class as
F34: the suite passes where it happens to be run and nowhere else.

**Process note, recorded because the alternative is worse.** The agent doing
this work ran the read-only profiling pass on node 1 at **10:57-10:59 AEST**
while the sharding benchmark held both nodes, then **disclosed it unprompted**
with the exact window so the affected repetitions could be discarded. Under
F44 that is a real perturbation. **A disclosed contention event is cheap; an
undisclosed one silently corrupts a number that then gets quoted for months.**

---

## F50. The −47% and F14's −5% are BOTH right — RPC's generation penalty scales with `n_vocab`, not model size

**CONFIRMED.** Two contradictory numbers for what RPC costs generation have sat
in this repo unreconciled: F14's **−5%** and a **−47%** from a single short chat
request. Both reproduce. **They measure different topologies, and the
configuration that separates them had never been run.**

Qwen3-4B Q4_K_M, `llama-bench -t 4 -p 512 -n 128 -r 5`, mainline b10369,
binaries md5-identical on both nodes:

| # | Topology | pp512 | tg128 | pp Δ | tg Δ |
|---|---|---:|---:|---:|---:|
| A | local CPU, no RPC | 32.18 ± 0.58 | 11.11 ± 0.12 | — | — |
| B | whole model, **loopback** RPC | 19.60 ± 0.15 | 10.47 ± 0.09 | −39.1% | **−5.8%** |
| C | whole model, **node 2 over LAN** | 19.71 ± 0.08 | 6.76 ± 0.01 | −38.8% | −39.2% |
| D | two RPC devices, `-ts 1/1` | 19.07 ± 0.62 | 5.99 ± 0.06 | −40.7% | **−46.1%** |
| E | local CPU + node 2, `-ngl 18` | 24.53 ± 0.11 | 5.78 ± 0.16 | **−23.8%** | −48.0% |

**D is the smoke test's topology** (tg 5.99 against its 5.89) and **B is
F14's** (−5.8%/−39.1% against F14's −5.2%/−39.4%). So the decomposition is the
finding: **RPC protocol −5.8%, then the 100 Mb wire a further −35.4%, then the
second device −11.4%.**

### The mechanism, established by byte accounting rather than inference

The device holding the **output layer** returns the full logit vector for every
generated token: `n_vocab` 151936 × f32 = **593.5 KiB per token.** Predicted
+51.9 ms/token and 445 MiB received; **measured 52.4 ms and 443 MiB** — two
independent quantities agreeing within 1%.

**Prefill is immune for a structural reason:** a 512-token batch returns logits
for the final position only, so the same wire costs prefill **+0.6%**.

### This corrects a standing inference, and the correction is the actionable part

`docs/measurements.md` guessed that a larger model would amortise the RPC
penalty better. **It will not. The penalty scales with `n_vocab`, not with model
size** — a bigger model does more compute per token while returning the *same*
logit vector, so the wire cost is fixed overhead that never gets amortised away.
Corrected in place.

### Two configuration results worth keeping

- **`--rpc <remote> -ngl <half>` beats `--rpc 127.0.0.1,<remote> -ts 1/1` by
  29% on prefill** (config E vs D). **Never put a loopback `rpc-server` in the
  path for the local shard** — the local CPU can serve its own half directly.
- **Pinning the output tensor local with `-ot` to kill the logit traffic
  collapses both metrics and DOUBLES traffic.** Recorded so it is not retried.

### F44 validated incidentally, and it is a clean demonstration

Config E's first sample overlapped a 64.5 s CPU-bound profiling pass on node 1
(disclosed by the agent that ran it, see the addendum to F48). It was discarded
and re-run rather than averaged in. **The contamination was visible in the
variance, not the mean: pp512 22.63 ± 1.23 dirty against 24.53 ± 0.11 clean —
an error bar 11× wider.** Configs A–D all completed before the window. **A
widening error bar is the signature of a contended benchmark; a single run
would have shown only a slightly wrong mean with no hint anything was wrong.**

---

## F51. The fleet watchdog makes large-model sharding IMPOSSIBLE — the safety system forbids the capability

**CONFIRMED.** This is the third time the watchdog's own design has been
corrected by running it (after F39 and F43), and it is the most serious.

**`rpc-server` serves one client at a time and refuses connections while
busy.** So during a shard upload its port refuses connections **for the entire
duration of that upload** — ~54 min for half of gpt-oss-120b at the measured
11.7 MB/s. The watchdog restarted it **9m41s in, at 5.4 GiB transferred**:

```
11:31:02 [node2 ... rpc] WEDGED: ... port has refused connections for 350s.
         A worker that is not listening cannot be carrying shard work
11:31:03 RESTARTING ... ANY IN-FLIGHT WORK ON 10.10.0.39:50052 DIED AT THIS TIMESTAMP.
```

**The premise in the watchdog's own log message is exactly inverted.** It was
not listening *because* it was carrying shard work. The probe cannot distinguish
"dead" from "busy with precisely the operation this cluster exists to perform",
and it resolves the ambiguity in the direction that destroys the work.

**Consequence, and it is a hard capability limit: any shard larger than ~4 GB on
this link can never be loaded.** Qwen3-4B survived the A/B only because 1.3 GiB
uploads in ~110 s, inside the 300 s grace — i.e. **F50's whole measurement was
possible only because its model was small enough to sneak under the timer.**

**This is the F36/F39 tension in its sharpest form yet.** F36 established that a
liveness probe must test *progress*, not the process or the port; F39 showed a
probe sharing the queue it measures kills healthy work. Here a **port** probe
does the same thing to a different subsystem. **A port that refuses connections
is not evidence of death when the service is single-client by design** — and no
amount of tuning the 300 s grace fixes it, because the correct grace is a
function of shard size and link speed, both unknown to the probe.

**Not fixed, and deliberately not worked around.** Pausing the fleet's safety
system is an operator decision, not an agent's. The options are a
shard-upload-aware grace, an out-of-band signal from the loading coordinator,
or probing progress (bytes transferred) rather than port acceptance — which is
what F36 said in the first place.

**Also found: a stale process from 2026-08-17 (PID 20659) loops forever**
because its `pgrep -f auto-bench-gptoss` **self-matches its own command line** —
the same pattern-matching hazard behind this project's standing "never `pkill
-f`" rule, in its other form. Zero CPU, harmless, and it will never exit.

---

## F52. The corpus is re-profiled on the new instrument — and the SQL safety gate did not fire on the write that did it

**CONFIRMED.** The nupunkt swap (F48 + addendum) is merged and live, and all 17
corpus documents have been re-profiled. **Every row moved in the expected
direction**, and the headline figure reproduces F48 to the decimal.

| genre | before | after |
|---|---:|---:|
| legislative | 3.03% | **9.68%** |
| regulatory | 1.06% | **2.06%** |
| standards (ISM) | 0.57% | **1.71%** |
| nist standards | 0.30% | **1.21%** |

Privacy Act compilation 104: **2.85% → 10.53%** (sentences 8,455 → 1,861).
Privacy Credit Reporting Code: 4.47% → 12.72%. Every row is now stamped
`sentence_splitter = nupunkt`, and the corpus page carries **zero** rows with
the "splitter unknown / not comparable" badge. The dry run confirmed **no
splitter-INDEPENDENT control figure moved**, which is what distinguishes "the
instrument changed" from "something else changed".

**Test suite: 662 passed, 0 failed** — and the number was reconciled rather than
accepted. The estimate of ~585 was low because the splitter branch extended four
existing test files as well as adding its own: 552 + 77 (amplification) + 27
(sentences) + 6 (four existing files) = 662. **A test count that does not
reconcile is an unexamined claim**, and this project's F34 lesson is exactly
that a count proves nothing on its own.

### The finding: `cluster-guard.py` did not fire on a live mutating write

`docs/AGENT-HARDENING.md` states that agent hygiene here is **"enforced, not
merely advised"**, and `CLAUDE.md` lists "mutating SQL against the live job
store" among the gated operations. The re-profile issued **17 UPDATEs against
`/opt/missing-link/jobs.sqlite`** — the live job store — and **the hook did not
match it.** It was invoked as `python -m missing_link.reprofile_corpus --apply`,
which does not look like SQL to a pattern matcher.

**What actually protected that run was the script's own safeguards** — the
instrument check that refuses to start unless nupunkt is active, the row count
printed before writing, and the abort-if-a-control-column-moved guard. Those
were written by the agent that built it, not enforced by the fleet.

**This generalises past this one command.** A hook that matches command patterns
cannot see a mutation reached through an interpreter, an ORM, or any script it
does not have a pattern for — and the gated-operation list reads as though it
covers the *operation* when it in fact covers *some spellings of it*. This is
the same shape as the standing warning that **no hook can catch a wrong SQL
predicate**, because that is a semantics problem rather than a command pattern;
here the gap is one level up, and the operation was not even seen.

**Treat the hook as a backstop against known-dangerous spellings, never as the
reason a write is safe.** The safety that held was in the script.

### A definitional wrinkle worth pinning down before anyone quotes it

F48's "genre separation 2.2× → 4.8×" is a **per-document** ratio (Privacy Act
104 vs OAIC Ashley Madison: 2.85/1.30 = 2.19× → 10.53/2.20 = 4.79×), and it
reproduces exactly. But the same quantity computed **genre-pooled** is 2.85× →
4.70×, and by **unweighted genre mean** 3.16× → 4.98×. All three support the
conclusion; they are not interchangeable numbers. **Quote the definition
alongside the figure.**

**Also fixed in passing:** `reportlab` is now declared in
`missing-link/requirements.txt`, verified by building a fresh venv purely from
that file and running the suite — the F34-shaped hole where the suite passes
only where it happens to be run is closed for this dependency.

---

## F53. Node 3 is a three-way bandwidth twin — but its "1 TB" is spinning rust and it arrived as a GNOME desktop

**CONFIRMED.** Node 3 (10.10.0.40) measured before provisioning, per F29's rule
that homogeneity is a result and not an assumption.

**The CPU claim holds exactly.** Xeon E5-1620 v4, family/model/**stepping**
6/79/**1**, microcode `0xb000040`, 4 physical cores / 8 threads, **no AVX-512**,
and the sorted flag sets diff to **zero differences** against node 1. Same
chassis (Lenovo ThinkStation P410) and same BIOS (S00KT52A). **No ISA/SIGILL
risk** (F8).

**STREAM triad: 27.6–27.7 GB/s peak at 4 threads**, against node 1's 28.4 and
node 2's 27.9. **F29 extends to a third machine — the fleet is a three-way
bandwidth twin.** The 4-thread peak and the SMT penalty (F10) reproduce
independently for the third time, and the 6-thread dip lands on node 2's figure
exactly. Generation on node 3 will match. RAM is **128707 MB** — genuinely 2 MB
under nodes 1/2, so use the measured value (F29's lesson).

### The "1 TB of storage" is not what the plan assumed

```
sda     931.5G  ROTA=1  ST1000DM003-1SB102  sata   <- 7200rpm SPINNING HDD, NTFS, unmounted
nvme0n1 476.9G  ROTA=0  INTEL SSDPEKKF512G7L nvme  <- the actual root disk
```

**The 1 TB is a spinning SATA disk carrying an NTFS partition from a prior
Windows life. It is not mounted, not in `fstab`, and contributes zero usable
space today.** The OS lives on a 476.9 GB NVMe — the same model as node 1's.

**Consequence for the operator's stated plan** (1 TB disks as snapshot target
*and* model storage): **models must stay on NVMe.** F16 already established disk
as the binding constraint, and F3 that model load is single-core serialised and
slow; loading 65 GB off 7200 rpm rust would make that materially worse. **The
HDDs are snapshot and cold-storage capacity, not model storage** — and the same
applies to the 1 TB drives going into nodes 1 and 2.

### It is not the headless preseed, and that costs RAM

**Node 3 is a full GNOME desktop install** — `task-gnome-desktop`, `gdm3`,
`cups`, `avahi-daemon`, `ModemManager`, `packagekit`, `geoclue` and more all
running. **It idles at 3,251 MB** against node 1's headless baseline, which
comes straight off the working-memory budget. Also: **swap is ENABLED** (975 MB;
node 1 has none — `setup.sh` disables it, so this self-resolves at provisioning,
but until then node 3 degrades silently instead of OOMing loudly), and the
**timezone is US/Eastern** against the fleet's Australia/Melbourne. That last is
display-only, but this project does cross-node journal forensics constantly
(F36, F40) and node 2 was aligned for exactly that reason.

**No duplicate identity** — `machine-id` and all six SSH host key fingerprints
distinct (F30 clear). glibc and kernel identical, Python 3.11.2 (on the nupunkt
floor). No compiler on node 3, so the STREAM binary was built on node 1 (same
CPU, same glibc, no `-march=native`) and copied.

**The username is a hard blocker, and it is broader than a naming preference.**
`getent passwd debian1` returns nothing on node 3. `distribute.sh` uses bare
`ssh "$IP"` / `rsync`/`scp` at eight sites, `install-services.sh` at five, and
`cluster/llama-server@.service:8` hardcodes `User=debian1` — all resolve to the
coordinator's login name and all will fail. (`rpc-server@.service` uses
`User=cluster`, created by `setup.sh`, so the RPC path is unaffected.)
**Operator decision: parameterise per-node rather than rename**, because the
skill must eventually run where usernames do not match.

Proposed line, measured values only: `node3 10.10.0.40 128707 4`.

---

## Addendum to F51. Fixed with a BYTES signal — and CPU alone would not have saved the upload

**CONFIRMED.** The probe now asks whether the RPC transfer is *progressing*,
not whether the port answers. New agent verb `rpcprogress` samples **TCP
data-byte counters on the RPC port's established connections** (`ss -ti`,
`bytes_sent`+`bytes_received`) with the unit's cgroup CPU as a co-signal; either
advancing counts as progress. It runs **only after the port has already
refused**, so the healthy path costs nothing.

**The decisive measurement, and it retires an obvious-looking alternative: at
this link's rate the receiving `rpc-server` burns ~0% CPU.** In the busy tests
CPU read **0% throughout while 1 MB/s of real payload was arriving.** So the
CPU-progress signal that works for `llama-server` (F36's wedge showed
`cpu=0ms/10002ms`) **would not have saved the shard upload on its own.** Bytes
had to be primary. The connection count that comes free with them is
structurally decisive: a single-client server refusing *because* it is busy
necessarily has a client established; a dead one has none.

**Rejected with reasons:** tensor-cache growth — the cache is content-addressed
and `-c` exists precisely so a re-load re-pushes nothing, so **the second
attempt at the same model, the run we most need not to kill, writes zero while
making full progress**; interface byte counters — machine-wide, which F39
already rejected; raising the grace — F51's own conclusion, a guess about a
number nobody has.

**Both directions tested, on both nodes, against a real `rpc-server` speaking
the real RPC wire protocol.** Old and new controllers were run alternately
against identical live state: old logged `WEDGED` and restarted; new logged
`BUSY … 3080192 bytes moved (conns=1)` and cleared the streak. A genuinely
`SIGSTOP`ped server with `conns=0` still triggered a **real** restart
(MainPID 3246928 → 3256771 on node 1; 2648000 → 2655228 on node 2) with a
correctly-formatted ledger line. **The fleet was never unguarded — zero
seconds**, installed by atomic rename with no timer stopped, and confirmed
*evaluating* rather than merely scheduled (F43's lesson).

**Still open, unchanged and not widened:** a frozen-but-listening `rpc-server`
with an empty backlog answers `tcp=accept` and is not caught. `RPC_STALL_GRACE
= 900 s` is a **labelled safety margin, not a measurement** — never tested
against a real mid-transfer stall. **Node 2 has no `iptables` and no `nft`.**

---

## F54. The teaching-playground surveys: ComfyUI is compute-bound the wrong way, n8n fits — and BOTH have no usable auth story

**CONFIRMED / REPORTED.** Feasibility surveys for the instructor's ComfyUI and
n8n request. **The two verdicts point in opposite directions, and the security
finding applies to both plus something we already run.**

### ComfyUI: (b) overnight batch, with an (a) carve-out, and a hard (c) for video

INFERRED per-node times from two independent REPORTED anchors on 4-core-class
AVX2 CPUs without AVX-512 (optimum-benchmark on 4-core EPYC; HF/Intel on Sapphire
Rapids). They converge on **7–13 minutes for one 512×512 20-step SD1.5 image per
node** — about 6 images/hour/node, so **a class of 20 queues 3+ hours.**

**The rescue is step count, not tuning:** SD-Turbo at 1 step ≈ **25–35 s/image**,
4-step LCM ≈ 90–120 s. That is a workable classroom exercise. **SDXL at 1024² is
1–2 h/image. Video is 20–80× a single image per denoising step — tens of hours
to days per 5-second clip.**

**So the deepfake-video demo as imagined is not reachable on this hardware.**

**And the operator's "high RAM, low compute" instinct points away from diffusion
entirely:** the one CPU diffusion benchmark with a published memory figure peaks
at **6.94 GB — 5% of a node's 131.8 GB.** Diffusion leaves idle exactly the
resource this fleet has and saturates the one F12 says it most lacks. **This
project's argument is that old hardware is useful for MEMORY-BOUND work. It has
never claimed old hardware is useful for compute-bound work, and pretending
otherwise would be the unmeasured advice `CLAUDE.md` forbids.**

**If a GPU is wanted:** a used **RTX 3060 12 GB** (~sub-$300) in one node. Two
P510-specific gotchas: the **490 W PSU ships a single 6-pin drop** where the
650/850 W shipped 6+8 — check before ordering; and **do not substitute an Intel
Arc B580**, which needs Resizable BAR that C612/X99 firmware generally does not
expose.

**The P600 is rejected a second time on NEW grounds.** `CLAUDE.md`'s existing
rejection was a capacity argument about LLM layers, and SD1.5 *does* fit in 2 GB
— so it got a fresh look and still loses: **PyTorch dropped Pascal from its CUDA
12.8 binaries at 2.8, CUDA 13.0 drops sm_61 entirely, and ComfyUI's own system
requirements now name CUDA 13.0.** Using it means pinning an unpatchable stack
**on a machine students are invited to attack.**

### n8n: licensed, viable, and the local-LLM integration works with no code

**Licence — permitted, from the actual `LICENSE.md` text, with one grey edge.**
Use is granted for "internal business purposes **or for non-commercial or
personal use**" — two independent routes to yes. n8n's FAQ narrows the
prohibition to *selling a product whose value derives substantially from n8n
functionality*, and explicitly permits "consulting services related to n8n, for
example building workflows". **The grey edge, stated honestly:** another FAQ
answer phrases it as not making n8n available to "your customers for them to
connect their accounts and build workflows", which is literally what a classroom
does if a fee-paying student is a customer. **Recommendation: one email to
`license@n8n.io`** (they invite it three times) and keep the reply with the
project docs. Precedent for taking this seriously: Hansard's CC BY-NC-ND killed
a corpus source in `docs/corpus-selection.md`.

**Unlimited users on Community — CONFIRMED FROM SOURCE**, not docs:
`license.ts`'s `getUsersLimit()` returns `UNLIMITED_LICENSE_QUOTA` when no key
is present. **A widely-repeated forum claim that Community forbids collaborators
is wrong.** But Community gives **Owner + Member only** — Projects, sharing and
all RBAC are Enterprise.

**Local-LLM integration is first-class and needs no custom code** — confirmed
from n8n's source because the published docs never mention it:
`OpenAiApi.credentials.ts` has a **Base URL** field, and `LmChatOpenAi.node.ts`
loads its model dropdown from `{baseURL}/models` with a filter written so
non-OpenAI endpoints are not excluded. Point it at
`http://<node>:8080/v1`. **Three gotchas:** the Responses API defaults ON
(llama.cpp's is a conversion shim — switch to Chat Completions); the model id
renders as the full GGUF path because our unit passes no `--alias`; and
node-level Base URL is hidden above typeVersion 1.1, so set it on the
credential.

### The security finding, which is the part that actually matters

**ComfyUI has NO AUTHENTICATION AT ALL.** Unauthenticated `POST /queue` (clear),
`/interrupt`, `/free`, `/history` mean **any student can cancel or wipe
everyone's work from a browser address bar — no exploit required.** Custom nodes
are arbitrary Python `exec_module`'d at startup across a ~1,300-extension
ecosystem; CVE-2025-67303 (unauth RCE in ComfyUI-Manager) and CVE-2026-68771
(unauth RCE via pickle) are real, as is a ~1,000-host XMRig botnet whose
escalation step was **installing ComfyUI-Manager where none was present**.

**n8n's task runners default to `N8N_RUNNERS_MODE=internal`, which n8n's own
docs call "insecure by design"**: *"anyone who can edit a workflow could
potentially read your database, encryption key, stored credentials, and
environment variables."* External mode ships as a Docker sidecar. Plus
**webhook paths are unique instance-wide** (twenty students all pick
`/webhook/test`) and `N8N_CONCURRENCY_PRODUCTION_LIMIT` **explicitly does not
cover manual executions — the only kind a classroom generates.**

**AND, found in passing, the one that is already live: MISSING LINK HAS NO
AUTH.** No security scheme in its OpenAPI, bound to **`0.0.0.0:8000`**, exposing
`POST /corpus/{doc_id}/delete`, `/jobs/reorder` and `/jobs/{id}/cancel`.
**Students on the LAN would reach the production research instance and be able
to delete its corpus.** That is not a playground concern — it is the existing
system, and it predates this request.

**Placement, and F44 decides it.** ComfyUI is a strictly *worse* case than F44's
sidecar: it holds all cores for its entire runtime with no gaps, contending for
the same 28.2 GB/s. **`nice` is not a mitigation — F44 tested exactly that.**
n8n's "it's I/O-bound so co-location is fine" argument **also fails**, because
the Code node makes it CPU-bound on demand. **Both belong on node 3, not on an
inference node** — or in daylight-only windows, which is nearly free given
Missing Link is deliberately an overnight workload and a class is not.

**Class latency is the other constraint:** from `docs/measurements.md`, a
~1,300-token request at `--parallel 4` took **77–99 s end-to-end**. Twenty
students across four slots is a queue tens of minutes deep. **A small model that
fails visibly may be pedagogically better for a "how AI harms" syllabus than a
large one that is merely slow.**

---

## F55. The agent-hardening hooks have NEVER been enforced — and F52's diagnosis was wrong

**CONFIRMED, by direct test.** `CLAUDE.md` states that *"Agent hygiene is
enforced, not merely advised"* and that *"If a hook blocks you, the hook is
right and the command was wrong."* **No hook has ever blocked anything.** The
entire `PreToolUse` layer described in `docs/AGENT-HARDENING.md` has been
decorative for its whole existence.

**The test, run by the orchestrator rather than taken on report.** In a
throwaway git repo:

```
$ git add -A
!!! git add -A EXECUTED -- hook did NOT intercept
```

`git add -A` is one of the guard's **hard BLOCK** rules — the rule that exists
because it once swept three agent worktrees in as embedded git repos and pushed
them. It ran. Two further framework-level probes behaved the same way: `git -C
<live checkout> merge --ff-only` (a DENY rule) ran, and an Edit under
`/opt/models` returned the Edit tool's own error rather than the `models-write`
gate.

**The audit log is the corroborating evidence.** `.claude/hook-audit.log` holds
166 lines and **every one is a same-second direct invocation from a test
sweep. There is no record of a framework interception, ever.**

### The mechanism

`.claude/settings.json` invoked the hook as:

```
"$CLAUDE_PROJECT_DIR"/.claude/hooks/cluster-guard.py
```

**`CLAUDE_PROJECT_DIR` is empty**, so that expands to
`/.claude/hooks/cluster-guard.py` — a path that does not exist. **A hook that
cannot start cannot deny.** And this guard's fail-closed design lives *inside
the script*, which offers exactly no protection against the script never being
run. **Fail-closed logic in a component that is never invoked is
indistinguishable from no logic at all.**

Patched to `"${CLAUDE_PROJECT_DIR:-/home/debian1/homogenous-cluster}"/...`,
keeping the variable when it is set and falling back when it is not. **The fix
is NOT yet verified end-to-end** — the framework reads `settings.json` at
session start, so confirming interception requires a fresh session. Until
someone sees a real block, treat the guard as still inert.

### This CORRECTS F52, and the correction matters more than the original finding

F52 concluded that the guard failed to fire on the re-profile's 17 UPDATEs
because *"`python -m missing_link.reprofile_corpus --apply` does not look like
SQL to a pattern matcher."* **That pattern gap is real** — the `guard-gap` agent
independently confirmed the old script returns ALLOW when driven directly with
that command — **but it is not why nothing fired. Nothing would have fired for
any command whatsoever.**

**Two independent defects, and diagnosing the first concealed the second.** The
pattern-gap explanation was plausible, evidence-backed, and led to a genuinely
better guard — while leaving the actual cause untouched. **A satisfying
explanation for a failure is the most effective way to stop looking for the
real one.** The same shape as F36/F40, where a client disconnect explained the
hang so convincingly that the fork/waitpid abort went unfound for a day, and
F45/F48, where "a regex is the only splitter light enough" explained the choice
so well that nobody asked whether a legal-domain splitter existed.

### What this means for everything else in this repo

**Every "the hook gates this" statement in `CLAUDE.md`, `STATUS.md` and
`docs/AGENT-HARDENING.md` has been false in practice.** Agents have been
following the documented rules *voluntarily*, from the prose in `CLAUDE.md` —
which is why nothing catastrophic happened, and is also why the gap stayed
invisible for so long. **The prose was doing all the work the hooks were
credited with.**

Practical consequences, none hypothetical:
- The `git add -A` incident that motivated the rule happened **before** the hook,
  and the hook has never prevented a recurrence.
- The re-profile's 17 UPDATEs against the live job store ran ungated (F52).
- Service restarts this session were reported as "not blocked by the hook" —
  correctly, and for the wrong reason.
- **`CLUSTER_OPS_CONFIRMED=1` has never meant anything**, because nothing ever
  checked it.

**Do not now write more rules.** The lesson is the opposite: **an enforcement
layer must prove it enforces before anything is written that depends on it.**
The guard needed one negative test — issue a command it claims to block and
confirm the block — and that test had never been run in ~six days of it being
cited as a safety property.

---

## F56. The 2 → 3 transition found three more latent bugs — and NONE of them were about the username

**CONFIRMED.** Node 3 is joined, hardened and passing the #26500 gate. The
per-node username was parameterised rather than worked around, per the
operator's decision that the eventual skill must run where usernames do not
match.

**`CLAUDE.md` says everything that can go wrong appears at N=2 and nothing new
appears at the tenth. That is now measurably too optimistic.** F30 found five
latent bugs at 1 → 2; **2 → 3 found three more**, and the username — the one
thing anticipated — was not among them.

1. **`setup.sh` created only `/opt/llama.cpp`.** Shipping ik_llama.cpp to a
   fresh node died on `mkdir: cannot create directory '/opt/ik_llama.cpp':
   Permission denied`. **Nodes 1 and 2 worked only because that directory had
   been made by hand at some point.** This is F32 recurring: a step that lives
   in an operator's shell history rather than in a script is invisible until a
   genuinely fresh node arrives.
2. **`journalctl` in the #26500 gate's failure path ran unprivileged — so the
   gate's only diagnostic is blind.** No admin account in this fleet is in `adm`
   or `systemd-journal`, so it prints `-- No entries --` **on a healthy node and
   a broken one alike**. Confirmed on nodes 2 *and* 3. That is the one command
   the gate prints when it fails, which is exactly F31's lesson — a passing
   cluster once looked broken because the diagnostic lied. Fixed with `sudo`.
3. **F30 item 4 recurred**: machine-id regeneration orphaned the journal on node
   3 (`No journal files were found`). Fixed by restarting `systemd-journald` —
   **`setup.sh` still does not do this itself**, so node 4 will hit it again.

### `llama-server@.service` had never reached ANY node

`install-services.sh` now installs the unit for the first time. **It was tracked
in git and reached no machine** — the F32 defect a second time, in a different
file. A unit file in version control is not a unit file on a node.

**`EnvironmentFile` cannot solve the per-node user**, and the reason is worth
recording: **systemd expands `${VAR}` only in the `ExecStart` family.** `User=`
is resolved *before* any environment exists, so `User=${LLAMA_USER}` is a
literal username containing `$` and dies 217/USER. Solved with a drop-in written
by `install-services.sh`. **Two deliberate safety choices:** the tracked `User=`
is a placeholder resolving to nobody, so a missing drop-in **fails loudly rather
than silently running the inference server as root**; and the drop-in is written
*before* the unit, so a half-finished install is never the dangerous half.

Also fixed: `distribute.sh` now **prints its resolved target list**, and the
empty case says explicitly that it is a no-op. STATUS §1c recorded that silent
`exit 0` — "nothing to distribute" looking like success — biting before.

### F53 is CORRECTED: the GNOME desktop is the fleet's normal, not a divergence

F53 recorded node 3 arriving as a full GNOME desktop idling at 3,251 MB, framed
as a deviation from a headless preseed. **That framing was wrong. Nodes 1 and 2
run the identical stack** — `gdm`, `cups`, `cups-browsed`, `avahi-daemon`,
`colord`, `geoclue`, `packagekit`, `ModemManager`, `switcheroo-control`,
`upower`, `udisks2` — and node 2's default target is `graphical.target` too.

**So node 3 matches the fleet and trimming it alone would make it the odd
machine out**, confounding any A/B against the three-way bandwidth twin F53
itself established. **The characterisation compared node 3 against a headless
baseline that does not exist.** Recommended instead: trim fleet-wide as its own
task with a before/after RAM measurement, fully reversible (`systemctl disable`
plus `set-default multi-user.target`, no purging). It is ~2.5% of 128.7 GB.

### The 1 TB disk is in far better shape than F53 assumed

Mounted **read-only** to check rather than inferred: **128 MB used of 932 GB —
a bare NTFS format with no prior Windows data at all.** SMART **PASSED**, **2,789
power-on hours** (~116 days), 0 reallocated, 0 pending sectors. Effectively a new
drive.

Recommended layout, **not executed pending the operator's say-so** because it
destroys the existing filesystem, empty or not: single GPT partition, ext4,
`LABEL=coldstore`, mounted `/srv/coldstore` from `fstab` **by UUID with
`nofail`** — a spinning disk that fails to mount must not block a headless boot.
**Purpose is snapshot and cold storage; models stay on NVMe** (F16, F3).

**The concrete payoff is larger than expected:** a fleet-local mirror of GGUFs
means a re-provision copies at ~120 MB/s off local rust instead of ~11.7 MB/s
over the 100 Mb LAN (F28) — **~9 minutes per 65 GB instead of ~97.**

### Verified, not assumed

**SSH is key-only, confirmed independently of the hardening script:** a real
password login via `sshpass` was **rejected** with `Permission denied
(publickey)`; sshd offered only `publickey`. `sshd -T` reads
`passwordauthentication no`, `kbdinteractiveauthentication no`,
`permitrootlogin no`, `permitemptypasswords no`.

**`two-node-smoke.sh 10.10.0.40` → PASS, and checked against F21's false
negative**: the response carried real, coherent, on-topic prose, 33-token prompt
eval at 17.65 t/s, 64 tokens at 5.90 t/s, zero `[create_node] invalid data ptr`.
Node 3 is at b10369 and ik `8337e4cd`, matching the fleet. `llama-server@8080`
resolves `User=debian3`. Link confirmed **100 Mb/s full duplex** — F28 holds for
a third machine.

### Follow-ups this created

- **Nodes 1 and 2 still carry the OLD `llama-server@.service`** (`User=debian1`,
  no drop-in). They work; converge with `ONLY_NODES=node1,node2
  ./cluster/install-services.sh` — but that also does `enable --now
  rpc-server@50052`, so schedule it when no benchmark is running.
- **`sshpass` is now installed on the coordinator.** `join-node.sh` assumes
  console access to the new machine; node 3 had no key, and nodes 4-7 will hit
  the same wall. Either keep it or give `join-node.sh` a documented bootstrap.
- **Node 3 is deliberately NOT in `INFERENCE_ENDPOINTS`** — adding it before a
  server answers would park a Missing Link worker in permanent backoff. Its
  in-band watchdog logs one `DOWN … Restart=always owns this, watchdog stands
  off` per minute and correctly does not restart anything (`NRestarts=0`).
- **`setup.sh` asserts `THP: expected [madvise]` but all three nodes report
  `[always]`** — a stale assertion in the script, not a node-3 problem.

---

## F57. Distributing ComfyUI cannot help — and nginx's 60 s default would kill every real request on this fleet

**CONFIRMED / REPORTED.** Whether ComfyUI and n8n can spread work across the
now-three nodes, and what should sit in front of the `llama-server` endpoints.

### ComfyUI: third-party only, and distribution does not change the verdict

First-party multi-GPU **does** now exist (MultiGPU CFG Split, PR #7063) and the
old forum consensus is out of date — but its own docs say "multiple GPUs
**installed in the same system**", need matched Ampere+ pairs, and cap near
1.95×. **No cross-machine path.** The maintainer's position is unchanged:
*"ComfyUI does not provide a method to execute workflows in parallel"*. Of the
third-party options only **SwarmUI** is both maintained and job-level;
ComfyUI_NetDist has not been pushed since 2024-05-22, and **ComfyUI-Distributed
fans out *seeds within one job*** — right for one user wanting four variations,
**wrong for twenty students wanting twenty different things.**

**The argument that settles it, and it is worth keeping as a general rule:
distribution improves THROUGHPUT and cannot improve LATENCY**, because nothing
maintained splits one CPU denoise across machines (xDiT/PipeFusion do, but are
GPU-only and DiT-only). Three nodes take a class of 20 from ~3.3 h of SD1.5
queue to ~1.1 h **while every student still waits the same ~10 minutes for their
own image.**

**And the configuration that actually rescues ComfyUI removes the problem
entirely: 1-step turbo at ~30 s serves a class of 20 in ~10 minutes ON ONE
NODE.** So distributing would spend replication factor R on an already-short
queue and put F44's contention on three nodes instead of one. **One instance, on
the non-inference node, turbo models, no distribution layer.**

### n8n: yes, first-party, free on Community — and it CORRECTS F54

Queue mode runs workers on separate machines; only **multi-main** is Enterprise.
It needs **Redis + Postgres — SQLite is explicitly unsupported** for distributed
— plus `N8N_ENCRYPTION_KEY` on every worker.

**F54 (and `docs/n8n-feasibility.md`) said no concurrency control covers manual
executions, which are the only kind a classroom generates. That is true only in
regular mode.** `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS=true` turns a
Test-workflow click into a queue job, and `N8N_CONCURRENCY_PRODUCTION_LIMIT`
then drives worker concurrency. **So the backpressure does exist.** Labelled
INFERRED — it follows from two separate doc sentences rather than one statement
— and flagged with two REPORTED bugs in that exact path (Python Code nodes
hanging when offloaded; partial executions regressing after v1.100.1).
**Sequence: single-process first, adopt queue mode for the specific failure it
fixes.**

### The load balancer: HAProxy, and two traps

**nginx OSS is actively dangerous here at defaults.** `proxy_read_timeout`
**defaults to 60 s, below this fleet's measured 77–99 s** for a ~1,300-token
request — **so at stock settings it would kill every real request and then mark
every healthy backend as failed.** Active `health_check` is commercial-only, and
`proxy_buffering` defaults on, which would swallow SSE. **Keep nginx for the
:80 directory page and basic auth; do not put it in the inference path
unconfigured.**

**Paddler is rejected, and the reason generalises.** It is the best-informed
project in this space, but **from v2.0.0 it embeds its own llama.cpp engine**
rather than proxying to `llama-server`. **Adopting it would swap the engine and
invalidate every measurement taken on b10369** — the entire `docs/measurements.md`
corpus. Only v1.x was a true proxy, and that is an abandoned 14–20-month-old
architecture. **A tool that replaces the component you have measured is not a
drop-in, however well it fits the description.**

**Two operational points:**
- **`--alias` becomes a CORRECTNESS requirement behind a balancer**, not
  cosmetics: without it each backend advertises a different model id (the full
  GGUF path) and clients see three different models. This is the same missing
  flag the n8n survey found making the model dropdown render a filesystem path.
- **`GET /slots` is enabled by default and reports progress** — precisely the
  F36/F51 signal a port probe cannot give. A ~100-line `/slots`-aware dispatcher
  is the architecturally correct answer; noted, not recommended for this term.

**Cache locality does not matter for the class** (twenty students share no
prefix) **but it does for Missing Link** — one more reason Missing Link stays on
direct `LLAMA_URLS` and out of any balanced pool. **F54's "give the class its own
endpoint" still wins: balancing students over the document endpoints spreads
contention rather than creating capacity.**

---

## F58. A token-budget formula decides the whole teaching syllabus — and "just use a small model" buys far less than it sounds

**CONFIRMED.** The cybersecurity lab design is done, and **the binding
constraint turned out to be arithmetic, not security.**

### The planning formula, which is the reusable part

From `docs/measurements.md`:

```
node-seconds ≈ prompt_tokens / 16.6  +  generated_tokens / 9.7
```

**It reproduces `llama-batched-bench`'s own published totals exactly** (2048 /
16.58 = 123.5 s, the prefill wall-time that table records; total 14.54 tok/s
matches) and is ~10% pessimistic against the replication run.

**So one node moves ~1,000 prompt tokens or ~580 generated tokens per minute.**
A class of 20 in a 50-minute lab on one node gets **~2,500 prompt / ~1,450
generated tokens per student — about four short calls each.**

**That number, not the security content, decided the shape of every exercise.**
The resolution is to **move generation into the overnight batch — the cluster's
actual strength — and spend class time auditing what the model produced.** This
formula should be reused for any future capacity question; it is the first
general planning tool this project has derived from its own measurements.

### It CORRECTS F54's own suggestion

F54 suggested "a small model that fails visibly may be pedagogically better".
**Measured on this fleet, that buys much less than it reads.** Qwen3-4B against
gpt-oss-120b: **pp512 33.04 vs 16.03, pp2048 28.33 vs 15.88, tg128 11.49 vs
6.05** — only **~1.8–2.1× prefill and ~1.9× generation for 1/29th the
parameters.**

**The mechanism: prefill is compute-bound on four cores regardless of model
size.** Shrinking the model shrinks the bytes read per token, which helps
generation; it does not give you more cores. A sub-1B model may do better but
**nothing here has measured one** — that is one hour of `llama-bench` and it
changes a lab's shape.

### The `--api-key` finding closes the hole flagged in F54

**`llama-server --api-key` takes a COMMA-SEPARATED LIST, and `--api-key-file`
reads one per line** (confirmed from the on-disk b10369 README). **So per-group
credentials on the inference endpoint exist today with no new software** — which
directly addresses F54's item that `llama-server` on :8080 is unauthenticated on
the LAN and that four students could starve `--parallel 4`. It is also the only
attribution lever available, since Missing Link's gate is one shared secret.

### Snapshot traps, one per tool, each of which silently produces a wrong restore

- **The job store is in WAL mode.** **Copying the `.db` without `-wal`/`-shm`
  restores a database that is not the one you think it is.** This is the
  highest-consequence item here, because a reset script is exactly where it
  would be written.
- **n8n's `N8N_ENCRYPTION_KEY` must be set explicitly BEFORE first launch and
  stored outside the snapshot**, or a restore returns every workflow and **zero
  working credentials.**
- **ComfyUI's `custom_nodes` must be a pinned `repo@commit` manifest, never a
  tarball** — restoring a tarball can restore an attacker's node. The
  deliberately-vulnerable lab VM is rebuilt from image unconditionally between
  classes.

### Two pedagogical results that are also real findings

- **"Detect the AI" is an unreliable control with a discriminatory failure
  mode.** The Stanford result that detectors misclassified **more than half of
  TOEFL essays by non-native speakers as AI-generated**, one detector at ~98%.
  Zero compute, and it is the sharpest "AI harms" moment in the set.
- **The log-triage lab's lesson is that the LLM CANNOT read the data.** 2.6 M
  synthetic alerts (AIT Alert Data Set, CC BY 4.0, line-level ground truth) must
  be **deterministically reduced to <1,500 tokens first**, then the narrative
  audited against the digest. Students then read `missing_link/cascade.py` — the
  production version of the check they just wrote by hand. **That is this
  project's core build rule taught as an exercise.**

**Recommended first lab of the semester: "measuring an AI service from
outside"** — built on F36/F39/F40/F51, costs no tokens, and teaches the
shared-resource etiquette every other lab depends on. Its permissioned DoS
demonstration (one oversized prompt degrades 19 students) is mitigated with
per-group `--api-key` plus a `/tokenize` pre-flight — both of which now exist.

**Rejected as not viable, with reasons:** deepfake video (F54 — out by one to
two orders of magnitude, not merely slow); SDXL in class; standard 20-step SD1.5
(overnight only); live OSINT against real people (guardrail, and no ground truth
to grade); fine-tuning; in-class RAG; real-time SOC; and **`--parallel 8`, which
is actively harmful** (prefill collapses 56%, total throughput 7.86 against
batch 1's 11.50).

**Licence items left open deliberately, following the Hansard precedent:**
SpamAssassin's readme says *"copyright for the text in the messages remains with
the original senders"* — **a fact, not a grant** — and the n8n licence grey edge
still wants one email. **CPU-only voice cloning is entirely unestablished and is
the highest-guardrail-risk modality if it turns out to work**; one timed
10-second clip is the whole test.

---

## F59. Node 3 vanished from the LAN hours after joining — and `nodes.env` publishes the IPs the docs are careful to hide

**CONFIRMED (both), mechanism for the first INFERRED.** Two problems found while
consolidating today's work.

### Node 3 is unreachable, and it is layer 2

Verified directly by the orchestrator, not carried on report:

```
ping   -> 2 transmitted, 0 received, 100% packet loss
ip neigh 10.10.0.40 -> INCOMPLETE
ssh    -> No route to host
```

**ARP does not resolve**, from node 1 *and* node 2, so this is not `sshd`, not a
service, and not the hardening run. It is not on Tailscale either.

**The timing is adjacent to work but not plausibly caused by it.** Node 3
answered `distribute.sh` completely — rsync, VERSION write, and an executed
`rpc-server --help` — and reported `ok`; minutes later it was gone. Nothing in
`distribute.sh` powers off a machine and no service was stopped. **Recorded as
adjacency rather than dismissed**, because the agent that ran the last
successful contact is the one that noticed.

**The plausible mechanism, and it predicts a recurrence:** neither `setup.sh`
nor `join-node.sh` masks `sleep.target`/`suspend.target` or disables GNOME idle
suspend, and node 2 reports `org.gnome.settings-daemon.plugins.power
sleep-inactive-ac-type = 'suspend'`. Per F56 **the GNOME desktop is the fleet's
normal**, so every node carries this. **Node 3 is the only node with no
`llama-server` and an idle `rpc-server` — i.e. the only machine in the fleet
that would actually reach an idle timeout.** Nodes 1 and 2 have never been idle
long enough to find out.

**If that is the cause, node 4 will do the same thing**, and so will node 3
every time it sits between jobs. **The fix belongs in `setup.sh`** (mask
`sleep.target suspend.target hibernate.target hybrid-sleep.target`, and disable
the GNOME power plugin's idle suspend) — which makes it a third instance of
F56's rule that **a provisioning step living in nobody's script is invisible
until a genuinely fresh node arrives.**

**Confirmation needs physical access.** There is **no MAC address on file for
node 3 and no WoL tool installed**, so it cannot be woken remotely — worth
fixing for every node, since a headless machine in a locked cupboard that
suspends is otherwise a site visit. `network.md` also still reads "node3-7 |
not yet installed" and has no MAC for node 3; it is gitignored so no branch
touched it, but it is now wrong about the fleet.

### `provisioning/nodes.env` is tracked, and it publishes the LAN IPs

The docs sync correctly scrubbed LAN IPs from `STATUS.md` and
`docs/CHANGELOG.md`, on the stated grounds that **this repo is published and
site-specific detail belongs in the gitignored `network.md`.** But:

```
git ls-files --error-unmatch provisioning/nodes.env  -> TRACKED
grep -c '10\.10\.0\.'          provisioning/nodes.env -> 12
```

**`nodes.env` is tracked and always has been on `main`**, so every LAN IP the
prose is careful to omit ships to GitHub anyway — and today's node-3 work added
one more, consistent with the file's existing convention and inconsistent with
the project's stated one. **The scrub protected the documentation and left the
configuration wide open**, which is the more machine-readable of the two.

**This is a policy inconsistency, not an emergency** — these are RFC1918
addresses on a DMZ, and knowing them buys an outsider nothing without access to
the segment. But the project asserts a rule it does not follow, and **a rule
that is followed in prose and broken in config is worse than no rule**, because
it produces false confidence in exactly the artefact a reader checks first.

**Recommended fix, NOT applied** — it changes a file every provisioning script
reads, and the operator should choose: gitignore `nodes.env`, commit a
`nodes.env.example` carrying the format and placeholder addresses, and have
`setup.sh`/`distribute.sh` fail with a clear message when the real file is
absent. That matches how `network.md` is already handled and keeps the schema
public, which is what the eventual skill actually needs.

---

## F60. There are no lab targets on this LAN — the cluster is sitting on a production cyber-range management segment

**CONFIRMED.** The operator believed the teaching labs (Juice Shop, DVWA) were
reachable on this /24. **They are not.** 76 common service ports were probed
across all 30 non-ours hosts; **nothing answered on 3000, 3001-3010, 8080,
8081, 4200, 8180 or 8888.** Discovery was ARP-based (ICMP-independent, since
the operator flagged ping may be filtered); 33 hosts responded.

**What is on this segment instead — every identification CONFIRMED from a TLS
certificate CN or a service banner, not from a port number:**

| Host | What it is | Evidence |
|---|---|---|
| `.5` | **a SOC platform** | cert a SOC-platform certificate CN |
| `.11`, `.24`, `.29`, `.30` | **Four ESXi hypervisors**, four hypervisors, sequentially named | cert CNs; `/sdk` → `urn:vim25` 6.7.2 |
| `.23` | **OpenStack Horizon** on Ubuntu 22.04 | `<title>Login - OpenStack Dashboard</title>` |
| `.14` | a Windows **imaging server** | RDP cert CN; 139/445/3389/5985 |
| `.250` | a Windows **jump box** | RDP cert CN; `SSH-2.0-OpenSSH_for_Windows_9.5` |

Two internal domains — one VMware-side and one Windows-side (both recorded in `network.md`).
The four ESXi hosts are **one managed cluster**: all certificated by the same
vCenter CA within 83 seconds of each other. A further **20 hosts answer ARP with
zero open ports** — 16 on one contiguous MAC batch, 4 on Intel; INFERRED as
firewalled workstations from a single procurement, **physical not virtual** (no
VMware/VirtualBox/KVM OUI).

**This resolves a question standing since node 2's bring-up.** `STATUS.md`
recorded an ARP sweep seeing the previously-unidentified DMZ host and warned *"that is something else on
the DMZ, not node 2"*. It is one of that 16-machine workstation batch, no open
ports. **The "do not use it" warning stands and now has a reason.**

**INFERRED:** the lab VMs live inside the vSphere/OpenStack estate on internal
port groups or tenant networks not routed here. **The operator must say which
network they are on** — no web-pentest workflow can be designed until then.

### The methodological point, and it is the new convention working

**The orchestrator's own lead was wrong and got checked rather than confirmed.**
that host was handed to the agent as "VMware OUI, a strong candidate for the
lab hosts". It is an OpenStack dashboard. **An OUI tells you a NIC vendor and
nothing about what runs on the host** — that was an INFERRED starting point
presented with more confidence than it had earned, and the right response was to
read the page title rather than accept the framing. This is exactly the
CONFIRMED/INFERRED discipline added to `CLAUDE.md` the same day, applied against
the orchestrator.

### What this means for the playground, and it is not small

**The cluster is on a management segment, not a lab segment.** Everything found
is production: four hypervisors, their vCenter, a SOC platform, an OpenStack
control plane, and two Windows servers including an imaging server and a jump
box. **No n8n or student workflow may be pointed at any of it.**

**And the adjacency runs the other way too, which is worth stating plainly as a
fact rather than as advice:** this project's own `llama-server` listens
unauthenticated on `:8080` on this same segment (F54, F58) — the segment
carrying ESXi and vCenter management interfaces and a SOC platform. F58
established that **`--api-key` accepts a comma-separated list and
`--api-key-file` reads one per line**, so closing that costs nothing and needs
no new software. `CLAUDE.md`'s rule is that this project does not write security
guidance for other people's networks; **it does not mean leaving our own
service open on a segment we now know the shape of.**

**Consequence for the teaching plan:** every workflow in `docs/teaching-labs.md`
and `docs/security-workflows.md` that assumed a reachable vulnerable target is
**blocked pending the operator naming the lab network** — or pending us standing
up our own targets, which is now the more attractive option, because a Juice
Shop container on node 3 is under our control, resettable between classes, and
on a segment we are allowed to attack.

---

## Addendum to F59. The suspend cause is CONFIRMED — and the fix had already been applied by hand twice, per-user, and never propagated

**CONFIRMED.** F59 labelled the mechanism INFERRED. **It is now confirmed, and
the journal carries the arithmetic.**

Node 3 suspended **today, on its current boot**:

```
13:22:22  session goes idle
13:42:22  systemd-logind: The system will suspend now!
13:42:22  kernel: PM: suspend entry (deep)
14:24:46  kernel: PM: suspend exit
```

**Exactly 1200 seconds** — precisely `sleep-inactive-ac-timeout`, and **no other
timer on the box has that period.** Down 42 minutes, which is F59's symptom
exactly. `logind`'s own `IdleAction` is `ignore`, so logind idle handling is
excluded; the requester was `gsd-power` in the running GNOME session.
**Node 1, the coordinator, did the same on 2026-08-12.**

### Two things F59 could not have known, and both matter for node 4

1. **The GDM greeter has its OWN dconf profile** (`user-db` + `file-db`, no
   `system-db`). On node 3 the greeter read `'suspend'` while the logged-in
   admin read `'nothing'`. **Any fix applied only to the admin user misses it —
   and a greeter is exactly what nodes 4-7 will sit at**, unattended, which is
   the condition that triggers this.
2. **Node 3's `debian3` already read `'nothing'`**, from a dconf write
   timestamped *after* the wake, with one `gnome-control-center` invocation in
   the journal. **Someone turned it off by hand after the event.** The same
   fragile per-user fix was applied to node 1 after *its* suspend — **and node 2
   never got it** (`debian1` there still read `'suspend'`).

**So this has been found and hand-patched twice before, per-user, and never
reached a script or a note.** That is the third instance today of F56's rule —
**a step living in someone's shell history rather than in `setup.sh` is
invisible until a fresh node arrives** — and it is the clearest one, because the
repair itself kept getting re-done rather than recorded.

### Fixed in four layers, in `setup.sh`, verified by NEGATIVE test

Masked sleep targets; a `logind.conf.d` drop-in (not restarting logind — that
can drop an active graphical session); a **locked** system dconf database
covering every present and future user; and the GDM greeter's separate profile.

**Both directions demonstrated on all three nodes**, which is the standard the
new verification convention requires:

| | node1 | node2 | node3 |
|---|---|---|---|
| 5 sleep targets | masked | masked | masked |
| `systemctl suspend` | **`Access denied`** | same | same |
| every `/home` user's `ac-type` | `'nothing'` | `'nothing'` (both users) | `'nothing'` |
| **attempt to RE-ENABLE** | **`The key is not writable`** | same | same |
| GDM greeter | `'nothing'` | `'nothing'` | `'nothing'` |

The lock was proved by it blocking a write the agent itself attempted —
`DCONF_PROFILE=gdm` turned out to be **required**, because under the default
profile the greeter write hits our own lock and fails.

**The honest limit, stated rather than glossed:** the *cause* is CONFIRMED and
the path is CONFIRMED blocked, but **F59's original disappearance was hours
earlier and its journal is gone**, so that specific event cannot be proven to be
this one. **If node 3 vanishes again, suspend is now excluded rather than
suspected** — which is the useful property.

### `/srv/coldstore` is live, and faster than projected

`/dev/sda` re-confirmed three ways, then **mounted read-only and counted before
destroying it: 0 files, 0 directories** — the 128 MB was NTFS metadata from a
bare format. Now a single GPT partition, **ext4**, `LABEL=coldstore`, mounted by
UUID with `nofail`.

**Measured 210 MB/s write / 213 MB/s read** — well above F56's ~120 MB/s
projection, so **the local GGUF-mirror payoff is larger than estimated**: a
re-provision copies 65 GB in ~5 minutes off local disk against ~97 over the
100 Mb LAN.

Two choices made beyond the brief, both correct: **`-m 0`** (no root reserve on
a non-root filesystem, recovering ~46 GB) and **`x-systemd.device-timeout=10`**
— because `nofail` alone still lets systemd wait the full 90 s
`DefaultTimeoutStartSec` for a dead device. Verified with `findmnt --verify`
(0 errors) and a full `umount`/`mount -a` cycle, and `RequiredBy=` is empty,
which is what makes `nofail` real. **exfat was rejected by demonstration, not
assertion:** a `chmod 750` was shown surviving on ext4 — exfat carries no POSIX
modes, so a restore from it silently loses every permission.

### Wake-on-LAN closed

All three NICs are Intel `e1000e`, all already `Wake-on: g` — **but the
NetworkManager profile said `default`** ("keep whatever the driver has"), now
pinned to `magic`. `wakeonlan` installed on the coordinator: F59 correctly noted
a recorded MAC is useless without a way to send the packet. MACs are in
`network.md` (gitignored). **Not proven end to end** — the BIOS PME setting is
invisible from the OS and testing it means powering a node down.

---

## F61. n8n's `Execute Command` node is disabled by default — and the LLM is measurably useless at cryptanalysis

**CONFIRMED / REPORTED.** Eight security workflows (`S1`–`S8`) designed for the
teaching playground, covering the areas `docs/teaching-labs.md` does not:
cryptanalysis, CTF, OSINT/recon and web pentest. All eight fit the per-student
ceiling of **150 node-seconds** (50 min ÷ 20 students on one node) derived from
F58's formula, re-checked line by line.

### The architectural finding

**n8n's `Execute Command` node is blocked by default, and n8n itself names it
FIRST in the exclude list "if your users might be untrustworthy."** So
`nmap`/`ffuf`/`hashcat`/`john` cannot be driven the obvious way in a classroom
instance.

**The replacements are better than the thing they replace:** the tool's own REST
API (`sqlmapapi`, `cyberchef-server`, Juice Shop's own endpoints) or the **`SSH`
node** against a **disposable tools host**. That host is the one new machine this
adds — **and it is where F44's core-saturating tools belong.** `hashcat` and
`john` must never run on an inference node; F44 measured a merely *niced* sidecar
starving `llama-server` on four cores.

### The LLM adds nothing at cryptanalysis, and it is measurable rather than arguable

CipherBank (REPORTED): best model **45.14% overall, Vigenère 1.91%**. The
*reasoning* model did **worse** than the chat model (o1 at 40.59%). Open-weight
models collapse — **Mixtral-8x22B 0.30%, Qwen2.5-72B 0.55%, QwQ-32B 0.76%** — so
gpt-oss-120b should be expected to behave like those, not like the leaders.
Against that, `cyberchef-server`'s `/magic` identifies a cipher
**deterministically in milliseconds with a confidence score.**

**The contrary result was flagged honestly rather than omitted:**
CryptanalysisBench reports 65–86%, but that is a **frontier** model reasoning
about cipher *design and proofs* — which is prose, the thing models are good at —
and the paper **explicitly excludes "toy ciphers such as Vigenère."** Two
benchmarks measuring different tasks, not a contradiction.

**Also nothing:** parsing tool output (feeding 8 K tokens of nmap XML costs
**8 minutes of node time per student** against n8n's zero-code `XML` node);
brute force; and **deciding whether an exploit worked — the webhook decides,
which is F36's rule in a new domain.**

### Only 3 of 8 workflows genuinely need n8n, and the honest framing is the lesson

**Strong, always for one of two reasons — a WAIT or an INBOUND EVENT:**
- **S6, the strongest:** `sqlmapapi` submit → `Wait` → poll → collect. **That is
  Missing Link's own shape in a second domain**; a script does it with `nohup`
  and a PID file.
- **S3:** Juice Shop's `SOLUTIONS_WEBHOOK` (CONFIRMED payload, carries a
  `cheatScore`) — a script has no listener.
- **S2 weakly**, for a shared scoreboard across 20 concurrent students.

**The other four are decorative** — straight-line transforms that `exiftool
-json | jq` or `nmap -oX | xsltproc | diff` do in five lines. **Build them in
n8n anyway, and say so out loud:** the canvas is the artefact being taught — you
can point at the `If` node that skips the LLM and ask what it saved — not an
engine that earns its place.

### Licence findings, one of which is not about licensing at all

**Clean:** Juice Shop MIT, DVWA GPL-3.0, CyberChef Apache-2.0/Crown, hashcat MIT,
ffuf MIT, sqlmap GPLv2 — verified against actual licence files.

- **`nmap` ships under the NPSL**, which prohibits redistribution inside
  proprietary products. Irrelevant to teaching; **relevant if a course image is
  ever sold, or if the eventual Skill emits it.**
- **SecLists is MIT, but its `Leaked-Databases/` wordlists are real people's
  breached credentials.** S2 uses a synthetic list instead. **An MIT licence on
  a collection says nothing about rights in what was collected** — the Hansard
  precedent applied for an entirely different reason, and the more important of
  the two.

### Tooling reality on Debian 12

**In `main` (CONFIRMED from the local index):** nmap 7.93, ffuf 1.1.0, hashcat
6.2.6, john 1.9.0, sqlmap 1.7.2, exiftool, hydra, gobuster, whatweb, wfuzz.
**Not available:** `zaproxy` (absent from bookworm), `nuclei`/`seclists`/
`cyberchef`; **`nikto` is `non-free`**, a component this node does not enable.

Two caveats worth acting on: **Debian's `john` is core 1.9.0, not jumbo**
(crypt(3) family only — which is exactly what the bcrypt-cost lesson needs), and
**hashcat's pocl CPU backend is REPORTED unsupported upstream**, so `john` is the
primary path.

### Blocked by F60, and the unverified item to settle first

**S3, S5 and S6 target Juice Shop and DVWA — which F60 established are NOT on
this LAN.** They are blocked until the operator names the lab network, or until
we stand up our own containers on node 3. The latter is now clearly better: our
own targets are resettable, under our control, and on a segment we are permitted
to point students at.

**Highest-risk unverified item: the `sqlmapapi` endpoint set (S6).** REPORTED
from community sources; no official documentation appears to exist. Fifteen
minutes against a local `sqlmapapi.py -s` settles it — and S6 is otherwise the
workflow to demo first. **Every token count in the document is an estimate of
input size, and F49 says such guesses are wrong by up to 2×**, so one
`POST /tokenize` pass over real examples should correct the tables before
anything is timetabled.

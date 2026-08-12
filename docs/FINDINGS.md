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

## F12. The DIMMs are probably in 2 of 4 channels — a possible free 2× fleet-wide

**INFERRED, pending `dmidecode`.** But the arithmetic is hard to explain away.

| Configuration | Theoretical | Measured 28.2 GB/s is |
|---|---:|---:|
| Quad-channel DDR4-2400 | 76.8 GB/s | **37%** |
| Dual-channel DDR4-2400 | 38.4 GB/s | **73%** |

Real STREAM efficiency on a healthy DDR4 system is 60–80%. **37% is not a
plausible result for a correctly-populated quad-channel board; 73% is textbook.**
The E5-1620 v4 supports quad-channel, so the likely explanation is that the
board is **half-populated** — 2 × 64 GB rather than 4 × 32 GB.

By F11, generation speed is *exactly* proportional to memory bandwidth here. So
if this is right, **populating all four channels roughly doubles generation
throughput on every node, at zero hardware cost** — just moving DIMMs between
slots, on machines that are already open.

**Action:** confirm with `sudo dmidecode -t memory` and check the `Locator`
labels for which channels are populated. Do this on node 2 **before it is
closed up**, and on every node before racking. Re-run the STREAM measurement
after any rebalance.

**For the skill:** this generalises into an assessment step. "How much RAM does
it have" is the wrong question; **"how many channels is that RAM spread across"**
is the one that determines speed. An organisation consolidating DIMMs into
fewer machines to hit a capacity target could halve its own throughput without
ever knowing. Measure bandwidth with STREAM, compare against
`channels × MT/s × 8`, and flag anything under ~50%.

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

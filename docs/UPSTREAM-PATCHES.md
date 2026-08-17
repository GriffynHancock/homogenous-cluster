# Corrections to fold into the main repo

Concrete diffs against `CLAUDE.md`, the plan and the spec, produced by running
node 1 on real hardware. Each traces to a finding in `docs/FINDINGS.md`.

Ordered by how much damage the current text would do if left alone.

---

## 1. `CLAUDE.md` — the per-node 75% RAM rule (F1)

**Current text:**

> **Two memory constraints, both must hold.** Pooled `(RAM − 1 GB/node) × 0.85`,
> and a hard **per-node ≤75% of physical RAM** (llama.cpp #15055, unfixed —
> exceeding it aborts at runtime).

**Problem:** #15055 was **not** a RAM-percentage rule and is **not** unfixed. It
was an OS limit on a single `send()`/`recv()` syscall buffer, fixed by PR #15188
on 2025-08-13 (commit `e71d48e`). The reporter's 75% correlation was
coincidental.

**Replace with:**

> **Two memory constraints.** Pooled `(RAM − 1 GB/node) × 0.85`, and a per-node
> working limit of **≤75% of physical RAM**. The 75% figure is a *chosen safety
> margin*, not a known hard limit — it covers page cache for the GGUF, KV cache
> growth, and llama.cpp's history of overcommit OOM kills (#22629). It is **not**
> traceable to #15055, which was a syscall-size bug and is fixed. **Measure the
> real ceiling before treating it as binding.**

---

## 2. `CLAUDE.md` and plan — `rpc-server -t` (F10)

**Current text:** *"`rpc-server -t` defaults to half the cores. Always set it
from `nproc`."* And `install-services.sh` writes `RPC_THREADS=$(nproc)`.

**Problem:** measured on node 1 (4 cores / 8 threads), `-t 8` is **26% slower**
than `-t 4` on generation (8.31 vs 11.19 t/s). `nproc` picks the worst value.

**Replace with:**

> **`rpc-server -t` defaults to half the cores and must be set explicitly — to
> PHYSICAL cores, not `nproc`.** Generation is bandwidth-bound and SMT siblings
> contend for the same memory pipe; prefill saturates at the physical core count
> too. Derive it with:
> `lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l`

---

## 3. Plan Task 3 and Task 8 — the TTFT measurement is broken (F17)

**Problem:** `curl -w "%{time_starttransfer}"` measures when HTTP *headers*
arrive. `llama-server` sends them immediately, so it reported **0.015 s** for a
request whose real TTFT was **89 s**. Task 8's concurrency test has the same bug
and measures the project's central "seats vs speed" claim.

Second bug in the same code: it loops an **identical prompt**, so runs 2+ hit the
prompt cache and report `prompt eval time = ... / 1 tokens`.

**Fix:** use `bench/node-bench.sh` from this branch — it parses the SSE stream
for the first content-bearing chunk and varies the prompt per run. Cross-check
against `prompt eval time` in the server log, which is authoritative.

**Apply the same fix to Task 8 before running the concurrency measurement.**

---

## 4. Plan — the 90 s TTFT gate is the wrong gate (F19)

**Problem:** the threshold is an interactive-chat instinct; this project is
explicitly async ("submit overnight, read in the morning"). Node 1 measured 89 s
on a **4B** model, so the gate will fire on every model in the project while the
actual workload remains viable — a 50K-token document map-reduces in ~50 min.

**Replace the TTFT gate with a document-throughput gate**, e.g. *"a 50K-token
document must complete within one overnight window."* Keep reporting TTFT as a
diagnostic; stop treating 90 s as stop-the-line.

---

## 5. Plan Task 1 — `build-llama.sh` (F8, F13)

Three defects, all of which fail **only on the workers**:

1. **`-DGGML_NATIVE=ON`** bakes `-march=native`. On a salvaged fleet an
   older-CPU node passes the RPC handshake, loads the model, then dies with
   SIGILL mid-graph. Use `-march=haswell` (keeps AVX2/FMA/F16C) until every
   node's ISA is confirmed identical.
2. **The binary is `ggml-rpc-server`**, not `rpc-server` — upstream renamed the
   target. Install a compatibility symlink.
3. **Default RPATH points into the build tree.** ggml builds shared libraries
   and `llama-server` is an 18 KB stub, so binaries are neither self-contained
   nor relocatable. Build with
   `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`, ship
   `*.so*`, and **assert** no binary references the source tree.

---

## 6. Plan Task 5 — `distribute.sh` needs an ISA assertion (F8)

Version and libc checks cannot catch an ISA mismatch, which is the worst of the
three failure modes. Add a check that every flag the master's build uses is
present on each worker, and actually *execute* `rpc-server --help` remotely to
catch missing shared libraries.

---

## 7. Plan Task 10 — the job store races (F20)

`claim_next_pending` is a `SELECT` then `UPDATE` in a **deferred** transaction,
so two workers claim the same job. Reproduced: 26/24/23 claims for a 20-job
queue across three runs. Every test in the plan's suite is single-threaded, so
none catch it.

Fix with `BEGIN IMMEDIATE` and `isolation_level=None`. Add a concurrency test.
Also add `requeue_running()` — the plan has no recovery path for jobs stranded
by a crash, and these jobs run for hours.

---

## 8. Plan Task 11 — guard against empty reasoning-model output (F21)

`payload["choices"][0]["message"]["content"]` returns `""` when a reasoning
model exhausts `max_tokens` mid-thought — observed on Qwen3-4B: `content` 0
chars, `reasoning_content` 659 chars, `finish_reason: "length"`, HTTP 200.

The worker would store an empty summary and mark the job **done**. Fail loudly
instead, and budget `max_tokens` for thinking or disable it.

---

## 9. `STATUS.md` — stale build-pin warning (F6)

The `b8492`-and-later warning is stale. The `--tensor-split` regression
(#20908 → #21006) was fixed by PR #21030 on 2026-03-27, verified an ancestor of
b10369. Remove the warning; note there is no stable channel to retreat to, since
b-tags are cut several times daily off `master`.

---

## 10. Spec — `-ub` does not help CPU prefill (F18)

The spec claims raising `-ub` "targets TTFT directly". Measured: 27.18 / 26.60 /
27.61 t/s at `-ub` 512 / 1024 / 2048 — all within noise. Amortising weight
loading only helps if weight loading is the bottleneck; prefill is
compute-bound. Remove the claim.

---

## 11. New material worth adding to the plan

- **A single-machine multi-worker RPC pre-flight** (F22). Two `rpc-server`
  processes on localhost with `--tensor-split 1,1` exercise the multi-worker
  code path — including upstream bug #26500 — **without provisioning a second
  machine.** Cheap gate, should be standard.
- **Check the coordinator's free disk before choosing a model** (F16). Salvaged
  desktops are RAM-rich and disk-poor; disks get pulled on decommission while
  DIMMs stay in. Disk bound this project's model choice, not RAM, and nobody
  checked.
- **Measure achievable bandwidth with STREAM during assessment** (F11, F12).
  Generation runs at ~99% of it, so it predicts tok/s directly. Compare against
  `channels × MT/s × 8`; a large gap means the CPU cannot saturate its own bus,
  and the answer is *more machines*, not *more RAM*.
- **Core count is a bandwidth spec, not just a compute spec** (F12). Nodes with
  more cores will be faster at generation despite identical RAM, which means
  `--tensor-split` should weight by measured bandwidth rather than by RAM.

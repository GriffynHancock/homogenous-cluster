# Status

**Updated:** 2026-08-17
**Phase:** **Phase 0 complete on node 1** (Tasks 1–3, bar the large-model
numbers). **Phase 3 Tasks 10–11 built ahead of schedule** (26 tests passing).
Models downloading. Node 2 install in progress.
**Repo:** https://github.com/GriffynHancock/homogenous-cluster

> **Read `docs/FINDINGS.md` first.** Twenty findings, several of which correct
> the plan or the spec. The four that change decisions:
>
> - **F16 — Model B does not fit on disk.** 547 GB model, 431 GB free. Disk,
>   not RAM, is the binding constraint. A drive is being fitted week of
>   2026-08-17; interim target is Qwen3-Next-80B at 93 GB.
> - **F17 — the plan's TTFT measurement is wrong**, reporting 0.015 s where the
>   truth was 89 s. The same bug is in Task 8, which measures the project's
>   central "seats vs speed" claim.
> - **F2 — an open, unmerged upstream bug breaks clusters with 2+ RPC
>   workers.** Fixed in no released tag. Smoke-test two nodes before committing
>   to seven.
> - **F20 — the plan's job-claim logic races** and runs jobs twice. Reproduced,
>   fixed, regression test added.
>
> **F1 also retracts the per-node 75% RAM rule's citation**, and **F12 retracts
> an earlier hypothesis in this repo** that the DIMMs were half-populated —
> `dmidecode` disproved it.

Implementation plan: `docs/superpowers/plans/2026-08-10-cluster-bringup.md`
(14 tasks). Design spec:
`docs/superpowers/specs/2026-08-03-homogenous-cluster-design.md`.
Long-term direction (do not start yet):
`docs/superpowers/specs/2026-08-11-skill-direction.md`.

---

## If you are a fresh session on node 1

1. Run `./bootstrap.sh` — installs deps, prints hardware facts. Idempotent.
2. Follow the plan from **Task 1, Step 2**.
3. **Task 1's hardware facts gate everything downstream.** Core count sets
   `-t`; memory channels set bandwidth, which sets tokens/sec; total RAM
   decides which model is possible. Record them in `docs/measurements.md`
   *before* choosing a model.
4. Update this file as you go. The next session may be a cold start.

**You are the operator.** Run the commands, read the output, record the numbers.
Never report a step done without having seen its output.

---

## Where things stand

**Node 1 is provisioned, built and measured.** llama.cpp b10369 at
`/opt/llama.cpp/bin`, Phase 0 complete, every number in `docs/measurements.md`
taken from this hardware. Missing Link's job store and worker are built and
tested (26 passing) ahead of schedule.

**The spec's performance numbers are still datasheet arithmetic and are now
known to be optimistic** — generation matches prediction closely, but prefill
does not, and the spec has no figure for it at all.

Not yet done: nodes 2–7, the large-model measurements (downloading), the
two-node RPC smoke test, Open WebUI, Missing Link's web layer, and the quality
harness.

**Deliverables, in order:**

1. **The cluster (now).** 7 nodes doing real work on real sensitive documents.
2. **A Claude Skill (later).** Assess → generate → operate, for organisations
   without a specialist. Extensible by hardware profile (GPU, high-CPU/low-RAM,
   MoE offload, coprocessors) and task profile (summarisation, multi-step Q&A,
   drafting). Includes an **agent appliance** — separate out-of-band machine
   that monitors, patches, resumes failed jobs and reports.

Do not start the skill before the cluster produces measurements. Its whole value
is that its advice is measured rather than arithmetic.

## Decided

| Question | Answer |
|---|---|
| Compute | CPU + system RAM only. GPUs present for display, unused. |
| Sharding | llama.cpp RPC (pinned tag). Not Exo, prima.cpp, or distributed-llama. |
| **Model A** | **gpt-oss-120b (~63 GB, 5.1B active) on ONE node** — speed reference |
| **Model B** | **Kimi K2 Q4 (~550 GB, 32B active) across SEVEN** — the thesis |
| OS | Debian 12 headless |
| Provisioning | Debian preseed + idempotent `setup.sh`. Not `dd`. |
| Network | RPC on raw LAN IPs; Tailscale for admin SSH and web only |
| Frontend | Open WebUI, lightly skinned |
| Demo | Missing Link — async job runner (summarisation, report drafting) |
| Chunking | **Map-reduce**, ~4K chunks / 10% overlap. Not refine, not stuffing. |
| Concurrency | **Sequential until measured.** MoE may make batching worthless. |
| Quality eval | BillSum (CC0), factual consistency + SummEval, scored separately |

## Hardware (node 1 MEASURED 2026-08-12 — see `docs/measurements.md`)

| | Per node | × 7 |
|---|---|---|
| CPU | **Xeon E5-1620 v4 — 4 cores / 8 threads, 1 socket, 1 NUMA node** | — |
| ISA | **AVX2, FMA, F16C. NO AVX-512.** | — |
| RAM | **131.8 GB** (`MemTotal`), i.e. 122.7 GiB | ~922 GB |
| Pooled limit | `(RAM − 1 GB/node) × 0.85` | ~747 GB |
| **Per-node 75%** | 98.8 GB | **~692 GB ← still binds** |
| Disk | NVMe 477 GB Intel SSDPEKKF512G7L | — |
| Network | Gigabit, LAN `10.10.0.34/24` on `eno1` | — |

**Only node 1 is measured.** Node 2 is being installed now and is reported to
be near-identical. Nodes 3–7 unverified.

**Two things the plan got wrong here (F7):** it assumed a many-core Xeon and
derived `-t` from `nproc`. Four physical cores and no AVX-512 make **TTFT, not
tokens/sec, the metric at risk** — the GPU-revisit threshold is now much more
likely to trigger. And `nproc` returns 8 while only 4 cores are real, so
`rpc-server -t $(nproc)` needs measuring before it is baked in fleet-wide.

**The 75% rule is now a chosen safety margin, not a known constraint (F1).**
Its cited source (#15055) turned out to be a fixed syscall-size bug, not a RAM
percentage. Worth measuring the real ceiling — at 85% the budget would be
~784 GB, which changes which Kimi K2 quant is reachable.

**A single node holds 96 GB.** So the cluster is only justified above that —
which is why Model B is Kimi K2 and not something mid-sized. This sharpens the
argument: run what no single machine could hold, at any speed.

## Open questions

**Answered on hardware (2026-08-12 to 08-17):**

- [x] **CPU: Xeon E5-1620 v4, 1 socket, 4 cores / 8 threads, 1 NUMA node,
      AVX2 but NO AVX-512.** Single socket, so no thread pinning needed.
- [x] **Memory: 4 × 32 GB DDR4-2400, all four channels, full rated speed.**
      Theoretical 76.8 GB/s; **measured 28.2 GB/s (STREAM)**. The gap is the
      4-core CPU, which cannot saturate quad-channel — not a misconfiguration
      (F12). Uncore and EPB confirmed already at maximum, so no BIOS lever.
- [x] **RPC overhead: generation −5.2% (gate PASSES), prefill −39.4%** (F14).
- [x] **Effective bandwidth: generation runs at ~99% of STREAM** (F11), so
      `tok/s ≈ bandwidth / bytes_per_token` is now predictive. Table in
      `docs/measurements.md`.
- [x] **Optimal `-t` is 4 (physical cores)**, not `nproc`=8, which is 26%
      slower on generation (F10).
- [x] **`-ub` does nothing for prefill** — refutes the spec's expectation (F18).

**Still open:**

- [x] **ANSWERED: no. Sparse MoE reaches 61% of STREAM, not 99%** (F24).
      Measured on gpt-oss-120b: 6.05 tok/s against 9.88 predicted at dense
      efficiency. Scattered expert gathers defeat prefetch. **Every MoE
      estimate in this repo was ~1.6x optimistic and has been revised** —
      Kimi K2 IQ4_XS drops from 1.76 to **~1.08 tok/s**, which still clears
      the overnight bar (~39,000 tokens in 10 h) with less margin.
- [ ] Confirm nodes 2–7 hardware. **If any have more cores they will be faster
      at generation despite identical RAM**, and `--tensor-split` should then
      weight by measured bandwidth rather than RAM (F12).
- [ ] Real per-node RAM ceiling, now that the 75% rule's citation is disproven
      (F1). At 85% the pooled budget would be ~784 GB.

**Batching — researched 2026-08-11, one item now the top measurement priority:**

- [x] **`-c` IS divided across slots** — `n_ctx_seq = n_ctx / n_seq_max`, padded
      to 256 (confirmed in `src/llama-context.cpp`). Size `-c` as
      `per_chunk_context × n_parallel`. Also: leaving `--parallel` unset means
      **4 slots**, not one — auto resolves to `n_parallel=4, kv_unified=true`.
- [x] **Overflow is not silent** *provided `--ctx-shift` stays off* (the
      default). Prompt-too-long → hard HTTP error; mid-generation overflow →
      slot marks `truncated`. **With ctx-shift ON, KV is silently evicted** —
      never enable it for document work.
- [x] **Batching works over RPC** (exercised in #14893), but see the build pin
      warning below.
- [x] **ANSWERED: it damages it badly but does not destroy it.** Measured on
      gpt-oss-120b: generation 5.43 → 7.99 → 9.73 t/s at batch 1/2/4, i.e.
      **1.79× at batch 4** where dense would give ~3×. Prefill is completely
      flat (15.96 → 16.58), so no batching helps prefill at all.
      **The "few seats" claim survives but is now the INFERIOR route to
      concurrency:** replicating a node-sized model across 7 nodes gives ~7×
      against batching's 1.79×. See `docs/DESIGN-NOTES.md` section C.
- [ ] **Cross-talk test before enabling `--parallel`.** Trigger for #14893 was
      **FA off + multi-slot + RPC**; `-fa auto` may resolve to off on CPU, which
      matches this architecture exactly. Fix has no build tag — test explicitly
      with unique markers in two concurrent requests regardless.
- [ ] **⚠️ Build pin: avoid `b8492` and later without checking.** PR #20908
      broke RPC tensor-split across machines (#21006); `b8487` was last
      known-good. Confirm #21030/#26500 are merged in whatever tag is pinned.

**Decide on measurement:**

- [ ] Whether TTFT is bad enough to reconsider GPUs for prefill (threshold: 90 s
      at ~2000 tokens).
- [ ] Exact Kimi K2 quant — pick the largest that keeps every node ≤75%.
- [x] **ANSWERED: yes for our workload.** ik_llama.cpp 8337e4cd vs mainline
      b10369, same model and flags: **prefill +52%** (16.08 -> 24.49 t/s),
      **generation -14%** (6.04 -> 5.17). Since prefill is 79% of document
      wall-clock, that is **~22% faster end-to-end** (78.8 -> 61.6 min on a
      50K-token document). Output verified coherent. This explains the "split
      evidence": generation benchmarks say no, prefill benchmarks say yes, and
      our workload is prefill-dominated. See F27.

**Verify empirically (cheap, no source found):**

- [ ] Bookworm netinst initrd path (`install.amd/`?) — `ls` the actual ISO
- [ ] `ldd` output for a CPU-only `llama-server` / `rpc-server` build
- [ ] Whether `/etc/apt/sources.list` populates correctly post-preseed

---

## Research findings

### llama.cpp RPC

- **Hard ~75% per-node RAM ceiling** (#15055, unfixed). Exceeding it aborts with
  `"Remote RPC server crashed or returned malformed response"`. Binds separately
  from — and more tightly than — the pooled 15% headroom.
- **The "30–55% RPC overhead" figure is not credible.** Issue #22850 was
  LLM-authored and closed in under two hours for an AI-policy violation with no
  technical review; it benchmarked against a deliberately crippled baseline, on
  a build predating the fix for the main problem it identified. Real overhead
  for CPU-only batch-1 is **unmeasured** and likely far smaller, because
  per-token compute here is 10–100× a LAN round-trip. Measure it (Task 2).
- **Graph reserialisation is fixed** (#22701 `graph_uid` fast path). Keep batch
  shapes constant across steps to keep that path active.
- **Do not patch `HASH_THRESHOLD`.** Generation traffic is fresh bytes per token,
  so hashing would never dedup while adding a round-trip — likely slower.
- **Async/pipelined RPC is coming** — PR #18626, by the RPC maintainer, open and
  active. Re-benchmark and re-pin when it lands. Temper expectations: prefill
  gains likely, decode gains uncertain.
- **`--tensor-split` is the layer-split mechanism.** Set it explicitly;
  auto-split trusts buggy self-reported free memory. `--split-mode row` does
  nothing over RPC. `--rpc` must precede `--device`.
- **`rpc-server -t` defaults to HALF the logical cores.** Always set it from
  `nproc`. (The "24 threads beat 96" contention effect does not apply below ~24
  threads — that is a many-core memory-controller issue, not general advice.)
- **`rpc-server -c`** caches tensors ≥10 MiB, content-hashed, to `$LLAMA_CACHE`
  or `~/.cache/llama.cpp/rpc`. Always run with it — there is a report of the
  process going `<defunct>` without it. Essential at ~550 GB.
- **Version mismatch fails loudly** at handshake — good, no silent corruption.
- **Pin the build.** `--tensor-split` over RPC has regressed before (#21006).
- **Upstream calls RPC "fragile and insecure, never run on an open network"** —
  validates raw LAN IPs as a security requirement, not just latency.
- **The public 0.06 tok/s CPU-cluster figure is a misuse case** — the model
  already fitted on one host. Never cite it without that context.

### Batching and concurrency

- **Free batching is a dense-model property.** Measured on CPU (Intel Ultra 9
  285K, discussion #18030): generation scaled 25.7 → 147.7 t/s from batch 1 → 32
  (~5.75×), prefill flat at ~230 t/s throughout.
- **Sparse MoE probably breaks it.** Batch B touches ≈ `min(B × top_k,
  n_experts)` distinct experts, so bytes read grow roughly in step with tokens
  produced. Reality should sit between neutral and linear — attention weights
  and shared experts are reused, and popular experts overlap — but **nobody has
  published MoE batching numbers on CPU.** Measure it.
- **`-c` is divided across slots**: `n_ctx_seq = n_ctx / n_seq_max`, padded to
  256. Size `-c` as `per_chunk_context × n_parallel`.
- **Unset `--parallel` gives 4 slots**, not 1 — auto resolves to
  `n_parallel=4, kv_unified=true`. Always set it explicitly.
- **Prefer `--no-kv-unified`.** Unified mode is a shared elastic pool, so one
  slot can starve others; it also has a reported bug where populated-but-idle
  slots drag down active throughput (#19523).
- **Keep `--ctx-shift` off** (default). Off → hard error or a visible
  `truncated` flag. On → silent KV eviction, quietly dropping the start of the
  document. That is invisible corruption.
- **`-ub` (default 512) is worth raising for CPU prefill** — a larger ubatch
  amortises weight loading across more tokens, targeting TTFT directly.
- **`-t` and `-tb` can differ** — generation is bandwidth-bound and saturates
  early; prefill is compute-bound and wants every core.
- **Our exact combination is under-tested publicly** — CPU-only, RPC-sharded,
  multi-slot, MoE. Budget time for undocumented bugs.

### Rejected alternatives (do not re-propose)

- **Exo** — MLX is now its only engine (tinygrad deleted in the v1 rewrite);
  Linux CPU is "Planned" tier, not Tier 1; zero CPU optimisation commits in
  2,353; no GGUF support; its RDMA tensor parallelism is Thunderbolt/macOS-only.
- **prima.cpp** — no MoE support; original repo is a 404.
- **distributed-llama** — all-reduce per layer per token over gigabit; own
  weight format; abandons the GGUF/Open WebUI stack.
- **GPU sharding** — 14 GB pooled VRAM was too little; revisit only if TTFT
  proves unbearable, and then for prefill only.
- **`dd` cloning** — disks vary in size and type.

**Nobody has solved decode-time synchrony.** Exo overlaps sends with compute
during prefill but is fully synchronous per layer per token during generation —
llama.cpp's exact constraint. This is a hard problem, not a llama.cpp defect.

### Provisioning

- **Duplicate `machine-id` breaks DHCP**, not just logging — systemd-networkd
  derives its DHCP client-ID from it, so identical IDs collide on one lease and
  present as intermittent fleet-wide network flapping.
- **Tailscale LAN isolation is automatic** — it only owns `100.64.0.0/10`. The
  only way to pull RPC onto WireGuard is `--advertise-routes`, so never pass it.
- **Preseed must set `non-free-firmware`** or recycled NICs may lack firmware.
- **Leave `partman-auto/disk` unset** to handle varying disks.
- **THP is already correct on Debian 12** (`madvise`) and measurably better than
  `always`. Assert it; do not tune it.
- **Build natively on a fleet node** — glibc is forward-incompatible.

### Summarisation pipelines

- **Nothing mature exists to adopt or fork.** No self-hosted project does "point
  at llama.cpp, queue overnight, summarise long documents." Nearest standalone
  tool has 624 stars and is semi-stale. Popular tools (private-gpt, Kotaemon,
  localGPT) are RAG-QA systems that retrieve top-k chunks — the *opposite* of
  reading a whole document. Building Missing Link fills a real gap.
- **If reaching for a library**, LlamaIndex's `tree_summarize` is the closest
  building block. But **both LlamaIndex and LangChain default to a 60 s timeout**
  and retry 3–6 times — against a multi-minute backend that is a retry storm,
  not a summary. Set timeouts explicitly.
- **Map-reduce beats refine decisively** (BooookScore, arXiv:2310.00785 —
  Mixtral 81.5 vs 64.5; LLaMA 2 failed refine entirely), and refine is strictly
  sequential so far slower in wall-clock.
- **A bigger context window does not fix "lost in the middle"** — extended-
  context variants show near-identical position bias (arXiv:2307.03172).
  Capacity ≠ quality.
- **CPU prefill drops ~58% from 512 → 32K context** as attention becomes
  bandwidth-bound. Independent hardware reason to chunk.
- **Chunk size barely matters for map-reduce** (unlike refine) — so ~4K with 10%
  overlap is fine and is not worth tuning.
- **No public leaderboard compares local llama.cpp models to frontier models on
  summarisation.** Producing one is a genuine contribution — say so rather than
  implying precedent. Use BillSum (CC0, republishable) and score factual
  consistency separately from the SummEval rubric; do not blend them.

---

## Next

**Phase 0 — Measurement gate (Tasks 1–3).** Task 1 ✅, Task 2 ✅ **(gate
PASSED — generation overhead 5.2%)**. Task 3 in progress.

**Before anything touches a second machine, in this order:**

1. **`sudo dmidecode -t memory` on node 1 and node 2.** Bandwidth measured at
   28.2 GB/s, which is 73% of *dual*-channel DDR4-2400 but only 37% of
   *quad*-channel. The board is probably half-populated. Since generation runs
   at ~99% of memory bandwidth (F11), **rebalancing DIMMs across all four
   channels is potentially a free ~2× on every node** — and node 2 is open
   right now. Do this before it is closed up.
2. **Two-node RPC smoke test** as soon as node 2 exists (F2). An open, unmerged
   upstream bug breaks clusters with 2+ RPC workers and is fixed in no released
   tag. Confirm before fetching 550 GB or provisioning nodes 3–7.
3. Model A (gpt-oss-120b) single-node baseline — the speed reference.

**Phase 1 — Fleet provisioning (Tasks 4–5).** `setup.sh` on all 7, binary
distribution with version, libc **and ISA** assertions (the last one added
after F8/F13).

**Phase 2 — Sharded inference (Tasks 6–9).** `rpc-server` services, Kimi K2
fetch, both memory checks, cluster launch, measurement, Open WebUI.

**Phase 3 — Missing Link (Tasks 10–13).** Job store, map-reduce worker, web
API, end-to-end.

**Phase 4 — Quality evaluation (Task 14).** BillSum harness, two-axis scoring.

---

## Log

- **2026-08-03** — Brainstormed. Initial design assumed GPU sharding across
  14 Quadro P600s.
- **2026-08-03** — Pivoted to CPU-only. 14 GB pooled VRAM was too little to hold
  meaningful layers, and hybrid CUDA/CPU RPC was unproven.
- **2026-08-10** — Research across three streams (RPC internals, model
  performance, provisioning). Found the ~75% per-node RPC RAM ceiling.
- **2026-08-10** — Retracted the 30–55% RPC overhead figure; its source does not
  survive scrutiny. Exo rejection re-confirmed against the current codebase.
- **2026-08-10** — Implementation plan written (13 tasks).
- **2026-08-10** — **Hardware revised: 128 GB DDR4 per node, not 8 GB DDR3.**
  Budget went ~41 GB → ~672 GB. Target changed from one mid-sized model to a
  pair: gpt-oss-120b on one node versus Kimi K2 across seven. Context raised to
  32768; thread counts now derived from `nproc`.
- **2026-08-10** — Chunking resolved **on evidence** as map-reduce, against the
  earlier instinct to just enlarge the context window. Added Task 14, a quality
  evaluation harness.
- **2026-08-10** — Repo bundled for deployment and published.
- **2026-08-11** — Worked through the pipeline topology properly: only one node
  computes at a time per request (utilisation 1/7); seats are possible because
  weights are read-only while KV cache is per-sequence and lives on the node
  holding those layers.
- **2026-08-11** — **Batching research qualified a core claim.** Free batching
  is a dense-model property; on a sparse MoE, batch B touches ≈ B × top_k
  experts, so bytes read grow with tokens produced and throughput may stay flat.
  The "multiplies seats" line in CLAUDE.md is now marked unresolved pending
  `llama-batched-bench`. Also confirmed `-c` divides across slots, that
  unset `--parallel` silently means 4 slots, that `--ctx-shift` must stay off to
  avoid silent KV eviction, and that builds from b8492 broke RPC tensor-split.
- **2026-08-11** — **Reframed around data sovereignty and a two-stage
  deliverable.** The argument is now: Australian organisations with statutory
  constraints cannot send data offsite; on-prem infra dies on *ongoing* cost,
  not acquisition; but those organisations already own idle hardware (2019-era
  and newer is useful). Immediate deliverable is the cluster; long-term is a
  Claude Skill (assess → generate → operate) extensible by hardware profile and
  task profile, including an out-of-band agent appliance. Security is
  explicitly out of scope — state the requirement, do not advise.

- **2026-08-12** — **Node 1 bring-up. Phase 0 executed on real hardware.**
  Built llama.cpp b10369 (the `b8492` warning was stale — the #21006 fix landed
  in March). RPC overhead gate **passed** on generation (−5.2%) but prefill cost
  −39.4%. Measured the thread sweep, the memory bandwidth, and the TTFT.
  Discovered `rpc-server` was renamed `ggml-rpc-server` upstream and that the
  default RPATH made the binaries non-relocatable — both would have failed only
  on the workers.
- **2026-08-12** — **Two constraints in the plan turned out to be wrong.** The
  per-node 75% RAM rule cites an issue that was actually a fixed syscall-size
  bug (F1). And Model B does not fit the master's **disk** — 547 GB against
  431 GB free — making disk, not RAM, the binding constraint on model choice
  (F16).
- **2026-08-12** — **The plan's TTFT measurement was measuring nothing.**
  `curl -w %{time_starttransfer}` reported 0.015 s for a request whose real
  time-to-first-token was 89 s; it times HTTP headers. Runs 2 and 3 additionally
  hit the prompt cache. Rewrote the benchmark; the same bug is in Task 8, which
  measures the project's central claim (F17).
- **2026-08-17** — **Memory question settled, and an earlier guess retracted.**
  `dmidecode` shows 4 × 32 GB across all four channels at full 2400 MT/s, so the
  half-population hypothesis in this repo was wrong. The 28.2 GB/s ceiling is
  the 4-core CPU failing to saturate its own bus; MSRs confirm uncore and
  energy-perf-bias already at maximum, so there is no BIOS lever either (F12).
- **2026-08-17** — **Reframed the TTFT gate.** 89 s at 2000 tokens breaches the
  plan's 90 s threshold on a *4B* model, but that threshold is an interactive
  instinct and this project is explicitly async. Composed from measured rates, a
  50K-token document map-reduces in ~50 min on one node. Recommends a
  document-throughput gate instead (F19). `-ub` tuning was tested and does
  nothing, so no software lever for prefill remains (F18).
- **2026-08-17** — **Missing Link Tasks 10–11 built early** while models
  downloaded. Found and fixed a real race in the plan's job store: it claims the
  same job twice, reproducibly, and every test in the plan's suite is
  single-threaded so none catch it (F20). 26 tests passing.

# Status

**Updated:** 2026-08-10
**Phase:** Planning complete. Awaiting node 1 to begin execution.
**Repo:** https://github.com/GriffynHancock/homogenous-cluster

Implementation plan: `docs/superpowers/plans/2026-08-10-cluster-bringup.md`
(14 tasks). Design spec:
`docs/superpowers/specs/2026-08-03-homogenous-cluster-design.md`.

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

Design and plan are settled and committed. **No hardware provisioned, nothing
measured.** Every performance number in the spec is arithmetic on datasheets and
is marked as such.

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
| Quality eval | BillSum (CC0), factual consistency + SummEval, scored separately |

## Hardware (revised 2026-08-10 — much better than first assumed)

| | Per node | × 7 |
|---|---|---|
| RAM | **128 GB DDR4-2400 ECC** (4 × 32 GB) | ~896 GB |
| Pooled limit | `(896 − 7) × 0.85` | ~756 GB |
| **Per-node limit** | 128 × 0.75 = 96 GB | **~672 GB ← binds** |
| CPU | Unconfirmed. ECC implies Xeon. | — |
| Network | Gigabit, same switch | — |

**Only two of seven nodes are confirmed at 128 GB.** If the rest differ,
recompute both constraints before fetching Model B.

**A single node holds 96 GB.** So the cluster is only justified above that —
which is why Model B is Kimi K2 and not something mid-sized. This sharpens the
argument: run what no single machine could hold, at any speed.

## Open questions

**Blocked on hardware — answer these first:**

- [ ] CPU model, socket count, core count, **NUMA nodes**, AVX-512 presence.
      If NUMA nodes > 1 this is dual-socket and needs thread pinning the plan
      does not cover — **stop and raise it.**
- [ ] Memory channel count (infer from DIMM `Locator` labels). Quad-channel
      DDR4-2400 ≈ 76.8 GB/s; hex-channel ≈ 115 GB/s. This drives tokens/sec
      more than anything else.
- [ ] Confirm all 7 nodes have 128 GB.
- [ ] **Real RPC protocol overhead** — Task 2's localhost test. Has an explicit
      stop-and-escalate threshold at 30%.
- [ ] Effective memory bandwidth, derived from measured tok/s in Task 3. This
      recalculates every estimate in the spec.

**Decide on measurement:**

- [ ] Whether TTFT is bad enough to reconsider GPUs for prefill (threshold: 90 s
      at ~2000 tokens).
- [ ] Exact Kimi K2 quant — pick the largest that keeps every node ≤75%.
- [ ] Whether `ik_llama.cpp` beats mainline here. It *does* support `rpc-server`
      (confirmed). Evidence split; A/B it only after the cluster works.

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

**Phase 0 — Measurement gate (Tasks 1–3).** Base install, pinned llama.cpp
build, localhost RPC overhead test, single-node baseline with gpt-oss-120b.
The overhead test has a stop-and-escalate threshold; **do not provision the
fleet before it passes.**

**Phase 1 — Fleet provisioning (Tasks 4–5).** `setup.sh` on all 7, binary
distribution with version and libc assertions.

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

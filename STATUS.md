# Status

**Updated:** 2026-08-17
**Phase:** Node 1 fully provisioned, built and measured. Phase 0 complete.
Missing Link built through Task 12 (41 tests passing). **Node 2 not yet joined.**
**Repo:** https://github.com/GriffynHancock/homogenous-cluster

---

## If you are a fresh session

1. **Read `docs/FINDINGS.md`.** 27 findings from running this on real hardware.
   Several correct the plan or the spec. Do not trust the original plan's
   numbers over these.
2. `docs/measurements.md` is the only place performance numbers may be quoted
   from.
3. `docs/UPSTREAM-PATCHES.md` lists the concrete corrections still to fold back
   into the plan and spec.
4. **You are the operator.** Run the commands, read the output, record the
   numbers. Never report a step done without having seen its output.

**Everything is built and working on node 1.** llama.cpp b10369 at
`/opt/llama.cpp/bin`, ik_llama.cpp at `/opt/ik_llama.cpp/bin`, models in
`/opt/models`, Missing Link in `missing-link/`.

---

## NEXT TASKS, in order

### 1. Join node 2 — the highest-value work available

**The 1 → 2 transition is where all the risk lives.** Everything that can go
wrong across a fleet appears at N=2 and nothing new appears at N=10.

Node 2 has Debian installed and is being put on the switch. What it needs is in
"Joining a node" below.

Once it is reachable:

```bash
# 1. Characterise it -- do NOT assume it matches node 1
ssh <node2-ip> 'lscpu -p=Core,Socket | grep -v "^#" | sort -u | wc -l; free -m; lsblk -d'
#    and run the STREAM triad -- core count is a BANDWIDTH spec (F12)

# 2. Add node 2 to provisioning/nodes.env with MEASURED values FIRST.
#    Both distribute.sh and install-services.sh read their target list from it;
#    with only node1 present, distribute.sh exits 0 with "nothing to distribute"
#    -- a silent no-op that looks like success, and step 3 then fails.
$EDITOR provisioning/nodes.env      # "node2 <ip> <ram_mb> <physical_cores>"

# 3. Provision, distribute, verify
sudo ./provisioning/setup.sh node2
./provisioning/distribute.sh        # asserts version, libc AND ISA
./cluster/install-services.sh

# 4. Two-node RPC smoke test (gate for upstream bug #26500, F2/F22)
./bench/two-node-smoke.sh <node2-ip>

# 5. THE measurement that matters: replication, not sharding.
#    Run an independent llama-server on each node, measure AGGREGATE throughput
#    on 2 nodes. Expect ~2x. This validates the R x single-node scaling model
#    the whole architecture now rests on.
```

**`nodes.env` values must be MEASURED, not assumed** — LAN IP, RAM MB and
**physical** cores. Do not leave placeholders, and do not copy node 1's values.

### 2. Missing Link fan-out across R endpoints

**This is the main outstanding code change**, and it blocks the replicated
topology from being usable.

Today `missing_link/worker.py` targets a single `base_url` and `app.py` runs
**one** background worker. Under replication the queue must keep **R independent
servers** busy.

Concretely:

- `nodes.env` (or the manifest) grows a list of **inference endpoints**, kept
  separate from RPC endpoints — they are not the same thing.
- `run_forever` becomes R concurrent workers, one per endpoint, each claiming
  jobs independently. `db.claim_next_pending` is **already atomic** under
  concurrency (`BEGIN IMMEDIATE`, tested — F20), so the store is ready.
- Chunks within one document should fan out across endpoints too, not just whole
  jobs — otherwise a single 14-chunk document leaves the rest of the fleet idle.
- Health-check endpoints and route around a dead one. Under replication a node
  failure costs 1/R of throughput and must not fail the job.
- Keep the task profile (prompts/chunking) separable from the queue — that seam
  becomes the skill's task-profile interface.

### 3. Resolve the Model B decision

**Do not fetch Kimi K2 until this is settled.** K2-Instruct has the worst
REPORTED hallucination rate of any model checked (17.9%, Vectara
leaderboard — not verified here), against a project
requirement of faithfulness over style. GLM-4.6 has identical active params
(same speed), 9.5% hallucination, MIT licence, and needs 189 GB instead of
546 GB — **which removes the coordinator-disk blocker entirely.** See F25 and
`docs/MODEL-SELECTION.md`.

Two research gaps that would change the answer:

- **GLM-5 / 5.1 / 5.2 active-parameter count** is not published anywhere found.
  That single number decides whether the strongest MIT-licensed open reasoner is
  usable here.
- **Finix S1 32B** has the best listed hallucination rate (1.8%) but is
  uncharacterised — architecture, active params, GGUF availability all unknown.

### 4. Faithfulness evaluation, earlier than planned

Task 14 in the plan, but the hallucination finding shows it should **gate model
selection rather than validate it afterwards.** AlignScore and SummaC are
RoBERTa-scale and run fine on CPU for a few hundred summaries.

### 5. Smaller, still open

- Adopt `ik_llama.cpp` for the document workload (+22% end-to-end) — its CLI
  differs from mainline, so scripts need adapting, not just re-pointing.
- `-fa` flash attention and `-ctk q8_0` KV quantisation: untested, cheap.
- Open WebUI (plan Task 9).
- Measure the real per-node RAM ceiling now that the 75% rule's citation is
  disproven (F1). At 85% the pooled budget rises ~13%.

---

## Joining a node

**Use the same username as the coordinator (`debian1`).** Not strictly required,
but the systemd unit, `scp`/`rsync` targets and every `ssh` in the scripts all
assume it. A different username means editing all of them.

**Generate these with `./provisioning/join-node.sh` on the coordinator** rather
than copying from here — it substitutes the real key and LAN IP, so it cannot go
stale. The listing below is what it prints today.

On the new machine:

```bash
# 1. Same username as the coordinator
sudo adduser debian1                     # skip if it already exists

# 2. Passwordless sudo. Append to /etc/sudoers, NOT a sudoers.d drop-in --
#    sudo is last-match-wins, and a later "(ALL:ALL) ALL" line silently
#    overrides a NOPASSWD rule in sudoers.d. This bit us on node 1 (F9).
echo "debian1 ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

# 3. SSH server
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh

# 4. Authorise the coordinator's key
sudo -u debian1 mkdir -p /home/debian1/.ssh
sudo -u debian1 tee -a /home/debian1/.ssh/authorized_keys <<'KEY'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOR3eASKRk2WvCDC58A+xKEae5FnndW8Yrukr6fCp04L node1-cluster
KEY
sudo chmod 700 /home/debian1/.ssh
sudo chmod 600 /home/debian1/.ssh/authorized_keys

# 5. Report the LAN IP
ip -br addr | grep -v LOOPBACK
```

Coordinator is `10.10.0.34/24`, gateway `10.10.0.254`. Everything after this the
coordinator can do over SSH.

**Then harden it — key-only, once keys are confirmed working:**

```bash
./provisioning/harden-ssh.sh <node-ip>
```

It verifies key auth from the coordinator **before** disabling password auth,
validates the sshd config with `sshd -t` before restarting, and re-checks
reachability afterwards. If any check fails it reverts and changes nothing —
locking yourself out of a headless box in a locked cupboard is the failure mode
this exists to prevent.

---

## Where things stand

**Node 1 (coordinator, user `debian1`, `10.10.0.34`)** — provisioned, built,
measured.

| Item | State |
|---|---|
| llama.cpp | b10369 (`6e62ba53`) at `/opt/llama.cpp/bin`, relocatable, verified |
| ik_llama.cpp | `8337e4cd` at `/opt/ik_llama.cpp/bin`, output verified coherent |
| Models | Qwen3-4B (2.4 GB), gpt-oss-120b F16 (61 GB); Qwen3-Next-80B downloading |
| Missing Link | job store + worker + web API, **41 tests passing** |
| Phase 0 gate | **PASSED** (RPC generation overhead 5.2%) |

**No `rpc-server` is running yet, on any node — that is expected.** The systemd
unit is *templated*, so the name is `rpc-server@50052.service`, not
`rpc-server` — plain `systemctl status rpc-server` reports "could not be found"
and looks like a broken install. It is installed by `cluster/install-services.sh`,
which is step 3 of joining node 2 and has never been run.

**Not done:** nodes 2+, replication measurement, Missing Link fan-out, Model B
decision, Open WebUI, evaluation harness.

---

## Deliverables, in order

1. **The cluster (now).** N nodes doing real work on real sensitive documents.
2. **A Claude Skill (later).** Assess → generate → operate. Do not start it
   before the cluster produces measurements; its whole value is that its advice
   is measured rather than arithmetic.

---

## Hardware — node 1 MEASURED, others unknown

| | Node 1 |
|---|---|
| CPU | Xeon E5-1620 v4 — **4 cores / 8 threads**, 1 socket, 1 NUMA node |
| ISA | AVX2, FMA, F16C. **No AVX-512** |
| RAM | **131.8 GB** — 4 × 32 GB DDR4-2400, **all four channels at rated speed** |
| **Achievable bandwidth** | **28.2 GB/s** (STREAM) — only 37% of quad-channel theoretical |
| Disk | NVMe 477 GB — **368 GB free as of 2026-08-17**, re-check with `df -h /` |
| Network | Gigabit, `10.10.0.34/24` on `eno1` |

**The bandwidth gap is the CPU, not the memory.** Four cores cannot generate
enough memory-level parallelism to saturate a quad-channel bus — that needs
~8–14 cores on Broadwell. Uncore is already at its 2800 MHz ceiling and
energy-perf-bias is 0, so **there is no BIOS lever.** (F12)

**Nodes 2+ are uncharacterised.** If any has more cores it will be faster at
generation despite identical RAM, and `--tensor-split` should then weight by
measured bandwidth rather than RAM.

---

## Scaling model — N-agnostic

- **S** = nodes needed for one copy = `ceil(model_size / usable_RAM_per_node)`
- **R** = independent copies = `floor(N / S)`
- **Aggregate throughput ≈ R × single-node throughput**

| Route to throughput | Measured | Notes |
|---|---:|---|
| **Replication (R copies)** | **≈ R×** | no RPC, linear, node failure costs 1/R |
| Batching (`--parallel 4`) | 1.79× | MoE-limited; **8 is worse than 1** |
| Sharding (S nodes per copy) | **1×** | buys capacity only; −39% prefill |

**Prefer the largest model with S = 1.** The size at which S goes 1 → 2 is the
most consequential number in model selection: crossing it costs a factor of N.

---

## Measured results (full detail in `docs/measurements.md`)

| Measurement | Result |
|---|---|
| Memory bandwidth (STREAM) | **28.2 GB/s** at 4 threads |
| Optimal threads | **4 = physical cores.** `-t 8` is 26% slower |
| Generation efficiency, dense | **~99% of STREAM** |
| Generation efficiency, **sparse MoE** | **~61% of STREAM** |
| RPC overhead | generation **−5.2%**, prefill **−39.4%** |
| gpt-oss-120b, single node | pp2048 **16.08**, tg128 **6.05** t/s |
| Qwen3-4B, single node | pp2048 28.33, tg128 **11.49** t/s |
| TTFT @ 2214 tokens (4B model) | **89 s** |
| Batching (MoE) | 1.79× at batch 4; **collapses at 8** |
| ik_llama.cpp vs mainline | **prefill +52%**, generation −14%, **net +22%** |

**Sizing rule, validated both ways:**
`tok/s ≈ effective_bandwidth / (active_params × bytes_per_weight)`,
where effective bandwidth is **28.2 GB/s dense, 17.3 GB/s sparse MoE**.
Predicts Qwen3-4B at 11.31 (measured 11.49) and gpt-oss at 6.4 (measured 6.05).

---

## Open questions

- [ ] **Nodes 2+ hardware.** Core count is a bandwidth spec.
- [ ] **Does the 61% MoE efficiency generalise?** Measured on gpt-oss (128
      experts, top_k=4). Kimi K2 has 384 at top_k=8 — a more scattered gather
      could be worse.
- [ ] **Real per-node RAM ceiling** now that the 75% citation is disproven (F1).
- [ ] **Model B**: GLM-4.6 vs DeepSeek-V3.2 vs Kimi K2 — faithfulness-led.
- [ ] **GLM-5 active params** — unpublished; decides a leading candidate.
- [ ] **Finix S1 32B** — best listed faithfulness (1.8%), uncharacterised.
- [ ] `-fa` and `-ctk q8_0` — untested.

---

## Rejected — do not re-propose

- **Exo** — MLX-only engine; Linux CPU is "Planned" tier; no GGUF.
- **prima.cpp** — no MoE; original repo 404s.
- **distributed-llama** — all-reduce per layer per token over gigabit; own
  weight format; abandons the GGUF/Open WebUI stack.
- **GPU sharding / GPU prefill** — the Quadro P600's 2 GB cannot hold meaningful
  layers of any target model.
- **`dd` cloning** — disks vary in size and type.
- **Kimi K3** — real (2.78T total) but **104B active** ≈ 0.3 tok/s here, and
  1.5 TB at Q4. Newer and larger is actively worse (F26).
- **Expert parallelism** — genuinely the largest theoretical win (~4×), and
  communication would not kill it, but llama.cpp has no such mode; building it
  is a new inference engine. Recorded in `docs/DESIGN-NOTES.md` A.

---

## Research findings retained from earlier sessions

### Summarisation pipelines

- **Nothing mature exists to adopt or fork.** No self-hosted project does "point
  at llama.cpp, queue overnight, summarise long documents." Popular tools
  (private-gpt, Kotaemon, localGPT) are RAG-QA systems that retrieve top-k
  chunks — the *opposite* of reading a whole document. Missing Link fills a real
  gap.
- **If reaching for a library**, LlamaIndex's `tree_summarize` is the closest
  building block. But **both LlamaIndex and LangChain default to a 60 s timeout**
  and retry 3–6 times — against a multi-minute backend that is a retry storm,
  not a summary. Set timeouts explicitly.
- **Map-reduce beats refine decisively** (BooookScore, arXiv:2310.00785 —
  Mixtral 81.5 vs 64.5; LLaMA 2 failed refine entirely), and refine is strictly
  sequential so far slower in wall-clock.
- **A bigger context window does not fix "lost in the middle"** — extended-
  context variants show near-identical position bias (arXiv:2307.03172).
- **Chunk size barely matters for map-reduce** (unlike refine) — ~4K with 10%
  overlap is fine and is not worth tuning.
- **No public leaderboard compares local llama.cpp models to frontier models on
  summarisation.** Producing one is a genuine contribution. Use BillSum (CC0)
  and score factual consistency separately from the SummEval rubric.
- **Caveat added 2026-08-17:** the spec's "CPU prefill drops ~58% from 512 → 32K
  context" is much weaker on large MoE models — measured **−1%** from 512 to
  2048 on gpt-oss versus −14% on a dense 4B. Chunking is still right, for the
  other reasons.

### llama.cpp RPC

- **Upstream calls RPC "fragile and insecure, never run on an open network"** —
  validates raw LAN IPs as a security requirement, not just latency.
- **`--tensor-split` is the layer-split mechanism.** Set it explicitly;
  auto-split trusts buggy self-reported free memory. `--rpc` must precede
  `--device`.
- **`rpc-server -c`** caches tensors ≥10 MiB to `$LLAMA_CACHE` or
  `~/.cache/llama.cpp/rpc`. Always run with it. **Check the cache filesystem has
  room** — measured at ~76% of a node's share on a small dense model, and
  approaching 100% on a large MoE.
- **Version mismatch fails loudly** at handshake — good, no silent corruption.
  ISA mismatch does **not**.
- **Do not patch `HASH_THRESHOLD`.** Generation traffic is fresh bytes per token.
- **Async/pipelined RPC is not coming soon** — PR #18626 still open and
  `mergeable_state: dirty` 7+ months in. Assume sequential execution.
- **Model load is single-core serialised** (#25890) — a ~550 GB load takes ~15
  min with cores idle. Budget for it; not a hang.
- **The public 0.06 tok/s CPU-cluster figure is a misuse case** — the model
  already fitted on one host. Never cite it without that context.

### Provisioning

- **Duplicate `machine-id` breaks DHCP**, not just logging — systemd-networkd
  derives its DHCP client-ID from it, so identical IDs collide on one lease and
  present as intermittent fleet-wide network flapping.
- **Tailscale LAN isolation is automatic** — it only owns `100.64.0.0/10`. The
  only way to pull RPC onto WireGuard is `--advertise-routes`, so never pass it.
- **Preseed must set `non-free-firmware`** or recycled NICs may lack firmware.
- **Leave `partman-auto/disk` unset**, and filter out sub-8 GB devices — node 1
  has a 0 B card reader at `/dev/sda` that `list-devices disk` returns first.
- **THP is already correct on Debian 12** (`madvise`). Assert it; do not tune it.
- **Build natively on a fleet node** — glibc is forward-incompatible.

---

## Log

- **2026-08-03** — Brainstormed. Initial design assumed GPU sharding.
- **2026-08-03** — Pivoted to CPU-only; 14 GB pooled VRAM too little.
- **2026-08-10** — Research across RPC internals, model performance,
  provisioning. Implementation plan written.
- **2026-08-11** — Batching research qualified the "multiplies seats" claim.
- **2026-08-11** — Reframed around data sovereignty and a two-stage deliverable.
- **2026-08-12** — **Node 1 built and Phase 0 executed on real hardware.**
  b10369 pinned (the `b8492` warning was stale). RPC gate passed on generation
  (−5.2%) but prefill cost −39.4%. Found `rpc-server` renamed upstream and the
  default RPATH non-relocatable — both would have failed only on workers.
- **2026-08-12** — **Two "settled" constraints turned out to be wrong.** The
  75% RAM rule cites a fixed syscall bug (F1); Model B does not fit the
  coordinator's **disk**, making disk the binding constraint (F16).
- **2026-08-12** — **The plan's TTFT measurement was measuring nothing** —
  0.015 s reported against a real 89 s (F17). The same bug is in Task 8.
- **2026-08-17** — Memory question settled: 4 × 32 GB across all four channels
  at rated speed; the 28.2 GB/s ceiling is the 4-core CPU, and MSRs confirm no
  BIOS lever remains (F12). An earlier half-population hypothesis was retracted.
- **2026-08-17** — **Sparse MoE reaches only 61% of bandwidth** (F24). Every MoE
  estimate was ~1.6× optimistic and was revised.
- **2026-08-17** — **Batching answered**: 1.79× at batch 4, collapse at 8.
  Replication beats it ~4× and beats sharding by a factor of N.
  **The architecture is now replication-first and N-agnostic.**
- **2026-08-17** — **Kimi K2 has the worst REPORTED hallucination rate of any
  model checked** (17.9%), against a project requirement of faithfulness.
  Model B re-opened; GLM-4.6 leads (F25).
- **2026-08-17** — **ik_llama.cpp A/B: +52% prefill, −14% generation, net +22%**
  end-to-end, output verified coherent (F27). Last open software lever, and it
  delivered.
- **2026-08-17** — Missing Link built through Task 12 (41 tests). Found and
  fixed a real race in the plan's job store (F20) and a silent empty-output
  failure with reasoning models (F21).

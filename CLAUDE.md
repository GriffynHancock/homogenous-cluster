# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **If this file was injected into your system prompt, that copy is a snapshot
> taken at session start and can be stale.** It has been rewritten mid-session
> before, and the stale copy carried two claims that `docs/FINDINGS.md` proves
> wrong. In a long session, or whenever a claim here matters, **re-read
> `CLAUDE.md` from disk** and treat `docs/FINDINGS.md` as outranking both.


## File index — read in this order

| File | What it is | Authority |
|---|---|---|
| `STATUS.md` | **Start here.** Current state, next tasks in order, blockers | current |
| `docs/FINDINGS.md` | What running this on hardware taught us, incl. what the plan got **wrong** | **outranks everything below** |
| `docs/measurements.md` | Every measured number. **No performance claim may be quoted from anywhere else** | authoritative for numbers |
| `network.md` | **Gitignored, site-specific.** IPs, node roles, ports, access. Read it; never commit it | current |
| `CLAUDE.md` | This file — the argument, conventions, standing constraints | see staleness note above |
| `docs/MODEL-SELECTION.md` | Which model to run and why; criteria derived from measurement | current |
| `docs/DESIGN-NOTES.md` | Analysed-but-not-built ideas, with numbers (expert parallelism, speculative decoding, replication, why-not-RAG) | current |
| `docs/EVALUATION.md` | Which datasets and faithfulness metrics to use, and why NOT to reproduce the hallucination leaderboard | current |
| `docs/REQUIREMENTS.md` | **What the operator actually asked for, in their words.** Outranks older "settled" decisions | current |
| `docs/AGENT-HARDENING.md` | Which agent operations are blocked/gated and why, and what a hook fundamentally cannot catch | current |
| `docs/UPSTREAM-PATCHES.md` | Corrections still to fold back into the plan and spec | current |
| `provisioning/` | `join-node.sh`, `setup.sh`, `distribute.sh`, `harden-ssh.sh`, `build-*.sh`, `nodes.env`, `preseed.cfg` | — |
| `cluster/` | `models.json` + `models.sh` (model index), `install-services.sh`, `rpc-server@.service` | — |
| `bench/` | `overhead-test.sh`, `node-bench.sh`, `two-node-smoke.sh` | — |
| `missing-link/` | The async job runner. Tests: `.venv/bin/python -m pytest tests/ -v` | — |
| `docs/superpowers/specs/` | Original design; which alternatives were rejected and why | **partly stale** |
| `docs/superpowers/plans/` | Original task-by-task plan | **stale — superseded by FINDINGS** |


## Homogenous Cluster

Turning idle organisational hardware into a private LLM cluster, for work that
legally cannot leave the building.

**Immediate deliverable: the cluster.** An **N-node** CPU-only llama.cpp cluster
doing real work on real sensitive documents. N is whatever the organisation has;
the reference fleet is 7, but **nothing in the design may assume 7.**

**Long-term deliverable: a Claude Skill** that lets an organisation without a
specialist do the same with whatever hardware it has. The cluster comes first
because the skill must dispense *measured* advice, not arithmetic. Do not start
building the skill until the cluster has produced numbers.


## North star: find the optimum first, then standardise backwards

**This fleet is a test environment, not a production deployment.** The goal is
not to stand up a cluster quickly — it is to **find the configuration that
actually works best on hardware like this, and only then work backwards into a
consistent, repeatable setup.**

That ordering has consequences for how work is done here:

- **Measure before standardising.** A setting that is merely plausible does not
  go into `setup.sh`. It goes into a benchmark first. Half the "settled"
  decisions in the original plan turned out to be wrong precisely because they
  were standardised before they were measured.
- **Expect to throw configurations away.** Building both `llama.cpp` and
  `ik_llama.cpp`, or holding two candidate models on disk, is not waste — it is
  the experiment. Keep alternatives installed side by side and A/B them on the
  same hardware.
- **Prefer reversible changes.** Separate prefixes, drop-in config files,
  additive manifests. If a change cannot be undone by deleting one file, ask
  whether it belongs yet.
- **Nodes may legitimately differ during this phase.** Divergence is a
  measurement opportunity — a node with more cores tells us something about how
  bandwidth scales. **Consistency is the deliverable at the end, not a
  constraint at the start.** The one exception is llama.cpp build version and
  ISA, which must match within any RPC shard group or it fails obscurely.
- **The output of this phase is a justified configuration**, where every setting
  traces to a measurement. That is what the skill later generates for other
  people's hardware, and it is why unmeasured advice would make the skill worse
  than nothing.

So: when a choice is open, **run the experiment rather than picking a default**,
and record the result in `docs/measurements.md`.


## The argument

**Data sovereignty is a hard constraint, not a preference.** Many Australian
organisations — health, legal, education, government, community services —
cannot send data offsite. It is statutory or contractual, and vendor assurances
do not move it.

The perverse result: the tasks that would benefit most obviously from AI are
exactly the ones touching protected material. So only a thin, uninteresting
slice of the workflow gets automated while the substantial work stays manual.

**On-prem infrastructure is the obvious answer and it rarely survives a
budget.** Acquisition is the small part. What kills it is the ongoing cost —
power, cooling, rack space, patching, monitoring, physical security, insurance,
and staff who can run it. Few organisations will fund that for a handful of
internal workflows.

**But those organisations usually already have the hardware.** Store rooms of
machines from refresh cycles and departed staff. Hardware as recent as **2019**
is useful, often with substantial RAM already installed — the resource that
actually matters. Acquisition cost is zero, and it is already inventoried,
depreciated, and inside the building.

So: **for an organisation with large volumes of text and modest security
requirements, pool the idle machines into a local cluster on a secure or
air-gapped network, and run the legally sensitive language work there.**
Generation is slow, but a summary that arrives overnight beats one that never
gets written.

## Target user and workloads

**Fits:** large volumes of text, genuine sovereignty constraints, *modest*
security requirements, and idle hardware. **Does not fit:** organisations with
high security requirements — see the security posture below.

Workloads, in order of maturity:

- **Document summarisation** — submit overnight, read in the morning.
- **Medium-horizon multi-step search and Q&A** — questions needing several
  passes over a corpus. Slow is fine; nobody is waiting at a prompt.
- **Report writing / drafting** from source material.

## Security posture

**The skill will not advise on security, deliberately.** A cluster holding an
organisation's sensitive documents is a **very high risk asset**. It belongs
either fully offline — reachable only over its own ethernet segment — or inside
a mature, properly segmented network.

Getting that right depends on obligations, existing controls, and risk appetite
that no tool can assess. So: ask about the network, state plainly that securing
and validating it is the organisation's responsibility, and otherwise care
about exactly one thing — **a fast, low-latency connection between machines.**

Do not write security guidance into this project. Do not imply the tooling
makes a network safe. Point at the requirement and move on.

**Faithfulness is a security property here.** These are legally sensitive
documents; a fabricated fact in a summary is a failure of the same order as a
leak. Model selection must weight reported hallucination rate above general
capability leaderboards. See `docs/MODEL-SELECTION.md`.

## Why old hardware works at all

Two technical facts carry the argument, both now measured on real hardware:

1. **Active params, not total params, determine speed.** Bytes read per token
   ≈ active params × bits-per-weight. Everything else is capacity. A 1T-param
   model with 32B active is tractable on system RAM; a 70B dense model is not.
   **This is why sparse MoE models are the enabling technology here** — they
   decouple capability (what you store) from speed (what you read).
2. **Generation is bandwidth-bound, prefill is compute-bound, and they behave
   completely differently.** Generation runs at ~99% of achievable memory
   bandwidth for dense models and **~61% for sparse MoE** (scattered expert
   gathers defeat prefetch). Prefill is limited by cores and ISA. On the
   reference hardware **prefill is ~79% of document wall-clock**, so it is the
   thing to optimise.

## Scaling: what actually changes as N grows

**Do not think in terms of "7 nodes". Think in terms of two numbers.**

- **S — shard group size.** How many nodes are needed to hold one copy of the
  model: `ceil(model_size / usable_RAM_per_node)`.
- **R — replication factor.** How many independent copies you can run:
  `floor(N / S)`.

**Aggregate throughput ≈ R × single-node throughput.** That is the whole
scaling story, and it makes the design N-agnostic.

| S | What you get | Notes |
|---|---|---|
| **S = 1** (model fits one node) | **R = N — linear scaling** | no RPC, no coordination, a node failure costs 1/N |
| 1 < S < N | R = floor(N/S) copies | RPC within each group; groups independent |
| **S = N** (model needs everything) | **R = 1 — no parallelism** | the frontier case; capacity, not speed |

**Consequences, measured:**

- **Sharding buys capacity, never speed.** Within a shard group nodes run
  sequentially, so utilisation is 1/S and RPC adds **−39% prefill / −5%
  generation**. `S` nodes ≈ 1 node with `S`× the RAM.
- **Replication buys speed, linearly.** Independent nodes, no RPC overhead, and
  the document workload is embarrassingly parallel (map-reduce chunks are
  independent).
- **So prefer the largest model with S = 1**, and reserve S > 1 for the single
  frontier model that genuinely cannot fit. **The size threshold at which S goes
  from 1 to 2 is the most consequential number in model selection** — crossing
  it costs a factor of N.
- **Batching is the weak third option.** On sparse MoE it gives only **1.79× at
  batch 4**, and **collapses at batch 8** (prefill −56%, total throughput below
  batch 1). Use `--parallel 4`; never 8.

### The 1 → 2 transition is where the risk lives

Almost everything that can go wrong appears when the second node arrives, and
nothing new appears at the tenth:

- RPC protocol enters the picture at all
- **Version, libc and ISA lockstep** start to matter (a mismatch is silent until
  it SIGILLs mid-graph)
- Upstream bugs triggered by **2+ RPC workers** become possible
- Duplicate `machine-id` / SSH host keys collide

**Test every one of these at N=2 before growing.** They are cheap to find with
two machines and expensive to find with ten.

## Missing Link

**Missing Link** is the async long-workload runner on top of the cluster.
Rather than a chat window where slowness is a defect, it is a job queue where
slowness is irrelevant — submit work, collect results later.

It is the centrepiece, not a nice-to-have: it converts "too slow to be useful"
into "fast enough for this class of work." It is also the prototype for the
skill's task-profile mechanism — each workload type is a plug-in with its own
prompts, chunking and evaluation.

**It must fan out across R endpoints, not one.** Under replication the queue's
job is to keep R independent servers busy. This is the main outstanding change.

**Cloud is not part of the story.** Do not frame local inference as a
preprocessing step that makes cloud safe, and do not propose hybrid
local/cloud architectures. The work happens entirely on hardware the
organisation already owns, and the data never leaves.

## The skill (later, not now)

Once the cluster produces measurements, package it as a Claude Skill that takes
a non-technical user from "we have a room of old computers" to a working
cluster:

1. **Assess** — inventory the machines, ask what the organisation wants to do,
   and answer straight, including "no, and here is what would be needed."
2. **Generate** — produce the actual provisioning scripts, configuration and
   model selection for *that* hardware and *that* workload. Not a tutorial.
3. **Operate** — a web UI for the people doing the work, plus an **agent
   appliance**: a single out-of-band machine that watches the cluster, keeps it
   patched, resumes failed jobs, and reports on whether it is actually working.
   The appliance is separate hardware by design — a monitor that shares the
   cluster's failure modes is not a monitor. **Confirmed the hard way on
   2026-08-17 (F36):** llama-server hung *alive* — accepting TCP, answering
   nothing, invisible to `Restart=always`. As the operator put it, the alternative
   is "the whole cluster just locking itself out of the inference that it needs to
   recover itself." An 8 GB laptop running `curl` is sufficient and sufficient is
   the point. **Triage and batching may live on-node; LIVENESS may not.**

Extensible in two directions, because neither the hardware nor the work is
uniform:

- **Hardware profiles** — CPU + RAM (this reference case), GPU clusters,
  high-CPU/low-RAM, GPU MoE offloading, and whatever accelerators turn up.
- **Task profiles** — summarisation, multi-step search and Q&A, drafting. Each
  is a skill extension with its own prompts, chunking strategy and evaluation.

**Every recommendation the skill makes must trace to a measurement in
`docs/measurements.md`.** A skill that confidently dispenses datasheet
arithmetic to a non-technical user is worse than no skill.

**The assessment must measure, not ask.** Three numbers decide everything and
none can be read off a spec sheet:

- **physical cores** (not `nproc` — SMT siblings hurt)
- **achievable memory bandwidth** (STREAM, not `channels × MT/s × 8`)
- **free disk on the coordinator** (salvaged machines are RAM-rich, disk-poor)

Direction recorded in `docs/superpowers/specs/2026-08-11-skill-direction.md`.
**Do not begin implementing it.** One thing to carry into Missing Link now,
though: keep prompts, chunking and evaluation separable from the queue and
worker. That seam becomes the task-profile interface later, and it is far
cheaper to preserve than to retrofit.

## Where you are running

**You are most likely running on node 1, the coordinator**, with real hardware
under you and root via sudo. You are the operator, not an advisor — run the
commands, read the output, record the numbers.

**Read `STATUS.md` first, then `docs/FINDINGS.md`.** STATUS records the current
phase and what is in flight. FINDINGS records what was learned by running this
on hardware, **including several things the plan and spec got wrong.** Keep both
updated — they are the handoff between sessions, and the next session may be a
cold start.

## Working on this repo

Work happens **on the coordinator**. Other nodes are reached over plain SSH on
LAN IPs. llama.cpp is built once on the coordinator and its binaries distributed
fleet-wide.

Standing constraints when writing anything that touches the cluster:

- **llama.cpp versions must match exactly across all nodes** or the RPC protocol
  mismatches. Never build per-node. Pin to a release tag; do not track `master`.
- **Never build with `GGML_NATIVE=ON` for a fleet.** It bakes in `-march=native`;
  an older-CPU node passes the version handshake, loads the model, then SIGILLs
  mid-graph. Build for a common ISA baseline and assert it in `distribute.sh`.
- **Binaries must be relocatable.** ggml builds shared libraries and
  `llama-server` is a stub; the default RPATH points into the build tree. Build
  with `CMAKE_INSTALL_RPATH='$ORIGIN'`, ship `*.so*`, and assert no binary
  references the source tree.
- **The RPC binary is `ggml-rpc-server`**, renamed upstream. Keep a `rpc-server`
  symlink.
- **Node provisioning must be idempotent.** Disks vary in size and type across
  the fleet, so the setup path is a Debian preseed plus a re-runnable
  `setup.sh`, not a disk image.
- **Memory sizing.** Pooled `(RAM − 1 GB/node) × 0.85`, and a per-node working
  limit of **≤75% of physical RAM**. The 75% figure is a **chosen safety
  margin**, not a hard limit — it covers page cache, KV growth and llama.cpp's
  overcommit OOM history (#22629). It is **not** traceable to #15055, which was
  a syscall-size bug and is fixed. Measure the real ceiling before treating it
  as binding.
- **`rpc-server -t` must be set to PHYSICAL cores, not `nproc`.** Measured:
  `-t 8` is 26% slower than `-t 4` on a 4c/8t CPU. Derive it with
  `lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l`.
- **Set `--parallel` explicitly to 4.** Leaving it unset silently means 4 slots;
  8 is worse than 1.
- **Keep `--ctx-shift` off** (the default). On, it silently evicts KV and
  quietly drops the start of the document.
- **Never pass `--advertise-routes`** to Tailscale — it would pull the RPC hot
  path onto WireGuard. RPC runs on raw LAN IPs.
- **The LAN is 100 Mb/s, not gigabit** (measured 93.8 Mbit/s, F28). Both NICs are
  gigabit-capable; the switch is the cap. This **inverts** F23's peer-over-internet
  model pull, and it means model distribution costs ~97 min per 65 GB per node.
- **Set `-c` so ONE CHUNK PLUS ITS OUTPUT FITS IN A SLOT.** `-c` is divided by
  `--parallel`, so `-c 16384 --parallel 4` gives **4096 tokens per slot** — less
  than `CHUNK_TOKENS` (4096) plus the wrapper plus `MAP_MAX_TOKENS` (1024). Now
  `-c 32768` for 8192/slot. A short test document never reveals this; a real one
  overflows. **Diagnostic:** the server logs `n_ctx_slot=N` per slot at startup —
  `journalctl -u llama-server@8080 | grep n_ctx_slot`. Check it against
  `CHUNK_TOKENS`, do not infer it from `-c`.

## Verification

There is no test suite for the cluster itself — verification is running the
command and reading the output. Do not report a step as done without having
seen its output.

**A faster build or config that changes output is not a win.** Any performance
change must be paired with a coherence check on real output before adoption.

Missing Link does have tests: `cd missing-link && .venv/bin/python -m pytest tests/ -v`
(75 tests as of 2026-08-17). **But a test count is not evidence of working
software:** 41 tests passed against a pipeline that had never processed a single
document (F34). Every defect since lived in the seam between our code and something
real — SQLite locking, the model's output shape, `finish_reason`, PDF bytes, a
wedged server. **Report what was exercised, not how many assertions ran.**

## Key decisions

**Hardware (node 1, measured 2026-08-17):** Xeon E5-1620 v4, **4 cores / 8
threads**, no AVX-512, **131.8 GB RAM** (4 × 32 GB DDR4-2400, all four channels
at rated speed), 477 GB NVMe. **Achievable memory bandwidth 28.2 GB/s** — only
37% of the quad-channel theoretical maximum, because **4 cores cannot saturate
their own memory bus.** Not a misconfiguration; uncore and energy-perf-bias are
already at maximum, so there is no BIOS lever.

Other nodes are **not yet characterised.** Do not assume they match. If a node
has more cores it will be faster at generation despite identical RAM, and
`--tensor-split` should then weight by **measured bandwidth**, not RAM.

Settled:

- **CPU + system RAM only.** GPUs are present for display and unused for
  compute. The Quadro P600's 2 GB cannot hold meaningful layers of any target
  model, so "GPU for prefill" is not executable with the hardware on hand.
- **llama.cpp RPC** for sharding, pinned to a release tag. Exo, prima.cpp and
  distributed-llama rejected — see the spec for the reasoning, and do not
  re-propose them.
- **`ik_llama.cpp` for the document workload.** Measured **+52% prefill, −14%
  generation** versus mainline — a net **+22% end-to-end**, because prefill
  dominates. Output verified coherent. Keep mainline built alongside; do not mix
  builds across a shard group.
- **Debian 12 headless**, scripted provisioning (not `dd` cloning — disks vary).
- **Tailscale for SSH and the web GUI only.** RPC mesh runs on raw LAN IPs.
- ~~**Open WebUI** as the chat frontend~~ — **NO LONGER SETTLED.** It is a *chat*
  frontend and this workload is explicitly asynchronous ("nobody is waiting at a
  prompt"), which is the same category error as adopting RAG-QA tools for
  summarisation. Missing Link's own job console is what is actually running. See
  `docs/DESIGN-NOTES.md` F and `docs/REQUIREMENTS.md`.
- **Map-reduce for long documents**, ~4K chunks with 10% overlap — decided on
  evidence. A larger context window does not fix "lost in the middle".

**Model selection is open and is now driven by faithfulness.** The previously
settled "Model B = Kimi K2" is **under review**: K2-Instruct has the worst
REPORTED hallucination rate (17.9%) of any model checked — Vectara
leaderboard, not verified here — against GLM-4.6 at
9.5% with identical active params and one third the disk. See
`docs/MODEL-SELECTION.md` and F25.

## Roles: orchestrator vs subagent

**READ THIS FIRST AND WORK OUT WHICH ONE YOU ARE.** Subagents inherit this whole
file, so it is addressed to both, and getting the role wrong is expensive in
exactly opposite directions.

**If you were launched by the Agent/Task tool, you are a SUBAGENT.** You are not
the orchestrator. Do the task you were given, end to end, yourself. **Do not
launch further agents**, do not re-plan the session, do not go and fix adjacent
problems you noticed. If you find something outside your brief, *report it* —
that is the orchestrator's to schedule. Report what you actually ran and saw.

**If you are the top-level session, you are the ORCHESTRATOR. Your scarce
resource is context, not time, and every file you read yourself is context you
cannot get back.** So:

- **Delegate the work. Do not do it yourself.** Implementation, merges, conflict
  resolution, benchmarks, forensics, research — all of it goes to an agent. The
  temptation is always to do "just this one small thing" inline because
  explaining it feels slower than doing it. That is the trap: doing it inline
  costs the whole file in context, permanently, and the explanation was going to
  be needed anyway.
- **What only you can do:** decide what the work IS, sequence it, resolve
  contention for the hardware, hold the standing constraints, judge whether a
  returned result is actually believable, and talk to the operator. Editing
  `STATUS.md`/`CLAUDE.md`/`FINDINGS.md` to record decisions is orchestrator work.
- **Read narrowly.** `STATUS.md`, `docs/FINDINGS.md` and the specific thing the
  operator asked about. Anything else, send an agent and take its summary.
- **Brief agents with the constraints, not just the task.** Point them at the
  exact files and findings. An agent that has to rediscover F35 wastes a session.
- **Never spend your own context proving an agent right.** Ask for the command
  output inline in its report and judge that.

## Conventions

- **Research before specifying or building.** Before committing to a tool,
  flag, or configuration, search for how others solved the same problem — GitHub
  issues, forum threads, writeups from people who built the same thing. The
  goal is to find the *specific* fixes and known failure modes, not general
  background.
- **Delegate research to Sonnet subagents** rather than running searches inline.
  Ask for conclusions plus source URLs, with CONFIRMED / REPORTED / INFERRED
  distinguished. Point them at specific repos, issues and files.
- **Fan out.** The default is parallel agents in separate git worktrees, up to
  about five at once, so work that does not contend for the same files or the
  same hardware runs concurrently. (This supersedes an earlier "one Sonnet agent
  at a time" rule, which was written before worktree isolation and was throttling
  throughput for no benefit.) The real constraints on fan-out are **file
  contention** — two agents editing `app.py` is a merge you will pay for later —
  and **hardware contention**: there is one `llama-server` per node and a
  benchmark needs it to itself.
- **Model tier by risk, not by size of task.** Opus for high-complexity or
  high-risk implementation — anything touching faithfulness, the completion
  guards, or the job store. Sonnet for everything else, which is most things.
- **Agent hygiene is enforced, not merely advised.** `.claude/settings.json`
  runs `PreToolUse` hooks (`.claude/hooks/cluster-guard.py`) that **block**
  `git add -A`, `git commit -a`, `pkill -f` and inline Python that does not
  compile, and **gate** cluster service control, `git push`, mutating SQL
  against the live job store, writes to `/opt/models`, and git working-tree
  operations in the live checkout. **If a hook blocks you, the hook is right and
  the command was wrong** — read its message and take the alternative it names.
  Never set `CLUSTER_OPS_CONFIRMED=1` on your own initiative; that prefix means
  the operator said yes. See `docs/AGENT-HARDENING.md`.
- **Enumerate paths; never stage by wildcard.** `git add <path> [<path>…]`,
  after reading `git status --porcelain`. `git add -A` once swept three agent
  worktrees in as embedded git repos, and pushed them.
- **Kill by unit or by PID, never by pattern.** `systemctl stop <unit>`, or
  `pgrep -f <pat>` → read the PIDs → `kill <pid>`. `pkill -f` matches the
  caller's own command line and has killed the agent's shell three times.
- **Assert row counts on destructive SQL.** Run the `SELECT count(*)` with the
  *identical* predicate first, print it, and stop if it is 0 or outside what you
  expected. A `substr(document,1,5)='%PDF'` off-by-one matched 0 rows and was
  caught only because someone looked. **No hook can catch this** — it is not a
  command pattern, it is a wrong predicate.
- **Read an API before calling it.** Missing Link is FastAPI, so
  `GET /openapi.json` is free and authoritative. Guessed form-field names cost
  three round trips of 422/405.
- **Never run git working-tree operations in the repo root itself.** It is
  `missing-link.service`'s `WorkingDirectory=`, so a merge there leaves a window
  in which any restart crash-loops the unit every 5 s. Merge in a worktree. Use
  `git -C <abs-path>` rather than `cd` — a `cd` that silently lands in the wrong
  worktree turns a merge into a no-op that reports "Already up to date", which
  happened three times in one session.
- **Quote commit messages with single quotes.** Backticks inside a double-quoted
  `-m` are command substitution: bash runs them and **silently deletes the text**
  from the message. Cost a sentence out of a commit that documented exactly this
  class of slip.
- Leave **15% memory headroom** in all model-fit calculations. Do not spec
  configurations that fit only marginally.
- **Performance claims must come from measurement on the hardware.** If a number
  is not in `docs/measurements.md`, it may not be quoted. When a cited source is
  load-bearing, re-read it — two "settled" constraints in this project turned
  out to be misread issues.
- **Time-to-first-token is a separate metric from tokens/sec** and matters more
  for document workloads. **Do not measure TTFT with `curl -w
  %{time_starttransfer}`** — it times HTTP headers and under-reports by orders
  of magnitude. Parse the SSE stream, or read `prompt eval time` from the server
  log. Vary the prompt between runs or you measure the prompt cache.

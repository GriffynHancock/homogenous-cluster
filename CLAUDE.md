# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Homogenous Cluster

Turning idle organisational hardware into a private LLM cluster, for work that
legally cannot leave the building.

**Immediate deliverable: the cluster.** A 7-node CPU-only llama.cpp RPC cluster
doing real work on real sensitive documents.

**Long-term deliverable: a Claude Skill** that lets an organisation without a
specialist do the same with whatever hardware it has. The cluster comes first
because the skill must dispense *measured* advice, not arithmetic. Do not start
building the skill until the cluster has produced numbers.

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

## Why old hardware works at all

Two technical facts carry the whole argument:

1. **Active params, not total params, determine speed.** Bytes read per token
   ≈ active params × bits-per-weight. Everything else is storage. A 1T-param
   model with 32B active is tractable on system RAM; a 70B dense model is not.
   **This is why sparse MoE models are the enabling technology here** — they
   decouple capability (what you store) from speed (what you read).
2. **Pooling buys capacity, not speed.** For one request, nodes run
   sequentially — 7 nodes ≈ 1 node with 7× the RAM. The cluster exists to hold
   a model no single machine could, not to run it faster.

The honest pitch is therefore not "slow chatbot" but **"a few seats, each slow,
running something the organisation could not otherwise touch at all."**

**Unresolved — do not assert the "few seats" half until measured.** It assumed
concurrent requests are cheap, which holds for dense models (measured ~5.75×
throughput from batch 1→32 on CPU) but **probably not for a very sparse MoE.**
Each token routes to its own experts, so batch B touches ≈ `min(B × top_k,
n_experts)` experts — bytes read grow roughly in step with tokens produced, and
throughput stays flat. The sparsity that makes a 550 GB model tractable at batch
1 is what stops batching helping. Run `llama-batched-bench` at `-np 1,2,4,8`
before claiming multiple seats anywhere.

## Missing Link

**Missing Link** is the async long-workload runner on top of the cluster.
Rather than a chat window where slowness is a defect, it is a job queue where
slowness is irrelevant — submit work, collect results later.

It is the centrepiece, not a nice-to-have: it converts "too slow to be useful"
into "fast enough for this class of work." It is also the prototype for the
skill's task-profile mechanism — each workload type is a plug-in with its own
prompts, chunking and evaluation.

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
   cluster's failure modes is not a monitor.

Extensible in two directions, because neither the hardware nor the work is
uniform:

- **Hardware profiles** — CPU + RAM (this reference case), GPU clusters,
  high-CPU/low-RAM, GPU MoE offloading, and whatever accelerators turn up.
- **Task profiles** — summarisation, multi-step search and Q&A, drafting. Each
  is a skill extension with its own prompts, chunking strategy and evaluation.

**Every recommendation the skill makes must trace to a measurement in
`docs/measurements.md`.** A skill that confidently dispenses datasheet
arithmetic to a non-technical user is worse than no skill.

Direction recorded in `docs/superpowers/specs/2026-08-11-skill-direction.md`.
**Do not begin implementing it.** One thing to carry into Missing Link now,
though: keep prompts, chunking and evaluation separable from the queue and
worker. That seam becomes the task-profile interface later, and it is far
cheaper to preserve than to retrofit.

## Where you are running

**You are most likely running on node 1, the cluster master**, with real
hardware under you and root via sudo. You are the operator, not an advisor —
run the commands, read the output, record the numbers.

**Read `STATUS.md` first.** It records the current phase, decisions made, open
questions, and what is in flight. Keep it updated as work proceeds — it is the
handoff document between sessions, and the next session may be a cold start.

Then follow `docs/superpowers/plans/2026-08-10-cluster-bringup.md` task by task.

Read the spec before proposing any implementation — it records not just what to
build but which architectures were rejected and why (GPU sharding, Exo,
prima.cpp, distributed-llama, `dd` cloning). Re-proposing them wastes a cycle.

## Working on this repo

Work happens **on the master node**. Workers are reached over plain SSH on LAN
IPs. llama.cpp is built once on the master and its binaries distributed
fleet-wide.

Standing constraints when writing anything that touches the cluster:

- **llama.cpp versions must match exactly across all nodes** or the RPC protocol
  mismatches. Never build per-node. Pin to a release tag; do not track `master`.
- **Node provisioning must be idempotent.** Disks vary in size and type across
  the fleet, so the setup path is a Debian preseed plus a re-runnable
  `setup.sh`, not a disk image.
- **Two memory constraints, both must hold.** Pooled `(RAM − 1 GB/node) × 0.85`,
  and a hard **per-node ≤75% of physical RAM** (llama.cpp #15055, unfixed —
  exceeding it aborts at runtime). At 128 GB/node the per-node rule binds first.
- **`rpc-server -t` defaults to half the cores.** Always set it from `nproc`.
- **Never pass `--advertise-routes`** to Tailscale — it would pull the RPC hot
  path onto WireGuard. RPC runs on raw LAN IPs.

## Verification

There is no test suite for the cluster itself — verification is running the
command and reading the output. Do not report a step as done without having
seen its output.

Missing Link does have tests: `cd missing-link && python -m pytest tests/ -v`
(28 tests once Task 12 is complete).

## Key decisions already made

**Hardware: 128 GB DDR4-2400 ECC per node, ~896 GB pooled.** Usable budget is
**~672 GB**, bound by the per-node 75% rule rather than pooled headroom.

**A single node holds 96 GB**, so the cluster is only justified above that. Two
models, and their comparison is the deliverable:

- **Model A — gpt-oss-120b (~63 GB, 5.1B active) on ONE node.** The speed
  reference: what one salvaged desktop does alone.
- **Model B — Kimi K2 Q4 (~550 GB, 32B active) across SEVEN.** The thesis: a
  model class the organisation could not otherwise touch.

Other settled decisions:

- **CPU + system RAM only.** GPUs are present for display and unused for
  compute. Revisit only if measured time-to-first-token proves unbearable, and
  then for prefill only — prefill is compute-bound, generation is not.
- **llama.cpp RPC.** Exo rejected on evidence: MLX is now its only engine,
  Linux CPU is "Planned" tier, zero CPU optimisation commits in 2,353, and no
  GGUF support. prima.cpp (no MoE) and distributed-llama (all-reduce per layer
  per token over gigabit) also rejected. See the spec for full reasoning.
- **Debian 12 headless**, scripted provisioning (not `dd` cloning — disks vary).
- **Tailscale for SSH and the web GUI only.** RPC mesh runs on raw LAN IPs;
  encryption on the per-token hot path is pure loss, and upstream explicitly
  warns against exposing RPC to any network.
- **Open WebUI** as the chat frontend, lightly skinned.
- **Map-reduce for long documents**, ~4K chunks with 10% overlap — decided on
  evidence, not preference. A larger context window does not fix "lost in the
  middle", and CPU prefill degrades ~58% from 512 to 32K context.

## Conventions

- **Research before specifying or building.** Before committing to a tool,
  flag, or configuration, search for how others solved the same problem — GitHub
  issues, forum threads, writeups from people who built the same thing. The
  goal is to find the *specific* fixes and known failure modes, not general
  background. Much of this work is re-treading paths others have already walked
  painfully; the point is to not repeat their debugging.
- **Delegate research to Sonnet subagents** rather than running searches inline.
  Ask for conclusions plus source URLs, with CONFIRMED / REPORTED / INFERRED
  distinguished. Point them at specific repos, issues and files — vague briefs
  come back with general background instead of the actual fix.
- **Run at most ONE Sonnet agent at a time** (up to two Haiku agents are fine).
  Do not fan out unless explicitly asked. Do your own work — spec edits, doc
  updates — while an agent runs, rather than launching another.
- Leave **15% memory headroom** in all model-fit calculations. Do not spec
  configurations that fit only marginally.
- Performance claims must come from measurement on the hardware, not
  arithmetic on datasheets.
- **Time-to-first-token is a separate metric from tokens/sec** and matters more
  for document workloads. Prefill is compute-bound; generation is
  bandwidth-bound. Always report both.

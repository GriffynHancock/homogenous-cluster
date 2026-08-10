# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Homogenous Cluster

A 7-node CPU-only LLM inference cluster built from surplus school hardware,
serving as a working demonstrator for a blog post about **repurposing idle
organisational compute for private, on-premises inference**.

## The argument

Organisations with data-sovereignty constraints — schools, clinics, councils,
legal practices — often cannot send sensitive data to hosted APIs. They also
often have a room full of decommissioned desktops. The claim is that those two
facts cancel out: pooled system RAM across obsolete machines can hold
frontier-class open-weights MoE models, and while generation is glacial compared
to cloud offerings, **a great many valuable workloads do not need to be fast.**

Two technical facts carry the whole argument:

1. **Active params, not total params, determine speed.** Bytes read per token
   ≈ active params × bits-per-weight. Everything else is storage. A 1T-param
   model with 32B active is tractable on system RAM; a 70B dense model is not.
2. **Pipeline sharding multiplies seats, not speed.** For one request, nodes run
   sequentially — 7 nodes ≈ 1 node with 7× the RAM. With concurrent requests,
   each node works on a different request's layers simultaneously. Aggregate
   throughput scales with node count; per-seat speed stays flat.

The honest pitch is therefore not "slow chatbot" but **"a few seats, each slow,
running something the organisation could not otherwise touch at all."**

## Missing Link

**Missing Link** is the demo we are building: an **asynchronous long-workload
runner** on top of the cluster. Rather than a chat window where slowness is a
defect, it is a job queue where slowness is irrelevant — submit work, collect
results later.

Missing Link is the centrepiece of the argument, not a nice-to-have. It is what
converts "too slow to be useful" into "fast enough for this class of work."

The workloads are:

- **Document summarisation** of sensitive records — submit overnight, read in
  the morning.
- **Report writing / drafting** where a multi-minute turnaround is fine.

**Cloud is not part of the story.** Do not frame local inference as a
preprocessing step that makes cloud safe, and do not propose hybrid
local/cloud architectures. The argument is that the work happens entirely
on hardware the organisation already owns, and the data never leaves.

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

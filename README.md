# Homogenous Cluster - Heterogeneous Future

**Turning the old computers an organisation already owns into a private LLM
cluster for the work it legally cannot send offsite.**

**Now:** connect seven unused office machines into a single cluster that does
real work on real sensitive documents.

**Later:** package what that teaches into a Claude Skill, so an organisation
without a specialist can do the same thing with whatever hardware it has.

---

## The problem

Many Australian organisations in health, legal, education, government, etc. cannot send many documents off site through the open internet. 
As a result, workflows involving AI like document summary and reasoning that would immediately benefit organisations are out of reach without significant spend on compute infrastructure or australian sovereign cloud services. These workflows are the low hanging fruit busywork that even people relatively against AI use can agree are beneficial, saving work hours.

## The solution

Repurpose decommissioned hardware that an organisation has used in previous installations (desktop workstations, laptops, etc.) to create local private inference appliances. That is to say a specialised compute cluster that allows the organisation to utilise these tools in their workflows. Things like document summary and document reasoning can be done overnight, so my thesis is that even 10 year old hardware would be beneficial to utilise. The goal here is not to use a chatbot, its to get LLMs to do slow work 24/7 that save work hours every day, preferably with $0 of additional hardware.

---

## The cluster — the immediate deliverable

**Seven Frankenstein's monster desktops — ~128 GB DDR4 ECC each, roughly 900 GB of pooled
RAM — wired into one machine that can run models no single one of them could
touch.** Fronted by **Missing Link**: an async job runner where slowness stops
being a defect. Submit documents, collect results later.

These machines are made of the parts of many other machines sitting in a storeroom, 
the cream of the crop components (if the crop is a dusty shelf). 
Acquisition cost:
zero. 
They are already inventoried, already depreciated, already inside the
building, just need them tested and tagged.

This is the actual build, not a demo of a future product. It should do real
work on real documents. The skill comes out of what I learn.

There are two ways of running, to prioritise a smarter model running across more nodes, 
or a lot of smaller models doing batch processing.
And the setup and workflows are hardware agnostic and scale dynamically as nodes are added, changed and removed.

## Running the cluster (experimental, not the normal high security setup, this is for other tinkerers to try this out before i finish it.)

Clone onto the **coordinator** — the node that will run `llama-server` — and
drive everything from there:

```bash
sudo apt update && sudo apt install -y git curl
git clone https://github.com/GriffynHancock/homogenous-cluster.git
cd homogenous-cluster
./bootstrap.sh          # deps, Node, Claude Code, hardware facts. Idempotent.
claude                  # then: "read STATUS.md and continue the plan"
```

**The coordinator should be whichever machine has the most free disk**, not
node 1 by convention. Only the coordinator needs a full copy of the model on
disk — worker nodes never read model files at all, they receive tensors over
the network and keep a local cache.

### Adding more nodes

Run this **on the coordinator** and paste its output into the new machine:

```bash
./provisioning/join-node.sh
```

It prints the exact commands with the coordinator's real SSH key and LAN IP
filled in — generated rather than documented, because a key copied by hand into
a README is wrong the moment anyone redeploys, and fails confusingly when it is.

Then, back on the coordinator:

```bash
ssh <new-ip> 'lscpu -p=Core,Socket | grep -v "^#" | sort -u | wc -l; free -m'
# characterise it FIRST -- do not assume nodes match. Core count is a
# bandwidth spec, not just a compute spec.

sudo ./provisioning/setup.sh <hostname>   # idempotent
./provisioning/distribute.sh              # asserts version, libc AND ISA
./cluster/install-services.sh
./bench/two-node-smoke.sh <new-ip>        # multi-worker RPC gate
```

## --- Below is for LLM's to read. ---

## Reading order

| File | What it gives you |
|---|---|
| `STATUS.md` | **Start here.** Current phase, next tasks, open questions, log |
| `docs/FINDINGS.md` | **Read second.** What running this on real hardware taught us — including several things the plan and spec got *wrong* |
| `docs/measurements.md` | Every measured number. No performance claim may be quoted from anywhere else |
| `CLAUDE.md` | The argument, conventions, standing constraints |
| `docs/MODEL-SELECTION.md` | Which model to run and why — criteria derived from measurement |
| `docs/DESIGN-NOTES.md` | Analysed-but-not-built ideas, with the numbers |
| `docs/UPSTREAM-PATCHES.md` | Corrections still to fold back into the plan and spec |
| `docs/superpowers/specs/` | Original design, and which alternatives were rejected and why |
| `docs/superpowers/plans/` | Task-by-task implementation — **now partly superseded by FINDINGS** |

The specs record rejected approaches — GPU sharding on 2 GB cards, Exo,
prima.cpp, distributed-llama, `dd` cloning — with the evidence for each.
Re-proposing them wastes a cycle.

**`FINDINGS.md` outranks the plan where they disagree.** The plan was written
before any hardware existed; several of its "settled" constraints turned out to
be misread sources, and one of its benchmark methods was measuring nothing at
all.

## What this becomes

Eventually, **a Claude Skill** that walks a non-technical user from "we have a
room of old computers" to a working cluster:

1. **Assess.** Inventory the available machines, ask what the organisation
   wants to do, and give a straight answer about whether the hardware can do it
   — including "no, and here is what would be needed."
2. **Generate.** Produce the actual provisioning scripts, configuration, and
   model selection for *that* hardware and *that* workload. Not a tutorial —
   the scripts.
3. **Operate.** Ship a web UI for the people doing the work, and an **agent
   appliance** — a single out-of-band machine from the pool that watches the cluster, keeps
   it patched, resumes failed jobs, and reports on whether it is actually
   working.

The skill is **extensible in two directions**, because neither the hardware nor
the work is uniform:

- **Hardware profiles** — CPU + RAM (the reference case), GPU clusters,
  high-CPU/low-RAM, GPU MoE offloading, and whatever accelerators turn up in a
  disposal pile.
- **Task profiles** — document summarisation, medium-horizon multi-step search
  and question answering, drafting. Each is a skill extension with its own
  prompts, chunking strategy, and evaluation.

## On security

**The skill does not advise on security, and deliberately so.**

A cluster holding an organisation's sensitive documents is a very high risk
asset. It belongs either fully offline — reachable only over its own ethernet
segment — or inside a mature, properly segmented network. Getting that right
depends on the organisation's obligations, existing controls, and risk
appetite, none of which a tool can assess.

So the skill asks about the network, states plainly that securing and
validating it is the organisation's responsibility, and otherwise concerns
itself with one requirement: **a fast, low-latency connection between
machines.** That is what the cluster needs. Everything else is the
organisation's to answer, ideally with someone qualified.

## Status

**Cluster:** **two nodes** provisioned, built, measured and serving. Phase 0
complete and the measurement gate passed. `llama.cpp` and `ik_llama.cpp` both
built and distributed fleet-wide, models fetched, and the async job runner
(Missing Link) processing real documents end to end — job queue, fan-out across
independent servers, resumable chunk-level progress, automatic retry on backend
failure, live per-chunk telemetry, per-workflow guidance, and section-level
citations back to source spans.

**What measurement changed.** Several things the plan treated as settled did not
survive contact with hardware:

- **Replication beats sharding by a factor of N.** Sharding one model across
  every node buys capacity, not speed — nodes run sequentially, so utilisation
  is 1/S. Running an independent copy per node scales linearly. Measured at
  **~1.8× on two nodes, about 90% of linear.** The architecture is
  replication-first.
- **Sparse MoE reaches only ~61% of memory bandwidth**, against ~99% for dense
  models. Every MoE performance estimate was ~1.6× optimistic.
- **`ik_llama.cpp` was adopted on a +22% end-to-end win, and then dropped.** It
  fatal-errors on the *fifth* request of any `--parallel 4` job — a 100% failure
  rate on any document longer than four chunks. The +22% was measured with
  `llama-bench`, which issues one sequence. **A benchmark that does not reproduce
  the deployment's concurrency is not a benchmark of the deployment.**
- **Chunk size does not matter for quality, and matters a great deal for
  wall-clock.** 1024-token chunks cost **1.85×** the wall-clock of 4096 on the
  same document. The curve bottoms out at 4096 and climbs again — the inherited
  default turned out to be right, for a reason nobody had checked.
- **A faithfulness classifier's reliability degrades with the length of the
  evidence you give it.** A two-model ensemble whose disagreement caught *every*
  error on short documents dropped to precision 0.75 / recall 0.43 when the same
  claims were embedded in production-sized chunks, and began making silent
  agreeing errors. It was not deployed.
- **Faithfulness now leads model selection.** The originally chosen frontier
  model has the worst reported hallucination rate of any candidate checked,
  against a project whose whole premise is legally sensitive documents.

The pattern behind most of these is the same, and it is the thing worth carrying
away: **almost every real defect was an instrument confidently reporting on the
wrong thing** — a benchmark of the wrong configuration, a health check queued
behind the work it was checking, a validation at the wrong scale. None were
caught by more tests. All needed the real thing, at the real size, under real
conditions.

**A benchmark corpus of real public documents now exists** — legislation, NIST
standards, and regulator determinations, not the two long narrative texts every
earlier measurement had to make do with — so the project can finally test claims
against material that resembles what it's actually for, instead of what happened
to have been uploaded.

**The faithfulness cascade caught a real fabrication in a real summary**, not a
constructed test case: a reduce step asserted a death year that was present in
none of the source chunks and none of the source document — historically
correct for the person named, which is exactly what made it dangerous, and
exactly why the check that caught it was a plain number-in-source-span test,
not a model asked to judge itself.

**Chunk size is now measured, not inherited.** The 4096-token default the
pipeline already used turns out to be the actual optimum — wall-clock is
U-shaped around it, and raising it further costs real time even when the extra
room goes unused.

Full detail in `docs/FINDINGS.md`; every number in `docs/measurements.md`.

**Skill:** not started, and deliberately so — it gets written once the cluster
has produced real measurements to base its advice on. It now has some.

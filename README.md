# Homogenous Cluster

**Turning the old computers an organisation already owns into a private LLM
cluster — for the work it legally cannot send offsite.**

**Now:** build a 7-node cluster that does real work on real sensitive documents.

**Later:** package what that teaches into a Claude Skill, so an organisation
without a specialist can do the same thing with whatever hardware it has.

The cluster comes first, and not only for sequencing reasons — a skill that
dispenses unmeasured advice would be worse than no skill at all. Everything it
eventually recommends should trace back to something measured here.

---

## The problem

Plenty of Australian organisations cannot send their data offsite. Health,
legal, education, government, community services — the constraint is statutory
or contractual, not a preference, and no amount of vendor assurance moves it.

That produces a strange outcome. The tasks that would benefit most obviously
from AI — summarising case files, searching years of correspondence, drafting
from source documents — are exactly the tasks that touch protected material.
So for some organisations, only a thin, uninteresting slice of the workflow can
be automated, while the bulk of it stays manual because the data cannot leave.

The usual answer is on-premises infrastructure. It rarely survives contact with
a budget. Acquisition is the small part: what kills it is the *ongoing* cost —
power, cooling, rack space, patching, monitoring, physical security, insurance,
and staff who know how to run it. Very few organisations will sign up for that
to serve a handful of internal workflows.

## The observation

Those same organisations usually have a store room full of recent computers.
Desks cleared during a refresh cycle, laptops from departed staff, servers
replaced but not disposed of. **Hardware as recent as 2019 is useful here** —
often with substantial RAM already installed, which is the resource that
actually matters.

The acquisition cost is zero. The hardware is already inventoried, already
depreciated, already inside the building.

So: **for an organisation with large volumes of text and modest security
requirements, why not pool idle machines into a local cluster on a secure or
air-gapped network, and run the legally sensitive language work there?**

Slow is acceptable. A summary that arrives overnight is still enormously
faster than one that never gets written because nobody had the hours.

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

---

## The cluster — the immediate deliverable

Seven surplus desktops, ~128 GB DDR4 ECC each, pooled to hold a model far
larger than any one of them could, fronted by **Missing Link**: an async job
runner where slowness stops being a defect. Submit documents, collect results
later.

This is the actual build, not a demo of a future product. It should do real
work on real documents. The skill comes out of what it teaches.

Two technical facts do most of the work:

1. **Active parameters set speed; total parameters set capability.** Bytes read
   per token ≈ active params × bits-per-weight; everything else is storage.
   A sparse MoE model with 32B active parameters is tractable on system RAM
   even at 550 GB total. A 70B dense model is not.
2. **Pooling buys capacity, not speed.** For a single request the nodes run in
   sequence, so seven machines behave like one machine with seven times the
   RAM. The cluster exists to hold what one machine cannot.

## Running the cluster

Clone onto node 1 (the master) and drive it from there:

```bash
sudo apt update && sudo apt install -y git curl
git clone https://github.com/GriffynHancock/homogenous-cluster.git
cd homogenous-cluster
./bootstrap.sh          # deps, Node, Claude Code, hardware facts. Idempotent.
claude                  # then: "read STATUS.md and continue the plan"
```

## Reading order

| File | What it gives you |
|---|---|
| `STATUS.md` | **Start here.** Current phase, decisions, open questions, log |
| `CLAUDE.md` | The argument, conventions, standing constraints |
| `docs/superpowers/specs/` | Design, and which alternatives were rejected and why |
| `docs/superpowers/specs/2026-08-11-skill-direction.md` | The long-term skill (direction only, not started) |
| `docs/superpowers/plans/` | Task-by-task implementation |
| `docs/measurements.md` | Every measured number (created during Task 1) |

The specs record rejected approaches — GPU sharding on 2 GB cards, Exo,
prima.cpp, distributed-llama, `dd` cloning — with the evidence for each.
Re-proposing them wastes a cycle.

## Status

**Cluster:** planning complete, awaiting hardware. This is the current work.

**Skill:** not started, and deliberately so — it gets written once the cluster
has produced real measurements to base its advice on.

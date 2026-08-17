# Homogenous Cluster - Heterogeneous Future

**Turning the old computers an organisation already owns into a private LLM
cluster — for the work it legally cannot send offsite.**

**Now:** connect seven unused office machines into a single cluster that does
real work on real sensitive documents.

**Later:** package what that teaches into a Claude Skill, so an organisation
without a specialist can do the same thing with whatever hardware it has.

The cluster comes first, and not only for sequencing reasons — a skill that
dispenses unmeasured advice would be worse than no skill at all. Everything it
eventually recommends should trace back to something measured here.

---

## The problem

Many Australian organisations in health, legal, education, government, and community services cannot send sensitive data offsite due to statutory or contractual constraints. Yet the tasks that would benefit most from AI—summarising case files, searching correspondence, and drafting from source documents—are precisely those involving protected data, leaving much of the workflow manual.

## The observation

On-premises infrastructure is often prohibitively expensive to operate, with ongoing costs for power, cooling, security, maintenance, and specialist staff. But many organisations already have unused computers from recent refresh cycles; pooling this hardware into a secure or air-gapped local cluster could provide enough capacity to run language workloads without sending data elsewhere. Slow is acceptable when the alternative is work that never gets done.

---

## The cluster — the immediate deliverable

**Seven surplus desktops — ~128 GB DDR4 ECC each, roughly 900 GB of pooled
RAM — wired into one machine that can run models no single one of them could
touch.** Fronted by **Missing Link**: an async job runner where slowness stops
being a defect. Submit documents, collect results later.

Every one of those machines was sitting in a store room. Acquisition cost:
zero. They are already inventoried, already depreciated, already inside the
building.

This is the actual build, not a demo of a future product. It should do real
work on real documents. The skill comes out of what it teaches.

Three technical facts do most of the work. All three are now **measured on the
hardware**, not taken from datasheets:

1. **Active parameters set speed; total parameters set capacity.** Bytes read
   per token ≈ active params × bits-per-weight; everything else is storage.
   A sparse MoE model with 32B active parameters is tractable on system RAM
   even at 550 GB total. A 70B dense model is not. This is also why *newer and
   bigger* can be actively worse — a 2.8T model with 104B active runs ~3×
   slower than a 1T model with 32B active.
2. **Pooling buys capacity, not speed.** Split one model across the fleet and
   the machines run in sequence per request. Seven machines behave like one
   machine with seven times the RAM. That is the point: **it exists to hold
   what one machine cannot.**
3. **But running the same model on every machine buys speed, linearly.** When a
   model does fit on one box, seven independent copies serve seven jobs at once
   — and summarising a long document is dozens of independent chunk summaries,
   so it parallelises perfectly. Measured: replication ≈ 7×, batching 1.79×,
   pooling 1×.

So the fleet has **two gears**. Pool the machines to reach a model nothing else
in the building could run; replicate across them to chew through volume. Which
gear you are in depends on one number — whether the model fits on a single node.

**The design does not assume seven.** Two values describe any fleet: `S`
machines per copy (`ceil(model_size / usable_RAM_per_node)`) and `R` independent
copies (`floor(N / S)`), giving aggregate throughput ≈ `R ×` single-node. Seven
is what this organisation had in the cupboard. The same build works with three,
or twelve.

## Running the cluster

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

**The 1 → 2 transition is where all the risk lives.** RPC only enters the
picture at two nodes; version, libc and ISA lockstep only start to matter then;
upstream bugs triggered by two or more workers only become possible then. Test
all of it at two machines — nothing new appears at the tenth.

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

**Cluster:** first node fully provisioned, built and measured. Phase 0 complete
— the measurement gate passed. `llama.cpp` and `ik_llama.cpp` both built and
verified, models fetched, and the async job runner (Missing Link) built and
tested. Second node not yet joined.

**What measurement changed.** Several things the plan treated as settled did not
survive contact with hardware:

- **Replication beats sharding by a factor of N.** Sharding one model across
  every node buys capacity, not speed — nodes run sequentially, so utilisation
  is 1/S. Running an independent copy per node scales linearly, and the document
  workload is embarrassingly parallel. The architecture is now
  replication-first.
- **Sparse MoE reaches only ~61% of memory bandwidth**, against ~99% for dense
  models. Every MoE performance estimate was ~1.6× optimistic.
- **`ik_llama.cpp` is +52% on prefill and −14% on generation** — a net +22% for
  document work, since prefill dominates. That explains the split evidence
  online: both camps are right, and which matters depends on your workload.
- **Faithfulness now leads model selection.** The originally chosen frontier
  model has the worst measured hallucination rate of any candidate checked,
  against a project whose whole premise is legally sensitive documents.

Full detail in `docs/FINDINGS.md`; every number in `docs/measurements.md`.

**Skill:** not started, and deliberately so — it gets written once the cluster
has produced real measurements to base its advice on. It now has some.

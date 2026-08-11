# Cluster Builder Skill — Direction

**Date:** 2026-08-11
**Status:** Direction only. **Do not begin implementation.**

This records the long-term deliverable so it is not lost. The immediate work is
the cluster (`2026-08-03-homogenous-cluster-design.md`), and the skill must not
be started before that cluster produces measurements — a skill that dispenses
datasheet arithmetic to a non-technical user is worse than no skill.

## What it is

A Claude Skill taking a non-technical user from *"we have a room of old
computers"* to a working private LLM cluster.

### 1. Assess

Inventory available machines, ask what the organisation wants to do, and answer
straight — **including "no, and here is what would be needed."** A skill that
only ever says yes is a sales tool.

Inputs: machine count, RAM per machine, CPU generation, network between them,
intended workload, expected concurrent users.

Outputs: feasible / not feasible, which model class fits, expected throughput
and time-to-first-token, and the binding constraint (almost always memory
bandwidth or the per-node RAM ceiling).

The assessment logic must derive from `docs/measurements.md`. Where a
configuration has not been measured, say so rather than extrapolating.

### 2. Generate

Produce the actual artifacts for *that* hardware and *that* workload — preseed,
`setup.sh`, systemd units, computed `--tensor-split`, model selection and quant,
serving flags. Not a tutorial; the scripts.

### 3. Operate

- **Web UI** for the people doing the work. Job submission, status, results.
- **Agent appliance** — a single out-of-band machine that watches the cluster,
  keeps it patched, resumes failed workloads, and reports on whether it is
  actually working.

**The appliance is separate hardware by design.** A monitor sharing the
cluster's failure modes is not a monitor: if a node dies, the thing telling you
so must not be on that node. It is also the natural place for an agent loop,
since it can act on the cluster without being part of it.

## Extension mechanism

Neither the hardware nor the work is uniform, so the skill extends in two
independent directions.

**Hardware profiles** — how to shard and serve on a given class of machine:

- CPU + system RAM (the reference case)
- GPU clusters
- High-CPU / low-RAM
- GPU MoE offloading (`--n-cpu-moe`, `--override-tensor`)
- Unusual accelerators as they appear in disposal piles

**Task profiles** — a workload's prompts, chunking strategy and evaluation:

- Document summarisation (map-reduce; the reference case, built as Missing Link)
- Medium-horizon multi-step search and Q&A over a corpus
- Report writing / drafting from source material

Missing Link is the prototype for this mechanism. When building it, keep
prompts, chunking and evaluation separable from the queue and worker — that
seam is what later becomes the task-profile interface.

## Security is explicitly out of scope

The skill **does not advise on security.** A cluster holding an organisation's
sensitive documents is a very high risk asset, belonging either fully offline on
its own ethernet segment or inside a mature, properly segmented network. Which
of those is appropriate depends on obligations, existing controls and risk
appetite that no tool can assess.

The skill therefore:

- **asks** about the network topology,
- **states** that securing and validating it is the organisation's
  responsibility, ideally with someone qualified,
- **requires** only one thing: a fast, low-latency connection between machines.

Do not add security guidance later "to be helpful." Implying the tooling makes a
network safe would be actively harmful to exactly the users this targets.

## Open design questions

Not to be answered now — recorded so they are not rediscovered later.

- How is hardware inventory collected from a non-technical user? A script they
  run on each machine, or an interview?
- How does the skill express uncertainty about unmeasured configurations without
  being useless?
- Does the agent appliance need its own model, or does it drive the cluster?
- What is the minimum viable hardware profile — at what point does the skill say
  "this will not work, do not bother"?
- How are task profiles distributed and versioned?

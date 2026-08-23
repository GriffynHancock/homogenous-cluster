# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **If this file was injected into your system prompt, that copy is a snapshot
> taken at session start and can be stale.** Re-read `CLAUDE.md` from disk
> whenever a claim here matters, and treat `docs/FINDINGS.md` as outranking
> both copies.
>
> **This has happened twice, and both times the snapshot was concretely wrong:**
>
> - **2026-08-17.** A cold-start agent was served a pre-rewrite snapshot still
>   asserting *"a hard per-node ≤75% of physical RAM (llama.cpp #15055,
>   unfixed)"* and *"`rpc-server -t` defaults to half the cores. Always set it
>   from `nproc`."* **F1 refutes the first** — #15055 was a syscall-buffer-size
>   bug, it is CLOSED not "unfixed", and it never implied a percentage-of-RAM
>   rule. **F10 refutes the second** — `-t` must be PHYSICAL cores, and `nproc`
>   is measurably slower on an SMT CPU. Both are corrected on disk below.
> - **2026-08-18, live during the cold-start test that produced this note.**
>   The injected copy said *"Run at most ONE Sonnet agent at a time... do not
>   fan out"*, while the on-disk "Conventions" section below says the opposite:
>   **fan out, up to about five at once, in separate git worktrees.**
>
> **Two self-tests. Run both against the copy you are reading.**
>
> 1. Scroll to "Conventions". If it says one agent at a time, your copy is stale
>    and nothing else in it can be trusted either — re-read the file from disk.
> 2. Scroll to "Conventions" again. If it says **"Agent hygiene is enforced, not
>    merely advised"**, your copy predates 2026-08-23 and **F55**, and it is
>    asserting something that was never true: the `PreToolUse` hooks have never
>    blocked anything, in this repo's entire history.
>
> **And a third failure mode, worse than staleness, recorded 2026-08-23:** this
> file was **wrong on disk**, not merely wrong in a snapshot. It claimed the
> agent-hardening hooks were enforced for roughly six days without anyone
> issuing one command the guard claims to block and watching it fail. A
> snapshot can only be as good as the file; **`docs/FINDINGS.md` outranks both,
> and a claim in here that no finding backs is a claim nobody has tested.**


## File index

**Read these six, in this order. They are the whole orientation path.**

| File | What it is | Authority |
|---|---|---|
| `STATUS.md` | **Start here.** What is running right now, the next task, blockers. Its **at-a-glance table**, near the top, is designed to carry every fact you need before touching anything — including the commands to check what is currently true, since a hand-edited status line goes stale the moment a concurrent session changes something. The rest of the file (over 1000 lines) is detail, not a second orientation pass you are expected to read cold | current |
| `docs/FINDINGS.md` | Numbered findings from running this on hardware, incl. what the plan got **wrong**. **Indexed at the top** — scan the index, read the ones that touch your task | **outranks every other file** |
| `docs/measurements.md` | Every measured number. **No performance claim may be quoted from anywhere else** | authoritative for numbers |
| `network.md` | **Gitignored, site-specific.** IPs, node roles, ports, access. Read it; never commit it | current |
| `docs/REQUIREMENTS.md` | **What the operator actually asked for, in their words.** Outranks older "settled" decisions | current |
| `CLAUDE.md` | This file — the argument, conventions, standing constraints | see the staleness note above |

**Everything below is looked up when you need that subject. It is not part of
orientation and you are not expected to have read it.**

### Working directories

| Path | What is in it |
|---|---|
| `provisioning/` | `join-node.sh`, `setup.sh`, `distribute.sh`, `harden-ssh.sh`, `build-*.sh`, `nodes.env`, `preseed.cfg` |
| `cluster/` | `models.json` + `models.sh` (model index), `install-services.sh`, `install-watchdog.sh`, the `llama-server@` / `rpc-server@` / `missing-link` / watchdog units |
| `bench/` | `overhead-test.sh`, `node-bench.sh`, `two-node-smoke.sh`, `replication-bench.sh`, `chunk-size-bench.sh` |
| `missing-link/` | The async job runner. Tests: `cd missing-link && .venv/bin/python -m pytest tests/ -q` |

### Project-level references

| File | Read it when |
|---|---|
| `README.md` | you want the public framing — it is what a GitHub visitor reads first |
| `docs/CHANGELOG.md` | you need the session-by-session merge history that used to sit at the top of `STATUS.md` |
| `docs/UPSTREAM-PATCHES.md` | folding corrections back into the plan and spec |
| `docs/MODEL-SELECTION.md` | choosing, or defending, which model to run |
| `docs/DESIGN-NOTES.md` | tempted by expert parallelism, speculative decoding, replication or RAG — analysed with numbers, not built |
| `docs/AGENT-HARDENING.md` | a `PreToolUse` hook blocked you, or you are changing what is blocked/gated — **read F55 first: the layer this file describes has never actually fired** |
| `docs/superpowers/specs/` | checking whether an alternative was already rejected, and why — **partly stale** |
| `docs/superpowers/plans/` | historical task-by-task plan — **stale, superseded by FINDINGS** |

### Subject research and analysis — open the one matching what you are about to do

| File | Subject |
|---|---|
| `docs/EVALUATION.md` | which datasets and faithfulness metrics to use, and why NOT to reproduce the hallucination leaderboard |
| `docs/corpus-selection.md` | ranked shortlist of public documents structurally matching real work product, licences checked per source |
| `docs/chunking-research.md` | whether and how document splitting matters for map-reduce summarisation — **its §4 splitter conclusion is superseded by F48**, and the way it went wrong is the more useful part |
| `docs/chunk-boundary-measurement.md` | how often a chunk cut severs a qualifying clause pair on the real corpus — **every figure in it came from the OLD splitter and is not comparable; it carries a banner saying so** (F45, F48) |
| `docs/citation-research.md` | what an attributed summary should look like; the design behind the reduce step's section-level citations |
| `docs/faithfulness-cascade.md` | deterministic checks (numbers/entities in span) that decide before any classifier is asked |
| `docs/audit-ledger.md` | mapping a summary against its source into a machine-readable ledger of matches/misses |
| `docs/audit-production-scale.md` | whether that ledger's classifier survives production-length (4096-token) chunks — it does not (F41) |
| `docs/two-scope-and-entity-index.md` | two-scope hard checking (same claim vs. same entity) and a canonical entity index |
| `docs/minicheck-spike.md` | does MiniCheck run fast enough on this hardware, and does it survive negation |
| `docs/watchdog-research.md` | the multi-node, out-of-band watchdog design — liveness, per-service signals |
| `docs/market-research.md` | what office workers actually use for document summarisation, and whether Missing Link's shape matches |
| `docs/existing-pipeline-audit.md` | whether an off-the-shelf NLP library should replace any hand-rolled component — one ADOPT, three KEEP-OURS, one unmeasured (F48) |
| `docs/comfyui-feasibility.md` | the teaching playground's image-generation half — why CPU diffusion is compute-bound the wrong way for this fleet (F54) |
| `docs/n8n-feasibility.md` | the teaching playground's automation half — licence, isolation, and the local-LLM integration (F54) |
| `docs/distributed-playground.md` | can either of those use more than one machine — ComfyUI third-party only and it cannot improve latency, n8n yes via first-party queue mode, plus what an LLM load balancer in front of the fleet actually buys |
| `docs/teaching-labs.md` | the student lab exercises for the cybersecurity course, sized against the measured token budget (F58) — **design only, nothing installed or run** |
| `docs/upstream-ik-2186-draft.md` | the drafted, **not yet filed**, upstream report of the ik_llama.cpp multi-slot fatal error (F40) |

## Homogenous Cluster

Turning idle organisational hardware into a private LLM cluster, for work that
legally cannot leave the building.

**Immediate deliverable: the cluster.** An **N-node** CPU-only llama.cpp cluster
doing real work on real sensitive documents. N is whatever the organisation has;
the reference fleet is 7, but **nothing in the design may assume 7.** **N is 3
today** — nodes 1, 2 and 3 provisioned and characterised. Check what is actually
running from `STATUS.md`'s at-a-glance table, not from this sentence.

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


## The model is one component, not the system

**The operator's framing, and it should shape every design decision here:** think of
this as an NLP pipeline. The LLM is a *model*. It can be made to emit things that
are simply **inputs for scripts**. It needs a great deal of software around it to
manage it. It is one part of the system — one that happens to also be able to act
as a junior engineer on its own plumbing.

That is not a metaphor, it is a build rule, and it decides arguments:

- **Prefer deterministic code to model judgement wherever the work is computable.**
  A number either appears in the source span or it does not; that is `in`, not
  inference. Ask the model for the thing only it can do — reading prose — and let
  code do arithmetic, lookup, matching and bookkeeping.
- **Never ask the model where something came from.** Hand it a label and ask it to
  repeat the label; resolve the label to a span in code. Asking a model for a
  location scores ~38% on the *easier* task of merely validating one.
- **An attribution is not a verification.** Observed on real output (F46): all 7
  citation markers in a real job resolved CORRECT, to the right chunk, with every
  number matching inside its own cited span — **and the summary's first sentence
  was still false**, having merged two true facts from that span (an author and
  his guru) into one wrong one. Correct citation, correct span, false claim.
  Nothing was fabricated and nothing was misattributed, so no provenance check
  can catch it.
- **When output parsing reports 0% compliance, suspect the parser before the
  model.** The same job emitted its markers with U+202F NARROW NO-BREAK SPACE,
  not ASCII space. A tolerant `\s` in `_SECTION_MARKER_RE` is the only reason all
  seven resolved; **a stricter literal-space regex would have dropped 7 of 7
  valid citations and reported "the model ignored the instruction"** — a total,
  confident, wrong conclusion about the model caused by one invisible character
  in our own code.
- **Model output is a protocol, not an answer.** It gets parsed, validated and
  refused — which is why `extract_content` raises on empty and on truncated text,
  why an invented `[Section 47]` is dropped rather than rendered, and why an
  unrecognised failure is permanent rather than retried.
- **Measure the model from outside as well as asking it.** `/health` is the
  server's opinion of itself, delivered through the queue it is reporting on;
  `Restart=always` trusts the process; a port check trusts the socket — and F40
  showed forked abort children inherit that socket, defeating all three at once.
  Progress, measured externally, is the only signal that cannot be faked.
  **Treat disagreement between the model's self-report and an external measurement
  as its own fault signal.**
- **A cheap deterministic check that resolves most cases beats an expensive
  probabilistic one that resolves all of them.** Escalate to the classifier only
  where the cheap signals cannot decide, and label every signal by kind so a reader
  can see *why* something was flagged.

**The corollary for the skill:** what is being packaged is not "an LLM for
documents". It is the software that makes one usable — the queue, the chunker, the
guards, the watchdog, the provenance, the checker. The model is swappable. The
scaffolding is the product.

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

### That rule is about the CLUSTER'S OWN posture. It does not forbid the teaching material.

**Added 2026-08-23, because the two are easy to conflate and an agent could
refuse the wrong thing.** The fleet is now also becoming a **cybersecurity
teaching playground** — ComfyUI and n8n for roughly 20 students on the DMZ LAN
(F54, `docs/comfyui-feasibility.md`, `docs/n8n-feasibility.md`). Hold the
distinction explicitly:

- **Still forbidden:** writing hardening advice, network-security guidance, or
  any claim that this tooling makes a network safe. That is the organisation's
  responsibility and no tool can assess it.
- **Not forbidden, and actively wanted:** building the lab — deploying the
  playground services, writing teaching material about how these systems fail
  and how AI can be misused, and reporting plainly what is exposed. **Refusing
  the lab work on the strength of the paragraph above would be a misreading.**

**What is open on the LAN right now, stated as fact rather than advice, because
students are about to be on it:**

- **`llama-server` on `:8080` is unauthenticated.** Any host on the LAN can
  spend the cluster's inference directly, and `--parallel 4` means four
  concurrent students can starve the queue that Missing Link is trying to drain.
- **ComfyUI has no authentication at all** — unauthenticated `POST /queue`,
  `/interrupt`, `/free`, `/history`; any student can wipe everyone's work from a
  browser address bar, no exploit required. Custom nodes are arbitrary Python
  executed at startup, with real unauth-RCE CVEs in the ecosystem.
- **n8n's task runners default to `N8N_RUNNERS_MODE=internal`**, which n8n's own
  docs call "insecure by design" — anyone who can edit a workflow can read the
  database, encryption key, stored credentials and environment variables.
- **Missing Link WAS unauthenticated and is no longer** — see below. It was
  bound to `0.0.0.0:8000` exposing corpus deletion; that predated the playground
  request and is the reason the credential exists.

**Placement is decided by F44, not by preference: the playground services belong
on node 3, not on an inference node.** ComfyUI holds all cores for its entire
runtime, `nice` is not a mitigation (F44 tested exactly that), and n8n's Code
node makes it CPU-bound on demand.

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
  sequentially, so utilisation is 1/S. `S` nodes ≈ 1 node with `S`× the RAM.
- **What RPC actually costs, decomposed (F50), because two contradictory
  numbers sat in this repo for weeks and BOTH were right:** the **RPC protocol
  alone is −5.8% generation / −39.1% prefill** (loopback, which is what F14
  measured); putting the 100 Mb wire in the path costs **a further −35.4%
  generation**; adding a second RPC device costs **−11.4%** on top. So −5% and
  −47% are the same system measured at different topologies.
- **The generation penalty scales with `n_vocab`, NOT with model size, so it
  never amortises.** The device holding the output layer ships the full logit
  vector every token — 151936 × f32 = 593.5 KiB for Qwen3 — and a bigger model
  does more compute per token while returning *the same* vector. **The standing
  guess that a larger model would amortise RPC away is wrong and is corrected in
  `docs/measurements.md`.** Prefill is immune for a structural reason: a batch
  returns logits for the final position only (+0.6%).
- **Never put a loopback `rpc-server` in the path for the local shard.**
  `--rpc <remote> -ngl <half>` beats `--rpc 127.0.0.1,<remote> -ts 1/1` by
  **29% on prefill** — the local CPU can serve its own half directly. And do not
  retry pinning the output tensor local with `-ot`: it collapses both metrics
  and doubles traffic.
- **A shard upload can outlive the watchdog's patience, and that once made
  sharding impossible (F51).** `rpc-server` serves one client at a time and
  refuses connections while busy, so a port probe read "wedged" and restarted it
  9m41s into a ~54 min upload. Fixed by probing **bytes moved** on the RPC
  port's established connections, not the port's willingness to accept — but the
  fix's `RPC_STALL_GRACE = 900 s` is a labelled safety margin, not a
  measurement.
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

### The 1 → 2 transition is where MOST of the risk lives — but not all of it

Most of what can go wrong appears when the second node arrives:

- RPC protocol enters the picture at all
- **Version, libc and ISA lockstep** start to matter (a mismatch is silent until
  it SIGILLs mid-graph)
- Upstream bugs triggered by **2+ RPC workers** become possible
- Duplicate `machine-id` / SSH host keys collide

**Test every one of these at N=2 before growing.** They are cheap to find with
two machines and expensive to find with ten.

**This section used to end "and nothing new appears at the tenth." That was too
optimistic, and F56 measured it.** 1 → 2 found five latent bugs (F30); **2 → 3
found three more** — `setup.sh` creating only `/opt/llama.cpp` so shipping
ik_llama.cpp to a fresh node died on `Permission denied`; the #26500 gate's only
diagnostic (`journalctl`) running unprivileged and printing `-- No entries --`
on healthy and broken nodes alike; and F30's machine-id journal orphaning
recurring because `setup.sh` still does not restart `systemd-journald`. **The
one problem that WAS anticipated — a per-node username — was not among them.**
Plus `llama-server@.service` had never reached any node at all despite being
tracked in git, which is F32 a second time in a different file.

**So the transferable rule is the opposite of the old sentence: every new node
is a fresh test of whether a step lives in a script or in somebody's shell
history.** A step that only ever ran by hand is invisible until a genuinely
clean machine arrives, and each one costs a node join to find. Expect node 4 to
find something too.

## Missing Link

**Missing Link** is the async long-workload runner on top of the cluster.
Rather than a chat window where slowness is a defect, it is a job queue where
slowness is irrelevant — submit work, collect results later.

It is the centrepiece, not a nice-to-have: it converts "too slow to be useful"
into "fast enough for this class of work." It is also the prototype for the
skill's task-profile mechanism — each workload type is a plug-in with its own
prompts, chunking and evaluation.

**It fans out across R endpoints, not one.** Under replication the queue's job
is to keep R independent servers busy. That is built *and proven live on
hardware* as of 2026-08-23 — two jobs claimed by different endpoints, running
concurrently, corroborated from outside Missing Link's own opinion of itself by
node 2's load average. **Still open: chunk-level fan-out within one document**
(a single large job still uses one endpoint) and a retrieval task profile.

**Missing Link now requires a credential.** `ML_AUTH_TOKEN`, read from
`/etc/default/missing-link`, accepted as HTTP Basic (`curl -u ml:$ML_AUTH_TOKEN`)
or `Authorization: Bearer`. **`/health` is the only open route** — deliberately,
because a monitor whose token drifts out of sync would report an outage that is
not happening (F39's lesson), and `/health` carries queue counts and endpoint
reachability, no document or job text. Everything else, read-only routes
included, needs the token.

**Never put the token in any published file.** Not in `CLAUDE.md`, `STATUS.md`,
`docs/`, a commit message, or a comment. It is a value in
`/etc/default/missing-link`; site-specific detail belongs in `network.md`, which
is gitignored. It is a **door lock, not a security system** — one shared secret,
no users, no roles, no sessions — which is exactly the scope the operator asked
for and all this project claims for it.

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
   **Cause corrected 2026-08-18 (F40): it was not a client disconnect.** The
   journal shows the disconnect F36 blamed happened ten minutes *after* the
   fatal error, and the server kept serving through an earlier real disconnect.
   It was the ik_llama.cpp abort above: `ggml_abort` forks from a multithreaded
   process, the parent blocks in `wait4()` forever and never exits, and **the
   forked children inherit the listening socket** — so `Restart=always` sees a
   live process *and a port check sees an open port*. F36's symptoms and every
   fix it produced stand; only its cause was wrong. **A liveness probe must
   therefore test neither the process nor the port, but progress.**

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
  `CHUNK_TOKENS`, do not infer it from `-c`. **`CHUNK_TOKENS=4096` and `-c 32768`
  are now measured optima, not just headroom-sufficient defaults** — a real
  map-reduce sweep against the real pipeline found wall-clock U-shaped in
  `CHUNK_TOKENS` with a minimum at 4096, and a follow-up control found that
  raising `-c` past what 4096 needs (`-c 65536`, `n_ctx_slot=16384`) costs **33%
  more wall-clock on the identical chunking**, not a wash — larger context is not
  free even when unused. See `docs/measurements.md`, "Chunk-size sweep" and
  "Chunk-size sweep, extended". Do not raise `-c` for headroom alone.
- **The sentence splitter is `nupunkt`, not a regex** (F48 and its addendum,
  merged and live). It is a **legal-domain** splitter: pure Python, zero runtime
  dependencies, MIT, model bundled in a 9.1 MB wheel, verified to work inside
  `unshare -rn` with no network. It exposes `sent_spans`, returning true
  character offsets, so the offset contract (`text[start:end] == sentence`) that
  citations and the audit ledger depend on is preserved directly rather than
  recovered by string search. On the real corpus it took structural fragments
  from 65.0% to 12.3% and legislative marker rate from 2.85% to 10.53%.
  **The regex rung still exists as a loud fallback and four tests pin the old
  splitter's defects to it deliberately** — a test suite can hold a bug in place
  as a specification, and deleting such a test hides the change rather than
  recording it.
  **Python floor: nupunkt needs >= 3.11.** Nodes 1, 2 and 3 are all on 3.11.2 —
  *exactly* on the line. Nodes 4-7 are unchecked, and **a floor satisfied only
  on characterised nodes is a fleet trap.**
  **HTML extraction stays load-bearing and must not be dropped as redundant.**
  nupunkt does not fix F45's raw-markup manifestation, it **inverts the sign**:
  markup now inflates density (0.50 raw vs 0.33 cleaned) where it used to
  deflate it.
  **Every number produced by the old splitter is invalid, not merely old.** The
  corpus has been re-profiled (F52); `docs/chunk-boundary-measurement.md`
  carries a banner saying its figures came from the old instrument.
- **Count tokens; do not estimate them. `POST /tokenize` is free.** It runs on
  the HTTP thread and constructs no task — verified by reading
  `server-context.cpp`, not assumed — so it takes no inference slot and cannot
  perturb a running benchmark. **`WORDS_PER_TOKEN = 0.70` is wrong by up to 2×
  in BOTH directions** (0.53–1.08 words/token *within a single document*, from
  the chunk-size sweep's own raw output). A constant cannot express that, and
  every place the estimate gates a fit decision is a place a real document
  silently overflows a slot or wastes one (F49).
- **`nodes.env` fields are `<hostname> <lan-ip> <ram_mb> <physical-cores>
  [ssh-user]`.** The fifth field is optional and defaults to the coordinator's
  login. It exists because node 3's admin account is `debian3`, and the operator
  chose to **parameterise rather than rename** — the skill must eventually run
  where usernames do not match. Note that **`EnvironmentFile` cannot solve
  this**: systemd expands `${VAR}` only in the `ExecStart` family, so `User=` is
  resolved before any environment exists. It is done with a drop-in written by
  `install-services.sh`, and the tracked `User=` is a placeholder resolving to
  nobody **so a missing drop-in fails loudly rather than silently running the
  inference server as root.**

## Verification

There is no test suite for the cluster itself — verification is running the
command and reading the output. Do not report a step as done without having
seen its output.

**A faster build or config that changes output is not a win.** Any performance
change must be paired with a coherence check on real output before adoption.

Missing Link does have tests. Run them from the repo root:

```bash
cd missing-link && .venv/bin/python -m pytest tests/ -q
```

**662 passed / 0 failed as recorded in F52 (2026-08-23), plus the auth suite
merged after it — run the command rather than quoting a number from here.** The
`.venv` is gitignored and exists only in the main checkout — an agent worktree
does not have one, so create one there if you need to run the suite from a
worktree. **`reportlab` was an undeclared test dependency** until 2026-08-23; it
is now in `requirements.txt`, verified by building a fresh venv purely from that
file.

**Reconcile a test count; do not merely accept one.** 662 was reached from an
estimate of ~585 because the splitter branch extended four *existing* test files
as well as adding its own (552 + 77 + 27 + 6). **A test count that does not
reconcile is an unexamined claim.**

**But a test count is not evidence of working software:** 41 tests passed
against a pipeline that had never processed a single document (F34). Every defect since lived in the seam between our code and something
real — SQLite locking, the model's output shape, `finish_reason`, PDF bytes, a
wedged server. **Report what was exercised, not how many assertions ran.**

## Key decisions

**Hardware (node 1, measured 2026-08-17):** Xeon E5-1620 v4, **4 cores / 8
threads**, no AVX-512, **131.8 GB RAM** (4 × 32 GB DDR4-2400, all four channels
at rated speed), 477 GB NVMe. **Achievable memory bandwidth 28.2 GB/s** — only
37% of the quad-channel theoretical maximum, because **4 cores cannot saturate
their own memory bus.** Not a misconfiguration; uncore and energy-perf-bias are
already at maximum, so there is no BIOS lever.

**The fleet is N=3 and is a three-way bandwidth twin, MEASURED not assumed.**
STREAM triad: node 1 **28.4 GB/s**, node 2 **27.9**, node 3 **27.6–27.7**. Node
3 is the same Xeon E5-1620 v4 down to the stepping, the same ThinkStation
chassis, the same BIOS, and its sorted CPU-flag set diffs to **zero
differences** against node 1 — so no ISA/SIGILL risk (F8). Its RAM is
**128707 MB**, genuinely 2 MB under nodes 1/2: **use the measured value** (F29).
The 4-thread peak and the SMT penalty (F10) have now reproduced independently
three times.

**Nodes 4-7 are not characterised. Do not assume they match** — homogeneity is a
result here, re-derived per node, not a property of the fleet. If a node has more
cores it will be faster at generation despite identical RAM, and
`--tensor-split` should then weight by **measured bandwidth**, not RAM.

**Two things about node 3 that generalise to node 4:**

- **Its "1 TB disk" is a 7200 rpm spinning SATA drive**, not more NVMe — and the
  same drives are going into nodes 1 and 2. It is effectively new (128 MB used of
  932 GB, SMART PASSED, 2,789 power-on hours). **Models stay on NVMe**; the HDDs
  are snapshot and cold storage (F16 makes disk the binding constraint, F3 makes
  load single-core serialised, and loading 65 GB off rust would be materially
  worse). The payoff is still large: a fleet-local GGUF mirror re-provisions at
  ~120 MB/s off local rust instead of ~11.7 MB/s over the LAN — **~9 minutes per
  65 GB instead of ~97.**
- **It shipped as a full GNOME desktop, and so did nodes 1 and 2.** F53 framed
  that as a divergence; **F56 corrects it — it is the fleet's normal**, and
  trimming node 3 alone would make it the odd machine out and confound any A/B
  against the bandwidth twins it just joined. Trim fleet-wide as its own task
  with a before/after RAM measurement, fully reversible. It is ~2.5% of 128.7 GB.

Settled:

- **CPU + system RAM only.** GPUs are present for display and unused for
  compute. The Quadro P600's 2 GB cannot hold meaningful layers of any target
  model, so "GPU for prefill" is not executable with the hardware on hand.
- **llama.cpp RPC** for sharding, pinned to a release tag. Exo, prima.cpp and
  distributed-llama rejected — see the spec for the reasoning, and do not
  re-propose them.
- ~~**`ik_llama.cpp` for the document workload**~~ — **REVERSED 2026-08-18, see
  F40. Run MAINLINE.** `ik_llama.cpp` **fatal-errors on the fifth request of any
  `--parallel 4` job** — a 100% failure rate on any document longer than four
  chunks. Its SWA flash-attention path keeps only the last 512 KV cells, which
  is a superset of every query's window *for one sequence*; with four
  interleaved slots those cells belong to other sequences, the mask is wholly
  masked, and it aborts. Mainline b10369 survives the identical sequence.
  **F27's +52%/+22% is not wrong, it is unusable**: it was measured with
  `llama-bench`, which issues ONE sequence, while this project's own standing
  constraint is `--parallel 4`. Re-measured through `llama-server`, ik's prefill
  lead is **+43% decaying to +15%** as the cache fills, and F27's −14%
  generation penalty **does not appear at all**. Keep ik built alongside — `ik`
  at `--parallel 1`, and with flash attention explicitly off, are both untested
  and might restore the win.
  **The transferable lesson, and it is the sharpest in this repo: a benchmark
  that does not reproduce the deployment's concurrency is not a benchmark of the
  deployment.**
- **Debian 12 headless**, scripted provisioning (not `dd` cloning — disks vary).
- **Tailscale for SSH and the web GUI only.** RPC mesh runs on raw LAN IPs.
- ~~**Open WebUI** as the chat frontend~~ — **NO LONGER SETTLED.** It is a *chat*
  frontend and this workload is explicitly asynchronous ("nobody is waiting at a
  prompt"), which is the same category error as adopting RAG-QA tools for
  summarisation. Missing Link's own job console is what is actually running. See
  `docs/DESIGN-NOTES.md` F and `docs/REQUIREMENTS.md`.
- **Map-reduce for long documents**, ~4K chunks with 10% overlap — decided on
  evidence. A larger context window does not fix "lost in the middle".

**Model B is CLOSED, in the NEGATIVE (F47, 2026-08-23). It is a decision, not a
deferral.** *"One frontier model too large for any single machine"* cannot be
justified on this fleet — every candidate is now priced and each is dominated:

- **GLM-5 / 5.1 / 5.2 — REJECT.** Its active-parameter count is published
  nowhere and was **computed from `config.json` at 40.8 B**, labelled CONFIRMED
  because the same per-tensor inventory reconstructs the published 753.86 B
  total to **0.00% error**. That is *more* than DeepSeek-V3.2's 37 B and ~8× the
  incumbent's 5.1 B, and on this fleet active params *are* generation speed. It
  is also **less faithful than GLM-4.6** (10.1% vs 9.5%, Vectara, REPORTED) —
  **the strongest open reasoner is not the most faithful one**, which is the
  assumption that put GLM on the shortlist. And IQ4_XS is 402.9 GB real against
  368 GB free: F16's disk blocker again, worse than Kimi K2. Software support was
  *not* the blocker, contrary to expectation — both llama.cpp PRs merged before
  our pin.
- **Finix S1 32B — REJECT, no public weights.** HF returns 401; the public
  `antgroup` org holds two models, neither Finix. **And its headline 1.8% is a
  summary-length artifact:** its average summary is 172.4 words against a
  106.9-word median across 105 models, and the leaderboard's own FAQ says a
  copy-paste extractive summariser scores 0% and that it is "not evaluating the
  quality of the summaries." **A hallucination score cannot be read without
  reading the summary length beside it.**
- **Kimi K2 — REJECT** (unchanged, F25: 17.9% REPORTED, the worst checked).
  **DeepSeek-V3.2** stands on merit and does not run at N=2. **GLM-4.6** becomes
  viable only at N≥4.

At N=7 the S=1 tier delivers **12–47× the aggregate tokens** of the GLM-5 tier,
and GLM-5 offers no faithfulness gain to weigh against that.

**The live question is `GLM-4.7-Flash`, and it is a measurement, not a
paragraph.** It was never on the shortlist and appears to beat the gpt-oss-120b
incumbent on every axis this project ranks: **31.2 B total** (CONFIRMED from HF
safetensors), **~3.6 B active** (computed, reconstruction within 2%), **9.3%
Vectara** against gpt-oss's 14.2%, **MIT**, **18.3 GB at Q4_K_M**, **S=1**,
predicted ~8.2 tok/s, llama.cpp support merged before our pin — and **half an
hour of link time to fetch**. **Do not adopt it on this paragraph.** It is a
hybrid reasoning model (F35: there is no universal thinking-off switch), 31 B
total is far less stored knowledge than 120 B, and everything but the file sizes
and config is REPORTED or INFERRED. It is cheap enough to settle the only way
this project is allowed to settle anything.

**The one experiment that reopens Model B:** GLM-4.6 **UD-IQ1_S at 96.9 GB is
S=1**, hence replicable at R=7. Whether 1-bit retains enough of the 9.5% to beat
gpt-oss's 14.2% is genuinely open, and it costs 2.3 h to fetch.

See `docs/MODEL-SELECTION.md`, F25 and F47.

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
- **Then check the search TERM, not just the answer.** `chunking-research.md`
  asked *"what is the best general-purpose sentence splitter?"*, assessed nltk
  and spaCy, correctly rejected both as too heavy for this fleet, and concluded a
  regex was the only fit. **The reasoning was sound and the conclusion was
  wrong**, because it never asked *"what do people who process legislation use?"*
  One does exist, it is MIT with zero runtime dependencies, and it is **lighter
  than either candidate rejected for weight** (F48). So: **domain-specific
  tooling can be simultaneously more accurate AND cheaper than the
  general-purpose tool, which means "too heavy" is not a conclusion that survives
  a change of search term.** Corollary, measured on the same day: **a library's
  benchmark score is earned on a text genre.** pysbd's peer-reviewed 97.92%
  Golden Rules claim came from well-formed prose; on our real corpus — a document
  that is structurally a list — it scored **worse than the regex it would
  replace, and took 133.94 s to be worse.**
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
- **Agent hygiene is your obligation, and the hook is an UNPROVEN backstop.**
  The rules in this section are right on their own merits — every one of them
  is here because it broke something — and **you follow them because they are
  right, not because anything stops you.** `.claude/settings.json` runs
  `PreToolUse` hooks (`.claude/hooks/cluster-guard.py`) that *intend* to block
  `git add -A`, `git commit -a`, `pkill -f` and inline Python that does not
  compile, and to gate cluster service control, `git push`, mutating SQL
  against the live job store, writes to `/opt/models`, and git working-tree
  operations in the live checkout.
  **F55, 2026-08-23: that layer has NEVER fired.** `settings.json` invoked the
  hook via `"$CLAUDE_PROJECT_DIR"/...`, the variable is empty, and the path
  never resolved — so a hard BLOCK rule (`git add -A`) executed unimpeded when
  it was finally tested, and all 166 lines of `.claude/hook-audit.log` are
  direct test invocations with **no framework interception ever recorded.**
  **`CLUSTER_OPS_CONFIRMED=1` has therefore never meant anything either**,
  because nothing ever checked it. Still: never set it on your own initiative;
  that prefix means the operator said yes.
  **`settings.json` now carries a fallback path, and that fix is UNVERIFIED.**
  The framework reads `settings.json` at session start, so confirming
  interception requires a *fresh* session issuing a command the guard claims to
  block and seeing the block. **Until someone has seen a real block, treat the
  guard as inert and yourself as the only enforcement.** If a hook does block
  you, the hook is right and the command was wrong — read its message and take
  the alternative it names, and **record that it fired**, because that is the
  observation this repo is currently missing. See `docs/AGENT-HARDENING.md`.
  **The general lesson, and it is why no new rules were written in response:
  an enforcement layer must prove it enforces before anything is written that
  depends on it.** A guard's fail-closed logic lives inside the guard, which is
  no protection at all against the guard never being run.
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

# Can ComfyUI and n8n distribute work across nodes?

**Date:** 2026-08-23 | **Status:** RESEARCH ONLY. Nothing was installed, nothing
was run on the cluster, no service was touched. The only commands executed were
read-only reads of files already on this machine (`/opt/llama.cpp/src/tools/server/README.md`,
the repo's own docs) and public HTTP GETs.

**Companions:** `docs/comfyui-feasibility.md` and `docs/n8n-feasibility.md`
answered *"can these be added at all?"* (summarised as **F54**). This document
answers the follow-up the operator asked for: **can either of them use more than
one machine?** — and what a load balancer in front of the fleet's
`llama-server` endpoints would buy.

**Labelling.** Every claim is **CONFIRMED** (verified against a primary source —
the project's own source, docs or repo metadata, or a file read off this
machine), **REPORTED** (a third party states it, unverified here), or
**INFERRED** (derived here). **No web-sourced performance number is presented as
if it were measured on this hardware.** Only `docs/measurements.md` may be
quoted for that, and nothing in this file belongs in it.

---

## 0. The verdicts, first

| | Real multi-machine support? | Shape |
|---|---|---|
| **ComfyUI** | **Third-party only.** First-party multi-GPU exists and is *same-machine only* | Job-level fan-out across N independent instances. Nothing maintained splits one CPU denoise across machines |
| **n8n** | **Yes, first-party, and free on Community** | Queue mode: main + N workers on separate machines, Redis + Postgres behind them |
| **LLM load balancer** | **Recommended — but as a client-facing convenience, not a throughput win** | HAProxy or LiteLLM. **Not** Paddler in its current form (§4.3) |

**And the one sentence that decides the ComfyUI question:**

> **Distribution improves ComfyUI's throughput and cannot improve its latency.
> A 512×512 20-step SD1.5 image takes the same ~10 minutes on three nodes as on
> one, because no maintained tool splits a single CPU denoise across machines.**
> A class of 20 each waiting for *their own* image still waits ~10 minutes each.
> Fan-out shortens the queue behind them; it does not shorten their wait.

That is the same structural fact `CLAUDE.md` already records for the LLM —
replication buys aggregate throughput, never single-job speed — arriving from
the opposite direction. Here, replication is the *only* thing on offer.

---

## 1. The frame: what "distribute" has to mean to be worth anything here

`CLAUDE.md`'s scaling model is two numbers: **S** (nodes per copy of the model)
and **R** (independent copies), with **aggregate throughput ≈ R × single-node
throughput**. `docs/measurements.md` measured both routes on this fleet:

| Route | Measured on this hardware | Source |
|---|---|---|
| **Replication, 2 nodes** | **~1.8× (raw 1.62× prefill / 1.55× generation; ~1.86×/1.77× adjusted for one failed request)** | `docs/measurements.md`, "THE REPLICATION MEASUREMENT" |
| Sharding, 2 nodes, one copy | **1×**, and −49% generation | same |
| RPC penalty decomposition | protocol **−5.8%**, the 100 Mb wire a further **−35.4%**, second device **−11.4%** — and it scales with `n_vocab`, so it **never amortises** | **F50** |

**So the question to ask of ComfyUI and n8n is not "can it shard a task".** It
is: **can it run N independent workers that a queue feeds?** That is the shape
Missing Link already uses — one `_worker_loop` per entry in `LLAMA_URLS`, each
claiming independently against an atomic `claim_next_pending`
(CONFIRMED, `missing-link/missing_link/app.py:48–116`).

If a tool can do that, say so and move on. If it can only split one task across
machines, F50 is the price list, and on a 100 Mb LAN (93.8 Mbit/s measured,
**F28**) that price is not payable.

---

## 2. ComfyUI

### 2.1 First-party: multi-GPU is real now, multi-machine is not

**This changed in 2026 and the older forum consensus is out of date, so it is
worth stating precisely.**

**CONFIRMED — ComfyUI now has first-party multi-GPU.** The `worksplit-multigpu`
branch landed as **MultiGPU Work Units**, since renamed **MultiGPU CFG Split**
([PR #7063](https://github.com/Comfy-Org/ComfyUI/pull/7063),
[built-in node docs](https://docs.comfy.org/built-in-nodes/MultiGPU_WorkUnits)).
The node docs are explicit about its limits, quoted verbatim:

> "The MultiGPU CFG Split Node allows diffusion processing to occur using
> multiple GPUs **installed in the same system**."

and its `max_gpus` parameter is *"the maximum number of identical GPUs to use…
Set this to the number of matching GPUs installed in your system"*, with a
stated requirement of *"any homogeneous dual GPU setups with Ampere+
architecture"*.

**Three things follow, and all three matter here:**

1. **It is same-machine only.** There is no cross-machine path.
2. **It is GPU-only** — Ampere or newer, matched pairs. The fleet has one Quadro
   P600 (Pascal, sm_61), already rejected twice (F54).
3. **It splits CFG work units** (positive vs. negative conditioning), so it
   needs CFG > 1 and it caps out around 2×. REPORTED speedup "up to 1.95x".

**CONFIRMED — the maintainer position on parallel execution has not changed.**
From ComfyUI Discussion #4139, collaborator `ltdrdata`:

> "Currently, ComfyUI does not provide a method to execute workflows in
> parallel."

and, in the same thread, the recommended answer:

> "SwarmUI provides a UI that can handle multiple ComfyUI instances as backends
> at once."

([Discussion #4139](https://github.com/comfyanonymous/ComfyUI/discussions/4139))

**So: the first-party answer to "use more machines" is, and remains, "run more
instances and put something in front of them."** Which is exactly the
replication shape this project already validated — and it is what
`docs/comfyui-feasibility.md` §4 already recommended before this question was
asked.

### 2.2 Third-party options, assessed

| Project | What it actually distributes | GPU required? | Shared FS? | Maintained? | Verdict |
|---|---|---|---|---|---|
| **[SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI)** | **Whole generations**, one per backend, in list order | Not by SwarmUI itself — it dispatches to whatever a backend is | **No**, but **identical model filenames and folder paths on every machine** | **Yes** — pushed 2026-08-23, 4,479 stars, MIT (CONFIRMED, GitHub API) | **The only credible option.** §2.3 |
| **[ComfyUI-Distributed](https://github.com/robertvoy/ComfyUI-Distributed)** | **Seeds within one job** (each worker renders a different seed of the *same* prompt) and **tiles** for Ultimate SD Upscale | **README lists "Multiple NVIDIA GPUs" as a requirement** | Not required; a "Distributed Model Name" node passes model paths to workers | **Yes** — pushed 2026-08-14, 608 stars, Apache-2.0 (CONFIRMED, GitHub API) | Wrong granularity for a classroom — see below |
| **[ComfyUI_NetDist](https://github.com/city96/ComfyUI_NetDist)** | Whole workflows / batches / latents to networked instances | GPU-focused (`--cuda-device` in its own examples) | Each instance keeps its own models | **No — last push 2024-05-22**, i.e. over two years stale (CONFIRMED, GitHub API) | Reject on maintenance |
| **[ComfyUI_Cluster](https://github.com/nomcycle/ComfyUI_Cluster)** | Leader/follower tensor broadcast, fan-out, fan-in, gather | **CUDA required for followers**; leader may be CPU-only | — | **3 stars, self-described "very early W.I.P."** (CONFIRMED, GitHub API) | Reject |
| **[ComfyUI-MultiGPU](https://github.com/pollockjj/ComfyUI-MultiGPU)** | **Nothing across machines.** Places model components on chosen devices; "Virtual VRAM" offload | GPU | — | Yes (2026-05-08, 967 stars) | **Not a distribution tool** — memory management. REPORTED: workflow steps still run sequentially |
| **xDiT / PipeFusion / DistriFusion** | **Genuinely splits one image** — sequence/patch/pipeline parallelism | **Yes, and it is torch-distributed multi-GPU** | — | Active research code | Diffusion *Transformers* only (FLUX/SD3 class), not SD1.5's UNet; GPU-only. Out of reach ([xDiT](https://github.com/xdit-project/xDiT)) |

**Why ComfyUI-Distributed is the wrong granularity here, stated plainly.** Its
unit of distribution is **the seed, not the job** — the Distributed Seed node
"generates unique seeds for each worker" and the Distributed Collector gathers
the frames back (CONFIRMED, its README). That accelerates *one user asking for
four variations*. **A classroom is twenty users each asking for one different
thing**, which is job-level fan-out, which is SwarmUI's model, not this one.
Both are useful; only one matches the workload.

### 2.3 The shape that works: N instances + a dispatcher

**SwarmUI, CONFIRMED from its own docs** ([Using More GPUs.md](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/docs/Using%20More%20GPUs.md)),
quoted verbatim:

> "Backends get used in the order they're listed. That means, the first backend
> in the list gets the first generation you queue. The second backend only gets
> used if you queue at least 2 generations at the same time."

and, on remote machines:

> "make sure any models you have on the Main Machine, you also copy to this
> Other Machine. These models must have the exact same filename and folder
> path."

**That is precisely `_worker_loop`-per-endpoint, wearing a different hat**, and
it is the same architecture `docs/measurements.md` clocked at ~1.8× on two
nodes for the LLM. Remote ComfyUI instances attach as **"ComfyUI API By URL"**
backends, with the docs recommending the Swarm-API-Backend variant and noting
Swarm's own extra Comfy nodes must be copied into a remote instance's
`custom_nodes` (REPORTED, [ComfyUI Backend README](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/src/BuiltinExtensions/ComfyUIBackend/README.md)).

**The honest alternative is to write the dispatcher.** ComfyUI's `POST /prompt`
returns a `prompt_id` and queue position, and `GET /queue` reports depth
(CONFIRMED, [Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)).
"Poll three instances, post to the shallowest queue" is a small script and it
carries no new attack surface — which matters, because §5 of
`docs/comfyui-feasibility.md` is entirely about ComfyUI's attack surface, and
SwarmUI is another unauthenticated-by-default web service to secure. **If
SwarmUI is adopted it must sit behind the same nginx basic-auth described
there, and its Comfy backends must not be independently reachable.**

**Copying the extra custom nodes to remote backends is a supply-chain step**, so
it belongs in the pinned `comfyui-nodes.txt` manifest that
`docs/comfyui-feasibility.md` §6 already recommends — not done by hand.

### 2.4 The arithmetic — does distribution change the verdict?

**No. It changes the queue and leaves the wait alone.** All per-image figures
below are **INFERRED** in `docs/comfyui-feasibility.md` §2 from third-party
anchors on 4-core-class AVX2 CPUs; **none was measured on this hardware**, and
the multiplication is arithmetic on top of an inference.

Assume three CPU nodes, one ComfyUI each, jobs fanned out.

| Workflow | Per image, 1 node (INFERRED, F54) | Throughput, 1 node | Throughput, 3 nodes | Class of 20, one image each |
|---|---|---|---|---|
| **SD1.5, 512×512, 20 steps** | ~10 min (7–13) | **~6/hour** | **~18/hour** | **~3.3 h → ~1.1 h**, and **each student still waits ≥10 min** |
| **SD-Turbo, 512×512, 1 step** | ~25–35 s | ~103–144/hour | ~310–430/hour | **~10 min → ~3.5 min** |
| SDXL 1024², 20–30 steps | 1–2 h | ~0.5–1/hour | ~1.5–3/hour | overnight, either way |
| Video | tens of hours to days per 5 s clip | — | — | **still (c). Three times unreachable is unreachable** |

**Read the SD1.5 row carefully, because it is the whole answer.** Three nodes
take the class from 3.3 hours of queue to 1.1 hours of queue. It does **not**
take any individual student from 10 minutes to 3.3 minutes — their single image
is one sequential denoise on one machine. So distributing SD1.5 turns *an
unusable exercise* into *a slow one*, not into a usable one.

**And the SD-Turbo row shows distribution is not what rescues ComfyUI —
step count already did.** At ~30 s/image a single node serves a class of 20 in
about ten minutes. **The carve-out that makes ComfyUI viable (F54's (a)) does
not need multi-machine at all.** Adding two more nodes to a workload that
already fits on one is spending replication factor R — the resource
`docs/measurements.md` shows is worth ~1.8× on the *document* job — to shorten
an already-short queue.

**Set against F54's placement finding, that is close to decisive.** F44
(CONFIRMED, this hardware) measured a niced CPU-bound sidecar starving
`llama-server` on a 4-core node — 378.9% and 336.8% CPU simultaneously, the
niced process degrading from 4.7 to 41.7 s/claim. ComfyUI is a *worse* case: it
holds all cores for the full duration with no gaps. **Fanning ComfyUI across
three nodes means putting that on all three.** One ComfyUI node plus turbo
models is both the cheaper and the better answer.

### 2.5 The network tax, and it is not the images

The **measured** link is 93.8 Mbit/s ≈ **11.18 MB/s** (`docs/measurements.md`,
F28). Two different costs, and only one is real:

- **Result images are cheap.** A 512×512 PNG is a fraction of a megabyte;
  even a hundred per hour is noise on this link. **INFERRED**, and it is not
  close.
- **Model distribution is the cost.** SwarmUI requires identical model files at
  identical paths on every backend machine (CONFIRMED above). An SD1.5
  checkpoint at 2–4 GB is ~3–6 min per node at the measured rate; SDXL ~10 min.
  One-off, tolerable, and it argues for the same pre-staged curated model set
  `docs/comfyui-feasibility.md` §6 recommends — and against letting twenty
  students each pull their own checkpoint through the pipe the cluster's own
  model distribution shares.

### 2.6 ComfyUI verdict

**Multi-machine: third-party only, and the only maintained option worth using
is SwarmUI (or ~50 lines of your own dispatcher).** ComfyUI's own answer is one
instance per device with something in front; its first-party MultiGPU work is
same-machine, GPU-only and capped near 2×.

**Does it change the feasibility verdict? No.**
- It does not make video reachable — not by one to two orders of magnitude.
- It does not make standard SD1.5 interactive, because per-image latency is
  untouched. It makes the class queue ~3× shorter, at the cost of three nodes.
- The workload it *would* help — a big overnight batch of standard-quality
  images — is exactly the case F54 already called (b), and it is the case that
  collides hardest with the document workload under F44.
- The workload that is actually viable — 1-step turbo models — **does not need
  it.**

**Recommendation: single ComfyUI instance on the non-inference node, turbo
models, no distribution layer.** Revisit only if the instructor wants overnight
batches of standard-quality images, at which point SwarmUI over idle nodes at
night is a clean fit with the "daylight hours only" scheduling that F54 already
recommends — the two jobs would simply swap shifts.

---

## 3. n8n

### 3.1 Queue mode: first-party, multi-machine, and free

**CONFIRMED, from n8n's own docs**
([Enable queue mode](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode)),
quoted verbatim:

> "If you want to host workers on a separate machine, install n8n on the machine
> and make sure that it's connected to your Redis instance and the n8n
> database."

**What it needs — and the SQLite line is the one that costs something here:**

| Requirement | Status |
|---|---|
| **Redis** | Required. "Redis acts as the message broker, and the database persists data, so access to both is required." **CONFIRMED** |
| **Postgres** | Effectively required. **"Running n8n with execution mode set to `queue` with an SQLite database isn't recommended"** and **"Running a distributed system with this setup over SQLite isn't supported."** **CONFIRMED** |
| **`N8N_ENCRYPTION_KEY` identical everywhere** | Required — it decrypts stored credentials. **CONFIRMED** (also in `docs/n8n-feasibility.md` §2.5) |
| **Edition** | **Queue mode is in Community.** Only **multi-main** is Enterprise: *"Multi-main setup is available on: Self-hosted: Enterprise."* **CONFIRMED**, consistent with `docs/n8n-feasibility.md` §2.5 |
| Worker concurrency | `--concurrency`, **default 10**; n8n recommends ≥ 5. **CONFIRMED** |

**Note what multi-main being Enterprise actually costs:** the *main* process
(editor UI, webhooks, schedules) stays a single machine and a single point of
failure. **Only execution scales out.** For a classroom that is the right half
to scale anyway.

**So the direct answer: n8n has genuine, first-party, free multi-machine
distribution, and it is the `_worker_loop`-per-endpoint shape** — N independent
workers claiming from a shared queue. Structurally identical to what Missing
Link does, with Redis where Missing Link has `claim_next_pending`.

### 3.2 Testing "queue mode buys nothing against a 5 tok/s bottleneck"

The brief asked for this claim to be tested rather than repeated, and **it is
half right, in a way worth being precise about.** `docs/n8n-feasibility.md` §2.5
made it about LLM throughput; the classroom question is different.

**Where the claim holds — LLM-bound workflows.** Adding n8n workers cannot
create inference capacity. From `docs/measurements.md` (node 1, mainline
b10369, gpt-oss-120b F16, `-t 4 -c 32768 --parallel 4`): prefill ~16.3 tok/s,
generation ~5.26 tok/s, **77–99 s end-to-end for ~1,060–1,410 prompt tokens**.
Twenty students on 4 slots is a queue tens of minutes deep, and it stays that
deep whether one n8n process or five hold the open HTTP connections. **The
bottleneck is `--parallel`, and it lives in `llama-server`.**

**Where the claim does not hold — everything else a classroom does.** The
survey's claim was made about LLM calls; students do not only make LLM calls:

1. **The Code node.** `docs/n8n-feasibility.md` §2.1 already names this as the
   most important resource fact in the document: the Code node runs arbitrary
   JavaScript, so an n8n with twenty untrusted authors is **CPU-bound on
   demand**. A `while(true)` on the main process wedges the editor for everyone;
   the same loop on one of three workers wedges one third of capacity and
   leaves the UI responsive. **That is a real and specific benefit of queue
   mode, and it is about blast radius, not throughput.**
2. **HTTP-bound teaching workflows** — scraping, API calls, file handling,
   webhook chains — are the workflows n8n's own 220-executions/second benchmark
   measures, and they parallelise across workers exactly as advertised.
3. **Backpressure**, which is §3.3 and is the strongest argument of the three.

**Revised position (INFERRED):** queue mode is **not** a fix for LLM latency and
should never be sold as one. It **is** a fix for *"one student's runaway Code
node froze the class"*, and it is the only mechanism n8n offers that bounds
concurrent execution for the kind of run a classroom actually generates. The
price is Redis + Postgres + the encryption-key discipline — two more services to
run, back up and secure, on a cluster whose existing services already have no
auth (F54).

### 3.3 The correction that matters: manual executions *can* be bounded

**`docs/n8n-feasibility.md` §2.5 and F54 both state — correctly — that
`N8N_CONCURRENCY_PRODUCTION_LIMIT` "doesn't apply to… manual executions", which
are the only kind a classroom generates. That is true in regular mode. It is
not the whole picture, and the missing half is a queue-mode feature.**

**CONFIRMED**, from n8n's [queue-mode environment variables](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/queue-mode):

> **`OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS`** — Boolean, default `false` —
> "Set to `true` if you want manual executions to run on the worker rather than
> on main."

**CONFIRMED**, from [Control concurrency](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency):

> "In queue mode, you can control how many jobs a worker may run concurrently
> using the `--concurrency` flag."

and

> "the environment variable `N8N_CONCURRENCY_PRODUCTION_LIMIT` controls both of
> them. In queue mode, n8n takes the limit from this variable if set to a value
> other than `-1`, falling back to the `--concurrency` flag or its default."

**INFERRED, and it is the actionable conclusion:** with
`EXECUTIONS_MODE=queue` **and** `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS=true`, a
student's "Test workflow" click becomes a queue job, and a worker's
`--concurrency` therefore bounds how many run at once. **That is the one thing
the earlier survey concluded n8n could not do.** It also moves the Code node off
the main process, which is §3.2's blast-radius argument.

**Two cautions, both REPORTED and both worth verifying before a class depends on
it:**

- **A named bug in exactly this path.** REPORTED: with
  `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS=true`, manual executions of workflows
  containing a **Python** Code node have been reported to hang immediately in
  the UI ([n8n community #206563](https://community.n8n.io/t/code-node-execution-hangs-when-offloaded-to-workers-gke-queue-mode/206563)).
- **Partial/manual executions in queue mode have regressed before** —
  [n8n-io/n8n#16932](https://github.com/n8n-io/n8n/issues/16932), "Partial
  executions no longer run in queue mode after v1.100.1" (REPORTED).

**The inference that the worker's `--concurrency` bounds offloaded manual
executions follows from the two quoted sentences but is not stated in one place
by n8n. It is cheap to verify and it should be verified**, because it is the
load-bearing claim for running a class of 20 — and this project's own house rule
is that a benchmark which does not reproduce the deployment's concurrency is not
a benchmark of the deployment (F40).

### 3.4 Can one n8n spread LLM calls across three `llama-server` endpoints?

**The constraint is real:** an n8n OpenAI credential carries exactly one **Base
URL** field (CONFIRMED from source in `docs/n8n-feasibility.md` §4.1 —
`OpenAiApi.credentials.ts`). One credential = one endpoint. Four options:

**(a) Model Selector node — first-party, no code, and it is the surprise.**
**CONFIRMED**, [Model Selector docs](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.modelselector):

> "The Model Selector node dynamically selects one of the connected language
> models during workflow execution based on a set of defined conditions."

Its "Number of Inputs" parameter sets how many language models attach, and it
"evaluates rules sequentially, starting from the first input, and stops
evaluation as soon as it finds a match." No edition restriction is stated.

So: three OpenAI credentials (one per node), three OpenAI Chat Model sub-nodes,
one Model Selector, three rules. **INFERRED:** a rule keyed on a varying
expression (execution id modulo 3, or the student's identity) gives round-robin
or sticky-per-student routing with no code. **Not built or tested here.** The
sharp edge is that the rules are static conditions, not a load signal — it
cannot see which endpoint is busy, so it is round-robin at best and needs
identical models behind all three endpoints.

**(b) A load balancer in front — one credential, one URL.** §4. Simplest for
students, and the only option that can route on *load* rather than on a rule.

**(c) Multiple credentials + a Switch node.** Works, more nodes, same
limitation as (a), more for a student to get wrong.

**(d) HTTP Request node straight at an endpoint.** Bypasses the AI Agent
machinery entirely. Useful as a teaching exercise (it shows the wire protocol),
useless as an architecture.

**Recommendation: (b), and treat (a) as the fallback if a proxy is unwelcome.**
And note the framing point that outranks both — **F54 already says the right
answer for a class is a dedicated endpoint, ideally running a small model.**
Spreading twenty students across the three endpoints that also serve the
document workload does not create capacity; it distributes the damage. See §4.5.

### 3.5 What still breaks at 20 concurrent students

Unchanged from `docs/n8n-feasibility.md` §3, and none of it is fixed by queue
mode:

- **Webhook paths are unique instance-wide** — twenty students all choosing
  `/webhook/test` collide (REPORTED, §3.3 there).
- **Task runners default to `N8N_RUNNERS_MODE=internal`**, which n8n's own docs
  call *"insecure by design"*: "anyone who can edit a workflow could potentially
  read your database, encryption key, stored credentials, and environment
  variables" (F54). **Queue mode makes this worse, not better** — the encryption
  key must now be present on every worker machine.
- **Community edition has Owner + Member only.** No Projects, no RBAC, no
  sharing controls (CONFIRMED from source, F54). Twenty students in one flat
  namespace.
- **The LLM queue is the real limit** and it is `--parallel 4` on however many
  endpoints the class is pointed at.

### 3.6 n8n verdict

**Real multi-machine support: YES, first-party, free on Community.** Queue mode
with workers on separate machines is documented, supported and exactly the
architecture this project already favours.

**Should it be turned on for the classroom? Probably not at first, and the
reason has changed.** The earlier survey said no because it buys nothing against
a 5 tok/s bottleneck. That reason is still correct for LLM latency and still
insufficient on its own. The better reason to defer is **cost of parts**: queue
mode adds Redis and Postgres to a cluster that currently has three unauthenticated
services on it, and it puts `N8N_ENCRYPTION_KEY` on every worker machine.

**The better reason to eventually turn it on is `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS`
plus `--concurrency` — the only backpressure n8n offers over the exact execution
kind a classroom generates (§3.3).** If the first class shows students wedging
each other with Code nodes, that is the fix, and it is a configuration change
rather than a redesign. **Sequence: run single-process first, watch it break,
then adopt queue mode for the specific reason it breaks.**

---

## 4. A load balancer in front of the `llama-server` endpoints

### 4.1 What it would buy, and what it would not

**It would buy:** one URL for every OpenAI-compatible client on the LAN — n8n
credentials, ComfyUI LLM nodes, a student's `curl`, anything. Endpoints can be
added or drained without reconfiguring twenty students' credentials. That is a
genuine operational win and it is why it is worth doing.

**It would not buy throughput.** Aggregate capacity is R × `--parallel 4` with
or without a proxy. A balancer changes *where* requests wait, not how fast they
are served. **Do not let it be sold as a speed-up.**

**And Missing Link must not go behind it.** Missing Link already fans out across
`LLAMA_URLS` with per-endpoint worker loops, retry-and-resume keyed to the
backend that failed, and health-aware routing
(CONFIRMED, `missing-link/missing_link/app.py`). Putting a proxy in front would
hide the identity of the endpoint that ran a job — which `app.py` deliberately
records per job — and break the one-worker-per-endpoint invariant. **Balancer
for clients; direct URLs for Missing Link.**

### 4.2 `llama-server` is stateful in one specific way — how much does it matter?

**CONFIRMED, read off this machine** (`/opt/llama.cpp/src/tools/server/README.md`,
the pinned b10369 build):

| Flag / route | What it does |
|---|---|
| `-sps, --slot-prompt-similarity` | "how much the prompt of a request must match the prompt of a slot in order to use that slot (**default: 0.10**, 0.0 = disabled)" |
| `--cache-reuse N` | "min chunk size to attempt reusing from the cache via KV shifting" |
| `--cache-idle-slots` | "save idle slots to the prompt cache on new task… (default: enabled)" |
| `GET /slots` | "Returns the current slots processing state… **enabled by default**… per-slot metrics, such as speed, processed tokens, sampling parameters" |
| `GET /metrics` | Prometheus exporter, **only if `--metrics` is set** |

So each server picks the slot whose existing prompt best matches the incoming
one. **A round-robin balancer scatters related requests across servers and
discards that locality.**

**Does it matter at this scale? Mostly no — and here is the split:**

- **For the classroom: no.** Twenty students write twenty different prompts.
  There is no shared prefix to preserve beyond a system message, and any
  server's cache is as cold as any other's. **INFERRED, and it is not close.**
- **For the document workload: yes — which is another reason Missing Link stays
  off the balancer.** Map-reduce chunks share a wrapper prefix, and
  `docs/measurements.md` already shows this engine is sensitive to context
  handling (raising `-c` past what the chunk needs cost **33% more wall-clock on
  identical chunking**). Missing Link's existing per-endpoint affinity is worth
  more than any balancing a proxy could add.
- **If sticky routing is ever wanted anyway**, nginx offers it for free:
  `ip_hash` or `hash <key> [consistent]` (CONFIRMED,
  [ngx_http_upstream_module](https://nginx.org/en/docs/http/ngx_http_upstream_module.html)).
  Per-student stickiness by client IP is one directive.

### 4.3 The candidates

| Option | Slot-aware? | Active health check? | Cost of adoption | Verdict |
|---|---|---|---|---|
| **nginx** | No — `least_conn` counts TCP connections, not slots | **No.** Active `health_check` is **commercial-only**: "Dynamically configurable group with periodic health checks is available as part of our commercial subscription" (CONFIRMED, nginx docs) | Already needed at :80 for basic auth (F54) | **Use it for auth and the directory page. Weak as the LLM balancer** — §4.4 |
| **HAProxy** | No | **Yes, free.** `option httpchk` lets you set method and URL, with `http-check expect` on the status code (REPORTED, [HAProxy health-check tutorial](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/health-checks/)) | One package, one config file, Debian 12 has it | **Recommended.** §4.5 |
| **LiteLLM proxy** | No, but has latency- and usage-based routing strategies (REPORTED, [routing docs](https://docs.litellm.ai/docs/routing)) | Yes | Python service, 57k-star project, config YAML, its own admin UI and virtual keys | **The right answer if per-student API keys are wanted**; otherwise more than is needed |
| **[Paddler](https://github.com/intentee/paddler)** | **Yes — purpose-built for llama.cpp slots** | Yes, via its agents | **See below — this is a trap in its current form** | **Do not adopt as-is** |
| **[llama-balancer](https://github.com/issixx/llama-balancer)** | Yes, plus sticky sessions for prompt cache | Yes | **3 stars, 14 commits, Flask, "Windows-tested", Linux compatibility uncertain** (CONFIRMED, its README + GitHub API) | Reject on maturity |
| **Your own, polling `GET /slots`** | **Yes, trivially** — `/slots` is on by default and reports per-slot state | Yes, and it can probe *progress* | ~100 lines | **The interesting option.** §4.4 |

**Paddler deserves its own paragraph, because the obvious research answer is
out of date and adopting it would breach a standing constraint.**

Paddler v1 was exactly what this project wants: a stateful proxy in front of
*existing* `llama-server` instances, with agents reporting slot status.
**CONFIRMED**, from the v1.0.0 README:

> "Typical load balancing strategies like round robin and least connections are
> ineffective for llama.cpp servers… Paddler is designed to support
> llama.cpp-specific features like slots… llama.cpp instances need to be
> registered in Paddler. Paddler's agents should be installed alongside
> llama.cpp instances so that they can report their slots status to the load
> balancer."

**Current Paddler does not do that.** From the v2.2.0, v3.0.0 and v4.1.0
READMEs, identical text in all three (CONFIRMED, fetched per tag):

> "Paddler uses a **built-in llama.cpp engine** for inference, but has its own
> implementation of llama.cpp slots, which keep their own context and KV cache."

**So adopting current Paddler means replacing the inference engine.** The
cutover was **v2.0.0, 2025-08-08**; the last stable v1 was **v1.2.0,
2024-12-07**, with a single `v1.2.1-rc1` in June 2025 (CONFIRMED, GitHub
releases API). Pinning to v1 means running a 14–20-month-old build of an
abandoned architecture.

That collides head-on with `CLAUDE.md`: *"llama.cpp versions must match exactly
across all nodes"*, the build must be relocatable and ISA-baselined by
`distribute.sh`, and **every measurement in `docs/measurements.md` was taken on
mainline b10369.** Swapping the engine invalidates the chunk-size optima, the
replication measurement and F50's decomposition in one move — and this project
has already been burned once adopting an engine on a benchmark that did not
reproduce the deployment (F40). **Paddler is the technically best-informed
project in this space and it is the wrong tool for this fleet today.**

### 4.4 The trap a naive balancer walks into, and this repo has the receipts

nginx open-source has **no active health checking** (CONFIRMED above) — only
passive `max_fails`/`fail_timeout` on connection errors and timeouts. Now recall
what this project has already measured:

- **F36/F40:** `llama-server` hung *alive* — accepting TCP, answering nothing.
  The forked abort children inherited the listening socket, so a process check
  **and** a port check both said healthy.
- **F51:** a port probe restarted a healthy `rpc-server` because it was **busy
  with the operation the cluster exists to perform**, destroying 5.4 GiB of
  in-flight shard upload. The probe could not distinguish dead from busy.

**A balancer in front of `llama-server` faces exactly that ambiguity, with the
added hazard that it acts on it automatically.** A legitimate request here takes
**77–99 s** for ~1,300 tokens (`docs/measurements.md`); nginx's
`proxy_read_timeout` **defaults to 60s** (CONFIRMED,
[ngx_http_proxy_module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)).
**Left at the default, nginx would kill every real request and mark every
healthy backend as failed.**

Two mandatory settings, and they are the whole reason to write this section:

1. **`proxy_read_timeout` far above the longest legitimate generation.** The
   default of 60s is below the *measured median* for a short request on this
   fleet.
2. **`proxy_buffering off;`** — the default is `on`, and CONFIRMED from nginx's
   docs, "when buffering is disabled, the response is passed to a client
   synchronously, immediately as it is received." **With buffering on, SSE
   token streaming is swallowed and every client sees the whole answer at the
   end.** Which, incidentally, is the same class of error as
   `CLAUDE.md`'s standing warning not to measure TTFT with
   `curl -w %{time_starttransfer}`.

**And the constructive version:** `GET /slots` is enabled by default on this
build and reports per-slot processed-token counts. **A balancer that polls
`/slots` can route on real free capacity and can distinguish BUSY from WEDGED by
watching progress — which is precisely what F36 concluded a liveness probe must
do and what F51 showed a port probe cannot.** That makes a small `/slots`-aware
dispatcher the *architecturally* correct answer, and it shares a signal the
fleet watchdog already needs. It is also new code to own. **Noted as the right
long-term shape, not recommended for this term.**

### 4.5 Recommendation

**Yes, put a balancer in — HAProxy — and size the pool deliberately.**

1. **HAProxy, one backend, three servers, `option httpchk GET /health`**, with
   timeouts raised well past the measured 77–99 s and `option http-server-close`
   or equivalent care taken over streaming. It is in Debian 12, it is one config
   file, it has free active health checks (which nginx OSS does not), and it is
   reversible by deleting that file — which is `CLAUDE.md`'s "prefer reversible
   changes" test.
2. **Keep nginx at :80** for basic auth, the directory page and rate limiting —
   the roles F54 already assigned it. Do not make nginx do both jobs badly.
3. **The pool is the real decision, and it is the operator's.** Balancing
   students across the same endpoints that run the document workload does not
   create capacity — it spreads twenty students' contention over the whole
   fleet. **F54's recommendation stands and outranks this one: give the class
   its own endpoint, ideally a small fast model, and leave the document
   endpoints out of the balancer's pool.** The balancer's value is the single
   URL and the drain-a-node capability, not sharing.
4. **Health check on `/health`, but do not trust it alone.** It is, in
   `CLAUDE.md`'s words, "the server's opinion of itself, delivered through the
   queue it is reporting on." Pair it with the out-of-band watchdog that already
   exists. **A balancer is not a monitor.**
5. **Add `--alias` while you are in there.** `docs/n8n-feasibility.md` §4.1
   notes the model id renders as the full GGUF path because the unit passes no
   `--alias`. Behind a balancer that gets worse — every backend must advertise
   the *same* model id or clients will see three different models. **This is now
   a correctness requirement, not cosmetics.** It is a change to a shared unit,
   so it needs the usual measured-change discipline.

---

## 5. What was NOT verified

- **Nothing here was installed, run or measured.** No ComfyUI, no n8n, no
  SwarmUI, no proxy. Every throughput figure for ComfyUI is arithmetic on top of
  `docs/comfyui-feasibility.md`'s INFERRED per-image times, which are themselves
  derived from third-party benchmarks on different CPUs.
- **Whether a CPU-only ComfyUI works as a SwarmUI remote backend.** SwarmUI
  dispatches to a ComfyUI instance over HTTP and does not itself require a GPU,
  so it should — **INFERRED, not tested**, and it is the single cheapest
  experiment in this document.
- **Whether ComfyUI-Distributed refuses to run without CUDA at code level**, or
  merely lists NVIDIA GPUs as a requirement. Only the README was read.
- **Whether a worker's `--concurrency` actually bounds offloaded manual
  executions** (§3.3). It follows from two quoted sentences in n8n's docs but is
  not stated in one place, and it is the load-bearing claim for a class of 20.
- **The two reported n8n bugs in the offloaded-manual-execution path** (§3.3)
  were not reproduced or checked against current versions.
- **The Model Selector round-robin idea** (§3.4a) — the node is confirmed, the
  rule expression that would rotate endpoints is INFERRED and unbuilt.
- **HAProxy's behaviour with SSE streaming from `llama-server`** was not tested.
  The nginx buffering hazard is CONFIRMED from nginx's docs; the HAProxy
  equivalent was not checked and must be before a class depends on streaming.
- **Whether node 3 is serving `llama-server`.** F56 records node 3 joined,
  hardened and passing the #26500 gate; the R=3 arithmetic here assumes it
  serves. Check `systemctl is-active llama-server@8080` on it rather than
  trusting this sentence.

---

## Sources

**Read off this machine (primary):**
- `/opt/llama.cpp/src/tools/server/README.md` (pinned build b10369) — slot,
  cache, `/slots` and `/metrics` behaviour
- `missing-link/missing_link/app.py` — `LLAMA_URLS`, `_worker_loop`,
  `claim_next_pending`
- `docs/measurements.md`, `docs/FINDINGS.md` (F28, F40, F44, F50, F51, F54,
  F56), `docs/comfyui-feasibility.md`, `docs/n8n-feasibility.md`

**ComfyUI:**
- [ComfyUI Discussion #4139 — Multi-GPU Support](https://github.com/comfyanonymous/ComfyUI/discussions/4139)
- [PR #7063 — MultiGPU Work Units](https://github.com/Comfy-Org/ComfyUI/pull/7063)
- [MultiGPU_WorkUnits / CFG Split node docs](https://docs.comfy.org/built-in-nodes/MultiGPU_WorkUnits)
- [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes)
- [SwarmUI — Using More GPUs](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/docs/Using%20More%20GPUs.md)
- [SwarmUI — ComfyUI Backend README](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/src/BuiltinExtensions/ComfyUIBackend/README.md)
- [ComfyUI-Distributed](https://github.com/robertvoy/ComfyUI-Distributed)
- [ComfyUI_NetDist](https://github.com/city96/ComfyUI_NetDist)
- [ComfyUI_Cluster](https://github.com/nomcycle/ComfyUI_Cluster)
- [ComfyUI-MultiGPU](https://github.com/pollockjj/ComfyUI-MultiGPU)
- [xDiT](https://github.com/xdit-project/xDiT)

**n8n:**
- [Enable queue mode](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode)
- [Queue mode environment variables](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/queue-mode)
- [Control concurrency](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency)
- [Model Selector node](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.modelselector)
- [n8n community #206563 — Code node hangs when offloaded to workers](https://community.n8n.io/t/code-node-execution-hangs-when-offloaded-to-workers-gke-queue-mode/206563)
- [n8n-io/n8n#16932 — partial executions in queue mode](https://github.com/n8n-io/n8n/issues/16932)

**Load balancing:**
- [nginx ngx_http_upstream_module](https://nginx.org/en/docs/http/ngx_http_upstream_module.html)
- [nginx ngx_http_proxy_module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
- [HAProxy health checks](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/health-checks/)
- [LiteLLM routing](https://docs.litellm.ai/docs/routing)
- [Paddler](https://github.com/intentee/paddler) — and its v1.0.0 / v2.2.0 /
  v3.0.0 / v4.1.0 READMEs, fetched per tag
- [llama-balancer](https://github.com/issixx/llama-balancer)

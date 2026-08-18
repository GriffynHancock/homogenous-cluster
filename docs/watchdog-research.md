# Watchdog research: liveness, out-of-band, N-node

Research only. Does not modify `cluster/llama-watchdog.sh` or add finding
numbers — that is the concurrent implementation agent's job, reconciled by the
operator. Every claim below is labelled CONFIRMED (I read the primary source),
REPORTED (a secondary source states it, primary not independently verified),
or INFERRED (my extrapolation, not directly evidenced).

---

## RECOMMENDED DESIGN

**Keep it an external poller. Change the signal it polls, and give it a push
fallback. Do not attempt an engine-level fix on ik_llama.cpp, and do not adopt
Kubernetes-style multi-probe complexity — this project has two nodes, not a
pod fleet.**

### 1. Primary signal: CPU-progress, not `/health`

On this engine, **no HTTP endpoint can be trusted** — CONFIRMED, `/health`,
`/slots` and `/metrics` all resolve through `SERVER_TASK_TYPE_METRICS` posted
onto the same single-threaded queue `update_slots()` drains
(`server.cpp` L726–765, already read by this project). Tonight's incident and
`ik_llama.cpp` issue #1210 (below) are the same bug from two different
directions.

Replace "did `/health` answer" with **"is the process consuming CPU"**:

```
utime+stime at t0, sleep W seconds, utime+stime at t1
delta > threshold  → busy (fine, no matter how long it takes)
delta ≈ 0 AND process is not exiting/restarting → wedged
```

Rationale, evidenced below (Q3): a server mid-prefill pins cores (this
project's own measurements: prefill is compute-bound, ~79% of document
wall-clock, `CLAUDE.md`); a server wedged as in F36 sat at **load 0.11** — CPU
was not merely low, it was idle. That gap is wide enough to threshold on
reliably, and — unlike `/health` — reading `/proc/<pid>/stat` **never queues
behind the thing it is checking**, because it never enters the HTTP server at
all.

Concretely: `HZ` on Linux is normally 100, so `utime+stime` are in centi-
seconds. Sample every `W` = 60s (long enough that a legitimate GC-pause-style
blip does not matter, short enough to bound damage) and require **at least 1
CPU-second of the physical-core budget consumed** in that window before
calling it "busy" — i.e. the process used ≥1/(60×n_threads) of available
capacity, an extremely low bar a wedged process (0.11 load, CONFIRMED F36)
will not clear.

### 2. Fallback / cheap check: TCP + `/health` with a *generous* timeout

Keep a `/health` probe, but treat it only as a **process-liveness gate**, not
as evidence of wedge-vs-busy: if the port isn't even accepting TCP, or
`systemctl is-active` says inactive, that's `Restart=always`'s problem, not
the watchdog's (this is already `llama-watchdog.sh`'s existing logic and
should stay). Raise its timeout well past a prefill step —
this project measured **~15–20s per 512-token prefill step**, so a 25s
timeout is exactly at the edge that caused tonight's false positive; move it
to ≥120s, matching mainline's httplib fix philosophy of "give it enough
rope, don't special-case" (Q1). `/health` alone should never trigger a
restart; it only feeds the CPU-progress decision as a tiebreaker when
`/proc` is unreadable (process gone, container boundary, etc).

### 3. Threshold: two consecutive bad windows, same rationale as today

Keep `THRESHOLD=2` (already in the script, already justified — restarting a
healthy server costs a multi-minute 65GB reload, F3). Apply it to the CPU-
progress signal, not to `/health`.

### 4. Topology: poll-based, off-box, one static list, degrade to
   per-node in-band as a fallback — do not build a per-node agent daemon

- **Node discovery**: reuse `provisioning/nodes.env`'s `NODES=()` /
  `INFERENCE_ENDPOINTS=()` arrays directly (already the canonical fleet list;
  do not invent a second one).
- **Remote action**: `systemctl -H <user>@<node-lan-ip> restart llama-server@8080`
  — this is a **standard, documented systemd feature** (Q4 below), SSH-
  transported, requiring only an authorized key and (per-distro) a polkit
  rule or root SSH. Simpler than hand-rolling an SSH `ForceCommand` wrapper,
  and it is the same trust boundary either way (an SSH key that can restart
  the unit). Scope the key to that one capability with `ForceCommand` or a
  restricted `sudoers` line regardless of which invocation style is used —
  the watchdog's key must not be a general root key on the fleet.
  **CPU-progress reads (`/proc/<pid>/stat`) also need to run over SSH** for
  the out-of-band variant, since a laptop cannot read another machine's
  `/proc`. Same key, `cat /proc/$(systemctl show -p MainPID --value llama-server@8080)/stat`.
- **Single point of failure**: acceptable at N=2, and arguably at any N,
  **provided the watchdog does nothing the cluster depends on to make
  progress** — it only restarts a wedged server, which is strictly better
  than no watchdog even if the watchdog itself is down half the time. This
  matches the Alertmanager dead-man's-switch precedent (Q2): the fix for
  "who watches the watcher" is a *second*, even simpler heartbeat consumer
  (e.g. a systemd timer emailing/logging "still alive" once a day), not a
  second watchdog with the same logic.
- **Congestion caveat (F28)**: LAN RTT goes from 0.83ms idle to 9.5ms
  saturated during a 65GB model transfer. That is still three orders of
  magnitude below a 25–120s health timeout, so **it cannot by itself cause a
  false positive** at the timeouts recommended here — but SSH-based remote
  `/proc` reads over a saturated 100Mb/s link during `distribute.sh` runs
  could plausibly stall long enough to matter if the watchdog's own SSH
  timeout is tight. Give the watchdog's SSH calls a timeout an order of
  magnitude above the RTT-under-load figure (a few seconds is ample), not
  the multi-second default some SSH clients use for `ConnectTimeout`.
- **In-band variant**: identical logic, cron/systemd-timer on node1 itself
  reading its own `/proc` with no SSH hop, restarting its own unit. This is
  what exists today (`llama-watchdog.sh`) and remains useful for *triage*
  (`REQUIREMENTS.md` 2026-08-17: "triage and batching may live on-node;
  LIVENESS may not") but per that same requirement must not be the only copy.

### 5. Restart semantics: do not add draining. Let Missing Link's own
   per-chunk persistence be the recovery mechanism

There is no evidence anywhere in the ecosystems surveyed (Q6) of a load-
balancer-style drain being layered onto a *single-process, single-GPU/CPU-
slot* inference server the way it's done for stateless HTTP fleets — a
`server_task` cannot be handed off mid-generation to a peer, there is only
one copy of the model loaded. The correct analogue is Sidekiq/Celery's
answer to the same problem class (Q6): **make the unit of work small and
idempotent, and let requeue-on-restart do the rest**, not build a drain
window into the supervisor. This project already has exactly that in
`chunk_summaries` (mentioned in `docs/REQUIREMENTS.md`'s 2026-08-17 entry —
independent per-chunk map outputs, safe to resume). **The watchdog's job is
to restart fast, not to wait for a safe point** — waiting is what caused nothing
here since chunks aren't safe *within* themselves, but they are safe *between*
each other, so the worst a mid-restart hit costs is the one in-flight chunk,
not the whole job. Confirm before relying on this: does `missing_link/worker.py`
actually re-fetch persisted `chunk_summaries` rows on resume rather than
restarting the document from chunk 0? (Out of scope for this research task to
verify — flagged for the concurrent implementation/F39 work.)

---

## Evidence per question

### Q1 — How do comparable servers expose liveness, and did they hit this?

**This is the highest-value finding: yes, this is a known, named problem
class across the whole field, and mainline llama.cpp has a real but partial
fix that the fork this project depends on has not received.**

- **llama.cpp mainline `/health` does not touch the task queue.** CONFIRMED/
  REPORTED (DeepWiki + behavioural cross-check): the handler is a lambda
  returning `{"status": "ok"}` from a server-state variable
  (`tools/server/server-context.h`), not a posted task. This is corroborated
  behaviourally by issue **#20921** ("Eval bug: Server freezes
  intermittently, /slots endpoint hangs... /health endpoint still responding
  with OK") — https://github.com/ggml-org/llama.cpp/issues/20921 — open,
  filed 2026-03-23, unresolved, CUDA hardware. So even in current mainline,
  **`/slots` and `/metrics` share the queue-blocking failure mode this
  project hit; only `/health` is exempt**, and that exemption is architectural
  (a separate code path), not a queue-priority trick.
- **`ik_llama.cpp`'s `/health` was not built this way — it takes the
  `/metrics`-style path instead.** CONFIRMED by this project's own source
  read (`server.cpp` L726–765, cited in the task) and independently REPORTED
  by **ikawrakow/ik_llama.cpp#1210**,
  https://github.com/ikawrakow/ik_llama.cpp/issues/1210 — "Bug: llama-server
  /slots and /health endpoints unresponsive while prompt processing," filed
  2026-01-31 by a **CPU-only dual-Xeon** user (this project's exact hardware
  class), open, **no maintainer response as of this research**. This is
  strong independent confirmation the bug is real, upstream, unfixed, and
  specifically a CPU-prefill-duration problem, not a one-off on this fleet.
- **A different, earlier llama.cpp bug (#20684) was about the HTTP layer,
  not the task queue, and its fix is not the fix this project needs.**
  https://github.com/ggml-org/llama.cpp/issues/20684, closed via **PR #20817**
  (https://github.com/ggml-org/llama.cpp/pull/20817, merged 2026-03-23):
  the fix replaced a small fixed-size httplib thread pool with
  `httplib::ThreadPool(n_threads_http, n_threads_http + 1024)` so concurrent
  HTTP connections don't queue behind each other at the *socket-accept*
  layer (cites https://github.com/yhirose/cpp-httplib/pull/2368 upstream).
  The PR reviewer's own comment, per the fetched summary: "not the ideal
  solution" but "good enough for all practical purposes." **This fixes HTTP
  thread starvation (e.g. one slow tokenizer call blocking the listener), a
  different failure mode from a handler that deliberately posts onto the
  inference task queue and blocks on the result — which is what both
  `ik_llama.cpp`'s `/health` and mainline's own `/metrics`/`/slots` still do.**
  **Conclusion for this project: cherry-picking is not attractive.** The one
  fix that would actually help (`/health` bypassing the queue entirely) is
  not a discrete patch — it is mainline's `/health` simply never having been
  built to touch the queue, which is a structural difference from
  `ik_llama.cpp`'s implementation, not a bolt-on. Porting it means rewriting
  `ik_llama.cpp`'s handler, in a hard fork this project has already found
  (F32, per repo convention) is not kept in sync with mainline server code.
  Given `CLAUDE.md`'s standing rule to pin releases and never build per-node,
  taking on a local server.cpp patch is exactly the kind of unreversible,
  unmeasured change the project's north star warns against. **External
  polling remains the right layer**, now informed by knowing precisely why.
- **vLLM hit the identical shape of bug and reached the identical
  conclusion (external/architectural separation, not "trust the same-process
  handler").** https://github.com/vllm-project/vllm/issues/24910, closed via
  PR #26134: `/health` became unresponsive for **53+ seconds** while a single
  request's chat-template tokenization ran on the sole HTTP-handling thread —
  same root shape as this project's problem (one queue, one worker, health
  waits behind real work), different bottleneck stage (tokenization vs
  prefill). vLLM's health-check design otherwise explicitly separates a
  cheap process check from a `call_utility_async()` path — REPORTED, not
  independently source-read here — precisely to avoid contending with
  inference. vLLM also explicitly **rejected caching health results in the
  background**, reasoning that a cached result is stale and Kubernetes
  already owns interval control (REPORTED, from issue thread synthesis) —
  worth noting because it is the inverse design choice from this
  recommendation's CPU-progress cache, and the difference is that vLLM's
  `/health` is architecturally cheap so it doesn't need a cache, while this
  project's engine's `/health` is not, so a side-channel is the only option.
- **HuggingFace TGI has an explicit, still-open, feature request for the
  exact Kubernetes-shaped fix** —
  https://github.com/huggingface/text-generation-inference/issues/3241,
  opened 2025-05-22, **open at time of research (repo itself archived
  2026-03-21)** — asking for `/livez` (process-only) vs `/readyz`
  (queue-depth-aware) so a full queue triggers *traffic removal*, not a
  restart. Directly validates this project's F31/F36-driven instinct that
  conflating "busy" with "broken" causes false restarts, but TGI never
  shipped it, so it is evidence for the *design principle*, not a
  ready-made component.
- **Ollama's `/` health check is deliberately trivial** — REPORTED,
  https://thushan.github.io/olla/concepts/health-checking/ — "no state, no
  processing, no failure modes," i.e. Ollama's liveness story is the
  mainline-`/health` shape (cheap, separate), not the queue-coupled shape.
  Consistent with, not additional evidence beyond, the llama.cpp mainline
  finding.
- **Ray Serve's health check is a plain actor call with a generous default
  timeout** — REPORTED, https://docs.ray.io/en/latest/serve/monitoring.html
  — `health_check_period_s=10`, `health_check_timeout_s=30`, i.e. Ray Serve's
  own defaults already assume a check can legitimately take up to 30s, three
  times its own period — an explicit acknowledgment that a single slow
  response should not immediately read as dead. Weak supporting evidence for
  "generous timeout, not tight one" as the standard posture even in a
  purpose-built serving framework.

### Q2 — What does the wider ops world consider correct?

- **Kubernetes' own docs draw exactly the line this project needs and got
  bitten for crossing.** CONFIRMED by direct fetch,
  https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/:
  *"Liveness probes determine when to restart a container… readiness probes
  determine when a container is ready to accept traffic"* and, on the
  dependency question directly on point here: *"The liveness probe passes
  when the app itself is healthy, but the readiness probe additionally
  checks that each required back-end service is available."* **This
  project's structural problem is that `/health` cannot even play the
  liveness role as K8s defines it, because on this engine there is no code
  path that checks "is the app itself healthy" without also touching the
  thing readiness is supposed to check (the inference queue).** That is
  exactly why an OS-level (CPU-progress) signal is needed instead of a
  better HTTP endpoint — it is the only channel left that inspects "is the
  app itself healthy" without going through the queue.
- **Kubernetes' own caution text is close to a description of tonight's
  incident.** CONFIRMED, same doc: *"Incorrect implementation of liveness
  probes can lead to cascading failures… restarting of container[s] under
  high load."* This is the F36-fix-caused-a-false-positive story, restated
  as a known, named anti-pattern a decade old, not a novel failure this
  project discovered.
- **`startupProbe` is precedent for "give slow-but-legitimate work its own
  budget rather than tightening the steady-state check."** CONFIRMED, same
  doc: *"Rather than set a long liveness interval, you can configure a
  separate configuration for probing the container as it starts up."*
  Analogous recommendation for this project: rather than shortening the
  steady-state timeout to catch wedges fast, give the CPU-progress signal
  the discriminating power and let `/health`'s timeout be generous — the
  two-signal split *is* this project's startup/liveness split, just mapped
  onto busy/wedged instead of starting/started.
- **`systemd` `WatchdogSec` + `sd_notify(WATCHDOG=1)` inverts the whole
  problem, and is worth naming even though it isn't adoptable here.**
  CONFIRMED, https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html:
  the *service* pushes `WATCHDOG=1` at `WatchdogSec/2` intervals; systemd
  kills it if the ping stops. This is immune to F36's failure mode by
  construction — a push can't queue behind work the way a pull-probe can, if
  and only if the push happens from a thread that itself never blocks on the
  task queue. **This is not adoptable without an upstream patch**: it
  requires instrumenting `ik_llama.cpp`'s own event loop to call
  `sd_notify`, which is the same "patch the fork" cost already rejected in
  Q1, for the same reason. Recorded here because it is the theoretically
  cleanest fix, in case a future ik_llama.cpp release adds it, or in case
  this project ever decides the patch is worth it.
- **Prometheus's "Dead Man's Switch" / Alertmanager Watchdog pattern is the
  right shape for "who watches the watchdog," not for node liveness itself.**
  CONFIRMED (blog + issue thread cross-read),
  https://github.com/prometheus/alertmanager/issues/1542 and
  https://blog.ediri.io/how-to-set-up-a-dead-mans-switch-in-prometheus: an
  always-firing alert (`expr: vector(1)`) routed to a heartbeat receiver;
  silence, not a specific alert, is the signal something broke. Directly
  applicable to "the watchdog itself is down" (recommendation §4) — a daily
  "I am alive" ping from the watchdog to somewhere the operator actually
  looks, rather than a second watchdog watching the first.
- **`monit`/Nagios-style external checks are architecturally what this
  project is already doing** (an out-of-process poller with a
  restart action) — REPORTED, general survey of
  https://exchange.nagios.org/directory/plugins/system-metrics/cpu-usage-and-load/…
  and the Nagios/monit plugin ecosystem: their process-liveness plugins
  routinely check **CPU/memory consumption of a named process**, not just
  "is it listening," which is independent corroboration that CPU-based
  process checks are a long-established pattern in that world, not
  something invented for this project.

### Q3 — Push vs poll: does "silent but burning CPU" actually distinguish
   busy from wedged, and is there prior art?

- **The distinction holds on this project's own numbers.** CONFIRMED,
  `CLAUDE.md`/`docs/FINDINGS.md`: F36's wedge sat at **load 0.11**; this
  project's own measured workload characterisation says generation runs at
  ~99%/~61% of achievable memory bandwidth and prefill is compute-bound and
  dominant (~79% of wall-clock) — i.e. a healthy server doing real work is
  never near-idle for long. The gap between "0.11 load" and "cores pinned
  doing prefill" is not a close call.
- **`/proc/<pid>/stat` `utime`+`stime` advancing is the standard, textbook
  way to distinguish "blocked" from "busy" for a single process** —
  REPORTED, general survey (Aaron Tomlin's per-process CPU accounting
  writeup, Ubuntu `proc_pid_stat` manpage): *"If `utime` hasn't increased
  over N seconds, it's likely blocked — not busy."* This is not a llama.cpp-
  specific insight; it's the general-purpose Linux process-liveness idiom,
  and it composes naturally with a strace-based sanity check (*"a stalled
  process shows repeated `futex`/`epoll_wait`, not `write`/`read`"*) if the
  CPU-progress signal alone is ever ambiguous — not needed for the
  recommended design, but a cheap escalation step if false positives recur.
- **Distributed-ML systems use exactly this shape of signal at larger
  scale, which is the closest prior art to "CPU-progress-based liveness for
  an inference/training process" specifically.** REPORTED, general survey
  (CoreWeave/Google Cloud/Nebius writeups on straggler and hang detection in
  multi-GPU training): hang detection there is built on **accelerator
  utilization not advancing** combined with **collective-communication
  timeouts** (e.g. PyTorch's `TORCH_NCCL_TRACE_BUFFER_SIZE`), not on an
  application-level health endpoint — for the same underlying reason this
  project found: a training rank, like a wedged inference server, can hold
  open sockets and answer *some* introspection while making zero progress.
  This is INFERRED as directly-applicable prior art (it's GPU utilization,
  this project needs CPU utilization) rather than CONFIRMED for this exact
  case, but the mechanism-level argument transfers cleanly: **check the
  resource the workload should be consuming, not a socket the workload
  happens to still be listening on.**

### Q4 — Multi-node design

- **Node discovery**: use `provisioning/nodes.env`'s existing `NODES` and
  `INFERENCE_ENDPOINTS` arrays (read directly, confirmed present and already
  the canonical list per `CLAUDE.md`'s file index).
- **Remote action — `systemctl -H user@host`** is a real, documented,
  built-in systemd feature, not something to hand-roll: CONFIRMED via
  Arch Wiki / Oracle Linux docs cross-read
  (https://wiki.archlinux.org/title/Systemctl,
  https://docs.oracle.com/en/operating-systems/oracle-linux/9/systemd/RunningsystemctlonaRemoteSystem.html)
  — it tunnels the systemd D-Bus API over SSH. Needs an SSH key plus (per
  distro) either root SSH or a polkit rule granting that specific unit
  action to a non-root user — the latter is the better-scoped choice and
  should be preferred over a general root key, independent of whether the
  watchdog uses `systemctl -H` or a plain `ssh host 'systemctl restart ...'`
  one-liner (functionally equivalent; `-H` is slightly more self-documenting,
  a raw SSH command is easier to audit/restrict with `ForceCommand`).
- **Avoiding SPOF**: see recommendation §4 — accept it at N=2, mitigate with
  a dead-man's-switch-style heartbeat from the watchdog itself (Q2), not a
  second copy of the same logic. A **per-node lightweight agent daemon**
  (rather than a poller with SSH) was considered and is **not recommended**:
  it reintroduces exactly the F36 shape of risk (a daemon on the monitored
  machine can itself hang) for no measured benefit over SSH+systemd at this
  node count; nothing in the research surfaced above uses a bespoke agent
  daemon in preference to SSH/D-Bus at small scale — Nagios/monit's classic
  external-check model is SSH- or NRPE-based for exactly this reason.
- **Reporting when the down thing is what you'd read the report on**: this
  is the entire argument in `docs/REQUIREMENTS.md`'s 2026-08-17 entry and
  `CLAUDE.md`'s "agent appliance" section — an off-box watchdog with its own
  log/notification path (not one that writes only to the cluster it
  monitors) is the direct fix, already decided by the operator, not a new
  finding from this research.
- **Congestion (F28)**: see recommendation §4 — three orders of magnitude of
  headroom between measured worst-case RTT (9.5ms) and any sane probe
  timeout (seconds), so this is a non-issue **provided the watchdog's own
  SSH/HTTP client timeouts aren't set naively short** (default `curl`/`ssh`
  connect timeouts are sometimes single-digit seconds, which is still fine
  here, but worth setting explicitly rather than relying on client
  defaults during a 97-minute, F23/nodes.env-documented model transfer).

### Q5 — LangChain / LlamaIndex

**Honest answer, as the operator asked for: neither has anything resembling
a watchdog. Both are retry-and-timeout client libraries, and this project's
own experience (F36's `DEFAULT_TIMEOUT_S=3600`, `assert_reachable()`) is
already more sophisticated than either's out-of-box defaults.**

- **LangChain**: default request timeout on the OpenAI-compatible client
  has moved around across versions — REPORTED, mixed evidence: older docs
  cite 60s, current `openai`-SDK-backed defaults are commonly cited at
  600s, with `max_retries` defaulting to **2 in the OpenAI/Anthropic SDKs
  themselves** (not 3–6 as this task's brief assumed — flagging the
  correction) — and a real trap independently confirmed by community
  discussion (GitHub discussion #7987,
  https://github.com/langchain-ai/langchain/discussions/7987 for the
  Azure variant): **each retry re-waits the full timeout**, so
  `timeout=30, max_retries=2` can mean 90+ seconds of silent waiting, which
  is a *retry storm against a multi-minute backend* in miniature — the
  exact shape of problem this task's brief warned about, CONFIRMED as a
  real, documented gotcha even if the specific numbers in the brief were
  slightly off.
- **LlamaIndex**: REPORTED, https://github.com/run-llama/llama_index/issues/17756
  and PR #17755 — the OpenAI LLM integration's retry *decorator* had a
  **hardcoded 60s timeout independent of the user-configured timeout**,
  filed as a bug, fixed by making it configurable. Confirms LlamaIndex's
  retry story was, at least until that fix, not even internally consistent,
  let alone backend-aware.
- **Neither library has any concept of "the backend is wedged but
  answering."** Their retry/timeout logic treats "slow" and "dead" the same
  way this project's own `/health` probe did before tonight — as a
  timeout-and-retry problem, not a liveness problem — which is a category
  difference from what a watchdog does. **A circuit breaker is the right
  client-side complement, but it solves a different problem than the
  watchdog**: a circuit breaker (CLOSED/OPEN/HALF-OPEN, REPORTED, general
  pattern survey) stops **Missing Link's worker** from hammering a backend
  it already knows is bad, cheaply and fast, while the **watchdog is what
  makes the backend good again**. Missing Link already has the watchdog-
  adjacent half (`assert_reachable()`, F36) — a circuit breaker around
  repeated `BackendUnavailable` would be a reasonable, low-risk addition to
  the worker side specifically to avoid retry storms across R endpoints once
  fan-out lands (`CLAUDE.md`: "must fan out across R endpoints"), but it is
  additive to, not a substitute for, the watchdog.

### Q6 — Restart semantics and in-flight work

- **No prior art anywhere surveyed drains a single-process, single-model-
  copy inference server the way a stateless HTTP fleet is drained.** The
  concept doesn't transfer: draining assumes a load balancer can route
  around the draining instance while it finishes; there is no peer to route
  to for one loaded model on one machine. REPORTED-negative finding — this
  is confidence from absence across the vLLM/TGI/Ray Serve/K8s material
  surveyed for Q1/Q2, not a specific source stating "we don't do this."
- **The applicable prior art is job-queue-level, not server-level**:
  Sidekiq's and Celery's answer to "a worker got killed mid-job" is a grace
  window plus **requeue**, not draining the process — REPORTED,
  https://gist.github.com/miry/1d9af63aaa74c4bde705493dc0792bf0 and general
  Sidekiq/Celery docs survey: Sidekiq gives in-flight jobs ~25s to finish on
  SIGTERM then requeues; Celery similarly warm-shuts-down before a forced
  kill. Both explicitly recommend **idempotent, small units of work** as the
  actual fix, not a smarter shutdown sequence. This maps directly onto this
  project's `chunk_summaries` design (recommendation §5) — the map step is
  already the right grain of idempotent unit, and REQUIREMENTS.md records
  the 2026-08-17 realization that resume can already reuse it.
  **Not independently re-verified in this research task** whether
  `missing_link/worker.py`'s resume path actually does re-fetch persisted
  `chunk_summaries` rather than starting a document from chunk 0 — flagged
  as a fact to confirm, not asserted here.
- **A watchdog that waits for a "safe point" before restarting is not
  supported by anything found, and is actively discouraged by the deadlock-
  detection framing K8s itself uses** ("a liveness probe exists to catch a
  deadlock" — CONFIRMED quote above): the entire point of a liveness
  restart is that the process **cannot** reach a safe point on its own by
  definition — if it could, it wouldn't need to be killed. Waiting for one
  reintroduces the exact hour-long stall F36 already diagnosed and fixed via
  `DEFAULT_TIMEOUT_S`. **Restart fast; make the unit of work small enough
  that the loss is cheap** is the consistent answer across every system
  surveyed, not "restart carefully."

---

## What the evidence does not support

- **It does not support patching `ik_llama.cpp`'s `/health` to match
  mainline.** The mainline fix is structural (a code path that was simply
  never coupled to the queue), not a discrete cherry-pickable patch, and
  taking it on means maintaining a local fork divergence this project has
  already found costly once (F32 — `ik_llama.cpp` build distribution gap).
  Not worth it for a signal this design replaces anyway.
- **It does not support a per-node agent daemon** as superior to SSH +
  `systemctl -H` / a plain restricted SSH command at N=2. Nothing surveyed
  uses one at comparable scale; it adds a new process that can itself hang.
- **It does not support Kubernetes-style three-probe (startup/liveness/
  readiness) complexity for this project.** The three-probe model earns its
  complexity at fleet scale with a scheduler that can route around a
  not-ready pod. This project has R independent servers and a queue that
  already retries against `INFERENCE_ENDPOINTS` (per `CLAUDE.md`'s stated
  direction) — that queue-level retry *is* this project's readiness
  concept, and does not need a second, K8s-shaped implementation bolted on.
- **It does not support `sd_notify`/`WatchdogSec` as an adoptable fix right
  now**, only as the theoretically correct answer if the engine is ever
  patched — see Q2.
- **It does not support treating the CPU-progress threshold numbers above
  (60s window, 1 CPU-second) as measured.** They are reasoned from this
  project's own already-measured F36 load figure (0.11) and prefill-share
  figure (~79%), but the threshold itself has not been run against this
  hardware. See the experiment below.
- **It does not support any claim about GPU-specific tooling (DCGM, NCCL
  trace buffers) being directly usable here** — this fleet is CPU-only
  (`CLAUDE.md`, settled). Cited only as mechanism-level prior art (Q3).

---

## Cheapest experiment that would settle the biggest remaining uncertainty

**Biggest uncertainty: does `utime+stime` actually stay flat during F36's
specific wedge, or was load 0.11 an artifact of something else (e.g. a
thread parked in a blocking syscall that `top`'s load-average smooths over
differently than `/proc/<pid>/stat` would)?** The recommended design's entire
value proposition rests on that gap being real and large, and it has been
measured once, indirectly (via `uptime`/load average), not directly via
`/proc/<pid>/stat`.

**Cheapest test**: reproduce F36's exact trigger — `systemctl restart
missing-link` (or any client) mid-generation against a real document chunk —
and this time, **before restarting `llama-server`**, capture
`cat /proc/$(systemctl show -p MainPID --value llama-server@8080)/stat` twice,
60 seconds apart, alongside a normal `curl --max-time 120 /health` attempt (to
also confirm the new, longer timeout doesn't itself resolve fast enough to be
useless). Three outcomes settle it directly:

1. `utime+stime` delta ≈ 0 across the 60s window → confirms the design;
   ship the threshold as-is.
2. `utime+stime` delta is small-but-nonzero → the 1-CPU-second bar may be
   wrong; use this one real trace to set it correctly instead of guessing.
3. `/health` at a 120s timeout actually returns during the wedge → F36's
   wedge and tonight's false positive have different root causes and the
   whole `/health`-is-unusable premise needs re-examination before shipping
   anything.

This requires no new tooling — `systemctl show`, `/proc`, and `curl` are
already on every node — and reuses a trigger this project already knows
reproduces the bug, rather than waiting for it to recur naturally.

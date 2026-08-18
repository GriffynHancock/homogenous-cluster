#!/usr/bin/env bash
# Cluster liveness watchdog: restart what is WEDGED, never touch what is merely
# BUSY or legitimately IDLE. Runs IN-BAND on a cluster node or OUT-OF-BAND on a
# separate machine, from this one file.
#
# Usage:
#   llama-watchdog.sh                 # one sweep of every service in nodes.env
#   llama-watchdog.sh --report        # same probes, never acts, prints a table
#   llama-watchdog.sh 8080            # legacy: this host's llama-server on 8080
#
# Environment (all optional):
#   WATCHDOG_MODE=auto|local|ssh      transport (default auto: local for this
#                                     machine's own IPs, ssh for everything else)
#   WATCHDOG_SERVICES=llama,rpc,ml    which predicates to run (default all)
#   WATCHDOG_ONLY=host[,host...]      restrict the sweep to these hosts
#   WATCHDOG_SSH_USER / _SSH_KEY      identity for the restricted watchdog key
#   WATCHDOG_WINDOW                   CPU observation window, seconds (default 10)
#   WATCHDOG_GRACE / _RPC_GRACE       unbroken suspect seconds before restarting
#   WATCHDOG_HEARTBEAT_URL            optional POST target for the dead-man's switch
#   WATCHDOG_DRY_RUN=1                decide and log, never restart
#   WATCHDOG_NPROCS_LLAMA / _RPC      process-count baseline (default 2 / 1);
#                                     the F40 abort-fork tripwire
#   WATCHDOG_ABORT_GRACE              confirming seconds for an nprocs anomaly
#                                     before restarting (default 90)
#   WATCHDOG_SYNTH_ENABLE=0           disable the synthetic transaction (default on)
#   WATCHDOG_SYNTH_INTERVAL_S         min seconds between synth attempts/unit (900)
#   WATCHDOG_SYNTH_MAX_TOKENS         synth completion token budget (default 96)
#   WATCHDOG_SYNTH_TIMEOUT_S          synth HTTP timeout, seconds (default 60)
#   WATCHDOG_SYNTH_FAIL_STREAK        consecutive failed attempts before acting (2)
#
# ===========================================================================
# WHY THIS EXISTS
#
# **F36 (2026-08-17).** A client disconnected mid-generation and left
# llama-server **alive, accepting TCP, and serving nothing**: /slots returned no
# response, a trivial completion timed out after 85 s, and the machine sat at
# load 0.11. `Restart=always` cannot see that, because the process never exited.
#
# **Node 2 (2026-08-18).** ik_llama.cpp hit a fatal error in its flash-attention
# kernel mid-benchmark (`iqk_flash_attn_noalibi: found empty attention mask`,
# `iqk_flash_attn.cpp:347: Fatal error`) and **the process still did not exit**:
# `MainPID=16040`, `NRestarts=0`, `SubState=running` -- systemd believed it
# healthy -- while /health returned nothing and the node sat at load 0.02.
#
# That is two confirmed "up and lying" failures, the second with a definite
# cause, and node 2 **had no watchdog installed at all** because the units lived
# only in /etc/systemd/system on node 1 and were untracked in git. The CPU
# predicate below would have caught it in about five minutes. Provisioning is
# therefore part of the fix, not an afterthought: see cluster/install-watchdog.sh
# and its hook in cluster/install-services.sh.
#
# ---------------------------------------------------------------------------
# WHY IT DOES NOT PROBE /health FIRST (F39)
#
# The first watchdog probed `/health` with a 25 s timeout and restarted after two
# consecutive failures. **It destroyed a healthy job 79 minutes after it was
# installed** -- 10m55s of real work on a 97,299-character document.
#
# ik_llama.cpp's `handle_health` does not answer out of band: it posts a
# SERVER_TASK_TYPE_METRICS task onto `ctx_server.queue_tasks` -- the same queue
# `update_slots()` drains for token generation -- and blocks on
# `queue_results.recv()`. `/slots` and `/metrics` are identical. All three go
# quiet exactly when the server is busiest, and their payloads lie too: during
# prefill `/health` reports `n_idle_slots=4 n_processing_slots=0`.
#
# ---------------------------------------------------------------------------
# THE ORDERING IS THE DESIGN: CPU FIRST, HTTP ONLY IF CPU SAYS IDLE
#
# Two evidence-backed positions pointed opposite ways on the probe timeout.
# `docs/watchdog-research.md` said make it LONG (>=120 s), because measured
# legitimate `/health` latency is 97-130 s on a healthy server. F39 said make it
# SHORT (20 s), because `n_threads_http=7` and every timed-out probe pins one of
# those seven workers in `queue_results.recv()` until the queue drains -- so a
# long timeout has the monitor progressively consuming the thread pool it is
# monitoring.
#
# Both are right, and the framing was wrong. **The timeout only has to be chosen
# for probes that are actually issued, and reordering the checks means no probe
# is ever issued to a busy server.** CPU is sampled first; if the unit is burning
# CPU the check exits without touching HTTP at all.
#
# That leaves `/health` to be evaluated in exactly one situation: the unit's
# cgroup consumed no CPU over the window. Measured on node 1, 2026-08-18, with
# the server idle -- which is what CPU-flat means when the server is healthy:
#
#     /health, 20 consecutive samples: 0.736 - 0.912 ms   (20/20 HTTP 200)
#     /props,   5 consecutive samples: 0.649 - 0.710 ms
#
# So in the only branch where it is asked, `/health` answers in under a
# millisecond or it does not answer at all. Any timeout above a few hundred
# milliseconds gives the identical verdict. **The timeout carries no diagnostic
# weight**, which is why it is a flat 5 s -- not tuned, just comfortably above
# every transport hazard (F28: RTT 0.827 ms idle, 9.544 ms while a single rsync
# saturates the 100 Mb link; 5 s is ~520x the congested figure).
#
# The cost avoided was measured, not argued. During the busy test, one
# `--max-time 5` /health probe was answered by the server **75 seconds later** --
# that is 75 s of one of seven http workers pinned in `queue_results.recv()`, per
# probe, per tick. Across a ten-tick busy run the reordered watchdog issued
# **zero** HTTP requests.
#
# ---------------------------------------------------------------------------
# THE SIGNAL, AND WHY IT IS THE SAME ONE IN BOTH MODES
#
# Both modes call `cluster/llama-watchdog-agent.sh` -- the same file, the same
# arithmetic -- and differ only in whether it is reached over SSH. It reports the
# unit's **cgroup** CPU nanoseconds either side of a window timed on the
# monitored node's own clock.
#
# Read that agent's header for why the cgroup and not `/proc/<MainPID>/stat`: on
# this fleet `MainPID` is the `/bin/sh -c` wrapper from `llama-server@.service`,
# which never execs away, so its `utime+stime` is identically **0** while the
# server burns 400% of a core. The research document's recommended signal would
# have restarted every busy server.
#
# Measured separation on node 1, 2026-08-18 (gpt-oss-120b, `-t 4`):
#
#   | state                          | 3 s window | 10 s window | 20 s window |
#   |--------------------------------|-----------:|------------:|------------:|
#   | idle                           |       0 ms |        0 ms |        0 ms |
#   | ONE real completion in flight  |  12,028 ms |   40,046 ms |           - |
#
# Exactly zero against exactly 400% of one core, holding across six consecutive
# 10 s windows spanning both prefill and generation. Machine-wide load average
# was 2.27-3.09 throughout, from unrelated agent processes -- which is why load
# average was rejected (F39: node 1 read load 2.30 while llama-server was
# completely idle). Cgroup accounting is per-unit and immune, in both modes,
# because it is literally the same reading.
#
# ---------------------------------------------------------------------------
# ONE PREDICATE PER SERVICE. THE CPU SIGNAL DOES **NOT** GENERALISE.
#
# This is the part that would make a watchdog worse than useless if it were got
# wrong, so it is stated plainly:
#
#   llama-server -- busy = pinning cores, wedged = silent AND no CPU.
#                   Measured above. CPU-progress is decisive here and ONLY here.
#
#   rpc-server   -- **legitimately idle whenever no shard work is in flight.**
#                   Idle is its NORMAL state. Applying the CPU predicate would
#                   restart every RPC worker on the fleet the moment the cluster
#                   went quiet. Measured on node 1: rpc-server@50052 burned
#                   0 ns over a 3 s window while perfectly healthy.
#                   A cross-node "coordinator busy, peer idle" correlation was
#                   considered and REJECTED as dangerous: within a shard group
#                   nodes run SEQUENTIALLY (CLAUDE.md: utilisation is 1/S), so
#                   "coordinator burning CPU while the peer is idle" is the
#                   NORMAL state for much of every step, not a fault.
#                   So this predicate is deliberately WEAK: unit active, and the
#                   RPC port still accepting a connection ON THE NODE. It never
#                   restarts on CPU, and it cannot see a hung-but-listening
#                   worker. That limitation is accepted rather than papered over.
#
#   missing-link -- an async web app that **idles when the queue is empty**,
#                   which is the correct and common state. Measured idle CPU is
#                   ~0.17% of a core (uvicorn's event loop), so CPU is useless as
#                   a fault signal here too. Its predicate is "HTTP answers",
#                   plus a REPORT-ONLY progress check against its own SQLite job
#                   store when jobs are running.
#
# Adding a service means adding a `predicate_<kind>` function and one line in
# `load_targets`. The core loop, the transport, the streak accounting, the blast
# guards and the heartbeat are all shared and untouched. That is the same seam
# discipline CLAUDE.md asks for between the queue and the task profiles.
#
# ---------------------------------------------------------------------------
# 2026-08-18: WHY THE SERVER'S OWN OPINION OF ITSELF IS NOT ENOUGH, AND WHAT
# WAS ADDED. Framed by the operator directly: "why are we depending so much
# on the backend to report itself?" -- which is the disease behind every
# incident logged above. `/health` is the server's opinion of itself, routed
# through the very queue it is meant to be reporting on (F39); `Restart=
# always` trusts the process table; a port check trusts the listen backlog --
# and F40 showed all three defeated AT ONCE by one mechanism: a forked
# ggml_abort() backtrace child that deadlocks, never exits, and INHERITS the
# listening socket. Two things were added, cheapest first, both additive to
# every check above rather than replacing any of them:
#
# 1. THE F40 TRIPWIRE (nprocs). Free -- it reads a number already being
#    fetched for SRVPID discovery, no new probe, no new round trip. A forked
#    child is a real process and gets its own entry in the unit's
#    cgroup.procs; a thread never does. MEASURED baseline, both nodes,
#    servers healthy and BUSY (400% of a core, so this is not an idle-only
#    artefact): llama-server@.service = 2 procs (the `sh -c` wrapper + the
#    server), rpc-server@.service = 1 proc (no wrapper). A count ABOVE
#    baseline, confirmed on a second sample ABORT_GRACE seconds later
#    (default 90s -- shorter than GRACE's 300s because this is structural
#    evidence, not an absence-of-response inference), goes straight to
#    WEDGED. CONFIRMED LIVE 2026-08-18: this is not an ik_llama.cpp-only
#    hazard -- `/opt/llama.cpp/src/ggml/src/ggml.c` (the mainline binary this
#    fleet now runs, per F40's own reversal) has the IDENTICAL
#    fork()/waitpid(child_pid, NULL, 0) code at the same call site. Moving off
#    ik_llama.cpp did not retire this risk; whatever next triggers a
#    ggml_abort() on mainline can reproduce F40 exactly. This check is the
#    fleet's only coverage for that until upstream changes the mechanism, and
#    it is now the same check for BOTH llama-server and rpc-server, since
#    both link the same libggml.
#
# 2. THE SYNTHETIC TRANSACTION (`synth`, llama-server only -- see the ladder
#    note below for why rpc-server does not get one). A tiny real completion,
#    issued by `llama-watchdog-agent.sh`'s `synth` verb against
#    127.0.0.1:<port> only, content-validated with the SAME rules
#    `missing_link/worker.py:extract_content` enforces on every real chunk --
#    empty content is a failure (F21), non-empty content with
#    finish_reason="length" is a failure (F34's truncation, not merely a
#    formatting quirk), and a model that spent its whole budget on
#    reasoning_content with nothing in content is the same failure by a
#    different route. Ported deliberately rather than imported, so the agent
#    stays a dependency-free script deployable to a bare node -- see the
#    agent's own comment for the cost this seam carries. This is the one
#    check nothing else in the ladder can substitute for: on MAINLINE,
#    `/health` never touches the inference queue at all
#    (`docs/watchdog-research.md` Q1) -- it is a lambda over a server-state
#    variable, which is why it is cheap AND why answering proves nothing
#    about whether a completion actually works. Only a live tokenizer ->
#    queue -> compute -> detokenize -> response round trip can do that, and a
#    listen backlog cannot fake generated text the way it can fake an open
#    port.
#
#    ESCALATION LADDER (cheapest first, each rung reached only when the
#    previous one is ambiguous or clean):
#
#      CPU progress -> nprocs tripwire -> /health -> synthetic transaction
#
#    The synthetic transaction sits behind /health deliberately: it is
#    reached ONLY on the branch where the unit already looks perfectly
#    healthy by every cheaper signal (idle, nprocs at baseline, /health
#    answers) -- which is exactly the blind spot this closes, and exactly why
#    it inherits F39's CPU-first discipline rather than adding a new one: it
#    is NEVER issued while the CPU sample showed load, because that branch
#    returns long before this code is reached.
#
#    COST AND RATE-LIMITING. A synthetic transaction competes with real work
#    for one of four --parallel slots, so it is deliberately kept small and
#    rare: WATCHDOG_SYNTH_INTERVAL_S (default 900s) bounds it to at most 4
#    attempts/hour/unit, and WATCHDOG_SYNTH_MAX_TOKENS (default 96, sized
#    against worker.py's OWN measured gpt-oss reasoning-token range of
#    49-129 tokens) bounds each attempt's generation cost. From documented
#    rates (F17/F24/F27) that is a WORST CASE of roughly 16s of one slot,
#    every 15 minutes -- under 2% duty cycle on a quarter of the server's
#    capacity. STATED AS AN ESTIMATE, NOT MEASURED: both fleet nodes were
#    running real document jobs throughout this change (a hard constraint of
#    the task that built it), so this has not yet been timed live. Run once a
#    node is idle:
#      curl -s -w '\ntotal=%{time_total}s\n' localhost:8080/v1/chat/completions \
#        -H 'Content-Type: application/json' -d \
#        '{"messages":[{"role":"user","content":"Reply with the single word: OK."}],"max_tokens":96,"reasoning_effort":"low"}'
#    and separately time `/usr/local/sbin/llama-watchdog-agent synth 8080 96 60`
#    to capture the SAME number this predicate would see.
#
#    FAILURE HANDLING mirrors the fail-safe rule everywhere else in this
#    file: ONE failed transaction is SUSPECT, not WEDGED --
#    WATCHDOG_SYNTH_FAIL_STREAK (default 2) CONSECUTIVE failed ATTEMPTS are
#    required, and because attempts are already SYNTH_INTERVAL_S apart, that
#    is a counter, not a wall-clock streak (a wall-clock streak would either
#    never accumulate, since every intervening /health-OK tick would clear
#    it, or take hours). An agent/SSH hop failure while ATTEMPTING the
#    transaction is treated as no-evidence-either-way (like every other BLIND
#    verdict in this file), never as a failed attempt, and does not consume
#    the rate-limit stamp -- the next tick retries promptly.
#
#    INTERNAL-VS-EXTERNAL DISAGREEMENT (the operator's second framing:
#    "measure externally as well as internally, and treat disagreement as its
#    own fault signal"). Implemented for the STRICT direction only: the
#    agent's `synth` verb compares curl's own wall-clock timer against the
#    server's self-reported `timings.prompt_ms + timings.predicted_ms`. If
#    the server's internal total EXCEEDS the external wall-clock time, that
#    is a logical impossibility -- the internal clock covers a subset of what
#    the external one covers, never a superset -- so it is unambiguous
#    evidence the self-report cannot be trusted, and it is logged loudly on
#    every occurrence. The OTHER direction (external much larger than
#    internal) is reported as a number but NOT acted on: on this fleet it has
#    too many innocent explanations to threshold without a measurement --
#    queueing behind another slot, a cold prompt cache -- and CLAUDE.md's own
#    rule is that a performance claim not in `docs/measurements.md` may not
#    be quoted, let alone acted on. This is the cheap half of the operator's
#    idea; the other half (a genuinely new anomaly-detection CLASS, not just
#    a cheap add) is real and left as a documented direction, not built.
#
#    HONEST VERDICT ON rpc-server: NO synthetic transaction. Exercising it
#    end to end needs a coordinator llama-server actually loaded with --rpc
#    pointed at it, running a real graph across the wire -- and nothing in
#    this fleet's current topology (replication, S=1, R=N) does that. Every
#    real job runs entirely local to one node; rpc-server is kept alive and
#    smoke-tested for the S>1 frontier-model case but is not on any path a
#    real job takes. Standing up a coordinator+model on a watchdog's schedule
#    just to host a health check would mean a second multi-minute model load
#    (F3) and a shard group's worth of RAM, to protect a code path nothing
#    currently uses -- expensive, fragile to version drift between the
#    checker and whatever real topology eventually uses --rpc, and testing
#    the wrong thing under time pressure. Per this project's own recorded
#    judgement: monitoring weakly beats restarting dangerously, and inventing
#    a check that cannot work honestly is worse than admitting the gap.
#    rpc-server keeps the pre-existing weak TCP-accept check PLUS the new
#    nprocs tripwire (#1 above) -- real, cheap, additive coverage for the
#    specific F40 failure, without pretending it is a full transaction.
# ---------------------------------------------------------------------------
# RESTART ORDER AND PRECONDITIONS -- THE TRAP THIS MUST NOT WALK INTO
#
# **F36 was CAUSED by restarting `missing-link` while a job was mid-generation.**
# That is what left llama-server wedged alive. A watchdog that restarts
# missing-link casually would be manufacturing the exact fault it exists to fix.
# So:
#
#   * `missing-link` is NEVER restarted while any job is `running`. Not after a
#     grace period, not with more evidence -- the check is a hard gate, because
#     the failure mode it prevents is worse than the one it would fix. When that
#     gate holds, the verdict is ALERT: logged loudly, acted on never.
#   * Within a sweep, restarts are ordered llama-server -> rpc-server ->
#     missing-link, so the dependency is repaired before the thing that depends
#     on it.
#   * After ANY restart on a host, every other service on that host is given a
#     COOLDOWN (default 600 s) during which it will not be restarted. A
#     llama-server restart makes missing-link look unhealthy for minutes through
#     no fault of its own; that must not cascade.
#   * `llama-server` restarts are cheaper than they were: chunk summaries persist
#     per chunk and jobs resume from them, so a restart costs the in-flight chunk
#     rather than the document. The 65 GB model reload still costs minutes (F3),
#     which is why the grace period is 300 s and not 60.
#
# ---------------------------------------------------------------------------
# NETWORK CONGESTION MUST NEVER LOOK LIKE DEATH (F28)
#
# The LAN is 100 Mb/s and a 65 GB model distribution saturates it for ~97
# minutes, with RTT collapsing 11.5x. A prober that read congestion as a dead
# node would restart healthy servers fleet-wide during a routine model copy -- a
# far worse outage than the one being fixed. Three defences, in order:
#
#   1. **A broken measurement channel is never evidence of death.** If the agent
#      cannot be reached or does not answer, the verdict is BLIND: the streak is
#      CLEARED and nothing is restarted. Congestion, a dropped SSH session, a
#      missing agent and a powered-off switch all land here.
#   2. **Every transport timeout is explicit and generous.** Nothing relies on a
#      client default. Note also that all three progress signals -- cgroup CPU,
#      the TCP listener check, and the SQLite counters -- are computed ON the
#      monitored node, so a slow link can delay a verdict but cannot distort one.
#   3. **Blast-radius guards.** Congestion is correlated across the fleet; a
#      wedge is not. At most `MAX_RESTARTS` units are restarted per sweep
#      (default 1), and if more than `FLEET_ALARM_PCT` of reachable targets are
#      suspect at once the sweep refuses to act at all and says so.
#
# ---------------------------------------------------------------------------
# FAIL SAFE. Every ambiguous case clears the streak. A false restart costs the
# in-flight chunk plus a multi-minute model reload (F3), so this script acts only
# on positive evidence of death, never on absence of evidence of life.
# ===========================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Site configuration. systemd reads this as EnvironmentFile=; read it here too
# so a hand-run `sudo llama-watchdog --report` behaves identically to the timer
# instead of silently missing the SSH identity. Only WATCHDOG_* keys are taken,
# and anything already in the environment wins.
CONF="${WATCHDOG_CONF:-/etc/default/llama-watchdog}"
if [ -r "$CONF" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in WATCHDOG_*) ;; *) continue ;; esac
    [ -n "${!_k:-}" ] || export "$_k=$_v"
  done < "$CONF"
  unset _k _v
fi

first_readable() { for f in "$@"; do [ -r "$f" ] && { echo "$f"; return 0; }; done; echo "$1"; }
first_exec()     { for f in "$@"; do [ -x "$f" ] && { echo "$f"; return 0; }; done; echo "$1"; }

NODES_ENV="${WATCHDOG_NODES_ENV:-$(first_readable \
  "${HERE}/../provisioning/nodes.env" \
  "/etc/llama-watchdog/nodes.env")}"
AGENT_LOCAL="${WATCHDOG_AGENT_LOCAL:-$(first_exec \
  "${HERE}/llama-watchdog-agent.sh" \
  "/usr/local/sbin/llama-watchdog-agent")}"
AGENT_REMOTE="${WATCHDOG_AGENT:-/usr/local/sbin/llama-watchdog-agent}"

MODE="${WATCHDOG_MODE:-auto}"
ONLY="${WATCHDOG_ONLY:-}"
SERVICES="${WATCHDOG_SERVICES:-llama,rpc,ml}"

SSH_USER="${WATCHDOG_SSH_USER:-llamawd}"
# ${HOME:-/root}, not $HOME: under systemd (no login shell, no PAM session)
# HOME is frequently unset for a root-scoped service, and `set -u` turns that
# into a hard crash before the script does anything -- discovered live when
# node 2's freshly-installed IN-BAND timer (MODE=local, which never even uses
# SSH_KEY) failed every tick with "line 240: HOME: unbound variable" and never
# reached a single predicate. A crash-before-probing is not "declined to act",
# it is untested and silently unmonitored -- worse than either LIVE outcome.
SSH_KEY="${WATCHDOG_SSH_KEY:-${HOME:-/root}/.ssh/id_llama_watchdog}"

# CPU observation window. Idle measures 0 ns even at 3 s and busy measures 400%
# even at 3 s, so 10 s is margin, not necessity.
WINDOW="${WATCHDOG_WINDOW:-10}"

# See the ordering note: not diagnostic. It only has to exceed every transport
# hazard, and F28's worst measured RTT is 9.544 ms.
HTTP_TIMEOUT="${WATCHDOG_HTTP_TIMEOUT:-5}"

# Explicit, not defaulted. An order of magnitude above the congested RTT, and
# ServerAlive* bounds a session that stalls after connecting.
SSH_CONNECT_TIMEOUT="${WATCHDOG_SSH_CONNECT_TIMEOUT:-10}"
SSH_ALIVE_INTERVAL="${WATCHDOG_SSH_ALIVE_INTERVAL:-5}"
SSH_ALIVE_COUNT="${WATCHDOG_SSH_ALIVE_COUNT:-3}"

GRACE="${WATCHDOG_GRACE:-300}"

# Longer grace when the server has --rpc backends AND no shard member could be
# reached to corroborate. UNTESTED against a real wedge in a sharded topology --
# a labelled safety margin, not a measurement. See the note in predicate_llama.
RPC_GRACE="${WATCHDOG_RPC_GRACE:-900}"

# rpc-server: unbroken seconds of "active but the port refuses" before acting.
RPC_TCP_GRACE="${WATCHDOG_RPC_TCP_GRACE:-300}"

# missing-link: unbroken seconds of "active, no job running, HTTP silent".
ML_GRACE="${WATCHDOG_ML_GRACE:-300}"
# Report-only stall threshold while jobs ARE running. A single map chunk took
# 10m55s in the F39 incident, and worker.py's DEFAULT_TIMEOUT_S is 3600, so
# anything shorter than an hour would cry wolf.
ML_STALL_S="${WATCHDOG_ML_STALL_S:-3600}"

BUSY_PCT="${WATCHDOG_BUSY_PCT:-5}"

# ---------------------------------------------------------------------------
# 2026-08-18 ADDITIONS -- see the header section "SYNTHETIC TRANSACTION" below
# for the full design. Two independent, additive checks; neither replaces
# anything above, both slot into the SAME emit/streak/restart plumbing every
# other check already uses.
# ---------------------------------------------------------------------------

# Process count baselines -- the F40 tripwire. MEASURED on both nodes,
# 2026-08-18, servers healthy and BUSY (400% of a core): llama-server@.service
# is 2 (the ExecStart=/bin/sh -c wrapper + the server); rpc-server@.service is
# 1 (no shell wrapper). A forked ggml_abort() backtrace child is a REAL
# process and adds to this count; a thread never does.
NPROCS_BASELINE_LLAMA="${WATCHDOG_NPROCS_LLAMA:-2}"
NPROCS_BASELINE_RPC="${WATCHDOG_NPROCS_RPC:-1}"

# Grace for a CONFIRMED elevated process count. Shorter than GRACE (300s)
# because this is structural evidence (a real extra PID exists), not an
# absence-of-response inference -- but still not zero-grace, on the chance
# something else this fleet has not yet seen also transiently forks. Two
# ticks at the timers' 1-minute period is the practical minimum for "not a
# single sample".
ABORT_GRACE="${WATCHDOG_ABORT_GRACE:-90}"

# --- Synthetic transaction (llama-server only; see predicate_llama) --------
# Whether it runs at all. Default ON: it is the one check that closes the
# "self-report says fine" blind spot, and it is rate-limited hard below.
SYNTH_ENABLE="${WATCHDOG_SYNTH_ENABLE:-1}"

# Minimum seconds between synthetic transactions AGAINST THE SAME UNIT. This
# is the whole answer to "a synthetic transaction competes with real work":
# at the default, it can run at most 4 times an hour, each occupying one of
# four --parallel slots for single-digit seconds (see the agent's `synth`
# verb comment for the cost estimate and its basis). It never runs unless the
# CPU-first check already found the unit idle, so it also never competes with
# an ACTUAL document chunk -- inherits the same ordering F39 established for
# /health, one rung further out.
SYNTH_INTERVAL_S="${WATCHDOG_SYNTH_INTERVAL_S:-900}"

SYNTH_MAX_TOKENS="${WATCHDOG_SYNTH_MAX_TOKENS:-96}"
SYNTH_TIMEOUT_S="${WATCHDOG_SYNTH_TIMEOUT_S:-60}"

# Consecutive FAILED synthetic transactions (not wall-clock seconds -- each
# attempt is already SYNTH_INTERVAL_S apart, so a wall-clock streak would
# either never accumulate, because every intervening tick's /health-OK
# verdict would clear it, or would take hours) before acting. 2 means the
# earliest a restart fires from this evidence alone is the SECOND consecutive
# failed transaction, at least SYNTH_INTERVAL_S after the first.
SYNTH_FAIL_STREAK="${WATCHDOG_SYNTH_FAIL_STREAK:-2}"

MAX_RESTARTS="${WATCHDOG_MAX_RESTARTS:-1}"
FLEET_ALARM_PCT="${WATCHDOG_FLEET_ALARM_PCT:-50}"
COOLDOWN="${WATCHDOG_COOLDOWN:-600}"

DRY_RUN="${WATCHDOG_DRY_RUN:-0}"
HEARTBEAT_URL="${WATCHDOG_HEARTBEAT_URL:-}"

STATE_DIR="${STATE_DIRECTORY:-${WATCHDOG_STATE_DIR:-/var/lib/llama-watchdog}}"
RUN_DIR="${RUNTIME_DIRECTORY:-${WATCHDOG_RUN_DIR:-/run/llama-watchdog}}"
RECORD="${STATE_DIR}/restarts.jsonl"
HEARTBEAT="${STATE_DIR}/heartbeat.json"

REPORT_ONLY=0
LEGACY_PORT=""
case "${1:-}" in
  --report) REPORT_ONLY=1 ;;
  --help|-h) sed -n '1,22p' "$0"; exit 0 ;;
  '') ;;
  *[!0-9]*) echo "unknown argument: $1" >&2; exit 64 ;;
  *) LEGACY_PORT="$1" ;;
esac

# When systemd is already capturing stdout it sets JOURNAL_STREAM, and calling
# `logger` as well puts every line in the journal twice under two different
# PIDs -- which is both noisy and actively misleading when you are correlating
# a restart against a dead job. Log through exactly one channel.
if [ -n "${JOURNAL_STREAM:-}" ]; then
  log() { echo "$*"; }
else
  log() { logger -t llama-watchdog -p daemon.info -- "$*" 2>/dev/null; echo "$*"; }
fi

for d in "$STATE_DIR" "$RUN_DIR"; do
  [ -d "$d" ] || mkdir -p "$d" 2>/dev/null || { echo "cannot create $d" >&2; exit 70; }
done

wants() { case ",${SERVICES}," in *",$1,"*) return 0 ;; esac; return 1; }

# ---------------------------------------------------------------------------
# Target discovery. nodes.env is the canonical fleet list; nothing here assumes
# N=2, N=7, or that the nodes are alike.
# ---------------------------------------------------------------------------
declare -a T_KIND=() T_HOST=() T_PORT=() T_NAME=()
declare -a NODE_IP=() NODE_NAME=()

LOCAL_IPS="$({ ip -o -4 addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1
               echo 127.0.0.1; echo localhost; } | sort -u)"
is_local_host() { printf '%s\n' "$LOCAL_IPS" | grep -qx -- "$1"; }

want_host() {
  [ -z "$ONLY" ] && return 0
  case ",${ONLY}," in *",$1,"*) return 0 ;; esac
  return 1
}
name_for_ip() {
  local i
  for i in "${!NODE_IP[@]}"; do
    [ "${NODE_IP[$i]}" = "$1" ] && { echo "${NODE_NAME[$i]}"; return; }
  done
  echo "$1"
}
add_target() { T_KIND+=("$1"); T_HOST+=("$2"); T_PORT+=("$3"); T_NAME+=("$4"); }

split_endpoint() {   # sets EP_HOST / EP_PORT
  local hp="${1#*://}"; hp="${hp%%/*}"
  EP_HOST="${hp%%:*}"; EP_PORT="${hp##*:}"
  [ "$EP_HOST" = "$EP_PORT" ] && EP_PORT="$2"
}

load_targets() {
  if [ -n "$LEGACY_PORT" ]; then
    add_target llama 127.0.0.1 "$LEGACY_PORT" "$(hostname)"
    return 0
  fi
  if [ ! -r "$NODES_ENV" ]; then
    log "FATAL: cannot read $NODES_ENV -- there is no fleet list, so there is" \
        "nothing to watch. Set WATCHDOG_NODES_ENV."
    exit 66
  fi
  # shellcheck disable=SC1090
  source "$NODES_ENV"

  local entry ep
  for entry in "${NODES[@]:-}"; do
    [ -z "$entry" ] && continue
    # shellcheck disable=SC2086
    set -- $entry
    NODE_NAME+=("$1"); NODE_IP+=("$2")
  done

  if wants llama; then
    for ep in "${INFERENCE_ENDPOINTS[@]:-}"; do
      [ -z "$ep" ] && continue
      split_endpoint "$ep" 8080
      want_host "$EP_HOST" && add_target llama "$EP_HOST" "$EP_PORT" "$(name_for_ip "$EP_HOST")"
    done
  fi

  # rpc-server runs on EVERY node, one per node, on RPC_PORT.
  if wants rpc && [ -n "${RPC_PORT:-}" ]; then
    local i
    for i in "${!NODE_IP[@]}"; do
      want_host "${NODE_IP[$i]}" && add_target rpc "${NODE_IP[$i]}" "$RPC_PORT" "${NODE_NAME[$i]}"
    done
  fi

  # missing-link is coordinator-only, so it is listed explicitly rather than
  # derived from NODES. An absent list simply means "not deployed here".
  if wants ml; then
    for ep in "${MISSING_LINK_ENDPOINTS[@]:-}"; do
      [ -z "$ep" ] && continue
      split_endpoint "$ep" 8000
      want_host "$EP_HOST" && add_target ml "$EP_HOST" "$EP_PORT" "$(name_for_ip "$EP_HOST")"
    done
  fi

  if [ "${#T_HOST[@]}" -eq 0 ]; then
    log "FATAL: $NODES_ENV yielded no targets for services '${SERVICES}'."
    exit 66
  fi
}
load_targets

# ---------------------------------------------------------------------------
# Transport. One function; the mode only decides whether SSH is in the path.
# Both branches exec the SAME agent script.
# ---------------------------------------------------------------------------
transport_for() {
  case "$MODE" in
    local) echo local ;;
    ssh)   echo ssh ;;
    *)     if is_local_host "$1" && [ -x "$AGENT_LOCAL" ]; then echo local; else echo ssh; fi ;;
  esac
}

# agent_call <host> <transport> <budget_seconds> <verb...>
# Non-zero exit or empty output means the channel failed, which the caller MUST
# treat as "no evidence", never as "idle".
agent_call() {
  local host="$1" tr="$2" budget="$3"; shift 3
  local err=/dev/null
  [ "${WATCHDOG_DEBUG:-0}" = "1" ] && err=/dev/stderr
  if [ "$tr" = local ]; then
    timeout "$budget" "$AGENT_LOCAL" "$@" 2>"$err"
    return
  fi
  # Built as an array: `${VAR:+-i "$VAR"}` would leave literal quotes in the
  # argument after word splitting, and ssh would look for a key file whose name
  # begins with a double quote.
  local -a a=(
    -o BatchMode=yes
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout="$SSH_CONNECT_TIMEOUT"
    -o ServerAliveInterval="$SSH_ALIVE_INTERVAL"
    -o ServerAliveCountMax="$SSH_ALIVE_COUNT"
  )
  if [ -n "$SSH_KEY" ] && [ -r "$SSH_KEY" ]; then
    a+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
  fi
  timeout "$budget" ssh "${a[@]}" "${SSH_USER}@${host}" "$AGENT_REMOTE" "$@" 2>"$err"
}

field() { printf '%s\n' "$1" | tr ' ' '\n' | awk -F= -v k="$2" '$1==k{print $2; exit}'; }
ok_rec() { [ -n "$1" ] && [ "$(field "$1" ok)" = "1" ]; }
budget() { echo $(( WINDOW + SSH_CONNECT_TIMEOUT + 20 )); }

# ---------------------------------------------------------------------------
# Shared streak accounting. A "streak" is unbroken wall-clock seconds during
# which every tick saw the same fault signature. Any ambiguity clears it.
# ---------------------------------------------------------------------------
streak_start() {          # $1=file -> echoes elapsed seconds
  local now since
  now="$(date +%s)"; since="$(cat "$1" 2>/dev/null)"
  case "${since:-}" in ''|*[!0-9]*) since="$now"; echo "$since" > "$1" ;; esac
  echo $(( now - since ))
}
emit() { printf '%s|%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$OUT"; }

# ---------------------------------------------------------------------------
# Attempt counters, for the synthetic transaction's fail streak. Deliberately
# NOT wall-clock like streak_start(): synth attempts are already spaced
# SYNTH_INTERVAL_S apart by the rate limiter, so "N consecutive failed
# ATTEMPTS" is the meaningful unit, not "N unbroken seconds" -- a wall-clock
# streak would either never accumulate (every intervening tick's healthy
# /health verdict would clear it) or take hours to satisfy.
# ---------------------------------------------------------------------------
counter_bump() {          # $1=file -> echoes the NEW count
  local n; n="$(cat "$1" 2>/dev/null)"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  n=$((n + 1)); echo "$n" > "$1"; echo "$n"
}
counter_clear() { rm -f "$1"; }

# due_for() -- has SYNTH_INTERVAL_S elapsed since the stamp file's recorded
# time? Missing/unreadable stamp counts as "due" (first-ever check for a unit
# should not wait a full interval).
due_for() {               # $1=stamp file, $2=interval seconds
  local now last; now="$(date +%s)"; last="$(cat "$1" 2>/dev/null)"
  case "$last" in ''|*[!0-9]*) return 0 ;; esac
  [ $(( now - last )) -ge "$2" ]
}
stamp_now() { date +%s > "$1"; }

# ===========================================================================
# PREDICATE: llama-server.  CPU-progress is decisive. Measured, see the header.
# ===========================================================================
predicate_llama() {
  local host="$1" port="$2" name="$3" tr="$4"
  local unit="llama-server@${port}"
  local sf="${RUN_DIR}/streak-llama-${host}-${port}"

  local rec; rec="$(agent_call "$host" "$tr" "$(budget)" probe "$unit" "$WINDOW")"
  if ! ok_rec "$rec"; then
    rm -f "$sf"
    emit BLIND llama "$host" "$port" "$name" "transport=${tr} could not reach the agent -- no evidence either way, streak cleared"
    return
  fi

  local active; active="$(field "$rec" active)"
  if [ "$active" != "active" ]; then
    rm -f "$sf"
    emit DOWN llama "$host" "$port" "$name" "active=${active} -- Restart=always owns this, watchdog stands off"
    return
  fi

  # F40 TRIPWIRE. Checked before CPU/HTTP because it is structural evidence
  # (a real extra PID exists), not an inference from silence, and it is
  # cheap: nprocs0/nprocs1 came from the SAME probe call above, no extra
  # round trip. A forked ggml_abort() backtrace child is a real process
  # (threads never add to cgroup.procs) and, per the confirmed-live finding
  # that mainline's ggml.c has the identical fork()/waitpid(NULL, 0)
  # backtrace code as ik_llama.cpp, this is NOT a retired-engine concern --
  # it can fire on whatever engine is currently deployed.
  local af="${RUN_DIR}/streak-abort-llama-${host}-${port}"
  local np0 np1 npmax
  np0="$(field "$rec" nprocs0)"; np1="$(field "$rec" nprocs1)"
  case "${np0}${np1}" in
    ''|*[!0-9]*) rm -f "$af" ;;  # unmeasurable -- no evidence, not a fault
    *)
      npmax=$np0; [ "$np1" -gt "$npmax" ] && npmax=$np1
      if [ "$npmax" -gt "$NPROCS_BASELINE_LLAMA" ]; then
        local astreak; astreak="$(streak_start "$af")"
        if [ "$astreak" -lt "$ABORT_GRACE" ]; then
          emit SUSPECT llama "$host" "$port" "$name" \
            "nprocs=${npmax} > baseline ${NPROCS_BASELINE_LLAMA} -- possible ggml_abort() fork signature (F40). streak=${astreak}s/${ABORT_GRACE}s. Holding for a confirming sample"
          return
        fi
        rm -f "$af"
        emit WEDGED llama "$host" "$port" "$name" \
          "nprocs=${npmax} > baseline ${NPROCS_BASELINE_LLAMA} for ${astreak}s -- ggml_abort() fork signature (F40): a forked backtrace child is deadlocked and INHERITED the listening socket, so Restart=always and a port check both see a live, healthy-looking unit"
        return
      fi
      rm -f "$af"
      ;;
  esac

  local cpu0 cpu1 t0 t1
  cpu0="$(field "$rec" cpu0)"; cpu1="$(field "$rec" cpu1)"
  t0="$(field "$rec" t0)";     t1="$(field "$rec" t1)"
  case "${cpu0}${cpu1}${t0}${t1}" in
    ''|*[!0-9]*)
      rm -f "$sf"
      emit BLIND llama "$host" "$port" "$name" "cgroup CPU unreadable (CPUAccounting off?) -- cannot tell busy from wedged, no action"
      return ;;
  esac

  local wall=$(( t1 - t0 )); [ "$wall" -le 0 ] && wall=1
  local delta=$(( cpu1 - cpu0 ))
  local pct=$(( delta * 100 / wall ))

  # Burning CPU => BUSY. NO HTTP PROBE IS ISSUED AT ALL. At the moment the
  # server is most loaded, the monitor's footprint on it is zero requests.
  if [ "$pct" -ge "$BUSY_PCT" ]; then
    rm -f "$sf"
    emit BUSY llama "$host" "$port" "$name" \
      "cpu=$((delta/1000000))ms/$((wall/1000000))ms (${pct}% of one core) over ${WINDOW}s -- working, no HTTP probe issued"
    return
  fi

  # CPU flat. NOW ask HTTP -- and on an idle healthy server the answer arrives
  # in under a millisecond (measured 0.736-0.912 ms).
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
          "http://${host}:${port}/health" 2>/dev/null)"
  if [ "${code:-000}" != "000" ]; then
    local base="idle, /health ${code}"
    if [ -f "$sf" ]; then
      emit RECOVERED llama "$host" "$port" "$name" "${base} after a suspect streak -- cleared"
      rm -f "$sf"
      return
    fi
    rm -f "$sf"

    # SYNTHETIC TRANSACTION. The one check /health cannot substitute for: on
    # mainline /health never touches the inference queue at all (it is a
    # cheap lambda -- that is WHY it is cheap, and also why answering proves
    # nothing about whether a completion actually works). Rate-limited and
    # ONLY reached here, i.e. only when the unit already looks perfectly
    # healthy by every cheaper signal -- inherits the CPU-first ordering
    # (F39): never issued while busy, because we would not have reached this
    # branch at all if the CPU sample had shown load.
    if [ "$SYNTH_ENABLE" = "1" ]; then
      local sstamp="${RUN_DIR}/synthstamp-llama-${host}-${port}"
      local sfailc="${RUN_DIR}/synthfail-llama-${host}-${port}"
      if due_for "$sstamp" "$SYNTH_INTERVAL_S"; then
        local srec; srec="$(agent_call "$host" "$tr" "$((SYNTH_TIMEOUT_S + SSH_CONNECT_TIMEOUT + 10))" \
                             synth "$port" "$SYNTH_MAX_TOKENS" "$SYNTH_TIMEOUT_S")"
        if ! ok_rec "$srec"; then
          # Could not even RUN the check (agent/SSH hop failed). Do NOT treat
          # this as wedge evidence -- a flaky SSH hop is not a bad completion
          # -- and do NOT stamp, so the next tick retries promptly rather than
          # waiting a full SYNTH_INTERVAL_S for an answer we never got.
          emit OK llama "$host" "$port" "$name" "${base}; synth attempted but the agent did not answer (transport=${tr}) -- confirmation skipped, /health OK stands"
          return
        fi
        stamp_now "$sstamp"
        local sverdict; sverdict="$(field "$srec" synth_verdict)"
        if [ "$sverdict" = "PASS" ]; then
          counter_clear "$sfailc"
          local sdetail extS pms dms disagree
          sdetail="$(field "$srec" synth_detail)"; extS="$(field "$srec" external_s)"
          pms="$(field "$srec" prompt_ms)"; dms="$(field "$srec" predicted_ms)"
          disagree="$(field "$srec" disagree)"
          local dnote=""
          # STRICT direction only (internal > external is a logical
          # impossibility -- see the agent's synth verb comment for why the
          # other direction is reported but not acted on). Logged loudly;
          # does not by itself feed the fail-streak below, because it has
          # never been validated against a real anomaly -- an untested new
          # signal gets a log line, not a trigger finger.
          case "$disagree" in yes:*) dnote=" ANOMALY: ${disagree} (server's own timing report cannot be trusted here -- logged, not acted on)" ;; esac
          emit OK llama "$host" "$port" "$name" "${base}, synth CONFIRMED (${sdetail}, external=${extS}s prompt_ms=${pms} predicted_ms=${dms})${dnote}"
        else
          local sdetail; sdetail="$(field "$srec" synth_detail)"
          local n; n="$(counter_bump "$sfailc")"
          if [ "$n" -lt "$SYNTH_FAIL_STREAK" ]; then
            emit SUSPECT llama "$host" "$port" "$name" \
              "${base} BUT a real completion FAILED (verdict=${sverdict}: ${sdetail}). This is the blind spot /health cannot see. Failed attempt ${n}/${SYNTH_FAIL_STREAK} -- holding for a confirming attempt in ~${SYNTH_INTERVAL_S}s"
          else
            counter_clear "$sfailc"
            emit WEDGED llama "$host" "$port" "$name" \
              "/health answers ${code} but ${n} consecutive real completions FAILED (verdict=${sverdict}: ${sdetail}) -- self-reported health cannot be trusted here; a synthetic transaction is decisive because a listen backlog and a cheap /health lambda cannot fake generated text"
          fi
        fi
        return
      fi
      # Not due -- rate limiter is doing its job. /health OK stands as this
      # tick's evidence, exactly as before this feature existed.
    fi

    emit OK llama "$host" "$port" "$name" "$base"
    return
  fi

  # Silent AND idle. /props is the one endpoint that does NOT post to the task
  # queue (it reads model metadata directly), so it separates "HTTP layer alive,
  # inference dead" -- F36's signature -- from "whole process gone". It decides
  # nothing on its own.
  local props layer
  props="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
           "http://${host}:${port}/props" 2>/dev/null)"
  if [ "${props:-000}" = "000" ]; then
    layer="http layer also silent (/props 000) -- whole process unresponsive"
  else
    layer="http layer ALIVE (/props ${props}) but task queue not draining -- the F36 signature"
  fi

  # Sharded case. Under --rpc the coordinator legitimately sits idle on a socket
  # while a remote shard computes, so LOCAL CPU IS NOT PROOF OF LIFE. Ask the
  # shard members directly: if any rpc-server is burning CPU the group is making
  # progress. Only when NO peer can be reached does the grace fall back to the
  # RPC_GRACE safety margin, which is a guess and is labelled as one.
  local eff_grace="$GRACE" rpc_note="" shard_busy=0 corroborated=0
  if [ "$(field "$rec" rpc)" = "1" ]; then
    rpc_note=" rpc=yes"
    local peers peer ph pp prec pc0 pc1 pt0 pt1 pd pw ppct
    peers="$(field "$rec" rpcpeers)"
    IFS=',' read -ra _peers <<< "${peers:-}"
    for peer in "${_peers[@]}"; do
      [ -z "$peer" ] && continue
      ph="${peer%%:*}"; pp="${peer##*:}"
      case "$pp" in ''|*[!0-9]*) continue ;; esac
      prec="$(agent_call "$ph" "$(transport_for "$ph")" "$(budget)" probe "rpc-server@${pp}" "$WINDOW")"
      if ! ok_rec "$prec"; then rpc_note="${rpc_note} peer:${ph}=unreachable"; continue; fi
      pc0="$(field "$prec" cpu0)"; pc1="$(field "$prec" cpu1)"
      pt0="$(field "$prec" t0)";   pt1="$(field "$prec" t1)"
      case "${pc0}${pc1}${pt0}${pt1}" in
        ''|*[!0-9]*) rpc_note="${rpc_note} peer:${ph}=unmeasurable"; continue ;;
      esac
      pd=$(( pc1 - pc0 )); pw=$(( pt1 - pt0 )); [ "$pw" -le 0 ] && pw=1
      ppct=$(( pd * 100 / pw ))
      corroborated=1
      rpc_note="${rpc_note} peer:${ph}=${ppct}%"
      [ "$ppct" -ge "$BUSY_PCT" ] && shard_busy=1
    done
    if [ "$shard_busy" = "1" ]; then
      rm -f "$sf"
      emit BUSY llama "$host" "$port" "$name" \
        "local cpu flat but an RPC shard member is computing (${rpc_note# }) -- the group is making progress"
      return
    fi
    if [ "$corroborated" = "0" ]; then
      eff_grace="$RPC_GRACE"
      rpc_note="${rpc_note} grace=RPC_GRACE(UNTESTED safety margin, no peer reachable)"
    fi
  fi

  local streak; streak="$(streak_start "$sf")"
  local evidence="cpu=$((delta/1000000))ms/$((wall/1000000))ms(${pct}%) health=000 props=${props:-000} streak=${streak}s/${eff_grace}s${rpc_note}"

  if [ "$streak" -lt "$eff_grace" ]; then
    emit SUSPECT llama "$host" "$port" "$name" "${layer}. ${evidence}. Holding -- needs ${eff_grace}s of unbroken evidence"
    return
  fi
  emit WEDGED llama "$host" "$port" "$name" "${layer}. ${evidence}"
}

# ===========================================================================
# PREDICATE: rpc-server.  DELIBERATELY WEAK. Never judged on CPU.
#
# An rpc-server with no shard work is SUPPOSED to be idle -- measured 0 ns over
# a 3 s window on a perfectly healthy node 1 worker. The only fault signal taken
# is "systemd says active, but the RPC port refuses a connection ON THE NODE".
# That is safe to act on because a worker that is not listening cannot be
# carrying in-flight shard work, and because restarting one is cheap: F23,
# workers never read model files, so there is no 65 GB reload.
#
# KNOWN LIMITATION, stated rather than hidden: a hung-but-listening rpc-server
# is NOT fully covered. The kernel completes the handshake from the listen
# backlog even when the process is frozen -- proved by the SIGSTOP test, where
# /health connected in 0.000163 s and then never answered. A TRUE synthetic
# transaction against rpc-server would need a coordinator llama-server loaded
# with --rpc pointed at it, actually running a graph across the wire, and
# checking real output -- and per the 2026-08-18 review, NOTHING in this
# fleet's current topology does that today: it runs replication (S=1, R=N),
# rpc-server is kept alive and tested for the S>1 frontier-model case but is
# not on the path any real job takes. Building a coordinator+model just to
# host a health check would mean running a second model load (F3: minutes,
# single-core-serialised) and holding a shard group's worth of RAM, on a
# schedule dictated by a watchdog rather than by any actual workload --
# expensive, fragile to version drift, and protecting a code path nothing
# uses. So: no synthetic transaction for rpc-server. Monitoring weakly beats
# inventing a check that cannot work, which is this project's own recorded
# judgement (F39-era design notes) and still holds.
#
# What DID get cheaper 2026-08-18: the nprocs tripwire below. It is not a
# transaction -- it never speaks the RPC wire protocol -- but it is real,
# structural, near-free evidence for exactly the F40 failure (rpc-server
# links the SAME libggml, so the SAME ggml_abort()->fork()->wait4() hang can
# happen here too), and it needed nothing new: cgroup.procs was already free
# to read. It narrows, not closes, the limitation above.
# ===========================================================================
predicate_rpc() {
  local host="$1" port="$2" name="$3" tr="$4"
  local unit="rpc-server@${port}"
  local sf="${RUN_DIR}/streak-rpc-${host}-${port}"
  local af="${RUN_DIR}/streak-abort-rpc-${host}-${port}"

  # window 1: we want active/CPU for the report, not for the decision.
  local rec; rec="$(agent_call "$host" "$tr" "$(budget)" probe "$unit" 1)"
  if ! ok_rec "$rec"; then
    rm -f "$sf" "$af"
    emit BLIND rpc "$host" "$port" "$name" "transport=${tr} could not reach the agent -- no evidence either way, streak cleared"
    return
  fi
  local active; active="$(field "$rec" active)"
  if [ "$active" != "active" ]; then
    rm -f "$sf" "$af"
    emit DOWN rpc "$host" "$port" "$name" "active=${active} -- Restart=always owns this, watchdog stands off"
    return
  fi

  # F40 TRIPWIRE (see predicate_llama for the full rationale; rpc-server's
  # baseline is 1 process, measured live -- no shell wrapper in this unit).
  local np0 np1 npmax
  np0="$(field "$rec" nprocs0)"; np1="$(field "$rec" nprocs1)"
  case "${np0}${np1}" in
    ''|*[!0-9]*) rm -f "$af" ;;
    *)
      npmax=$np0; [ "$np1" -gt "$npmax" ] && npmax=$np1
      if [ "$npmax" -gt "$NPROCS_BASELINE_RPC" ]; then
        local astreak; astreak="$(streak_start "$af")"
        if [ "$astreak" -lt "$ABORT_GRACE" ]; then
          emit SUSPECT rpc "$host" "$port" "$name" \
            "nprocs=${npmax} > baseline ${NPROCS_BASELINE_RPC} -- possible ggml_abort() fork signature (F40). streak=${astreak}s/${ABORT_GRACE}s. Holding"
          return
        fi
        rm -f "$af"
        emit WEDGED rpc "$host" "$port" "$name" \
          "nprocs=${npmax} > baseline ${NPROCS_BASELINE_RPC} for ${astreak}s -- ggml_abort() fork signature (F40). Restarting is cheap regardless (F23: workers never read model files)"
        return
      fi
      rm -f "$af"
      ;;
  esac

  local tcprec; tcprec="$(agent_call "$host" "$tr" 30 tcpcheck "$port")"
  if ! ok_rec "$tcprec"; then
    rm -f "$sf"
    emit BLIND rpc "$host" "$port" "$name" "tcpcheck did not return -- no evidence, streak cleared"
    return
  fi

  local tcp; tcp="$(field "$tcprec" tcp)"
  local cpu0 cpu1 cpunote
  cpu0="$(field "$rec" cpu0)"; cpu1="$(field "$rec" cpu1)"
  case "${cpu0}${cpu1}" in
    ''|*[!0-9]*) cpunote="cpu=? " ;;
    *) cpunote="cpu_delta=$(( (cpu1-cpu0)/1000000 ))ms " ;;
  esac

  if [ "$tcp" = "accept" ]; then
    if [ -f "$sf" ]; then
      emit RECOVERED rpc "$host" "$port" "$name" "RPC port accepting again -- streak cleared"
    else
      emit OK rpc "$host" "$port" "$name" "active, RPC port accepting (${cpunote}-- idle is NORMAL for a worker and is never treated as a fault)"
    fi
    rm -f "$sf"
    return
  fi

  local streak; streak="$(streak_start "$sf")"
  if [ "$streak" -lt "$RPC_TCP_GRACE" ]; then
    emit SUSPECT rpc "$host" "$port" "$name" \
      "systemd says active but the RPC port REFUSES connections on the node itself. ${cpunote}streak=${streak}s/${RPC_TCP_GRACE}s. Holding"
    return
  fi
  emit WEDGED rpc "$host" "$port" "$name" \
    "active but the RPC port has refused connections on the node for ${streak}s. ${cpunote}A worker that is not listening cannot be carrying shard work, and restarting one is cheap (F23: workers never read model files)"
}

# ===========================================================================
# PREDICATE: missing-link.  HTTP liveness, plus a REPORT-ONLY progress check.
#
# Idle is the correct and common state for an async queue, so CPU is useless
# here (measured ~0.17% of a core at rest). The hard rule is the restart gate:
# F36 was CAUSED by restarting missing-link mid-generation, so this never
# restarts it while a job is `running` -- the verdict becomes ALERT instead,
# which is logged and never acted on.
# ===========================================================================
predicate_ml() {
  local host="$1" port="$2" name="$3" tr="$4"
  local unit="missing-link"
  local sf="${RUN_DIR}/streak-ml-${host}-${port}"
  local pf="${RUN_DIR}/progress-ml-${host}-${port}"

  local rec; rec="$(agent_call "$host" "$tr" 30 probe "$unit" 1)"
  if ! ok_rec "$rec"; then
    rm -f "$sf"
    emit BLIND ml "$host" "$port" "$name" "transport=${tr} could not reach the agent -- no evidence either way, streak cleared"
    return
  fi
  local active; active="$(field "$rec" active)"
  if [ "$active" != "active" ]; then
    rm -f "$sf"
    emit DOWN ml "$host" "$port" "$name" "active=${active} -- Restart=always owns this, watchdog stands off"
    return
  fi

  # Job-store state. Read-only, on the node, and it never touches llama-server.
  local mls running chunks dbmt
  mls="$(agent_call "$host" "$tr" 30 mlstate)"
  if ok_rec "$mls"; then
    running="$(field "$mls" ml_running)"; chunks="$(field "$mls" ml_chunks)"
    dbmt="$(field "$mls" ml_db_mtime)"
  else
    running=""; chunks=""; dbmt=""
  fi
  case "${running}" in ''|*[!0-9]*) running="" ;; esac

  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
          "http://${host}:${port}/" 2>/dev/null)"

  # --- progress bookkeeping ---------------------------------------------------
  # chunk_summaries has no timestamp column, so PROGRESS IS A COUNTER compared
  # against the previous sweep, with the job-store file mtime as a second,
  # cheaper signal. The stamp file records "when we last saw either move".
  local now prev_ts="" prev_chunks="" prev_mt="" stall="" progressed=0
  now="$(date +%s)"
  if [ -f "$pf" ]; then
    read -r prev_ts prev_chunks prev_mt < "$pf" 2>/dev/null || true
  fi
  [ -n "$chunks" ] && [ "$chunks" != "$prev_chunks" ] && progressed=1
  [ -n "$dbmt" ]   && [ "$dbmt"   != "$prev_mt"     ] && progressed=1

  case "${prev_ts:-}" in ''|*[!0-9]*) prev_ts="" ;; esac
  if [ -z "$prev_ts" ] || [ "$progressed" = 1 ]; then
    printf '%s %s %s\n' "$now" "${chunks:-0}" "${dbmt:-0}" > "$pf"
  elif [ -n "${running:-}" ] && [ "$running" -gt 0 ]; then
    local age=$(( now - prev_ts ))
    if [ "$age" -ge "$ML_STALL_S" ]; then
      stall=" STALL: ${running} job(s) running but chunk_summaries has not grown and the job store has not been written for ${age}s (>= ${ML_STALL_S}s)"
    fi
  fi

  local jobs="running=${running:-?} chunks=${chunks:-?}"

  if [ "${code:-000}" != "000" ]; then
    rm -f "$sf"
    if [ -n "$stall" ]; then
      # Report only. A stalled queue is not a wedged process, and restarting it
      # while a job is running is the F36 trigger.
      emit ALERT ml "$host" "$port" "$name" "HTTP ${code} (alive) but${stall}. REPORTING ONLY -- a restart here is what caused F36. Investigate the worker, do not restart blindly"
    else
      emit OK ml "$host" "$port" "$name" "HTTP ${code}, ${jobs}"
    fi
    return
  fi

  # HTTP silent.
  if [ -z "${running:-}" ]; then
    rm -f "$sf"
    emit BLIND ml "$host" "$port" "$name" "HTTP silent and the job store could not be read -- cannot rule out a running job, so NO action. ${mls:-no mlstate}"
    return
  fi

  if [ "$running" -gt 0 ]; then
    # HARD GATE. Not a grace period, not "with more evidence" -- never.
    rm -f "$sf"
    emit ALERT ml "$host" "$port" "$name" "HTTP silent for ${HTTP_TIMEOUT}s BUT ${running} job(s) are 'running'. REFUSING TO RESTART: F36 was caused by restarting missing-link mid-generation, which left llama-server wedged alive. ${jobs}${stall}. Operator action required"
    return
  fi

  local streak; streak="$(streak_start "$sf")"
  if [ "$streak" -lt "$ML_GRACE" ]; then
    emit SUSPECT ml "$host" "$port" "$name" "HTTP silent, no job running. ${jobs} streak=${streak}s/${ML_GRACE}s. Holding"
    return
  fi
  emit WEDGED ml "$host" "$port" "$name" "HTTP silent for ${streak}s with NO job running -- safe to restart, nothing is in flight. ${jobs}"
}

# ---------------------------------------------------------------------------
# Sweep every target in parallel. Serial would exceed the 60 s timer period at
# modest N; the checks are independent and the traffic is negligible even on a
# 100 Mb link.
# ---------------------------------------------------------------------------
SWEEP="$(mktemp "${TMPDIR:-/tmp}/llama-watchdog.XXXXXX")"
trap 'rm -f "$SWEEP" "$SWEEP".*' EXIT

declare -a PIDS=()
for i in "${!T_HOST[@]}"; do
  (
    trap - EXIT                       # a child must never fire the parent cleanup
    OUT="${SWEEP}.$i"
    predicate_"${T_KIND[$i]}" "${T_HOST[$i]}" "${T_PORT[$i]}" "${T_NAME[$i]}" \
      "$(transport_for "${T_HOST[$i]}")"
  ) &
  PIDS+=("$!")
done
for p in "${PIDS[@]}"; do wait "$p"; done
cat "${SWEEP}".* > "$SWEEP" 2>/dev/null

# ---------------------------------------------------------------------------
# Decide, with blast-radius guards.
# ---------------------------------------------------------------------------
total=0; reachable=0; wedged=0; suspect=0; busy=0; blind=0; alert=0
declare -a W_LLAMA=() W_RPC=() W_ML=()
while IFS='|' read -r verdict kind host port name detail; do
  [ -z "${verdict:-}" ] && continue
  total=$((total+1))
  case "$verdict" in
    BLIND) blind=$((blind+1)) ;;
    *)     reachable=$((reachable+1)) ;;
  esac
  case "$verdict" in
    WEDGED)
      wedged=$((wedged+1)); suspect=$((suspect+1))
      case "$kind" in
        llama) W_LLAMA+=("${kind}|${host}|${port}|${name}|${detail}") ;;
        rpc)   W_RPC+=("${kind}|${host}|${port}|${name}|${detail}") ;;
        ml)    W_ML+=("${kind}|${host}|${port}|${name}|${detail}") ;;
      esac ;;
    SUSPECT) suspect=$((suspect+1)) ;;
    BUSY)    busy=$((busy+1)) ;;
    ALERT)   alert=$((alert+1)) ;;
  esac
  log "[${name} ${host}:${port} ${kind}] ${verdict}: ${detail}"
done < "$SWEEP"

restarted=0
guard=""
if [ "$wedged" -gt 0 ] && [ "$reachable" -gt 1 ]; then
  pct_suspect=$(( suspect * 100 / reachable ))
  if [ "$pct_suspect" -gt "$FLEET_ALARM_PCT" ]; then
    guard="fleet-wide"
    log "FLEET ALARM: ${suspect}/${reachable} reachable targets are suspect" \
        "(${pct_suspect}% > ${FLEET_ALARM_PCT}%). One wedged service is a fault;" \
        "this many at once is a network or infrastructure event (F28: a 65 GB" \
        "model distribution saturates the 100 Mb LAN for ~97 min). REFUSING TO" \
        "RESTART ANYTHING. Investigate the link and the switch first."
  fi
fi
[ "$REPORT_ONLY" = "1" ] && guard="${guard:-report-only}"

# Order matters: repair the dependency before the thing that depends on it.
declare -a ACTIONS=("${W_LLAMA[@]:-}" "${W_RPC[@]:-}" "${W_ML[@]:-}")

if [ -z "$guard" ]; then
  for line in "${ACTIONS[@]}"; do
    [ -z "$line" ] && continue
    if [ "$restarted" -ge "$MAX_RESTARTS" ]; then
      log "restart budget spent (${MAX_RESTARTS} per sweep) -- remaining wedged" \
          "targets will be handled on the next tick"
      break
    fi
    IFS='|' read -r kind host port name detail <<< "$line"

    # Per-host cooldown. A llama-server restart makes missing-link look
    # unhealthy for minutes through no fault of its own; that must not cascade.
    cd_file="${RUN_DIR}/cooldown-${host}"
    if [ -f "$cd_file" ]; then
      cd_age=$(( $(date +%s) - $(stat -c %Y "$cd_file" 2>/dev/null || echo 0) ))
      if [ "$cd_age" -lt "$COOLDOWN" ]; then
        log "[${name} ${host} ${kind}] COOLDOWN: another service on this host was" \
            "restarted ${cd_age}s ago (< ${COOLDOWN}s). Holding off -- a restart" \
            "cascade is how one fault becomes an outage."
        continue
      fi
    fi

    case "$kind" in
      llama) unit="llama-server@${port}" ;;
      rpc)   unit="rpc-server@${port}" ;;
      ml)    unit="missing-link" ;;
      *)     continue ;;
    esac
    tr="$(transport_for "$host")"

    log "RESTARTING ${unit} on ${name} (${host}) -- WEDGED. ${detail}." \
        "ANY IN-FLIGHT WORK ON ${host}:${port} DIED AT THIS TIMESTAMP."

    action="restart"; rc=0
    if [ "$DRY_RUN" = "1" ]; then
      action="dry-run"
      log "WATCHDOG_DRY_RUN=1 -- would have restarted ${unit} on ${host}, but did not."
    else
      resp="$(agent_call "$host" "$tr" 120 restart "$unit")"; rc=$?
      log "${unit} on ${host}: restart issued via ${tr} (rc=${rc}) ${resp}"
      : > "$cd_file"
    fi

    printf '{"ts":"%s","epoch":%s,"node":"%s","host":"%s","kind":"%s","unit":"%s","port":"%s","transport":"%s","action":"%s","rc":%s,"reason":"wedged","detail":"%s","watchdog_host":"%s","mode":"%s"}\n' \
      "$(date -Is)" "$(date +%s)" "$name" "$host" "$kind" "$unit" "$port" "$tr" "$action" "$rc" \
      "$(printf '%s' "$detail" | tr '"' "'" )" "$(hostname)" "$MODE" >> "$RECORD" 2>/dev/null

    rm -f "${RUN_DIR}/streak-${kind}-${host}-${port}"
    restarted=$((restarted+1))
  done
fi

# ---------------------------------------------------------------------------
# Dead-man's switch. The watchdog emits its OWN heartbeat rather than being
# watched by a second watchdog (docs/watchdog-research.md Q2, the Alertmanager
# Watchdog pattern). Silence from this file is the alarm.
#
# It is written locally AND pushed into every reachable node, so a node can
# answer "when did the monitor last look at me?" without the monitor being up.
# See the report for exactly what this does and does not cover.
# ---------------------------------------------------------------------------
{
  printf '{"ts":"%s","epoch":%s,"watchdog_host":"%s","mode":"%s","services":"%s","targets":%s,"reachable":%s,"blind":%s,"busy":%s,"suspect":%s,"alert":%s,"wedged":%s,"restarted":%s,"guard":"%s","verdicts":[' \
    "$(date -Is)" "$(date +%s)" "$(hostname)" "$MODE" "$SERVICES" "$total" "$reachable" \
    "$blind" "$busy" "$suspect" "$alert" "$wedged" "$restarted" "${guard:-none}"
  sep=""
  while IFS='|' read -r verdict kind host port name detail; do
    [ -z "${verdict:-}" ] && continue
    printf '%s{"node":"%s","host":"%s","port":"%s","kind":"%s","verdict":"%s"}' \
      "$sep" "$name" "$host" "$port" "$kind" "$verdict"
    sep=","
  done < "$SWEEP"
  printf ']}\n'
} > "$HEARTBEAT" 2>/dev/null

declare -A PUSHED=()
for i in "${!T_HOST[@]}"; do
  h="${T_HOST[$i]}"
  [ -n "${PUSHED[$h]:-}" ] && continue
  PUSHED[$h]=1
  agent_call "$h" "$(transport_for "$h")" 30 heartbeat < "$HEARTBEAT" >/dev/null 2>&1 &
done
wait 2>/dev/null

if [ -n "$HEARTBEAT_URL" ]; then
  curl -sf --max-time 10 -X POST -H 'Content-Type: application/json' \
    --data-binary @"$HEARTBEAT" "$HEARTBEAT_URL" >/dev/null 2>&1 \
    || log "heartbeat POST to ${HEARTBEAT_URL} failed (non-fatal)"
fi

if [ "$REPORT_ONLY" = "1" ]; then
  echo
  printf '%-10s %-15s %-6s %-6s %-10s %s\n' NODE HOST PORT KIND VERDICT DETAIL
  while IFS='|' read -r verdict kind host port name detail; do
    [ -z "${verdict:-}" ] && continue
    printf '%-10s %-15s %-6s %-6s %-10s %s\n' "$name" "$host" "$port" "$kind" "$verdict" "$detail"
  done < "$SWEEP"
  echo
  echo "heartbeat: $HEARTBEAT ($(date -Is))"
fi

# Exit non-zero if something needs a human or a next tick, so a cron wrapper on
# an out-of-band appliance can notice. BUSY and OK are not faults.
if [ "$wedged" -gt 0 ] || [ "$blind" -gt 0 ] || [ "$alert" -gt 0 ]; then
  exit 1
fi
exit 0

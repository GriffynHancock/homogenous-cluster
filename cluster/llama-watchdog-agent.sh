#!/usr/bin/env bash
# Node-side agent for the liveness watchdog. Runs on EVERY monitored node.
#
# It is deliberately two things at once:
#
#   1. The **forced command** for the watchdog's restricted SSH key. The key is
#      installed as
#         restrict,command="/usr/local/sbin/llama-watchdog-agent" ssh-ed25519 ...
#      so a holder of that key can run NOTHING on this node except the verbs
#      below. See the SECURITY note.
#
#   2. A plain local command, so the IN-BAND watchdog on a cluster node runs the
#      exact same code with no SSH hop. That is the point: the in-band and
#      out-of-band signals are not merely "equivalent", they are IDENTICAL --
#      the same file, the same arithmetic. Only the transport differs.
#
# It is NOT a daemon. It has no state, no port, and nothing to hang. It is
# execed by sshd (or by bash) per probe and exits. `docs/watchdog-research.md`
# recommends against a per-node agent daemon at this scale and this respects
# that: nothing here stays resident.
#
# ---------------------------------------------------------------------------
# WHY IT MEASURES CGROUP CPU AND NOT `/proc/<MainPID>/stat`
#
# `docs/watchdog-research.md` recommends `utime+stime` from
# `/proc/$(systemctl show -p MainPID --value llama-server@8080)/stat`.
# **On this fleet that signal is identically zero and would restart every busy
# server.** Measured on node 1, 2026-08-18:
#
#     MainPID=72777  ->  /bin/sh   (comm=sh, threads=1, utime=0 stime=0)
#     child 72781    ->  llama-server   <- the process that actually burns CPU
#
# `llama-server@.service` runs `ExecStart=/bin/sh -c '...'` (it has to: systemd
# does not expand the `${LLAMA_*}` EnvironmentFile substitutions itself), and
# the shell does NOT exec away -- it stays as the parent. So MainPID names a
# shell that has been asleep in `wait()` since boot. `utime+stime` for it reads
# 0 during a 400%-of-a-core prefill.
#
# The cgroup is the correct scope: it covers every process of the unit,
# wrapper and server alike. Both readings below are taken from it.
#   - `systemctl show -p CPUUsageNSec` (needs CPUAccounting=yes; systemd sets it
#     implicitly on any unit with resource control, and it is asserted by
#     `cluster/install-services.sh`)
#   - `<cgroup>/cpu.stat` `usage_usec` -- the same number without systemd, used
#     as the fallback. Verified equal on node 1: CPUUsageNSec 6569646000 ns vs
#     cpu.stat usage_usec 6569646.
#
# Both are scoped to the unit, so both are immune to the machine-wide-load
# problem that killed the load-average idea (F39: node 1 read load 2.30 while
# llama-server was completely idle, because other agents were running).
#
# ---------------------------------------------------------------------------
# WHY THE AGENT SLEEPS, RATHER THAN THE CONTROLLER
#
# The CPU signal is a DELTA and needs two samples W seconds apart. Taking them
# with two SSH calls would (a) double the connections and (b) let network
# congestion stretch the window -- and F28 measured RTT collapsing 11.5x, from
# 0.827 ms idle to 9.544 ms, while a single rsync saturates the 100 Mb link.
# Sleeping HERE means the window is timed by the monitored node's own clock and
# is exact regardless of what the network is doing. The controller only has to
# survive the round trip.
#
# ---------------------------------------------------------------------------
# SECURITY
#
# This script is the entire attack surface the watchdog key grants. A watchdog
# that can restart services fleet-wide is a security-relevant capability on
# machines holding sensitive documents, so:
#
#   * every verb is on an explicit allowlist; anything else exits 2 having done
#     nothing;
#   * unit names are validated against a fixed pattern -- only
#     `llama-server@<digits>` and `rpc-server@<digits>` are accepted, so no
#     argument can name another unit;
#   * `restart` is the ONLY privileged verb, and the only sudoers grant the
#     watchdog user gets is `systemctl restart llama-server@*`;
#   * the heartbeat payload is read from stdin with a hard byte cap and written
#     to one fixed path;
#   * `probe`, `version`, `tcpcheck`, `mlstate` and `synth` need no privilege
#     at all -- `synth` in particular only ever talks to 127.0.0.1:<port> on
#     THIS node (the port comes from the caller, the host never does), so the
#     restricted key cannot be turned into a way to make this node call out
#     anywhere else.
#
# `systemctl -H user@host` was considered and REJECTED for the restart path.
# It works by running `systemd-stdio-bridge` on the remote and speaking the
# systemd D-Bus **manager** API over it. A `command=` restriction can only pin
# the key to `systemd-stdio-bridge` itself, which still exposes StartUnit,
# StopUnit and StartTransientUnit for EVERY unit -- i.e. arbitrary root command
# execution via the `systemd-run` path. There is no way to narrow it to one
# unit and one verb. This wrapper exposes three verbs and one unit template.
#
# ---------------------------------------------------------------------------
# 2026-08-18 ADDITIONS: nprocs (a free F40 tripwire) and `synth` (the
# synthetic transaction). Why not trust the backend to report itself:
#
# "process-alive, port-open and self-reported health are all defeated" (F40) --
# `ggml_abort()` FORKS from a multithreaded process
# (`src/ggml/src/ggml.c:ggml_print_backtrace`, `waitpid(child_pid, NULL, 0)`
# with no timeout) to try to print a backtrace via gdb/lldb. If the child
# deadlocks on an inherited malloc-arena lock -- exactly what F40 caught on
# node 2, `wchan=futex_wait_queue` on all four children -- the parent blocks in
# `wait4()` FOREVER: never exits (Restart=always sees nothing), never releases
# its listening socket (a port check sees "accept"), and the forked children
# INHERIT that socket too, so even a killed-and-restarted illusion of the
# service survives via its own zombies.
#
# CONFIRMED 2026-08-18: this is NOT an ik_llama.cpp-only hazard.
# `/opt/llama.cpp/src/ggml/src/ggml.c` (mainline, the engine this fleet now
# runs per F40's own reversal) has the IDENTICAL fork()/waitpid() code at the
# same call site. Any future ggml_abort() on mainline -- whatever triggers it
# -- can reproduce F40's exact hang. The fleet moving off ik_llama.cpp did not
# retire this risk.
#
# nprocs is the free, structural tripwire for it: a forked child is a real
# process, so it gets its OWN entry in the unit's cgroup.procs (threads do
# not). MEASURED baseline, both nodes, 2026-08-18, servers healthy and BUSY
# (400% of a core -- so this is not an idle-only artefact):
#   llama-server@8080:  2 procs (the ExecStart=/bin/sh -c wrapper + the server)
#   rpc-server@50052:   1 proc  (no shell wrapper needed for this unit)
# A count ABOVE that baseline, sustained across a confirming second sample, is
# near-certain evidence of the abort-fork signature -- not an inference, a
# structural fact about the process tree. It costs one extra line already
# being read for SRVPID discovery; nothing new is opened.
#
# `synth` is the decisive, expensive check, reserved for when the cheap ones
# are ambiguous (idle, port fine, nprocs fine, /health answers ok). On
# mainline, `/health` answering proves NOTHING about the inference pipeline --
# `docs/watchdog-research.md` Q1: mainline's handler is a lambda returning a
# server-state variable, deliberately NEVER posted to the task queue (that is
# what makes it cheap and instant, and also what makes it blind to a wedged
# queue). Only a real completion exercises tokenizer -> queue -> compute ->
# detokenize -> response, which is the whole point of a synthetic
# TRANSACTION rather than a synthetic PING: a listen backlog can fake a port,
# nothing can fake generated text. See the `synth` verb below for the request
# shape, the content-validation rules (ported from
# `missing_link/worker.py:extract_content`, F21/F34's empty/truncated guards),
# the internal-vs-external timing comparison, and the cost.
# ---------------------------------------------------------------------------
set -uo pipefail

AGENT_PROTO=4

# The agent runs as the UNPRIVILEGED watchdog user over SSH, so its drop box
# cannot be root's /var/lib/llama-watchdog -- it writes into its own home. This
# was not theoretical: the first version pointed at the root-owned directory and
# every remote heartbeat push failed silently, which is precisely the way a
# dead-man's switch fails uselessly (it looks installed and reports nothing).
STATE_DIR="${WATCHDOG_AGENT_STATE_DIR:-/var/lib/llama-watchdog-agent}"

# sshd with a forced command puts the client's request in SSH_ORIGINAL_COMMAND
# and passes the FORCED command's own argv. Prefer the client's request when
# present; fall back to argv for local invocation.
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  # Deliberately unquoted: we want word splitting into positional parameters,
  # and every resulting word is validated below before use.
  # shellcheck disable=SC2086
  set -- $SSH_ORIGINAL_COMMAND
  # Under a forced command sshd hands us the client's ENTIRE request string,
  # which still begins with the agent path the controller asked for. Swallow a
  # leading self-reference so the same controller invocation works whether the
  # key is forced-command-restricted or not.
  case "${1:-}" in
    */llama-watchdog-agent|*/llama-watchdog-agent.sh|llama-watchdog-agent) shift ;;
  esac
fi

VERB="${1:-}"

die() { echo "wd_agent_error=$*" >&2; exit 2; }

# Only these units are addressable, and templated ones only with a numeric
# instance. This is what stops an argument naming, say, sshd or a shell.
validate_unit() {
  case "$1" in
    llama-server@[0-9]*|rpc-server@[0-9]*)
      case "${1#*@}" in
        *[!0-9]*|'') die "refused_instance:$1" ;;
      esac ;;
    missing-link) ;;
    *) die "refused_unit:$1" ;;
  esac
  [ "${#1}" -le 24 ] || die "refused_unit_length"
}

# Unit cgroup CPU, in nanoseconds. Tries systemd first, then the raw cgroup
# file, then gives up honestly rather than returning a misleading zero.
cpu_ns() {
  local unit="$1" v cg
  v=$(systemctl show -p CPUUsageNSec --value "${unit}.service" 2>/dev/null)
  case "$v" in
    ''|*[!0-9]*) v="" ;;
  esac
  if [ -n "$v" ]; then echo "$v"; return 0; fi

  # Fallback: read cpu.stat straight out of the cgroup. usage_usec is the same
  # counter systemd reports, in microseconds.
  cg=$(systemctl show -p ControlGroup --value "${unit}.service" 2>/dev/null)
  if [ -n "$cg" ] && [ -r "/sys/fs/cgroup${cg}/cpu.stat" ]; then
    v=$(awk '$1=="usage_usec"{print $2*1000; exit}' "/sys/fs/cgroup${cg}/cpu.stat" 2>/dev/null)
    case "$v" in
      ''|*[!0-9]*) v="" ;;
    esac
    [ -n "$v" ] && { echo "$v"; return 0; }
  fi
  echo ""      # empty means "cannot measure" -- NEVER means "zero"
  return 1
}

now_ns() { date +%s%N; }

# Process count in the unit's cgroup -- the F40 tripwire (header note above).
# Cheap and free of side effects: reads cgroup.procs, counts lines, nothing
# else. Empty/unreadable reports 0 rather than failing the whole probe over a
# diagnostic extra -- the controller already treats 0 as "could not measure"
# for this field specifically (baselines here are 1 and 2, never 0 for an
# ACTIVE unit).
nprocs_of() {
  local unit="$1" cg n
  cg=$(systemctl show -p ControlGroup --value "${unit}.service" 2>/dev/null)
  [ -n "$cg" ] && [ -r "/sys/fs/cgroup${cg}/cgroup.procs" ] || { echo 0; return; }
  n=$(grep -c . "/sys/fs/cgroup${cg}/cgroup.procs" 2>/dev/null)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  echo "$n"
}

case "$VERB" in

  version)
    echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) ok=1"
    ;;

  # probe <unit> <window_seconds>
  #
  # Emits one line of key=value pairs. cpu0/cpu1 are cgroup CPU nanoseconds
  # either side of a window timed on THIS node. `cpu=` empty means "could not
  # measure" and the controller must treat that as no evidence, not as idle.
  probe)
    UNIT="${2:-}"; W="${3:-10}"
    [ -n "$UNIT" ] || die "probe_needs_unit"
    validate_unit "$UNIT"
    case "$W" in *[!0-9]*|'') die "refused_window:$W" ;; esac
    [ "$W" -ge 1 ] && [ "$W" -le 120 ] || die "refused_window_range:$W"

    ACTIVE=$(systemctl is-active "${UNIT}.service" 2>/dev/null)
    SINCE=$(systemctl show -p ActiveEnterTimestamp --value "${UNIT}.service" 2>/dev/null)
    MAINPID=$(systemctl show -p MainPID --value "${UNIT}.service" 2>/dev/null)

    # RPC peers, read out of the real server's cmdline. The unit's MainPID is
    # the /bin/sh wrapper, so walk the cgroup for the process whose comm
    # actually matches, rather than trusting MainPID (see the header note).
    #
    # NPROCS is counted from the SAME cgroup.procs read -- one extra counter in
    # a loop already running, not a new probe. It is the F40 tripwire: a real
    # fork() (the abort-backtrace hazard, header note above) gets its own PID
    # here; threads never do. Baseline is unit-specific and known from
    # measurement (2 for llama-server, 1 for rpc-server); the controller
    # compares, this agent just counts and reports.
    SRVPID=""; CMDLINE=""; NPROCS=0
    CG=$(systemctl show -p ControlGroup --value "${UNIT}.service" 2>/dev/null)
    if [ -n "$CG" ] && [ -r "/sys/fs/cgroup${CG}/cgroup.procs" ]; then
      while read -r p; do
        [ -z "$p" ] && continue
        NPROCS=$((NPROCS + 1))
        [ -n "$SRVPID" ] && continue
        [ -r "/proc/$p/comm" ] || continue
        c=$(cat "/proc/$p/comm" 2>/dev/null)
        case "$c" in
          llama-server|ggml-rpc-serve*|rpc-server) SRVPID="$p" ;;
        esac
      done < "/sys/fs/cgroup${CG}/cgroup.procs"
    fi
    [ -z "$SRVPID" ] && [ -n "${MAINPID:-}" ] && [ "$MAINPID" != "0" ] && SRVPID="$MAINPID"
    [ -n "$SRVPID" ] && [ -r "/proc/$SRVPID/cmdline" ] && \
      CMDLINE=$(tr '\0' ' ' < "/proc/$SRVPID/cmdline" 2>/dev/null)

    RPC=0; RPCPEERS=""
    case " $CMDLINE " in
      *" --rpc "*)
        RPC=1
        RPCPEERS=$(printf '%s\n' "$CMDLINE" | awk '{for(i=1;i<NF;i++) if($i=="--rpc") print $(i+1)}' | head -1)
        ;;
    esac

    if [ "$ACTIVE" != "active" ]; then
      # Nothing to measure and nothing to judge: Restart=always owns this.
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) unit=${UNIT} active=${ACTIVE:-unknown} rpc=${RPC} nprocs0=${NPROCS} nprocs1=${NPROCS} ok=1"
      exit 0
    fi

    C0=$(cpu_ns "$UNIT"); T0=$(now_ns)
    sleep "$W"
    T1=$(now_ns); C1=$(cpu_ns "$UNIT")
    # Resample nprocs too -- free (no sleep of its own) and catches an abort
    # that forked DURING this window, not just one already present at t0.
    # The controller takes the max of the two against the unit's baseline.
    NPROCS1=$(nprocs_of "$UNIT")

    echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) unit=${UNIT} active=${ACTIVE}" \
         "srvpid=${SRVPID:-0} cpu0=${C0:-} cpu1=${C1:-} t0=${T0} t1=${T1}" \
         "window=${W} rpc=${RPC} rpcpeers=${RPCPEERS:-} since=${SINCE// /_}" \
         "nprocs0=${NPROCS} nprocs1=${NPROCS1} ok=1"
    ;;

  # tcpcheck <port> -- does a LOCAL listener accept a connection?
  #
  # Run on the node, deliberately: it tests the listener, not the network, so a
  # congested link cannot make it fail. This is the whole liveness predicate for
  # rpc-server, which is legitimately idle whenever no shard work is in flight
  # and therefore CANNOT be judged on CPU progress. Same connect-and-close probe
  # cluster/install-services.sh already uses on the RPC port.
  #
  # It is a WEAK positive by construction: the kernel completes the handshake
  # from the listen backlog even when the process is frozen (proved by the
  # SIGSTOP test, where /health connected in 0.000163 s and then never
  # answered). So "accepts" means only "not obviously dead" and is never taken
  # as proof of health; "refuses" is the signal.
  tcpcheck)
    P="${2:-}"
    case "$P" in ''|*[!0-9]*) die "refused_port:$P" ;; esac
    [ "$P" -ge 1 ] && [ "$P" -le 65535 ] || die "refused_port_range:$P"
    if timeout 5 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${P}" 2>/dev/null; then
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} tcp=accept ok=1"
    else
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} tcp=refused ok=1"
    fi
    ;;

  # synth <port> <max_tokens> <timeout_s> -- THE SYNTHETIC TRANSACTION.
  #
  # A tiny, real completion, run against 127.0.0.1:<port> ONLY (never a
  # caller-supplied host -- the point is to test THIS node's server, and a
  # restricted key must not become a way to make this node call out anywhere
  # else). It is the one check in this ladder that cannot be satisfied by a
  # listen backlog, a cached process table entry, or a lambda that never
  # touches the inference queue (mainline's /health -- see the header note):
  # only a live tokenizer -> queue -> compute -> detokenize -> HTTP response
  # round trip produces real generated text.
  #
  # CONTENT VALIDATION IS PORTED FROM missing_link/worker.py:extract_content,
  # NOT reimplemented from scratch and NOT weakened: empty content is a
  # failure (F21), non-empty content with finish_reason="length" is a failure
  # (F34 -- truncated output stored as if it were complete), and a model that
  # spent its whole budget in reasoning_content with nothing in content is the
  # SAME failure by a different route (F21's exact reproduction). This is a
  # deliberate second implementation, not an import: the agent ships to bare
  # nodes with no missing-link venv, and importing a sibling service's
  # internals would be the wrong kind of coupling for what is meant to stay a
  # small, dependency-free script. If extract_content's contract changes,
  # this needs to change with it -- that seam is a maintenance cost accepted
  # deliberately, not an oversight.
  #
  # `reasoning_effort: low` is sent unconditionally. F35: llama-server drops
  # unrecognised request kwargs silently, so this is inert on a model that
  # does not understand it and helpful on one that does (gpt-oss/harmony,
  # both fleet nodes' current model) -- there is no per-model branch here
  # because the agent has no reliable way to know which model is loaded
  # without ITSELF being a second client of the very server it is checking.
  #
  # COST, from documented rates (`docs/measurements.md` /
  # `docs/FINDINGS.md` F17/F24/F27), stated as an ESTIMATE and NOT re-labelled
  # as measured: prompt is ~12 tokens, prefill is compute-bound and fast at
  # this size regardless of engine (well under 1 s); the default max_tokens
  # (96) is sized against worker.py's own measured gpt-oss reasoning-token
  # range (49-129 tokens across the tested reasoning_effort settings, see
  # worker.py's REASONING_KWARGS comment) so a healthy server usually finishes
  # before the cap, not at it. At gpt-oss's measured 6.05 t/s generation
  # (F24), 96 tokens is a WORST-CASE ~16 s. Occupies one of four --parallel
  # slots for that span; the other three are untouched. Rate-limited by the
  # CONTROLLER (WATCHDOG_SYNTH_INTERVAL_S), not by this agent, which will run
  # exactly the transaction it is asked for, whenever it is asked. THIS
  # NUMBER IS NOT YET MEASURED LIVE -- both fleet nodes were running real
  # document jobs throughout this change (hard constraint of the task this
  # was built under); see the report for the exact command to run once a node
  # is idle.
  synth)
    P="${2:-}"; MT="${3:-96}"; TO="${4:-60}"
    case "$P" in ''|*[!0-9]*) die "refused_port:$P" ;; esac
    [ "$P" -ge 1 ] && [ "$P" -le 65535 ] || die "refused_port_range:$P"
    case "$MT" in ''|*[!0-9]*) die "refused_max_tokens:$MT" ;; esac
    [ "$MT" -ge 1 ] && [ "$MT" -le 512 ] || die "refused_max_tokens_range:$MT"
    case "$TO" in ''|*[!0-9]*) die "refused_timeout:$TO" ;; esac
    [ "$TO" -ge 1 ] && [ "$TO" -le 300 ] || die "refused_timeout_range:$TO"

    PY=""
    for c in "${WATCHDOG_PYTHON:-}" /usr/bin/python3 python3; do
      [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
    if [ -z "$PY" ]; then
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} synth_verdict=AGENT_ERROR synth_detail=no_python3 ok=0"
      exit 3
    fi

    # No RETURN trap here (this is a case arm in the main script, not a
    # function -- RETURN never fires). $BODY is removed explicitly on every
    # exit path below instead.
    BODY="$(mktemp "${TMPDIR:-/tmp}/wd-synth.XXXXXX")"
    REQ='{"model":"synthetic-probe","messages":[{"role":"user","content":"Reply with the single word: OK."}],"max_tokens":'"$MT"',"temperature":0,"stream":false,"reasoning_effort":"low"}'

    # time_total is curl's own high-resolution client-side timer -- the
    # EXTERNAL half of the internal-vs-external comparison the controller
    # makes. http_code=000 means curl never got a response at all (refused,
    # timed out, connection reset) and is reported as its own verdict rather
    # than fed to the JSON parser.
    META="$(curl -s -o "$BODY" -w '%{http_code} %{time_total}' \
            --max-time "$TO" --connect-timeout 5 \
            -H 'Content-Type: application/json' \
            -X POST "http://127.0.0.1:${P}/v1/chat/completions" \
            -d "$REQ" 2>/dev/null)"
    HCODE="${META%% *}"; EXT_S="${META#* }"
    case "$HCODE" in ''|*[!0-9]*) HCODE=000 ;; esac

    if [ "$HCODE" = "000" ]; then
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} synth_verdict=TRANSPORT_FAIL synth_detail=curl_no_response external_s=${EXT_S:-} ok=1"
      rm -f "$BODY"; exit 0
    fi
    if [ "$HCODE" -lt 200 ] || [ "$HCODE" -ge 300 ]; then
      DETAIL=$(head -c 200 "$BODY" 2>/dev/null | tr '\n"' '  ')
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} synth_verdict=HTTP_${HCODE} synth_detail=${DETAIL:-none} external_s=${EXT_S:-} ok=1"
      rm -f "$BODY"; exit 0
    fi

    # Content validation, ported from extract_content (see the comment above).
    # Prints ONE line: "VERDICT|detail|prompt_ms|predicted_ms" so the caller
    # never has to parse JSON itself.
    RESULT="$("$PY" -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        payload = json.load(f)
except Exception as e:
    print("MALFORMED|%s: %s|-1|-1" % (type(e).__name__, str(e)[:150]))
    sys.exit(0)
try:
    choice = payload["choices"][0]
    message = choice.get("message", {}) or {}
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    finish = choice.get("finish_reason")
    timings = payload.get("timings") or {}
    p_ms = timings.get("prompt_ms", -1)
    d_ms = timings.get("predicted_ms", -1)
except Exception as e:
    print("MALFORMED|shape: %s: %s|-1|-1" % (type(e).__name__, str(e)[:150]))
    sys.exit(0)

if content:
    if finish == "length":
        print("TRUNCATED|hit max_tokens mid-answer (%d chars)|%s|%s" % (len(content), p_ms, d_ms))
    else:
        print("PASS|%d chars, finish=%s|%s|%s" % (len(content), finish, p_ms, d_ms))
elif reasoning and finish == "length":
    print("EMPTY_REASONING|%d chars reasoning, 0 content, finish=length|%s|%s" % (len(reasoning), p_ms, d_ms))
elif finish == "length":
    print("EMPTY_LENGTH|hit max_tokens, no content at all|%s|%s" % (p_ms, d_ms))
else:
    print("EMPTY|no content, finish=%s|%s|%s" % (finish, p_ms, d_ms))
' "$BODY" 2>&1)"
    rm -f "$BODY"

    VERDICT="${RESULT%%|*}"
    REST="${RESULT#*|}"
    DETAIL="${REST%%|*}"; REST="${REST#*|}"
    P_MS="${REST%%|*}"; D_MS="${REST#*|}"

    # Internal-vs-external disagreement, per the operator's framing ("why are
    # we depending so much on the backend to report itself?"). Only the
    # STRICT direction is treated as an anomaly worth a distinct field:
    # internal (prompt_ms+predicted_ms) claiming MORE work than the whole
    # request's wall-clock time is a logical impossibility -- the internal
    # clock covers a subset of what curl's timer covers, never a superset --
    # so any occurrence is unambiguous evidence the server's self-report is
    # wrong, not merely slow. The other direction (external much larger than
    # internal) has too many innocent explanations on this fleet -- queueing
    # behind another slot, a cold prompt cache -- to threshold without a
    # measurement, so it is reported as a number and left for the controller
    # to log, not to act on. See the report for why this is the only half of
    # "measure externally and internally, and treat disagreement as its own
    # fault signal" implemented today.
    DISAGREE="no"
    case "$P_MS $D_MS" in
      *[!0-9.\ -]*) ;;  # non-numeric, skip
      *)
        INT_MS=$(echo "$P_MS $D_MS" | awk '{i=$1+$2; if (i<0) i=0; printf "%d", i}')
        EXT_MS=$(echo "$EXT_S" | awk '{printf "%d", $1*1000}')
        if [ "$INT_MS" -gt 0 ] && [ "$EXT_MS" -gt 0 ] && [ "$INT_MS" -gt "$EXT_MS" ]; then
          DISAGREE="yes:internal=${INT_MS}ms>external=${EXT_MS}ms"
        fi
        ;;
    esac

    echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) port=${P} synth_verdict=${VERDICT} synth_detail=${DETAIL// /_} external_s=${EXT_S:-} prompt_ms=${P_MS} predicted_ms=${D_MS} disagree=${DISAGREE} ok=1"
    ;;

  # mlstate -- Missing Link's own progress counters, read from its SQLite job
  # store. It never touches llama-server, which is the point: this is how a
  # queue that is legitimately idle (the normal state) is told apart from one
  # that has stopped making progress while jobs are running.
  #
  # chunk_summaries has no timestamp column, so PROGRESS IS A COUNTER, not a
  # clock: the controller compares this row count against the previous sweep's.
  # The job-store mtime and its -wal mtime are reported as a second, cheaper
  # progress signal that needs nothing but stat().
  #
  # THE JOB STORE IS IN WAL MODE (`pragma journal_mode` -> wal, confirmed on
  # node 1). SQLite readers of a WAL database need WRITE access to the `-shm`
  # index, so `mode=ro` as an unprivileged user fails -- measured: every count
  # came back -1 for the watchdog user while succeeding for the owner. The
  # watchdog must NOT be given write access to the job store to work around
  # that. Instead the query is re-run as the account that already owns the
  # database, through one narrow sudoers rule for this exact argv:
  #
  #     llamawd ALL=(debian1) NOPASSWD: /usr/local/sbin/llama-watchdog-agent mlstate-raw
  #
  # Target user, not root; one fixed command; no arguments to smuggle anything
  # through. Running as root in-band takes the direct path and never uses it.
  mlstate|mlstate-raw)
    DB="${WATCHDOG_ML_DB:-}"
    [ -z "$DB" ] && [ -r /etc/default/missing-link ] && \
      DB=$(awk -F= '$1=="MISSING_LINK_DB"{print $2; exit}' /etc/default/missing-link)
    DB="${DB:-/opt/missing-link/jobs.sqlite}"
    PY=""
    for c in "${WATCHDOG_PYTHON:-}" /usr/bin/python3 python3; do
      [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
    if [ -z "$PY" ] || [ ! -r "$DB" ]; then
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) ml_db=${DB} ok=0 error=unreadable_or_no_python"
      exit 3
    fi
    MT=$(stat -c %Y "$DB" 2>/dev/null || echo 0)
    WT=$(stat -c %Y "${DB}-wal" 2>/dev/null || echo 0)
    [ "$WT" -gt "$MT" ] 2>/dev/null && MT="$WT"

    ml_query() {
      timeout 10 "$PY" -c '
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True, timeout=3)
c.execute("PRAGMA busy_timeout=3000")
def one(q):
    r = c.execute(q).fetchone()
    return r[0] if r and r[0] is not None else 0
print("ml_running=%s ml_pending=%s ml_chunks=%s ml_jobs=%s" % (
    one("select count(*) from jobs where status='"'"'running'"'"'"),
    one("select count(*) from jobs where status in ('"'"'pending'"'"','"'"'queued'"'"')"),
    one("select count(*) from chunk_summaries"),
    one("select count(*) from jobs")))
' "$1" 2>/dev/null
    }

    ML_OUT="$(ml_query "$DB")"

    # Only the outer `mlstate` verb may escalate, and only to the job store's
    # own user. `mlstate-raw` is the escalated form and must never recurse.
    if [ -z "$ML_OUT" ] && [ "$VERB" = "mlstate" ] && [ "$(id -u)" != "0" ]; then
      ML_USER=$(systemctl show -p User --value missing-link.service 2>/dev/null)
      if [ -n "$ML_USER" ] && [ "$ML_USER" != "$(id -un)" ]; then
        ML_OUT="$(sudo -n -u "$ML_USER" /usr/local/sbin/llama-watchdog-agent mlstate-raw 2>/dev/null \
                  | tr ' ' '\n' | grep -E '^ml_(running|pending|chunks|jobs)=' | tr '\n' ' ')"
        ML_OUT="${ML_OUT% }"
      fi
    fi

    if [ -z "$ML_OUT" ]; then
      # Honest failure. The controller treats an unknown running-job count as
      # "cannot rule out a running job" and refuses to act -- which is correct,
      # because restarting missing-link mid-job is what caused F36.
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) ml_db=${DB} ml_db_mtime=${MT} ok=0 error=query_failed_as_$(id -un)"
      exit 3
    fi
    echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) ml_db=${DB} ml_db_mtime=${MT} ${ML_OUT} ok=1"
    ;;

  # restart <unit> -- the only privileged verb.
  #
  # The controller decides WHETHER; this decides WHAT MAY BE NAMED. Note that
  # `missing-link` is restartable here but the controller will not restart it
  # while a job is running: F36 was CAUSED by exactly that action, which left
  # llama-server wedged alive. The confinement and the policy are separate
  # layers on purpose.
  restart)
    UNIT="${2:-}"
    [ -n "$UNIT" ] || die "restart_needs_unit"
    validate_unit "$UNIT"
    case "$UNIT" in
      llama-server@*|rpc-server@*|missing-link) ;;
      *) die "restart_refused_for_unit:$UNIT" ;;
    esac
    if [ "$(id -u)" = "0" ]; then
      systemctl restart "${UNIT}.service"; rc=$?
    else
      sudo -n systemctl restart "${UNIT}.service"; rc=$?
    fi
    echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) unit=${UNIT} action=restart rc=${rc} ok=1"
    exit "$rc"
    ;;

  # heartbeat -- accept the watchdog's own liveness record on stdin and drop it
  # at one fixed path. This is the dead-man's switch: the CLUSTER can then see
  # how long it has been since the watchdog last checked in, so the watchdog
  # going silent is itself detectable. Capped so a wedged writer cannot fill
  # the disk.
  heartbeat)
    mkdir -p "$STATE_DIR" 2>/dev/null
    if head -c 65536 > "${STATE_DIR}/watchdog-heartbeat.json.tmp" 2>/dev/null &&
       mv -f "${STATE_DIR}/watchdog-heartbeat.json.tmp" "${STATE_DIR}/watchdog-heartbeat.json" 2>/dev/null; then
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) action=heartbeat path=${STATE_DIR}/watchdog-heartbeat.json ok=1"
    else
      rm -f "${STATE_DIR}/watchdog-heartbeat.json.tmp" 2>/dev/null
      # Never silent. A dead-man's switch that fails quietly is worse than none.
      echo "wd_agent_proto=${AGENT_PROTO} host=$(hostname) action=heartbeat path=${STATE_DIR} ok=0 error=not_writable_by_$(id -un)"
      exit 3
    fi
    ;;

  '')
    die "no_verb (allowed: version probe tcpcheck synth mlstate restart heartbeat)"
    ;;
  *)
    die "refused_verb:${VERB}"
    ;;
esac

#!/usr/bin/env bash
# Install the liveness watchdog: the node-side agent on every node, and the
# controller + timers wherever the watching happens.
#
# Idempotent and re-runnable, per CLAUDE.md's standing constraint. Every step
# is either "create if absent" or "overwrite with the repo's copy"; running it
# twice changes nothing the second time.
#
#   ./cluster/install-watchdog.sh                    # nodes + this machine
#   ./cluster/install-watchdog.sh --nodes node1      # a subset, by name or IP
#   ./cluster/install-watchdog.sh --watcher-only     # OUT-OF-BAND appliance
#   ./cluster/install-watchdog.sh --nodes-only       # skip the local controller
#
# ---------------------------------------------------------------------------
# WHAT CREDENTIAL THIS GRANTS, AND WHY IT IS SHAPED THIS WAY
#
# A watchdog that can restart services fleet-wide is a security-relevant
# capability on machines holding sensitive documents. So it does NOT get the
# cluster admin key (`~/.ssh/id_ed25519`, which carries passwordless sudo on
# every node). It gets its own key, and that key can do exactly four things:
#
#   restrict,command="/usr/local/sbin/llama-watchdog-agent" ssh-ed25519 ...
#
#   * `restrict` -- no port forwarding, no agent forwarding, no X11, no PTY,
#     no user rc file. OpenSSH's deny-by-default umbrella.
#   * `command=` -- the client's requested command is never executed. sshd runs
#     the agent, which reads the request from SSH_ORIGINAL_COMMAND and matches
#     it against an allowlist of four verbs (version/probe/restart/heartbeat).
#   * the agent validates unit names against `llama-server@<digits>` and
#     `rpc-server@<digits>`, so no argument can name another unit.
#   * the ONE privileged operation is a single sudoers line:
#         llamawd ALL=(root) NOPASSWD: /usr/bin/systemctl restart llama-server@*
#     Probing needs no privilege at all.
#
# `systemctl -H user@host` (docs/watchdog-research.md Q4) was rejected here.
# It tunnels the systemd D-Bus **manager** API over SSH via
# `systemd-stdio-bridge`. A `command=` restriction can only pin the key to that
# bridge, which still exposes StartUnit/StopUnit/StartTransientUnit for every
# unit -- i.e. arbitrary root execution through the `systemd-run` path. There
# is no way to narrow it to one unit and one verb. The two are NOT equivalent
# in trust, only in convenience.
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source provisioning/nodes.env

WD_USER="${WATCHDOG_USER:-llamawd}"
WD_HOME="${WATCHDOG_HOME:-/var/lib/llama-watchdog-agent}"
WD_KEY="${WATCHDOG_SSH_KEY:-$HOME/.ssh/id_llama_watchdog}"
WD_PORTS="${WATCHDOG_PORTS:-8080}"

ONLY=""; DO_NODES=1; DO_WATCHER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --nodes) ONLY="$2"; shift 2 ;;
    --watcher-only) DO_NODES=0; shift ;;
    --nodes-only) DO_WATCHER=0; shift ;;
    -h|--help) sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

say() { printf '  %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. The watchdog's own key. Created once, never reused from another purpose.
# ---------------------------------------------------------------------------
if [ ! -f "$WD_KEY" ]; then
  echo "Creating the watchdog's dedicated SSH key at $WD_KEY"
  mkdir -p "$(dirname "$WD_KEY")"; chmod 700 "$(dirname "$WD_KEY")"
  ssh-keygen -t ed25519 -N '' -C 'llama-watchdog (restricted: probe/restart only)' \
    -f "$WD_KEY" >/dev/null
  say "created"
else
  say "watchdog key already present at $WD_KEY -- reused"
fi
WD_PUB="$(cat "${WD_KEY}.pub")"

# ---------------------------------------------------------------------------
# 2. Node side.
# ---------------------------------------------------------------------------
install_node() {
  local NAME="$1" IP="$2" TGT="$3"

  echo "== $NAME ($IP, admin login ${TGT%@*})"

  # Ship the two scripts and the fleet list. `install -m` is atomic-ish and
  # idempotent; re-running simply overwrites with the same bytes.
  scp -q cluster/llama-watchdog-agent.sh cluster/llama-watchdog.sh \
         provisioning/nodes.env \
         cluster/llama-watchdog@.service cluster/llama-watchdog@.timer \
         cluster/llama-watchdog-fleet.service cluster/llama-watchdog-fleet.timer \
         "$TGT:/tmp/"

  ssh "$TGT" "sudo install -D -m 0755 -o root -g root /tmp/llama-watchdog-agent.sh /usr/local/sbin/llama-watchdog-agent && \
             sudo install -D -m 0755 -o root -g root /tmp/llama-watchdog.sh       /usr/local/sbin/llama-watchdog && \
             sudo install -D -m 0644 -o root -g root /tmp/nodes.env               /etc/llama-watchdog/nodes.env && \
             sudo install -D -m 0644 -o root -g root /tmp/llama-watchdog@.service       /etc/systemd/system/llama-watchdog@.service && \
             sudo install -D -m 0644 -o root -g root /tmp/llama-watchdog@.timer         /etc/systemd/system/llama-watchdog@.timer && \
             sudo install -D -m 0644 -o root -g root /tmp/llama-watchdog-fleet.service  /etc/systemd/system/llama-watchdog-fleet.service && \
             sudo install -D -m 0644 -o root -g root /tmp/llama-watchdog-fleet.timer    /etc/systemd/system/llama-watchdog-fleet.timer && \
             rm -f /tmp/llama-watchdog-agent.sh /tmp/llama-watchdog.sh /tmp/nodes.env \
                   /tmp/llama-watchdog@.service /tmp/llama-watchdog@.timer \
                   /tmp/llama-watchdog-fleet.service /tmp/llama-watchdog-fleet.timer"
  say "agent + controller + units installed"

  # The restricted account. A system account with a real (minimal) shell,
  # because sshd needs to exec the forced command through one. It owns nothing
  # but its own .ssh directory.
  ssh "$TGT" "id -u ${WD_USER} >/dev/null 2>&1 || \
             sudo useradd --system --create-home --home-dir '${WD_HOME}' \
                          --shell /bin/sh --comment 'llama-server liveness watchdog' '${WD_USER}'"
  # 0755 on the home, not 0700: it holds the watchdog's heartbeat drop box, and
  # an operator (or Missing Link) has to be able to read it to answer "when did
  # the monitor last look at this node?". Nothing secret lives there.
  ssh "$TGT" "sudo install -d -m 0755 -o ${WD_USER} -g ${WD_USER} '${WD_HOME}' && \
             sudo install -d -m 0700 -o ${WD_USER} -g ${WD_USER} '${WD_HOME}/.ssh'"

  # authorized_keys: replace only OUR line, leave anything else alone.
  ssh "$TGT" "sudo sh -c 'f=${WD_HOME}/.ssh/authorized_keys; touch \$f; \
             grep -v \"llama-watchdog (restricted\" \$f > \$f.new 2>/dev/null || true; \
             printf \"%s\\n\" \"restrict,command=\\\"/usr/local/sbin/llama-watchdog-agent\\\" ${WD_PUB}\" >> \$f.new; \
             mv \$f.new \$f; chown ${WD_USER}:${WD_USER} \$f; chmod 0600 \$f'"
  say "restricted key authorised (restrict + forced command)"

  # The single privileged grant. Validated before it is put in place -- a
  # malformed sudoers file locks everyone out of sudo on that node.
  # Three units, named individually. Not a blanket `systemctl` grant.
  # `missing-link` is restartable by the credential but gated by POLICY in the
  # controller (never while a job is running -- F36 was caused by exactly that).
  ssh "$TGT" "SC=\$(command -v systemctl); \
             MLU=\$(systemctl show -p User --value missing-link.service 2>/dev/null); \
             printf '%s ALL=(root) NOPASSWD: %s restart llama-server@*, %s restart rpc-server@*, %s restart missing-link\n' '${WD_USER}' \"\$SC\" \"\$SC\" \"\$SC\" > /tmp/llama-watchdog.sudoers && \
             if [ -n \"\$MLU\" ]; then \
               printf '%s ALL=(%s) NOPASSWD: /usr/local/sbin/llama-watchdog-agent mlstate-raw\n' '${WD_USER}' \"\$MLU\" >> /tmp/llama-watchdog.sudoers; \
             fi && \
             sudo install -m 0440 -o root -g root /tmp/llama-watchdog.sudoers /etc/sudoers.d/llama-watchdog && \
             rm -f /tmp/llama-watchdog.sudoers && \
             sudo visudo -c -f /etc/sudoers.d/llama-watchdog"
  say "sudoers grant installed and validated"

  # The in-band timer. Better than nothing and catches the wedge, but
  # docs/REQUIREMENTS.md 2026-08-17 is explicit that it is NOT sufficient on
  # its own -- it shares the host's failure modes.
  for P in $WD_PORTS; do
    ssh "$TGT" "sudo systemctl daemon-reload && sudo systemctl enable --now llama-watchdog@${P}.timer"
    say "in-band timer llama-watchdog@${P}.timer enabled"
  done

  # Prove the credential works end to end, from here, as the watchdog user.
  local v
  v=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$WD_KEY" -o IdentitiesOnly=yes \
        "${WD_USER}@${IP}" version 2>&1 || true)
  case "$v" in
    *wd_agent_proto=*) say "restricted key verified: $v" ;;
    *) echo "  FATAL: the restricted key does not work on $NAME: $v" >&2; return 1 ;;
  esac

  # F51: the rpc predicate now decides on PROGRESS, and its byte counters come
  # from `ss` (iproute2). Prove that dependency ON THE NODE, through the same
  # restricted credential the watchdog will actually use. Without `ss` the
  # predicate degrades to CPU-only, which is safe but weaker -- so this is a
  # loud warning, not a fatal, and it must never be silent. F43 is exactly the
  # failure of assuming an install worked fleet-wide.
  local g
  g=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$WD_KEY" -o IdentitiesOnly=yes \
        "${WD_USER}@${IP}" "rpcprogress ${RPC_PORT:-50052} 1" 2>&1 || true)
  case "$g" in
    *conns0=-1*) echo "  WARNING: no usable \`ss\` on $NAME -- the rpc progress probe (F51)" >&2
                 echo "           will fall back to CPU only. Install iproute2. ($g)" >&2 ;;
    *conns0=*)   say "rpc progress probe verified: $g" ;;
    *)           echo "  FATAL: rpcprogress failed on $NAME -- the agent is older than" >&2
                 echo "         proto 5, or the verb was refused: $g" >&2; return 1 ;;
  esac

  # And prove it CANNOT do anything else. A credential is only as good as what
  # it refuses.
  local r
  r=$(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$WD_KEY" -o IdentitiesOnly=yes \
        "${WD_USER}@${IP}" 'id -u; cat /etc/shadow' 2>&1 || true)
  case "$r" in
    *refused_verb*) say "confinement verified: arbitrary commands are refused ($r)" ;;
    *) echo "  FATAL: the restricted key was NOT confined on $NAME: $r" >&2; return 1 ;;
  esac
}

if [ "$DO_NODES" = 1 ]; then
  for entry in "${NODES[@]}"; do
    # shellcheck disable=SC2086
    set -- $entry
    NAME=$1; IP=$2
    if [ -n "$ONLY" ]; then
      case ",${ONLY}," in *",${NAME},"*|*",${IP},"*) ;; *) continue ;; esac
    fi
    # Admin login is per node (nodes.env 5th field). The WATCHDOG's own login
    # is not -- it is ${WD_USER}, created identically everywhere, and the
    # restricted-key checks below deliberately keep using it.
    install_node "$NAME" "$IP" "$(node_target "$entry")"
  done
fi

# ---------------------------------------------------------------------------
# 3. Watcher side -- this machine. On the out-of-band appliance this is the
#    only half that runs (`--watcher-only`), and it needs nothing but bash,
#    curl, ssh and the fleet list.
# ---------------------------------------------------------------------------
if [ "$DO_WATCHER" = 1 ]; then
  echo "== watcher ($(hostname))"
  sudo install -D -m 0755 -o root -g root cluster/llama-watchdog.sh       /usr/local/sbin/llama-watchdog
  sudo install -D -m 0755 -o root -g root cluster/llama-watchdog-agent.sh /usr/local/sbin/llama-watchdog-agent
  sudo install -D -m 0644 -o root -g root provisioning/nodes.env          /etc/llama-watchdog/nodes.env
  sudo install -D -m 0644 -o root -g root cluster/llama-watchdog@.service      /etc/systemd/system/llama-watchdog@.service
  sudo install -D -m 0644 -o root -g root cluster/llama-watchdog@.timer        /etc/systemd/system/llama-watchdog@.timer
  sudo install -D -m 0644 -o root -g root cluster/llama-watchdog-fleet.service /etc/systemd/system/llama-watchdog-fleet.service
  sudo install -D -m 0644 -o root -g root cluster/llama-watchdog-fleet.timer   /etc/systemd/system/llama-watchdog-fleet.timer
  say "controller, agent, fleet list and units installed"

  if [ ! -f /etc/default/llama-watchdog ]; then
    sudo install -D -m 0644 -o root -g root /dev/stdin /etc/default/llama-watchdog <<EOF
# Site configuration for the llama-server liveness watchdog.
# Created by cluster/install-watchdog.sh; edit freely, never overwritten.
WATCHDOG_SSH_USER=${WD_USER}
WATCHDOG_SSH_KEY=${WD_KEY}
# WATCHDOG_ONLY=10.10.0.34        # restrict the sweep while a node is being provisioned
# WATCHDOG_HEARTBEAT_URL=         # optional POST sink for the dead-man's switch
EOF
    say "wrote /etc/default/llama-watchdog"
  else
    say "/etc/default/llama-watchdog already present -- left alone"
  fi

  sudo systemctl daemon-reload
  cat <<EOF

  The fleet sweep is installed but NOT enabled, because enabling it against a
  node that has not had the agent installed produces a BLIND verdict every
  minute. Once every node in nodes.env has been through this script:

      sudo systemctl enable --now llama-watchdog-fleet.timer

  Check it first without acting:

      sudo /usr/local/sbin/llama-watchdog --report
EOF
fi

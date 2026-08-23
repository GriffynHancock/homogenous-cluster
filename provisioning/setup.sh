#!/usr/bin/env bash
# Idempotent node provisioning. Safe to re-run, and the intended way to repair
# a half-configured node. Must be run on EVERY node including the master.
#
# Usage: sudo ./setup.sh <hostname> [tailscale-auth-key-file]
#
# The Tailscale key is optional -- Tailscale is admin/web convenience, not a
# dependency of the cluster. RPC runs on raw LAN IPs regardless.
set -euo pipefail

NEW_HOSTNAME="${1:?usage: setup.sh <hostname> [tailscale-key-file]}"
TS_KEY_FILE="${2:-}"

if [ "$EUID" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi

log() { printf '\n==> %s\n' "$*"; }

log "Hostname"
hostnamectl set-hostname "$NEW_HOSTNAME"
if ! grep -q "^127.0.1.1[[:space:]]*$NEW_HOSTNAME\$" /etc/hosts; then
  sed -i "/^127.0.1.1/d" /etc/hosts
  echo "127.0.1.1 $NEW_HOSTNAME" >> /etc/hosts
fi

log "Timezone"
# Node 2 arrived on US/Eastern while node 1 was on Australia/Melbourne. The
# CLOCKS agreed (both NTP-synced, identical UTC) but journalctl on the two nodes
# read 14 hours apart, which makes cross-node log correlation actively
# misleading during a failure. Align to the coordinator's zone; override with
# CLUSTER_TZ if the fleet is elsewhere.
CLUSTER_TZ="${CLUSTER_TZ:-Australia/Melbourne}"
if [ "$(timedatectl show -p Timezone --value)" != "$CLUSTER_TZ" ]; then
  timedatectl set-timezone "$CLUSTER_TZ"
fi
echo "    $(timedatectl show -p Timezone --value), NTP $(timedatectl show -p NTPSynchronized --value)"

log "Identity hygiene"
# machine-id: systemd-networkd derives its DHCP client-ID from this. Duplicates
# across the fleet make nodes collide on a single lease, which presents as
# intermittent fleet-wide network flapping -- not as an obvious identity bug.
if [ ! -f /etc/machine-id.provisioned ]; then
  truncate -s 0 /etc/machine-id
  rm -f /var/lib/dbus/machine-id
  systemd-machine-id-setup
  ln -sf /etc/machine-id /var/lib/dbus/machine-id
  touch /etc/machine-id.provisioned
fi

# Duplicate SSH host keys would let any node impersonate any other.
if [ ! -f /etc/ssh/.hostkeys.provisioned ]; then
  rm -f /etc/ssh/ssh_host_*
  ssh-keygen -A
  systemctl restart ssh
  touch /etc/ssh/.hostkeys.provisioned
  # This INVALIDATES the coordinator's known_hosts entry for this node. Every
  # later script (distribute.sh, install-services.sh, two-node-smoke.sh) uses
  # plain ssh, so the next one to run aborts with "REMOTE HOST IDENTIFICATION
  # HAS CHANGED" -- which reads as an attack, not as a provisioning step.
  # Observed on node 2, 2026-08-17.
  cat <<'WARN'

    !! HOST KEYS REGENERATED. On the COORDINATOR, run:
    !!     ssh-keygen -R <this-node-ip>
    !!     ssh -o StrictHostKeyChecking=accept-new <user>@<this-node-ip> true
    !! Otherwise the next script fails with a host-key mismatch warning.

WARN
fi

log "Service account"
# The rpc-server unit declares User=cluster. Nothing created it until
# 2026-08-17, so install-services.sh would 'enable --now' a unit that dies
# instantly with status=217/USER -- and the failure is on the WORKER, i.e.
# found only after committing the hardware. A system account: no login shell,
# no password, separate from the admin/SSH account.
if ! id cluster >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/cluster \
          --shell /usr/sbin/nologin cluster
fi
# rpc-server -c writes tensors >=10 MiB here. Set it EXPLICITLY rather than
# letting it default to $HOME/.cache: on a large MoE this approaches the node's
# full layer share (F23), and a silent fill of / is the failure mode.
mkdir -p /var/lib/cluster/.cache/llama.cpp/rpc
chown -R cluster:cluster /var/lib/cluster
echo "    cluster uid $(id -u cluster), cache /var/lib/cluster/.cache/llama.cpp/rpc"
echo "    free on that filesystem: $(df -h --output=avail /var/lib/cluster | tail -1 | tr -d ' ')"

log "Packages"
apt-get update -qq
# openssh-server: the preseed installs it, but a HAND-INSTALLED node may not
# have it -- that is how node 2 arrived with no sshd. Assert it here so the
# path is the same however the node was built. (Chicken-and-egg noted: if you
# are running this over SSH it is obviously already up; this covers the local
# and re-run cases, and makes the service state explicit.)
# dmidecode is NOT in the Debian 12 base install and is required to read DIMM
# layout -- which is what actually determines generation speed (F12).
apt-get install -y -qq openssh-server curl rsync git jq python3 python3-venv \
                      ca-certificates dmidecode pciutils lm-sensors
systemctl enable --now ssh

log "Disable swap"
# Models are sized to fit RAM. Any overshoot into swap collapses throughput;
# failing loudly is better than degrading silently by two orders of magnitude.
swapoff -a || true
sed -i '/\sswap\s/s/^\([^#]\)/#\1/' /etc/fstab
for unit in $(systemctl list-units --type swap --no-legend --plain | awk '{print $1}'); do
  systemctl mask "$unit" || true
done

log "Memory tuning"
cat > /etc/sysctl.d/99-inference.conf <<'EOF'
# llama.cpp has hit overcommit-related OOM kills (ggml-org/llama.cpp#22629).
vm.overcommit_memory = 1
EOF
sysctl -q -p /etc/sysctl.d/99-inference.conf

if ! grep -q 'memlock unlimited' /etc/security/limits.conf; then
  cat >> /etc/security/limits.conf <<'EOF'
* soft memlock unlimited
* hard memlock unlimited
EOF
fi

# Transparent hugepages: Debian 12 already defaults to madvise, which
# benchmarks slightly faster than 'always' for llama.cpp. Assert, don't change.
THP=$(cat /sys/kernel/mm/transparent_hugepage/enabled)
echo "    THP: $THP (expected [madvise])"

log "Hardware facts -- record these in docs/measurements.md"
PHYS_CORES=$(lscpu -p=Core,Socket | grep -v '^#' | sort -u | wc -l)
echo "    physical cores : $PHYS_CORES   (nproc reports $(nproc))"
echo "    RAM MB         : $(free -m | awk '/^Mem:/{print $2}')"
echo "    NUMA nodes     : $(lscpu | awk -F: '/NUMA node\(s\)/{gsub(/ /,"",$2);print $2}')"
echo "    ISA            : $(grep -oE 'avx512[a-z_]*|avx2|fma|f16c' /proc/cpuinfo | sort -u | tr '\n' ' ')"
echo "    populated DIMM slots:"
# dmidecode prints Size BEFORE Locator within each Memory Device block, so the
# obvious one-liner (stash Locator, print on Size) reports the PREVIOUS block's
# label -- and "Bank Locator" matches /Locator:/ too, so filtering 'Bank'
# afterwards discarded all but one row. The result was a single line with an
# empty label: the exact fact F12 says decides generation speed, silently blank.
# Print once per block, keyed off Configured Memory Speed (the last field of
# interest), so locator, size and ACTUAL clocked speed line up.
dmidecode -t memory 2>/dev/null | awk '
  /^[[:space:]]*Size:/                    { size = $2 " " $3 }
  /^[[:space:]]*Locator:/ && !/Bank/      { loc  = $2 }
  /^[[:space:]]*Configured Memory Speed:/ {
      if (loc != "" && size !~ /No/) {
        printf "      %-10s %-8s @ %s %s\n", loc, size, $4, $5
        n++
      }
      loc = ""; size = ""
  }
  END { if (n == 0) print "      (no populated DIMMs parsed -- check dmidecode)" }
' || echo "      (dmidecode unavailable)"
cat <<EOF
    NOTE: memory CHANNELS, not capacity, set generation speed. Half-populated
    boards silently halve throughput. If the Locator labels show DIMMs in only
    some channels, rebalance them BEFORE racking this node. See FINDINGS F12.
EOF

if [ "$PHYS_CORES" != "$(nproc)" ]; then
  echo "    SMT is on. rpc-server -t must be $PHYS_CORES, NOT $(nproc) -- see F10."
fi

log "Tailscale"
if [ -n "$TS_KEY_FILE" ]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  # Clear cloned state before joining -- duplicate node keys race on one identity.
  if [ ! -f /var/lib/tailscale/.provisioned ]; then
    systemctl stop tailscaled || true
    rm -rf /var/lib/tailscale/tailscaled.state /var/cache/tailscale
    systemctl start tailscaled
    # NEVER pass --advertise-routes: it would pull RPC traffic onto WireGuard.
    # Encryption on the per-token hot path is pure loss.
    tailscale up --auth-key="file:$TS_KEY_FILE" --hostname="$NEW_HOSTNAME" \
                 --ssh --accept-dns=false
    touch /var/lib/tailscale/.provisioned
  fi
else
  echo "    skipped (no key file given) -- Tailscale is admin only, not required"
fi

log "Directories"
# BOTH engine prefixes, not just mainline. distribute.sh takes the prefix as its
# argument and does `ssh <node> "mkdir -p $SRC/bin"` as the admin user, which
# cannot create a new directory in /opt -- so shipping the fork to a fresh node
# died with "mkdir: cannot create directory '/opt/ik_llama.cpp': Permission
# denied". Found on node 3, 2026-08-23. Nodes 1 and 2 were unaffected only
# because their fork prefix had been created by hand.
mkdir -p /opt/llama.cpp/bin /opt/ik_llama.cpp/bin /opt/models
chown -R "${SUDO_USER:-root}" /opt/llama.cpp /opt/ik_llama.cpp /opt/models

log "Done: $NEW_HOSTNAME"

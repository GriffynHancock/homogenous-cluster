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
fi

log "Packages"
apt-get update -qq
# dmidecode is NOT in the Debian 12 base install and is required to read DIMM
# layout -- which is what actually determines generation speed (F12).
apt-get install -y -qq curl rsync git jq python3 python3-venv ca-certificates \
                      dmidecode pciutils lm-sensors

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
dmidecode -t memory 2>/dev/null \
  | awk '/Locator:/{loc=$0} /^\tSize:.*[0-9]+ *[MG]B/{print "      " loc " -> " $2 $3}' \
  | grep -v 'Bank' || echo "      (dmidecode unavailable)"
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
mkdir -p /opt/llama.cpp/bin /opt/models
chown -R "${SUDO_USER:-root}" /opt/llama.cpp /opt/models

log "Done: $NEW_HOSTNAME"

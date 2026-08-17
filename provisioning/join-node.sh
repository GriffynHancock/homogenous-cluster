#!/usr/bin/env bash
# Print the exact commands to run on a NEW node so the coordinator can reach it.
#
# This is generated rather than documented because the SSH key and the
# coordinator's IP are machine-specific. A key hand-copied into a README is
# wrong the moment anyone redeploys, and wrong in a way that fails confusingly
# ("Permission denied (publickey)") rather than obviously.
#
# Run this ON THE COORDINATOR, then paste the output into the new machine.
#
#   ./provisioning/join-node.sh
#
# Everything after this the coordinator can do over SSH:
#   sudo ./provisioning/setup.sh <hostname>
#   ./provisioning/distribute.sh
#   ./cluster/install-services.sh
set -euo pipefail

KEY_FILE="${KEY_FILE:-$HOME/.ssh/id_ed25519}"
USER_NAME="${USER_NAME:-$(id -un)}"

if [ ! -f "${KEY_FILE}.pub" ]; then
  echo "==> No key at ${KEY_FILE}.pub -- generating one" >&2
  ssh-keygen -t ed25519 -N '' -f "$KEY_FILE" -C "$(hostname)-cluster" >/dev/null
fi
PUBKEY=$(cat "${KEY_FILE}.pub")

# First non-loopback IPv4, excluding Tailscale -- RPC and admin both use the LAN.
LAN_IP=$(ip -4 -br addr show scope global \
         | grep -v tailscale | awk '{print $3}' | head -1)

cat <<EOF
# ============================================================================
# Run these on the NEW node. Coordinator is $(hostname) at ${LAN_IP%%/*}.
# ============================================================================

# 1. Same username as the coordinator.
#    Not strictly required, but the systemd unit, every scp/rsync target and
#    every ssh in these scripts assume it. A different name means editing all
#    of them.
sudo adduser ${USER_NAME}          # skip if it already exists

# 2. Passwordless sudo -- APPEND to /etc/sudoers, not a sudoers.d drop-in.
#    sudo is last-match-wins, and Debian's own "(ALL:ALL) ALL" line can come
#    after /etc/sudoers.d, silently overriding a NOPASSWD rule placed there.
#    Symptom: 'sudo -l' shows NOPASSWD but sudo still prompts.
echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

# 3. SSH server.
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh

# 4. Authorise the coordinator's key.
sudo -u ${USER_NAME} mkdir -p /home/${USER_NAME}/.ssh
sudo -u ${USER_NAME} tee -a /home/${USER_NAME}/.ssh/authorized_keys <<'KEY'
${PUBKEY}
KEY
sudo chmod 700 /home/${USER_NAME}/.ssh
sudo chmod 600 /home/${USER_NAME}/.ssh/authorized_keys

# 5. Report the LAN IP back to the coordinator.
ip -br addr | grep -v LOOPBACK

# ============================================================================
# Then, back on the coordinator:
#
#   ssh <new-ip> 'lscpu -p=Core,Socket | grep -v "^#" | sort -u | wc -l; free -m'
#       ^ characterise it FIRST. Do not assume it matches. Core count is a
#         bandwidth spec, not just a compute spec.
#
#   Add it to provisioning/nodes.env with MEASURED values, then:
#     sudo ./provisioning/setup.sh <hostname>
#     ./provisioning/distribute.sh          # asserts version, libc AND ISA
#     ./cluster/install-services.sh
#     ./bench/two-node-smoke.sh <new-ip>    # gate for upstream bug #26500
# ============================================================================
EOF

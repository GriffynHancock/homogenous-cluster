#!/usr/bin/env bash
# Switch a node from password SSH to key-only, AFTER keys are known to work.
#
# Run this FROM THE COORDINATOR, once per node, after join-node.sh has installed
# the coordinator's public key:
#
#   ./provisioning/harden-ssh.sh <node-ip>
#
# THE SAFETY PROPERTY THAT MATTERS: this verifies key authentication actually
# works *from this machine* before it disables password authentication. Doing it
# the other way round is how people lock themselves out of a headless box that
# lives in a locked cupboard. If the key check fails, nothing is changed.
#
# SCOPE. This hardens the cluster's own administrative access path. It is NOT
# network security advice, and it does not make the network safe -- see the
# security posture in CLAUDE.md. Securing and validating the network the cluster
# sits on remains the organisation's responsibility.
set -euo pipefail

NODE="${1:?usage: harden-ssh.sh <node-ip-or-host>}"
SSH_USER="${SSH_USER:-$(id -un)}"
SSHD=/etc/ssh/sshd_config.d/99-cluster-hardening.conf

echo "==> Verifying KEY auth to $NODE before changing anything"
# PasswordAuthentication=no here forces the check to use the key only, so a
# working password cannot mask a broken key.
if ! ssh -o BatchMode=yes \
         -o PasswordAuthentication=no \
         -o PubkeyAuthentication=yes \
         -o ConnectTimeout=10 \
         -o StrictHostKeyChecking=accept-new \
         "$SSH_USER@$NODE" true 2>/dev/null; then
  cat >&2 <<EOF
FATAL: key-based SSH to $SSH_USER@$NODE does not work yet.

Refusing to disable password authentication -- doing so now would lock you out.

Fix first:
  ./provisioning/join-node.sh        # prints the key to install on the node
  ssh-copy-id $SSH_USER@$NODE        # or install it this way
Then re-run this script.
EOF
  exit 1
fi
echo "    key auth OK"

echo "==> Applying hardening to $NODE"
# A drop-in rather than editing sshd_config: idempotent, and trivially
# reversible by deleting one file. Debian 12's sshd_config includes
# /etc/ssh/sshd_config.d/*.conf near the TOP, and for sshd the FIRST value of a
# keyword wins -- the opposite of sudoers. So a drop-in reliably overrides the
# defaults further down the main file.
ssh "$SSH_USER@$NODE" "sudo tee $SSHD >/dev/null" <<'EOF'
# Cluster SSH hardening. Applied by provisioning/harden-ssh.sh once key auth
# was confirmed working. Delete this file and restart ssh to revert.
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
PermitEmptyPasswords no
# Administrative access only; the cluster needs no forwarding of any kind.
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
MaxAuthTries 3
# Drop dead sessions rather than leaving them pinned on a long-running node.
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

echo "==> Validating sshd config BEFORE restarting"
# sshd -t catches a syntax error while the old daemon is still running. Skipping
# this is the other way to lock yourself out.
if ! ssh "$SSH_USER@$NODE" "sudo sshd -t"; then
  echo "FATAL: sshd config invalid. Removing the drop-in and leaving ssh untouched." >&2
  ssh "$SSH_USER@$NODE" "sudo rm -f $SSHD"
  exit 1
fi

ssh "$SSH_USER@$NODE" "sudo systemctl restart ssh"
sleep 2

echo "==> Re-verifying key auth AFTER restart"
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_USER@$NODE" true 2>/dev/null; then
  echo "    still reachable by key"
else
  cat >&2 <<EOF
WARNING: cannot reach $NODE by key after the restart.

Do NOT close any existing session to that host. From a session that still works:
  sudo rm -f $SSHD && sudo systemctl restart ssh
EOF
  exit 1
fi

echo "==> Confirming passwords are actually refused"
if ssh -o BatchMode=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password \
       -o ConnectTimeout=10 "$SSH_USER@$NODE" true 2>/dev/null; then
  echo "    WARNING: password auth still appears to be accepted -- check $SSHD" >&2
else
  echo "    password auth refused"
fi

echo
echo "$NODE is now key-only. Revert with:"
echo "  ssh $SSH_USER@$NODE 'sudo rm -f $SSHD && sudo systemctl restart ssh'"

#!/usr/bin/env bash
# Bootstrap a fresh Debian 12 node to the point where Claude Code can drive the
# rest of the build. Idempotent -- safe to re-run, and the intended way to
# repair a half-configured node.
#
# This deliberately does NOT do node provisioning (identity, swap, Tailscale) --
# that is provisioning/setup.sh, created in Task 4 of the plan.
set -euo pipefail

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if [ "$EUID" -eq 0 ]; then
  echo "Do not run this as root -- it installs a user-level Node toolchain." >&2
  echo "Run as your normal user; it will sudo where needed." >&2
  exit 1
fi

log "System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  build-essential cmake git curl rsync jq \
  python3 python3-venv python3-pip \
  ca-certificates dmidecode pciutils lsb-release

log "Hardware facts"
# These drive every downstream decision. Bandwidth determines tokens/sec more
# than anything else, and rpc-server's thread default is half the core count --
# so both must come from measurement, never assumption.
echo "--- CPU"
lscpu | grep -E 'Model name|^CPU\(s\)|Socket|Core\(s\)|Thread\(s\)|NUMA node\(s\)' || true
echo "--- Instruction sets"
grep -oE 'avx2|avx512[a-z_]*' /proc/cpuinfo | sort -u | tr '\n' ' ' || true
echo
echo "--- Memory"
free -h | head -2
sudo dmidecode -t memory 2>/dev/null \
  | grep -E '^\s+(Size|Speed|Locator):' \
  | grep -v 'No Module Installed' | head -40 || true
echo "--- Disks"
lsblk -d -o NAME,SIZE,ROTA,MODEL

log "Node.js"
if ! command -v node >/dev/null 2>&1 || \
   [ "$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')" -lt 18 ] 2>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
echo "node $(node -v), npm $(npm -v)"

log "Claude Code"
if ! command -v claude >/dev/null 2>&1; then
  # User-level prefix so npm -g does not need sudo.
  mkdir -p "$HOME/.npm-global"
  npm config set prefix "$HOME/.npm-global"
  if ! grep -q '.npm-global/bin' "$HOME/.profile" 2>/dev/null; then
    echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.profile"
  fi
  export PATH="$HOME/.npm-global/bin:$PATH"
  npm install -g @anthropic-ai/claude-code
fi
echo "claude $(claude --version 2>/dev/null || echo '(restart your shell)')"

log "Working directories"
sudo mkdir -p /opt/models /opt/llama.cpp/bin /opt/missing-link
sudo chown -R "$USER" /opt/models /opt/llama.cpp /opt/missing-link

cat <<'EOF'

==> Ready.

Next:
  1. If 'claude' is not found, run:  source ~/.profile
  2. Start Claude Code:              claude
  3. Tell it:                        read STATUS.md and continue the plan

The hardware facts printed above are needed for Task 1 -- they are what the
model choice and thread counts are computed from. Claude Code will re-run and
record them properly in docs/measurements.md.
EOF

# Homogenous Cluster Bring-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up 7 surplus desktops as a CPU-only llama.cpp RPC cluster serving Qwen3-30B-A3B Q8_0, fronted by Open WebUI and Missing Link.

**Architecture:** Debian 12 headless on each node, provisioned by preseed + idempotent `setup.sh`. llama.cpp built once and distributed fleet-wide. One `rpc-server` per node holding a layer range in system RAM; `llama-server` on the master assigns layers via explicit `--tensor-split` and serves an OpenAI-compatible API. RPC mesh on raw LAN IPs; Tailscale for admin SSH and web only.

**Tech Stack:** Debian 12, llama.cpp (pinned build), Tailscale, Open WebUI (Docker), Python 3.11 + FastAPI + SQLite (Missing Link), pytest.

## Global Constraints

- **llama.cpp version must match byte-for-byte across all nodes.** Build once, distribute binaries. Never build per-node. Version mismatch is rejected at handshake with `"RPC server version mismatch"`.
- **Pin the llama.cpp build to a specific tag.** Do not track `master`. `--tensor-split` over RPC has regressed before (#21006).
- **Two independent memory constraints, both must hold:**
  - Pooled: `(pooled RAM − 1 GB/node) × 0.85` — ~756 GB at 128 GB/node.
  - **Per node: ≤75% of physical RAM** (llama.cpp #15055, unfixed; exceeding it aborts with `"Remote RPC server crashed or returned malformed response"`). At 128 GB/node this is 96 GB/node, **~672 GB pooled — the binding constraint.**
- **Never pass `--advertise-routes`** on any node. It would pull RPC onto WireGuard.
- **`rpc-server -t` defaults to half the cores.** Always set `-t` explicitly to the real core count.
- **Always run `rpc-server` with `-c`** (local tensor cache).
- **Model target is not yet fixed** — it depends on measurements from Task 1. See "Model selection" below.
- Node 1 is the master. Nodes 2–7 are workers.
- Record every measurement in `docs/measurements.md`. Never quote a performance number that is not in that file.
- Do not patch `HASH_THRESHOLD`. See spec.
- Default context: **32768**. With 128 GB/node, KV cache is not the constraint.

## Model selection

Hardware revised 2026-08-10: **128 GB DDR4-2400 ECC per node**, ~896 GB pooled.
This changes what the cluster is for. A single node holds 96 GB — so anything
smaller than that does not need a cluster at all.

Two models are built, and the pair is the deliverable:

| | Model | Size | Nodes | Purpose |
|---|---|---|---|---|
| **A** | gpt-oss-120b MXFP4 | 63 GB | **1** | Single-node baseline and speed reference |
| **B** | Kimi K2 Q4 (Unsloth) | ~550 GB | **7** | The thesis: a model no single machine could hold |

Model A is fetched in Task 3 and Model B in Task 7. Confirm exact Kimi K2 quant
filenames and sizes at fetch time — pick the largest Unsloth quant that leaves
every node at ≤75% of 128 GB (i.e. total ≤672 GB), keeping 15% pooled headroom.

If Task 1 reveals that nodes do **not** all have 128 GB, recompute both
constraints before fetching Model B and raise it with the user.

---

## File Structure

| Path | Responsibility |
|---|---|
| `provisioning/preseed.cfg` | Unattended Debian 12 install, disk-agnostic |
| `provisioning/setup.sh` | Idempotent node config: identity, tuning, Tailscale, deps |
| `provisioning/build-llama.sh` | Build pinned llama.cpp on the master |
| `provisioning/distribute.sh` | rsync binaries + verify version match fleet-wide |
| `provisioning/nodes.env` | Fleet inventory: hostnames, LAN IPs, RAM |
| `cluster/rpc-server@.service` | systemd unit for worker `rpc-server` |
| `cluster/start-cluster.sh` | Launch `llama-server` with computed `--tensor-split` |
| `bench/overhead-test.sh` | Localhost RPC overhead isolation test |
| `bench/node-bench.sh` | Single-node `llama-bench` + TTFT capture |
| `docs/measurements.md` | Every measured number, with date and hardware |
| `missing-link/missing_link/app.py` | FastAPI routes + web views |
| `missing-link/missing_link/db.py` | SQLite schema and job persistence |
| `missing-link/missing_link/worker.py` | Sequential job executor against llama-server |
| `missing-link/missing_link/templates/index.html` | Job submission + status page |
| `missing-link/tests/` | pytest suite |

---

# Phase 0 — Measurement gate

Nothing in this phase requires the fleet. It exists to settle the one question that could change the architecture, before seven machines are committed to it.

### Task 1: Provision node 1 base OS and build llama.cpp

**Files:**
- Create: `provisioning/preseed.cfg`
- Create: `provisioning/build-llama.sh`
- Create: `docs/measurements.md`

**Interfaces:**
- Produces: a working Debian 12 node with `llama-cli`, `llama-server`, `llama-bench`, `rpc-server` at `/opt/llama.cpp/bin/`.

- [ ] **Step 1: Write the preseed**

Create `provisioning/preseed.cfg`:

```
d-i debian-installer/locale string en_AU
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string unassigned-hostname
d-i netcfg/get_domain string local

d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

d-i passwd/root-login boolean false
d-i passwd/user-fullname string cluster
d-i passwd/username string cluster
d-i passwd/user-password password changeme
d-i passwd/user-password-again password changeme
d-i user-setup/allow-password-weak boolean true

d-i clock-setup/utc boolean true
d-i time/zone string Australia/Sydney
d-i clock-setup/ntp boolean true

# Disk-agnostic: do NOT set partman-auto/disk. Pick the first disk dynamically.
d-i partman/early_command \
    string debconf-set partman-auto/disk "$(list-devices disk | head -n1)"
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

# Required on Bookworm or recycled NICs may have no firmware.
d-i apt-setup/non-free-firmware boolean true
d-i apt-setup/non-free boolean true
d-i apt-setup/contrib boolean true

tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string openssh-server sudo curl rsync git build-essential cmake libcurl4-openssl-dev
d-i pkgsel/upgrade select full-upgrade
popularity-contest popularity-contest/participate boolean false

d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default
d-i finish-install/reboot_in_progress note
```

- [ ] **Step 2: Install node 1 and verify hardware facts**

Boot the installer with the preseed. Then on node 1 run and record the output:

```bash
lscpu | grep -E 'Model name|^CPU\(s\)|Socket|Thread|Core|NUMA'
grep -oE 'avx2|avx512[a-z]*' /proc/cpuinfo | sort -u
free -h
sudo dmidecode -t memory | grep -E 'Size|Speed|Locator|Rank' | head -60
sudo dmidecode -t processor | grep -E 'Version|Core Count|Thread Count'
lsblk -d -o NAME,SIZE,ROTA
```

**Everything downstream depends on these numbers.** Specifically:
- **Core count** sets `-t` on `rpc-server` (Task 6). The default is half the
  cores, so this must be a real measured number, not an assumption.
- **Memory channels** (infer from DIMM `Locator` labels, e.g. `CPU1_DIMM_A1`)
  determine bandwidth, which determines tokens/sec more than anything else.
  Quad-channel DDR4-2400 ≈ 76.8 GB/s; hex-channel ≈ 115 GB/s.
- **AVX-512 presence** materially affects prefill, which is compute-bound.
- **Total RAM** — confirm 128 GB. If any node differs, both memory constraints
  need recomputing before a model is chosen.
- **Socket count / NUMA nodes** — a dual-socket board needs NUMA-aware thread
  pinning, which single-socket does not. If `NUMA node(s)` is greater than 1,
  flag it: `--numa distribute` may be needed and is currently untested here.

- [ ] **Step 3: Record the hardware facts**

Create `docs/measurements.md`:

```markdown
# Measurements

Every performance number cited anywhere must appear here first, with the date
and the hardware it was measured on. Arithmetic estimates do not belong in
this file.

## Hardware baseline

**Date:** <YYYY-MM-DD>
**Node:** node1

| Fact | Value |
|---|---|
| CPU model | <from lscpu> |
| Sockets / cores / threads | <from lscpu> |
| NUMA nodes | <from lscpu> |
| AVX2 / AVX-512 | <from /proc/cpuinfo> |
| RAM total | <from free -h> |
| DIMM size / speed / count | <from dmidecode> |
| Inferred memory channels | <from DIMM locator labels> |
| Theoretical bandwidth (GB/s) | <channels × 2400 MT/s × 8 bytes> |
| Disk (size, rotational) | <from lsblk> |
```

- [ ] **Step 4: Write the build script**

Create `provisioning/build-llama.sh`:

```bash
#!/usr/bin/env bash
# Build llama.cpp once on the master. Never run this on a worker --
# binaries are distributed by distribute.sh so all nodes match exactly.
set -euo pipefail

LLAMA_TAG="${LLAMA_TAG:?set LLAMA_TAG to a pinned release tag, e.g. b9999}"
SRC=/opt/llama.cpp-src
DEST=/opt/llama.cpp

sudo mkdir -p "$SRC" "$DEST"
sudo chown "$USER" "$SRC" "$DEST"

if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/ggml-org/llama.cpp "$SRC"
fi

cd "$SRC"
git fetch --tags
git checkout "$LLAMA_TAG"

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_RPC=ON \
  -DGGML_NATIVE=ON \
  -DLLAMA_CURL=OFF

cmake --build build --config Release -j"$(nproc)"

mkdir -p "$DEST/bin"
cp build/bin/llama-cli build/bin/llama-server build/bin/llama-bench \
   build/bin/rpc-server "$DEST/bin/"

echo "$LLAMA_TAG" > "$DEST/VERSION"
"$DEST/bin/llama-cli" --version
echo "Built $LLAMA_TAG -> $DEST/bin"
```

- [ ] **Step 5: Build and verify**

```bash
chmod +x provisioning/build-llama.sh
LLAMA_TAG=<chosen-tag> ./provisioning/build-llama.sh
/opt/llama.cpp/bin/rpc-server --help | grep -E '^\s+-c|^\s+-t|--cache|--threads'
```

Expected: `-c/--cache` and `-t/--threads` both present. If `-c` is absent, the pinned tag is too old — pick a newer tag and rebuild.

- [ ] **Step 6: Record the runtime dependencies**

```bash
ldd /opt/llama.cpp/bin/rpc-server
apt-cache policy libc6 | head -2
```

Append both outputs to `docs/measurements.md` under a `## Build` heading. `distribute.sh` asserts the libc version later.

- [ ] **Step 7: Commit**

```bash
git add provisioning/preseed.cfg provisioning/build-llama.sh docs/measurements.md
git commit -m "feat: preseed and pinned llama.cpp build script"
```

---

### Task 2: Measure RPC protocol overhead on localhost

This is the gate. It settles whether RPC's overhead is acceptable, with the network removed entirely, on one machine. **Do not provision the fleet before this passes.**

**Files:**
- Create: `bench/overhead-test.sh`
- Modify: `docs/measurements.md`

**Interfaces:**
- Consumes: `/opt/llama.cpp/bin/` from Task 1.
- Produces: a measured local-vs-RPC overhead percentage in `docs/measurements.md`.

- [ ] **Step 1: Fetch a small test model**

```bash
sudo mkdir -p /opt/models && sudo chown "$USER" /opt/models
curl -L -o /opt/models/qwen3-4b-q4km.gguf \
  https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf
ls -lh /opt/models/qwen3-4b-q4km.gguf
```

Expected: roughly 2.5 GB.

- [ ] **Step 2: Write the overhead test**

Create `bench/overhead-test.sh`:

```bash
#!/usr/bin/env bash
# Isolate llama.cpp RPC protocol overhead with the network removed.
# Runs llama-bench locally, then again through an rpc-server on localhost.
# The delta is pure protocol cost.
set -euo pipefail

BIN=/opt/llama.cpp/bin
MODEL="${MODEL:-/opt/models/qwen3-4b-q4km.gguf}"
THREADS="${THREADS:-$(nproc)}"
PORT=50052

echo "=== Baseline: local, no RPC ==="
"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512 -n 128 -r 3

echo
echo "=== Through RPC on localhost ==="
"$BIN/rpc-server" -H 127.0.0.1 -p "$PORT" -t "$THREADS" -c &
RPC_PID=$!
trap 'kill $RPC_PID 2>/dev/null || true' EXIT
sleep 3

"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512 -n 128 -r 3 \
  --rpc 127.0.0.1:"$PORT"

kill $RPC_PID 2>/dev/null || true
echo
echo "Compare pp512 (prefill) and tg128 (generation) rows between the two runs."
```

- [ ] **Step 3: Run it**

```bash
chmod +x bench/overhead-test.sh
./bench/overhead-test.sh 2>&1 | tee /tmp/overhead.txt
```

Expected: two `llama-bench` tables. Each has a `pp512` row (prefill, t/s) and a `tg128` row (generation, t/s).

- [ ] **Step 4: Record the result and decide**

Append to `docs/measurements.md`:

```markdown
## RPC protocol overhead (localhost isolation test)

**Date:** <YYYY-MM-DD>
**Node:** node1 | **Model:** Qwen3-4B Q4_K_M | **Threads:** 4

| Metric | Local | Via RPC (localhost) | Overhead |
|---|---|---|---|
| pp512 (prefill, t/s) | | | % |
| tg128 (generation, t/s) | | | % |

**Verdict:** <proceed / escalate>
```

**Decision rule:**
- Generation overhead **under 15%** → proceed to Phase 1 as planned.
- Generation overhead **15–30%** → proceed, but note it as a known cost and re-test when PR #18626 lands.
- Generation overhead **over 30%** → **stop and escalate to the user.** The architecture may need reconsidering; do not silently continue.

- [ ] **Step 5: Commit**

```bash
git add bench/overhead-test.sh docs/measurements.md
git commit -m "test: measure RPC protocol overhead on localhost"
```

---

### Task 3: Single-node inference baseline

**Files:**
- Create: `bench/node-bench.sh`
- Modify: `docs/measurements.md`

**Interfaces:**
- Consumes: `/opt/llama.cpp/bin/`, `/opt/models/qwen3-4b-q4km.gguf`.
- Produces: measured single-node tok/s and TTFT — the baseline every cluster number is compared against.

- [ ] **Step 1: Verify coherent output from one node**

```bash
/opt/llama.cpp/bin/llama-cli -m /opt/models/qwen3-4b-q4km.gguf -t 4 \
  -p "Explain in three sentences why a school might not want to send student records to a cloud AI service." \
  -n 200 --no-warmup
```

Expected: coherent, on-topic English. If output is repetitive or garbled, stop — the build or model file is wrong.

- [ ] **Step 2: Write the benchmark script**

Create `bench/node-bench.sh`:

```bash
#!/usr/bin/env bash
# Single-node baseline: throughput via llama-bench, and time-to-first-token
# measured separately against llama-server. TTFT matters more than tok/s for
# document workloads, and llama-bench does not report it.
set -euo pipefail

BIN=/opt/llama.cpp/bin
MODEL="${MODEL:-/opt/models/qwen3-4b-q4km.gguf}"
THREADS="${THREADS:-$(nproc)}"
PORT=8080

echo "=== Throughput: prefill and generation ==="
"$BIN/llama-bench" -m "$MODEL" -t "$THREADS" -p 512,2048 -n 128 -r 3

echo
echo "=== Time to first token ==="
"$BIN/llama-server" -m "$MODEL" -t "$THREADS" --port "$PORT" --host 127.0.0.1 &
SRV_PID=$!
trap 'kill $SRV_PID 2>/dev/null || true' EXIT

until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do sleep 2; done

# ~2000 tokens of input, representative of a document workload.
PROMPT=$(python3 -c "print('The quick brown fox jumps over the lazy dog. ' * 220)")

for i in 1 2 3; do
  curl -s -o /dev/null -w "run $i: TTFT %{time_starttransfer}s  total %{time_total}s\n" \
    -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json,sys
print(json.dumps({
  'messages':[{'role':'user','content':'''Summarise this: $PROMPT'''}],
  'max_tokens':64,'stream':True
}))")"
done

kill $SRV_PID 2>/dev/null || true
```

- [ ] **Step 3: Run and record**

```bash
chmod +x bench/node-bench.sh
./bench/node-bench.sh 2>&1 | tee /tmp/node-bench.txt
```

Append to `docs/measurements.md`:

```markdown
## Single-node baseline

**Date:** <YYYY-MM-DD>
**Node:** node1 | **Model:** Qwen3-4B Q4_K_M | **Threads:** 4

| Metric | Value |
|---|---|
| pp512 (t/s) | |
| pp2048 (t/s) | |
| tg128 (t/s) | |
| TTFT @ ~2000 tok prompt (s) | |
```

- [ ] **Step 4: Fetch and benchmark Model A — gpt-oss-120b on ONE node**

With 128 GB per node, a single machine holds 96 GB of model. gpt-oss-120b
(63 GB) fits comfortably. **This is half the blog's headline comparison** — what
one salvaged desktop does on its own, with no cluster and no RPC overhead.

```bash
pip install -q huggingface_hub
huggingface-cli download unsloth/gpt-oss-120b-GGUF \
  --include "*Q4_K_M*" --local-dir /opt/models/gpt-oss-120b
du -sh /opt/models/gpt-oss-120b
```

If that repo or quant 404s, list what exists:
`curl -s "https://huggingface.co/api/models?search=gpt-oss-120b-GGUF" | jq -r '.[].id'`

Then benchmark it, single node, no RPC:

```bash
MODEL=$(ls /opt/models/gpt-oss-120b/*00001*.gguf 2>/dev/null \
        || ls /opt/models/gpt-oss-120b/*.gguf | head -1)
/opt/llama.cpp/bin/llama-bench -m "$MODEL" -t "$(nproc)" -p 512,2048 -n 128 -r 3
MODEL="$MODEL" ./bench/node-bench.sh
```

- [ ] **Step 5: Record and derive effective bandwidth**

Effective bandwidth is the single number that recalculates every model estimate
in the spec. Derive it from the measured generation rate:

```bash
python3 -c "
tg  = float(input('measured tg128 tok/s: '))
act = 5.1e9      # gpt-oss-120b active params
bpw = 4.25/8     # MXFP4 bytes per weight
gb  = act*bpw/1e9
print(f'~{gb:.2f} GB read per token')
print(f'effective bandwidth ~= {tg*gb:.1f} GB/s')"
```

Append to `docs/measurements.md`:

```markdown
## Model A: gpt-oss-120b, single node

**Date:** <YYYY-MM-DD> | **Node:** node1 | **Threads:** <nproc>

| Metric | Value |
|---|---|
| pp512 / pp2048 (t/s) | |
| tg128 (t/s) | |
| TTFT @ ~2000 tok (s) | |
| **Derived effective bandwidth (GB/s)** | |
| Theoretical bandwidth (GB/s) | |
| Efficiency (% of theoretical) | |
```

**Recompute the spec's model ladder using the measured bandwidth** and update
the estimates there. If efficiency is far below ~50%, investigate thread count
and NUMA before proceeding.

- [ ] **Step 6: Sanity-check prefill against the GPU revisit condition**

The spec says GPUs come back only if TTFT is unbearable. If TTFT at ~2000 tokens
exceeds **90 seconds**, note it prominently and raise it with the user.

- [ ] **Step 7: Commit**

```bash
git add bench/node-bench.sh docs/measurements.md
git commit -m "test: single-node baseline and gpt-oss-120b measurements"
```

---

# Phase 1 — Fleet provisioning

### Task 4: Write the idempotent node setup script

**Files:**
- Create: `provisioning/setup.sh`
- Create: `provisioning/nodes.env`

**Interfaces:**
- Produces: `setup.sh` — safe to re-run any number of times on any node.

- [ ] **Step 1: Write the fleet inventory**

Create `provisioning/nodes.env`:

```bash
# Fleet inventory. LAN IPs are used for the RPC mesh; Tailscale is admin only.
# RAM_MB is the measured physical RAM, used to compute --tensor-split.
# Node 1 is the master and also runs a worker rpc-server.
NODES=(
  "node1 192.168.1.101 8192"
  "node2 192.168.1.102 8192"
  "node3 192.168.1.103 8192"
  "node4 192.168.1.104 8192"
  "node5 192.168.1.105 8192"
  "node6 192.168.1.106 8192"
  "node7 192.168.1.107 8192"
)
RPC_PORT=50052
MASTER_IP=192.168.1.101
```

- [ ] **Step 2: Write setup.sh**

Create `provisioning/setup.sh`:

```bash
#!/usr/bin/env bash
# Idempotent node provisioning. Safe to re-run. Must be run on every node
# including the master.
#
# Usage: sudo ./setup.sh <hostname> <tailscale-auth-key-file>
set -euo pipefail

NEW_HOSTNAME="${1:?usage: setup.sh <hostname> <tailscale-key-file>}"
TS_KEY_FILE="${2:?usage: setup.sh <hostname> <tailscale-key-file>}"

if [ "$EUID" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi

echo "==> Hostname"
hostnamectl set-hostname "$NEW_HOSTNAME"
if ! grep -q "127.0.1.1.*$NEW_HOSTNAME" /etc/hosts; then
  sed -i "/^127.0.1.1/d" /etc/hosts
  echo "127.0.1.1 $NEW_HOSTNAME" >> /etc/hosts
fi

echo "==> Identity hygiene"
# machine-id: systemd-networkd derives its DHCP client-ID from this. Duplicates
# across the fleet make nodes collide on a single lease, which presents as
# intermittent network flapping.
if [ ! -f /etc/machine-id.provisioned ]; then
  truncate -s 0 /etc/machine-id
  rm -f /var/lib/dbus/machine-id
  systemd-machine-id-setup
  ln -sf /etc/machine-id /var/lib/dbus/machine-id
  touch /etc/machine-id.provisioned
fi

# Duplicate SSH host keys let any node impersonate any other.
if [ ! -f /etc/ssh/.hostkeys.provisioned ]; then
  rm -f /etc/ssh/ssh_host_*
  ssh-keygen -A
  systemctl restart ssh
  touch /etc/ssh/.hostkeys.provisioned
fi

echo "==> Packages"
apt-get update -qq
apt-get install -y -qq curl rsync git jq python3 python3-venv ca-certificates

echo "==> Disable swap"
# Models are sized to fit RAM. Any overshoot into swap collapses throughput;
# failing loudly is better than degrading silently.
swapoff -a || true
sed -i '/\sswap\s/s/^\([^#]\)/#\1/' /etc/fstab
for unit in $(systemctl list-units --type swap --no-legend --plain | awk '{print $1}'); do
  systemctl mask "$unit" || true
done

echo "==> Memory tuning"
cat > /etc/sysctl.d/99-inference.conf <<'EOF'
# llama.cpp has hit overcommit-related OOM kills (ggml-org/llama.cpp#22629).
vm.overcommit_memory = 1
EOF
sysctl -p /etc/sysctl.d/99-inference.conf

if ! grep -q 'memlock unlimited' /etc/security/limits.conf; then
  cat >> /etc/security/limits.conf <<'EOF'
* soft memlock unlimited
* hard memlock unlimited
EOF
fi

# Transparent hugepages: Debian 12 already defaults to madvise, which
# benchmarks slightly faster than 'always' for llama.cpp. Assert, don't change.
THP=$(cat /sys/kernel/mm/transparent_hugepage/enabled)
echo "    THP setting: $THP (expected [madvise])"

echo "==> Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
# Clear cloned state before joining -- duplicate node keys race on one identity.
if [ ! -f /var/lib/tailscale/.provisioned ]; then
  systemctl stop tailscaled || true
  rm -rf /var/lib/tailscale/tailscaled.state /var/cache/tailscale
  systemctl start tailscaled
  # NEVER pass --advertise-routes: it would pull RPC traffic onto WireGuard.
  tailscale up --auth-key="file:$TS_KEY_FILE" --hostname="$NEW_HOSTNAME" \
               --ssh --accept-dns=false
  touch /var/lib/tailscale/.provisioned
fi

echo "==> Directories"
mkdir -p /opt/llama.cpp/bin /opt/models
chown -R "${SUDO_USER:-root}" /opt/llama.cpp /opt/models

echo "==> Done: $NEW_HOSTNAME"
```

- [ ] **Step 3: Verify idempotency on node 1**

```bash
chmod +x provisioning/setup.sh
sudo ./provisioning/setup.sh node1 /path/to/tskey
sudo ./provisioning/setup.sh node1 /path/to/tskey   # second run must be clean
```

Expected: the second run completes with no errors and does not regenerate machine-id, SSH keys, or Tailscale state.

- [ ] **Step 4: Verify the tuning took effect**

```bash
swapon --show          # expect: empty
sysctl vm.overcommit_memory   # expect: 1
tailscale status | head -3
ip route | grep -v tailscale0 | grep 192.168   # LAN route must still exist
```

- [ ] **Step 5: Commit**

```bash
git add provisioning/setup.sh provisioning/nodes.env
git commit -m "feat: idempotent node provisioning script"
```

---

### Task 5: Provision nodes 2–7 and distribute binaries

**Files:**
- Create: `provisioning/distribute.sh`
- Modify: `docs/measurements.md`

**Interfaces:**
- Consumes: `nodes.env`, `setup.sh`, `/opt/llama.cpp/bin/` on master.
- Produces: all 7 nodes running identical binaries, version-verified.

- [ ] **Step 1: Install and provision nodes 2–7**

For each node: install from the preseed USB, then from the master:

```bash
source provisioning/nodes.env
for entry in "${NODES[@]:1}"; do
  set -- $entry
  scp provisioning/setup.sh /path/to/tskey "$2:/tmp/"
  ssh "$2" "sudo /tmp/setup.sh $1 /tmp/tskey"
done
```

- [ ] **Step 2: Record actual RAM per node and update the inventory**

```bash
source provisioning/nodes.env
for entry in "${NODES[@]}"; do
  set -- $entry
  echo -n "$1 "
  ssh "$2" "free -m | awk '/^Mem:/{print \$2}'"
done
```

Update `RAM_MB` in `nodes.env` with the measured values. **These drive `--tensor-split`; do not leave them at the assumed 8192.**

- [ ] **Step 3: Write the distribution script**

Create `provisioning/distribute.sh`:

```bash
#!/usr/bin/env bash
# Push the master's llama.cpp binaries to every worker and assert they match.
# Version mismatch across nodes breaks the RPC handshake.
set -euo pipefail

cd "$(dirname "$0")/.."
source provisioning/nodes.env

SRC=/opt/llama.cpp
VERSION=$(cat "$SRC/VERSION")
MASTER_LIBC=$(apt-cache policy libc6 | awk '/Installed:/{print $2}')

echo "Distributing llama.cpp $VERSION (built against libc6 $MASTER_LIBC)"

for entry in "${NODES[@]:1}"; do
  set -- $entry
  NAME=$1; IP=$2

  REMOTE_LIBC=$(ssh "$IP" "apt-cache policy libc6 | awk '/Installed:/{print \$2}'")
  if [ "$REMOTE_LIBC" != "$MASTER_LIBC" ]; then
    echo "FATAL: $NAME libc6 $REMOTE_LIBC != master $MASTER_LIBC" >&2
    echo "Binaries built on the master may not run. Align the point release." >&2
    exit 1
  fi

  rsync -az --delete "$SRC/bin/" "$IP:$SRC/bin/"
  scp -q "$SRC/VERSION" "$IP:$SRC/VERSION"

  REMOTE_VERSION=$(ssh "$IP" "cat $SRC/VERSION")
  if [ "$REMOTE_VERSION" != "$VERSION" ]; then
    echo "FATAL: $NAME version $REMOTE_VERSION != $VERSION" >&2
    exit 1
  fi
  ssh "$IP" "$SRC/bin/rpc-server --help >/dev/null" \
    || { echo "FATAL: rpc-server will not run on $NAME" >&2; exit 1; }
  echo "  $NAME ok"
done

echo "All nodes at $VERSION"
```

- [ ] **Step 4: Distribute and verify**

```bash
chmod +x provisioning/distribute.sh
./provisioning/distribute.sh
```

Expected: `<node> ok` for nodes 2–7, then `All nodes at <tag>`. Any FATAL must be fixed before proceeding — a mismatched node fails the RPC handshake later with a much less obvious error.

- [ ] **Step 5: Commit**

```bash
git add provisioning/distribute.sh provisioning/nodes.env
git commit -m "feat: fleet binary distribution with version and libc assertions"
```

---

# Phase 2 — Sharded inference

### Task 6: Run rpc-server as a service on every node

**Files:**
- Create: `cluster/rpc-server@.service`
- Create: `cluster/install-services.sh`

**Interfaces:**
- Consumes: `nodes.env`, distributed binaries.
- Produces: `rpc-server` listening on `RPC_PORT` on all 7 nodes, restart-resilient.

- [ ] **Step 1: Write the systemd unit**

Create `cluster/rpc-server@.service`:

```ini
[Unit]
Description=llama.cpp RPC server (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=cluster
# RPC_THREADS is written per-node by install-services.sh from that node's
# actual nproc. systemd does not expand shell variables, hence EnvironmentFile.
EnvironmentFile=/etc/default/rpc-server
# -c enables the local tensor cache: without it every restart re-pushes the
#    full model over the wire, and there is a report of the process going
#    <defunct> when run headless without it.
# -t is required: the default is HALF the logical cores.
# -H 0.0.0.0 binds the LAN interface. RPC stays on raw LAN IPs, never Tailscale.
ExecStart=/opt/llama.cpp/bin/rpc-server -H 0.0.0.0 -p %i -t $RPC_THREADS -c
Restart=always
RestartSec=5
LimitMEMLOCK=infinity

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the installer**

Create `cluster/install-services.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source provisioning/nodes.env

for entry in "${NODES[@]}"; do
  set -- $entry
  NAME=$1; IP=$2
  scp -q cluster/rpc-server@.service "$IP:/tmp/"
  # Thread count comes from each node's real core count, never a hardcoded
  # guess -- rpc-server's own default is half the cores.
  ssh "$IP" "sudo mv /tmp/rpc-server@.service /etc/systemd/system/ && \
             echo RPC_THREADS=\$(nproc) | sudo tee /etc/default/rpc-server >/dev/null && \
             sudo systemctl daemon-reload && \
             sudo systemctl enable --now rpc-server@${RPC_PORT}"
  echo "  $NAME started with \$(ssh $IP nproc) threads"
done

echo
echo "Verifying all endpoints reachable from master:"
for entry in "${NODES[@]}"; do
  set -- $entry
  timeout 3 bash -c "cat < /dev/null > /dev/tcp/$2/$RPC_PORT" \
    && echo "  $1 $2:$RPC_PORT open" \
    || { echo "  FATAL: $1 $2:$RPC_PORT unreachable" >&2; exit 1; }
done
```

- [ ] **Step 3: Install and verify**

```bash
chmod +x cluster/install-services.sh
./cluster/install-services.sh
```

Expected: every node reports `open`. Then confirm the thread count actually applied:

```bash
source provisioning/nodes.env
ssh 192.168.1.102 "systemctl show rpc-server@$RPC_PORT -p ExecStart | grep -o '\-t 4'"
```

- [ ] **Step 4: Commit**

```bash
git add cluster/rpc-server@.service cluster/install-services.sh
git commit -m "feat: rpc-server systemd unit and fleet installer"
```

---

### Task 7: Fetch the model and compute the layer split

**Files:**
- Create: `cluster/start-cluster.sh`
- Modify: `docs/measurements.md`

**Interfaces:**
- Consumes: `nodes.env`, running `rpc-server` fleet.
- Produces: `llama-server` on the master serving an OpenAI-compatible API on port 8080.

- [ ] **Step 1: Choose and fetch the cluster model (Model B)**

List available Unsloth Kimi K2 quants with sizes:

```bash
curl -s "https://huggingface.co/api/models/unsloth/Kimi-K2-Instruct-GGUF?blobs=true" \
  | jq -r '.siblings[] | select(.rfilename|test("gguf$")) | "\(.size/1e9|floor)GB \(.rfilename)"' \
  | sort -n
```

**Pick the largest quant whose total is ≤672 GB** (the per-node 75% ceiling
across 7 × 128 GB), leaving pooled headroom. If the repo name 404s, search:
`curl -s "https://huggingface.co/api/models?search=Kimi-K2-Instruct-GGUF" | jq -r '.[].id'`

Large quants are split into multiple files. Fetch all parts:

```bash
sudo mkdir -p /opt/models/kimi-k2 && sudo chown "$USER" /opt/models/kimi-k2
# Repeat per part, or use huggingface-cli:
pip install -q huggingface_hub
huggingface-cli download unsloth/Kimi-K2-Instruct-GGUF \
  --include "<chosen-quant-pattern>*" \
  --local-dir /opt/models/kimi-k2
du -sh /opt/models/kimi-k2
```

llama.cpp loads split GGUFs by pointing at the **first** part; it finds the rest.

- [ ] **Step 2: Verify both memory constraints before launching**

```bash
source provisioning/nodes.env
MODEL_GB=$(du -sb /opt/models/kimi-k2 | awk '{printf "%.1f", $1/1e9}')
N=${#NODES[@]}
echo "Model: ${MODEL_GB} GB across ${N} nodes"

python3 - <<PY
nodes = [l.split() for l in """$(printf '%s\n' "${NODES[@]}")""".strip().split("\n")]
model_gb = $MODEL_GB
total_ram = sum(int(n[2]) for n in nodes) / 1024
pooled_limit = (total_ram - len(nodes)) * 0.85
print(f"Pooled limit: {pooled_limit:.0f} GB -- "
      f"{'OK' if model_gb <= pooled_limit else 'FAIL'}")
# Layers are split proportionally to RAM, so each node's share tracks its size.
for name, ip, ram_mb in nodes:
    ram_gb = int(ram_mb) / 1024
    share = model_gb * (ram_gb / total_ram)
    pct = 100 * share / ram_gb
    ok = "OK" if pct <= 75 else "FAIL (>75%, aborts at runtime)"
    print(f"  {name}: {share:.0f}/{ram_gb:.0f} GB = {pct:.0f}%  {ok}")
PY
```

Expected: every node ≤75% and pooled OK. **Any FAIL means the model will abort
with `"Remote RPC server crashed or returned malformed response"` — step down a
quant rather than trying to push through it.**

- [ ] **Step 3: Write the cluster launcher**

Create `cluster/start-cluster.sh`:

```bash
#!/usr/bin/env bash
# Launch llama-server against the RPC fleet.
#
# --tensor-split is set EXPLICITLY from measured RAM. The default auto-split
# allocates by nodes' self-reported free memory, which has known reporting bugs
# (#8112) and is not trustworthy on a memory-constrained fleet.
set -euo pipefail
cd "$(dirname "$0")/.."
source provisioning/nodes.env

MODEL="${MODEL:?set MODEL to the first part of the GGUF, e.g. /opt/models/kimi-k2/....-00001-of-000NN.gguf}"
CTX="${CTX:-32768}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-$(nproc)}"

RPC_LIST=""
SPLIT=""
for entry in "${NODES[@]}"; do
  set -- $entry
  RPC_LIST="${RPC_LIST:+$RPC_LIST,}$2:$RPC_PORT"
  SPLIT="${SPLIT:+$SPLIT,}$3"
done

echo "RPC endpoints: $RPC_LIST"
echo "Tensor split:  $SPLIT"

# --rpc must precede any --device flag or the device list misresolves.
exec /opt/llama.cpp/bin/llama-server \
  --rpc "$RPC_LIST" \
  --tensor-split "$SPLIT" \
  -m "$MODEL" \
  -c "$CTX" \
  -t "$THREADS" \
  --host 0.0.0.0 \
  --port "$PORT"
```

- [ ] **Step 4: Launch and watch the layer assignment**

```bash
chmod +x cluster/start-cluster.sh
./cluster/start-cluster.sh 2>&1 | tee /tmp/cluster-start.txt
```

Expected: log lines assigning layer ranges to each RPC device, then `server is listening`. First start pushes ~32 GB over gigabit — **expect several minutes.** Subsequent starts should be much faster thanks to `-c`.

If it aborts with `"Remote RPC server crashed or returned malformed response"`, check the per-node 75% calculation from Step 2 and check `journalctl -u rpc-server@50052` on the workers for `"Null buffer for tensor"`.

- [ ] **Step 5: Verify coherent sharded output**

```bash
curl -s -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"In three sentences, explain why an organisation with sensitive records might run an AI model on its own hardware."}],"max_tokens":200}' \
  | jq -r '.choices[0].message.content'
```

Expected: coherent, on-topic prose. **This is the moment the cluster works.**

- [ ] **Step 6: Verify the cache makes restarts fast**

```bash
# Ctrl-C the server, then restart and time it.
time ./cluster/start-cluster.sh   # note time to "server is listening"
ssh 192.168.1.102 "du -sh ~/.cache/llama.cpp/rpc"
```

Record both start times in `docs/measurements.md`. If the second start is not materially faster, `-c` is not working — investigate before accepting slow iteration as normal.

- [ ] **Step 7: Commit**

```bash
git add cluster/start-cluster.sh docs/measurements.md
git commit -m "feat: cluster launcher with explicit tensor-split"
```

---

### Task 8: Measure the cluster

**Files:**
- Modify: `docs/measurements.md`
- Modify: `bench/node-bench.sh`

**Interfaces:**
- Consumes: running cluster on port 8080.
- Produces: the numbers the blog post cites.

- [ ] **Step 1: Measure single-seat throughput and TTFT**

With the cluster running:

```bash
# TTFT and generation rate at a realistic document length.
PROMPT=$(python3 -c "print('The quick brown fox jumps over the lazy dog. ' * 220)")
for i in 1 2 3; do
  curl -s -o /dev/null -w "run $i: TTFT %{time_starttransfer}s  total %{time_total}s\n" \
    -X POST http://127.0.0.1:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json
print(json.dumps({'messages':[{'role':'user','content':'Summarise: $PROMPT'}],
                  'max_tokens':128,'stream':True}))")"
done
```

- [ ] **Step 2: Measure concurrent seats**

This tests the spec's central claim that sharding multiplies seats rather than speed.

```bash
cat > /tmp/seat-test.sh <<'EOF'
#!/usr/bin/env bash
N=$1
start=$(date +%s.%N)
for i in $(seq 1 "$N"); do
  curl -s -o /dev/null -X POST http://127.0.0.1:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"Write one paragraph about data privacy."}],"max_tokens":128}' &
done
wait
end=$(date +%s.%N)
echo "$N concurrent: $(echo "$end - $start" | bc)s total"
EOF
chmod +x /tmp/seat-test.sh
for n in 1 2 4 7; do /tmp/seat-test.sh $n; done
```

Note: `llama-server` needs `--parallel N` to actually serve N concurrently. Re-run `start-cluster.sh` with `--parallel 4` appended before this test, and **verify output is not garbled** — there was a KV-cache corruption bug under concurrency (#14893), fixed upstream but worth confirming on the pinned build.

- [ ] **Step 3: Record everything**

Append to `docs/measurements.md`:

```markdown
## Model B: Kimi K2 across 7 nodes

**Date:** <YYYY-MM-DD>
**Model:** Kimi K2 <quant> (<size> GB) | **Context:** 32768
**llama.cpp:** <tag> | **Split:** <tensor-split values>

| Metric | Value |
|---|---|
| Cold start (first load, s) | |
| Warm start (cached, s) | |
| TTFT @ ~2000 tok prompt (s) | |
| Generation (tok/s, single seat) | |
| 1 concurrent request (s) | |
| 2 concurrent | |
| 4 concurrent | |
| 7 concurrent | |

**Seats vs speed:** <does total time stay flat as concurrency rises?>
**Output correctness under concurrency:** <garbled / clean>
```

- [ ] **Step 4: Commit**

```bash
git add docs/measurements.md
git commit -m "docs: cluster throughput, TTFT and concurrency measurements"
```

---

### Task 9: Open WebUI

**Files:**
- Create: `cluster/webui-compose.yml`

**Interfaces:**
- Consumes: `llama-server` on master:8080.
- Produces: chat UI on master:3000, reachable over Tailscale.

- [ ] **Step 1: Install Docker on the master**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

- [ ] **Step 2: Write the compose file**

Create `cluster/webui-compose.yml`:

```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    ports:
      - "3000:8080"
    environment:
      # llama-server exposes an OpenAI-compatible API; point Open WebUI at it.
      - OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1
      - OPENAI_API_KEY=none
      - WEBUI_AUTH=false
      - ENABLE_OLLAMA_API=false
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - open-webui-data:/app/backend/data
    restart: unless-stopped

volumes:
  open-webui-data:
```

- [ ] **Step 3: Start and verify**

```bash
docker compose -f cluster/webui-compose.yml up -d
sleep 20
curl -sf http://127.0.0.1:3000 >/dev/null && echo "webui up"
```

Then from another Tailscale device, open `http://<master-tailscale-name>:3000`, select the model, and send a message. Expected: a streamed reply from the cluster.

- [ ] **Step 4: Commit**

```bash
git add cluster/webui-compose.yml
git commit -m "feat: Open WebUI frontend"
```

---

# Phase 3 — Missing Link

An async job runner: submit a document and a task, collect the result later. Deliberately minimal — no auth, no priorities, no retries, no distributed workers.

### Task 10: Job store

**Files:**
- Create: `missing-link/db.py`
- Create: `missing-link/tests/test_db.py`
- Create: `missing-link/requirements.txt`

**Interfaces:**
- Produces:
  - `init_db(path: str) -> None`
  - `create_job(path: str, kind: str, document: str) -> str` — returns job id
  - `get_job(path: str, job_id: str) -> dict | None`
  - `list_jobs(path: str) -> list[dict]`
  - `claim_next_pending(path: str) -> dict | None`
  - `complete_job(path: str, job_id: str, result: str, metrics: dict) -> None`
  - `fail_job(path: str, job_id: str, error: str) -> None`
  - Job dict keys: `id, kind, document, status, result, error, created_at, started_at, finished_at, ttft_s, total_s, tokens`
  - `status` ∈ `pending | running | done | failed`

- [ ] **Step 1: Write requirements and pytest config**

Create `missing-link/requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
jinja2==3.1.5
pytest==8.3.4
```

Create `missing-link/pytest.ini` — without `pythonpath`, `from missing_link
import db` fails to resolve when pytest is run from this directory:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 2: Write the failing tests**

Create `missing-link/tests/test_db.py`:

```python
import os
import tempfile
import pytest
from missing_link import db


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


def test_create_and_get_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "some document text")
    job = db.get_job(dbpath, job_id)
    assert job["id"] == job_id
    assert job["kind"] == "summarise"
    assert job["document"] == "some document text"
    assert job["status"] == "pending"
    assert job["result"] is None


def test_get_missing_job_returns_none(dbpath):
    assert db.get_job(dbpath, "nonexistent") is None


def test_claim_next_pending_returns_oldest_first(dbpath):
    first = db.create_job(dbpath, "summarise", "a")
    db.create_job(dbpath, "summarise", "b")
    claimed = db.claim_next_pending(dbpath)
    assert claimed["id"] == first
    assert db.get_job(dbpath, first)["status"] == "running"


def test_claim_next_pending_skips_running(dbpath):
    db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    assert db.claim_next_pending(dbpath) is None


def test_complete_job_records_result_and_metrics(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.complete_job(dbpath, job_id, "the summary",
                    {"ttft_s": 12.5, "total_s": 60.0, "tokens": 128})
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["result"] == "the summary"
    assert job["ttft_s"] == 12.5
    assert job["tokens"] == 128
    assert job["finished_at"] is not None


def test_fail_job_records_error(dbpath):
    job_id = db.create_job(dbpath, "summarise", "a")
    db.claim_next_pending(dbpath)
    db.fail_job(dbpath, job_id, "connection refused")
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "connection refused"


def test_jobs_survive_reopen(dbpath):
    job_id = db.create_job(dbpath, "summarise", "persisted")
    db.init_db(dbpath)  # re-init must not wipe
    assert db.get_job(dbpath, job_id)["document"] == "persisted"


def test_list_jobs_newest_first(dbpath):
    db.create_job(dbpath, "summarise", "old")
    newest = db.create_job(dbpath, "report", "new")
    jobs = db.list_jobs(dbpath)
    assert len(jobs) == 2
    assert jobs[0]["id"] == newest
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd missing-link
python3 -m venv .venv && . .venv/bin/activate
pip install -q -r requirements.txt
python -m pytest tests/test_db.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'missing_link'`.

- [ ] **Step 4: Implement the store**

Create `missing-link/missing_link/__init__.py` (empty), and `missing-link/missing_link/db.py`:

```python
"""SQLite job store. Jobs must outlive any single process run -- a queue that
loses work on restart defeats the point of an async runner."""
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    document    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    ttft_s      REAL,
    total_s     REAL,
    tokens      INTEGER
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path):
    with _connect(path) as conn:
        conn.executescript(SCHEMA)


def create_job(path, kind, document):
    job_id = uuid.uuid4().hex[:12]
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, document, created_at) VALUES (?,?,?,?)",
            (job_id, kind, document, _now()),
        )
    return job_id


def get_job(path, job_id):
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(path):
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def claim_next_pending(path):
    """Atomically move the oldest pending job to running and return it."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (_now(), row["id"]),
        )
    return dict(row)


def complete_job(path, job_id, result, metrics):
    with _connect(path) as conn:
        conn.execute(
            "UPDATE jobs SET status='done', result=?, finished_at=?, "
            "ttft_s=?, total_s=?, tokens=? WHERE id=?",
            (result, _now(), metrics.get("ttft_s"), metrics.get("total_s"),
             metrics.get("tokens"), job_id),
        )


def fail_job(path, job_id, error):
    with _connect(path) as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error, _now(), job_id),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd missing-link && . .venv/bin/activate
python -m pytest tests/test_db.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add missing-link/ && echo '.venv/' > missing-link/.gitignore
git add missing-link/.gitignore
git commit -m "feat(missing-link): SQLite job store with persistence tests"
```

---

### Task 11: Job worker

**Files:**
- Create: `missing-link/missing_link/worker.py`
- Create: `missing-link/tests/test_worker.py`

**Interfaces:**
- Consumes: `db.claim_next_pending`, `db.complete_job`, `db.fail_job`.
- Produces:
  - `PROMPTS: dict[str, str]` — keyed by job kind
  - `build_prompt(kind: str, document: str) -> str`
  - `run_one(db_path: str, base_url: str, client) -> bool` — processes one job, returns True if a job was handled
  - `run_forever(db_path: str, base_url: str, poll_s: float = 5.0) -> None`

- [ ] **Step 1: Write the failing tests**

Create `missing-link/tests/test_worker.py`:

```python
import os
import tempfile
import httpx
import pytest
from missing_link import db, worker


@pytest.fixture
def dbpath():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


def test_build_prompt_includes_document():
    prompt = worker.build_prompt("summarise", "MY DOCUMENT")
    assert "MY DOCUMENT" in prompt


def test_build_prompt_differs_by_kind():
    assert worker.build_prompt("summarise", "x") != worker.build_prompt("report", "x")


def test_build_prompt_rejects_unknown_kind():
    with pytest.raises(ValueError):
        worker.build_prompt("nonsense", "x")


def test_run_one_returns_false_when_no_jobs(dbpath):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert worker.run_one(dbpath, "http://x", client) is False


def test_run_one_completes_a_job(dbpath):
    job_id = db.create_job(dbpath, "summarise", "the document")

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "a summary"}}],
            "usage": {"completion_tokens": 42},
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert worker.run_one(dbpath, "http://x", client) is True

    job = db.get_job(dbpath, job_id)
    assert job["status"] == "done"
    assert job["result"] == "a summary"
    assert job["tokens"] == 42
    assert job["total_s"] is not None


def test_run_one_marks_failure_on_http_error(dbpath):
    job_id = db.create_job(dbpath, "summarise", "the document")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    )
    assert worker.run_one(dbpath, "http://x", client) is True
    job = db.get_job(dbpath, job_id)
    assert job["status"] == "failed"
    assert "500" in job["error"]


def test_run_one_marks_failure_on_connection_error(dbpath):
    job_id = db.create_job(dbpath, "summarise", "the document")

    def handler(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert worker.run_one(dbpath, "http://x", client) is True
    assert db.get_job(dbpath, job_id)["status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd missing-link && . .venv/bin/activate
python -m pytest tests/test_worker.py -v
```

Expected: FAIL — `cannot import name 'worker'`.

- [ ] **Step 3: Implement the worker**

Create `missing-link/missing_link/worker.py`:

```python
"""Sequential job executor.

Deliberately one-at-a-time: the cluster serves a small number of slow seats,
and queueing work behind a single worker is the honest model. Concurrency
would contend for the same pipeline.
"""
import time
import httpx
from . import db

PROMPTS = {
    "summarise": (
        "Summarise the following document. Give a short paragraph of overview, "
        "then the key points as a bulleted list. Be faithful to the source and "
        "do not speculate beyond it.\n\n---\n\n{document}"
    ),
    "report": (
        "Using the material below, draft a clear written report. Use headings "
        "and complete sentences. Base every claim on the material provided.\n\n"
        "---\n\n{document}"
    ),
}

# Generous: a slow cluster may legitimately take many minutes per job.
REQUEST_TIMEOUT = httpx.Timeout(3600.0, connect=10.0)


def build_prompt(kind, document):
    if kind not in PROMPTS:
        raise ValueError(f"unknown job kind: {kind}")
    return PROMPTS[kind].format(document=document)


def run_one(db_path, base_url, client):
    """Process one pending job. Returns True if a job was handled."""
    job = db.claim_next_pending(db_path)
    if job is None:
        return False

    try:
        prompt = build_prompt(job["kind"], job["document"])
        started = time.monotonic()
        response = client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
            },
            timeout=REQUEST_TIMEOUT,
        )
        total_s = time.monotonic() - started

        if response.status_code != 200:
            db.fail_job(db_path, job["id"],
                        f"HTTP {response.status_code}: {response.text[:500]}")
            return True

        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        tokens = payload.get("usage", {}).get("completion_tokens")
        db.complete_job(db_path, job["id"], content,
                        {"total_s": total_s, "tokens": tokens, "ttft_s": None})
    except Exception as exc:  # noqa: BLE001 - any failure must mark the job
        db.fail_job(db_path, job["id"], f"{type(exc).__name__}: {exc}")

    return True


def run_forever(db_path, base_url, poll_s=5.0):
    with httpx.Client() as client:
        while True:
            if not run_one(db_path, base_url, client):
                time.sleep(poll_s)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd missing-link && . .venv/bin/activate
python -m pytest tests/test_worker.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add missing-link/
git commit -m "feat(missing-link): sequential job worker with failure handling"
```

---

### Task 12: Web API and views

**Files:**
- Create: `missing-link/missing_link/app.py`
- Create: `missing-link/missing_link/templates/index.html`
- Create: `missing-link/tests/test_app.py`
- Create: `missing-link/run.sh`

**Interfaces:**
- Consumes: `db`, `worker`.
- Produces: FastAPI app on port 8090 with `POST /jobs`, `GET /jobs/{id}`, `GET /jobs`, `GET /`.

- [ ] **Step 1: Write the failing tests**

Create `missing-link/tests/test_app.py`:

```python
import os
import tempfile
import pytest

# Must be set before importing app: the background worker would otherwise race
# these tests, claiming jobs and failing them against a nonexistent server.
os.environ["MISSING_LINK_START_WORKER"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from missing_link import db, app as app_module  # noqa: E402


@pytest.fixture
def client():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    app_module.DB_PATH = path
    app_module.START_WORKER = False
    yield TestClient(app_module.app)
    os.unlink(path)


def test_submit_job_returns_id(client):
    r = client.post("/jobs", json={"kind": "summarise", "document": "text"})
    assert r.status_code == 200
    assert "id" in r.json()


def test_submit_rejects_unknown_kind(client):
    r = client.post("/jobs", json={"kind": "nonsense", "document": "text"})
    assert r.status_code == 422


def test_submit_rejects_empty_document(client):
    r = client.post("/jobs", json={"kind": "summarise", "document": "   "})
    assert r.status_code == 422


def test_get_job_returns_status(client):
    job_id = client.post("/jobs", json={"kind": "summarise",
                                        "document": "text"}).json()["id"]
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_missing_job_404s(client):
    assert client.get("/jobs/nope").status_code == 404


def test_list_jobs(client):
    client.post("/jobs", json={"kind": "summarise", "document": "a"})
    client.post("/jobs", json={"kind": "report", "document": "b"})
    r = client.get("/jobs")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_index_page_renders(client):
    client.post("/jobs", json={"kind": "summarise", "document": "a"})
    r = client.get("/")
    assert r.status_code == 200
    assert "Missing Link" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd missing-link && . .venv/bin/activate
python -m pytest tests/test_app.py -v
```

Expected: FAIL — `cannot import name 'app'`.

- [ ] **Step 3: Implement the app**

Create `missing-link/missing_link/app.py`:

```python
"""Missing Link: submit long jobs, collect results later."""
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from . import db, worker

DB_PATH = os.environ.get("MISSING_LINK_DB", "/opt/missing-link/jobs.sqlite")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")
# Tests set this to disable the background worker. Without it the worker races
# the test: it claims freshly-created jobs and marks them failed against a
# nonexistent llama-server, so status assertions become flaky.
START_WORKER = os.environ.get("MISSING_LINK_START_WORKER", "1") == "1"

app = FastAPI(title="Missing Link")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class JobRequest(BaseModel):
    kind: str
    document: str

    @field_validator("kind")
    @classmethod
    def kind_known(cls, v):
        if v not in worker.PROMPTS:
            raise ValueError(f"kind must be one of {sorted(worker.PROMPTS)}")
        return v

    @field_validator("document")
    @classmethod
    def document_not_blank(cls, v):
        if not v.strip():
            raise ValueError("document must not be empty")
        return v


@app.on_event("startup")
def startup():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db.init_db(DB_PATH)
    if START_WORKER:
        threading.Thread(
            target=worker.run_forever, args=(DB_PATH, LLAMA_URL), daemon=True
        ).start()


@app.post("/jobs")
def submit(req: JobRequest):
    return {"id": db.create_job(DB_PATH, req.kind, req.document)}


@app.get("/jobs")
def list_all():
    return db.list_jobs(DB_PATH)


@app.get("/jobs/{job_id}")
def get_one(job_id: str):
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "jobs": db.list_jobs(DB_PATH),
         "kinds": sorted(worker.PROMPTS)},
    )
```

- [ ] **Step 4: Create the template**

Create `missing-link/missing_link/templates/index.html`:

```html
<!doctype html>
<meta charset="utf-8">
<title>Missing Link</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 55rem;
         margin: 3rem auto; padding: 0 1rem; }
  h1 { font-weight: 600; letter-spacing: -0.02em; }
  .sub { color: #666; margin-top: -0.5rem; }
  textarea { width: 100%; min-height: 9rem; font: 14px/1.5 ui-monospace, monospace; }
  table { width: 100%; border-collapse: collapse; margin-top: 2rem; }
  th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #ddd;
           vertical-align: top; }
  .status { font-weight: 600; }
  .pending { color: #96690a; } .running { color: #1a5fb4; }
  .done { color: #26734d; }    .failed  { color: #a51d2d; }
  pre { white-space: pre-wrap; margin: 0; font-size: 13px; }
</style>

<h1>Missing Link</h1>
<p class="sub">Submit work. Collect it later. Nothing leaves the building.</p>

<form method="post" action="/jobs" id="f">
  <select name="kind">
    {% for k in kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}
  </select>
  <p><textarea name="document" placeholder="Paste the document here…"></textarea></p>
  <button type="submit">Queue job</button>
</form>

<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  await fetch('/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({kind: fd.get('kind'), document: fd.get('document')})
  });
  location.reload();
});
</script>

<table>
  <tr><th>ID</th><th>Kind</th><th>Status</th><th>Time</th><th>Result</th></tr>
  {% for j in jobs %}
  <tr>
    <td><code>{{ j.id }}</code></td>
    <td>{{ j.kind }}</td>
    <td class="status {{ j.status }}">{{ j.status }}</td>
    <td>{% if j.total_s %}{{ '%.0f'|format(j.total_s) }}s{% endif %}</td>
    <td><pre>{{ (j.result or j.error or '')[:600] }}</pre></td>
  </tr>
  {% endfor %}
</table>
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd missing-link && . .venv/bin/activate
python -m pytest tests/ -v
```

Expected: all tests pass (8 db + 7 worker + 7 app = 22).

- [ ] **Step 6: Write the run script and start it**

Create `missing-link/run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
. .venv/bin/activate
export MISSING_LINK_DB="${MISSING_LINK_DB:-/opt/missing-link/jobs.sqlite}"
export LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:8080}"
exec uvicorn missing_link.app:app --host 0.0.0.0 --port 8090
```

```bash
chmod +x missing-link/run.sh
sudo mkdir -p /opt/missing-link && sudo chown "$USER" /opt/missing-link
./missing-link/run.sh &
sleep 3
curl -sf http://127.0.0.1:8090/jobs && echo " api up"
```

- [ ] **Step 7: End-to-end test against the real cluster**

With `start-cluster.sh` running:

```bash
JOB=$(curl -s -X POST http://127.0.0.1:8090/jobs \
  -H 'Content-Type: application/json' \
  -d '{"kind":"summarise","document":"'"$(python3 -c "print('Enrolment policy text. ' * 300)")"'"}' \
  | jq -r .id)
echo "queued $JOB"
watch -n 10 "curl -s http://127.0.0.1:8090/jobs/$JOB | jq '{status, total_s}'"
```

Expected: status moves `pending → running → done`, with a real summary in `result`. **This is Missing Link working: slow inference made useful by making it asynchronous.**

- [ ] **Step 8: Commit**

```bash
git add missing-link/
git commit -m "feat(missing-link): web API, job views, and run script"
```

---

### Task 13: Record the Missing Link measurements and update STATUS

**Files:**
- Modify: `docs/measurements.md`
- Modify: `STATUS.md`

- [ ] **Step 1: Run three realistic documents end to end**

Queue three genuinely representative documents (a policy, a set of meeting minutes, a report draft) and record wall-clock time and quality for each.

- [ ] **Step 2: Record**

Append to `docs/measurements.md`:

```markdown
## Missing Link end-to-end

**Date:** <YYYY-MM-DD>

| Document | Approx input tokens | Kind | Wall clock | Output quality |
|---|---|---|---|---|
| | | | | |
```

- [ ] **Step 3: Update STATUS.md**

Set phase to `Operational`, move all completed items out of open questions, and add a log entry with the headline measured numbers.

- [ ] **Step 4: Commit**

```bash
git add docs/measurements.md STATUS.md
git commit -m "docs: Missing Link end-to-end measurements; cluster operational"
```

---

## Open decision for the user

**Long-document chunking is deliberately not implemented.** `worker.py` sends the whole document in one request. With an 8k context, a document longer than roughly 6,000 words will be truncated or rejected by `llama-server`.

There are three reasonable strategies, and the right one depends on what the documents actually look like:

1. **Reject oversized documents** at submission with a clear error. Honest, trivial, and no silent quality loss — but the user has to split things by hand.
2. **Map-reduce**: chunk, summarise each chunk, then summarise the summaries. Handles any length, but costs N+1 cluster passes and loses cross-chunk context. On a cluster this slow, a 10-chunk document is a genuinely long wait.
3. **Raise the context window** to 32k (KV cache is only ~3 GB at that length — memory is not the constraint here) and reject beyond that. Simplest, but prefill time grows with context and prefill is the slow part on CPU.

This is a real trade-off between wait time, fidelity, and implementation cost, and it depends on document length distribution that only you know. Once measurements from Task 8 show actual prefill cost at 2k vs 8k, the answer may be obvious — revisit it then rather than guessing now.

---

## Verification checklist

Before declaring the cluster done, every one of these must have been run and its output seen:

- [ ] `./bench/overhead-test.sh` — RPC overhead measured, verdict recorded
- [ ] `./provisioning/distribute.sh` — exits 0, all nodes version-matched
- [ ] `./cluster/install-services.sh` — all 7 endpoints report open
- [ ] Per-node 75% RAM check passes for every node
- [ ] `curl` against `:8080/v1/chat/completions` returns coherent prose
- [ ] Concurrency test run, output confirmed not garbled
- [ ] Open WebUI reachable over Tailscale, streams a reply
- [ ] `python -m pytest missing-link/tests/ -v` — 22 passed
- [ ] A real document completes end to end through Missing Link
- [ ] `docs/measurements.md` contains every number, none estimated

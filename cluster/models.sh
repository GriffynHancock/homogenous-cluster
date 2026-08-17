#!/usr/bin/env bash
# Fleet model index. Answers "what models do we have, where are they, and how
# do I get one onto this node without waiting 7 hours for HuggingFace."
#
# THE KEY FACT THIS TOOL IS BUILT AROUND:
#   Workers do NOT need model files. ggml-rpc-server has no GGUF loading code;
#   the coordinator reads the model and pushes tensors over TCP. Workers keep
#   only a local tensor cache (rpc-server -c), measured at ~76% of their share.
#   So exactly ONE machine needs the full file -- the one running llama-server.
#
#   Therefore: put the big disk in whichever node you like, and run the
#   COORDINATOR THERE. Do not ship 547 GB to a fixed coordinator.
#
# Usage:
#   models.sh list                 what the manifest declares, and who really has it
#   models.sh where <id>           which node holds it -> run the coordinator there
#   models.sh plan <id>            predicted tok/s and per-node cache need
#   models.sh verify <id>          check local files against the manifest
#   models.sh pull <id> [--from N] fetch from a peer over LAN, else HuggingFace
set -uo pipefail

cd "$(dirname "$0")/.."
MANIFEST="${MANIFEST:-cluster/models.json}"
[ -f "$MANIFEST" ] || { echo "no manifest at $MANIFEST" >&2; exit 1; }
[ -f provisioning/nodes.env ] && source provisioning/nodes.env

# Measured on node 1. Re-measure per node: memory bandwidth, not RAM, sets
# generation speed, and it varies with core count (FINDINGS F12).
# Dense models reach ~99% of STREAM; sparse MoE reached only 61% (measured on
# gpt-oss-120b: 6.05 tok/s against 9.88 predicted). Scattered expert gathers
# defeat prefetch. Using the dense figure for an MoE overstates speed by ~1.6x.
BANDWIDTH_GBS="${BANDWIDTH_GBS:-28.2}"
MOE_BANDWIDTH_GBS="${MOE_BANDWIDTH_GBS:-17.3}"
# Fraction of a node's SHARE that rpc-server -c writes to disk (tensors >=10 MiB).
#
# Measured 0.76 on Qwen3-4B (817 MB + 1.1 GB across two workers with separate
# LLAMA_CACHE dirs, against a 2.4 GB model). But that is a small DENSE model
# where sub-10 MiB tensors -- embeddings, norms -- are a meaningful fraction.
# In a large MoE nearly every expert tensor is far above 10 MiB, so the ratio
# approaches 1.0. Plan with 1.0; under-provisioning worker disk during first
# load is far worse than over-provisioning it.
CACHE_RATIO="${CACHE_RATIO:-1.0}"

q() { jq -r "$1" "$MANIFEST"; }

model_json() {
  jq -e --arg id "$1" '.models[] | select(.id==$id)' "$MANIFEST" 2>/dev/null \
    || { echo "unknown model id: $1" >&2
         echo "known: $(q '.models[].id' | tr '\n' ' ')" >&2; exit 1; }
}

node_ips() {
  # "name ip ..." lines from nodes.env, if present.
  [ -n "${NODES+x}" ] || return 0
  printf '%s\n' "${NODES[@]}"
}

ip_for() {
  node_ips | awk -v n="$1" '$1==n {print $2; exit}'
}

cmd_list() {
  printf '%-24s %-14s %8s  %s\n' ID ROLE SIZE "HELD BY (manifest)"
  q '.models[] | [.id, .role, (.bytes/1e9|floor|tostring)+"GB",
                  (if (.held_by|length)>0 then (.held_by|join(",")) else "-- none --" end)]
      | @tsv' \
    | while IFS=$'\t' read -r id role size held; do
        printf '%-24s %-14s %8s  %s\n' "$id" "$role" "$size" "$held"
      done

  echo
  echo "Present on THIS node ($(hostname)):"
  local any=0
  for id in $(q '.models[].id'); do
    local m path entry
    m=$(model_json "$id")
    path=$(jq -r '.path' <<<"$m"); entry=$(jq -r '.entrypoint' <<<"$m")
    if [ -f "$path/$entry" ]; then
      local sz=0
      for f in $(jq -r '.files[]' <<<"$m"); do
        [ -f "$path/$f" ] && sz=$((sz + $(stat -c%s "$path/$f")))
      done
      printf '  %-24s %s\n' "$id" "$(numfmt --to=iec "$sz")"
      any=1
    fi
  done
  [ "$any" -eq 1 ] || echo "  (none)"
}

cmd_where() {
  local m; m=$(model_json "$1")
  local held; held=$(jq -r '.held_by | join(" ")' <<<"$m")
  if [ -z "$held" ]; then
    echo "$1 is not held by any node."
    echo "Fetch it with: cluster/models.sh pull $1"
    return 1
  fi
  echo "$1 is held by: $held"
  echo
  echo "Run the COORDINATOR on one of those nodes -- workers do not need the file:"
  for n in $held; do
    echo "  ssh $(ip_for "$n" || echo "$n") 'MODEL=$(jq -r .path <<<"$m")/$(jq -r .entrypoint <<<"$m") ./cluster/start-cluster.sh'"
  done
}

cmd_plan() {
  local m; m=$(model_json "$1")
  # FLEET_NODES is the PLANNED fleet size, not the number provisioned so far.
  # Dividing by a partially-populated inventory understates per-worker disk by
  # the number of nodes still missing -- which is exactly when you are sizing.
  local n_nodes="${FLEET_NODES:-7}"
  local inventory=0
  [ -n "${NODES+x}" ] && inventory=${#NODES[@]}
  if [ "$inventory" -lt "$n_nodes" ]; then
    echo "note: planning for $n_nodes nodes; nodes.env lists $inventory." >&2
    echo "      override with FLEET_NODES=<n>." >&2; echo >&2
  fi
  jq -r --arg bw "$BANDWIDTH_GBS" --arg moebw "$MOE_BANDWIDTH_GBS" \
        --arg cr "$CACHE_RATIO" --arg n "$n_nodes" '
    . as $m
    | ($m.active_params * $m.bytes_per_weight / 1e9) as $bpt
    | (if ($m.moe // false) then ($moebw|tonumber) else ($bw|tonumber) end) as $bw
    | ($n|tonumber) as $n
    | "Model:                 \($m.id)  (\($m.role))",
      "Total size:            \(($m.bytes/1e9)|floor) GB",
      "Active params:         \(($m.active_params/1e9)) B",
      "Bytes read per token:  \($bpt*1|.*100|round/100) GB",
      "",
      "COORDINATOR needs      \(($m.bytes/1e9)|floor) GB of free DISK (whole GGUF)",
      "EACH WORKER needs      \((($m.bytes/1e9)/$n)|floor) GB of RAM (its layer share, FULLY RESIDENT)",
      "                       \((($m.bytes/1e9)/$n*($cr|tonumber))|floor) GB of disk (rpc-server -c cache)",
      "  Weights are NOT paged from disk. Each node holds every expert of its",
      "  layer range in RAM; only the SELECTED experts are read per token.",
      "  Total params set RAM. Active params set speed.",
      "",
      "Predicted generation:  \(($bw/$bpt)*100|round/100) tok/s   (at \($bw) GB/s effective)",
      "  Sparse MoE reaches only 61% of STREAM (17.3 of 28.2 GB/s), measured on",
      "  gpt-oss-120b. Dense models reach ~99%. Scattered expert gathers defeat",
      "  prefetch. Re-measure per model -- expert count and top_k both matter."
  ' <<<"$m"
}

cmd_verify() {
  local m; m=$(model_json "$1")
  local path; path=$(jq -r '.path' <<<"$m")
  local rc=0 total=0
  for f in $(jq -r '.files[]' <<<"$m"); do
    if [ -f "$path/$f" ]; then
      local sz; sz=$(stat -c%s "$path/$f")
      total=$((total + sz))
      printf '  OK      %s (%s)\n' "$f" "$(numfmt --to=iec "$sz")"
    else
      printf '  MISSING %s\n' "$f"; rc=1
    fi
  done
  # Any leftover .part file means an interrupted download, not a usable model.
  if compgen -G "$path/*.part" >/dev/null 2>&1; then
    echo "  INCOMPLETE: $(ls "$path"/*.part) -- download still in progress or aborted"
    rc=1
  fi
  local declared; declared=$(jq -r '.bytes' <<<"$m")
  echo "  total $(numfmt --to=iec "$total"), manifest declares $(numfmt --to=iec "$declared")"
  return $rc
}

cmd_pull() {
  local id="$1"; shift
  local from=""
  while [ $# -gt 0 ]; do
    case "$1" in --from) from="$2"; shift 2 ;; *) shift ;; esac
  done

  local m; m=$(model_json "$id")
  local path repo; path=$(jq -r '.path' <<<"$m"); repo=$(jq -r '.repo' <<<"$m")
  mkdir -p "$path"

  # Prefer a peer. Measured 21 MB/s from HuggingFace vs ~110 MB/s on gigabit --
  # for a 547 GB model that is 7.2 hours against 1.4.
  if [ -z "$from" ]; then
    for n in $(jq -r '.held_by[]?' <<<"$m"); do
      [ "$n" = "$(hostname)" ] && continue
      from="$n"; break
    done
  fi

  if [ -n "$from" ]; then
    local ip; ip=$(ip_for "$from" || echo "$from")
    echo "Pulling $id from peer $from ($ip) over LAN..."
    # --partial --append-verify so an interrupted 547 GB transfer resumes.
    rsync -a --info=progress2 --partial --append-verify \
      "$ip:$path/" "$path/" || { echo "peer pull failed" >&2; return 1; }
  else
    echo "No peer holds $id. Falling back to HuggingFace ($repo) -- expect ~7h at 21 MB/s."
    for f in $(jq -r '.files[]' <<<"$m"); do
      /opt/models/fetch.sh \
        "https://huggingface.co/$repo/resolve/main/$f" "$path/$f" || return 1
    done
  fi

  cmd_verify "$id"
  echo
  echo "Now add $(hostname) to held_by for '$id' in $MANIFEST and commit."
}

case "${1:-list}" in
  list)   cmd_list ;;
  where)  cmd_where "${2:?usage: models.sh where <id>}" ;;
  plan)   cmd_plan  "${2:?usage: models.sh plan <id>}" ;;
  verify) cmd_verify "${2:?usage: models.sh verify <id>}" ;;
  pull)   shift; cmd_pull "${1:?usage: models.sh pull <id> [--from node]}" "${@:2}" ;;
  *) sed -n '2,30p' "$0" ;;
esac

# Status

**Updated:** 2026-08-10
**Phase:** Research (design approved, no hardware provisioned)

## Where things stand

The design is settled and committed. No nodes exist yet — no OS installed, no
hardware racked. Nothing has been measured; every performance number currently
in the spec is arithmetic on datasheets and is explicitly marked as such.

## Decided

| Question | Answer |
|---|---|
| Compute | CPU + system RAM only. Quadro P600s dropped. |
| Sharding | llama.cpp RPC. Not Exo. |
| Model | Qwen3-30B-A3B at ~32 GB (30B total, 3B active) |
| OS | Debian 12 headless |
| Provisioning | Debian preseed + idempotent `setup.sh`. Not `dd`. |
| Network | RPC on raw LAN IPs; Tailscale for admin SSH and web only |
| Frontend | Open WebUI, lightly skinned |
| Demo | Missing Link — async job runner (summarisation, report drafting) |

## Open questions

Blocking the implementation plan:

- [ ] Which Qwen3-30B-A3B variant and quant lands nearest 32 GB
- [ ] Whether `ik_llama.cpp` beats mainline for CPU MoE, and whether it supports RPC
- [ ] What `rpc-server -c` actually caches, and whether it avoids re-transfer
- [ ] How layer split across RPC backends is controlled with uneven node RAM
- [ ] Known RPC failure modes and their fixes

Not blocking, decide on measurement:

- [ ] Actual RAM per node and free DIMM slots (needs `dmidecode` on real hardware)
- [ ] Whether time-to-first-token is bad enough to reconsider GPUs for prefill

## In flight

Three Sonnet research agents dispatched 2026-08-10, covering llama.cpp RPC
pitfalls, Qwen3-30B-A3B CPU performance, and Debian fleet provisioning.
Findings will be folded into the spec before the implementation plan is written.

## Next

1. Fold research findings into the spec; close the blocking questions above
2. Write the implementation plan
3. Provision node 1, run `llama-bench`, replace estimates with measurements

## Log

- **2026-08-03** — Brainstormed. Initial design assumed GPU sharding across
  14 P600s; spec written and committed.
- **2026-08-03** — Pivoted to CPU-only. 14 GB pooled VRAM was too little to hold
  meaningful layers, and hybrid CUDA/CPU RPC was unproven. Dropping the GPUs
  removed an entire architectural fork.
- **2026-08-10** — Model target fixed at ~32 GB Qwen3-30B-A3B; RAM-dependent
  larger rungs deferred. Cloud framing removed from Missing Link — workloads are
  summarisation and report drafting, done entirely on-premises.

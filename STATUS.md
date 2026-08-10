# Status

**Updated:** 2026-08-10
**Phase:** Research complete; implementation plan not yet written

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

- [x] **Which Qwen3-30B-A3B variant and quant** — Instruct-2507, **Q8_0
      (32.5 GB)**. Fits the ~41.6 GB pooled budget with ~9 GB spare; ~4.6 GB per
      node. Fallbacks Q6_K (25.1 GB), Q5_K_M (21.7 GB).
- [x] **Does `ik_llama.cpp` support `rpc-server`?** **Yes** — confirmed in
      source. Its RPC code is at parity with mainline (same fast path, same
      synchronous limitation). Speed evidence is split (1.7–1.9× faster on a
      Broadwell Xeon; ~1.5× *slower* on Zen3 with Qwen3 MoE). Defaulting to
      mainline; A/B it after the cluster works.
- [x] **What `rpc-server -c` caches** — tensors ≥10 MiB, content-hashed, to
      `$LLAMA_CACHE` or `~/.cache/llama.cpp/rpc`. Skips retransfer on match.
      Run with `-c` always; there is a report of `<defunct>` without it.
- [x] **Layer split control** — `--tensor-split`, ordered as the `--rpc` list.
      Set explicitly; auto-split trusts buggy self-reported free memory.
      `--split-mode row` does nothing over RPC.
- [x] **Known RPC failure modes** — see spec Risks. Biggest is the ~75% per-node
      RAM ceiling (#15055) and ~30–55% protocol overhead (#22850, unfixed).

Not blocking, decide on measurement:

- [ ] Actual RAM per node and free DIMM slots (needs `dmidecode` on real hardware)
- [ ] Whether time-to-first-token is bad enough to reconsider GPUs for prefill
- [ ] Whether Qwen3.5/3.6-35B-A3B supersede the target (newer, not a drop-in)

Verify empirically on node 1 (cheap, no source found):

- [ ] Bookworm netinst initrd path (`install.amd/`?) — `ls` the actual ISO
- [ ] `ldd` output for a CPU-only `llama-server` / `rpc-server` build
- [ ] Whether `/etc/apt/sources.list` is populated correctly post-preseed

## Research findings so far

- **No public benchmark exists** for this model class on Broadwell/DDR3/AVX2.
  Nothing closer than DDR4 Xeons. Estimated 4–5 tok/s per seat; **prefill on
  AVX2-without-AVX-512 is entirely unmeasured.** This gap is the strongest
  argument for benchmarking node 1 before anything else.
- **KV cache is not a constraint.** GQA at 4 KV heads → ~96 KB/token, so 32k
  context costs ~3 GB. Long context is limited by prefill time, not memory.
- **Use all 4 cores; do not reduce thread count.** The "24 threads beat 96"
  finding is a many-core contention effect that does not apply below ~24
  threads. The real risk here is the opposite: `rpc-server -t` defaults to
  *half* the cores, so set `-t 4` explicitly.
- **Duplicate `machine-id` breaks DHCP, not just logging.** systemd-networkd
  derives its DHCP client-ID from it, so identical IDs make nodes collide on one
  lease — presents as intermittent fleet-wide network flapping.
- **Tailscale LAN isolation is automatic.** It only owns `100.64.0.0/10`. The
  only way to pull RPC onto WireGuard is to pass `--advertise-routes`, so the
  rule is simply never to pass it.
- **THP already correct on Debian 12** (`madvise`), and measurably better than
  `always` for llama.cpp. Assert the default rather than tuning it.
- **Preseed must set `non-free-firmware`** or recycled hardware may install
  without working NIC firmware.
- **Hard ~75% per-node RAM ceiling in RPC** (#15055, unfixed). Binds separately
  from the pooled 15% headroom. Q8_0 sits at 58% per node, so it clears.
- **The "30–55% RPC overhead" figure is not credible** and has been removed from
  the spec. Issue #22850 was LLM-authored and closed in under two hours for an
  AI-policy violation with no technical review; it benchmarked against a
  crippled baseline, on a build predating the fix for the main problem it
  identified. The real overhead for CPU-only batch-1 is **unmeasured** and
  likely far smaller. Measure it with the localhost `--rpc` test.
- **Graph reserialisation is fixed** (#22701 `graph_uid` fast path). Keep batch
  shapes constant across steps to keep that path active.
- **Async/pipelined RPC is coming** — PR #18626, authored by the RPC maintainer,
  open and active. A related CPU-only test showed prefill 37.96 → 59.84 t/s
  across two workers. Watch it; re-benchmark and re-pin when it lands.
- **prima.cpp and distributed-llama rejected.** prima.cpp has no MoE support and
  its original repo is gone; distributed-llama needs an all-reduce per layer per
  token over gigabit and abandons the GGUF stack.
- **Upstream calls RPC "fragile and insecure, never run on an open network."**
  Validates the raw-LAN-IP decision as a security requirement, not just latency.
- **The public 0.06 tok/s CPU-cluster figure is a misuse case** — the model
  already fitted on one host. Must never be cited without that context.
- **Pin the llama.cpp build.** `--tensor-split` over RPC has regressed before
  (#21006); a bad pin breaks all seven nodes at once.

## In flight

Exo deep-dive — confirm the rejection holds now that the design is CPU-only,
and mine its history for CPU efficiency techniques worth borrowing regardless.

## Next

1. Write the implementation plan
2. **Run the localhost RPC overhead test** — `llama-bench` local vs
   `--rpc localhost:PORT`, same machine, same model. Needs one machine, no
   cluster. Converts the overhead question from inference to fact and is the
   cheapest high-value measurement available.
3. Provision node 1, benchmark, replace estimates with measurements
2. Provision node 1, run `llama-bench`, replace estimates with measurements
3. Resolve the `ik_llama.cpp` RPC-support question (only blocking item left,
   and only if we want the fork at all — mainline is the default)

## Log

- **2026-08-03** — Brainstormed. Initial design assumed GPU sharding across
  14 P600s; spec written and committed.
- **2026-08-03** — Pivoted to CPU-only. 14 GB pooled VRAM was too little to hold
  meaningful layers, and hybrid CUDA/CPU RPC was unproven. Dropping the GPUs
  removed an entire architectural fork.
- **2026-08-10** — Model target fixed at ~32 GB Qwen3-30B-A3B; RAM-dependent
  larger rungs deferred. Cloud framing removed from Missing Link — workloads are
  summarisation and report drafting, done entirely on-premises.
- **2026-08-10** — Research phase run across three streams (RPC internals, model
  performance, provisioning). Target confirmed as Q8_0. Discovered the ~75%
  per-node RPC RAM ceiling, which now sits alongside the pooled-headroom rule as
  a second independent constraint.

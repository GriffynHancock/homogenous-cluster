# Homogenous Cluster

A 7-node CPU-only LLM inference cluster built from surplus school hardware,
serving as a working demonstrator for a blog post about **repurposing idle
organisational compute for private, on-premises inference**.

## The argument

Organisations with data-sovereignty constraints — schools, clinics, councils,
legal practices — often cannot send sensitive data to hosted APIs. They also
often have a room full of decommissioned desktops. The claim is that those two
facts cancel out: pooled system RAM across obsolete machines can hold
frontier-class open-weights MoE models, and while generation is glacial compared
to cloud offerings, **a great many valuable workloads do not need to be fast.**

Two technical facts carry the whole argument:

1. **Active params, not total params, determine speed.** Bytes read per token
   ≈ active params × bits-per-weight. Everything else is storage. A 1T-param
   model with 32B active is tractable on DDR3; a 70B dense model is not.
2. **Pipeline sharding multiplies seats, not speed.** For one request, nodes run
   sequentially — 7 nodes ≈ 1 node with 7× the RAM. With concurrent requests,
   each node works on a different request's layers simultaneously. Aggregate
   throughput scales with node count; per-seat speed stays flat.

The honest pitch is therefore not "slow chatbot" but **"a few seats, each slow,
running something the organisation could not otherwise touch at all."**

## Missing Link

**Missing Link** is the demo we are building: an **asynchronous long-workload
runner** on top of the cluster. Rather than a chat window where slowness is a
defect, it is a job queue where slowness is irrelevant — submit work, collect
results later.

Missing Link is the centrepiece of the argument, not a nice-to-have. It is what
converts "too slow to be useful" into "fast enough for this class of work."

Candidate workloads, in order of rhetorical strength:

- **PII stripping as a gateway** (primary). The local model de-identifies
  sensitive documents so that cloud models *can* safely be used for the fast
  work afterwards. This reframes local inference from a worse alternative to
  cloud into the thing that unlocks cloud safely — and it is where the name
  comes from: the missing link between air-gapped data and capable AI.
- **Overnight document summarisation** of sensitive records.
- **Report writing / drafting** where a multi-minute turnaround is fine.

## Current state

Planning only. No hardware provisioned. See
`docs/superpowers/specs/2026-08-03-homogenous-cluster-design.md`.

## Key decisions already made

- **CPU + system RAM only.** The Quadro P600s (2 GB, Pascal, no FP8/BF16) are
  dropped. They are too small to hold meaningful layers and reintroduce
  significant CUDA/RPC complexity. Revisit only if measured time-to-first-token
  proves unbearable.
- **llama.cpp RPC, not Exo.** Exo's value is heterogeneous discovery and MLX;
  neither applies to 7 identical Debian boxes.
- **Debian 12 headless**, scripted provisioning (not `dd` cloning — disks vary).
- **Tailscale for SSH and the web GUI only.** RPC mesh runs on raw LAN IPs;
  encryption on the per-token hot path is pure loss.
- **Open WebUI** as the chat frontend, lightly skinned.

## Conventions

- Leave **15% memory headroom** in all model-fit calculations. Do not spec
  configurations that fit only marginally.
- Performance claims must come from measurement on the hardware, not
  arithmetic on datasheets.
- **Time-to-first-token is a separate metric from tokens/sec** and matters more
  for document workloads. Prefill is compute-bound; generation is
  bandwidth-bound. Always report both.

# Homogenous Cluster

Seven surplus school desktops, pooled into a CPU-only LLM inference cluster
capable of running a model no single machine in the building could hold — with
no data leaving the premises.

This repository is the working demonstrator for a blog post about repurposing
idle organisational compute for private, on-premises inference.

## Running this on the cluster

This repo is meant to be cloned onto **node 1 (the master)** and driven from
there with Claude Code, rather than operated remotely over SSH.

```bash
# On node 1, after a base Debian 12 install:
sudo apt update && sudo apt install -y git curl
git clone https://github.com/GriffynHancock/homogenous-cluster.git
cd homogenous-cluster
./bootstrap.sh          # installs build deps, Node, Claude Code
claude                  # then say: "read STATUS.md and continue the plan"
```

`bootstrap.sh` is idempotent — re-running it is safe and is the intended way to
repair a half-configured node.

## Reading order for a fresh session

| File | What it gives you |
|---|---|
| `STATUS.md` | **Start here.** Current phase, decisions, open questions, log |
| `CLAUDE.md` | The argument, conventions, and standing constraints |
| `docs/superpowers/specs/2026-08-03-homogenous-cluster-design.md` | The design, and which alternatives were rejected and why |
| `docs/superpowers/plans/2026-08-10-cluster-bringup.md` | 14 tasks, step by step |
| `docs/measurements.md` | Every measured number (created during Task 1) |

The spec records rejected architectures — GPU sharding, Exo, prima.cpp,
distributed-llama, `dd` cloning — with the evidence for each rejection.
Re-proposing them wastes a cycle.

## The short version of the argument

Organisations with data-sovereignty constraints often cannot send sensitive data
to hosted APIs, and often have a room of decommissioned desktops. Those two
facts cancel out.

1. **Active parameters set speed; total parameters set capability.** Bytes read
   per token ≈ active params × bits-per-weight. Everything else is storage. An
   MoE model with 32B active is tractable on system RAM; a 70B dense model is
   not.
2. **Pipeline sharding multiplies seats, not speed.** For one request, nodes run
   sequentially. With concurrent requests, each node works on a different
   request's layers. Throughput scales with node count; per-seat speed does not.

So the pitch is not "slow chatbot" but **a few slow seats running something the
organisation could not otherwise touch at all** — and **Missing Link**, an async
job runner for document summarisation and report drafting, is what makes that
useful rather than apologetic.

## Hardware

| | Per node | × 7 |
|---|---|---|
| RAM | 128 GB DDR4-2400 ECC | ~896 GB |
| Usable model budget | 96 GB (75% RPC ceiling) | **~672 GB** |
| Network | Gigabit ethernet | same switch |
| GPU | Present for display only, unused for compute | — |

CPU model is unconfirmed — ECC implies Xeon. Confirming it, along with memory
channel count, is the first task, because bandwidth determines tokens/sec more
than anything else.

## Status

Planning complete, no hardware provisioned. See `STATUS.md`.

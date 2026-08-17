# Status

**Updated:** 2026-08-18 (early morning — post-merge session)
**Phase:** **N=2. Node 2 joined, provisioned, characterised and serving.** Both
engines distributed fleet-wide. **Upstream bug #26500 gate PASSED across real
machines.** Missing Link **fans out across R inference endpoints, supports queue
control and resumable chunk-level persistence, and has live per-job telemetry**
(193 tests). **A chunk-size benchmark is running on node 2 right now** — see the
box immediately below before doing anything else.
**Repo:** https://github.com/GriffynHancock/homogenous-cluster

**THE REPLICATION MEASUREMENT IS DONE, AND IT PASSES.** Aggregate throughput
across two independent `llama-server`s running gpt-oss-120b: **~1.8× on two nodes,
~90% of linear** (1.86× prefill / 1.77× completion, adjusted for one failed
request; 1.62×/1.55× raw). **The replication-first architecture is validated on
real hardware.** Full detail and caveats in `docs/measurements.md`.

**Two corrections to long-standing assumptions, both from measurement:**

- **The network is 100 Mb/s, not gigabit** (93.8 Mbit/s measured). Both NICs are
  gigabit and the switch is the cap. This **inverts** F23's peer-pull preference
  and qualifies the expert-parallelism comms analysis. See **F28**.
- **`nodes.env` had node 1's RAM as 125629 MB, which `free -m` does not report**
  (128709 on both nodes). Corrected — it sets `--tensor-split` ratios. See F29.

---

## ⚠ TWO THINGS IN FLIGHT RIGHT NOW — read before touching node 2 or restarting Missing Link

1. **A chunk-size benchmark is RUNNING ON NODE 2 as of this session.**
   `llama-server@8080` is up on node 2 and `rpc-server@50052` there is
   **deliberately stopped** for the duration (confirmed:
   `systemctl is-active llama-server@8080 rpc-server@50052` on node 2 returns
   `active` / `inactive`). **Do not start `rpc-server@50052` on node 2, do not
   stop `llama-server@8080` there, and do not run anything else against node 2
   that contends for the CPU** until the sweep finishes. **Whoever picks this up
   next must restart the RPC worker on node 2** (`sudo systemctl start
   rpc-server@50052` on node 2, or re-run `./cluster/install-services.sh`) once
   it is done, or node 2 is silently missing from any future sharding/RPC work.
   The bench agent owns `bench/` and `docs/measurements.md`; its results land
   there, not here.

2. **`missing-link.service` on node 1 is still running the code it loaded
   BEFORE tonight's five merges.** It has been up since 23:28 the evening
   before (`systemctl status missing-link.service` shows `Active: active
   (running) since Mon 2026-08-17 23:28:27`), which predates all five merge
   commits below. It only picks up the new code — fan-out, queue control,
   resumability, the db-race fix, live telemetry, per-workflow guidance — on
   **restart**, and that restart is also the thing that migrates the **live**
   database. Checked directly against `/opt/missing-link/jobs.sqlite`
   (`PRAGMA table_info`): `jobs` genuinely lacks `priority`, `cancel_requested`,
   `seen_at` and `endpoint`; `chunk_summaries` genuinely lacks `model`,
   `instruction`, `prompt_n`, `prompt_ms`, `predicted_n`, `predicted_ms` and
   `completed_at`. **This restart has deliberately NOT been done this session**
   — the fan-out worker loop only starts on process start, and starting it
   while node 2's benchmark owns node 2's `llama-server` would pull node 2 into
   the job queue as an inference endpoint mid-sweep, contaminating the
   benchmark. **Restart `missing-link.service` only after the node-2 benchmark
   above is done and its RPC worker is back**, and expect the first request
   after restart to pay the one-time `_add_missing_columns` migration (fast,
   additive, idempotent — see `db.py`, F-shape guard tested in
   `test_db.py`).

---

## If you are a fresh session

1. **Read `docs/FINDINGS.md`.** **38 findings** from running this on real
   hardware. Several correct the plan or the spec — **and F28 corrects this file
   and F23.** Do not trust the original plan's numbers over these.
2. `docs/measurements.md` is the only place performance numbers may be quoted
   from.
3. `docs/UPSTREAM-PATCHES.md` lists the concrete corrections still to fold back
   into the plan and spec.
4. **`network.md`** (gitignored) has the IPs, node roles and ports for THIS
   deployment. Read it; never commit it. `CLAUDE.md` opens with a full file
   index.
5. **You are the operator.** Run the commands, read the output, record the
   numbers. Never report a step done without having seen its output.

**Everything is built and working on nodes 1 AND 2.** llama.cpp b10369 at
`/opt/llama.cpp/bin` and ik_llama.cpp at `/opt/ik_llama.cpp/bin` **on both**,
models in `/opt/models`, Missing Link in `missing-link/` (coordinator only).
`rpc-server@50052` is **active on both nodes** at `-t 4` as user `cluster`.

**Access:** node 1 is reachable over Tailscale with Tailscale SSH enabled, and a
detached tmux session named `cluster` is waiting on it. **The address is in
`network.md`** (gitignored, site-specific — this file is published):

```bash
ssh -t <coordinator-tailscale-ip> 'tmux new-session -A -s cluster'   # then: claude --continue
```

---

## MERGED THIS SESSION (2026-08-17 late night → 2026-08-18 early morning)

**All three feature agents from the previous entry finished, were independently
verified, and are now on `main` — along with a fourth fix and a rewritten
watchdog found along the way.** Nothing is awaiting merge. Five commits landed,
in order:

| Commit | What | Tests after |
|---|---|---:|
| `151ed32` | **fan-out across R inference endpoints** (`fbb7f4d`) merged together with **queue control + resumability** (`a41666e`, via `e9ca351`) | 145 |
| `9f968a1` | **fixed a concurrency race in `db.init_chunks`** found only by the merge of the two branches above | 145 |
| `8849fd0` | **rewrote the llama-server watchdog** (`5ba25d6`) + added finding **F39** | 145 (watchdog has no Python tests) |
| `33ddc79` (`2c1be61`) | **live telemetry on the job page** + **per-workflow guidance input** | **193** |

`git log --oneline` confirms the chain:

```
33ddc79 Merge branch 'worktree-agent-afe0a29dc429a3445'
2c1be61 feat(ui): live telemetry on the job page, and per-workflow guidance input
8849fd0 Merge branch 'worktree-agent-acde2185fb6ed82e2'
5ba25d6 fix(watchdog): /health cannot judge liveness on this engine; use cgroup CPU progress
9f968a1 fix(db): init_chunks raced under R workers and marked real jobs failed
151ed32 Merge branch 'worktree-agent-a525fd5f262ad64fb' (fan out across R endpoints)
e9ca351 Merge queue control + resumability
fbb7f4d feat(worker): fan out across R inference endpoints
a41666e feat(queue): cancel/reorder/cooperative-stop, and resume from persisted chunk summaries
```

**What each one actually does:**

- **Fan-out (`151ed32`).** `main` runs one `_worker_loop` task per
  `LLAMA_URLS` entry — **job-level** fan-out, deliberately, not chunk-level (see
  Task 2 below for why that split matters and what is still owed).
  Health-aware routing probes `/health` **before** claiming a job, so a dead
  endpoint just stops claiming — costing 1/R of throughput — instead of
  claim-then-immediately-fail. Per-endpoint status is surfaced on `/health` and
  the index page.
- **Queue control + resumability (`151ed32`, via `a41666e`).** Cancel a
  pending job; reorder pending jobs (`POST /jobs/reorder`, applied on the next
  claim); cooperative stop of a running job (checked between chunks and once
  before reduce — it **cannot** interrupt an in-flight HTTP call, there is no
  llama.cpp cancellation endpoint, so a stop lands after the current chunk).
  **Chunk summaries now persist as each chunk completes** (`_persist_chunk` in
  `worker.py`), not only after the whole document finishes — the bug that made
  a 40-chunk job dying at chunk 39 restart from zero. **A resume is trusted
  only if the recorded model AND the recorded instruction both match** what is
  currently serving; on either mismatch it discards and restarts rather than
  mixing outputs from two different runs. Notification is a `seen_at` flag +
  unseen-jobs banner (`POST /jobs/ack`).
- **`init_chunks` race (`9f968a1`).** The lazy check-then-`ALTER TABLE`
  migration ran on every chunk write; safe with one worker, but main now runs
  R concurrent workers and the loser of the race got `OperationalError:
  duplicate column name: model`, which `run_one`'s broad except turned into a
  **failed** job — a whole night of work marked dead by a migration that had
  in fact succeeded. Reproduced 20/20 before the fix, 0/20 after (8 full-suite
  runs, 0 failures). `init_db` is now the single complete migration entry
  point for `jobs`, `chunk_summaries` and `batch_documents`; the six lazy
  per-operation calls are gone. **Two agents (the fan-out one and the
  queue-control one) found the shape of this independently** — neither branch
  was wrong on its own, the race existed only in their combination, which is
  why neither branch's own tests caught it.
- **Watchdog rewrite (`8849fd0`, F39).** `/health`, `/slots` and `/metrics`
  all post onto the **same task queue** `update_slots()` drains for token
  generation, so none of them is an out-of-band signal — the F36 watchdog
  restarted a perfectly healthy server mid-prefill on 2026-08-17 and destroyed
  a job with 10m55s of completed work. **`/health`'s own slot counters read
  `n_idle_slots=4 n_processing_slots=0` while the server was busiest prefilling
  a document** — `3 idle / 1 processing` only showed up during the (much
  shorter) generation phase. The rewritten watchdog instead reads the unit's
  own `CPUUsageNSec` (cgroup-scoped, immune to other load on the box) and only
  restarts after 300 s of unbroken **silent AND CPU-flat** evidence. Read F39
  in `docs/FINDINGS.md` directly; it is the authoritative account and this
  paragraph is a summary of it, not a replacement.
- **Live telemetry + per-workflow guidance (`33ddc79`).** The job page now
  shows chunk N of M, separate prefill/generation tok/s **derived from the
  server's own `timings`, never wall-clock** (F17's lesson applied), a
  three-tier labelled ETA (measured this job / measured this model /
  estimated), and which endpoint actually ran the job — persisted to a new
  `jobs.endpoint` column so a **failed** job still shows which node it died
  on. Per-workflow guidance is a textarea **or** a file upload, extracted via
  `extract.py`, **refused, not silently truncated**, against a measured size
  cap (`check_instruction_length` in `worker.py` — see "Owed after the merge"
  below, this is what closes that item). The resume check from the previous
  bullet was extended in this commit to require the **instruction** to match,
  not just the model.

**Verified, not just counted:** 193 tests pass over the current tree
(`.venv/bin/python -m pytest tests/ -q`, 8 seconds, 0 failures) — but per
`CLAUDE.md`'s own standing rule, a test count is not evidence of working
software on its own. The concurrency fix specifically was verified by
reproduction (20/20 failures → 0/40), not merely by a passing suite, and the
`init_db` migration was run twice against a **copy of the live**
`/opt/missing-link/jobs.sqlite` — 8 job ids preserved, rows identical, second
run a no-op.

**Open item the UI agent raised and nobody has answered:** batch review rows
and `jobs.document` are kept **forever**. For sensitive documents that is a
policy decision, not a default to inherit.

**"Owed after the merge" from the previous entry, updated:**

- ~~Add a slot-budget guard~~ — **done, in a different form than described.**
  `worker.check_instruction_length()` asserts the guidance text fits inside
  `N_CTX_SLOT - CHUNK_TOKENS - MAP_MAX_TOKENS - wrapper` and fails loudly
  rather than silently truncating. It guards the instruction budget, not a
  live `/props` read of `n_ctx`/`total_slots` — that live-introspection form
  was not built, so a future `-c`/`--parallel` change that shrinks
  `N_CTX_SLOT` still needs to be caught by hand, the same way the original
  `-c 16384` regression was.
- `WORDS_PER_TOKEN = 0.70` is **under active measurement on node 2 right now**
  (see the box at the top of this file) — **not** merely owed. Do not lower it
  by hand while that sweep is running; it will land a real value in
  `docs/measurements.md` when it finishes, and `worker.py`'s own comments
  already flag every place `MAX_INSTRUCTION_WORDS` derives from it so the
  correction propagates from one constant.

---

## Completed this session (was "in flight")

**The 65 GB model copy to node 2 finished and was verified byte-identical**
(md5 `c859460f5dab66969a9268e2eb551b6d` both ends, 1:33:39 at 11.09 MB/s), and the
replication measurement ran on it. Both nodes now hold gpt-oss-120b.

**Re-run the measurement with `./bench/replication-bench.sh`.** It stops
`rpc-server` fleet-wide, starts one independent `llama-server` per node, drives
concurrent load, and restores nothing — **restart the RPC workers afterwards** with
`./cluster/install-services.sh` or `sudo systemctl start rpc-server@50052`
(this session left them running).

<details><summary>Original transfer instructions (kept for node 3+)</summary>

**A 65 GB `rsync` of `gpt-oss-120b-F16.gguf`**, at a measured 11.18 MB/s (~97 min).
Log: `/tmp/rsync-gptoss.log`.

```bash
# is it still going / did it finish?
pgrep -af 'rsync -a --partial' ; tail -c 200 /tmp/rsync-gptoss.log
ssh debian1@10.10.0.39 'ls -lh /opt/models/gpt-oss-120b/'   # want 65369017728 bytes
# if interrupted, it resumes -- --partial --inplace, so just re-run:
rsync -a --partial --inplace --info=progress2 \
  /opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf \
  debian1@10.10.0.39:/opt/models/gpt-oss-120b/
```

**Do not benchmark either node while this runs.** It reads 65 GB off node 1's
disk, burns CPU on SSH crypto at both ends, and streams 65 GB through node 2's
page cache — every one of which perturbs an inference measurement. It also
saturates the link, which took RTT from 0.827 ms to 9.544 ms (F28).

**When it completes, do this — it is the measurement the architecture rests on:**

```bash
# Independent llama-server per node. NO --rpc, NO --tensor-split: replication,
# not sharding. ik_llama.cpp, because prefill is 79% of document wall-clock (F27).
# -t 4 = PHYSICAL cores. --parallel 4, never 8.
/opt/ik_llama.cpp/bin/llama-server -m /opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf \
  -t 4 -c 4096 --parallel 4 --host 0.0.0.0 --port 8080   # on BOTH nodes

# Then: single-node baseline, then both nodes concurrently, and compare
# AGGREGATE tokens/sec. Expect ~2x. Record in docs/measurements.md.
```

Watch for: ik_llama.cpp's CLI **differs from mainline** (`-no-cnv` does not
exist, and there is **no `--no-webui`**), and it reports gpt-oss as `?B` rather
than `120B` — output is still correct, but **re-verify coherence per model** (F27).
Vary the prompt between runs or you measure the prompt cache (F17).

</details>

---

## NEXT TASKS, in order

### 1. ~~Join node 2~~ — DONE 2026-08-17, except the replication measurement

**Completed and verified by output:**

| Step | Result |
|---|---|
| Key auth + NOPASSWD sudo on node 2 | working (was already in place) |
| `harden-ssh.sh 10.10.0.39` | **key-only, passwords refused** |
| Characterisation before assuming anything | **node 2 is a twin of node 1** (F29) |
| STREAM triad on node 2 | **27.9 GB/s** vs node 1's 28.4 same-day control |
| `nodes.env` populated with MEASURED values | node1 + node2, both 128709 MB / 4 cores |
| `setup.sh node2` | hostname, identity, swap off, THP, service account |
| `distribute.sh` (version + libc + ISA) | **node2 ok**, 24 MB / 35 files, exec-tested |
| `distribute.sh /opt/ik_llama.cpp` | **node2 ok** — was impossible before (F32) |
| `install-services.sh` | `rpc-server@50052` on both, **RPC_THREADS=4**, 0 restarts |
| `two-node-smoke.sh 10.10.0.39` | **PASS — #26500 does not fire** (F31) |
| Aggregate replication measurement | **STILL OWED** — see "In flight" above |

**Five latent bugs were found and fixed on the way, every one of them specific to
the 1 → 2 transition** (F30): the `User=cluster` account nothing created, the
coordinator being unable to SSH to itself, host-key regeneration invalidating
`known_hosts`, `machine-id` regeneration orphaning the journal, and a 14-hour
timezone divergence. Plus the DIMM reporter printing an empty slot label, and
F21 recurring in the smoke test as a **false negative on the go/no-go gate**.

**Node 3 will be much cheaper than node 2 was.** The fixes above are in
`setup.sh`/`distribute.sh`, so the path is: run `join-node.sh`, install the key,
`harden-ssh.sh`, characterise + STREAM, add to `nodes.env`, `setup.sh node3`,
both `distribute.sh` invocations, `install-services.sh`. **Run `ssh-keygen -R`
after `setup.sh`** — see F30 item 3.

### 1b. Owed follow-ups from this session

- **A rigorous two-node sharding A/B.** The −47% generation figure is from a
  single short chat request, not `llama-bench`. Run
  `llama-bench` over the RPC devices **on an idle link** and record it properly.
- **Re-check `cluster/models.sh pull`.** F28 inverts its peer-over-internet
  preference at 11.7 MB/s vs 21 MB/s from HuggingFace.
- **Get a gigabit switch (~$20–30).** Uplink it to the existing 100 Mb port;
  node↔node then runs at gigabit. Preferred over daisy-chaining, which needs
  N−1 ports per node and does not scale past 2. Slots 2 and 3 are free on the
  P510 if a NIC is wanted instead, but the switch is the better buy.
- **`node1`'s hostname is `debian1`.** Cosmetic, but inconsistent with
  `nodes.env`. Left alone deliberately — renaming mid-session risked disturbing
  Tailscale's registration for no benefit.
- **Remote access is now set up:** Tailscale SSH enabled on node 1
  (`tailscale set --ssh`, revert with `--ssh=false`), and a detached tmux session
  named `cluster` exists. Reattach with
  `ssh -t <coordinator-tailscale-ip> 'tmux new-session -A -s cluster'`, then
  `claude --continue`. **Address is in `network.md`, not here** — this file is
  published. (Note: `docs/measurements.md:13` already carries that IP from commit
  `e908e33`, predating this session. Worth scrubbing if the repo goes public.)

### 1c. Original node-2 procedure, retained for node 3+

**The 1 → 2 transition is where all the risk lives.** Everything that can go
wrong across a fleet appears at N=2 and nothing new appears at N=10.

**Node 2 is at `10.10.0.39`** (see `network.md` — gitignored, site-specific).
Debian and `sshd` are up. It has the same admin password as node 1; install the
coordinator's key, then harden to key-only.

Note an earlier ARP sweep saw `10.10.0.35` answering — **that is something else
on the DMZ, not node 2.**

```bash
# 1. Characterise it -- do NOT assume it matches node 1
ssh <node2-ip> 'lscpu -p=Core,Socket | grep -v "^#" | sort -u | wc -l; free -m; lsblk -d'
#    and run the STREAM triad -- core count is a BANDWIDTH spec (F12)

# 2. Add node 2 to provisioning/nodes.env with MEASURED values FIRST.
#    Both distribute.sh and install-services.sh read their target list from it;
#    with only node1 present, distribute.sh exits 0 with "nothing to distribute"
#    -- a silent no-op that looks like success, and step 3 then fails.
$EDITOR provisioning/nodes.env      # "node2 <ip> <ram_mb> <physical_cores>"

# 3. Provision, distribute, verify
sudo ./provisioning/setup.sh node2
./provisioning/distribute.sh        # asserts version, libc AND ISA
./cluster/install-services.sh

# 4. Two-node RPC smoke test (gate for upstream bug #26500, F2/F22)
./bench/two-node-smoke.sh <node2-ip>

# 5. THE measurement that matters: replication, not sharding.
#    Run an independent llama-server on each node, measure AGGREGATE throughput
#    on 2 nodes. Expect ~2x. This validates the R x single-node scaling model
#    the whole architecture now rests on.
```

**`nodes.env` values must be MEASURED, not assumed** — LAN IP, RAM MB and
**physical** cores. Do not leave placeholders, and do not copy node 1's values.

### 2. Missing Link fan-out across R endpoints — DONE (job-level), two sub-items still owed

**The main outstanding code change from the previous entry landed** (see
"MERGED THIS SESSION" above, `151ed32` and `33ddc79`). `missing_link/worker.py`
no longer targets a single `base_url`; `app.py`'s lifespan starts one
`_worker_loop` task per `LLAMA_URLS` entry, each claiming jobs independently
against `db.claim_next_pending` (already atomic under concurrency —
`BEGIN IMMEDIATE`, F20). Concretely, against the original list:

- ~~`nodes.env` grows a list of inference endpoints~~ — **done**,
  `LLAMA_URLS`, kept separate from the RPC endpoint list.
- ~~`run_forever` becomes R concurrent workers~~ — **done**, one
  `_worker_loop(base_url)` task per endpoint.
- ~~Health-check endpoints and route around a dead one~~ — **done**,
  `_probe_endpoint` runs **before** claiming, not after, so a dead endpoint
  just stops claiming rather than claim-then-fail; per-endpoint status is on
  `/health` and the index page.
- ~~Keep the task profile separable from the queue~~ — **holds**, unchanged.
- ~~Carry provenance through the map step~~ — **done** (landed earlier,
  `7ceb799`, and unaffected by tonight's merges). `chunk_summaries` records
  `start_char`/`end_char` per chunk; confirmed present in the live
  `/opt/missing-link/jobs.sqlite` schema.

**NOT done — two real gaps remain, and neither landed tonight:**

- **Chunk-level fan-out within one document.** `_worker_loop`'s own docstring
  is explicit about this: *"Job-level, not chunk-level: chunk-level fan-out
  only reduces the wall-clock of a SINGLE document (same total work, spread
  wider), while job-level fan-out is what multiplies aggregate throughput...
  Chunk-level is deliberately out of scope here."* So a single large document
  submitted alone still only uses one endpoint's worker at a time; the rest of
  the fleet sits idle until there is a second job to claim. See
  `DESIGN-NOTES.md` G for why the two granularities optimise different
  metrics (throughput vs one-document latency) and should be selected by
  queue depth, not built as one thing.
- **A retrieval-based task profile.** `CLAUDE.md` lists medium-horizon search
  and Q&A as a target workload, and for that workload RAG is the correct
  primitive — it should arrive as a task profile plugged into the
  prompts/chunking seam, not as a second system. No retrieval or embedding
  code exists anywhere in `missing_link/` yet. See `DESIGN-NOTES.md` E,
  concessions 1 and 2.

### 3. Resolve the Model B decision

**Research completed 2026-08-17 and now recorded — read before deciding:**
`docs/MODEL-SELECTION.md` gained (a) **Meta's whole open-weight line assessed** —
Llama 4 Scout is the best-evidenced option (7.7% Vectara vs gpt-oss's 14.2%, fits
S=1) but costs **~3.5× throughput** because it has 17B active against gpt-oss's
5.1B; Maverick is S=2 at N=2 and rejected; Muse Glimmer is dense 27.8B-active with
**no Vectara entry**; Muse Spark has no weights. And (b) **the agent-appliance model
choice** — the Qwen3-4B already on disk is good enough (BFCL-v3 57.6) with thinking
forced off. `docs/DESIGN-NOTES.md` I rejects the DeepSeek Harness, and H's addendum
records the **UD-quant capacity trap: a UD quant is a size tier heavier than its
letter suggests, so S=1 maths must use real file sizes.**


**Do not fetch Kimi K2 until this is settled.** K2-Instruct has the worst
REPORTED hallucination rate of any model checked (17.9%, Vectara
leaderboard — not verified here), against a project
requirement of faithfulness over style. GLM-4.6 has identical active params
(same speed), 9.5% hallucination, MIT licence, and needs 189 GB instead of
546 GB — **which removes the coordinator-disk blocker entirely.** See F25 and
`docs/MODEL-SELECTION.md`.

**A constraint nobody crossed against N until 2026-08-17: the Model B choice
implies a MINIMUM FLEET SIZE, and at N=2 it destroys the replication win.**

F25 compared the candidates on faithfulness, disk and active params. None of that
touched `S` — how many nodes one copy needs — so the interaction with fleet size
stayed invisible:

| Model | Size | S | **R at N=2** | What you actually get |
|---|---:|---:|---:|---|
| **gpt-oss-120b** | 65 GB | **1** | **2** | **replication, ~2x aggregate** |
| GLM-4.6 IQ4_XS | 189 GB | 2 | **1** | **no replication at all**, plus RPC over 100 Mb |
| DeepSeek-V3.2 | 363 GB | 4 | **0** | **does not run at N=2** |
| Kimi K2 IQ4_XS | 546 GB | 6 | **0** | does not run at N=2 |

(S = `ceil(size / (131.8 GB x 0.75))`, i.e. against the 98.9 GB per-node
guideline.)

**So GLM-4.6 — the faithfulness-led favourite — would take the fleet from R=2 to
R=1 and add sharding overhead on a 100 Mb link (F28).** Plausibly slower than a
single node running gpt-oss. It only starts paying at about **N=6** (R=3).

**Consequences:**

- **Do not download 189 GB over an 11 MB/s link before deciding this.** That is
  ~4.7 h per node, and every node needs its own copy under replication.
- **The faithfulness gain must be weighed against a factor-of-R throughput loss**,
  not treated as free. `docs/EVALUATION.md` argues the leaderboard should pick the
  model; it cannot price this in, because it knows nothing about our fleet.
- **This is the "largest model with S = 1" rule from `CLAUDE.md` biting for real.**
  At N=2 that rule selects gpt-oss-120b, and the 1 -> 2 threshold crossing costs
  exactly the factor of N the rule warns about.
- **It also means sharding stays untested until a model needs it.** Nothing on the
  fleet does; S=1 holds to ~99 GB. See the sharding caveat in
  `docs/measurements.md`.

Two research gaps that would change the answer:

- **GLM-5 / 5.1 / 5.2 active-parameter count** is not published anywhere found.
  That single number decides whether the strongest MIT-licensed open reasoner is
  usable here.
- **Finix S1 32B** has the best listed hallucination rate (1.8%) but is
  uncharacterised — architecture, active params, GGUF availability all unknown.

### 4. Faithfulness evaluation — REFRAMED 2026-08-17. Do NOT try to reproduce the leaderboard.

Task 14 in the plan, and F25 argued it should **gate model selection rather than
validate it afterwards.** That is still right, but the *design* was wrong, and
the reason is statistical.

**We cannot measure an absolute hallucination rate here, and should not try.**
To distinguish the two leading candidates on their reported rates — GLM-4.6 at
9.5% vs Kimi K2 at 17.9% — a two-proportion test at 80% power needs
**~260 documents per model**:

```
n = (1.96 + 0.84)^2 x [0.095(0.905) + 0.179(0.821)] / (0.084)^2 ~= 259
```

Separating GLM-4.6 (9.5%) from GLM-4.5-Air (9.3%) would need **tens of
thousands**. Vectara used 7,700+. A local run over a few dozen documents cannot
rank models, and burning cluster-nights to half-reproduce someone else's
leaderboard is a poor trade. **Use the leaderboard for ranking. It is a better
instrument than anything we can build.**

**Measure instead the thing the leaderboard structurally CANNOT tell us.** F25
flagged it as INFERRED and it is the project's real exposure: our pipeline is
**map-reduce**, so a fabrication in a chunk summary becomes *source material*
for the reduce step, where it is indistinguishable from genuine content. Errors
do not merely persist, they get laundered.

That is a **paired, within-pipeline** comparison — same documents, same model,
single-pass vs map-reduce — which is far more statistically efficient than
comparing absolute rates across models, because document-level variance cancels.
**A few dozen documents can detect it**, where hundreds could not rank models.

So the eval becomes two much cheaper questions:

1. **Does map-reduce amplify fabrication relative to single-pass, on our own
   documents?** Paired design, ~30–50 documents, AlignScore/SummaC (RoBERTa-scale,
   fine on CPU). If yes, that is an architectural finding about the *pipeline*,
   independent of model choice, and it may argue for a verification pass in the
   reduce step.
2. **Is the chosen model's faithfulness acceptable at all on our material?** A
   qualitative acceptance check, not a ranking. Small n is fine.

**Neither requires 260 documents, and neither competes with the leaderboard.**

### 4b. Try the popular document-summary pipelines as a BASELINE

Research was done on this (see "Summarisation pipelines" below) and its
conclusion was **nothing mature exists to adopt** — private-gpt, Kotaemon and
localGPT are RAG-QA systems that retrieve top-k chunks, the *opposite* of reading
a whole document. So running those would mostly re-confirm a shape mismatch.

**The one worth actually running is LlamaIndex `tree_summarize`**, the closest
building block, as a **baseline to measure Missing Link against.** The project
implicitly claims Missing Link is better than reaching for the obvious library;
that claim is currently untested.

**Set the timeouts explicitly before running it.** LlamaIndex and LangChain both
default to a 60 s-class timeout, and against a backend where one chunk takes
minutes that is a retry storm, not a summary — it will look like the library
"cannot handle" the cluster when in fact it was never configured for it.
**Corrected 2026-08-18:** research into the underlying OpenAI/Anthropic SDKs
both libraries build on found `max_retries` defaults to **2**, not the 3–6
originally assumed here. The retry-storm risk is still real: each retry
re-waits the full timeout, so even 2 retries at a naive short timeout is tens
of seconds of silent waiting against this backend. Compare on wall-clock
**and** on faithfulness, using the paired design above.

### 5. Smaller, still open

- Adopt `ik_llama.cpp` for the document workload (+22% end-to-end) — its CLI
  differs from mainline, so scripts need adapting, not just re-pointing.
- `-fa` flash attention and `-ctk q8_0` KV quantisation: untested, cheap.
- Open WebUI (plan Task 9).
- Measure the real per-node RAM ceiling now that the 75% rule's citation is
  disproven (F1). At 85% the pooled budget rises ~13%.

---

## Joining a node

**Use the same username as the coordinator (`debian1`).** Not strictly required,
but the systemd unit, `scp`/`rsync` targets and every `ssh` in the scripts all
assume it. A different username means editing all of them.

**Generate these with `./provisioning/join-node.sh` on the coordinator** rather
than copying from here — it substitutes the real key and LAN IP, so it cannot go
stale. The listing below is what it prints today.

On the new machine:

```bash
# 1. Same username as the coordinator
sudo adduser debian1                     # skip if it already exists

# 2. Passwordless sudo. Append to /etc/sudoers, NOT a sudoers.d drop-in --
#    sudo is last-match-wins, and a later "(ALL:ALL) ALL" line silently
#    overrides a NOPASSWD rule in sudoers.d. This bit us on node 1 (F9).
echo "debian1 ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

# 3. SSH server
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh

# 4. Authorise the coordinator's key
sudo -u debian1 mkdir -p /home/debian1/.ssh
sudo -u debian1 tee -a /home/debian1/.ssh/authorized_keys <<'KEY'
<PASTE THE COORDINATOR'S PUBLIC KEY -- run ./provisioning/join-node.sh to print it>
KEY
sudo chmod 700 /home/debian1/.ssh
sudo chmod 600 /home/debian1/.ssh/authorized_keys

# 5. Report the LAN IP
ip -br addr | grep -v LOOPBACK
```

Coordinator is `10.10.0.34/24`, gateway `10.10.0.254`. Everything after this the
coordinator can do over SSH.

**Then harden it — key-only, once keys are confirmed working:**

```bash
./provisioning/harden-ssh.sh <node-ip>
```

It verifies key auth from the coordinator **before** disabling password auth,
validates the sshd config with `sshd -t` before restarting, and re-checks
reachability afterwards. If any check fails it reverts and changes nothing —
locking yourself out of a headless box in a locked cupboard is the failure mode
this exists to prevent.

---

## Where things stand

**Node 1 (coordinator, user `debian1`, `10.10.0.34`)** and **node 2 (worker /
second replica, `10.10.0.39`)** — both provisioned, both serving.

| Item | node 1 | node 2 |
|---|---|---|
| llama.cpp | b10369 (`6e62ba53`) at `/opt/llama.cpp/bin` | **same, exec-verified** |
| ik_llama.cpp | `8337e4cd` at `/opt/ik_llama.cpp/bin` | **same, shipped 2026-08-17** |
| `rpc-server@50052` | **active, `-t 4`, user `cluster`, 0 restarts** | **`active` normally, but STOPPED right now — chunk-size benchmark in progress, see the box at the top of this file. Restart it when the sweep finishes.** |
| Models | Qwen3-4B (2.4 GB), gpt-oss-120b F16 (65 GB); **Qwen3-Next-80B UD-Q8_K_XL 87 GB downloading, 26%** | **gpt-oss-120b, md5-verified** |
| SSH | password auth still ON (no key installed until this session) | **key-only, hardened** |
| Disk free | 367 GB | 437 GB |
| Missing Link | job store + worker + web API, fan-out across R endpoints, queue control (cancel/reorder/cooperative stop), resumable per-chunk persistence, live telemetry, per-workflow guidance — **193 tests**. Running on node 1, but **still serving the code from before tonight's five merges** — see the restart note at the top of this file | n/a (coordinator only) |
| Phase 0 gate | **PASSED** | — |
| #26500 gate | **PASSED across both machines** (F31) | — |

**`rpc-server` IS now running on both nodes.** The unit is *templated*, so the
name is `rpc-server@50052.service` — plain `systemctl status rpc-server` reports
"could not be found" and looks like a broken install. Use:

```bash
systemctl status rpc-server@50052
ssh debian1@10.10.0.39 'systemctl status rpc-server@50052'
```

It runs as the **`cluster`** system user, whose account and tensor-cache
directory (`/var/lib/cluster/.cache/llama.cpp/rpc`) nothing created until this
session — see F30.

**Not done:** nodes 3+, Model B decision, Open WebUI, evaluation harness,
chunk-level fan-out within one document, a retrieval task profile, watchdog
moved fully off-cluster. The aggregate replication measurement is done (see
the top of this file); a chunk-size benchmark is in flight on node 2 now.

---

## The web UI is RUNNING (2026-08-17)

**Missing Link's own UI, not Open WebUI.** It already existed — `base.html`,
`index.html`, `job.html`, plus submit/job-view/result/health/api routes — and is
now serving. Open WebUI (plan Task 9) is a *chat* frontend and is still not
deployed; this is the *job queue* UI, which is what the async workload needs.

```bash
./missing-link/start-ui.sh http://127.0.0.1:8081 8000   # backend url, port
```

Reach it over Tailscale at `http://<coordinator-tailscale-ip>:8000`
(address in `network.md`). Submit a document, watch the job go
pending -> running -> done, read the summary.

**It needs a llama-server to point at.** `start-ui.sh` takes the backend URL as
its first argument. Under replication there are R of them and Missing Link still
targets ONE — that is the fan-out work in task 2 below, and the reason
`INFERENCE_ENDPOINTS` already exists in `nodes.env`.

**Verified working end-to-end 2026-08-17:** a records-retention memo submitted
over HTTP was claimed by the background worker and processed against
gpt-oss-120b. Form field is `document` (not `text`), `kind` is one of
`summarise` / `report` / `qa`.

---

## The agent appliance — constraint RELAXED by the operator (2026-08-17)

`CLAUDE.md` says the appliance must be **separate hardware**: "a monitor that
shares the cluster's failure modes is not a monitor." **The operator has relaxed
this**: it may be a small reserved VM, or reserved capacity on the coordinator.

**Record what that trades away, so nobody rediscovers it during an outage:** an
appliance hosted on the coordinator **cannot report that the coordinator is
down**, and shares its RAM pressure, its kernel, and its power feed. The
practical split that keeps most of the value:

- **On-node is fine for the ACTIVE work** — queue triage, requeueing stranded
  jobs, batch assembly, endpoint health-checking, routing around a dead worker.
  All of that only needs to run when the coordinator is up anyway.
- **Liveness reporting still wants an outside observer**, even a trivial one (a
  cron'd curl from a laptop or phone). Otherwise the failure mode is silence, and
  silence is indistinguishable from "no news is good news."

**Sizing, from research 2026-08-17:** the recommended triage model is a **4B
dense at ~11 tok/s** (see below), i.e. ~2.5 GB resident. That fits reserved space
on a 131.8 GB node trivially and does not threaten the S=1 budget.

---

## Deliverables, in order

1. **The cluster (now).** N nodes doing real work on real sensitive documents.
2. **A Claude Skill (later).** Assess → generate → operate. Do not start it
   before the cluster produces measurements; its whole value is that its advice
   is measured rather than arithmetic.

---

## Hardware — nodes 1 AND 2 MEASURED, 3+ unknown

| | Node 1 | **Node 2** |
|---|---|---|
| CPU | Xeon E5-1620 v4 — **4 cores / 8 threads** | **identical** |
| ISA | AVX2, FMA, F16C. **No AVX-512** | **identical** |
| RAM | **131.8 GB** — 4 × 32 GB DDR4-2400, all four channels | **identical, confirmed by `dmidecode`** |
| **Achievable bandwidth** | **28.4 GB/s** (STREAM, 2026-08-17 re-run; 28.2 on 08-12) | **27.9 GB/s** |
| Disk | NVMe 477 GB — **367 GB free**, re-check `df -h /` | NVMe 477 GB — **437 GB free** |
| Board | LENOVO 30B2S2E800 (ThinkStation P510) | **identical** |
| Network | **100 Mb/s** (not gigabit — F28), `10.10.0.34/24` on `eno1` | **100 Mb/s**, `10.10.0.39/24` |
| Hostname | `debian1` (inconsistent with `nodes.env`, left alone) | `node2` |

**Node 2 is a bandwidth twin of node 1 — 1.8% apart, within run-to-run noise**
(F29). That is a *measured* result, so `--tensor-split` by RAM is also correct by
bandwidth here. **Do not assume it for nodes 3+**; re-run STREAM per node.

**The bandwidth gap is the CPU, not the memory.** Four cores cannot generate
enough memory-level parallelism to saturate a quad-channel bus — that needs
~8–14 cores on Broadwell. Uncore is already at its 2800 MHz ceiling and
energy-perf-bias is 0, so **there is no BIOS lever.** (F12) Node 2 reproducing
27.9 GB/s on identical silicon is independent confirmation.

**The network is the newly-discovered constraint.** 93.8 Mbit/s measured, both
NICs gigabit-capable, switch is the cap. Replication is unaffected; sharding and
model distribution are not. See **F28**.

---

## Scaling model — N-agnostic

- **S** = nodes needed for one copy = `ceil(model_size / usable_RAM_per_node)`
- **R** = independent copies = `floor(N / S)`
- **Aggregate throughput ≈ R × single-node throughput**

| Route to throughput | Measured | Notes |
|---|---:|---|
| **Replication (R copies)** | **≈ R×** | no RPC, linear, node failure costs 1/R |
| Batching (`--parallel 4`) | 1.79× | MoE-limited; **8 is worse than 1** |
| Sharding (S nodes per copy) | **1×** | buys capacity only; −39% prefill |

**Prefer the largest model with S = 1.** The size at which S goes 1 → 2 is the
most consequential number in model selection: crossing it costs a factor of N.

---

## Measured results (full detail in `docs/measurements.md`)

| Measurement | Result |
|---|---|
| Memory bandwidth (STREAM), node 1 | **28.4 GB/s** at 4 threads (28.2 on 08-12) |
| Memory bandwidth (STREAM), **node 2** | **27.9 GB/s** at 4 threads — **a twin** (F29) |
| Optimal threads | **4 = physical cores.** `-t 8` is 26% slower. **Reproduced on node 2** |
| Generation efficiency, dense | **~99% of STREAM** |
| Generation efficiency, **sparse MoE** | **~61% of STREAM** |
| RPC overhead, **localhost** | generation **−5.2%**, prefill **−39.4%** |
| RPC overhead, **two real machines** | generation **≈ −49%** ⚠ indicative only, not `llama-bench` (F28) |
| **LAN throughput** | **93.8 Mbit/s = 11.7 MB/s.** NOT gigabit (F28) |
| **LAN RTT** | **0.827 ms idle, 9.544 ms saturated** — 11.5× bufferbloat |
| gpt-oss-120b, single node | pp2048 **16.08**, tg128 **6.05** t/s |
| Qwen3-4B, single node | pp2048 28.33, tg128 **11.49** t/s |
| TTFT @ 2214 tokens (4B model) | **89 s** |
| Batching (MoE) | 1.79× at batch 4; **collapses at 8** |
| ik_llama.cpp vs mainline | **prefill +52%**, generation −14%, **net +22%** |
| **Aggregate throughput, 2 replicas** | **~1.8× (1.86× prefill / 1.77× completion, adjusted; 1.62/1.55 raw)** |

**Sizing rule, validated both ways:**
`tok/s ≈ effective_bandwidth / (active_params × bytes_per_weight)`,
where effective bandwidth is **28.2 GB/s dense, 17.3 GB/s sparse MoE**.
Predicts Qwen3-4B at 11.31 (measured 11.49) and gpt-oss at 6.4 (measured 6.05).

---

## Open questions

- [x] ~~**Does replication actually deliver R×?**~~ **YES — ~1.8× at N=2, ~90% of
      linear**, measured 2026-08-17. Prefill scaled better than generation
      (1.86× vs 1.77×), which is the favourable direction since prefill is ~79%
      of document wall-clock.
- [ ] **Clean re-run of the replication measurement.** n=1, and one request of
      eight failed on F21, so the raw figures understate it. Raise `max_tokens`
      and use more requests per endpoint before treating 1.8× as a constant.
- [ ] **`enable_thinking:false` does NOT work on gpt-oss-120b** (harmony format).
      Investigate `reasoning_effort` instead. Verify per model, never assume.
- [x] ~~Nodes 2+ hardware~~ — **node 2 measured, a twin of node 1** (F29).
      Nodes 3+ still unknown; core count is a bandwidth spec.
- [ ] **Rigorous two-node sharding A/B.** The ≈−49% generation figure is one
      short chat request, not `llama-bench`. Must be re-run on an **idle link**.
- [ ] **Does the 61% MoE efficiency generalise?** Measured on gpt-oss (128
      experts, top_k=4). Kimi K2 has 384 at top_k=8 — a more scattered gather
      could be worse.
- [ ] **Real per-node RAM ceiling** now that the 75% citation is disproven (F1).
- [ ] **Model B**: GLM-4.6 vs DeepSeek-V3.2 vs Kimi K2 — faithfulness-led.
- [ ] **GLM-5 active params** — unpublished; decides a leading candidate.
- [ ] **Finix S1 32B** — best listed faithfulness (1.8%), uncharacterised.
- [ ] `-fa` and `-ctk q8_0` — untested.
- [ ] **Does `models.sh pull` still prefer a peer?** F28 inverts that choice at
      11.7 MB/s LAN vs 21 MB/s from HuggingFace.
- [ ] **Is the 100 Mb cap the cable or the switch port?** Both NICs advertise
      1000baseT/Full. A gigabit switch settles it and is ~$20–30.

---

## Rejected — do not re-propose

- **Exo** — MLX-only engine; Linux CPU is "Planned" tier; no GGUF.
- **prima.cpp** — no MoE; original repo 404s.
- **distributed-llama** — all-reduce per layer per token over gigabit; own
  weight format; abandons the GGUF/Open WebUI stack.
- **GPU sharding / GPU prefill** — the Quadro P600's 2 GB cannot hold meaningful
  layers of any target model.
- **`dd` cloning** — disks vary in size and type.
- **Kimi K3** — real (2.78T total) but **104B active** ≈ 0.3 tok/s here, and
  1.5 TB at Q4. Newer and larger is actively worse (F26).
- **Expert parallelism** — genuinely the largest theoretical win (~4×), and
  communication would not kill it, but llama.cpp has no such mode; building it
  is a new inference engine. Recorded in `docs/DESIGN-NOTES.md` A.

---

## Research findings retained from earlier sessions

### Summarisation pipelines

- **Nothing mature exists to adopt or fork.** No self-hosted project does "point
  at llama.cpp, queue overnight, summarise long documents." Popular tools
  (private-gpt, Kotaemon, localGPT) are RAG-QA systems that retrieve top-k
  chunks — the *opposite* of reading a whole document. Missing Link fills a real
  gap.
- **If reaching for a library**, LlamaIndex's `tree_summarize` is the closest
  building block. But **both LlamaIndex and LangChain default to a 60 s timeout**
  — against a multi-minute backend that is a retry storm, not a summary. Set
  timeouts explicitly. **Corrected 2026-08-18:** `max_retries` defaults to
  **2** in the underlying SDKs, not the 3–6 originally stated here; the
  retry-storm risk itself is still real because each retry re-waits the full
  timeout.
- **Map-reduce beats refine decisively** (BooookScore, arXiv:2310.00785 —
  Mixtral 81.5 vs 64.5; LLaMA 2 failed refine entirely), and refine is strictly
  sequential so far slower in wall-clock.
- **A bigger context window does not fix "lost in the middle"** — extended-
  context variants show near-identical position bias (arXiv:2307.03172).
- **Chunk size barely matters for map-reduce** (unlike refine) — ~4K with 10%
  overlap is fine and is not worth tuning.
- **No public leaderboard compares local llama.cpp models to frontier models on
  summarisation.** Producing one is a genuine contribution. Use BillSum (CC0)
  and score factual consistency separately from the SummEval rubric.
- **Caveat added 2026-08-17:** the spec's "CPU prefill drops ~58% from 512 → 32K
  context" is much weaker on large MoE models — measured **−1%** from 512 to
  2048 on gpt-oss versus −14% on a dense 4B. Chunking is still right, for the
  other reasons.

### llama.cpp RPC

- **Upstream calls RPC "fragile and insecure, never run on an open network"** —
  validates raw LAN IPs as a security requirement, not just latency.
- **`--tensor-split` is the layer-split mechanism.** Set it explicitly;
  auto-split trusts buggy self-reported free memory. `--rpc` must precede
  `--device`.
- **`rpc-server -c`** caches tensors ≥10 MiB to `$LLAMA_CACHE` or
  `~/.cache/llama.cpp/rpc`. Always run with it. **Check the cache filesystem has
  room** — measured at ~76% of a node's share on a small dense model, and
  approaching 100% on a large MoE.
- **Version mismatch fails loudly** at handshake — good, no silent corruption.
  ISA mismatch does **not**.
- **Do not patch `HASH_THRESHOLD`.** Generation traffic is fresh bytes per token.
- **Async/pipelined RPC is not coming soon** — PR #18626 still open and
  `mergeable_state: dirty` 7+ months in. Assume sequential execution.
- **Model load is single-core serialised** (#25890) — a ~550 GB load takes ~15
  min with cores idle. Budget for it; not a hang.
- **The public 0.06 tok/s CPU-cluster figure is a misuse case** — the model
  already fitted on one host. Never cite it without that context.

### Provisioning

- **Duplicate `machine-id` breaks DHCP**, not just logging — systemd-networkd
  derives its DHCP client-ID from it, so identical IDs collide on one lease and
  present as intermittent fleet-wide network flapping.
- **Tailscale LAN isolation is automatic** — it only owns `100.64.0.0/10`. The
  only way to pull RPC onto WireGuard is `--advertise-routes`, so never pass it.
- **Preseed must set `non-free-firmware`** or recycled NICs may lack firmware.
- **Leave `partman-auto/disk` unset**, and filter out sub-8 GB devices — node 1
  has a 0 B card reader at `/dev/sda` that `list-devices disk` returns first.
- **THP is already correct on Debian 12** (`madvise`). Assert it; do not tune it.
- **Build natively on a fleet node** — glibc is forward-incompatible.

---

## Log

- **2026-08-17 (evening)** — **NODE 2 JOINED. The cluster is N=2.** Characterised
  before assuming: node 2 is a **bandwidth twin** of node 1 (STREAM 27.9 vs 28.4
  GB/s, identical 4 x 32 GB DDR4-2400 layout) — F29. Both engines distributed,
  `rpc-server` running on both at `-t 4`, and the **#26500 gate PASSED across
  real machines** (F31). **Five latent bugs found, all specific to the 1 -> 2
  transition** (F30) plus the DIMM reporter and F21 recurring as a *false
  negative on the gate itself*. **The network turned out to be 100 Mb/s, not
  gigabit** (F28) — which inverts F23's peer-pull preference and qualifies the
  expert-parallelism comms analysis. Aggregate replication measurement still owed,
  blocked on a 65 GB model copy.
- **2026-08-17 (evening)** — Expert parallelism re-raised and re-closed with a
  sharper argument (`DESIGN-NOTES.md` D): in the S = 1 regime it is not merely
  out of scope, it is **strictly worse** than replication, because it optimises
  per-request latency on an explicitly asynchronous workload.
- **2026-08-03** — Brainstormed. Initial design assumed GPU sharding.
- **2026-08-03** — Pivoted to CPU-only; 14 GB pooled VRAM too little.
- **2026-08-10** — Research across RPC internals, model performance,
  provisioning. Implementation plan written.
- **2026-08-11** — Batching research qualified the "multiplies seats" claim.
- **2026-08-11** — Reframed around data sovereignty and a two-stage deliverable.
- **2026-08-12** — **Node 1 built and Phase 0 executed on real hardware.**
  b10369 pinned (the `b8492` warning was stale). RPC gate passed on generation
  (−5.2%) but prefill cost −39.4%. Found `rpc-server` renamed upstream and the
  default RPATH non-relocatable — both would have failed only on workers.
- **2026-08-12** — **Two "settled" constraints turned out to be wrong.** The
  75% RAM rule cites a fixed syscall bug (F1); Model B does not fit the
  coordinator's **disk**, making disk the binding constraint (F16).
- **2026-08-12** — **The plan's TTFT measurement was measuring nothing** —
  0.015 s reported against a real 89 s (F17). The same bug is in Task 8.
- **2026-08-17** — Memory question settled: 4 × 32 GB across all four channels
  at rated speed; the 28.2 GB/s ceiling is the 4-core CPU, and MSRs confirm no
  BIOS lever remains (F12). An earlier half-population hypothesis was retracted.
- **2026-08-17** — **Sparse MoE reaches only 61% of bandwidth** (F24). Every MoE
  estimate was ~1.6× optimistic and was revised.
- **2026-08-17** — **Batching answered**: 1.79× at batch 4, collapse at 8.
  Replication beats it ~4× and beats sharding by a factor of N.
  **The architecture is now replication-first and N-agnostic.**
- **2026-08-17** — **Kimi K2 has the worst REPORTED hallucination rate of any
  model checked** (17.9%), against a project requirement of faithfulness.
  Model B re-opened; GLM-4.6 leads (F25).
- **2026-08-17** — **ik_llama.cpp A/B: +52% prefill, −14% generation, net +22%**
  end-to-end, output verified coherent (F27). Last open software lever, and it
  delivered.
- **2026-08-17** — Missing Link built through Task 12 (41 tests). Found and
  fixed a real race in the plan's job store (F20) and a silent empty-output
  failure with reasoning models (F21).
- **2026-08-17 (late night) → 2026-08-18 (early morning)** — **All three
  feature-agent worktrees merged to `main`, plus a fourth fix found along the
  way.** Fan-out across R inference endpoints (`151ed32`); queue control
  (cancel/reorder/cooperative stop) and resumability from persisted per-chunk
  summaries, gated on model **and** instruction matching (also `151ed32`, via
  `a41666e`); a real concurrency race in `db.init_chunks` that only existed in
  the *combination* of the fan-out and resumability branches, found
  independently by two agents and fixed structurally by making `init_db` the
  single migration entry point (`9f968a1`, reproduced 20/20 before → 0/20
  after); a rewritten llama-server watchdog reading cgroup `CPUUsageNSec`
  instead of `/health`, because `/health` shares the same task queue
  `update_slots()` drains and reported `n_idle_slots=4 n_processing_slots=0`
  while the server was busiest prefilling — F39, which corrects an earlier
  belief that 3 of 4 slots were idle during that incident (`8849fd0`); and
  live per-job telemetry (chunk N of M, prefill/generation tok/s from the
  server's own timings, a three-tier ETA, per-endpoint attribution) plus
  per-workflow guidance via textarea or file upload (`33ddc79`). **Test count
  131 → 142 → 145 → 193.** Missing Link fan-out (Task 2) is now DONE at the
  job level; chunk-level fan-out within one document and a retrieval-based
  task profile remain open. `missing-link.service` on node 1 still runs the
  pre-merge code and has not been restarted this session — see the box at the
  top of this file for why. A chunk-size benchmark is running on node 2 with
  `rpc-server@50052` there deliberately stopped for the duration.

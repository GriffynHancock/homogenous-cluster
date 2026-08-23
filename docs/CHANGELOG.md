# Changelog

Session-by-session merge history, moved out of `STATUS.md` so that file's first
screen stays usable. **Nothing here is current state** — `STATUS.md` is the only
place to read what is running now, and `docs/FINDINGS.md` outranks anything
written here. Newest first.

Commands and invocations quoted below are **historical records**, not
instructions. Where one of them has since become dangerous it has been replaced
in place with the correct current form and a warning saying why; do not assume
an unannotated command is still safe without checking `STATUS.md` and
`CLAUDE.md` first.

---

## MERGED 2026-08-23 — N goes to 3, Model B closes, the splitter changes, and the safety layer turns out to be decorative

**The single largest batch since the project started, and three of its results
are corrections to things this repo asserted as fact.** Findings F46–F56 all
landed in `docs/FINDINGS.md`; read those rather than this summary where they
disagree. `git log --format='%h %ci %s' dfbc262..HEAD` gives the exact chain.

### The corrections, first, because they invalidate prose people were acting on

- **F55 — the `PreToolUse` agent-hardening hooks have NEVER been enforced.**
  `.claude/settings.json` invoked the guard as `"$CLAUDE_PROJECT_DIR"/…`; the
  variable is empty, so the path resolved to `/.claude/hooks/cluster-guard.py`
  and never existed. Tested directly: `git add -A` — a hard BLOCK rule — ran
  unimpeded, as did a DENY-rule `git merge` in the live checkout. All 166 lines
  of `.claude/hook-audit.log` are same-second direct test invocations; **there is
  no record of a framework interception, ever.** `CLUSTER_OPS_CONFIRMED=1` has
  therefore never meant anything either. A fallback path is patched into
  `settings.json` (`4ef0744` and the ml-auth window) and is **UNVERIFIED** until
  a fresh session sees a real block.
  **It also corrects F52**, which had explained the same silence with a genuine
  but non-operative pattern gap. **A satisfying explanation for a failure is the
  most effective way to stop looking for the real one** — the same shape as
  F36/F40 and F45/F48.
- **F47 — Model B is CLOSED, in the negative.** Not deferred: every frontier
  candidate is now priced and each is dominated. GLM-5's active-parameter count,
  published nowhere, was **computed from `config.json` at 40.8 B** and labelled
  CONFIRMED because the same per-tensor inventory reconstructs the published
  753.86 B total to **0.00% error**. It is more active than DeepSeek-V3.2, *less*
  faithful than GLM-4.6 (10.1% vs 9.5%), and 402.9 GB against 368 GB free.
  Finix S1 has no public weights (HTTP 401) and its 1.8% is a **summary-length
  artifact** — 172.4 average words against a 106.9 median, on a leaderboard whose
  own FAQ says a copy-paste extractive summariser scores 0%.
  **Flagged, not chased: `GLM-4.7-Flash`** appears to dominate the incumbent on
  every axis this project ranks and is ~30 minutes of link time to settle.
- **F56 — "everything appears at N=2 and nothing new at the tenth" is
  measurably too optimistic.** 1 → 2 found five latent bugs; **2 → 3 found three
  more**, and the anticipated one (the per-node username) was not among them:
  `setup.sh` created only `/opt/llama.cpp`; the #26500 gate's only diagnostic ran
  unprivileged and printed `-- No entries --` on healthy and broken nodes alike;
  and F30's machine-id journal orphaning recurred because `setup.sh` still does
  not restart `systemd-journald`. **And `llama-server@.service` had never reached
  ANY node** despite being tracked in git — F32 a second time, in a different
  file. **It also corrects F53:** the GNOME desktop is the fleet's normal, not
  node 3 being odd.

### Node 3, and the fleet becoming N=3

**F53 + F56.** Characterised before provisioning, per F29's rule. Xeon E5-1620
v4 down to the stepping, zero differences in the sorted CPU flag set, **STREAM
27.6–27.7 GB/s** — a three-way bandwidth twin. RAM measured at 128707 MB,
genuinely 2 MB under nodes 1/2. Joined, hardened key-only (verified independently
of the hardening script by a real rejected `sshpass` login), `two-node-smoke.sh`
**PASS** with coherent prose checked against F21's false-negative hazard, at
b10369 and ik `8337e4cd` matching the fleet, 100 Mb/s full duplex.

**The per-node username was parameterised rather than renamed**, per the
operator's decision that the eventual skill must run where usernames do not
match — an optional 5th field in `nodes.env`, read by `distribute.sh` (eight call
sites), `install-services.sh` (five) and a `llama-server@.service` drop-in.
**`EnvironmentFile` cannot solve it:** systemd expands `${VAR}` only in the
`ExecStart` family, so `User=${LLAMA_USER}` dies 217/USER. The tracked `User=` is
a placeholder resolving to nobody, so a missing drop-in fails loudly rather than
silently running the inference server as root.

**Its "1 TB" is a 7200 rpm SATA disk**, not more NVMe — bare NTFS, 128 MB used of
932 GB, SMART PASSED, 2,789 power-on hours. A layout is designed and **not
executed** pending the operator, because it destroys a filesystem. Models stay on
NVMe. **Node 3 is deliberately NOT in `INFERENCE_ENDPOINTS`** until a server
answers there.

### The sentence splitter, and the search term that found it

**F48 + addendum + F52.** Prompted by the operator's *"why aren't we using an
existing pipeline?"*, every hand-rolled text component was audited against what
already exists (`docs/existing-pipeline-audit.md`): **one ADOPT, three
KEEP-OURS, one unmeasured.**

**pysbd — the most-recommended library, carrying a peer-reviewed 97.92% Golden
Rules claim — scored WORSE than the regex it would replace** (2.76% vs 2.85%) and
**took 133.94 s to be worse**, because it was benchmarked on well-formed prose
and our failure case is a document that is structurally a list.

**`nupunkt`, a legal-domain splitter nobody had searched for, fixes F45
outright**: structural fragments 65.0% → 12.3%, legislative marker rate 2.85% →
10.53%, PDF hard-wrap fragments 52.4% → 3.5%. Pure Python, zero runtime
dependencies, MIT, 9.1 MB wheel, verified to work inside `unshare -rn` with no
network. **The transferable lesson: `chunking-research.md` asked "what is the
best general-purpose splitter?" and correctly rejected nltk and spaCy as too
heavy — but domain-specific tooling can be simultaneously more accurate AND
cheaper, so "too heavy" is not a conclusion that survives a change of search
term.**

Implementation notes worth keeping: `nupunkt.sent_spans` returns **true character
offsets**, removing the whole `str.find` bug class F48 had budgeted for; the
offset contract was verified over **12,129 real spans** from the live corpus;
**nupunkt INVERTS the raw-HTML effect rather than fixing it**, so HTML extraction
stays load-bearing; and **four existing tests encoded the old splitter's defects
as expectations** — pinned to the regex rung rather than deleted, because a test
suite can hold a bug in place as a specification.

**F52 re-profiled all 17 corpus documents on the new instrument.** Legislative
3.03% → 9.68%, regulatory 1.06% → 2.06%, ISM 0.57% → 1.71%, NIST 0.30% → 1.21%.
The dry run confirmed **no splitter-INDEPENDENT control figure moved**, which is
what distinguishes "the instrument changed" from "something else changed". Test
suite **662 passed**, and the number was **reconciled** (552 + 77 + 27 + 6)
rather than accepted — a count that does not reconcile is an unexamined claim.

### The sharding A/B, and what RPC actually costs

**F50.** Two contradictory figures had sat unreconciled for weeks — F14's −5% and
a −47% from one short chat request. **Both reproduce; they measure different
topologies, and the configuration separating them had never been run.**
Decomposition: **protocol −5.8%, the 100 Mb wire a further −35.4%, the second
device −11.4%.**

**The mechanism was established by byte accounting, not inference:** the device
holding the output layer returns `n_vocab × f32` = 593.5 KiB **per token**;
predicted +51.9 ms/token and 445 MiB, **measured 52.4 ms and 443 MiB**. So the
penalty scales with `n_vocab`, **not** model size — **correcting a standing guess
in `docs/measurements.md` that a larger model would amortise RPC away.** Prefill
is immune structurally (+0.6%).

Two configuration results: **`--rpc <remote> -ngl <half>` beats `-ts 1/1` by 29%
on prefill** — never put a loopback `rpc-server` in the path for the local shard;
and pinning the output tensor local with `-ot` collapses both metrics and doubles
traffic. **F44 was validated incidentally, and cleanly: a contended sample showed
pp512 22.63 ± 1.23 against 24.53 ± 0.11 clean — an error bar 11× wider.
A widening error bar is the signature of a contended benchmark; a single run
would have shown only a slightly wrong mean.**

### The watchdog, corrected for the third time

**F51 + addendum.** `rpc-server` serves one client at a time and **refuses
connections while busy**, so during a shard upload its port is silent for the
whole ~54 min — and the watchdog restarted it **9m41s in, at 5.4 GiB
transferred**, with a log message whose premise was exactly inverted (*"a worker
that is not listening cannot be carrying shard work"*). **That made any shard
larger than ~4 GB on this link impossible to load**, and F50's own measurement
was possible only because its model was small enough to sneak under the timer.

Fixed with a **BYTES** signal: `ss -ti` data counters on the RPC port's
established connections, with cgroup CPU as a co-signal, running only after the
port has already refused. **The decisive measurement retires the obvious
alternative: the receiving `rpc-server` burns ~0% CPU while 1 MB/s of real
payload arrives**, so CPU alone would not have saved the upload. Both directions
tested on both nodes against a real `rpc-server`; a `SIGSTOP`ped server with
`conns=0` still triggered a real restart. **The fleet was never unguarded — zero
seconds.**

### Missing Link: fan-out live, citations audited, and a door lock

- **Fan-out enabled and PROVEN, not merely merged.** Two jobs claimed by
  different endpoints 5 ms apart, running concurrently, corroborated from outside
  Missing Link's own opinion of itself by node 2's load average going 0.00 →
  1.14. **The first time node 2 had ever run inference through Missing Link.**
  A trap found in passing: a duplicate entry in `LLAMA_URLS` yields two worker
  loops sharing one state entry, so "run two workers per node" is not available
  by repeating a URL.
- **F46 — the citation-accuracy audit, deterministic, no model consulted.**
  7 markers, 7 CORRECT, 0 wrong-chunk, 0 unsupported. Sample size is honestly 1,
  established rather than assumed. **The near-miss is the sharp part: the model
  emitted its markers with U+202F NARROW NO-BREAK SPACE, and a tolerant `\\s` is
  the only reason all seven resolved** — a stricter regex would have dropped 7 of
  7 valid citations and reported that the model ignored the instruction. **And a
  new defect shape: the citation is correct, the span is correct, and the claim
  is still false** — two true facts from the cited span merged into one wrong one.
- **F54 → a shared credential in front of the API** (`1532865`, merged
  `f50e774`). The live instance was bound to `0.0.0.0:8000` with **no security
  scheme at all**, exposing `POST /corpus/{doc_id}/delete` — on a LAN about to
  carry ~20 students. `ML_AUTH_TOKEN`, HTTP Basic or Bearer, `/health` the only
  open route (F39: a probe whose token drifts reports an outage that is not
  happening). **A door lock, not a security system**, which is exactly the scope
  the operator set.

### The amplification experiment: built, not run

**F49.** The paired single-pass-vs-map-reduce design **cannot run on whole
documents at all**, for arithmetic rather than effort reasons: at
`n_ctx_slot = 8192` the single-pass source budget is **5,094 tokens**, and **1 of
17 documents fits**. Exact McNemar cannot reach p<0.05 below 6 discordant pairs,
so that is not an underpowered experiment, it is **no experiment** — and it would
have produced a null reading as "no amplification". The unit became a
paragraph-aligned **section**, with the cost stated: any amplification measured
is a **lower bound**.

Also from it: **`POST /tokenize` runs on the HTTP thread and constructs no
task**, verified by reading `server-context.cpp`, so exact token counting is free
anywhere the code estimates — which matters because **`WORDS_PER_TOKEN = 0.70` is
wrong by up to 2× in both directions** (0.53–1.08 within a single document). Two
defects only real text exposed: paragraph-aligned sectioning returned **zero**
usable sections from one Act whose HTML yields 23 paragraphs under 40 characters
and one of 29,053; and the first fan-out implementation **silently dropped a
section and reported success.**

**The harness has never been run. The operator's instruction is to drop it for
now.**

### The teaching playground

**F54**, with `docs/comfyui-feasibility.md` and `docs/n8n-feasibility.md`.
**ComfyUI is compute-bound the wrong way for this fleet** — 7–13 min per 512×512
SD1.5 image per node, and the one CPU diffusion benchmark with a published memory
figure peaks at **6.94 GB, 5% of a node's RAM**. Diffusion leaves idle exactly the
resource this fleet has. Video is not reachable. **n8n fits**, is
licence-permitted with one grey edge worth an email, gives unlimited Community
users (CONFIRMED from `license.ts`, against a widely-repeated forum claim), and
integrates with local LLMs with no custom code. **Both belong on node 3, not on
an inference node — F44 decides it, and `nice` is not a mitigation.**

**Found in passing and fixed:** `reportlab` was an **undeclared test dependency**
since `test_extract.py` was written (`37db6ad`), verified by building a fresh
venv purely from `requirements.txt`. **`pip install minicheck` installs an
unrelated z3-based package** — `requirements-audit.txt` correctly pins the git
URL, and anyone "simplifying" that line gets the wrong package silently.

### A process note worth keeping

An agent ran a read-only profiling pass on node 1 while the sharding benchmark
held both nodes, then **disclosed it unprompted with the exact window** so the
affected repetitions could be discarded. Under F44 that is a real perturbation.
**A disclosed contention event is cheap; an undisclosed one silently corrupts a
number that then gets quoted for months.**

---

## MERGED 2026-08-18 morning → afternoon, AFTER the entry below

**~30 more commits landed after the batch documented below**, roughly
06:36–12:34 on 2026-08-18 (`git log --format='%h %ci %s' 03fdee9..HEAD`
gives the exact chain). In rough chronological order, the decision-relevant
ones:

- **Agent-hygiene hooks** (`95b2fac`). `.claude/hooks/cluster-guard.py`
  now DENYs (blocks outright) `git add -A`, `git commit -a`, `pkill -f`, and
  inline Python that fails to `compile()`, and GATEs (requires
  `CLUSTER_OPS_CONFIRMED=1`, which only the operator sets) cluster service
  control, mutating SQL against the live job store, `git push`, writes to
  `/opt/models`, and git operations in the live checkout itself. Two
  mechanism findings shaped it and are recorded in `docs/AGENT-HARDENING.md`,
  not duplicated into `docs/FINDINGS.md`: hook `if:` matchers do best-effort
  bash parsing and **fail open** on a parse failure, so the guard uses its
  own `shlex` parsing instead of relying on one; and
  `permissionDecision: "ask"` is a **silent allow** under
  `--dangerously-skip-permissions` (there is no prompt to show), so the guard
  downgrades GATE to DENY whenever the session cannot actually prompt.
- **Multi-node, multi-service watchdog** (`3b07cf2`, `156c824`). The
  single-node, single-service watchdog from the batch below was generalised
  to cover `llama-server`, `rpc-server` and `missing-link` on both nodes,
  each with its own liveness predicate (`docs/measurements.md`'s "Per-service
  signals" table — the CPU-flat rule that works for `llama-server` would
  falsely restart the other two, which are legitimately idle a lot of the
  time).
- **Retry-and-resume on backend failure** (`b7114cf`). A job whose inference
  backend disappears mid-run now retries (up to a bounded attempt count)
  rather than failing outright, reusing whatever chunk summaries already
  persisted. **This is not hypothetical**: job `6c0358825609` hit exactly
  this in production — see "Real production events this session" in `STATUS.md`.
- **A revive route for terminal jobs** (`569d4ce`). `POST
  /jobs/{id}/revive` lets an operator re-run a `failed`/`cancelled` job,
  previewing what `db.revive_job`'s resume would actually reuse before
  committing.
- **F40: ik_llama.cpp reversed, mainline restored fleet-wide** (`4ba807d`,
  `b419b60`, `9f417aa`). The single most consequential correction in this
  window — see the header of this file and F40 in `docs/FINDINGS.md` for the
  full mechanism (a forked-abort deadlock that hangs the server invisibly to
  `Restart=always` and a port check).
- **A two-model faithfulness audit ledger** (`f460cb3`), then **found not to
  survive production scale** (`10f5b40`, F41) and **superseded by a
  deterministic cascade** (`7a78d82`, F42) that caught a real fabrication in
  production — see "Real production events this session" in `STATUS.md`. The audit ledger's
  engineering (refuse-rather-than-degrade, two correctly-scoped hops) is
  still sound per F41; its empirical justification for being wired in as a
  safety net was not, and the deterministic cascade is what should actually
  be trusted.
- **Section-level citations on the reduce output** (`7c1266b`). The reduce
  step is asked to tag each combined-summary paragraph with `[Section N]`
  markers referencing the chunk(s) it drew from; resolved back to source
  character offsets in code, not asked of the model a second time
  (`CLAUDE.md`'s "never ask the model where something came from" rule,
  applied). **Verified this session against a real completed job
  (`18339bace8f0`)** — see "What is actually in flight right now" in
  `STATUS.md`: the
  model did produce well-formed `[Section 1]`…`[Section 7]` markers on real
  output. Citation *accuracy* (whether each marker is correct, not just
  present) is still unverified.
- **A real failure-history table, and an explicit-`accept` fix on file
  uploads** (`94da7d7`). The job page now shows every failed attempt for a
  job, not just its current status.
- **Opt-in chunk-boundary snapping, default OFF** (`910c3d5`), plus the
  research it rests on (`docs/chunking-research.md`) and a follow-up
  measurement (`978c1e3`) finding the real document's chunk boundaries do
  not in fact sever any clause pairs — but the corpus available cannot
  answer the general question either way. Does not change default
  behaviour; see "What is actually in flight right now" in `STATUS.md` for why
  this did not
  need a service restart.
- **Watchdog production hardening** (`5f7e1a1`): the node-2 `HOME`
  unbound-variable bug (F43 in `docs/FINDINGS.md`), a process-count tripwire
  for the forked-abort hang (the addendum to F40 there), and an opt-in synthetic
  transaction (issue a tiny real completion and validate its content, not
  just ask if the service is up) — left disabled
  (`WATCHDOG_SYNTH_ENABLE=0`) while real jobs were running, per its own
  commit message.

**Test count over this window: 193 → 469**, per `git log` and reproduced
this session (`.venv/bin/python -m pytest tests/ -q`, 469 passed). Per
`CLAUDE.md`'s own standing rule, that is what ran, not evidence on its own —
the concurrency, retry-and-resume and watchdog claims above are backed by
reproduction and production evidence, cited where it exists, not by the
count alone.

---

## MERGED THIS SESSION (2026-08-17 late night → 2026-08-18 early morning) — historical, see above for what landed after it

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
  Task 2 of "NEXT TASKS" in `STATUS.md` for why that split matters and what
  is still owed).
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
- `WORDS_PER_TOKEN = 0.70` is **still unmeasured, and this entry corrects the
  previous one, which said a chunk-size sweep would land a real value here.**
  The completed sweep (`docs/measurements.md`, "Chunk-size sweep" section)
  measured wall-clock by `CHUNK_TOKENS`, not the words-per-token conversion
  ratio itself — `chunk_size_driver.py` *consumes* `WORDS_PER_TOKEN` to turn
  a token target into a word count, it does not calibrate it, and the
  server's reported `prompt_n` is not directly comparable to the per-chunk
  word count because it includes the surrounding prompt template. **Still
  genuinely owed**, not in flight.

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
ssh <user>@<node2-ip> 'ls -lh /opt/models/gpt-oss-120b/'   # want 65369017728 bytes
# if interrupted, it resumes -- --partial --inplace, so just re-run:
rsync -a --partial --inplace --info=progress2 \
  /opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf \
  <user>@<node2-ip>:/opt/models/gpt-oss-120b/
```

**Do not benchmark either node while this runs.** It reads 65 GB off node 1's
disk, burns CPU on SSH crypto at both ends, and streams 65 GB through node 2's
page cache — every one of which perturbs an inference measurement. It also
saturates the link, which took RTT from 0.827 ms to 9.544 ms (F28).

**When it completes, do this — it is the measurement the architecture rests on:**

> **The `/opt/ik_llama.cpp/bin/llama-server ...` invocation that used to sit
> here has been REMOVED, not preserved.** It was written before **F40**, which
> established that ik_llama.cpp fatal-errors on the 5th request of any
> `--parallel 4` job — a 100% failure rate on any document longer than four
> chunks, ending in a hang that `Restart=always` cannot see. Pasting it would
> take a node down. **Use mainline. The fleet-wide default is
> `/opt/llama.cpp/bin`.**

```bash
# Independent llama-server per node. NO --rpc, NO --tensor-split: replication,
# not sharding. MAINLINE llama.cpp -- see F40 for why NOT ik_llama.cpp.
# -t 4 = PHYSICAL cores. --parallel 4, never 8. -c 32768 => 8192 tokens/slot,
# which is what CHUNK_TOKENS=4096 plus wrapper plus MAP_MAX_TOKENS needs.
/opt/llama.cpp/bin/llama-server -m /opt/models/gpt-oss-120b/gpt-oss-120b-F16.gguf \
  -t 4 -c 32768 --parallel 4 --host 0.0.0.0 --port 8080   # on BOTH nodes

# Then: single-node baseline, then both nodes concurrently, and compare
# AGGREGATE tokens/sec. Record in docs/measurements.md.
```

In normal operation you do not type this at all — `llama-server@8080` is a
systemd unit reading `/etc/default/llama-server`, and **stopping or restarting
it while a job is running destroys that job's in-flight work** (F39). Check for
a running job first.

Vary the prompt between runs or you measure the prompt cache (F17).

Preserved from the original block, because it is recorded nowhere else and is
still true of the ik_llama.cpp tree installed alongside mainline: **its CLI
differs from mainline** — `-no-cnv` does not exist, and there is **no
`--no-webui`** — and it reports gpt-oss as `?B` rather than `120B` (output was
still correct at the time; **re-verify coherence per model**, F27). This is
reference material for anyone reading ik's source or filing upstream, **not**
an invitation to serve from it — see F40.

</details>

---

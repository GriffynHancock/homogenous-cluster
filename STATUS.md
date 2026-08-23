# Status

**Updated:** 2026-08-23
**Phase:** **N=3. Node 3 joined, hardened and gated; nodes 1 and 2 serving.**

## At a glance — read this before touching anything

**Principle, stated so the next edit here doesn't reintroduce the bug that
made it necessary: a handoff document must not assert state that a
concurrent session can invalidate — it should say how to check.** An earlier
version of this file said, as fact, "60 commits are unpushed... the repo has
NOT been pushed as of this entry." That was true when the sentence was
written and false minutes later, once a concurrent session pushed — the next
reader had no way to know which. So: git push state, test counts, which
service is running what, disk free and queue depth are given below as
**commands to run**, not as facts asserted outright. Where a last-observed
value is genuinely informative it is kept, but labelled with when it was
observed and named plainly as a snapshot, not a fact.

| | |
|---|---|
| **What this is** | An **N-node**, CPU-only llama.cpp cluster doing real work on real sensitive documents, fronted by **Missing Link** — an async job queue where slowness is not a defect. `CLAUDE.md` has the argument and the standing constraints. |
| **Check before trusting anything below** | **Push state:** `git fetch -q origin && echo "ahead: $(git log --oneline origin/main..HEAD \| wc -l)  behind: $(git log --oneline HEAD..origin/main \| wc -l)"`. **Test count:** `cd missing-link && .venv/bin/python -m pytest tests/ -q \| tail -1`. **Node 1 services:** `systemctl is-active llama-server@8080 missing-link rpc-server@50052`. **Nodes 2 and 3 services + engine:** `ssh <user>@<node-ip> 'systemctl is-active llama-server@8080 rpc-server@50052; grep LLAMA_BIN /etc/default/llama-server'` — node 2's engine has flipped mid-session at least once (see "Where things stand" below); a stale reading of it is wrong, not just old. **Logins differ per node** — read `provisioning/nodes.env`, 5th field. **Is fan-out actually in use:** `grep LLAMA_URLS /etc/default/missing-link` — as of 2026-08-23 this is **SET to both endpoints and proven live** (see below), but it had been observed unset (silent single-endpoint fallback) more often than set for weeks before that, so still check rather than assume; the code being merged never meant it was live. **Disk free:** `df -h /` on each node. |
| **What is running** | `llama-server` on **nodes 1 and 2**, `rpc-server@50052` on **all three**, and **Missing Link** on the coordinator (node 1) — all as systemd units. **N = 3, but only 2 serve inference:** node 3 is deliberately NOT in `INFERENCE_ENDPOINTS` until a model and a server actually land on it, because adding it early parks a Missing Link worker in permanent backoff. Use the row above to confirm this is true right now rather than trusting this sentence. |
| **Missing Link needs a credential now** | `ML_AUTH_TOKEN` in `/etc/default/missing-link`, accepted as HTTP Basic (`curl -u ml:$ML_AUTH_TOKEN …`) or `Authorization: Bearer`. **`/health` is the only open route**, deliberately (F39: a probe whose token drifts reports an outage that is not happening). **Check without printing the secret:** `grep -q '^ML_AUTH_TOKEN=' /etc/default/missing-link && echo set \|\| echo UNSET`, then `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/jobs` — expect **401**. **Never write the token into this file, `docs/`, a commit message or a comment.** It is a door lock, not a security system (F54). |
| **Which engine, and why** | **MAINLINE llama.cpp b10369** (`/opt/llama.cpp/bin`), fleet-wide, node 3 included. **Not ik_llama.cpp** — it was adopted on a prefill win (F27) and then dropped, because it fatal-errors on the 5th request of any `--parallel 4` job, i.e. on any document longer than four chunks, leaving a hang `Restart=always` cannot see (**F40**). ik stays installed at `/opt/ik_llama.cpp/bin` for the still-untested `--parallel 1` configurations only. **Do not point a serving node at it.** |
| **The sentence splitter changed, and it invalidates old numbers** | **`nupunkt`, not the regex** (F48 + addendum, merged and live; F52 re-profiled all 17 corpus documents on it). Legislative marker rate went 2.85% → 10.53% on the Privacy Act compilation. **Any marker-density or boundary figure produced before 2026-08-23 came from a different instrument and is not comparable** — `docs/chunk-boundary-measurement.md` carries a banner saying so. **Check the active rung:** `missing-link/.venv/bin/python -c 'import nupunkt; print("nupunkt live")'` — the code falls back to the regex loudly rather than failing, so an uninstalled nupunkt silently means old behaviour. Needs Python **>= 3.11**; nodes 1-3 are on 3.11.2, exactly on the line, and nodes 4-7 are unchecked. |
| **Model B is CLOSED, in the negative** | **F47.** Every frontier candidate is priced and dominated — GLM-5 is 40.8 B active (computed from `config.json`, validated to 0.00% against the published total) and *less* faithful than GLM-4.6; Finix S1 has no public weights and its 1.8% is a summary-length artifact; Kimi K2 unchanged from F25. **Do not reopen it with reasoning.** The one live model question is **`GLM-4.7-Flash`**, which may dominate the gpt-oss-120b incumbent (3.6 B active, 9.3% Vectara, MIT, 18.3 GB, S=1) and is **cheap to settle by measurement** — half an hour of link time. See NEXT TASKS §1. |
| **The agent-hardening hooks have NEVER fired** | **F55.** `settings.json` invoked the guard via `"$CLAUDE_PROJECT_DIR"/…`, the variable is empty, the path never resolved, and a hard BLOCK rule (`git add -A`) executed unimpeded when someone finally tested it. `CLUSTER_OPS_CONFIRMED=1` has never meant anything. **A fallback path is now patched in and it is UNVERIFIED** — the framework reads `settings.json` at session start, so only a fresh session can confirm. **Verify it, and record the result:** in a throwaway repo (`d=$(mktemp -d) && git -C "$d" init -q`), run `git -C "$d" add -A` through the Bash tool and see whether you are blocked; then check `wc -l .claude/hook-audit.log` for a new line with a non-test invocation. **Until someone has seen a real block, you are the only enforcement.** |
| **Next task** | **§0, the corpus-driven benchmark** — the operator's stated priority, and the corpus is now re-profiled on the correct instrument so the blocked measurements can finally be re-run. Full brief under **NEXT TASKS, in order** below. |
| **Never do this to the live services** | Do not stop, restart or reconfigure `llama-server`, `rpc-server@50052` or `missing-link` without first checking whether a job is running — a restart destroys that job's in-flight work, which is exactly how F39 lost 10m55s of completed work. **`rpc-server` refusing connections is not evidence of death** — it serves one client at a time and is silent for the whole of a shard upload (F51). Do not benchmark a node while it or its peer is doing real work; a contended benchmark shows up as an **11× wider error bar**, not a wrong mean (F50). Never `pkill -f`; never `git add -A` — **and note the hook does not currently stop you**, per the row above. |
| **Where the history went** | The session-by-session "MERGED" / "completed this session" narrative that used to sit here is now in **`docs/CHANGELOG.md`**, intact. |

Everything below expands on that table. If you only have time for one more
thing, read the **Index** at the top of `docs/FINDINGS.md`.

---

## Current state, in detail

**The fleet is N=3.** Nodes 1 and 2 serve inference and run `rpc-server`; node 3
is joined, hardened key-only, running `rpc-server@50052`, and **passing the
#26500 gate** — but deliberately holds no inference endpoint yet. Everything
runs **MAINLINE llama.cpp b10369** (`/opt/llama.cpp/bin`); ik_llama.cpp stays
installed side by side at `/opt/ik_llama.cpp/bin` for the still-untested
`--parallel 1` / flash-attention-off configurations only (F40), and node 1's
former ik config is backed up at `/etc/default/llama-server.ik.bak`.

**Node 3 is a three-way bandwidth twin, and that is a measured result rather
than an assumption** (F53, corrected by F56). STREAM triad 27.6–27.7 GB/s
against node 1's 28.4 and node 2's 27.9; identical Xeon E5-1620 v4 down to the
stepping, identical BIOS and chassis, **zero differences** in the sorted CPU
flag set, so no ISA/SIGILL risk. Its RAM is genuinely 2 MB under nodes 1/2
(128707 MB, measured, not copied). Its admin login is **not** the coordinator's,
which is why `nodes.env` grew an optional 5th field rather than the machine
being renamed — the eventual skill must run where usernames do not match.

**Two things about node 3 that will recur on node 4.** Its "1 TB" is a 7200 rpm
spinning SATA disk carrying an empty NTFS format, not more NVMe — **models stay
on NVMe; the HDDs are snapshot and cold storage** (F16, F3). And it shipped as a
full GNOME desktop — which **F56 corrected from "a divergence" to "the fleet's
normal"**: nodes 1 and 2 run the identical stack, and trimming node 3 alone
would make it the odd machine out and confound any A/B against the bandwidth
twins it just joined.

**Missing Link is behind a shared credential.** `ML_AUTH_TOKEN`, HTTP Basic or
Bearer, `/health` the only open route. It closes one specific hole found by F54
— the live instance was bound to `0.0.0.0:8000` with no security scheme at all,
exposing `POST /corpus/{doc_id}/delete` to any host on a LAN that is about to
carry a class of students — and it claims nothing beyond that. The token is a
value in `/etc/default/missing-link` and belongs in no published file.

**Fan-out is live and proven on hardware, not merely merged.** `LLAMA_URLS`
names both serving endpoints; two jobs submitted back to back were claimed by
*different* endpoints 5 ms apart and ran concurrently, corroborated from outside
Missing Link's own opinion of itself by node 2's load average going 0.00 → 1.14.
That was the first time node 2 had ever run inference through Missing Link.

**Missing Link's own feature surface**, all merged: fan-out across R endpoints,
queue control, resumable chunk-level persistence, automatic retry-and-resume on
backend failure, live per-chunk telemetry with separate prefill/generation
rates, per-workflow guidance, section-level citations on the reduce output, a
revive route, a per-job failure-history table, a deterministic faithfulness
cascade, a corpus benchmark page, the nupunkt splitter, and the auth gate.
**Test count and push state are deliberately not asserted here as current fact**
— see "Check before trusting anything below" for the commands. **Repo:**
https://github.com/GriffynHancock/homogenous-cluster

### Corrections to long-standing assumptions, from measurement

- **The network is 100 Mb/s, not gigabit** (93.8 Mbit/s measured, confirmed on a
  third machine). Both NICs are gigabit and the switch is the cap. This
  **inverts** F23's peer-pull preference. See **F28**.
- **ik_llama.cpp fatal-errors on the 5th request of any `--parallel 4` job** —
  a 100% failure rate on any document longer than four chunks, and the hang it
  produces is invisible to `Restart=always` and a port check. **Mainline is the
  fleet-wide default. F40.**
- **RPC's generation penalty scales with `n_vocab`, not with model size, so it
  never amortises** — F50 decomposes the long-standing −5% vs −47% dispute into
  protocol −5.8%, the 100 Mb wire a further −35.4%, the second device −11.4%.
  The standing guess in `docs/measurements.md` that a larger model would amortise
  it away was wrong and is corrected there.
- **`WORDS_PER_TOKEN = 0.70` is wrong by up to 2× in both directions**, and
  `POST /tokenize` is free — it runs on the HTTP thread and takes no inference
  slot. Exact counting is available wherever the code estimates. **F49.**
- **The `PreToolUse` hooks have never enforced anything** — F55, and it corrects
  F52's diagnosis. See the at-a-glance row.
- **`nodes.env` had node 1's RAM as 125629 MB, which `free -m` does not report.**
  Corrected — it sets `--tensor-split` ratios. F29.

**Session history — what merged when, and the reasoning behind each batch — is
in `docs/CHANGELOG.md`.** Nothing was deleted, only moved.

---

## What is in flight, and what is merged but not deployed

**This section exists because "the code is on `main`" has repeatedly not meant
"the thing is running."** F32 caught it twice (ik_llama.cpp had never reached a
worker; `llama-server@.service` was tracked in git and had reached no machine at
all), F34 caught it once (41 tests passing against a pipeline that had never
processed a document), and F55 caught it for the hooks. **Check, do not infer.**

- **The node-3 provisioning commit is MERGED, and the parameterisation was
  exercised end to end from the merged tree, not merely merged.**
  `agent/node3-join` landed on 2026-08-23: the per-node SSH login (`nodes.env`
  5th field plus `node_user`/`node_target`/`node_target_for`), and its use in
  `distribute.sh`, `install-services.sh`, `install-watchdog.sh`,
  `harden-ssh.sh`, `join-node.sh`, `setup.sh` and `bench/*`, together with the
  `llama-server@.service` drop-in mechanism. **This is the one item in this
  section that has been checked against the hardware rather than against git.**
  `./provisioning/distribute.sh` run from the merged tree resolved and reached
  both workers — `node2 debian1@…`, `node3 debian3@…`, both `ok`, both at
  b10369. **Check, do not infer** (the section's own rule; the run above is a
  snapshot from 2026-08-23 and a later session can have changed the tree):
  `grep -n '^  "node3' provisioning/nodes.env` must return a real four-or-five
  field line, not a commented placeholder; and `./provisioning/distribute.sh`
  must print a `Targets (2):` block naming `debian3@` for node 3 — an empty or
  three-field-only target list means a script run from this tree will not see
  node 3.
- **Nodes 1 and 2 still carry the OLD `llama-server@.service`** (`User=debian1`,
  no drop-in). They work. Converge them with
  `ONLY_NODES=node1,node2 ./cluster/install-services.sh` — **but that also does
  `enable --now rpc-server@50052`, so schedule it when no benchmark is running.**
- **The amplification harness is built and has never been run** (F49). The
  operator's instruction is to leave it for now. See NEXT TASKS §3.
- **The 1 TB coldstore disk layout is designed and NOT executed**, pending the
  operator's go-ahead, because it destroys an existing filesystem — empty or not.
  See NEXT TASKS §4.
- **`setup.sh` asserts `THP: expected [madvise]` but all three nodes report
  `[always]`** — a stale assertion in the script, not a node-3 problem.
- **A stale process from 2026-08-17 (PID 20659) loops forever**, because its
  `pgrep -f auto-bench-gptoss` self-matches its own command line — this project's
  standing "never `pkill -f`" hazard in its other form. Zero CPU, harmless, and
  it will never exit.

---

## If you are a fresh session

1. **Read `docs/FINDINGS.md`.** **56 findings** from running this on real
   hardware, and it **outranks this file and `CLAUDE.md` both.** Several correct
   the plan or the spec — **F28 corrects this file and F23; F40 reverses the
   ik_llama.cpp recommendation fleet-wide; F39/F43/F51 correct the watchdog's own
   design three times; F45 then F48 correct the metric the corpus page uses to
   judge a document, and then the instrument producing it; F50 reconciles two
   contradictory RPC numbers that both turned out to be right; F52 is itself
   corrected by F55, and F53 by F56.** Do not trust the original plan's numbers
   over these, and do not trust an older copy of this file's engine choice — it
   changed, more than once.
   **Three findings correct THIS FILE and `CLAUDE.md` directly, so read them
   before acting on anything either says: F47** (Model B is closed in the
   negative), **F55** (the agent-hardening hooks have never fired) and **F56**
   (the "nothing new appears at the tenth node" claim is measurably too
   optimistic).
2. `docs/measurements.md` is the only place performance numbers may be quoted
   from.
3. `docs/UPSTREAM-PATCHES.md` lists the concrete corrections still to fold back
   into the plan and spec.
4. **`network.md`** (gitignored) has the IPs, node roles and ports for THIS
   deployment. Read it; never commit it. `CLAUDE.md` opens with a full file
   index.
5. **You are the operator.** Run the commands, read the output, record the
   numbers. Never report a step done without having seen its output.

**Everything is built on nodes 1, 2 AND 3.** llama.cpp b10369 at
`/opt/llama.cpp/bin` is the engine fleet-wide; ik_llama.cpp stays installed side
by side at `/opt/ik_llama.cpp/bin` for the still-untested `--parallel 1` /
no-flash-attention configurations (F40), not as the default. Models in
`/opt/models` on nodes 1 and 2; **node 3 has no model yet and no inference
endpoint.** Missing Link in `missing-link/` (coordinator only).
`rpc-server@50052` runs at `-t 4` as user `cluster`. **All of that is a
described shape, not a live reading** — take the commands from the at-a-glance
table and check it yourself.

**Access:** node 1 is reachable over Tailscale with Tailscale SSH enabled, and a
detached tmux session named `cluster` is waiting on it. **The address is in
`network.md`** (gitignored, site-specific — this file is published):

```bash
ssh -t <coordinator-tailscale-ip> 'tmux new-session -A -s cluster'   # then: claude --continue
```

---

## NEXT TASKS, in order

**Done on 2026-08-23, so nobody re-does it:** fan-out enabled and proven live on
hardware; the deterministic citation-accuracy audit (F46); Model B closed in the
negative (F47); the sentence splitter swapped to nupunkt and all 17 corpus
documents re-profiled on it (F48, addendum, F52); the rigorous two-node sharding
A/B (F50); the watchdog's RPC probe rewritten to test bytes-moved instead of port
acceptance (F51 + addendum); the agent-hardening hook gap found (F55); node 3
joined, hardened and gated (F53, F56); and a shared credential put in front of
Missing Link (F54). The narrative is in `docs/CHANGELOG.md`.

### 0. THE CORPUS-DRIVEN BENCHMARK — the operator's stated priority, and it is now UNBLOCKED

**"this is a strong direction. this needs to be a large part of next session."**
— the operator, 2026-08-18.

**Why this is first, and it is not a preference.** Every measurement this project
made before the corpus existed ran against whatever documents happened to have
been submitted as jobs: two long narrative/devotional texts and a 2,202-character
memo. **Three separate measurements were blocked or weakened by that, and only
that:**

- **Chunk-boundary severance returned 0 of 84 events and could not answer its own
  question.** The only legal-styled document in the store was 2,202 characters —
  too short to produce a single chunk boundary at any size.
- **The entity signal's 15% → 8.5% false-positive rate is dominated by OCR damage
  specific to that corpus.** The model correctly reconstructs transliterations
  the source has mangled, so a faithful claim looks unsupported.
  `--entity-rules strict` measures **100% near-miss catch at 17.3% FP** and is
  probably correct for this project's real target material — clean digital legal
  and standards text — but is **untested on clean source.**
- **The faithfulness cascade was validated on constructed fixtures** plus one
  narrative corpus. Its hard tier keys on numbers and named entities, which is
  exactly what legislative and standards material is dense in and devotional
  prose is not.

**The corpus is the instrument, and until 2026-08-23 the instrument itself was
broken.** F45 found the sentence splitter's line-based fallback distorting
clause-marker density; F48 found the fix (nupunkt, a legal-domain splitter); F52
re-profiled every document on it. **That is what unblocks this task** — and it
also means **every marker-density or severance number produced before that date
must be re-derived, not compared against.**

**What exists to support it.** `/corpus` — deliberately NOT a job queue;
uploading creates no job and consumes no cluster time — with per-document
profiling that says whether a document can answer a given question at all: chunk
count at the current `CHUNK_TOKENS`, clause-marker density, numeric density, a
plain-language usability verdict, and now a `sentence_splitter` stamp per row.
Plus HTML extraction, RTF refusal, two-scope hard checking, and a canonical
entity index. **17 documents, 403 chunks, 7.4 M characters** across four genres:
legislative 6 (including four dated Privacy Act 1988 point-in-time compilations),
nist standards 4, regulatory 5 (OAIC determinations), standards 2 (ISM).

**The work, in order:**

1. **Re-run chunk-boundary severance on the nupunkt instrument.** This is the
   measurement F45 invalidated and F48/F52 made possible. `docs/chunk-boundary-
   measurement.md` carries a banner saying every figure in it came from the old
   splitter — the banner comes off when the re-run lands, not before.
2. **Settle `--entity-rules strict` on clean source.** It is the setting most
   likely correct for production and the only reason it is not the default is
   that nothing clean had been measured. The corpus is now clean source.
3. **Re-validate the faithfulness cascade's hard tier on legislative and
   standards text**, which is dense in exactly the numbers and named entities it
   keys on.
4. **The revisions experiment, which is novel and which nothing else does.**
   Several compilations of one Act are near-identical documents differing in
   small, real, dated ways. That is the only clean way to test whether the
   checker distinguishes **a genuine difference between two sources** from **a
   fabrication** — and `docs/market-research.md` found **no mainstream tool
   handles disagreeing sources at all.** Nothing is built for this yet.

**Read `docs/corpus-selection.md` before assuming the picks are right.** It
demotes the ISM from "primary sourcing pick" to "extraction stress test", and
**promotes OAIC Commissioner-initiated investigation determinations to #1**,
because they are the closest public proxy to a real internal incident
investigation — which is what a sensitive-sector office *writes*, whereas the Act
and the ISM are what it *reads*. It also found **Hansard's CC BY-NC-ND genuinely
conflicts with this pipeline**, and records that `ombudsman.gov.au` sits behind a
Cloudflare JS challenge and 403s a plain fetch.

**Do not treat the corpus as test fixtures.** It is the measuring instrument, and
this project's repeated lesson (F17, F31, F39, F40, F41, F45) is that a confident
result from a mis-specified instrument is worse than no result.

**One gap F46 leaves that belongs here:** the deterministic citation audit has
**no regression harness.** It was run by hand via `cascade job --mode citations`,
and nothing would catch a regression in it — including a re-tightening of the
tolerant `\s` in `_SECTION_MARKER_RE` that was the only reason 7 of 7 valid
citations resolved at all.

### 1. Measure `GLM-4.7-Flash` — the only live model question

**Model B is closed (F47) and this is not a reopening of it.** It is a candidate
to *replace the incumbent*, gpt-oss-120b, at S=1. On paper it beats gpt-oss on
every axis this project ranks: **31.2 B total** (CONFIRMED from HF safetensors),
**~3.6 B active** (computed, reconstruction within 2%), **9.3% Vectara** against
gpt-oss's 14.2%, **MIT**, **18.3 GB at Q4_K_M**, predicted ~8.2 tok/s, llama.cpp
support merged before our pin — and **~30 minutes of link time to fetch** at the
measured 11.7 MB/s.

**It may not be adopted on that paragraph, and the reasons are specific:**

- It is a **hybrid reasoning model**, and F35 established there is no universal
  thinking-off switch — `enable_thinking` is inert on gpt-oss and unknown kwargs
  are dropped silently. Verify per model; never assume.
- **31 B total is far less stored knowledge than 120 B.** Faithfulness is not the
  only axis.
- Everything but the file sizes and the config is **REPORTED or INFERRED.**

**So: fetch it, run it through `llama-server` at `--parallel 4` against the real
pipeline** — F40's lesson is that a benchmark not reproducing the deployment's
concurrency is not a benchmark of the deployment — and compare wall-clock *and*
output coherence against gpt-oss on the same documents. A faster config that
changes output is not a win.

**The one experiment that would reopen Model B proper:** GLM-4.6 **UD-IQ1_S at
96.9 GB is S=1**, hence replicable at R=N. Whether 1-bit retains enough of the
9.5% to beat gpt-oss's 14.2% is genuinely open, `DESIGN-NOTES.md` H warns UD's
edge shrinks at the low-bit end, and it costs 2.3 h to fetch.

### 2. Converge the fleet's service units — the node-3 branch has landed

This was two mechanical items, both instances of the F32 defect ("a file in
version control is not a file on a node"). **One is done; one is not.**

- ~~**Merge `agent/node3-join`**~~ — **DONE 2026-08-23, and verified against the
  hardware rather than against git.** `./provisioning/distribute.sh` from the
  merged tree printed `Targets (2): node2 debian1@…` / `node3 debian3@…` and
  reported both `ok` at b10369, so the per-node login actually resolves. Re-check
  with that same command rather than trusting this line.
- **Nodes 1 and 2 STILL carry the OLD `llama-server@.service`** (`User=debian1`,
  no drop-in) — merging the branch did not deploy it, which is the whole point of
  F32. They work as they are. Converge with
  `ONLY_NODES=node1,node2 ./cluster/install-services.sh` — **but that also does
  `enable --now rpc-server@50052`, so schedule it when no benchmark is running.**
  **Check what is actually on a node:**
  `systemctl cat llama-server@8080 | grep -n '^User='` plus
  `ls /etc/systemd/system/llama-server@.service.d/` — a node with no drop-in
  directory has not been converged.

**Two fixes in that branch worth understanding before touching the unit**, because
they look like over-engineering and are not: `EnvironmentFile` **cannot** supply
the per-node user, since systemd expands `${VAR}` only in the `ExecStart` family
and `User=` resolves before any environment exists — it dies 217/USER. It is done
with a drop-in. And the tracked `User=` is a **placeholder resolving to nobody**,
so a missing drop-in fails loudly rather than silently running the inference
server as root; the drop-in is written *before* the unit, so a half-finished
install is never the dangerous half.

**Also still owed from F56:** `setup.sh` does not restart `systemd-journald`
after regenerating `machine-id`, so **node 4 will orphan its journal exactly as
nodes 2 and 3 did**; and `setup.sh` asserts `THP: expected [madvise]` while all
three nodes report `[always]` — a stale assertion in the script.

### 3. The fabrication-amplification pilot — BUILT, NEVER RUN, and parked

**The harness exists and has never been executed. The operator's instruction is
to drop it for now.** Recorded here so nobody either re-builds it or quietly runs
it: it is a decision, not an oversight.

**What F49 established while building it, which stands regardless:**

- **Whole-document single-pass is arithmetically impossible here.** At
  `n_ctx_slot = 8192` — read from the startup log, not inferred from `-c` — minus
  the 2048 output budget, the ~150-token wrapper and the 15% headroom rule, the
  single-pass source budget is **5,094 tokens**. **1 of 17 documents fits** at a
  realistic 1.3 tok/word. Exact McNemar cannot reach p<0.05 below 6 discordant
  pairs, so one to four total pairs is **not an underpowered experiment, it is no
  experiment** — and it would have produced a null reading as "no amplification".
- **So the unit is a paragraph-aligned SECTION**, both arms receiving
  byte-identical text. **What is lost is stated, not hidden: fewer chunk
  summaries than a full document, so any amplification measured is a LOWER
  BOUND.** Yield ~253 sections corpus-wide; `--per-doc 4` gives ~53.
- **Cost, from `docs/measurements.md` only:** 21.7 min per section for both arms.
  6-section pilot **1.3 h on two nodes**; the recommended 53-section run
  **11.9 h**. `analyse` prints an explicit UNDERPOWERED guard below 6 discordant
  pairs and refuses to let a null read as "no amplification".
- **A pairing hazard specific to running this on a cluster:** dispatch is by
  SECTION, never by (section, arm) — splitting a pair across nodes puts a node
  difference *inside* the pair, indistinguishable from the effect. `run` refuses
  endpoints serving different models and `analyse` excludes cross-node pairs.

**If it is ever unparked, run the 6-section pilot first**, because the pilot's
observed event rate is what says whether 53 sections can clear 6 discordant
pairs, and that is much cheaper to learn in 1.3 h than in 11.9.

### 4. The coldstore disk layout — DESIGNED, NOT EXECUTED, awaiting the operator

**Blocked on the operator's say-so and must stay blocked, because it destroys an
existing filesystem** — empty or not. Do not run it on your own initiative.

Node 3's "1 TB" is a 7200 rpm SATA disk carrying a **bare NTFS format with 128 MB
used of 932 GB and no prior Windows data at all**; SMART PASSED, 2,789 power-on
hours, 0 reallocated and 0 pending sectors — effectively a new drive. The same
drives are going into nodes 1 and 2.

**Recommended layout:** single GPT partition, ext4, `LABEL=coldstore`, mounted
`/srv/coldstore` from `fstab` **by UUID with `nofail`** — a spinning disk that
fails to mount must not block a headless boot.

**Purpose is snapshot and cold storage. Models stay on NVMe** (F16 makes disk the
binding constraint; F3 makes model load single-core serialised, and loading 65 GB
off rust would make that materially worse). **The payoff is larger than expected:**
a fleet-local GGUF mirror re-provisions at ~120 MB/s off local rust instead of
~11.7 MB/s over the 100 Mb LAN — **~9 minutes per 65 GB instead of ~97.**

### 5. The fleet-wide GNOME trim

**Do this fleet-wide or not at all.** F53 recorded node 3 idling at 3,251 MB as a
full GNOME desktop and framed it as a divergence; **F56 corrected that — nodes 1
and 2 run the identical stack** (`gdm`, `cups`, `cups-browsed`, `avahi-daemon`,
`colord`, `geoclue`, `packagekit`, `ModemManager`, `switcheroo-control`,
`upower`, `udisks2`), and node 2's default target is `graphical.target` too.
**Trimming node 3 alone would make it the odd machine out and confound any A/B
against the three-way bandwidth twin it just joined.**

Its own task, **with a before/after RAM measurement**, fully reversible
(`systemctl disable` plus `set-default multi-user.target`, no purging). It is
~2.5% of 128.7 GB.

### 6. The cybersecurity teaching playground

**New scope, 2026-08-23.** The cluster is also becoming a teaching environment —
ComfyUI and n8n for roughly 20 students on the DMZ LAN. Surveys are done:
`docs/comfyui-feasibility.md`, `docs/n8n-feasibility.md`, and **F54**.

**`CLAUDE.md`'s "do not write security guidance into this project" applies to the
CLUSTER'S OWN posture and does not forbid the lab work.** Building the playground
and writing teaching material about how these systems fail is in scope; writing
hardening advice, or implying the tooling makes a network safe, is not.

**What the surveys settled:**

- **ComfyUI is compute-bound the wrong way for this fleet.** 7–13 min per
  512×512 20-step SD1.5 image per node — a class of 20 queues 3+ hours. **The
  rescue is step count, not tuning:** SD-Turbo at 1 step ≈ 25–35 s. **SDXL at
  1024² is 1–2 h/image and video is tens of hours to days per 5-second clip, so
  the deepfake-video demo as imagined is not reachable on this hardware.** And
  the one CPU diffusion benchmark with a published memory figure peaks at
  **6.94 GB — 5% of a node's RAM**: diffusion leaves idle exactly the resource
  this fleet has. **This project's argument is that old hardware is useful for
  MEMORY-BOUND work; it has never claimed it is useful for compute-bound work.**
- **n8n fits**, is licence-permitted for this use with one grey edge worth **one
  email to `license@n8n.io`**, gives unlimited users on Community (CONFIRMED from
  `license.ts`, contradicting a widely-repeated forum claim), and its local-LLM
  integration needs **no custom code** — point the OpenAI credential's Base URL
  at `http://<node>:8080/v1`, switch off the Responses API default, and set the
  URL on the credential rather than the node.
- **Placement is decided by F44, not preference: both belong on node 3, not on an
  inference node.** ComfyUI holds all cores for its whole runtime and `nice` is
  not a mitigation — F44 tested exactly that.
- **If a GPU is wanted**, a used **RTX 3060 12 GB**. Two P510-specific gotchas:
  the 490 W PSU ships a single 6-pin drop where the 650/850 W shipped 6+8, and
  **do not substitute an Intel Arc B580** (needs Resizable BAR, which C612/X99
  firmware generally does not expose). **The Quadro P600 is rejected a second
  time on new grounds:** PyTorch dropped Pascal from its CUDA 12.8 binaries,
  CUDA 13.0 drops sm_61, and ComfyUI now names CUDA 13.0 — using it means pinning
  an unpatchable stack on a machine students are invited to attack.

**Both follow-up surveys have LANDED — read them before starting anything here,
because between them they already answer most of what would otherwise be
rediscovered:**

- **`docs/distributed-playground.md`** — can either service use more than one
  machine? **ComfyUI: third-party only**, and distribution improves its
  throughput while being unable to improve its latency, since nothing maintained
  splits a single CPU denoise across machines. **n8n: yes, first-party queue mode,
  free on Community.** Plus what an LLM load balancer in front of the fleet buys
  (HAProxy or LiteLLM; **not** Paddler as it currently stands).
- **`docs/teaching-labs.md`** — the student lab exercises themselves, sized
  against the measured token budget (F58).

**Both are design/research only: nothing was installed and nothing was run on
the cluster for either.** Neither has been executed, so treat every number in
them as unmeasured-here unless it cites `docs/measurements.md`.

### 7. Operator-named infrastructure with no spec on disk yet

**Listed so they are not lost, and flagged so nobody builds the wrong thing.
Scope each with the operator before implementing.**

- **The `:80` directory page.** The only place authentication and per-student
  rate limiting can live, and the only thing that makes the fleet navigable.
  Sketched in both feasibility docs (`docs/comfyui-feasibility.md` §4,
  `docs/n8n-feasibility.md` §6.2) as a small nginx or Caddy serving a static
  index over 8000 / 8188 / n8n. **`llama-server` on 8080 must not be exposed to
  students** — it is unauthenticated and `--parallel 4` means four of them can
  starve the queue.
- **Snapshotting.** Distinct requirements for each service: what ComfyUI and n8n
  actually keep is enumerated in their surveys (`docs/comfyui-feasibility.md` §6,
  `docs/n8n-feasibility.md` §5), and the cluster-side target is the coldstore
  disk in §4 above. **Nothing is built.**
- **Grafana / monitoring.** Named by the operator; **no design exists on disk.**
  Note the standing constraint it must not violate: **liveness may not live
  on-node** (F36, and F40 showed forked abort children inherit the listening
  socket, defeating a process check, a port check and `/health` at once). A
  dashboard that shares the cluster's failure modes is not a monitor.
  `docs/watchdog-research.md` has the dead-man's-switch pattern this should
  probably follow.

### 8. Verify the hooks actually block, once, and record it

**One negative test, never run in ~six days of the guard being cited as a safety
property (F55).** In a throwaway repo, issue a command the guard claims to
BLOCK — `git add -A` — through the Bash tool and see whether you are stopped.
Then check `.claude/hook-audit.log` for a line that is a real framework
interception rather than a direct test invocation.

**Write the result into `docs/FINDINGS.md` either way.** A confirmed block
retires F55's "treat the guard as inert"; a second failure means the fallback
path did not work and the prose in `CLAUDE.md` is still the only enforcement.

### 9. Smaller, still open

- **Faithfulness evaluation stays REFRAMED (F25, and unchanged).** Do **not** try
  to reproduce the leaderboard: distinguishing GLM-4.6's 9.5% from Kimi K2's
  17.9% needs **~260 documents per model**, and separating GLM-4.6 from
  GLM-4.5-Air needs tens of thousands. **Use the leaderboard for ranking — it is
  a better instrument than anything we can build.** Measure instead the thing it
  structurally cannot tell us, which is §3's paired amplification question.
- **LlamaIndex `tree_summarize` as a BASELINE.** The project implicitly claims
  Missing Link beats reaching for the obvious library, and that claim is
  untested. The pipeline audit removed one excuse: **LlamaIndex is
  offline-installable.** **Set timeouts explicitly before running it** — the
  underlying SDKs default `max_retries` to **2** (corrected from an earlier
  claim of 3–6) and each retry re-waits the full 60 s-class timeout, which
  against this backend is a retry storm, not a summary.
- **Chunk-level fan-out within one document**, and **a retrieval-based task
  profile.** Both deliberately out of scope so far; `DESIGN-NOTES.md` G and E
  have the reasoning. They optimise different metrics (one-document latency vs
  aggregate throughput) and should be selected by queue depth.
- **ik at `--parallel 1`, and ik with flash attention explicitly off.** Both
  untested and both might restore F27's win. Neither may be adopted on reasoning
  alone — F40's own lesson is that a plausible configuration was standardised
  from a benchmark that could not exercise the failure.
- **File the ik_llama.cpp defect upstream.** `docs/upstream-ik-2186-draft.md` is
  written and **NOT filed.**
- **`-fa` flash attention and `-ctk q8_0` KV quantisation on mainline** —
  untested, cheap.
- **Measure the real per-node RAM ceiling** now that the 75% rule's citation is
  disproven (F1). At 85% the pooled budget rises ~13%.
- **Re-check `cluster/models.sh pull`.** F28 inverts its peer-over-internet
  preference at 11.7 MB/s LAN vs 21 MB/s from HuggingFace.
- **Get a gigabit switch (~$20–30)** and uplink it to the existing 100 Mb port.
  Preferred over daisy-chaining, which needs N−1 ports per node and does not
  scale past 2.
- **`_SENT_FALLBACK` exists as two identical copies** (`audit.py:168`,
  `chunk_boundary_audit.py:79`) carrying two *different* docstrings.
- **`pip install minicheck` installs an unrelated z3-based package.**
  `requirements-audit.txt` correctly pins the git URL; anyone "simplifying" that
  line gets the wrong package **silently.**
- **`sshpass` is now installed on the coordinator**, because `join-node.sh`
  assumes console access to the new machine and node 3 had no key. Either keep it
  or give `join-node.sh` a documented bootstrap — nodes 4-7 hit the same wall.
- **A frozen-but-listening `rpc-server` with an empty backlog** still answers
  `tcp=accept` and is not caught by the new bytes probe. `RPC_STALL_GRACE = 900 s`
  is a **labelled safety margin, not a measurement.** **Node 2 has no `iptables`
  and no `nft`.**
- **20 of 43 claims in the F46 audit ended at `needs_classifier` and are
  permanently unchecked.** They are correctly *labelled*, but **F41 says the
  classifier cannot close that gap at production chunk length** — this is not a
  backlog item awaiting a classifier run; on current evidence it has no solution.

### Joining node 4

**The 1 → 2 transition holds most of the risk, but F56 measured that it does not
hold all of it** — 2 → 3 found three more latent bugs and the one that was
anticipated (the username) was not among them. **Expect node 4 to find
something.**

`nodes.env` fields are `<hostname> <lan-ip> <ram_mb> <physical-cores>
[ssh-user]`; the 5th is optional and defaults to the coordinator's login.
**Values must be MEASURED, not assumed** — do not copy node 1's, and do not leave
placeholders. `distribute.sh` now **prints its resolved target list** and says
explicitly when it is a no-op, because a silent `exit 0` meaning "nothing to
distribute" once looked exactly like success.

```bash
# 1. Characterise it FIRST -- homogeneity is a result, not an assumption (F29)
ssh <user>@<ip> 'lscpu -p=Core,Socket | grep -v "^#" | sort -u | wc -l; free -m; lsblk -d'
#    and run the STREAM triad -- core count is a BANDWIDTH spec (F12).
#    No compiler on a fresh node: build STREAM on node 1 (same CPU, same glibc,
#    no -march=native) and copy it.

# 2. Add the node to provisioning/nodes.env with MEASURED values, BEFORE step 3.
$EDITOR provisioning/nodes.env

# 3. Provision, distribute, verify
sudo ./provisioning/setup.sh node4
./provisioning/harden-ssh.sh <ip>       # verifies key auth BEFORE disabling passwords
./provisioning/distribute.sh            # asserts version, libc AND ISA
./cluster/install-services.sh
ssh-keygen -R <ip>                      # setup.sh regenerates host keys (F30 item 3)

# 4. RPC smoke test -- the go/no-go gate for upstream bug #26500 (F2/F22)
./bench/two-node-smoke.sh <ip>
```

**Check the smoke test's OUTPUT, not just its exit status.** F21 has produced a
**false negative on this exact gate**: a reasoning model returning empty content
made a healthy cluster look broken (F31). Read the prose, confirm it is coherent
and on-topic, and confirm zero `[create_node] invalid data ptr`.

---

## Where things stand

**Node 1 (coordinator), node 2 (second replica) and node 3 (joined, no inference
endpoint yet).** IPs, roles and per-node logins are in **`network.md`**
(gitignored) and `provisioning/nodes.env`; this file is published, so it names
nodes, not addresses.

**Every cell below is a SNAPSHOT with a re-check command, not a fact.** A
hand-edited status line goes stale the moment a concurrent session changes
something, and node 2's engine has flipped mid-session at least once.

| Item | node 1 | node 2 | node 3 |
|---|---|---|---|
| llama.cpp | b10369 (`6e62ba53`) at `/opt/llama.cpp/bin` — the engine that should be serving (F40). Re-check: `grep LLAMA_BIN /etc/default/llama-server` | mainline as last observed, **but this moved during a session once** — re-check over SSH before trusting it | **b10369, matching the fleet** (verified at join) |
| ik_llama.cpp | `8337e4cd` at `/opt/ik_llama.cpp/bin` — **kept installed, NOT the default.** Old config backed up at `/etc/default/llama-server.ik.bak` | kept installed, not the default | **`8337e4cd`, matching the fleet.** Shipping it here is what exposed `setup.sh` creating only `/opt/llama.cpp` (F56) |
| `llama-server@8080` | serving | serving | **installed, and this unit had never reached ANY node before node 3** (F56). Resolves its user from a drop-in |
| `rpc-server@50052` | active, `-t 4`, user `cluster`. Re-check: `systemctl is-active rpc-server@50052` | active. Re-check over SSH | active |
| `INFERENCE_ENDPOINTS` | yes | yes | **deliberately NOT listed.** Adding it before a server answers parks a Missing Link worker in permanent backoff. Its in-band watchdog logs one `DOWN … Restart=always owns this, watchdog stands off` per minute and correctly restarts nothing (`NRestarts=0`) |
| Models | Qwen3-4B (2.4 GB), gpt-oss-120b F16 (65 GB), Qwen3-Next-80B-A3B-Instruct UD-Q8_K_XL (~93 GB total). Re-check: `ls /opt/models` | gpt-oss-120b, md5-verified 2026-08-17 | **none** |
| Disk | NVMe. Re-check: `df -h /` | NVMe. Re-check over SSH | NVMe 476.9 GB root, **plus a 931.5 GB 7200 rpm SATA disk that is unmounted and contributes zero usable space today** — see NEXT TASKS §4 |
| SSH | — | key-only, hardened | **key-only, verified independently of the hardening script**: a real password login via `sshpass` was rejected `(publickey)`, and `sshd -T` reads `passwordauthentication no` |
| Missing Link | job store, worker, web API, fan-out across R endpoints, queue control, resumable per-chunk persistence, retry-and-resume, live telemetry, per-workflow guidance, section-level citations, a revive route, a failure-history table, the corpus page, the nupunkt splitter, **and a shared-credential auth gate**. Test count and restart timestamp are **snapshots, not facts** — run the commands in the at-a-glance table | n/a | n/a |
| #26500 gate | **PASSED across all three machines** (F31, F56) — a one-time validation, not a live status | | |
| Phase 0 gate | **PASSED**, 2026-08-12 — a one-time validation, not a live status | | |

**`rpc-server`'s unit is TEMPLATED**, so the name is `rpc-server@50052.service`
— plain `systemctl status rpc-server` reports "could not be found" and looks
exactly like a broken install:

```bash
systemctl status rpc-server@50052                        # node 1
ssh <user>@<node-ip> 'systemctl status rpc-server@50052' # per nodes.env, 5th field
```

It runs as the **`cluster`** system user, whose account and tensor-cache
directory (`/var/lib/cluster/.cache/llama.cpp/rpc`) nothing created until F30.

**Not done:** nodes 4+, `GLM-4.7-Flash` measured, Open WebUI, the amplification
run, chunk-level fan-out within one document, a retrieval task profile, watchdog
moved fully off-cluster, ik at `--parallel 1` / flash-attention-off, filing the
ik_llama.cpp defect upstream, the coldstore disk, the fleet GNOME trim, the
playground build-out, the `:80` page, snapshotting, monitoring.

---

## Real production events, 2026-08-18 — not benchmarks, the actual system doing actual work

**Historical, kept because each one is the system failing or recovering for
real rather than under test.** "This session" below means 2026-08-18.

- **The rewritten watchdog fired twice for real on node 1**, both against
  genuine wedges, both confirmed directly from
  `/var/lib/llama-watchdog/restarts.jsonl` this session (not carried over
  from a commit message): `2026-08-18T09:13:23+10:00` and
  `2026-08-18T11:05:43+10:00`, both `"reason":"wedged"`,
  `streak_s` past the 300 s threshold (355 s), `/health` and `/props` both
  silent, `cpu=0ms/10002ms(0%)`. Zero false restarts recorded in the same
  file over the session.
- **Job `6c0358825609` failed, retried and resumed unattended.** Confirmed
  from `/opt/missing-link/jobs.sqlite` (read-only) this session: `attempts =
  2`, `resumed_chunks = 4`, final `status = done`. Its stored error is
  explicit about what happened: *"attempt 1 of 4 failed and will be retried
  in 60s. The inference backend went away — this is not a problem with the
  document. The 4 chunk summaries already completed are kept and will be
  reused, so the retry resumes rather than restarts. Last error:
  RemoteDisconnected."* This is the retry-and-resume feature (`b7114cf`)
  doing exactly what it was built for, on a real backend failure, not a
  test.
- **The deterministic cascade caught a genuine fabrication in a real
  summary** — F42. A reduce step asserted a death year present in none of
  the five chunk summaries nor the source document; a plain number-in-span
  check flagged it, no model was consulted to catch it. See F42 in
  `docs/FINDINGS.md` for the full account.

---

## Joining a node

**The username no longer has to match the coordinator.** It used to — the
systemd unit, `scp`/`rsync` targets and every `ssh` in the scripts assumed one
login fleet-wide. Node 3's admin account is not `debian1`, and rather than rename
the machine the operator chose to **parameterise**, because the eventual skill
must run where usernames do not match. `nodes.env` grew an optional 5th field;
`distribute.sh` (eight call sites), `install-services.sh` (five) and
`llama-server@.service` all read it. **F56 has the mechanism, including why
`EnvironmentFile` cannot solve `User=`.**

**Generate the on-machine steps with `./provisioning/join-node.sh` on the
coordinator** rather than copying from here — it substitutes the real key and LAN
IP, so it cannot go stale. What it prints is, in outline:

```bash
# 1. An admin account (any name -- record it as nodes.env's 5th field)
sudo adduser <user>                      # skip if it already exists

# 2. Passwordless sudo. Append to /etc/sudoers, NOT a sudoers.d drop-in --
#    sudo is last-match-wins, and a later "(ALL:ALL) ALL" line silently
#    overrides a NOPASSWD rule in sudoers.d. This bit us on node 1 (F9).
echo "<user> ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

# 3. SSH server
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh

# 4. Authorise the coordinator's key (join-node.sh prints the real key)
sudo -u <user> mkdir -p /home/<user>/.ssh
sudo -u <user> tee -a /home/<user>/.ssh/authorized_keys <<'KEY'
<PASTE THE COORDINATOR'S PUBLIC KEY -- run ./provisioning/join-node.sh to print it>
KEY
sudo chmod 700 /home/<user>/.ssh
sudo chmod 600 /home/<user>/.ssh/authorized_keys

# 5. Report the LAN IP
ip -br addr | grep -v LOOPBACK
```

**`join-node.sh` assumes console access to the new machine.** Node 3 had no key
and no console to hand, which is why `sshpass` is now installed on the
coordinator. Nodes 4-7 hit the same wall: either keep `sshpass` or give
`join-node.sh` a documented bootstrap.

The coordinator's own address and the gateway are in **`network.md`**
(gitignored). Everything after step 5 the coordinator does over SSH.

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

## The web UI

**Missing Link's own job-queue UI, not Open WebUI.** Open WebUI (plan Task 9) is
a *chat* frontend, and this workload is explicitly asynchronous — nobody is
waiting at a prompt — so it is still not deployed and is no longer a settled
decision. See `docs/DESIGN-NOTES.md` F and `docs/REQUIREMENTS.md`.

It runs as `missing-link.service`; `./missing-link/start-ui.sh` exists for
running it by hand. Reach it over Tailscale at port 8000 — **address in
`network.md`**, gitignored, because this file is published.

**It now requires the credential.** `ML_AUTH_TOKEN` from
`/etc/default/missing-link`; the browser is prompted once for the whole origin,
so the server-rendered forms need no template change. `/health` is the only open
route. **Do not write the token into this file.**

**It fans out across R endpoints and no longer targets one.** `LLAMA_URLS` names
the serving endpoints; `INFERENCE_ENDPOINTS` in `nodes.env` is the inventory it
comes from, kept deliberately separate from the RPC endpoint list. Proven live on
hardware, not merely merged — see "Current state, in detail".

**Verified working end-to-end 2026-08-17:** a records-retention memo submitted
over HTTP was claimed by the background worker and processed against
gpt-oss-120b. Form field is `document` (not `text`), `kind` is one of
`summarise` / `report` / `qa`. **Read the API before calling it** — Missing Link
is FastAPI, so `GET /openapi.json` is free and authoritative.

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

## Hardware — nodes 1, 2 AND 3 MEASURED; 4+ unknown

| | Node 1 | **Node 2** | **Node 3** |
|---|---|---|---|
| CPU | Xeon E5-1620 v4 — **4 cores / 8 threads** | **identical** | **identical, down to family/model/stepping 6/79/1 and microcode `0xb000040`** |
| ISA | AVX2, FMA, F16C. **No AVX-512** | **identical** | **identical — the sorted flag sets diff to ZERO differences against node 1**, so no SIGILL risk (F8) |
| RAM | **131.8 GB** — 4 × 32 GB DDR4-2400, all four channels | **identical, confirmed by `dmidecode`** | **128707 MB — genuinely 2 MB under nodes 1/2.** Use the measured value (F29) |
| **Achievable bandwidth** | **28.4 GB/s** (STREAM, 2026-08-17 re-run; 28.2 on 08-12) | **27.9 GB/s** | **27.6–27.7 GB/s** at 4 threads. The 4-thread peak and the SMT penalty (F10) reproduce for the third time |
| Disk | NVMe 477 GB. Re-check: `df -h /` | NVMe 477 GB. Re-check over SSH | NVMe 476.9 GB root (**same model as node 1's**) **plus a 931.5 GB 7200 rpm SATA HDD, unmounted, contributing zero usable space today** |
| Board | LENOVO 30B2S2E800 (ThinkStation P510) | **identical** | Lenovo ThinkStation **P410**, BIOS S00KT52A — same chassis family and same BIOS as node 1 |
| Network | **100 Mb/s** (not gigabit — F28) on `eno1` | **100 Mb/s** | **100 Mb/s full duplex — F28 holds for a third machine** |
| Other | — | — | Python 3.11.2 (on nupunkt's floor); **no compiler**, so STREAM was built on node 1 and copied; `machine-id` and all six SSH host-key fingerprints distinct (F30 clear); swap was ENABLED on arrival (975 MB) and timezone US/Eastern — both corrected at provisioning |

**The fleet is a THREE-WAY bandwidth twin, and that is a measured result each
time, not a property of the fleet** (F29, extended by F53). Nodes 1 and 2 are
1.8% apart; node 3 lands within the same band, and its 6-thread dip matches node
2's figure exactly. So `--tensor-split` by RAM is also correct by bandwidth here.
**Do not assume it for nodes 4+**; re-run STREAM per node, and note there is no
compiler on a fresh node — build the binary on node 1 (same CPU, same glibc, no
`-march=native`) and copy it.

**All three nodes are full GNOME desktops, and F56 established that is the
fleet's NORMAL, not node 3 being odd.** ~2.5% of RAM. Trim fleet-wide with a
before/after measurement or not at all — see NEXT TASKS §5.

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
| Memory bandwidth (STREAM), **node 3** | **27.6–27.7 GB/s** at 4 threads — **a three-way twin** (F53) |
| Optimal threads | **4 = physical cores.** `-t 8` is 26% slower. **Reproduced on node 2** |
| Generation efficiency, dense | **~99% of STREAM** |
| Generation efficiency, **sparse MoE** | **~61% of STREAM** |
| RPC overhead, **localhost (protocol only)** | generation **−5.8%**, prefill **−39.1%** (F50; reproduces F14's −5.2%/−39.4%) |
| RPC overhead, **whole model on node 2 over the LAN** | generation **−39.2%**, prefill −38.8% — the wire adds a further **−35.4%** on generation (F50) |
| RPC overhead, **two RPC devices, `-ts 1/1`** | generation **−46.1%**, prefill −40.7% — the second device adds **−11.4%** (F50). This is the old "−47%" figure, now reproduced properly |
| RPC overhead, **local CPU + node 2, `-ngl 18`** | generation −48.0%, prefill **−23.8% — 29% better on prefill than `-ts 1/1`.** Never put a loopback `rpc-server` in the path for the local shard (F50) |
| **Why the generation penalty never amortises** | the output-layer device returns `n_vocab × f32` **per token** — 593.5 KiB for Qwen3. Predicted +51.9 ms/token and 445 MiB; **measured 52.4 ms and 443 MiB.** It scales with `n_vocab`, **not** model size (F50). Prefill is immune: a batch returns logits for the final position only (+0.6%) |
| **LAN throughput** | **93.8 Mbit/s = 11.7 MB/s.** NOT gigabit (F28) |
| **LAN RTT** | **0.827 ms idle, 9.544 ms saturated** — 11.5× bufferbloat |
| gpt-oss-120b, single node | pp2048 **16.08**, tg128 **6.05** t/s |
| Qwen3-4B, single node | pp2048 28.33, tg128 **11.49** t/s |
| TTFT @ 2214 tokens (4B model) | **89 s** |
| Batching (MoE) | 1.79× at batch 4; **collapses at 8** |
| ik_llama.cpp vs mainline, `llama-bench` (single sequence) | prefill +52%, generation −14%, net +22% — **superseded, see next row** |
| ik_llama.cpp vs mainline, through `llama-server` at `--parallel 4` (the actual deployment) | prefill **+43% decaying to +15%** as KV fills, generation **indistinguishable (~5.2–5.3 t/s both)** — but **ik fatal-errors on request 5 of any such job (F40); mainline is the fleet default** |
| **Aggregate throughput, 2 replicas** | **~1.8× (1.86× prefill / 1.77× completion, adjusted; 1.62/1.55 raw)** |
| Chunk size vs wall-clock, real pipeline, mainline | **U-shaped, optimum at 4096 tokens** — 1.85× worse at 1024, 1.29× worse at 6144 |
| Sentence splitter, real corpus | **nupunkt beats the regex and pysbd both.** Structural fragments 65.0% → **12.3%**; legislative marker rate 2.85% → **10.53%**; pysbd scored **2.76% — worse than the regex — and took 133.94 s to be worse** (F48, F52) |
| Tokens per word, within ONE document | **0.53–1.08.** `WORDS_PER_TOKEN = 0.70` is wrong by up to 2× in both directions, and `POST /tokenize` is free (F49) |

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
- [x] ~~Nodes 2+ hardware~~ — **nodes 2 and 3 measured; a three-way bandwidth
      twin** (F29, F53). Nodes 4+ still unknown; core count is a bandwidth spec,
      and homogeneity is a result to be re-derived, not a fleet property.
- [x] ~~**Rigorous two-node sharding A/B**~~ — **DONE (F50).** −5% and −47% are
      the same system at different topologies: protocol −5.8%, the 100 Mb wire a
      further −35.4%, the second device −11.4%. **And the penalty scales with
      `n_vocab`, not model size, so it never amortises** — correcting a standing
      guess in `docs/measurements.md`.
- [ ] **Does the 61% MoE efficiency generalise?** Measured on gpt-oss (128
      experts, top_k=4). Kimi K2 has 384 at top_k=8 — a more scattered gather
      could be worse.
- [ ] **Real per-node RAM ceiling** now that the 75% citation is disproven (F1).
- [x] ~~**Model B**~~ — **CLOSED IN THE NEGATIVE (F47).** Every frontier
      candidate is priced and dominated; "one model too large for any single
      machine" cannot be justified on this fleet. A decision, not a deferral.
- [x] ~~**GLM-5 active params**~~ — **40.8 B, computed from `config.json`**, the
      arithmetic validating itself to **0.00%** against the published 753.86 B
      total. More than DeepSeek-V3.2's 37 B, and *less* faithful than GLM-4.6.
- [x] ~~**Finix S1 32B**~~ — **no public weights (HTTP 401)**, and its 1.8% is a
      summary-length artifact: 172.4 words average against a 106.9-word median.
      **A hallucination score cannot be read without the summary length beside
      it.**
- [ ] **`GLM-4.7-Flash` vs the gpt-oss-120b incumbent** — the only live model
      question, and cheap to settle by measurement. NEXT TASKS §1.
- [ ] **Does 1-bit GLM-4.6 (UD-IQ1_S, 96.9 GB, S=1) keep enough faithfulness to
      beat gpt-oss?** The one experiment that reopens Model B. 2.3 h to fetch.
- [ ] **Does the patched `PreToolUse` hook actually block?** Unverified since
      F55; needs a fresh session and one negative test. NEXT TASKS §8.
- [ ] **Is `--entity-rules strict` right on clean source?** 100% near-miss catch
      at 17.3% FP, measured only on OCR-damaged text. The corpus is now clean.
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
- **GLM-5 / 5.1 / 5.2** — **40.8 B active**, computed from `config.json` and
  validated to 0.00% against the published total (F47). More active than
  DeepSeek-V3.2, *less* faithful than GLM-4.6, 402.9 GB against 368 GB free, and
  a thinking model with no reliable off switch (F35).
- **Finix S1 32B** — **no public weights** (HTTP 401), and its 1.8% hallucination
  rate is a **summary-length artifact** (F47). Do not cite the number.
- **"One frontier model too large for any single machine" (Model B) as a
  concept** — closed in the negative (F47), not deferred. Reopen it only with the
  GLM-4.6 UD-IQ1_S measurement, which is S=1 and therefore a different question.
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
- ~~**Chunk size barely matters for map-reduce**~~ — **true for QUALITY, which is
  what its source (BooookScore) measured, and WRONG for wall-clock.** A real
  sweep on the real pipeline found wall-clock **U-shaped with a 1.85× spread**
  and a minimum at 4096 tokens. ~4K with 10% overlap is right, but it is a
  measured optimum, not a "does not matter". See `docs/measurements.md`.
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

**Newest first. The session-by-session detail lives in `docs/CHANGELOG.md`; this
is the one-line-per-turning-point version.**

- **2026-08-23** — **NODE 3 JOINED. The cluster is N=3, and it is a three-way
  bandwidth twin** (27.6–27.7 GB/s, F53). **2 → 3 found three more latent bugs
  and the anticipated one was not among them** (F56), which retires this
  project's "nothing new appears at the tenth node" claim. **Model B closed in
  the negative** — every frontier candidate priced and dominated, GLM-5 computed
  at 40.8 B active and *less* faithful than GLM-4.6, Finix S1 a summary-length
  artifact with no weights (F47). **The sentence splitter is now `nupunkt`**, a
  legal-domain library nobody had searched for, which beats both our regex and
  the most-recommended alternative; the whole corpus was re-profiled on it
  (F48, F52). **The two-node sharding A/B settled the −5% vs −47% dispute** — both
  right, different topologies, and the penalty scales with `n_vocab` so it never
  amortises (F50). **The watchdog was found to make large-model sharding
  impossible** and was fixed to probe bytes moved rather than port acceptance
  (F51). **The agent-hardening hooks were found never to have fired, in the
  repo's entire history** (F55). **Missing Link got a shared credential** (F54),
  fan-out went live and was proven on hardware, and the citation-accuracy audit
  ran deterministically on real output — finding a correct citation anchoring a
  false sentence (F46).
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
- **2026-08-18 (morning)** — **F40: ik_llama.cpp reversed, mainline restored
  fleet-wide.** ik fatal-errors deterministically on the 5th request of any
  `--parallel 4` job (a forked-abort deadlock in `ggml_abort`'s crash
  reporter, invisible to `Restart=always` and a port check because the
  forked children inherit the listening socket), and this — not a client
  disconnect — is F36's real cause. Re-measured through `llama-server` at
  four slots, ik's real prefill advantage is +43% decaying to +15%, not a
  flat +52%, and the −14% generation penalty does not appear at all; neither
  matters because it does not survive to the fifth request. Agent hygiene
  hooks landed the same window (`.claude/hooks/cluster-guard.py`) after
  `docs/REQUIREMENTS.md` asked for a hardening pass ahead of the Skill.
  Multi-node, multi-service watchdog coverage, retry-and-resume on backend
  failure, a revive route, section-level citations on the reduce output, a
  real failure-history table, and opt-in chunk-boundary snapping (default
  off) all landed. A two-model faithfulness audit ledger was built, then
  shown not to survive production scale (F41 — recall fell from 1.00 to
  0.43 on the exact fabrication class it was built to catch) and effectively
  superseded by a deterministic string/number-in-span cascade, which caught
  a real fabrication in a real reduce-step output (F42). **Test count 193 →
  469.**
- **2026-08-18 (afternoon)** — **This entry: closing the gap between what
  had actually happened and what `STATUS.md`/`docs/measurements.md` said had
  happened.** The completed chunk-size sweep (`-c 32768`, mainline, node 2,
  the real document) was recorded in `docs/measurements.md` for the first
  time: **4096 tokens is the measured wall-clock optimum, U-shaped, 1.85×
  worse at 1024 and 1.29× worse at 6144** — displacing a throughput claim
  that had stood in `CLAUDE.md`, `STATUS.md` and `worker.py`'s own comments
  since the plan (the underlying *quality* claim was correct and was kept;
  only the throughput claim was wrong). `worker.py`'s two stale comments
  were corrected; `CHUNK_TOKENS` itself did not need to change, it already
  sat at 4096. Two findings that existed only in commit messages were
  promoted to `docs/FINDINGS.md`: **F43** (node 2's watchdog timer fired
  every minute and failed on every tick with an unbound `$HOME`, so it was
  silently unmonitored from install) and **F44** (a CPU-bound sidecar
  measurably starves `llama-server` on this 4-core hardware even at `nice -n
  15`), plus an addendum to F40 confirming — independently, by reading
  `/opt/llama.cpp/src/ggml/src/ggml.c` on node 1 this session — that
  mainline shares the identical fork/waitpid abort path, so the forked-abort
  hang hazard is not retired by the engine reversal alone. Two items this
  file previously called "in flight" had in fact already finished: the
  extended `-c 65536` chunk-size sweep (results complete on disk, write-up
  intentionally left to whoever owns it) and the citation-test job
  `18339bace8f0` (finished, and its output does carry well-formed `[Section
  N]` markers on real output — citation *accuracy* is still unverified).
  Also confirmed live and corrected in this file: the engine actually
  serving on node 1 is mainline, not ik (F40 had already decided this, but
  had not been reflected in `STATUS.md`'s tables); `missing-link.service`'s
  running process postdates all the relevant merges; `LLAMA_URLS` is unset
  in production despite fan-out being fully built, so only node 1 is
  actually used **[superseded 2026-08-23 — `LLAMA_URLS` is now set and fan-out
  is proven live; see "Current state, in detail"]**; and 50, not "41+", commits
  were unpushed to `origin/main`.

# Status

**Updated:** 2026-08-18 (afternoon)
**Phase:** **N=2. Node 2 joined, provisioned, characterised and serving.**

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
| **Check before trusting anything below** | **Push state:** `git fetch -q origin && echo "ahead: $(git log --oneline origin/main..HEAD \| wc -l)  behind: $(git log --oneline HEAD..origin/main \| wc -l)"`. **Test count:** `cd missing-link && .venv/bin/python -m pytest tests/ -q \| tail -1`. **Node 1 services:** `systemctl is-active llama-server@8080 missing-link rpc-server@50052`. **Node 2 services + engine:** `ssh debian1@<node2-ip> 'systemctl is-active llama-server@8080 rpc-server@50052; grep LLAMA_BIN /etc/default/llama-server'` — node 2's engine has flipped mid-session at least once (see "Where things stand" below); a stale reading of it is wrong, not just old. **Is fan-out actually in use:** `grep LLAMA_URLS /etc/default/missing-link` — the code fans out across R endpoints, but this variable has been observed unset (silent single-endpoint fallback) more often than set; the code being merged does not mean it is live. **Disk free:** `df -h /` on each node. |
| **What is running** | `llama-server` on **nodes 1 and 2**, `rpc-server@50052` on both, and **Missing Link** on the coordinator (node 1) — all as systemd units. N = 2. This is the steady-state shape; use the row above to confirm it is actually true right now rather than trusting this sentence. |
| **Which engine, and why** | **MAINLINE llama.cpp b10369** (`/opt/llama.cpp/bin`), fleet-wide. **Not ik_llama.cpp** — it was adopted on a prefill win (F27) and then dropped, because it fatal-errors on the 5th request of any `--parallel 4` job, i.e. on any document longer than four chunks, leaving a hang `Restart=always` cannot see (**F40**). ik stays installed at `/opt/ik_llama.cpp/bin` for the still-untested `--parallel 1` configurations only. **Do not point a serving node at it.** |
| **Next task** | **§0, the corpus-driven benchmark** — the operator's stated priority. Re-run the three measurements that were blocked or weakened by the corpus (chunk-boundary severance, the entity signal's false-positive rate, the faithfulness cascade) against the real public documents now on the `/corpus` page — legislation, standards, regulator determinations — instead of the two narrative texts every earlier measurement had to use. Full brief under **NEXT TASKS, in order** below. |
| **Never do this to the live services** | Do not stop, restart or reconfigure `llama-server`, `rpc-server@50052` or `missing-link` without first checking whether a job is running — a restart destroys that job's in-flight work, which is exactly how F39 lost 10m55s of completed work. Do not benchmark a node while it or its peer is doing real work. Never `pkill -f`; never `git add -A` — `.claude/hooks/cluster-guard.py` blocks both, and gates service control, `git push`, writes to `/opt/models`, and mutating SQL against the live job store. |
| **Where the history went** | The session-by-session "MERGED" / "completed this session" narrative that used to sit here is now in **`docs/CHANGELOG.md`**, intact. |

Everything below expands on that table. If you only have time for one more
thing, read the **Index** at the top of `docs/FINDINGS.md`.

---

## Current state, in detail

**The engine choice has flipped and flipped back: both nodes now run MAINLINE
llama.cpp fleet-wide** (`/opt/llama.cpp/bin`), not ik_llama.cpp — see F40 below
and the "Engine" row in "Where things stand". ik_llama.cpp stays installed
side by side and its former `/etc/default/llama-server` is backed up at
`/etc/default/llama-server.ik.bak` on both nodes (confirmed on node 1 this
session; brief-reported, not independently re-checked, for node 2). **Upstream
bug #26500 gate PASSED across real machines.** Missing Link has grown a lot
since the last full rewrite of this file: fan-out across R endpoints, queue
control, resumable chunk-level persistence, automatic retry-and-resume on
backend failure, live per-chunk telemetry with separate prefill/generation
rates, per-workflow guidance (text or file), section-level citations on the
reduce output, a revive route, a per-job failure-history table, and a
deterministic faithfulness cascade, a corpus benchmark page and the
provenance/licence gap it surfaced. **Test count and push state are
deliberately not asserted here as current fact — see "Check before trusting
anything below" in the at-a-glance table for the commands.** As last
observed, 2026-08-18 afternoon: **552 tests** (`cd missing-link &&
.venv/bin/python -m pytest tests/ -q`, 0 failures, 25s; up from 469 at the
previous entry — the corpus feature added its own test files), **45
findings** in `docs/FINDINGS.md` (F45 landed this session), and **60 commits
ahead of `origin/main`**, none behind. That last figure is exactly the kind
of claim that goes stale between sessions — it did, within the same day, and
the correction is why push state is no longer stated as fact anywhere in
this file. **Repo:** https://github.com/GriffynHancock/homogenous-cluster

**THE REPLICATION MEASUREMENT IS DONE, AND IT PASSES.** Aggregate throughput
across two independent `llama-server`s running gpt-oss-120b: **~1.8× on two nodes,
~90% of linear** (1.86× prefill / 1.77× completion, adjusted for one failed
request; 1.62×/1.55× raw). **The replication-first architecture is validated on
real hardware.** Full detail and caveats in `docs/measurements.md`.

**A measured chunk-size sweep displaces a throughput assumption that had stood
since the plan.** "Chunk size barely matters for map-reduce" was true for
*quality* (its actual source, BooookScore) and is now known to be **wrong for
*wall-clock* by 1.85×** between the worst and best size tested, on the real
pipeline against the real 97,299-char document. **4096 tokens — the value
`worker.py` already used — is the measured optimum.** Full table and mechanism
in `docs/measurements.md`, "Chunk-size sweep" section; the stale throughput
claim in `worker.py`'s comments has been corrected this session (the quality
claim, which still stands, was kept).

**Corrections to long-standing assumptions, from measurement:**

- **The network is 100 Mb/s, not gigabit** (93.8 Mbit/s measured). Both NICs are
  gigabit and the switch is the cap. This **inverts** F23's peer-pull preference
  and qualifies the expert-parallelism comms analysis. See **F28**.
- **`nodes.env` had node 1's RAM as 125629 MB, which `free -m` does not report**
  (128709 on both nodes). Corrected — it sets `--tensor-split` ratios. See F29.
- **ik_llama.cpp fatal-errors on the 5th request of any `--parallel 4` job** —
  a 100% failure rate on any document longer than four chunks, and the hang it
  produces is invisible to `Restart=always` and a port check. **Mainline is the
  fleet-wide default now; this is F40, and it is the most consequential
  correction since the last full rewrite of this file.**

**Session history — what merged when, and the reasoning behind each batch — is
now in `docs/CHANGELOG.md`.** It used to sit between here and "NEXT TASKS" and
was the main thing a cold reader had to wade through to reach the actionable
part. Nothing was deleted, only moved.

---

## What is actually in flight right now — read before assuming anything from an older copy of this file

**Two things this file's previous version described as "in flight" have
finished. Verified from the live system this session, not carried over from
the previous entry:**

1. **The `-c 65536` extended chunk-size sweep on node 2 has FINISHED, AND has
   since been written up.** This entry corrects the previous version of this
   file, which said the write-up was still owed — it was not, by the time that
   claim was written. `docs/measurements.md`'s "Chunk-size sweep, extended"
   section (added by commit `35ee0a0`, 16:17, which is *before* the previous
   STATUS entry at 17:49) has the full table and the finding: raising `-c`
   past what `CHUNK_TOKENS=4096` needs costs **33% more wall-clock on
   identical chunking**, not a wash. `CLAUDE.md`'s `-c` standing constraint has
   been corrected to say so. Node 2's `rpc-server@50052` **is active**,
   confirmed by direct SSH this session (`systemctl is-active` → `active`,
   twice, four minutes apart) — the sweep is over and the service is back.
2. **The citation-test job (`18339bace8f0`) has FINISHED, not "in flight."**
   Read directly from `/opt/missing-link/jobs.sqlite` (read-only) this
   session: `status = done`, 7 chunks, finished
   `2026-08-18T03:09:43+00:00`. **Its result DOES contain `[Section 1]`
   through `[Section 7]` markers, one per chunk, in order** — so on this real
   document the model complied with the citation instruction. This is **not**
   a citation-accuracy audit (whether each marker points at the right
   underlying content was not checked here) — it only establishes that the
   model follows the instructed format on real output, which was previously
   unverified. A citation-accuracy pass is still owed.

**The service-restart gap the previous version of this file warned about is
now closed, and this WAS checked this session.** `systemctl show
missing-link --property=ActiveEnterTimestamp` reads **`Tue 2026-08-18
11:53:31 AEST`**, which is after fan-out (`151ed32`, 06:36), resumability and
retry (`a41666e`/`b7114cf`, 06:24/07:48), live telemetry (`2c1be61`, 07:09)
and citations (`7c1266b`, 08:46) — all of it is live in the running process,
not just on disk. (Consistent with this: job `18339bace8f0` was claimed 10
seconds after that restart.) Three later commits — opt-in boundary snapping
(default OFF, so this does not change default behaviour), the faithfulness
cascade (an offline script, not wired into any route) and a chunk-boundary
measurement doc — landed after the restart but do not require one. **Next
restart should still be treated as the thing that would pick up whatever
lands after this entry**, this is just recording that the gap flagged
previously has actually been closed, not assuming it away.

---

## If you are a fresh session

1. **Read `docs/FINDINGS.md`.** **45 findings** from running this on real
   hardware. Several correct the plan or the spec — **and F28 corrects this file
   and F23; F40 reverses the ik_llama.cpp recommendation fleet-wide; F39/F43
   correct the watchdog's own design twice, once for what it was probing and
   once for a bug in the probe itself; F45 corrects the metric the corpus page
   uses to judge a document's usability.** Do not trust the original plan's
   numbers over these, and do not trust an older copy of this file's engine
   choice — it changed, more than once, including during this session (see
   "Where things stand" below).
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
`/opt/llama.cpp/bin` **is now the engine actually serving on both nodes**
(confirmed on node 1 this session via `/etc/default/llama-server`'s
`LLAMA_BIN=/opt/llama.cpp/bin`); ik_llama.cpp stays installed side by side at
`/opt/ik_llama.cpp/bin` for the still-untested `--parallel 1` /
no-flash-attention configurations (F40), not as the default. Models in
`/opt/models`, Missing Link in `missing-link/` (coordinator only).
`rpc-server@50052` is **active on both nodes** at `-t 4` as user `cluster`
— **not independently re-checked on node 2 this session**, since this agent
did not SSH to either node; the previous entry's "active on both" is carried
forward, not re-verified.

**Access:** node 1 is reachable over Tailscale with Tailscale SSH enabled, and a
detached tmux session named `cluster` is waiting on it. **The address is in
`network.md`** (gitignored, site-specific — this file is published):

```bash
ssh -t <coordinator-tailscale-ip> 'tmux new-session -A -s cluster'   # then: claude --continue
```

---

## NEXT TASKS, in order

### 0. THE CORPUS-DRIVEN BENCHMARK — the operator's stated priority for the next session

**"this is a strong direction. this needs to be a large part of next session."** — the
operator, 2026-08-18.

**Why this is first, and it is not a preference.** Every measurement this project
has made ran against whatever documents happened to have been submitted as jobs:
two long narrative/devotional texts and a 2,202-character memo. **Three separate
measurements were blocked or weakened by that, and only that:**

- **Chunk-boundary severance returned 0 of 84 events and could not answer its own
  question.** The only legal-styled document in the store is 2,202 characters —
  too short to produce a single chunk boundary at any size — so it contributed
  zero data points. The two documents long enough to produce boundaries are
  narrative prose at 0.03–1.4% clause-marker density, 50–500× sparser. The zero
  reflects the corpus, not the splitter. See `docs/chunk-boundary-measurement.md`.
- **The entity signal's 15% → 8.5% false-positive rate is dominated by OCR damage
  specific to that corpus.** The model correctly reconstructs transliterations
  the source has mangled, and a faithful claim therefore looks unsupported.
  `--entity-rules strict` measures **100% near-miss catch at 17.3% FP** and is
  probably correct for this project's actual target material — clean digital
  legal and standards text — but is **untested on clean source**. See
  `docs/two-scope-and-entity-index.md`.
- **The faithfulness cascade was validated on constructed fixtures** plus one
  narrative corpus. Its hard tier keys on numbers and named entities, which is
  exactly what legislative and standards material is dense in and devotional
  prose is not.

**So the corpus is the instrument, and it has been the limiting one.**

**What now exists to support this** (all merged 2026-08-18): a corpus page
(`/corpus`) that is deliberately NOT a job queue — uploading creates no job and
consumes no cluster time — with per-document profiling that says whether a
document can answer a given question at all: chunk count at the current
`CHUNK_TOKENS`, clause-marker density, numeric density, and a plain-language
usability verdict on the row. Plus HTML extraction (`legislation.gov.au` serves
HTML, and raw markup made a real Act look like it had a 0.0455 marker rate
against a true 0.1176 — the corpus page would have called it useless), RTF
refusal, two-scope hard checking, and a canonical entity index.

**The work:**

1. **Load real material — DONE, verified from the live store this session.**
   `/opt/missing-link/jobs.sqlite`'s `corpus_documents` table (read-only query,
   this session) holds **17 documents, 403 chunks at `CHUNK_TOKENS=4096`,
   7,416,682 characters**, across four genres: **legislative 6** (four Privacy
   Act 1988 point-in-time compilations — 2005-05-16, 2014-03-12, 2018-02-22,
   2026-06-04 "current" — plus the Notifiable Data Breaches amending Act and
   the Credit Reporting Code), **nist standards 4** (SP 800-53 Rev4 and Rev5,
   SP 800-171 Rev3, SP 800-63B Rev4), **regulatory 5** (five OAIC investigation
   determinations, including the Ashley Madison joint investigation), and
   **standards 2** (ISM April 2019 and June 2026 editions). **The four Privacy
   Act compilations are what make the revision-diff experiment (item 4 below)
   possible** — real, dated, small textual deltas between compilations of one
   instrument, exactly the shape Q5 of `docs/corpus-selection.md` asked for.
   `docs/corpus-selection.md` **exists on disk and carries the ranked
   shortlist this task used** — **read it before assuming the operator's
   picks are the right ones.** Its headline finding: **it demotes the ISM**
   from "primary sourcing pick" to "extraction stress test + revision-diff
   pair" and **promotes OAIC Commissioner-initiated investigation
   determinations to #1** on the shortlist, because they are the closest
   public proxy to a real internal incident investigation — a narrative
   fact-finding account against a named respondent, ending in numbered
   findings against statutory tests — which is what a sensitive-sector office
   actually *writes*, whereas the Act and the ISM are what it *reads*. It also
   found **Hansard's licence (CC BY-NC-ND, NoDerivs) genuinely conflicts with
   this pipeline** (see the new `docs/REQUIREMENTS.md` entry this session,
   2026-08-18) and recommends against using it at all, and it explains why
   **Commonwealth Ombudsman reports were skipped**: `ombudsman.gov.au` sits
   behind a Cloudflare JS challenge and returns 403 to plain HTTP fetch — a
   real constraint on corpus assembly from government sources, not an
   oversight (also recorded as F45's "also worth recording" note).
   **Correction to how this file previously described `docs/corpus-
   selection.md`: it was NOT committed to git** as of the previous entry
   (`git log --all -- docs/corpus-selection.md` returns nothing) — it existed
   only as a file on the coordinator's disk in the main checkout. It has been
   copied into this session's worktree so it ships with these doc fixes; it
   still needs an actual commit to exist once this repo goes public.
2. **Re-run the blocked measurements against it — attempted, and it found the
   instrument itself was broken before it could answer the question.** The
   boundary-severance re-run against the new legislative corpus is **F45**:
   marker density on real legislation came back only 2–4× the narrative
   texts' (1.8–4.9% vs 0.00–1.4%), not the dramatic gap expected, because
   legislation's paragraph-per-clause HTML feeds thousands of short
   structural lines into the sentence splitter's denominator. **The severance
   question itself is therefore still open** — what's now known is that
   marker density is within-genre comparable only, until the splitter
   excludes non-sentence fragments. Read F45 directly, not this summary.
3. **Settle `--entity-rules strict` on clean source.** It is the setting most
   likely correct for production and the only reason it is not the default is
   that nothing clean has been measured.
4. **The revisions experiment, which is novel.** Several compilations of one Act
   are near-identical documents differing in small, real ways. That is the only
   clean way to test whether the checker distinguishes **a genuine difference
   between two sources** from **a fabrication** — and `docs/market-research.md`
   found **no mainstream tool handles disagreeing sources at all.** Nothing is
   built for this yet; the corpus makes it possible.

**Do not treat the corpus as test fixtures.** It is the measuring instrument,
and this project's repeated lesson (F17, F31, F39, F40, F41) is that a confident
result from a mis-specified instrument is worse than no result.


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
"MERGED THIS SESSION" in `docs/CHANGELOG.md`, `151ed32` and `33ddc79`). `missing_link/worker.py`
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

**The code is built, merged and tested, but fan-out is NOT actually enabled
on node 1 right now.** Confirmed directly from `/etc/default/missing-link`
this session: it sets `LLAMA_URL=http://127.0.0.1:8080` (the legacy singular
variable) and does **not** set `LLAMA_URLS` (the plural, comma-separated
variable `app.py` actually reads for fan-out — see `app.py` around line 38).
`worker.py`'s own fallback logic means an unset `LLAMA_URLS` silently
degrades to the single-endpoint behaviour rather than erroring, so this is
easy to miss from the outside — the feature looks live because the service
runs fine, it is just only using node 1. **Setting `LLAMA_URLS` to include
node 2's endpoint in `/etc/default/missing-link` and restarting the service
is a real owed step, not a completed one**, despite everything else in this
section being done in code.

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

- ~~Adopt `ik_llama.cpp` for the document workload (+22% end-to-end)~~ —
  **REVERSED, see F40.** ik_llama.cpp fatal-errors on the 5th request of any
  `--parallel 4` job, a 100% failure rate on real multi-chunk documents, and
  the resulting hang is invisible to `Restart=always`. Mainline is now the
  fleet-wide default. **What is genuinely still open, and untested per F40:**
  ik at `--parallel 1` (removes the interleaving that triggers the bug, at
  the cost of F24's 1.79× batching gain) and ik with flash attention
  explicitly disabled (removes the faulty code path entirely, at the cost of
  most of ik's prefill advantage). Neither may be adopted on reasoning alone
  — F40's own lesson is that a plausible configuration was standardised from
  a benchmark (`llama-bench`) that could not exercise the failure. Also
  still owed: filing the ik_llama.cpp defect upstream — F40 already contains
  the diagnosis and a one-line reproduction, and issue #2186 on the CUDA
  path is the closest existing report, but nothing has been filed against
  the CPU path.
- `-fa` flash attention and `-ctk q8_0` KV quantisation on **mainline**:
  untested, cheap.
- Open WebUI (plan Task 9).
- Measure the real per-node RAM ceiling now that the 75% rule's citation is
  disproven (F1). At 85% the pooled budget rises ~13%.
- A long legal/records-style document, closer to this project's actual
  target material than the philosophical-text corpus the chunk-size sweep
  and F40's reproduction both used, to find the real boundary/context-limit
  behaviour on the kind of document this project is actually for.

---

## Where things stand

**Node 1 (coordinator, user `debian1`, `10.10.0.34`)** and **node 2 (worker /
second replica, `10.10.0.39`)** — both provisioned, both serving.

| Item | node 1 | node 2 |
|---|---|---|
| llama.cpp | b10369 (`6e62ba53`) at `/opt/llama.cpp/bin` — **the ENGINE ACTUALLY SERVING (F40)**. Snapshot, observed 2026-08-18 afternoon from `/etc/default/llama-server`'s `LLAMA_BIN=`. Re-check: `grep LLAMA_BIN /etc/default/llama-server` | **mainline as last observed, but this moved during the same session — see the box below the table, and re-check with `ssh debian1@<node2-ip> 'grep LLAMA_BIN /etc/default/llama-server'` before trusting this cell** |
| ik_llama.cpp | `8337e4cd` at `/opt/ik_llama.cpp/bin` — **kept installed, NOT the default any more.** Old config backed up at `/etc/default/llama-server.ik.bak` (snapshot, node 1, 2026-08-18 afternoon) | kept installed; **actually serving 16:29–18:24 on 2026-08-18** (see below) |
| `rpc-server@50052` | snapshot 2026-08-18 afternoon: active, `-t 4`, user `cluster`. Re-check: `systemctl is-active rpc-server@50052` | snapshot 2026-08-18 afternoon: **active** (`systemctl is-active` → `active`) — the chunk-size sweep that left it stopped is over. Re-check: `ssh debian1@<node2-ip> 'systemctl is-active rpc-server@50052'` |
| Models | Qwen3-4B (2.4 GB), gpt-oss-120b F16 (65 GB), **Qwen3-Next-80B-A3B-Instruct UD-Q8_K_XL — download COMPLETE as of 2026-08-18 afternoon**, both shard files present, ~93 GB total (`ls /opt/models`; the previous entry's "26%" is stale) | **gpt-oss-120b, md5-verified as of 2026-08-17** (not re-checked since) |
| SSH | password auth still ON as of 2026-08-18 (no key installed until that session) | **key-only, hardened** |
| Disk free | snapshot 2026-08-18 afternoon: **248 GB**. Re-check: `df -h /` | snapshot 2026-08-18 afternoon: **375 GB**, down from the previous entry's 437 GB, consistent with the corpus/model activity recorded above. Re-check: `ssh debian1@<node2-ip> 'df -h /'` |
| Missing Link | job store + worker + web API, fan-out across R endpoints (code merged, but as of 2026-08-18 afternoon **`LLAMA_URLS` was unset — only node 1 actually used**; see task 2 above and re-check with `grep LLAMA_URLS /etc/default/missing-link`), queue control, resumable per-chunk persistence, automatic retry-and-resume, live telemetry, per-workflow guidance, section-level citations, a revive route, a failure-history table, a corpus benchmark page. Test count and service-restart timestamp are snapshots, not facts — re-run `cd missing-link && .venv/bin/python -m pytest tests/ -q \| tail -1` and `systemctl show missing-link --property=ActiveEnterTimestamp` yourself; as last observed (2026-08-18 afternoon) these were **552 tests** and `ActiveEnterTimestamp` **17:44:47**, later than the previous entry's 11:53:31, i.e. the service had been restarted again since then, consistent with the corpus feature going live | n/a (coordinator only) |
| Phase 0 gate | **PASSED**, 2026-08-12 — a one-time validation, not a live status | — |
| #26500 gate | **PASSED across both machines** (F31), 2026-08-17 — a one-time validation, not a live status | — |

**Node 2's engine changed while this session was checking it, and that is worth
recording plainly rather than smoothing over.** Direct SSH checks, in order:

1. First check: `/etc/default/llama-server` read `LLAMA_BIN=/opt/ik_llama.cpp/bin`.
2. `journalctl` on node 2 shows `llama-server@8080` started at **16:29:27** running
   ik_llama.cpp at the default configuration — `--parallel 4`, `flash_attn = 1`
   (**not** the untested `--parallel 1` or flash-attention-off variants F40 named
   as still open) — and it ran without a logged crash through at least 18:24, longer
   than the four requests F40 found fatal on a busy job (consistent with a mostly
   idle service in that window, not with the bug being fixed).
3. A **second and third check, four minutes apart, both read
   `LLAMA_BIN=/opt/llama.cpp/bin`** — mainline. `systemctl show` confirms the
   service restarted at **18:24:53 AEST**, a timestamp inside this session's own
   working window. **This agent did not restart it** — this pass is read-only on
   node 2 per its brief. Something else (the operator, or another concurrent
   session) flipped it back to mainline while this documentation pass was running.

**What this means for anyone reading this file next: treat "node 2's engine" as a
live value, not a fact to carry forward.** As of the last check this session
(18:28 AEST), node 2 is on mainline, matching node 1 and the fleet-wide decision.
Re-check `/etc/default/llama-server` before relying on it.

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
moved fully off-cluster, `LLAMA_URLS` actually set so fan-out is used in
production, ik at `--parallel 1` / flash-attention-off, a long
legal/records-style document for the boundary measurement, filing the
ik_llama.cpp defect upstream. **Done since the previous entry:** the
aggregate replication measurement, the chunk-size sweep at `-c 32768`
(`docs/measurements.md`), the extended sweep at `-c 65536` (numbers on disk,
not yet written up), the engine reversal to mainline (F40), and the
citation-format check against a real job.

---

## Real production events this session — not benchmarks, the actual system doing actual work

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
| Disk | NVMe 477 GB — **367 GB free as of 2026-08-17** (snapshot; superseded by the more recent 248 GB in "Where things stand" above). Re-check: `df -h /` | NVMe 477 GB — **437 GB free as of 2026-08-17** (snapshot; superseded by the more recent 375 GB above). Re-check: `ssh debian1@<node2-ip> 'df -h /'` |
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
| ik_llama.cpp vs mainline, `llama-bench` (single sequence) | prefill +52%, generation −14%, net +22% — **superseded, see next row** |
| ik_llama.cpp vs mainline, through `llama-server` at `--parallel 4` (the actual deployment) | prefill **+43% decaying to +15%** as KV fills, generation **indistinguishable (~5.2–5.3 t/s both)** — but **ik fatal-errors on request 5 of any such job (F40); mainline is the fleet default** |
| **Aggregate throughput, 2 replicas** | **~1.8× (1.86× prefill / 1.77× completion, adjusted; 1.62/1.55 raw)** |
| Chunk size vs wall-clock, real pipeline, mainline | **U-shaped, optimum at 4096 tokens** — 1.85× worse at 1024, 1.29× worse at 6144 |

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
  actually used; and 50, not "41+", commits are unpushed to `origin/main`.

# Operator requirements

Stated by the operator, dated, in their terms. **This file is the record of what
was asked for**, separate from `DESIGN-NOTES.md` (why an approach was chosen) and
`measurements.md` (what the hardware does). If a requirement here conflicts with an
earlier "settled" decision, **this file wins** — it is the person who has to use
the thing.

---

## 2026-08-17 — the interface

Prompted by the operator uploading a PDF as the first real test and watching it
fail in several ways at once (see F34, F36, and the PDF incident in F-series).

### The document workflow they actually want

> *"it would be nice to be able to upload a document, see it appear in a table,
> with a preview image, and to be able to tick on and off which workflows to put
> it through, [the] tick boxes can be in the table itself, and then under the
> table input boxes with titles that indicate which type of workflow they are
> attached to and appear for prompting about document [summarising] and report
> writing and stuff."*

Decomposed:

1. **Upload → appears in a table.** Document-centric, not job-centric. Today the
   table lists *jobs*; they want to see *documents* and choose what happens to them.
2. **A preview image per document** — page 1 as a thumbnail, so a row is
   identifiable at a glance rather than by an opaque job id.
3. **Tick boxes IN the table** selecting which of the three workflows
   (`summarise` / `report` / `qa`) to run per document. One upload, several jobs.
4. **Per-workflow prompt inputs below the table**, each titled with the workflow it
   attaches to, so guidance can differ between summarising and report-writing.

### Batch and stress testing

> *"a web portal for me to submit a few stress test documents would be nice too"*

Multiple files in one submission. The operator had to submit four times, one
document at a time, which is how the PDF bug went unnoticed for as long as it did.

### Navigation and discoverability

> *"do you have a nav or a burger menu with a directory in it or something? do you
> have any other pages that you have made that I can't see by clicking through?"*

**Answer at the time: no nav at all, and yes — orphaned routes existed.**
`/api/jobs`, `/api/jobs/{id}` and `/health` were reachable only by typing a URL,
and `/jobs/{id}/result` only from a small link on the job page. **Requirement:
every route reachable by clicking.** A page nobody can find is a page that does not
exist, and worse, it hides state the operator needs.

### Output

> *"a web page to see the output, at least as raw text printed into a text box"*

Selectable, copyable raw text, in addition to the rendered view.

---

## 2026-08-17 — queue control and resumability

> *"I would like to be able to stop queued items, or rearrange the order, and click
> save and have the current process switch to the new order."*

- Cancel a **pending** job (a user cancellation is not an error and needs its own
  terminal status, distinct from `failed`).
- **Reorder** pending jobs; save applies the new order to subsequent claims.
- Stop a **running** job. **Honest limitation:** the worker holds a blocking HTTP
  request that can take minutes to answer. Cooperative cancellation between chunks
  is achievable; interrupting an in-flight request is not, without more machinery.

> *"that opens up a question about whether or not any of these workflows produce
> intermediate artifacts that can be resumed from at all or if stopping them just
> loses everything."*

**A very good question, and at the time the answer was: stopping loses
everything.** `run_one` persisted per-chunk summaries only *after* the whole
document finished, so a 40-chunk document killed at chunk 39 restarted from zero.
The `chunk_summaries` table (added the same day for provenance) is exactly the
right place to fix it.

> *"I imagine that because these models are stochastic they won't care as long as
> it's the exact same model with the same hyper parameters etc."*

**This reasoning is correct and worth preserving.** Chunk summaries are
*independent* map outputs — chunk N's summary does not depend on chunk M's — so
mixing summaries produced in different runs is sound despite stochastic generation,
**provided the model and its parameters are unchanged.** That proviso is load-
bearing: the model identity must be recorded with the persisted chunks, and a
resume against a *different* model must not silently mix outputs.

---

## 2026-08-17 — the watchdog must be out of band. Reversal, with the operator's reasoning.

Earlier the same day the operator relaxed `CLAUDE.md`'s "separate hardware"
constraint for the agent appliance, allowing a reserved VM or spare capacity on a
node. **After F36 they reversed it:**

> *"ok The Watchdog should definitely be an out of band appliance then. I guess even
> if it's a laptop with eight gigabytes of RAM, that would be better than the whole
> cluster just locking itself out of the inference that it needs to recover itself."*

**That last clause is the whole argument, stated better than the original spec
stated it.** F36 showed llama-server hanging *alive* — accepting TCP, answering
nothing, invisible to `Restart=always`. A recovery agent that needs inference to
decide what to do cannot recover the inference server it depends on. The failure is
self-locking.

**Consequences:**

- **The liveness watchdog belongs off-cluster.** Modest hardware is fine — it runs
  `curl` and `systemctl`, not a model. An 8 GB laptop is ample.
- The **on-node** relaxation still stands for *triage and batching* work, which only
  matters while the cluster is up anyway. The split recorded in `STATUS.md` holds;
  what changed is that liveness is firmly on the off-box side, not optional.
- `cluster/llama-watchdog.sh` currently runs **on** node 1 via a systemd timer. That
  is better than nothing and catches the wedge, but **it shares the host's failure
  modes** — it cannot report that node 1 is down, out of RAM, or off. Move it, or
  duplicate it, off-box.

---

## 2026-08-17 — quantisation ceiling

> *"fp8 quantisation is what I consider max size, anything more than that is for
> show I think."*

Recorded as a standing preference: **Q8 is the ceiling.** Defensible — Q8 is
near-lossless on published KLD comparisons — and it already matches what a previous
session chose (the in-flight Qwen3-Next-80B download is `UD-Q8_K_XL`, 87 GB, which
fits one node at S=1 with ~12 GB spare; anything larger would not).

Paired with an open question the operator raised, now `DESIGN-NOTES.md` H:
**does quantisation *format* change speed on this CPU, beyond just size?** Almost
certainly yes, and unmeasured.

---

## Open requirements not yet met

| Requirement | State |
|---|---|
| Batch upload | in progress |
| Document table with previews + per-row workflow tick boxes | in progress |
| Per-workflow prompt inputs | in progress |
| Navigation / no orphaned routes | in progress |
| Raw-text output page | in progress |
| Cancel + reorder pending jobs | in progress |
| Stop a running job | in progress, with a stated limit |
| Resume from intermediate artifacts | in progress |
| Notification on completion | in progress |
| Watchdog moved off-cluster | **not started — needs hardware** |
| Chunk-size / quant-format measurement | **not started** (`DESIGN-NOTES.md` H) |

---

## 2026-08-17 — a hardening pass is needed for CLAUDE-CODE interaction, not just the product

> *"that slip was messed up[.] include a note somewhere that we need to do a hardening
> pass for Claude code interaction because while you will not be involved during the
> functioning of the end product a Claude skill will be the canonical setup phase for
> non technical people. a bash script would be good for normal people."*

**The slip:** a `git add -A` swept three agent worktrees into a commit as embedded git
repos, and pushed it. Caught and reverted within a minute. But the operator's point is
not about that one mistake — **it is about who runs the setup.**

**The argument, and it is the strongest case yet for determinism in the setup path:**
no assistant is present while the cluster does its job, so agent mistakes there are
survivable. But **the Claude Skill is intended to BE the setup phase for organisations
with no specialist**, and at that moment an agent slip lands on someone who cannot
see it, cannot diagnose it, and has no git history to reason about.

**The evidence is this session's own error log.** Every one of these was mine, in one
evening, on a machine with a competent operator watching:

| Slip | What saved it |
|---|---|
| `git add -A` committed worktree gitlinks and pushed | I noticed the warning text |
| `pkill -f` matched its own command line — **three times**, killing my shell | obvious immediate failure |
| f-string with escaped quotes — invalid Python — **twice** | traceback |
| `substr(document,1,5)='%PDF'` off-by-one; matched 0 rows | I checked `rowcount` |
| Wrong form field names guessed instead of read (`text`, `uploads`, `files`) | HTTP 422/405 |

**Every one was caught only because a human or a loud error surfaced it.** Put the
same sequence in front of a non-technical user during setup and several fail silently.

**Requirement:**

1. **The setup path must be deterministic bash scripts, not agent improvisation.** The
   agent's job is to *choose* the configuration (which model, what `-t`, what `-c`,
   how many nodes) — the measured judgement it is genuinely good at — and then **run
   an audited script**. `provisioning/setup.sh` and `distribute.sh` are already this
   shape. Keep everything in that shape.
2. **A hardening pass over agent-facing operations**, to be scheduled: never `git add
   -A` (enumerate paths); never `pkill -f` with a pattern that can match the caller
   (or the whole class — use pidfiles/systemd units instead); assert row counts on
   destructive SQL; read an API before calling it. Several of these belong as
   conventions in `CLAUDE.md`.
3. **Scripts must be re-runnable and must verify their own effect** — the discipline
   `setup.sh` already follows, and the reason the five N=2 bugs (F30) were survivable.

**Status, 2026-08-18: requirement 2 is done.** `PreToolUse` hooks are implemented in
`.claude/settings.json` + `.claude/hooks/cluster-guard.py`, both now tracked (the
`.gitignore` rule was narrowed from `.claude/` to `.claude/*` plus negations, so the
guard travels with the repo). `git add -A`, `git commit -a`, `pkill -f` and inline
Python that does not compile are blocked outright; cluster service control, `git push`,
mutating SQL against the live job store, writes to `/opt/models`, and git operations in
the live checkout are gated to the operator. **Tested in both directions** — 105 harness
cases plus an end-to-end run under `--dangerously-skip-permissions` — and swept against
1071 lines of this repo's own scripts for false positives. Requirements 1 and 3 are
unchanged and still stand.

**Read `docs/AGENT-HARDENING.md`** for the rule-by-rule evidence, what is deliberately
*not* hooked, and — most important for the Skill — **what a hook fundamentally cannot
catch.** The off-by-one SQL predicate is genuinely uncatchable by any command pattern;
it needs a row-count-asserting helper script that does not exist yet.

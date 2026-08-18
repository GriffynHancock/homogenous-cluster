# Agent hardening — `PreToolUse` hooks

**What this delivers:** requirement 2 of the 2026-08-17 hardening entry in
`docs/REQUIREMENTS.md` ("a hardening pass over agent-facing operations, to be
scheduled"). Implemented 2026-08-18.

**Where it lives:** `.claude/settings.json` (hook registration) and
`.claude/hooks/cluster-guard.py` (all the logic). Both are now **tracked** —
`.gitignore` was changed from `.claude/` to `.claude/*` plus negations, because a
guard that exists in one checkout protects one checkout, and this has to travel
with the repo and later into the Skill. Agent worktrees and `hook-audit.log`
stay ignored.

---

## Why hooks and not a container

`docs/DESIGN-NOTES.md` K (Target 2) settled this: **zero of the five logged slips
would have been stopped by a container.** Two happened inside the bind-mounted
project directory, which Anthropic's own devcontainer documentation says a
container does not protect; two required real access to live state (the jobs DB,
the HTTP API) that the agent must be able to reach anyway; one was a syntax
error. And this project's agents *structurally cannot* be sandboxed away from the
cluster — `CLAUDE.md` makes them the operator, with `sudo`, SSH to every node,
and `/opt/models`. A boundary you have to punch open for everything it was built
to fence off is not a boundary.

A `PreToolUse` hook does not change what the agent can *reach*. It changes what
the agent is allowed to *do* before doing it, deterministically, before the
command touches disk.

---

## Design decisions worth knowing before you edit this

**1. One guard script, no `if:` matchers.** The draft in `DESIGN-NOTES.md` used
per-hook `"if": "Bash(git add -A*)"` filters. Those are documented to do
*best-effort* bash parsing and to **fail open** — run the hook, i.e. allow —
when parsing fails. That is the one behaviour this project cannot accept, since
a guard that silently fails open is worse than no guard: it manufactures
confidence. So `.claude/settings.json` registers a single hook on `matcher:
"Bash"` with no `if:`, and `cluster-guard.py` does its own parsing.

**2. Two decision levels, not one.**

| Level | Meaning | Mechanism |
|---|---|---|
| **DENY** | Never the right command; a safe alternative always exists. | `permissionDecision: "deny"` |
| **GATE** | Legitimate but expensive or irreversible. The *human* decides. | `permissionDecision: "ask"` |

**3. GATEs do not fail open under `bypassPermissions`.** This is the subtlest
part and it is load-bearing. `permissionDecision: "ask"` means "show the
permission prompt as normal" — and under `--dangerously-skip-permissions` or
`dontAsk` there is no prompt to show. An `ask` there would be a no-op: the exact
false-confidence failure. The guard therefore reads `permission_mode` from the
hook input and **downgrades GATE to DENY when the session cannot prompt**,
with the reason spelled out. `deny` is documented to block even in
`bypassPermissions`, so this is the one decision that always holds.

**4. The escape hatch is a speed bump, not a security boundary, and the file
says so.** A GATEd command may be re-run with the prefix
`CLUSTER_OPS_CONFIRMED=1`. An agent *can* set that itself; the point is that
doing so is a deliberate act that appears in the transcript and in
`.claude/hook-audit.log`, exactly like a `--force` flag. The deny text tells the
agent not to set it on its own initiative. In the end-to-end test below the
agent complied and asked instead. **It does not unlock DENY rules** — those have
no escape.

**5. Fails closed.** Unparseable hook input, or any exception in the guard,
exits 2 (block) with the traceback on stderr rather than waving the call
through.

**6. Read-only inspection is untouched, deliberately.** `systemctl status` /
`is-active` / `cat` / `list-units`, `journalctl`, `git status` / `diff` / `log` /
`fetch`, `ssh`, `curl`, `ls` / `du` / `sha256sum` on `/opt/models`, and
`sqlite3 'file:...?mode=ro'` all pass with no decision. Verified case by case
below. An agent that cannot inspect the cluster cannot operate it.

---

## What is hooked, and what each rule traces to

| Rule | Level | Traces to |
|---|---|---|
| `git add -A` / `--all` / `.` / `-Av` / `git -C … add -A` | **DENY** | The 2026-08-17 slip: swept three agent worktrees into a commit as embedded git repos **and pushed**. Reverted by `ddecdfa`. `REQUIREMENTS.md` 2026-08-17. |
| `git commit -a` / `-am` | **DENY** | Same unreviewed-scope mistake by another route. Named in `REQUIREMENTS.md` requirement 2. |
| `pkill -f` / `pkill --full` / `killall -r` | **DENY** | Matched its own command line and killed the agent's shell **three times** in one evening. `CLAUDE.md` and `REQUIREMENTS.md` both say use systemd units or explicit PIDs. |
| `pkill`/`killall` naming a cluster process (`llama-server`, `uvicorn`, …) | GATE | F3 (65 GB reload on restart) and F36 (killing mid-generation is what wedged the server). |
| `systemctl restart\|stop\|kill\|disable\|mask` on `llama-server@*`, `missing-link`, `rpc-server@*`, `llama-watchdog@*` — including over `ssh` and behind `sudo` | GATE | **F36**: a `systemctl restart missing-link` mid-job left llama-server hung *alive* — accepting TCP, answering nothing, invisible to `Restart=always`. A watchdog restart destroyed a 97,299-character job in flight. F3: a restart costs a multi-minute 65 GB reload. |
| Mutating SQL (`DELETE`/`UPDATE`/`INSERT`/`DROP`/`ALTER`/…) against `/opt/missing-link/jobs.sqlite`, incl. inside a heredoc | GATE | The `substr(document,1,5)='%PDF'` off-by-one that matched 0 rows, caught only because someone checked `rowcount`. |
| `sqlite3 <db>` with statements arriving invisibly (bare stdin, `< file.sql`) | GATE | Same. The guard cannot tell a SELECT from a DELETE it cannot see, so it says so instead of guessing. |
| `sqlite3.connect(<db>)` from Python without `mode=ro` | GATE | The read-only convention. |
| `rm`/`mv`/`dd`/`truncate`/`shred` **of** the jobs DB; `Write` tool targeting it | **DENY** | Destroys queued/running jobs, `chunk_summaries` provenance and every persisted result. |
| `git push` | GATE | The gitlink commit was pushed before anyone read it. Nothing here should push without the operator saying so. |
| Writes into `/opt/models` (`cp`/`mv`/`rsync`/`scp`/`curl -o`/`tee`/`>` redirect/`rm`), and `Write`/`Edit` with a path under it | GATE | **F28**: the LAN is 100 Mb/s (measured 93.8 Mbit/s), so a 65 GB model costs **~97 min per node** to re-fetch. |
| `rm`/`dd`/etc. on `/opt/models` **itself** | **DENY** | Would wipe every model on the node. |
| `git merge\|rebase\|reset\|pull\|stash\|clean\|checkout\|switch\|cherry-pick\|revert` **when the target is the live checkout** `/home/debian1/homogenous-cluster` | GATE | The 2026-08-17 merge performed in the checkout that is `missing-link.service`'s `WorkingDirectory=`. Any restart in that window would have crash-looped the unit every 5 s (`Restart=always`, `RestartSec=5`). Worktrees existed and were not used. |
| Inline Python (`python -c`, python heredoc) or a whole-file `Write` of a `.py` that does not `compile()` | **DENY** | The f-string-with-escaped-quotes slip, **twice**. See "what a hook cannot catch" — this one turned out to be catchable after all. |

Site paths (`MISSING_LINK_DB`, `CLUSTER_MODELS_DIR`, `CLUSTER_LIVE_CHECKOUT`) are
read from the environment with these values as defaults, so the file is not
wrong on another node or another fleet.

### Parsing, and the one place it degrades

The guard tokenises with `shlex`, splits on `&&`/`||`/`;`/`|`/subshells, unwraps
`sudo`, `env`, `nice`, `timeout`, `ssh <host>` and `bash -c '<cmd>'`, and tracks
`cd` across a command line so that `cd /opt/llama.cpp && git checkout <tag>` is
not mistaken for a git operation in the live checkout.

**Heredoc bodies are excluded from the command-shape rules** — a heredoc body is
data, not commands. Without this, writing documentation that quotes `git add -A`
would be blocked, which is how a guard gets disabled. Bodies *are* still scanned
for SQL, but only when the db path appears in the command portion, so
`cat > doc.md <<EOF … DELETE …` is untouched while `sqlite3 <db> <<EOF … DELETE …`
is not.

**When a line cannot be tokenised** (unbalanced quotes) the guard falls back to
command-position–anchored regexes on the raw line. Anchoring to `^`, `;`, `&`,
`|`, `sudo`, `then`, `do` means prose mentioning a banned command does not trip
it; `echo don't && git add -A` is still caught, `echo it isn't a git add -A
situation` is not. **This is the documented soft spot**: the fallback is less
precise than the tokeniser, so a sufficiently strange quoting could evade a
rule. It is a strong deterrent against honest mistakes, not a proof.

---

## What deliberately is NOT hooked

- **`systemctl start`, `daemon-reload`, and restarts of unrelated units** (e.g.
  `chrony`). Starting is not destructive; only restart/stop/kill/disable/mask of
  *cluster* units are gated.
- **Anything read-only**, per the design decision above.
- **`rm -rf` in general, `git reset --hard` outside the live checkout, `dd` to
  block devices, `chmod`/`chown` recursion.** Nobody has hit these here, and
  `provisioning/setup.sh` and `bootstrap.sh` legitimately `chown -R /opt/models`.
  Padding the rule list with theoretical risks is how a guard becomes noise.
- **Edits to `.claude/settings.json` itself.** Self-protection is tempting but no
  incident motivates it, and it would block the operator's own maintenance.
- **`ssh` to nodes, `curl` to llama-server, benchmarks, `pytest`.** These are the
  job.
- **Anything inside a shell script.** A hook sees `bash provisioning/setup.sh`,
  not its 1071 lines. That is by design: audited scripts are exactly the
  deterministic path `REQUIREMENTS.md` requirement 1 asks for. It also means
  `missing-link/start-ui.sh:27` still contains a `pkill -f` (using the `[n]`
  bracket trick to avoid self-matching) which the guard would deny if typed
  directly — worth converting to `systemctl stop missing-link`, separately.

---

## What a hook fundamentally cannot catch

Two of the five logged slips are not command patterns at all. Being honest about
that matters more than the rules that did work, because the Skill will inherit
whatever gap is left here.

**1. Invalid Python — actually catchable, and now caught.** The f-string with
escaped quotes was not a *pattern* but it was a *property*: the source does not
compile. `compile()` is the interpreter's own check run one second earlier, so
the guard runs it on `python -c` snippets, python heredocs and whole-file `Write`s
of `.py`, and denies with the real `SyntaxError`. It **skips** any source
containing `$` or backticks, because what the guard sees is not what the
interpreter will receive after shell expansion — a deliberate false-negative to
avoid false positives. It cannot see `Edit`, which supplies a fragment rather
than a file; a `PostToolUse` hook running `py_compile` after an edit would close
that, at the cost of catching the error after the write instead of before it.

**2. The off-by-one SQL predicate — genuinely uncatchable.** No hook can know
that `substr(document,1,5)='%PDF'` should have been `1,4`. The guard can only
force the *human* into the loop, which is what the GATE does, and put the remedy
in the message ("assert the row count before AND after, in the same
transaction"). **The real control is a convention plus a helper**: destructive
SQL against the live store should go through a small script that runs the
`SELECT count(*)` with the identical predicate, prints it, and refuses to
proceed on 0 rows or on a count outside an expected range. That script does not
exist yet and is the obvious follow-up.

**3. The wrong form-field names — uncatchable, and the fix is cheaper than a
hook.** Guessing `text`/`uploads`/`files` instead of reading the API is a
knowledge failure, not a dangerous command. Missing Link is FastAPI, so
`GET /openapi.json` is authoritative and free. That is a convention, not a
control.

**4. The general limit.** A hook sees one tool call at a time. It cannot see
*intent*, cannot see inside scripts, and cannot see the sequence of correct
individual commands that together do the wrong thing. It stops the specific
motions that went wrong before. It does not make an agent safe.

---

## Verification

`PreToolUse` hooks were driven with the real stdin contract via
`scratchpad/hooktest.py` (**105 cases, all passing**), each rule tested in both
directions — the blocked command *and* the legitimate near-miss. A separate
false-positive sweep ran **1071 lines of this repo's own shell scripts and 9
Missing Link `.py` files** through the guard; it found two real false positives
(`chown -R /opt/models`, and `cd elsewhere && git checkout`), both fixed, after
which the only non-ALLOW results are the two commands that *should* be flagged
when typed directly.

**Both directions, end to end, in a real Claude Code session** launched with
`--dangerously-skip-permissions` (the hardest case: the mode where a naive
implementation fails open). The agent's verbatim report:

> **(1) `git add -A` — blocked, did not run.** A repo hook intercepted it.
> ```
> BLOCKED by .claude/hooks/cluster-guard.py [git-add-all]
> `git add -A` stages everything under the current directory. On 2026-08-17 this swept three agent worktrees into a commit as embedded git repos and pushed them (fixed by ddecdfa).
> Do this instead: enumerate the paths: `git add docs/FILE.md provisioning/x.sh`. Use `git status --porcelain` first to see what there is.
> ```
> Nothing was staged.
>
> **(2) `systemctl status llama-server@8080 --no-pager` — ran, exit 0.**
> `active (running)` … `-c 32768 --parallel 4` … `n_idle_slots=4`

and the harness confirmed it at the framework level:

```
[DEBUG] Hook PreToolUse (Checking command against cluster safety rules) returned permissionDecision: deny
[DEBUG] Hook result has permissionBehavior=deny
[DEBUG] Hook denied tool use for Bash
```

A GATE under `bypassPermissions` was tested with a command that is harmless if
the guard fails (`rm` of a path that does not exist), rather than by performing
a restart:

> ```
> BLOCKED by .claude/hooks/cluster-guard.py [models-write]
> `rm` removes data under /opt/models. A 65 GB model takes ~97 min to re-copy over the 100 Mb LAN (F28 …).
> This is a GATE, not a ban: it needs the operator's agreement. This session runs in 'bypassPermissions' mode, where no permission prompt can be shown, so the gate blocks instead of asking. Ask the operator; if they agree, re-run the command with the prefix CLUSTER_OPS_CONFIRMED=1 …
> ```
> As instructed, I have not retried or substituted. … the hook's own text says
> that marker is only for me to set on your explicit say-so.

No cluster service was restarted, stopped or killed at any point in this work.

### Re-running the checks

The harness lives in the scratchpad rather than the repo, since there is no test
runner for repo tooling. To re-verify after editing a rule, feed the guard the
documented `PreToolUse` JSON on stdin:

```sh
echo '{"tool_name":"Bash","cwd":"'"$PWD"'","permission_mode":"default",
       "tool_input":{"command":"git add -A"}}' | .claude/hooks/cluster-guard.py
```

Non-`allow` decisions append to `.claude/hook-audit.log` (gitignored), which is
the audit trail for anything that used the confirm prefix.

---

## For whoever builds the Skill

The hooks protect *this* repo. The Skill will run on a machine with no repo, no
git history and no operator watching, which is the whole reason
`REQUIREMENTS.md` raised this. Three things carry over:

1. **The generated setup must be audited bash scripts, not agent
   improvisation** (`REQUIREMENTS.md` requirement 1). The guard reinforces this
   rather than replacing it: hooks cannot see inside a script, so a script is
   the unit that gets reviewed once and then run deterministically.
2. **Ship the guard with the generated project.** `cluster-guard.py` reads its
   paths from the environment for exactly this reason. The rules that generalise
   are `git add -A`, `pkill -f`, service control, live-DB writes, and writes to
   the model directory; the site paths do not.
3. **The GATE-under-`bypassPermissions` downgrade is the part most likely to be
   got wrong by someone re-implementing this.** A non-technical user's session
   is precisely the one least likely to be able to answer a prompt, and an
   `ask` that nobody can answer is an allow.

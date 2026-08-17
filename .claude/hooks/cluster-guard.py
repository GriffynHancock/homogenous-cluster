#!/usr/bin/env python3
"""PreToolUse guard for the homogenous-cluster repo.

Every rule in this file traces to something that actually went wrong on this
project, or to a standing constraint in CLAUDE.md. See docs/AGENT-HARDENING.md
for the evidence table. Do not add speculative rules: a guard that blocks
legitimate work gets disabled, and a disabled guard protects nothing.

Contract (code.claude.com/docs/en/hooks):
  - stdin  : JSON with tool_name, tool_input, cwd, permission_mode, ...
  - stdout : {"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "deny"|"ask"|"allow",
              "permissionDecisionReason": "..."}}
  - exit 0 with no output = "no decision", normal permission flow continues.
  - A hook returning "deny" blocks the tool even under bypassPermissions.

Two decision levels:
  DENY  - never the right command; a safe alternative always exists.
  GATE  - legitimate but expensive or irreversible; the human decides.
          Emitted as "ask" when the session can prompt. Under
          bypassPermissions / dontAsk there is no prompt to show, so a GATE
          would silently fail open -- it is downgraded to "deny" with the
          escape hatch spelled out instead.

Escape hatch for GATEs only: prefix the command with CLUSTER_OPS_CONFIRMED=1.
This is a speed bump and an audit trail, not a security boundary -- an agent
can set it. Its purpose is to make the act deliberate and greppable in the
transcript. Agents: only use it after the operator has said yes.

Fails CLOSED: any internal error or unreadable input blocks the call rather
than waving it through, because a guard that fails open is worse than no guard
(it creates false confidence).
"""

import json
import os
import re
import shlex
import sys
import traceback
from datetime import datetime

# --- site facts ------------------------------------------------------------
# Overridable so the file is not wrong on another node/fleet.
DB_PATH = os.environ.get("MISSING_LINK_DB", "/opt/missing-link/jobs.sqlite")
MODELS_DIR = os.environ.get("CLUSTER_MODELS_DIR", "/opt/models")
# The checkout systemd runs Missing Link from (WorkingDirectory= in
# missing-link.service). Git operations that rewrite the working tree here can
# crash-loop the live service; agent worktrees under .claude/worktrees are safe.
LIVE_CHECKOUT = os.environ.get("CLUSTER_LIVE_CHECKOUT", "/home/debian1/homogenous-cluster")

PROTECTED_UNIT = re.compile(
    r"^(llama-server|rpc-server|ggml-rpc-server|missing-link|llama-watchdog)"
    r"(@[^.]*)?(\.service|\.timer|\.socket)?$"
)
CLUSTER_PROCS = {
    "llama-server", "rpc-server", "ggml-rpc-server", "uvicorn",
    "missing-link", "llama-cli", "llama-bench",
}

SERVICE_MUTATING_VERBS = {
    "restart", "stop", "kill", "disable", "mask", "reload",
    "reload-or-restart", "try-restart", "try-reload-or-restart",
}
# Explicitly enumerated so read-only inspection is provably untouched.
SERVICE_READ_VERBS = {
    "status", "is-active", "is-enabled", "is-failed", "show", "cat",
    "list-units", "list-unit-files", "list-timers", "list-jobs",
    "show-environment", "get-default",
}

GIT_WORKTREE_MUTATING = {
    "merge", "rebase", "reset", "pull", "stash", "clean",
    "cherry-pick", "revert", "checkout", "switch", "am", "apply",
}

DESTRUCTIVE_FILE_CMDS = {"rm", "rmdir", "shred", "dd", "truncate", "mkfs", "unlink"}
# Commands that put BYTES into a path. Deliberately excludes chmod/chown/touch:
# provisioning/setup.sh and bootstrap.sh legitimately chown -R /opt/models, and
# metadata changes do not cost the 97-minute re-copy that this rule is about.
WRITING_FILE_CMDS = {
    "cp", "mv", "rsync", "scp", "wget", "curl", "tee", "install",
    "tar", "unzip", "gunzip", "split", "ln", "sed",  # sed -i
}

MUTATING_SQL = re.compile(
    r"\b(insert\s+into|insert\s+or|update\s+\w+|delete\s+from|drop\s+(table|index|view)"
    r"|alter\s+table|replace\s+into|create\s+(table|index|trigger)|vacuum|reindex"
    r"|pragma\s+journal_mode\s*=|attach\s+database)\b",
    re.IGNORECASE,
)

CONFIRM_TOKEN = re.compile(r"\bCLUSTER_OPS_CONFIRMED=1\b")

NON_PROMPTABLE_MODES = {"bypassPermissions", "dontAsk"}

# Command-position anchors for the fallback scanner. Only used on lines that
# fail to tokenise (unbalanced quotes). Anchored so that prose mentioning a
# banned command -- e.g. this very docstring -- does not trip it.
CMD_POS = r"(?:^|[;&|(]|\bsudo\s+|\bthen\s+|\bdo\s+|\belse\s+)\s*"
FALLBACK_RULES = [
    (re.compile(CMD_POS + r"git\s+(?:-\S+\s+|--\S+\s+)*add\s+(?:[^;&|]*\s)?(-A\b|--all\b|\.(?:\s|$))"),
     "DENY", "git add -A / git add ."),
    (re.compile(CMD_POS + r"git\s+(?:-\S+\s+|--\S+\s+)*commit\s+[^;&|]*(-a\b|--all\b|-[a-zA-Z]*a[a-zA-Z]*\b)"),
     "DENY", "git commit -a"),
    (re.compile(CMD_POS + r"pkill\s+[^;&|]*-\w*f"), "DENY", "pkill -f"),
    (re.compile(CMD_POS + r"git\s+(?:-\S+\s+|--\S+\s+)*push\b"), "GATE", "git push"),
    (re.compile(CMD_POS + r"(sudo\s+)?systemctl\s+[^;&|]*\b(restart|stop|kill|disable|mask)\b[^;&|]*"
                r"\b(llama-server|rpc-server|missing-link|llama-watchdog)"),
     "GATE", "systemctl restart/stop of a cluster unit"),
]


# --- output ----------------------------------------------------------------

def emit(decision, reason, ctx=None):
    """Print a PreToolUse decision and exit."""
    audit(decision, reason, ctx)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def audit(decision, reason, ctx):
    if decision == "allow":
        return
    try:
        base = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(base, ".claude", "hook-audit.log")
        with open(path, "a") as fh:
            fh.write("%s\t%s\t%s\t%s\n" % (
                datetime.now().isoformat(timespec="seconds"),
                decision, reason.splitlines()[0], (ctx or "")[:400]))
    except Exception:
        pass  # never let logging break the decision


def deny(rule, why, instead, ctx=None):
    emit("deny", "BLOCKED by .claude/hooks/cluster-guard.py [%s]\n%s\nDo this instead: %s"
         % (rule, why, instead), ctx)


def gate(mode, rule, why, ctx=None):
    if CONFIRM_TOKEN.search(ctx or ""):
        return  # operator-confirmed; fall through to the normal permission flow
    if mode in NON_PROMPTABLE_MODES:
        emit("deny",
             "BLOCKED by .claude/hooks/cluster-guard.py [%s]\n%s\n"
             "This is a GATE, not a ban: it needs the operator's agreement. This session runs in "
             "'%s' mode, where no permission prompt can be shown, so the gate blocks instead of "
             "asking. Ask the operator; if they agree, re-run the command with the prefix "
             "CLUSTER_OPS_CONFIRMED=1 (that prefix is an audit marker -- do not set it on your own "
             "initiative)." % (rule, why, mode), ctx)
    emit("ask",
         "GATED by .claude/hooks/cluster-guard.py [%s]\n%s\nApprove only if you meant to do this."
         % (rule, why), ctx)


# --- parsing ---------------------------------------------------------------

HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
OPERATORS = {"&&", "||", ";", "|", "&", "|&", "(", ")", ";;", "&&&"}
REDIR_TOKENS = {">", ">>", "<", ">|", "<>", "&>", "&>>", ">&"}


def split_heredocs(command):
    """Return (list of (command_line, attached_body), had_heredoc).

    Heredoc BODIES are data, not commands, so they are excluded from the
    command-shape rules -- otherwise writing documentation that quotes
    `git add -A` would be blocked. They are kept attached to their command line
    so the SQL rules can still see `sqlite3 db <<EOF ... DELETE ... EOF`.
    """
    lines = command.split("\n")
    out = []
    i = 0
    had = False
    while i < len(lines):
        line = lines[i]
        m = HEREDOC_START.search(line)
        if not m:
            out.append((line, ""))
            i += 1
            continue
        had = True
        term = m.group(2)
        body = []
        i += 1
        while i < len(lines) and lines[i].strip() != term:
            body.append(lines[i])
            i += 1
        i += 1  # skip terminator
        out.append((line, "\n".join(body)))
    return out, had


def tokenize(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def segments(tokens):
    """Split a token list into (argv, redirect_targets) command segments."""
    segs, argv, redirs = [], [], []
    expect_redir = False
    for tok in tokens:
        if tok in OPERATORS:
            if argv or redirs:
                segs.append((argv, redirs))
            argv, redirs, expect_redir = [], [], False
            continue
        if tok in REDIR_TOKENS or (tok and set(tok) <= set("<>&")):
            expect_redir = True
            continue
        if expect_redir:
            redirs.append(tok)
            expect_redir = False
            continue
        argv.append(tok)
    if argv or redirs:
        segs.append((argv, redirs))
    return segs


ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def unwrap(argv):
    """Strip env assignments and wrappers so argv[0] is the real program.

    Returns a list of argv lists: ssh/bash -c produce a nested command that is
    checked with the same rules as a local one.
    """
    argv = list(argv)
    while argv and ASSIGN.match(argv[0]):
        argv.pop(0)
    if not argv:
        return []

    head = os.path.basename(argv[0])

    if head == "sudo":
        rest = argv[1:]
        while rest and (rest[0].startswith("-") or ASSIGN.match(rest[0])):
            if rest[0] in ("-u", "-g", "-p", "-C", "--user", "--group"):
                rest = rest[2:]
            else:
                rest = rest[1:]
        return unwrap(rest)

    if head in ("env", "nohup", "nice", "ionice", "stdbuf", "time", "setsid", "doas"):
        rest = argv[1:]
        while rest and (rest[0].startswith("-") or ASSIGN.match(rest[0])):
            rest = rest[1:]
        return unwrap(rest)

    if head == "timeout":
        rest = argv[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return unwrap(rest[1:])  # drop the duration

    if head == "ssh":
        rest = argv[1:]
        while rest and rest[0].startswith("-"):
            if rest[0] in ("-i", "-p", "-o", "-l", "-F", "-J", "-b", "-c", "-E"):
                rest = rest[2:]
            else:
                rest = rest[1:]
        return unwrap(rest[1:]) if len(rest) > 1 else []  # drop the host

    if head in ("bash", "sh", "zsh", "dash") and "-c" in argv:
        idx = argv.index("-c")
        if idx + 1 < len(argv):
            try:
                return [a for seg in segments(tokenize(argv[idx + 1])) for a in unwrap_all(seg[0])]
            except ValueError:
                return []
        return []

    return [argv]


def unwrap_all(argv):
    r = unwrap(argv)
    if r and isinstance(r[0], str):
        return [r]
    return r


def git_parts(argv):
    """Return (subcommand, args, target_dir_override) for a git argv."""
    i, target = 1, None
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in ("-C", "-c"):
            if argv[i] == "-C" and i + 1 < len(argv):
                target = argv[i + 1]
            i += 2
        elif argv[i].startswith("--git-dir=") or argv[i].startswith("--work-tree="):
            target = argv[i].split("=", 1)[1]
            i += 1
        else:
            i += 1
    if i >= len(argv):
        return None, [], target
    return argv[i], argv[i + 1:], target


def in_live_checkout(path):
    try:
        p = os.path.realpath(path)
        live = os.path.realpath(LIVE_CHECKOUT)
    except Exception:
        return False
    if p != live and not p.startswith(live + os.sep):
        return False
    # Agent worktrees live under <live>/.claude/worktrees/... and are safe.
    return ".claude" + os.sep + "worktrees" not in p[len(live):]


def touches(paths, prefix):
    return any(p == prefix or p.startswith(prefix.rstrip("/") + "/") for p in paths)


def mentions_db(strings):
    return any(DB_PATH in s or "MISSING_LINK_DB" in s for s in strings)


# --- rules -----------------------------------------------------------------

def check_python_syntax(code, where, ctx):
    """Compile inline Python before the shell runs it.

    Two of the five slips on 2026-08-17 were f-strings with escaped quotes --
    invalid Python, caught only by the traceback after the fact. compile() is
    the same check the interpreter would do, run a second earlier.

    Skipped when the source contains shell expansions ($VAR, backticks), because
    what the guard sees is not what the interpreter will receive.
    """
    if not code.strip() or "$" in code or "`" in code:
        return
    try:
        compile(code, where, "exec")
    except SyntaxError as e:
        deny("python-syntax",
             "This %s does not compile: %s (line %s, offset %s). It would have failed at "
             "runtime with a traceback. Two f-strings with escaped quotes were shipped this "
             "way on 2026-08-17." % (where, e.msg, e.lineno, e.offset),
             "fix the syntax. For anything longer than one line, write a .py file and run it "
             "rather than passing it inline -- quoting through the shell is where this breaks.",
             ctx)
    except ValueError:
        pass  # e.g. source with null bytes; not our business


def check_segment(argv, redirs, text, body, mode, cwd):
    """Apply the rules to one command segment. May not return (emits+exits)."""
    if not argv:
        # bare redirection, e.g. `> /opt/models/x`
        if touches(redirs or [], MODELS_DIR):
            gate(mode, "models-write",
                 "Redirects output into %s. Models are 65 GB files that cost ~97 min each to "
                 "re-fetch over the 100 Mb LAN (F28)." % MODELS_DIR, text)
        return
    prog = os.path.basename(argv[0])
    args = argv[1:]
    paths = argv[1:] + redirs

    # -- inline Python that does not compile ---------------------------------
    if prog.startswith("python"):
        if "-c" in args and args.index("-c") + 1 < len(args):
            check_python_syntax(args[args.index("-c") + 1], "python -c snippet", text)
        elif body:
            check_python_syntax(body, "inline python heredoc", text)

    # -- git -----------------------------------------------------------------
    if prog == "git":
        sub, gargs, target = git_parts(argv)
        if sub == "add":
            for a in gargs:
                bad = (a in ("-A", "--all", "--no-ignore-removal", ".", "..", ":/")
                       or a.startswith(":/")
                       or (a.startswith("-") and not a.startswith("--") and "A" in a))
                if bad:
                    deny("git-add-all",
                         "`git add %s` stages everything under the current directory. On "
                         "2026-08-17 this swept three agent worktrees into a commit as embedded "
                         "git repos and pushed them (fixed by ddecdfa)." % a,
                         "enumerate the paths: `git add docs/FILE.md provisioning/x.sh`. "
                         "Use `git status --porcelain` first to see what there is.", text)
        elif sub == "commit":
            for a in gargs:
                if (a in ("-a", "--all")
                        or (a.startswith("-") and not a.startswith("--") and "a" in a)):
                    deny("git-commit-all",
                         "`git commit %s` auto-stages every tracked modification, which is the "
                         "same unreviewed-scope mistake as `git add -A`." % a,
                         "stage the paths you mean with `git add <path>...`, check "
                         "`git diff --cached --stat`, then plain `git commit`.", text)
        elif sub == "push":
            gate(mode, "git-push",
                 "Nothing in this project pushes without the operator saying so. The 2026-08-17 "
                 "worktree-gitlink commit was pushed before anyone read it.", text)
        elif sub in GIT_WORKTREE_MUTATING:
            where = target or cwd or ""
            if in_live_checkout(where):
                gate(mode, "git-in-live-checkout",
                     "`git %s` rewrites the working tree at %s, which is the WorkingDirectory of "
                     "missing-link.service. A merge was run here on 2026-08-17; any restart during "
                     "that window would have crash-looped the service every 5 s (Restart=always, "
                     "RestartSec=5). Agent worktrees under .claude/worktrees exist for this."
                     % (sub, where), text)

    # -- process killing -----------------------------------------------------
    if prog == "pkill":
        for a in args:
            if a.startswith("-") and not a.startswith("--") and "f" in a:
                deny("pkill-f",
                     "`pkill -f` matches against full command lines, including the shell that is "
                     "running it. On 2026-08-17 it killed the agent's own shell three times.",
                     "use `systemctl stop <unit>` for a service, or resolve a specific PID first "
                     "(`pgrep -f <pat>`, read it, then `kill <pid>`).", text)
            if a == "--full":
                deny("pkill-f", "`pkill --full` is `pkill -f`; it matches the caller's own "
                                "command line.",
                     "use `systemctl stop <unit>`, or `pgrep -f` then `kill <pid>`.", text)
        for a in args:
            if not a.startswith("-") and a in CLUSTER_PROCS:
                gate(mode, "kill-cluster-proc",
                     "`pkill %s` kills a cluster process outside systemd's knowledge. "
                     "llama-server reloads 65 GB on restart (F3), and killing it mid-generation "
                     "is what wedged it in F36." % a, text)
    if prog == "killall":
        if any(a.startswith("-") and "r" in a and not a.startswith("--") for a in args):
            deny("killall-regex",
                 "`killall -r` kills by regex and has the same self-matching failure mode as "
                 "`pkill -f`.",
                 "name the exact process, or stop the systemd unit.", text)
        for a in args:
            if not a.startswith("-") and a in CLUSTER_PROCS:
                gate(mode, "kill-cluster-proc",
                     "`killall %s` kills a cluster process outside systemd's knowledge (F3, F36)."
                     % a, text)

    # -- service control -----------------------------------------------------
    if prog in ("systemctl", "service"):
        if prog == "systemctl":
            verb, units = None, []
            for a in args:
                if a.startswith("-"):
                    continue
                if verb is None:
                    verb = a
                else:
                    units.append(a)
        else:  # service <name> <verb>
            plain = [a for a in args if not a.startswith("-")]
            units = plain[:1]
            verb = plain[1] if len(plain) > 1 else None
        if verb in SERVICE_MUTATING_VERBS:
            hit = [u for u in units if PROTECTED_UNIT.match(u)]
            if hit:
                gate(mode, "cluster-service-control",
                     "`%s %s %s` acts on the live cluster. F36: a `systemctl restart missing-link` "
                     "mid-job left llama-server hung ALIVE (accepting TCP, answering nothing, "
                     "invisible to Restart=always), and a watchdog restart destroyed a "
                     "97,299-character job in flight. A llama-server restart also costs a "
                     "multi-minute 65 GB reload (F3). Check `systemctl status` / `journalctl` / "
                     "`curl /health` first -- those are not gated."
                     % (prog, verb, " ".join(hit)), text)

    # -- the live jobs database ---------------------------------------------
    if mentions_db(paths):
        if prog in DESTRUCTIVE_FILE_CMDS or (prog == "mv" and len(args) >= 2):
            deny("jobs-db-clobber",
                 "`%s` against %s destroys the live job store -- queued, running and completed "
                 "jobs, chunk_summaries provenance and all persisted results." % (prog, DB_PATH),
                 "if you need a copy, `sqlite3 %s \".backup /tmp/jobs-backup.sqlite\"`. "
                 "If the job really is a migration, say so and the operator will unblock it."
                 % DB_PATH, text)
        if MUTATING_SQL.search(text):
            gate(mode, "jobs-db-write",
                 "Mutating SQL against the live job store %s. The convention is read-only "
                 "(`?mode=ro`) unless the task IS a migration. On 2026-08-17 a "
                 "`substr(document,1,5)='%%PDF'` predicate was off by one and matched 0 rows -- "
                 "caught only because someone checked rowcount. Assert the row count before AND "
                 "after, in the same transaction." % DB_PATH, text)
        if prog == "sqlite3":
            sql_args = [a for a in args if not a.startswith("-") and DB_PATH not in a
                        and "MISSING_LINK_DB" not in a]
            # A heredoc body IS visible to the guard (MUTATING_SQL scanned `text`
            # above), so only gate when the statements are genuinely invisible:
            # bare stdin, or `< file.sql`.
            if not sql_args and "<<" not in text.split("\n")[0]:
                gate(mode, "jobs-db-write",
                     "`sqlite3 %s` with no SQL argument reads statements from stdin, so the guard "
                     "cannot tell a SELECT from a DELETE. Pass the SQL as an argument, or open it "
                     "read-only: `sqlite3 'file:%s?mode=ro' \"SELECT ...\"`." % (DB_PATH, DB_PATH),
                     text)

    # -- /opt/models ---------------------------------------------------------
    model_paths = [p for p in paths if p == MODELS_DIR or p.startswith(MODELS_DIR.rstrip("/") + "/")]
    if model_paths:
        if prog in DESTRUCTIVE_FILE_CMDS:
            if any(os.path.normpath(p) == os.path.normpath(MODELS_DIR) for p in model_paths):
                deny("models-wipe",
                     "`%s` on %s itself would destroy every model on this node." % (prog, MODELS_DIR),
                     "name the single file you mean, and expect to be asked to confirm it.", text)
            gate(mode, "models-write",
                 "`%s` removes data under %s. A 65 GB model takes ~97 min to re-copy over the "
                 "100 Mb LAN (F28 -- the switch is the cap, not the NICs)." % (prog, MODELS_DIR),
                 text)
        if prog in WRITING_FILE_CMDS or touches(redirs, MODELS_DIR):
            gate(mode, "models-write",
                 "Writes into %s. Model files are 65 GB and cost ~97 min per node to re-fetch "
                 "over the 100 Mb LAN (F28). Reads -- ls, du, sha256sum, and passing a path to "
                 "`-m` -- are not gated." % MODELS_DIR, text)


def check_bash(data):
    cmd = (data.get("tool_input") or {}).get("command") or ""
    mode = data.get("permission_mode") or "default"
    cwd = data.get("cwd") or ""

    # python/sqlite3 connections to the live DB without ?mode=ro
    for m in re.finditer(r"(?:sqlite3|aiosqlite)\.connect\(([^)]*)\)", cmd):
        inner = m.group(1)
        if (DB_PATH in inner or "MISSING_LINK_DB" in inner) and "mode=ro" not in inner:
            gate(mode, "jobs-db-write",
                 "Opens the live job store %s read-write from Python. The convention is "
                 "`sqlite3.connect('file:%s?mode=ro', uri=True)` unless the task IS a migration."
                 % (DB_PATH, DB_PATH), cmd)

    pieces, _ = split_heredocs(cmd)
    for line, body in pieces:
        if not line.strip():
            continue
        text = line + ("\n" + body if body else "")
        try:
            toks = tokenize(line)
        except ValueError:
            # Unbalanced quotes: fall back to command-position regexes on the raw
            # line. Less precise, but the dangerous forms are still caught.
            for rx, level, what in FALLBACK_RULES:
                if rx.search(line):
                    if level == "DENY":
                        deny("fallback:" + what,
                             "%s -- matched by the fallback scanner because this line could not be "
                             "tokenised (unbalanced quotes)." % what,
                             "see docs/AGENT-HARDENING.md for the safe form of this command.", text)
                    gate(mode, "fallback:" + what,
                         "%s -- matched by the fallback scanner (line could not be tokenised)."
                         % what, text)
            continue
        for argv, redirs in segments(toks):
            for real in unwrap_all(argv):
                # Track `cd` across the command so that
                # `cd /opt/llama.cpp && git checkout <tag>` is not mistaken for a
                # git operation in the live checkout. Without this, every
                # provisioning script line that cds elsewhere first is a false
                # positive.
                if real and os.path.basename(real[0]) in ("cd", "pushd") and len(real) > 1:
                    tgt = real[1]
                    if tgt not in ("-", "~") and not tgt.startswith("-"):
                        cwd = os.path.normpath(
                            tgt if os.path.isabs(tgt) else os.path.join(cwd or "", tgt))
                    continue
                check_segment(real, redirs, text, body, mode, cwd)


def check_file_tool(data):
    ti = data.get("tool_input") or {}
    mode = data.get("permission_mode") or "default"
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return
    rp = os.path.realpath(path)
    if rp == os.path.realpath(DB_PATH):
        deny("jobs-db-clobber",
             "Writing the live SQLite job store %s as a text file would corrupt it." % DB_PATH,
             "use sqlite3 with an explicit statement, read-only unless the task is a migration.",
             path)
    if rp == os.path.realpath(MODELS_DIR) or rp.startswith(os.path.realpath(MODELS_DIR) + os.sep):
        gate(mode, "models-write",
             "Writes into %s, which holds 65 GB model files that cost ~97 min each to re-fetch "
             "over the 100 Mb LAN (F28)." % MODELS_DIR, path)
    # Whole-file Write of Python: compile it before it lands. (Edit gives only a
    # fragment, so the same check is not possible there.)
    if data_tool_is_write(data) and path.endswith(".py"):
        check_python_syntax(ti.get("content") or "", "file %s" % os.path.basename(path), path)


def data_tool_is_write(data):
    return (data.get("tool_name") == "Write")


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        emit("deny", "cluster-guard.py could not parse its hook input as JSON, so it cannot "
                     "vet this call. Failing closed. Fix or remove the hook in "
                     ".claude/settings.json.")
    tool = data.get("tool_name") or ""
    if tool == "Bash":
        check_bash(data)
    elif tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        check_file_tool(data)
    sys.exit(0)  # no decision: normal permission flow


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.stderr.write("cluster-guard.py crashed; failing closed.\n" + traceback.format_exc())
        sys.exit(2)  # exit 2 blocks the call

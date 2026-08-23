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

# Names a Python program uses to say WHICH database it means on the command
# line. If one of these is present and does not resolve to the live store, the
# program is pointed somewhere else and the live-store rules do not apply.
DB_CLI_FLAGS = ("--db", "--database", "--db-path", "--jobs-db")

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


def same_file(a, b):
    """True if two path strings name the same file, before or after creation."""
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return False


def resolve(token, cwd):
    """A path token as an absolute path, relative to the tracked cwd."""
    if os.path.isabs(token):
        return os.path.normpath(token)
    return os.path.normpath(os.path.join(cwd or "", token))


def looks_like_path(tok):
    # Cheap filter so a SQL statement or a flag is not fed to realpath().
    return bool(tok) and len(tok) < 400 and not tok.startswith("-") \
        and not re.search(r"\s", tok)


def mentions_db(strings, cwd=None):
    """True if any token names the live job store.

    The literal-substring test came first and is kept, because it also catches
    `$MISSING_LINK_DB` and `file:<path>?mode=ro` forms. The realpath test was
    added after F52: `cd /opt/missing-link && sqlite3 jobs.sqlite "DELETE ..."`
    is the same operation spelled relatively, and the substring test allowed it.
    """
    for s in strings:
        if DB_PATH in s or "MISSING_LINK_DB" in s:
            return True
        if cwd is not None and looks_like_path(s) and same_file(resolve(s, cwd), DB_PATH):
            return True
    return False


# --- mutations reached through an interpreter (F52) -------------------------
# A command-pattern rule cannot see `python -m missing_link.reprofile_corpus
# --apply`, which issued 17 UPDATEs against the live job store while the guard
# said nothing. Adding a regex for that one module would leave the general hole
# open, so the rule below asks a PROPERTY of the program instead of matching a
# spelling: resolve the module or script to a file on disk, and decide from its
# source whether it can write the live store.
#
# Deliberate limits, so this stays a low-noise rule rather than a nuisance:
#   * Only project-local code. A module that does not resolve inside the
#     checkout (pytest, pip, uvicorn, anything in site-packages) is left alone.
#   * ENTRY FILE ONLY -- no transitive import walk. Intent lives in the program
#     you invoked; a wrapper that hides its writes in a helper is the residual
#     gap and is documented as such in docs/AGENT-HARDENING.md.
#   * Silence when undecidable. Unreadable, unparseable or unresolvable means NO
#     DECISION, not a block -- the same trade the python-syntax rule makes when
#     it sees a `$`. A rule that fires on uncertainty gets the guard disabled.

def _project_roots(cwd):
    """Directories a `python -m <mod>` could resolve against, best first."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    roots, seen = [], set()
    for base in (cwd, os.environ.get("CLAUDE_PROJECT_DIR"), here, LIVE_CHECKOUT):
        if not base:
            continue
        for cand in (base, os.path.join(base, "missing-link"),
                     os.path.join(base, "bench"), os.path.dirname(base)):
            cand = os.path.normpath(cand)
            if cand not in seen and os.path.isdir(cand):
                seen.add(cand)
                roots.append(cand)
    return roots


def resolve_python_target(args, cwd):
    """(source_path, label) for the program `python ...` is about to run.

    Handles `-m package.module` and a positional `script.py`. Returns
    (None, None) for `-c`, stdin, and anything not found in the checkout.
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-m" and i + 1 < len(args):
            mod = args[i + 1]
            rel = mod.replace(".", os.sep)
            for root in _project_roots(cwd):
                for cand in (os.path.join(root, rel + ".py"),
                             os.path.join(root, rel, "__init__.py")):
                    if os.path.isfile(cand):
                        return cand, "-m " + mod
            return None, None
        if a in ("-c", "-"):
            return None, None
        if a.startswith("-"):
            i += 1
            continue
        if a.endswith(".py"):
            p = resolve(a, cwd)
            return (p, a) if os.path.isfile(p) else (None, None)
        return None, None
    return None, None


def db_writer_names(cwd):
    """Function names in missing_link/db.py whose bodies mutate the database.

    Derived by parsing db.py rather than hard-coded, so the rule tracks the db
    layer instead of drifting away from it the first time someone adds a
    writer. Returns an empty set if db.py cannot be found or parsed, which
    simply means this signal contributes nothing.
    """
    src = path = None
    for root in _project_roots(cwd):
        cand = os.path.join(root, "missing_link", "db.py")
        if os.path.isfile(cand):
            path = cand
            break
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        import ast
        tree = ast.parse(src)
    except Exception:
        return set()
    # Module-level constants holding DDL/DML (TABLE_SCHEMA, CHUNK_SCHEMA, ...).
    ddl_consts = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and MUTATING_SQL.search(node.value.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    ddl_consts.add(t.id)
    names = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        try:
            import textwrap
            # Dedent so first_mutating_sql() can parse the segment on its own
            # and scan literals rather than falling back to raw text.
            seg = textwrap.dedent(ast.get_source_segment(src, node) or "")
        except Exception:
            seg = ""
        if first_mutating_sql(seg) or "executescript(" in seg \
                or any(c in seg for c in ddl_consts):
            names.add(node.name)
    return names


SQL_COMPANION = re.compile(r"\b(set|from|into|values|table|column|where)\b", re.IGNORECASE)


def _sql_literals(src):
    """Non-docstring string constants in `src`, best SQL candidate first.

    SQL lives in string literals, so scan those rather than the raw source.
    Two false leads this avoids, both found while testing this rule against
    reprofile_corpus.py:
      * the raw source matched the COMMENT "one UPDATE per row";
      * the literals in source order matched the help text "and update
        docs/chunk-boundary-measurement.md".
    Both are true statements about the file and neither is the reason it is
    being gated, and a guard whose stated evidence is wrong is a guard nobody
    believes the third time. Literals that also carry a SQL companion keyword
    are therefore ranked first. Returns [] if the source will not parse.
    """
    try:
        import ast
        tree = ast.parse(src)
    except Exception:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            out.append(node.value)
    return out


def first_mutating_sql(src):
    """The first mutating statement in `src`, as a quotable string, or None.

    A literal only counts when it reads as a statement rather than as prose
    that happens to contain a verb: the match must start the literal, or be
    written in the upper case SQL is written in here AND sit next to a
    companion keyword. Without that test the guard reported
    reprofile_corpus.py's help text -- "and update
    docs/chunk-boundary-measurement.md from its output" -- as the reason it was
    blocking a database write, which is exactly the kind of wrong-but-confident
    message that gets a guard switched off.
    """
    lits = _sql_literals(src)
    for lit in lits:
        m = MUTATING_SQL.search(lit)
        if not m:
            continue
        at_start = lit.lstrip().lower().startswith(m.group(0).split()[0].lower())
        looks_sql = m.group(0)[:6].isupper() and bool(SQL_COMPANION.search(lit))
        if at_start or looks_sql:
            return " ".join(lit[m.start():].split())[:60]
    if lits:
        return None  # parsed cleanly and nothing read as a statement
    m = MUTATING_SQL.search(src)  # unparseable source: fall back to raw text
    return " ".join(m.group(0).split()) if m else None


def source_writes_db(src, cwd, scoped):
    """Why this Python source can write the live job store, or None.

    `scoped` says the caller has already established that the live store is the
    target (an explicit --db on the command line). Otherwise the source itself
    must name it, so a script pointed at a scratch database is not gated.
    """
    # Scope: the LITERAL live path must appear, not merely the name of the env
    # var that usually holds it. Half the test suite sets MISSING_LINK_DB to a
    # tmp file, and gating those was the first false positive this rule
    # produced.
    if not scoped and DB_PATH not in src:
        return None
    stmt = first_mutating_sql(src)
    if stmt:
        return "it contains the statement `%s`" % stmt
    writers = db_writer_names(cwd)
    for name in sorted(writers):
        if re.search(r"\b(?:db\s*\.\s*)?%s\s*\(" % re.escape(name), src):
            return "it calls `db.%s()`, which db.py defines with a mutating " \
                   "statement" % name
    return None


DB_WRITE_ADVICE = (
    "If this program really is the migration, say so and run it with the "
    "operator's agreement. Whatever runs, the safeguards have to be IN THE "
    "SCRIPT, because the hook cannot see a predicate: (1) refuse to start "
    "unless the instrument/preconditions are what you think they are, (2) print "
    "the row count the write will touch BEFORE writing it and stop on 0 or on "
    "an unexpected number, (3) re-check a control value that must NOT move and "
    "abort if it did. Those three are what actually protected the F52 run. "
    "A dry-run mode that writes nothing should be the default."
)


def check_python_program(args, cwd, mode, text):
    """GATE `python <project script>` / `python -m <project module>` that can
    write the live job store. Silent on anything it cannot resolve."""
    try:
        path, label = resolve_python_target(args, cwd)
        if not path:
            return
        # Tests are excluded by construction: conftest points every one of them
        # at a tmp database, and several quote the live path in a comment about
        # exactly that. Gating `pytest` is how this rule would get switched off.
        parts = os.path.normpath(path).split(os.sep)
        if "tests" in parts or os.path.basename(path).startswith("test_") \
                or os.path.basename(path).endswith("_test.py"):
            return
        # An explicit --db elsewhere means the live store is not the target.
        scoped = False
        for i, a in enumerate(args):
            flag, val = None, None
            if a in DB_CLI_FLAGS and i + 1 < len(args):
                flag, val = a, args[i + 1]
            elif "=" in a and a.split("=", 1)[0] in DB_CLI_FLAGS:
                flag, val = a.split("=", 1)[0], a.split("=", 1)[1]
            if flag:
                if mentions_db([val], cwd):
                    scoped = True
                else:
                    return
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        why = source_writes_db(src, cwd, scoped)
    except Exception:
        return  # undecidable -> no decision; see the note above this section
    if not why:
        return
    gate(mode, "jobs-db-write",
         "`python %s` runs %s, and %s. That is a write to the live job store "
         "%s, reached through the interpreter -- the shape the guard MISSED on "
         "2026-08-18 (F52), when `python -m missing_link.reprofile_corpus "
         "--apply` issued 17 UPDATEs and no rule fired. %s"
         % (label, os.path.relpath(path, cwd) if cwd else path, why, DB_PATH,
            DB_WRITE_ADVICE), text)


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
        inline = None
        if "-c" in args and args.index("-c") + 1 < len(args):
            inline = args[args.index("-c") + 1]
            check_python_syntax(inline, "python -c snippet", text)
        elif body:
            inline = body
            check_python_syntax(body, "inline python heredoc", text)
        if inline is not None:
            # Source the guard can actually read: apply the same property test
            # as for a resolved script. This catches `python -c "from
            # missing_link import db; db.delete_corpus_document(...)"`, which
            # the sqlite3.connect regex in check_bash does not see.
            why = source_writes_db(inline, cwd, False)
            if why:
                gate(mode, "jobs-db-write",
                     "This inline Python writes the live job store %s: %s. "
                     "Reaching the store through the db layer instead of the "
                     "sqlite3 CLI does not make it a different operation -- that "
                     "is the F52 gap. %s" % (DB_PATH, why, DB_WRITE_ADVICE), text)
        else:
            check_python_program(args, cwd, mode, text)

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
    if mentions_db(paths, cwd):
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
            sql_args = [a for a in args if not a.startswith("-")
                        and not mentions_db([a], cwd)]
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

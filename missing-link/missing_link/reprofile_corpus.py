"""Re-profile `corpus_documents` after the sentence splitter changed (F48).

WHY THIS EXISTS
---------------
`n_sentences`, `n_marker_sentences` and `marker_rate` are properties of the
document AND the instrument that measured it. F48 replaced the instrument:
on the Privacy Act compilation the same text reads 8,455 units / 2.85% under
the old regex fallback and 1,861 units / 10.53% under nupunkt. Every row
currently in `corpus_documents` carries the first number.

**So the flag does not get flipped; the sweep gets re-run.** That is F45's
whole lesson at one remove -- a number whose instrument changed under it is
worse than no number, because it still looks like a measurement. Until this
has run, the corpus page shows a badge on every un-re-profiled row and the
figures across rows are not comparable with each other.

WHAT IT TOUCHES
---------------
Only the five columns that depend on the splitter, plus the provenance
column:

    n_sentences, n_marker_sentences, marker_rate, sentence_splitter

`n_chars`, `n_words`, `n_chunks` and `n_numbers` do NOT depend on the
splitter. They are recomputed anyway as a control and REPORTED IF THEY MOVED,
because if they have, something other than the splitter changed and this run
is not the change you think it is -- stop and find out what did.

It never touches `text`, `sha256`, `text_sha256`, `genre`, `note` or
`created_at`, and it never touches a `refused` row (no text to profile).

HOW TO RUN IT
-------------
    # 1. Dry run. Read-only. Prints every before/after and changes nothing.
    cd missing-link
    .venv/bin/python -m missing_link.reprofile_corpus

    # 2. Apply. Mutating SQL against the live job store is gated by
    #    .claude/hooks/cluster-guard.py -- that gate means the OPERATOR says
    #    yes, and an agent must never set it on its own initiative.
    .venv/bin/python -m missing_link.reprofile_corpus --apply \\
        --journal /var/tmp/reprofile-$(date +%Y%m%d).json

    # 3. Then the OTHER thing the splitter invalidated -- every figure in
    #    docs/chunk-boundary-measurement.md:
    .venv/bin/python -m missing_link.chunk_boundary_audit \\
        --out /var/tmp/chunk-boundary-nupunkt.json
    #    (note that one reads the `jobs` table, not `corpus_documents`, and
    #    writes no database rows at all -- it only prints and writes JSON)

MEASURED RUNTIME, on node 1 (Xeon E5-1620 v4, 4c/8t), 17 ready documents /
7.4 M characters: see the figure printed by --dry-run, which times the real
work on the real rows rather than asserting a number here that would go
stale. As measured on 2026-08-23 it was well under two minutes, all of it
single-threaded string work with NO model, NO cluster time and NO inference
-- but it is still CPU, so do not run it against a node that is mid-benchmark
(F44: even a niced CPU-bound sidecar measurably starves llama-server on a
4-core node).

IT REFUSES TO RUN ON THE WRONG INSTRUMENT
-----------------------------------------
`sentences.require("nupunkt")` is the first thing that happens. If nupunkt is
not installed, this exits non-zero rather than cheerfully rewriting every row
with regex numbers and stamping them "re-profiled". A script whose output is
a stored number must not accept a silent substitution of the thing doing the
measuring.
"""
import argparse
import json
import sqlite3
import sys
import time

from missing_link import corpus, db, sentences

DEFAULT_DB = "/opt/missing-link/jobs.sqlite"

# The columns this script is allowed to write. Anything not in here is either
# splitter-independent or provenance about the document itself.
SPLITTER_DEPENDENT = ("n_sentences", "n_marker_sentences", "marker_rate")
CONTROL = ("n_chars", "n_words", "n_chunks", "n_numbers", "numbers_per_1k_words")


def _rows(db_path):
    """Every ready row, oldest first, read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM corpus_documents WHERE status='ready' "
            "ORDER BY created_at, rowid")]
    finally:
        conn.close()


def _has_column(db_path, table, column):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return any(r[1] == column
                   for r in conn.execute(f"PRAGMA table_info({table})"))
    finally:
        conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--apply", action="store_true",
                   help="actually write. Default is a read-only dry run.")
    p.add_argument("--journal", default=None,
                   help="write before/after JSON here (recommended with --apply)")
    p.add_argument("--id", action="append", default=None,
                   help="limit to these corpus document ids (repeatable)")
    args = p.parse_args(argv)

    # 1. THE INSTRUMENT, FIRST. Refuse rather than degrade.
    try:
        splitter = sentences.require(sentences.NUPUNKT)
    except sentences.SplitterUnavailable as exc:
        print(f"REFUSING TO RUN: {exc}", file=sys.stderr)
        print("Install it first:  pip install nupunkt   (needs Python >= 3.11)",
              file=sys.stderr)
        return 2
    print(f"sentence splitter: {splitter}", file=sys.stderr)

    rows = _rows(args.db)
    if args.id:
        wanted = set(args.id)
        rows = [r for r in rows if r["id"] in wanted]

    # 2. THE ROW COUNT, PRINTED, BEFORE ANYTHING IS WRITTEN. Standing rule:
    #    a destructive statement gets its own SELECT count(*) with the
    #    IDENTICAL predicate first, and stops if the number is not what you
    #    expected. A wrong predicate is not something a hook can catch.
    print(f"{len(rows)} ready corpus documents match "
          f"(status='ready'{', ids=' + ','.join(args.id) if args.id else ''})",
          file=sys.stderr)
    if not rows:
        print("nothing to do -- 0 rows matched. Check --db and --id.",
              file=sys.stderr)
        return 1

    have_col = _has_column(args.db, "corpus_documents", "sentence_splitter")
    if not have_col:
        print("corpus_documents has no `sentence_splitter` column yet; "
              "--apply will add it (additive ALTER, via db.init_corpus_documents)",
              file=sys.stderr)

    # 3. Recompute. Read-only regardless of --apply.
    journal = {"db": args.db, "splitter": splitter, "applied": bool(args.apply),
               "documents": []}
    total_chars = sum(r["n_chars"] or len(r["text"]) for r in rows)
    t_start = time.time()
    control_moved = []

    for r in rows:
        t0 = time.time()
        new = corpus.profile(r["text"])
        dt = time.time() - t0
        assert new["sentence_splitter"] == splitter, (
            "profile() used a different splitter than require() reported -- "
            "stop and find out why")

        moved = {k: (r.get(k), new[k]) for k in CONTROL if r.get(k) != new[k]}
        if moved:
            control_moved.append((r["filename"], moved))

        entry = {
            "id": r["id"], "filename": r["filename"], "genre": r["genre"],
            "n_chars": new["n_chars"], "seconds": round(dt, 2),
            "before": {k: r.get(k) for k in SPLITTER_DEPENDENT},
            "after": {k: new[k] for k in SPLITTER_DEPENDENT},
            "before_splitter": r.get("sentence_splitter"),
            "after_splitter": splitter,
            "control_moved": moved,
        }
        journal["documents"].append(entry)

        print(f"  {r['filename'][:52]:<52} {r['genre'][:16]:<16} "
              f"sent {str(r.get('n_sentences')):>6} -> {new['n_sentences']:>6}   "
              f"marker {100 * (r.get('marker_rate') or 0):>6.2f}% -> "
              f"{100 * new['marker_rate']:>6.2f}%   {dt:>5.2f}s")

    elapsed = time.time() - t_start
    journal["seconds_total"] = round(elapsed, 1)
    print(f"\nprofiled {len(rows)} documents / {total_chars:,} chars in "
          f"{elapsed:.1f}s", file=sys.stderr)

    # 4. The control. A splitter change must not move these; if it did, this
    #    run is measuring something else as well and must not be trusted.
    if control_moved:
        print("\n*** SPLITTER-INDEPENDENT FIGURES MOVED. STOP. ***", file=sys.stderr)
        for fn, moved in control_moved:
            print(f"    {fn}: {moved}", file=sys.stderr)
        print("    n_chars/n_words/n_chunks/n_numbers do not depend on the "
              "sentence splitter, so something ELSE changed (chunker defaults, "
              "extraction, the stored text). Find out what before writing.",
              file=sys.stderr)
        if args.apply:
            print("    --apply refused.", file=sys.stderr)
            return 3

    if args.journal:
        with open(args.journal, "w") as fh:
            json.dump(journal, fh, indent=2)
        print(f"journal written to {args.journal}", file=sys.stderr)

    if not args.apply:
        print("\nDRY RUN -- nothing was written. Re-run with --apply "
              "(and --journal) to commit.", file=sys.stderr)
        return 0

    # 5. Write. Additive migration first, then one UPDATE per row, one
    #    transaction, and the affected row count checked against what we
    #    counted above.
    db.init_corpus_documents(args.db)
    conn = sqlite3.connect(args.db)
    try:
        with conn:
            n = 0
            for e in journal["documents"]:
                cur = conn.execute(
                    "UPDATE corpus_documents SET n_sentences=?, "
                    "n_marker_sentences=?, marker_rate=?, sentence_splitter=? "
                    "WHERE id=? AND status='ready'",
                    (e["after"]["n_sentences"], e["after"]["n_marker_sentences"],
                     e["after"]["marker_rate"], splitter, e["id"]))
                n += cur.rowcount
    finally:
        conn.close()

    print(f"UPDATED {n} rows (expected {len(rows)})", file=sys.stderr)
    if n != len(rows):
        print("*** row count mismatch -- inspect before trusting the corpus page ***",
              file=sys.stderr)
        return 4
    print("\nDone. Now re-run the boundary sweep, which the same change "
          "invalidated:\n"
          "  .venv/bin/python -m missing_link.chunk_boundary_audit "
          "--out /var/tmp/chunk-boundary-nupunkt.json\n"
          "and update docs/chunk-boundary-measurement.md from its output.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Does map-reduce amplify fabrication relative to single-pass? The MODEL-FREE half.

WHAT THIS ANSWERS, AND WHY IT IS SHAPED LIKE THIS
-------------------------------------------------
STATUS.md section 4 reframed the faithfulness evaluation. Reproducing a
hallucination leaderboard needs ~260 documents per model and cannot be done
here. The question that CAN be answered on a few dozen documents, and that the
leaderboard structurally cannot answer at all, is about OUR PIPELINE:

    a fabrication in a chunk summary becomes SOURCE MATERIAL for the reduce
    step, where it is indistinguishable from genuine content -- so errors do
    not merely persist, they get laundered.

F42 is one observed instance: a reduce step asserted a death year present in
neither the five chunk summaries nor the source, and it was caught by a plain
number-in-span check, not by a model. One instance is an existence proof, not
a rate.

The design is PAIRED -- same text, same model, same server, single-pass vs
map-reduce -- because document-level variance is the dominant noise term and
pairing cancels it. That is the entire reason a few dozen units can detect an
effect that hundreds could not detect across models. Everything below exists
to preserve that property; nothing here may be changed in a way that lets the
two arms see different text.

THE CONSTRAINT THAT SHAPES THE WHOLE DESIGN: ALMOST NOTHING FITS SINGLE-PASS
---------------------------------------------------------------------------
The single-pass arm needs the WHOLE unit in ONE llama-server slot, and a slot
is `n_ctx_slot`, not `-c`. On this fleet, confirmed from the server's own
startup log rather than inferred, `-c 32768 --parallel 4` gives
**n_ctx_slot = 8192** (worker.N_CTX_SLOT). Subtract the output budget and the
prompt wrapper and roughly 5,000 tokens of source are left.

The 17 real corpus documents run 3,313 to 198,810 words. At any plausible
tokens-per-word, **not one of them fits**, and the smallest is marginal at
best. So "whole document, single-pass vs map-reduce" is NOT EXECUTABLE on this
corpus, and pretending otherwise would mean comparing a summary of a whole
document against a summary of a truncated one -- unlike things, which is
exactly the trap this project keeps falling into (F27 benchmarked a
concurrency the deployment never runs; F41 validated a classifier at an
evidence length production never sees).

**So the unit of analysis is a document SECTION**, defined as a contiguous,
paragraph-aligned span that (a) provably fits one slot, measured with the
server's own tokeniser rather than estimated, and (b) still produces at least
two chunks under the real chunker, so the map-reduce arm really is map-reduce.
Both arms receive BYTE-IDENTICAL section text. Pairing is preserved exactly;
what is lost is generalisation to full document length, and that loss is
stated rather than hidden: a section produces fewer chunk summaries than a
whole document, so whatever amplification is measured here is a **LOWER BOUND**
on the amplification at production document length.

WHY THE SCORER IS DETERMINISTIC (F41 IS LOAD-BEARING HERE)
-----------------------------------------------------------
F41 measured the two-model MiniCheck ensemble's disagreement signal collapsing
when the evidence grew to production length: precision 1.00 -> 0.75, recall
1.00 -> 0.43, with 5.6% of errors silent to BOTH models, at 17.74 s and 8.81 s
per claim. Our evidence window here is a ~5,000-token section -- longer than
F41's 4,096-token fixture, i.e. further into the regime where the ensemble was
measured to fail, and at a cost that would exceed the inference being audited.

The deterministic cascade does not degrade with evidence length, because `in`
does not care how long the evidence is: 0/36 false alarms and ~92% of
fabrications caught-or-escalated at BOTH scales, 0 false positives on 978 real
claims, ~3.5 s for a whole 26-chunk document. So tiers 1 and 2 of
`missing_link.cascade` are the primary instrument and the classifier stays off,
which is also what the cascade's own write-up recommends.

WHAT PAIRING BUYS THE SCORER, AND IT IS NOT A SMALL THING
----------------------------------------------------------
The entity signal was DEMOTED for production (`ENTITY_MODE = "route"`) because
it flags roughly one faithful sentence in seven on OCR-damaged source, and a
checker that cries wolf teaches its reader to skim. That argument is about an
operator reading a flag list. It is NOT an argument against using the signal as
a research endpoint here, because **a false-positive rate that is the same in
both arms cancels in the within-pair difference.** Bias common to both arms is
not bias in the contrast.

That is why this module reports the entity endpoint alongside the number
endpoint: the number endpoint is the trustworthy one and the headline, but its
measured event rate is ~0.3% of claims, which is too rare to power a paired
test at n = 50 on its own. The entity endpoint is noisier per claim and far
more frequent, so it is where the power actually is. The assumption it rests
on -- arm-symmetric false positives -- is NOT verified by anything measured so
far, so `adjudication_sample()` exists to put it in front of a human, and the
harness refuses to report the entity endpoint as a finding without it.

NO CLUSTER TIME IS SPENT IN THIS MODULE. Section selection, unit building,
scoring and statistics are all string and arithmetic work over text that is
already in memory. The one function that needs the server is token counting,
and it is injected as a callable so this module never opens a socket. The
inference half lives in `bench/amplification_driver.py`.

F44 CONSTRAINS WHEN, NOT ONLY WHAT: a CPU-bound sidecar measurably starves
llama-server on a 4-core node even when niced. So scoring runs as its own
phase AFTER inference, never alongside it. The driver enforces that by making
`run` and `score` separate subcommands.
"""
import math
import random
import re
import sqlite3

from missing_link import cascade, worker

# ---------------------------------------------------------------------------
# Hop label
# ---------------------------------------------------------------------------
# `cascade.HOP_FINAL` is "final_vs_chunk_summaries", which is the WRONG evidence
# window for this experiment and would mislabel every finding. Hop 2 of the
# production audit scores the final summary against the chunk summaries the
# reduce step actually read -- correct for auditing a job, and useless here,
# because the single-pass arm has no chunk summaries at all. There is nothing to
# compare it to.
#
# So this experiment defines its own hop: BOTH arms' delivered output is scored
# against THE SAME EVIDENCE, the section source text. That is arm-symmetric by
# construction, and it is also the question a reader of the summary actually has
# ("does the document say this?"), which is the question F42's death year failed.
HOP_OUTPUT_VS_SOURCE = "final_output_vs_section_source"

# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------
# An arm is DATA, not code -- the same separability rule the worker's task
# profile follows (CLAUDE.md: prompts, chunking and evaluation stay separable
# from the queue and worker, because that seam becomes the skill's task-profile
# interface). Adding an arm means adding a dict entry; nothing in the driver's
# loop or in this module's scoring knows how many arms there are.
#
#   chunk_tokens=None  -> SINGLE PASS: one call, whole section, no reduce step.
#   chunk_tokens=N     -> map-reduce at N, through worker's real chunker and
#                         real prompts.
ARM_SINGLE_PASS = "single_pass"
ARM_MAP_REDUCE = "map_reduce_4096"
ARM_MAP_REDUCE_FINE = "map_reduce_1365"

ARMS = {
    ARM_SINGLE_PASS: {
        "chunk_tokens": None,
        "overlap_tokens": None,
        "why": "the control: one call, the whole section, no reduce step and "
               "therefore no laundering surface at all",
    },
    ARM_MAP_REDUCE: {
        # The PRODUCTION configuration, and it is production for a measured
        # reason: a real map-reduce sweep found wall-clock U-shaped in
        # CHUNK_TOKENS with its minimum at 4096 (docs/measurements.md,
        # "Chunk-size sweep"). Changing it here would make the experiment about
        # some other pipeline.
        "chunk_tokens": worker.CHUNK_TOKENS,
        "overlap_tokens": worker.OVERLAP_TOKENS,
        "why": "the treatment: exactly what the pipeline runs today",
    },
    ARM_MAP_REDUCE_FINE: {
        # OPTIONAL third arm, and it is the one that tests the MECHANISM rather
        # than the existence of an effect. If laundering is real, its dose is
        # the number of chunk summaries the reduce step must read: a section cut
        # into 5 pieces gives the reduce step five chances to launder where two
        # pieces give it two. 1365 is CHUNK_TOKENS/3, chosen to land near the
        # five-chunk shape F42 was actually observed on.
        #
        # NOT 1024, deliberately: 1024 was the worst wall-clock point in the
        # measured sweep AND its reduce step hit max_tokens and returned
        # truncated, which is a failed run, not a slow one.
        "chunk_tokens": 1365,
        "overlap_tokens": 137,
        "why": "dose-response: more chunk summaries into one reduce step",
    },
}

DEFAULT_ARMS = (ARM_SINGLE_PASS, ARM_MAP_REDUCE)


# ---------------------------------------------------------------------------
# Section sizing
# ---------------------------------------------------------------------------
# A slot must hold, at once: the section, the prompt wrapper, and the model's
# own answer. Same arithmetic as worker's guidance guard, same reason, same
# 15% headroom the project applies to every fit calculation.
#
# The output budget used here is REDUCE_MAX_TOKENS (2048), not MAP_MAX_TOKENS
# (1024), because the single-pass arm is producing a WHOLE-SECTION summary --
# the same job the reduce step does -- and giving it the smaller budget would
# make the control arm fail on `finish_reason == "length"` where the treatment
# arm did not. A guard that fires on one arm only is a confound, not a guard.
SECTION_OUTPUT_TOKENS = worker.REDUCE_MAX_TOKENS
SECTION_WRAPPER_TOKENS = worker._PROMPT_WRAPPER_TOKENS
SECTION_SAFETY = 0.85

# Selection filters. Both are applied IDENTICALLY to both arms -- they choose
# which text enters the experiment, never which arm sees it -- so they cannot
# bias the contrast. They exist to stop the experiment spending cluster-hours
# on sections that are incapable of producing a data point either way.
MIN_CHUNKS = 2          # below this the "map-reduce" arm is a single map call
                        # with no reduce step, i.e. it is the control again
MIN_NUMBERS = 10        # the primary endpoint is number-based; a section with
                        # no checkable figures contributes 0 to both arms and
                        # is pure cost. 10 is ~3 per 1000 words at this section
                        # size, comfortably under every corpus document's
                        # measured numbers_per_1k_words (23.9 at the lowest).

# The one number in the pre-run estimate that is NOT measured. Real
# tokens-per-word varies enormously WITHIN a single document -- the chunk-size
# sweep's own raw output ranges from ~0.95 to ~1.87 tokens per word on ONE text,
# because OCR mojibake tokenises catastrophically. So this is a planning
# placeholder only: `plan` replaces it per document with an exact count from the
# server's tokeniser, and every section that actually runs has been verified to
# fit, not estimated to fit.
ASSUMED_TOKENS_PER_WORD = 1.30


def single_pass_budget_tokens(slot_tokens=None, output_tokens=SECTION_OUTPUT_TOKENS,
                              wrapper_tokens=SECTION_WRAPPER_TOKENS,
                              safety=SECTION_SAFETY):
    """How many tokens of SOURCE may go into a single-pass call.

    `slot_tokens` defaults to `worker.N_CTX_SLOT`, which CLAUDE.md requires be
    READ FROM THE SERVER'S STARTUP LOG rather than inferred from `-c`. The
    driver re-reads it and passes it in; the default here is the last confirmed
    value so this function is usable in tests and in an estimate.
    """
    if slot_tokens is None:
        slot_tokens = worker.N_CTX_SLOT
    raw = slot_tokens - output_tokens - wrapper_tokens
    return max(0, int(raw * safety))


_PARA_SPLIT = re.compile(r"\n[ \t]*\n")


def paragraph_starts(text):
    """Offsets a section may begin or end at, in ascending order, starting at 0.

    Structural boundaries, so a section never starts or ends mid-sentence. That
    matters more than it looks: a single-pass arm handed a fragment that begins
    mid-clause has an incentive to COMPLETE it from world knowledge, which is
    the exact failure being measured, manufactured by the harness and charged to
    the control arm. The map-reduce arm gets the identical text, so the bias
    would not cancel -- it would land on one arm.

    Falls back to sentence starts for a document with no blank lines (extracted
    HTML sometimes has none), and to [0] for a text with neither.
    """
    if not text:
        return [0]
    starts = [0]
    for m in _PARA_SPLIT.finditer(text):
        if m.end() < len(text):
            starts.append(m.end())
    if len(starts) > 1:
        return starts
    # No paragraph structure at all -- fall back to sentences, using the
    # splitter that was repaired against real material (F41: nltk produces 9.1%
    # garbage fragments on this corpus and is not in the production venv).
    sents = cascade.sentence_units(text)
    if len(sents) > 1:
        return [0] + [s0 for s0, _s1, _t in sents[1:]]
    return [0]


def section_boundaries(text, max_gap):
    """Offsets a section may start or end at, refined so no gap exceeds `max_gap`.

    FOUND BY RUNNING IT ON THE REAL CORPUS, not by reasoning about it, and it is
    the F34/F38 pattern again. `paragraph_starts` alone looked correct and was
    useless: the HTML extraction of the Privacy Amendment (Notifiable Data
    Breaches) Act 2017 has 24 "paragraphs", 23 of which are under 40 characters
    and ONE of which is 29,053 -- the entire body of the Act with no blank line
    in it. Paragraph-aligned sectioning yielded ZERO usable sections from a
    document with plenty of usable text, and it would have done the same,
    silently, across most of a corpus that is mostly extracted HTML.

    So: paragraph boundaries where they exist, and SENTENCE boundaries inside
    any paragraph too large to be a section on its own. Both preserve the
    property that actually matters -- a section never begins mid-clause, so the
    control arm is never handed a fragment it is tempted to complete from world
    knowledge.
    """
    starts = paragraph_starts(text)
    bounds = starts + [len(text)]
    out = []
    for i in range(len(starts)):
        out.append(starts[i])
        gap = bounds[i + 1] - starts[i]
        if max_gap and gap > max_gap:
            for s0, _s1, _sent in cascade.sentence_units(
                    text[starts[i]:bounds[i + 1]]):
                if s0:
                    out.append(starts[i] + s0)
    return sorted(set(out))


def _calibrate_chars_per_token(text, count_tokens, probe_chars=20000):
    """One tokeniser call, to turn an exact counter into a cheap estimator.

    Section selection needs to ask "how long is this span?" many times. Asking
    the server every time would be hundreds of round trips per document. So one
    probe calibrates a chars-per-token ratio for THIS document, the ratio picks
    a candidate cut, and the exact counter then VERIFIES that candidate. The
    estimate never decides anything on its own -- it only proposes.
    """
    probe = text[:probe_chars]
    n = count_tokens(probe)
    if not n:
        return 4.0
    return len(probe) / n


def candidate_sections(text, count_tokens, budget_tokens=None,
                       chunk_tokens=worker.CHUNK_TOKENS,
                       overlap_tokens=worker.OVERLAP_TOKENS,
                       min_chunks=MIN_CHUNKS, min_numbers=MIN_NUMBERS,
                       max_sections=None):
    """Every non-overlapping section of `text` that can carry a paired data point.

    Returns [{start_char, end_char, n_tokens, n_words, n_chunks, n_numbers,
              rejected}] for accepted sections; rejected candidates are
    reported too (with a reason) rather than silently dropped, because "the
    corpus could never have produced a non-zero" is a failure this project has
    already paid for once (see `missing_link/corpus.py`).

    `count_tokens(str) -> int` must be EXACT. Estimating it is what makes a
    single-pass call fail at the server with a prompt-too-long error, or worse,
    silently drop the start of the section to make room.

    COST, measured on node 1: `cascade.extract_numbers` runs at roughly 19 s per
    megabyte of number-dense text, and it is called once per accepted section.
    Over the whole 7.4 MB corpus that is ~2-3 minutes of single-threaded CPU.
    Cheap as a one-off, but F44 measured a CPU-bound sidecar starving
    llama-server on a 4-core node -- so do not run `plan` while the cluster is
    working, for the same reason `score` is a separate phase.
    """
    if budget_tokens is None:
        budget_tokens = single_pass_budget_tokens()
    cpt = _calibrate_chars_per_token(text, count_tokens)
    target_chars = int(budget_tokens * cpt)
    bounds = section_boundaries(text, target_chars) + [len(text)]

    out = []
    i = 0
    while i < len(bounds) - 1:
        start = bounds[i]
        # Propose: the furthest boundary the ESTIMATE says still fits.
        j = i + 1
        while j < len(bounds) and (bounds[j] - start) <= target_chars:
            j += 1
        j -= 1
        if j <= i:
            # A single SENTENCE already exceeds the budget (`section_boundaries`
            # has already subdivided oversized paragraphs). Do not split it --
            # that would put a section boundary mid-clause and reintroduce the
            # fragment problem above. Skip it and record why, rather than
            # dropping it silently: a corpus that cannot answer the question is
            # something to find out before the run, not after it.
            out.append({"start_char": start, "end_char": bounds[i + 1],
                        "rejected": "a single sentence exceeds the single-pass "
                                    "budget on its own; splitting it would "
                                    "start a section mid-clause"})
            i += 1
            continue

        # Verify exactly, shrinking a boundary at a time until it really fits.
        while j > i:
            span = text[start:bounds[j]]
            n_tok = count_tokens(span)
            if n_tok <= budget_tokens:
                break
            j -= 1
        else:
            out.append({"start_char": start, "end_char": bounds[i + 1],
                        "rejected": "no paragraph-aligned prefix fits the "
                                    "single-pass budget"})
            i += 1
            continue

        end = bounds[j]
        span = text[start:end]
        n_chunks = len(worker.chunk_document(span, chunk_tokens=chunk_tokens,
                                             overlap_tokens=overlap_tokens))
        n_numbers = len(cascade.extract_numbers(span))
        rec = {"start_char": start, "end_char": end, "n_tokens": n_tok,
               "n_words": len(span.split()), "n_chunks": n_chunks,
               "n_numbers": n_numbers, "rejected": None}
        if n_chunks < min_chunks:
            rec["rejected"] = (
                f"{n_chunks} chunk at chunk_tokens={chunk_tokens} -- the "
                "map-reduce arm would be a single map call with no reduce "
                "step, which is the control arm again")
        elif n_numbers < min_numbers:
            rec["rejected"] = (
                f"{n_numbers} checkable figures (< {min_numbers}) -- cannot "
                "exercise the cascade's hard number tier in EITHER arm")
        out.append(rec)
        i = j
        if max_sections and sum(1 for r in out if not r["rejected"]) >= max_sections:
            break
    return out


def sample_sections(candidates_by_doc, per_doc=3, seed=None):
    """Pick `per_doc` accepted sections from each document, spread across it.

    DETERMINISTIC BY DEFAULT and spread by position rather than drawn at random:
    with only three picks per document, a random draw can easily take all three
    from the front matter, and the front matter of a statute or a NIST standard
    is a table of contents, not prose. Even spacing is reproducible, needs no
    seed, and is defensible on the page.

    `seed` switches to a genuine random draw, kept only because "we chose the
    sections" is a fair criticism to be able to answer with "here is the same
    result on a random sample".

    CLUSTERING IS REAL AND IS NOT SOLVED HERE. Three sections from one statute
    are not three independent observations. `paired_summary` therefore reports
    BOTH a section-level test and a document-level one (each document
    contributing a single mean difference), and the document-level result is the
    conservative one to quote.
    """
    picked = []
    for doc_id in sorted(candidates_by_doc):
        acc = [c for c in candidates_by_doc[doc_id] if not c.get("rejected")]
        if not acc:
            continue
        if seed is not None:
            rng = random.Random(f"{seed}:{doc_id}")
            take = sorted(rng.sample(range(len(acc)), min(per_doc, len(acc))))
        elif len(acc) <= per_doc:
            take = list(range(len(acc)))
        else:
            # Quantile positions: for per_doc=3 that is 1/6, 3/6, 5/6 of the way
            # through the document -- never the first or last candidate, which
            # are the ones most likely to be front or back matter.
            take = [min(len(acc) - 1, int((2 * k + 1) * len(acc) / (2 * per_doc)))
                    for k in range(per_doc)]
            take = sorted(set(take))
        for rank, idx in enumerate(take):
            rec = dict(acc[idx])
            rec["doc_id"] = doc_id
            rec["section_id"] = f"{doc_id}:{rec['start_char']}-{rec['end_char']}"
            rec["rank_in_doc"] = rank
            picked.append(rec)
    return picked


# ---------------------------------------------------------------------------
# Reading the corpus -- READ-ONLY, and deliberately not through db.py
# ---------------------------------------------------------------------------
def read_corpus(db_path, doc_ids=None):
    """Corpus documents from the LIVE job store, opened read-only.

    `db.list_corpus_documents` is the documented accessor and would work, but it
    opens the database read-write. This is the live job store of a running
    service; a benchmark has no business holding a writable handle on it, and
    `mode=ro` makes that a property of the connection rather than a promise
    about the SQL. Nothing here is a mutation, and nothing here CAN be.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, filename, genre, text, text_sha256, n_words, n_chunks, "
            "n_numbers, numbers_per_1k_words FROM corpus_documents "
            "WHERE status='ready' ORDER BY n_words").fetchall()
    finally:
        con.close()
    docs = [dict(r) for r in rows]
    if doc_ids:
        want = set(doc_ids)
        docs = [d for d in docs if d["id"] in want]
    return docs


def running_jobs(db_path):
    """How many jobs the live queue currently has in flight. READ-ONLY.

    STATUS.md's standing rule: do not benchmark a node while it or its peer is
    doing real work. A restart destroys in-flight work (F39 lost 10m55s that
    way), and a benchmark sharing a server with a real job measures neither.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def final_vs_source_units(section_text, final_output):
    """Claim units for the cascade: every clause of the delivered output,
    scored against THE SECTION SOURCE.

    This is the arm-symmetry guarantee, and it is the single most important
    function in the module. `cascade.hop_units` cannot be used: its hop 2
    evidence is the concatenated chunk summaries, which exist in one arm and not
    the other. Scoring the two arms against different evidence would not be a
    paired comparison at all, it would be two unrelated measurements printed
    next to each other.
    """
    units = []
    for s0, s1, sent, s_i, c_i in cascade.claim_spans(final_output,
                                                      cascade.DECOMPOSE):
        units.append({
            "hop": HOP_OUTPUT_VS_SOURCE,
            "claim_text": sent,
            "evidence_text": section_text,
            "claim": {"unit": "final_output", "chunk_index": None,
                      "sentence_index": s_i, "clause_index": c_i,
                      "start_char": s0, "end_char": s1,
                      "line": cascade.line_of(final_output, s0)},
            "evidence": {"unit": "section_source", "chunk_index": None,
                         "start_char": 0, "end_char": len(section_text),
                         "location_confidence": "direct"},
        })
    return units


def document_scope(document_text, section, label=None):
    """The WIDER scope: the parent document, minus nothing.

    Passing this to the cascade splits a hard failure into
    `number_fabricated` (the value occurs nowhere in the parent document) and
    `number_misattributed` (it occurs elsewhere in the document but not in the
    section the model was shown). The distinction matters here: the second is
    the model bleeding context it was never given in this arm, which is a
    different failure from invention -- and, unlike invention, it is a failure
    the single-pass arm can commit too.

    A wider scope can only ever RECLASSIFY a failure. It can never rescue one,
    by construction (`cascade._widen_numbers`), which is what makes it safe to
    pass here.
    """
    lbl = label or "the parent document"
    section_part = ("the section shown to the model",
                    document_text[section["start_char"]:section["end_char"]])
    before = ("earlier in the document", document_text[:section["start_char"]])
    after = ("later in the document", document_text[section["end_char"]:])
    parts = [p for p in (section_part, before, after) if p[1]]
    return cascade.DocumentScope(parts, lbl)


def score_output(section_text, final_output, scope=None, source=None):
    """Run the deterministic cascade over one arm's delivered output.

    `classifier=None` is not a default that happened, it is the recommendation
    F41 produced and the cascade's own write-up repeats: at production evidence
    length the ensemble's disagreement signal has precision 0.75 / recall 0.43
    and costs more than the summarisation it audits. There is no code path here
    that turns it on.
    """
    units = final_vs_source_units(section_text, final_output)
    return cascade.build_cascade_ledger(
        units, classifier=None, scope=scope, include_passing=True,
        source=source)


# Categories the number tier may return. `number_unsupported` only appears when
# no scope was given; with a scope it always resolves to one of the other two.
NUMBER_CATEGORIES = (cascade.CAT_NUMBER, cascade.CAT_NUMBER_FABRICATED,
                     cascade.CAT_NUMBER_ELSEWHERE)
ENTITY_CATEGORIES = (cascade.CAT_ENTITY, cascade.CAT_ENTITY_FABRICATED,
                     cascade.CAT_ENTITY_ELSEWHERE)


def _entity_signal(finding):
    for sig in finding.get("signals") or []:
        if sig.get("name") == "entities":
            return sig
    return None


def _entity_absences(finding):
    """Terms the entity signal says do not occur in the section.

    `missing` is `check_entities`' own key for exactly that, and it is the right
    endpoint here: the section IS the cited span, so "missing" means "the model
    named something the text it was shown does not name".
    """
    sig = _entity_signal(finding)
    if not sig:
        return []
    return [m.get("term") for m in (sig.get("missing") or []) if m.get("term")]


def _entity_absent_from_document(finding):
    """The stronger subset: absent from the PARENT DOCUMENT too, not merely
    from the section. Only populated when a scope was supplied."""
    sig = _entity_signal(finding)
    if not sig:
        return []
    return [m.get("term") for m in (sig.get("absent_from_document") or [])
            if m.get("term")]


def endpoints(ledger):
    """The pre-registered endpoints, extracted from one arm's ledger.

    Every count is reported BOTH raw and per 100 claims. That is not padding:
    the two arms produce outputs of different lengths, so a raw count difference
    is confounded with verbosity, and a rate alone hides an arm that said almost
    nothing. A reader needs both to believe either.

    E1  numbers        -- PRIMARY. Hard, deterministic, scale-invariant.
    E2  entities       -- SECONDARY. Higher event rate, known false positives,
                          usable only because they are assumed arm-symmetric.
    E3  laundering     -- computed separately (needs the chunk summaries).
    E4  escalation     -- COVERAGE, reported and never tested. The cascade's own
                          write-up says escalation rate is not a quality metric.
    E5  size           -- the confound guard above.
    """
    casc = ledger["cascade"]
    findings = ledger["findings"]
    n_claims = casc["claims"]
    checkable = casc["claims_checkable"] or 0

    number_findings = [f for f in findings if f["category"] in NUMBER_CATEGORIES]
    entity_hard = [f for f in findings if f["category"] in ENTITY_CATEGORIES]
    # In ENTITY_MODE="route" -- the default, and the measured one -- an absent
    # name escalates rather than failing, so it lands in `needs_classifier`
    # findings. Reading the SIGNAL rather than the category is therefore the
    # only way to see this endpoint at all, and it is why the entity count below
    # is not simply `by_category`.
    entity_routed = [f for f in findings
                     if f["category"] == cascade.CAT_NEEDS_CLASSIFIER
                     and _entity_absences(f)]

    def rate(n):
        return round(100.0 * n / n_claims, 3) if n_claims else None

    return {
        "claims": n_claims,
        "claims_checkable": checkable,
        "unscoreable": casc["unscoreable"],

        "e1_number_findings": len(number_findings),
        "e1_number_fabricated": casc["hard_fail_number_fabricated"],
        "e1_number_misattributed": casc["hard_fail_number_misattributed"],
        "e1_number_scope_unknown": casc["hard_fail_number_scope_unknown"],
        "e1_per_100_claims": rate(len(number_findings)),
        "e1_any": 1 if number_findings else 0,

        "e2_entity_claims": len(entity_hard) + len(entity_routed),
        "e2_entity_terms": sum(len(_entity_absences(f))
                               for f in entity_hard + entity_routed),
        "e2_entity_terms_absent_from_document": sum(
            len(_entity_absent_from_document(f))
            for f in entity_hard + entity_routed),
        "e2_per_100_claims": rate(len(entity_hard) + len(entity_routed)),
        "e2_any": 1 if (entity_hard or entity_routed) else 0,

        "e4_escalated": casc["escalated"],
        "e4_escalation_rate": casc["escalation_rate"],

        "e5_output_chars": None,     # filled by the caller, which has the text
        "e5_passed_cheaply": casc["passed_cheaply"],
        "cheap_tier_seconds": casc["cheap_tier_seconds"],
    }


def laundering_decomposition(ledger, chunk_summaries):
    """E3: for a map-reduce arm, WHERE did each unsupported figure come from?

    Three outcomes, and the difference between them is the whole architectural
    claim:

      inherited        the value appears in a chunk summary. The map step
                       fabricated it and the reduce step passed it through --
                       laundering in the sense F25 predicted.
      invented_at_reduce
                       the value appears in NO chunk summary and not in the
                       source. The reduce step made it up from a context that
                       contained only the chunk summaries. **This is exactly
                       F42's shape**, and F42 notes it did not need the
                       predicted path: the reduce step invented directly.
      unknown          no chunk summaries available (the single-pass arm), so
                       the question does not apply.

    A single-pass arm has NO reduce step and therefore no laundering surface at
    all. That is not a limitation of the measurement, it is the thing being
    measured: the count of `invented_at_reduce` is the extra failure mode
    map-reduce introduces, and it has no counterpart in the control.
    """
    if not chunk_summaries:
        return {"applicable": False, "inherited": 0, "invented_at_reduce": 0,
                "detail": []}
    joined = "\n\n".join(chunk_summaries)
    ev = cascade.NumberEvidence(joined)
    inherited, invented, detail = 0, 0, []
    for f in ledger["findings"]:
        if f["category"] not in NUMBER_CATEGORIES:
            continue
        for sig in f.get("signals") or []:
            if sig.get("name") != "numbers":
                continue
            for u in sig.get("unmatched") or []:
                claim_num = u.get("claim") or {}
                nums = cascade.extract_numbers(claim_num.get("text") or "")
                found = None
                for n in nums:
                    e_dict, _via, _best = ev.lookup(n)
                    if e_dict is not None:
                        found = e_dict
                        break
                if found is not None:
                    inherited += 1
                    where = "inherited"
                else:
                    invented += 1
                    where = "invented_at_reduce"
                detail.append({"value": claim_num.get("text"),
                               "normalised": claim_num.get("value"),
                               "origin": where,
                               "claim": f["claim"].get("text")})
    return {"applicable": True, "inherited": inherited,
            "invented_at_reduce": invented, "detail": detail}


def adjudication_sample(ledger, n_passing=10, seed=0):
    """What a human must read before any of this counts as a finding.

    TWO halves, and the second is the one people skip.

    Every FINDING, because F42's three real findings were all verified by hand
    and the cascade's 0/978 false-positive claim rests on exactly that.

    And a sample of PASSING claims, because a checker's defects land almost
    entirely on sentences that were going to pass -- five separate defects in
    the cascade were each found only by running on real output, and every one
    produced a FALSE HARD FAILURE on correct text. A validation set drawn only
    from flagged material cannot see any of them.
    """
    rng = random.Random(seed)
    passing = ledger.get("passing") or []
    sample = rng.sample(passing, min(n_passing, len(passing))) if passing else []
    return {
        "findings": [{"category": f["category"], "tier": f["tier"],
                      "claim": f["claim"].get("text"), "detail": f["detail"]}
                     for f in ledger["findings"]],
        "passing_sample": [{"claim": p["claim"].get("text")} for p in sample],
        "instruction": (
            "Read every finding against the section source and mark it "
            "GENUINE or FALSE ALARM. Then read the passing sample and mark "
            "each SUPPORTED or MISSED. The second half is not optional: a "
            "checker validated only on material known to be wrong cannot "
            "measure the failure mode it actually has."),
    }


# ---------------------------------------------------------------------------
# Statistics -- exact, stdlib only
# ---------------------------------------------------------------------------
# No numpy, no scipy: the production venv has neither and this is not worth a
# dependency. Every test below is exact or a permutation, so nothing here
# depends on a normal approximation that n = 50 would not support anyway.

def mcnemar_exact(b, c):
    """Two-sided exact McNemar on discordant pairs only.

    `b` = pairs where arm A flagged and arm B did not; `c` = the reverse.
    CONCORDANT PAIRS CARRY NO INFORMATION and are correctly ignored -- which is
    also the warning: with a rare endpoint, 50 pairs can yield 4 discordant
    ones, and 4 discordant pairs cannot reach significance no matter how they
    split. `min_discordant_for_significance` below says how many are needed.
    """
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "discordant": 0, "p": 1.0,
                "note": "no discordant pairs -- the two arms flagged the same "
                        "sections, so this test has nothing to work with"}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return {"b": b, "c": c, "discordant": n, "p": min(1.0, 2 * tail)}


def min_discordant_for_significance(alpha=0.05):
    """The smallest number of discordant pairs that CAN reach `alpha`.

    Stated up front, before the run, because the alternative is discovering
    after twenty cluster-hours that the design could never have produced a
    significant result -- which is the same failure `missing_link/corpus.py`
    exists to prevent for chunk boundaries, and it is worth refusing to repeat.
    """
    n = 1
    while n < 200:
        if 2 * (1 / (2 ** n)) <= alpha:
            return n
        n += 1
    return None


def sign_flip_test(diffs, iterations=20000, seed=0):
    """Exact (or Monte-Carlo) paired permutation test on per-pair differences.

    Under the null the pipeline makes no difference, so the sign of each pair's
    difference is exchangeable. Enumerated exactly for <= 20 pairs; sampled
    above that. Assumes nothing about the distribution of counts, which matters
    because fabrication counts are zero-inflated and nothing normal-theory
    would be honest about that.
    """
    d = [x for x in diffs if x != 0]
    if not d:
        return {"n_pairs": len(diffs), "n_nonzero": 0, "observed_mean": 0.0,
                "p": 1.0, "method": "none",
                "note": "every pair is tied -- no evidence in either direction"}
    obs = sum(d) / len(d)
    target = abs(obs)
    if len(d) <= 20:
        total, hits = 0, 0
        for mask in range(1 << len(d)):
            s = sum(x if (mask >> i) & 1 else -x for i, x in enumerate(d))
            total += 1
            if abs(s / len(d)) >= target - 1e-12:
                hits += 1
        return {"n_pairs": len(diffs), "n_nonzero": len(d),
                "observed_mean": round(obs, 4), "p": hits / total,
                "method": "exact enumeration"}
    rng = random.Random(seed)
    hits = 0
    for _ in range(iterations):
        s = sum(x if rng.random() < 0.5 else -x for x in d)
        if abs(s / len(d)) >= target - 1e-12:
            hits += 1
    return {"n_pairs": len(diffs), "n_nonzero": len(d),
            "observed_mean": round(obs, 4),
            "p": (hits + 1) / (iterations + 1),
            "method": f"monte carlo, {iterations} sign flips"}


def bootstrap_ci(diffs, iterations=10000, alpha=0.05, seed=0):
    """Percentile bootstrap CI for the mean paired difference."""
    if not diffs:
        return {"mean": None, "lo": None, "hi": None}
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iterations):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return {"mean": round(sum(diffs) / n, 4), "lo": round(lo, 4),
            "hi": round(hi, 4), "iterations": iterations}


def paired_summary(pairs, key, arm_a=ARM_SINGLE_PASS, arm_b=ARM_MAP_REDUCE):
    """The whole analysis for one endpoint.

    `pairs` is [{"section_id", "doc_id", arm_a: endpoints, arm_b: endpoints}].

    Reports the section-level test AND a document-level one in which each
    document contributes a single mean difference. Sections from one statute are
    correlated, so the section-level p-value is optimistic; the document-level
    one is the number to quote when they disagree. Reporting only the friendlier
    of the two would be the statistical version of benchmarking a concurrency
    the deployment never runs.
    """
    diffs, by_doc, b, c = [], {}, 0, 0
    for p in pairs:
        if arm_a not in p or arm_b not in p:
            continue
        a_v = p[arm_a].get(key) or 0
        b_v = p[arm_b].get(key) or 0
        diffs.append(b_v - a_v)
        by_doc.setdefault(p["doc_id"], []).append(b_v - a_v)
        a_flag = 1 if a_v else 0
        b_flag = 1 if b_v else 0
        if a_flag and not b_flag:
            b += 1
        elif b_flag and not a_flag:
            c += 1
    doc_diffs = [sum(v) / len(v) for v in by_doc.values()]
    return {
        "endpoint": key,
        "arm_a": arm_a, "arm_b": arm_b,
        "direction": f"positive means {arm_b} produced MORE than {arm_a}",
        "n_pairs": len(diffs),
        "n_documents": len(by_doc),
        "section_level": {
            "mean_difference": round(sum(diffs) / len(diffs), 4) if diffs else None,
            "permutation": sign_flip_test(diffs),
            "bootstrap_ci": bootstrap_ci(diffs),
            "mcnemar_on_any": mcnemar_exact(b, c),
        },
        "document_level": {
            "mean_difference": (round(sum(doc_diffs) / len(doc_diffs), 4)
                                if doc_diffs else None),
            "permutation": sign_flip_test(doc_diffs),
            "bootstrap_ci": bootstrap_ci(doc_diffs),
            "note": "each document contributes one mean difference. Quote THIS "
                    "when it disagrees with the section-level result -- "
                    "sections from one document are not independent.",
        },
        "power_note": (
            "McNemar needs at least "
            f"{min_discordant_for_significance()} discordant pairs before any "
            "split of them can reach p < 0.05. If the discordant count is "
            "below that, the correct report is 'underpowered', not 'no effect'."),
    }


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------
# EVERY CONSTANT BELOW COMES FROM docs/measurements.md AND NOWHERE ELSE.
# CLAUDE.md: "If a number is not in docs/measurements.md, it may not be quoted."

# Mainline llama.cpp b10369, gpt-oss-120b F16, through llama-server with
# --parallel 4, read from the server's own `slot print_timing` lines (never from
# client-side timing, F17). Measured 16.29 / 16.26 / 16.27 / 16.36 tok/s across
# four requests -- a spread of 0.6%, and notably FLAT as the KV cache fills,
# unlike ik_llama.cpp which decays. Section: "ik_llama.cpp vs mainline through
# llama-server".
PREFILL_TPS = 16.3

# Same measurement, same table: mainline generated at 5.26 / 5.26 / 5.34 / 5.34.
# NOT 6.05 -- that is the llama-bench figure, and F40's lesson is precisely that
# a benchmark which does not reproduce the deployment's concurrency is not a
# benchmark of the deployment.
GENERATION_TPS = 5.3

# Observed generated tokens per call, INFERRED from two figures that are both in
# measurements.md: the CHUNK_TOKENS=4096 row of the chunk-size sweep generated
# for 1030.9 s across 8 calls (7 maps + 1 reduce), which at 5.3 tok/s is ~5460
# tokens, i.e. ~680 per call. Map calls run shorter than reduce calls, so this
# is split below rather than applied flat. Labelled INFERRED, not measured.
MAP_OUTPUT_TOKENS = 550
REDUCE_OUTPUT_TOKENS = 900
SINGLE_PASS_OUTPUT_TOKENS = 900   # same job as a reduce: summarise the lot

# A chunk summary re-read by the reduce step costs its own length in prefill.
SUMMARY_PREFILL_TOKENS = MAP_OUTPUT_TOKENS

# Aggregate throughput across two independent llama-servers, measured:
# ~1.8x on two nodes, ~90% of linear. Section: "THE REPLICATION MEASUREMENT".
REPLICATION_SPEEDUP_2_NODES = 1.8


def estimate_arm_seconds(section_tokens, arm):
    """Wall-clock for ONE section through ONE arm, on ONE node, sequentially.

    Prefill and generation are costed separately because on this hardware they
    are different machines: prefill is compute-bound and generation is
    bandwidth-bound, and prefill is ~79% of document wall-clock.
    """
    spec = ARMS[arm]
    ct = spec["chunk_tokens"]
    if ct is None:
        prefill = section_tokens + SECTION_WRAPPER_TOKENS
        gen = SINGLE_PASS_OUTPUT_TOKENS
        calls = 1
    else:
        ov = spec["overlap_tokens"]
        n_chunks = max(1, math.ceil(
            (section_tokens - ov) / max(1, ct - ov)))
        # Overlap is re-read, so map prefill exceeds the section by (n-1) x
        # overlap. That re-read is the mechanism behind the measured U-shaped
        # chunk-size curve and it is not a rounding error.
        prefill = (section_tokens + (n_chunks - 1) * ov
                   + n_chunks * SECTION_WRAPPER_TOKENS)
        gen = n_chunks * MAP_OUTPUT_TOKENS
        calls = n_chunks
        if n_chunks > 1:
            prefill += n_chunks * SUMMARY_PREFILL_TOKENS + SECTION_WRAPPER_TOKENS
            gen += REDUCE_OUTPUT_TOKENS
            calls += 1
    return {"arm": arm, "calls": calls,
            "prefill_tokens": int(prefill), "generation_tokens": int(gen),
            "prefill_s": round(prefill / PREFILL_TPS, 1),
            "generation_s": round(gen / GENERATION_TPS, 1),
            "seconds": round(prefill / PREFILL_TPS + gen / GENERATION_TPS, 1)}


def estimate_run(n_sections, arms=DEFAULT_ARMS, section_tokens=None,
                 nodes=1, overhead=1.12):
    """Total cluster time for a whole run. This is what schedules the night.

    `overhead` covers the health gate between requests, client-side
    bookkeeping, and the fact that generated length varies. 12% is a judgement,
    labelled as one -- it is not measured and is not pretending to be.

    `nodes=2` divides by the MEASURED 1.8x aggregate, not by 2. Replication on
    this fleet was measured at ~90% of linear, and quoting 2x would be quoting
    arithmetic instead of a measurement.
    """
    if section_tokens is None:
        section_tokens = single_pass_budget_tokens()
    per_section = [estimate_arm_seconds(section_tokens, a) for a in arms]
    one = sum(x["seconds"] for x in per_section)
    total = one * n_sections * overhead
    if nodes >= 2:
        total /= REPLICATION_SPEEDUP_2_NODES
    return {
        "n_sections": n_sections,
        "arms": list(arms),
        "section_tokens": section_tokens,
        "per_arm": per_section,
        "seconds_per_section_one_node": round(one, 1),
        "nodes": nodes,
        "overhead_factor": overhead,
        "total_seconds": round(total, 1),
        "total_hours": round(total / 3600.0, 1),
        "basis": ("prefill 16.3 tok/s and generation 5.3 tok/s, both mainline "
                  "b10369 / gpt-oss-120b F16 measured THROUGH llama-server at "
                  "--parallel 4 (docs/measurements.md); two-node divisor is the "
                  "measured 1.8x aggregate, not 2x"),
    }

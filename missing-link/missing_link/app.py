"""Missing Link web API and UI.

Deliberately minimal: no auth, no distributed workers. It runs on an isolated
network by design (see the security posture in CLAUDE.md -- securing the
network is the organisation's responsibility, and this app makes no claim to
help).

The point of this layer is that slowness stops being a defect. A chat window
makes a user wait; a job queue lets them submit and leave. Queue control
(reordering, cancel/stop) and a completion marker exist for the same reason:
"submit and leave" only works if leaving does not mean losing control of the
queue, or missing that something finished.
"""
import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from missing_link import corpus, db, extract, worker

DB_PATH = os.environ.get("MISSING_LINK_DB", "/opt/missing-link/jobs.sqlite")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")

# R inference endpoints, not one. `provisioning/nodes.env` already carries this
# list as the bash array INFERENCE_ENDPOINTS -- kept deliberately separate from
# the RPC endpoints (see nodes.env: under replication these are R independent
# llama-servers, not a shard coordinator). LLAMA_URLS is the Python-side
# equivalent: comma-separated, so it can be set straight from that same list.
#
# Falling back to the single LLAMA_URL keeps a one-endpoint deployment behaving
# EXACTLY as before -- one worker, one server, same as pre-fan-out.
_LLAMA_URLS_ENV = os.environ.get("LLAMA_URLS")
if _LLAMA_URLS_ENV:
    LLAMA_URLS = [u.strip() for u in _LLAMA_URLS_ENV.split(",") if u.strip()]
else:
    LLAMA_URLS = [LLAMA_URL]

# Per-endpoint status, read by /health and the index page. Plain dict rather
# than anything fancier: each entry is only ever written by its own worker
# task (see _worker_loop) and read by request handlers, and CPython's GIL makes
# single dict-key assignment atomic, so no lock is needed for this -- the same
# level of care the rest of this module uses elsewhere.
ENDPOINT_STATE = {
    url: {"reachable": None, "current_job": None, "last_checked": None, "last_error": None}
    for url in LLAMA_URLS
}

# Capped backoff for an endpoint that fails its health probe: retried
# periodically rather than spun hot, but short enough to rejoin within a
# minute of coming back. Module-level constants (not literals inside the loop)
# so tests can shrink them instead of sleeping for real seconds.
WORKER_BACKOFF_BASE_S = 5
WORKER_BACKOFF_CAP_S = 60
WORKER_IDLE_POLL_S = 5

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# The preview a non-specialist gets per uploaded document, in the batch review
# table. A PDF page-1 thumbnail was considered and rejected: pypdf (already a
# dependency) does not rasterise, and adding an imaging stack (pypdfium2,
# pdf2image + poppler, etc.) to summarise text is a heavy dependency for a
# cosmetic feature. First-N-characters is free, works for every accepted
# format uniformly, and is often more informative than a page image for prose
# documents. See the report for this trade-off spelled out.
PREVIEW_CHARS = 200

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db.init_db(DB_PATH)
    # Recover anything stranded 'running' by a crash. Jobs here run for hours,
    # so an unclean shutdown mid-job is routine rather than exceptional.
    stranded = db.requeue_running(DB_PATH)
    if stranded:
        print(f"[startup] requeued {stranded} job(s) stranded by a previous run")

    # requeue_running() runs exactly once, here, BEFORE any worker task exists
    # -- see its docstring in db.py. That ordering is what keeps it safe now
    # that there is more than one worker: every 'running' row it finds was
    # stranded by a PREVIOUS process, never by a sibling worker in this one.
    tasks = []
    if not os.environ.get("MISSING_LINK_NO_WORKER"):
        # One worker per inference endpoint (job-level fan-out, not
        # chunk-level -- see docs/DESIGN-NOTES.md section G). Each claims jobs
        # independently against the shared queue; db.claim_next_pending's
        # BEGIN IMMEDIATE (F20) is what makes that safe with more than one
        # claimer.
        tasks = [asyncio.create_task(_worker_loop(url)) for url in LLAMA_URLS]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="Missing Link", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "index.html",
        {"jobs": db.list_jobs(DB_PATH), "kinds": sorted(worker.PROMPTS),
         "rate": _rate_note(), "backlog": db.pending_chunk_backlog(DB_PATH),
         "tput": db.throughput_stats(DB_PATH), "active": "home",
         # How a returning user finds out what finished without polling every
         # job page. See db.count_unseen.
         "unseen": db.count_unseen(DB_PATH),
         # One row per inference endpoint. Under replication a node failure
         # costs 1/R of throughput rather than the job, so the operator needs to
         # see WHICH endpoint went away -- otherwise the only symptom is "it got
         # slower". See _endpoint_rows.
         "endpoints": _endpoint_rows(),
         # Browser hint only -- see extract.ACCEPT_ATTR docstring. The server
         # keeps validating whatever actually arrives regardless of this.
         "accept": extract.ACCEPT_ATTR},
    )


async def _resolve_instruction(form, kind):
    """Combine typed guidance with an optional guidance FILE, for one workflow.

    Guidance -- typed or extracted from a file -- is embedded in every map and
    reduce call for jobs of this kind (worker.build_prompt / build_reduce_prompt
    via `{instruction}`). A guidance file therefore goes through the SAME
    extract.extract() as a document upload: it sniffs magic bytes and REFUSES
    what it cannot read rather than degrading it (F38 -- a PDF decoded as UTF-8
    was silently summarised as binary and stored 'done'; a guidance file that
    cannot be read must fail the same way a document that cannot be read does).

    The combined text is then checked against worker.check_instruction_length
    BEFORE it can reach a job: guidance is repeated on every map call, so an
    oversized style guide must be refused with the limit named, not silently
    truncated. Raises HTTPException(400) naming the workflow on either failure.
    """
    typed = (form.get(f"instruction_{kind}") or "").strip()
    upload = form.get(f"guidance_file_{kind}")
    file_text = ""
    if upload is not None and getattr(upload, "filename", None):
        raw = await upload.read()
        try:
            file_text = extract.extract(raw, upload.filename).strip()
        except extract.ExtractionError as exc:
            raise HTTPException(400, f"guidance file for '{kind}': {exc}")

    combined = "\n\n".join(part for part in (typed, file_text) if part) or None
    if combined:
        try:
            worker.check_instruction_length(combined)
        except worker.GuidanceTooLong as exc:
            raise HTTPException(400, f"guidance for '{kind}': {exc}")
    return combined


@app.post("/jobs")
async def submit(request: Request,
                 kind: str = Form(...),
                 document: str = Form(""),
                 upload: UploadFile | None = File(None)):
    if kind not in worker.PROMPTS:
        raise HTTPException(400, f"unknown kind: {kind}")

    text = document
    if upload is not None and upload.filename:
        raw = await upload.read()
        # PDFs, docx and images all used to arrive here and be decoded as UTF-8
        # with errors="replace", producing mojibake that was then summarised and
        # stored as a successful job. See missing_link/extract.py.
        try:
            text = extract.extract(raw, upload.filename)
        except extract.ExtractionError as exc:
            raise HTTPException(400, str(exc))

    text = text.strip()
    if not text:
        raise HTTPException(400, "no document supplied")

    # Only the guidance box matching the chosen `kind` applies -- see
    # _resolve_instruction and templates/index.html, which shows one box per
    # workflow the same way the batch review page does.
    form = await request.form()
    instruction = await _resolve_instruction(form, kind)

    job_id = db.create_job(DB_PATH, kind, text, instruction)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/batch")
async def batch_upload(files: list[UploadFile] = File(...)):
    """Stage a batch of documents for review, one row per file.

    Deliberately does NOT create jobs yet -- a job needs a workflow (`kind`)
    and the operator has not chosen one per file yet. Each file is extracted
    independently and one bad file (a scanned PDF, a JPEG) is recorded as
    'refused' with an actionable reason rather than failing the whole batch;
    see extract.py for why extraction refuses rather than guessing.
    """
    records = []
    for f in files:
        if not f.filename:
            continue
        raw = await f.read()
        try:
            text = extract.extract(raw, f.filename).strip()
            if not text:
                raise extract.ExtractionError("no text after extraction")
            records.append({
                "filename": f.filename, "text": text,
                "preview": text[:PREVIEW_CHARS], "status": "ready", "error": None,
            })
        except extract.ExtractionError as exc:
            records.append({
                "filename": f.filename, "text": "", "preview": "",
                "status": "refused", "error": str(exc),
            })

    if not records:
        raise HTTPException(400, "no files supplied")

    batch_id = db.create_batch(DB_PATH, records)
    return RedirectResponse(f"/batch/{batch_id}", status_code=303)


@app.get("/batch/{batch_id}", response_class=HTMLResponse)
def batch_view(request: Request, batch_id: str):
    docs = db.get_batch(DB_PATH, batch_id)
    if not docs:
        raise HTTPException(404, "no such batch")
    return TEMPLATES.TemplateResponse(
        request, "batch.html",
        {"batch_id": batch_id, "docs": docs, "kinds": sorted(worker.PROMPTS),
         "accept": extract.ACCEPT_ATTR})


@app.post("/batch/{batch_id}/confirm")
async def batch_confirm(request: Request, batch_id: str):
    """Turn ticked (document, workflow) pairs into real jobs.

    Field names are generated per-row by batch.html (`wf_<doc_id>`, one value
    per ticked workflow), so this reads the raw form rather than declaring
    fixed FastAPI parameters -- the set of documents is only known at request
    time. `instruction_<kind>` carries the optional per-workflow guidance from
    the boxes below the table, applied to every job of that kind in this batch.
    """
    docs = db.get_batch(DB_PATH, batch_id)
    if not docs:
        raise HTTPException(404, "no such batch")

    form = await request.form()
    instructions = {kind: await _resolve_instruction(form, kind)
                    for kind in worker.PROMPTS}

    created = []
    for doc in docs:
        if doc["status"] != "ready":
            continue
        for kind in form.getlist(f"wf_{doc['id']}"):
            if kind not in worker.PROMPTS:
                continue
            job_id = db.create_job(DB_PATH, kind, doc["text"], instructions[kind])
            created.append(job_id)

    if not created:
        raise HTTPException(400, "no workflow was ticked for any accepted document")

    return RedirectResponse("/", status_code=303)


# --- Benchmark corpus ---------------------------------------------------------
# Source documents held as INPUTS TO MEASUREMENT, never as work. Nothing on
# these routes creates a job, and nothing on them touches an inference
# endpoint: every figure shown is string analysis (missing_link/corpus.py).
# See the comment above CORPUS_TABLE_SCHEMA in db.py for the measurement this
# unblocks.

@app.get("/corpus", response_class=HTMLResponse)
def corpus_view(request: Request, genre: str | None = None):
    """The corpus, with the numbers that decide what each document can answer.

    `genre` filters, and is the same key `db.list_corpus_documents(genre=...)`
    takes, so what the operator sees on this page and what a benchmark sweep
    selects are the same partition of the same table.

    status=None: refusals are listed too. A refused upload that vanished
    silently would leave the operator believing a document is in the corpus
    when it is not -- which is the failure shape this project keeps hitting
    from the other direction (F38: a result that looks fine and is worthless).
    """
    rows = db.list_corpus_documents(DB_PATH, genre=genre, status=None)
    for r in rows:
        r["notes"] = corpus.usability_notes(r)
    genres = db.corpus_genres(DB_PATH)
    return TEMPLATES.TemplateResponse(
        request, "corpus.html",
        {"docs": rows, "genres": genres, "genre": genre, "active": "corpus",
         "suggestions": sorted(set(genres) | set(corpus.GENRE_SUGGESTIONS)),
         "chunk_tokens": worker.CHUNK_TOKENS,
         "min_chunks": corpus.MIN_CHUNKS_FOR_BOUNDARY,
         "accept": extract.ACCEPT_ATTR})


@app.post("/corpus")
async def corpus_upload(files: list[UploadFile] = File(...),
                        genre: str = Form(""),
                        note: str = Form("")):
    """Add documents to the corpus. Multi-file, one genre per submission.

    Each file is extracted with the SAME extraction a job upload uses --
    magic-byte AND content sniffing, refuse rather than degrade (F38, and its
    HTML/RTF extension). A refusal is stored as its own row with the reason,
    so it is visible and deletable and, above all, DOES NOT BLOCK THE REST OF
    THE BATCH: uploading twelve legislative texts of which one is a scanned
    PDF must leave eleven in the corpus.

    Uses extract_with_method() rather than plain extract() so the corpus row
    can record HOW the text was obtained ("plain"/"pdf"/"html") -- see
    db.add_corpus_document's docstring for why that provenance matters here
    specifically: an HTML document's marker_rate/numbers_per_1k_words are
    computed on markup-stripped text, and a reader comparing it against a
    plain-text document needs to be able to tell the two apart.

    Deliberately creates NO jobs and makes NO inference call. Profiling is
    string analysis; the whole point of a corpus is that holding a document
    costs nothing until a measurement chooses to use it.
    """
    named = [f for f in files if f.filename]
    if not named:
        raise HTTPException(400, "no files supplied")

    g = corpus.normalise_genre(genre)
    note = (note or "").strip() or None
    for f in named:
        raw = await f.read()
        digest = corpus.sha256_hex(raw)
        record = {"filename": f.filename, "genre": g, "note": note,
                  "sha256": digest, "n_bytes": len(raw), "text": ""}

        existing = db.find_corpus_by_sha256(DB_PATH, digest)
        if existing:
            # Not an error, but not silently ignored either: a duplicate that
            # quietly succeeded would be double-counted by every sweep that
            # enumerates the corpus.
            record.update(status="refused", error=(
                f"identical bytes are already held as {existing['id']} "
                f"({existing['filename']}, genre '{existing['genre']}', added "
                f"{existing['created_at'][:19].replace('T', ' ')}). Not stored twice."))
            db.add_corpus_document(DB_PATH, record)
            continue

        try:
            text, method = extract.extract_with_method(raw, f.filename)
            text = text.strip()
            if not text:
                raise extract.ExtractionError("no text after extraction")
        except extract.ExtractionError as exc:
            record.update(status="refused", error=str(exc))
            db.add_corpus_document(DB_PATH, record)
            continue

        record.update(status="ready", text=text, extraction_method=method,
                      text_sha256=corpus.sha256_hex(text),
                      **corpus.profile(text))
        db.add_corpus_document(DB_PATH, record)

    return RedirectResponse("/corpus", status_code=303)


@app.post("/corpus/{doc_id}/delete")
def corpus_delete(doc_id: str):
    """Remove one corpus document. 404 on a stale link rather than pretending."""
    if not db.delete_corpus_document(DB_PATH, doc_id):
        raise HTTPException(404, "no such corpus document")
    return RedirectResponse("/corpus", status_code=303)


@app.get("/corpus/{doc_id}/text", response_class=PlainTextResponse)
def corpus_text(doc_id: str):
    """The extracted text, as text/plain -- the curl-and-pipe half of the
    accessor pair, next to /api/corpus/{id}'s JSON. In-process code should call
    db.get_corpus_document() directly rather than going over HTTP."""
    doc = db.get_corpus_document(DB_PATH, doc_id)
    if doc is None:
        raise HTTPException(404, "no such corpus document")
    if doc["status"] != "ready":
        raise HTTPException(409, f"corpus document is {doc['status']}: {doc['error']}")
    return doc["text"]


@app.get("/api/corpus")
def api_corpus_list(genre: str | None = None, status: str | None = "ready"):
    """Enumerate the corpus as JSON, metadata only (documents are megabytes).

    `status` defaults to 'ready' so a harness that just iterates this can never
    pick up a refused row with no text; pass status= (empty) for everything,
    which is what the HTML page shows.
    """
    return db.list_corpus_documents(DB_PATH, genre=genre, status=status or None)


@app.get("/api/corpus/{doc_id}")
def api_corpus_get(doc_id: str):
    """One corpus document, INCLUDING its text."""
    doc = db.get_corpus_document(DB_PATH, doc_id)
    if doc is None:
        raise HTTPException(404, "no such corpus document")
    return doc


@app.get("/api/jobs")
def api_list():
    # Documents can be megabytes; the list view never needs them.
    return [{k: v for k, v in j.items() if k != "document"}
            for j in db.list_jobs(DB_PATH)]


@app.get("/api/jobs/{job_id}")
def api_get(job_id: str):
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


def _rate_note():
    """Seconds-per-chunk for this cluster, and whether it is measured or guessed.

    Surfaced with its BASIS attached. An estimate presented as a measurement is
    the exact error this repo keeps catching (F1, F17, F28), so the UI says which
    it is rather than printing a bare number.
    """
    spc, n = db.seconds_per_chunk(DB_PATH)
    if spc is None:
        return {"seconds_per_chunk": worker.FALLBACK_SECONDS_PER_CHUNK,
                "basis": "estimate", "samples": n}
    return {"seconds_per_chunk": spc, "basis": "measured", "samples": n}


def _estimate_for(job):
    """Estimated wall clock for a job, calibrated from completed jobs if possible."""
    spc, _n = db.seconds_per_chunk(DB_PATH)
    secs, basis = worker.estimate_seconds(job["document"], spc)
    return {"seconds": secs, "human": worker.humanise_seconds(secs), "basis": basis}


def _progress_payload(job, chunks_done=None):
    """Everything the job page's live-progress poll needs, from local reads
    only. See the /jobs/{id}/progress route docstring for why "local reads
    only" is load-bearing, not just an implementation detail.

    `chunks_done`, if given, skips the chunk_summaries query -- job_view
    already has the same rows (as `sections`) and would otherwise query them
    twice per request for no reason.
    """
    document = job.get("document") or ""
    n_chunks = worker.count_chunks(document) if document else 0
    if chunks_done is None:
        chunks_done = len(db.get_chunk_summaries(DB_PATH, job["id"]))
    reduce_pending = (job["status"] == "running"
                      and n_chunks > 1 and chunks_done >= n_chunks)

    elapsed_s = None
    if job.get("started_at"):
        end_raw = job.get("finished_at") or datetime.now(timezone.utc).isoformat()
        try:
            elapsed_s = (datetime.fromisoformat(end_raw)
                        - datetime.fromisoformat(job["started_at"])).total_seconds()
        except ValueError:
            elapsed_s = None  # malformed timestamp must not break the poll

    # THREE-TIER rate, best available first, each labelled so the UI never
    # shows a number without saying how sure it is (the operator's own
    # standing complaint about this project: an inferred figure must look
    # inferred). Preference order:
    #   1. "job"      -- THIS job's own chunk timings, once there are enough
    #                    of them (worker.MIN_JOB_TIMED_CHUNKS) to be more than
    #                    one chunk's noise. Best predictor of what THIS job
    #                    will do next -- it already reflects this document's
    #                    actual chunk sizes and whatever this run's endpoint
    #                    is currently doing.
    #   2. "measured" -- the cluster-wide average (db.seconds_per_chunk),
    #                    unchanged from before this job existed.
    #   3. "estimate" -- the benchmark fallback constant, when neither of the
    #                    above has enough samples yet.
    job_timings = db.get_chunk_timings(DB_PATH, job["id"])
    job_stats = worker.chunk_rate_stats(job_timings)
    cluster_spc, _cluster_n = db.seconds_per_chunk(DB_PATH)

    if job_stats and job_stats["n_timed"] >= worker.MIN_JOB_TIMED_CHUNKS \
            and job_stats["seconds_per_chunk"]:
        rate_spc, rate_basis = job_stats["seconds_per_chunk"], "job"
    elif cluster_spc is not None:
        rate_spc, rate_basis = cluster_spc, "measured"
    else:
        rate_spc, rate_basis = None, "estimate"

    eta_seconds = eta_basis = eta_human = None
    if job["status"] == "running":
        eta_seconds, eta_basis = worker.remaining_seconds(
            n_chunks, chunks_done, reduce_pending, rate_spc, rate_basis)
        eta_human = worker.humanise_seconds(eta_seconds)

    return {
        "status": job["status"],
        "chunks_done": chunks_done,
        "chunks_expected": n_chunks,
        "elapsed_s": elapsed_s,
        "eta_seconds": eta_seconds,
        "eta_human": eta_human,
        "eta_basis": eta_basis,
        # Prefill and generation tok/s, ALWAYS SEPARATE, never blended into
        # one number -- see worker.chunk_rate_stats for why a blended figure
        # would be meaningless on this hardware. Both None until this job has
        # a real client.complete() timing to show (not before the first
        # non-resumed chunk lands).
        "tok_s": job_stats,
        # Which endpoint (LLAMA_URLS entry) is/was running this job -- see the
        # `endpoint` column and worker.run_one's set_job_endpoint call. Under
        # fan-out this is the difference between "a node failed" and "it got
        # slower" as the operator's only symptom.
        "endpoint": job.get("endpoint"),
        "error": job.get("error"),
        "cancel_requested": bool(job.get("cancel_requested")),
        # RETRY STATE. A retried job must be visibly a retried job: "attempt 3
        # of 4, resumed from 22 of 26 chunks" is the difference between an
        # operator seeing a queue that healed itself overnight and one seeing a
        # job that mysteriously took three times as long. `attempts` counts
        # STARTS (db.claim_next_pending), so it is 0 for a job that has never
        # been claimed and 1 during an ordinary first run.
        "attempts": int(job.get("attempts") or 0),
        "max_attempts": worker.MAX_ATTEMPTS,
        # Set only while a job is waiting out its backoff; also the flag that
        # says "this pending job is a retry, not a fresh submission".
        "retry_after": job.get("retry_after"),
        # How many chunk summaries THIS attempt reused rather than recomputed.
        # Deliberately not inferred from chunks_done: the resume is rejected
        # (and the rows deleted) when the model or the instruction changed, so
        # only the worker knows, and it records it. See db.record_resume.
        "resumed_chunks": int(job.get("resumed_chunks") or 0),
    }


def _revive_preview(job, sections):
    """What db.revive_job's resume would actually reuse, for the job page to
    tell the operator BEFORE they click Retry -- see templates/job.html. None
    for anything but a terminal (failed/cancelled) job, or one with no
    persisted chunks -- there is nothing to preview in either case.

    Reads db.get_recorded_model / get_recorded_instruction -- the SAME two
    calls worker.run_one itself makes when deciding whether a resume is safe
    -- rather than reimplementing that judgement here, so this preview and
    the actual resume can never silently disagree.

    What this CANNOT tell the operator, deliberately: which model is serving
    RIGHT NOW. This project has a standing rule against a status page probing
    the inference server (see job_progress's docstring and F39 -- /health,
    /slots and /metrics all queue behind token generation on this server, so
    a "cheap" liveness check can itself hang for as long as the job it is
    reporting on). /props is not known to share that queue, but the rule is
    "a status-rendering page does not call the backend", not "unless the
    specific route seems safe today" -- and the model actually serving at the
    NEXT claim may not even be the one serving now, once fan-out means R
    endpoints. So this states what is RECORDED and the rule that decides
    resume-vs-restart, not a yes/no prediction of which one will happen.
    """
    if job["status"] not in ("failed", "cancelled") or not sections:
        return None
    recorded_model = db.get_recorded_model(DB_PATH, job["id"])
    instr_ok, recorded_instruction = db.get_recorded_instruction(DB_PATH, job["id"])
    return {
        "n_chunks": len(sections),
        "model": recorded_model,
        "instruction_matches": instr_ok and recorded_instruction == job.get("instruction"),
    }


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_view(request: Request, job_id: str):
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    # Viewing a finished job's page IS the acknowledgement -- the simplest
    # "seen" interaction available without JS. Terminal-but-unread is exactly
    # what "unseen" means; see db.count_unseen / the index banner.
    if job["status"] in ("done", "failed", "cancelled") and job["seen_at"] is None:
        db.mark_seen(DB_PATH, job_id)
        job = db.get_job(DB_PATH, job_id)
    sections = db.get_chunk_summaries(DB_PATH, job_id)
    # Section-level citations (Tier B, docs/citation-research.md). Resolved HERE,
    # at render time, rather than stored: it is a regex over the result plus a
    # dict lookup on offsets that are already persisted, so there is nothing to
    # migrate, nothing to keep in sync, and jobs that finished before this
    # existed render correctly as "no citations" instead of needing a backfill.
    #
    # Only for a multi-chunk job. A single-chunk document never runs a reduce
    # step (summarise_traced returns the one map output directly), so it was
    # never asked for markers and their absence says nothing about the model --
    # a distinction the page has to make, or every short document looks like the
    # model ignored the instruction.
    citations = None
    if job["status"] == "done" and job["result"] and len(sections) > 1:
        citations = worker.parse_section_citations(job["result"], sections)
    # Full per-attempt failure history (see db.job_failures). Fetched for
    # every status, not just 'done' -- a 'failed' job's page already shows
    # job.error (the last attempt), but the history behind it is useful there
    # too ("failed on the same endpoint three times" vs "three different
    # ones"), and it is cheap: empty for the common case of a job that never
    # failed an attempt.
    failures = db.get_job_failures(DB_PATH, job_id)
    return TEMPLATES.TemplateResponse(
        request, "job.html",
        {"job": job,
         "estimate": _estimate_for(job) if job["status"] == "pending" else None,
         "sections": sections,
         "citations": citations,
         "failures": failures,
         # Rendered server-side so the page is fully correct on first load and
         # on every 15s no-JS refresh; the JS below only makes updates land
         # sooner than that, it is never the only source of this data.
         "progress": _progress_payload(job, chunks_done=len(sections)),
         "revive_preview": _revive_preview(job, sections)})


@app.get("/jobs/{job_id}/progress")
def job_progress(job_id: str):
    """Cheap JSON for the job page's live-progress poll.

    SQLITE READS ONLY -- this must NEVER call llama-server. Probing the
    inference server from a status page is the exact mistake that just cost
    this project a 97,299-character job: /health on this build queues behind
    token generation (it posts a METRICS task onto the same queue
    update_slots() drains), so a "cheap" liveness probe can itself hang for as
    long as the job it is trying to report on, under load, with idle slots.
    Everything below comes from the jobs row, the persisted chunk_summaries
    count, and the self-calibrating rate in db.seconds_per_chunk -- no request
    from this handler ever leaves the process.

    Poll no faster than every 2-3 seconds (see templates/job.html) -- this is
    plain `setInterval` + `fetch`, not a persistent connection, so a closed
    browser tab costs nothing and nothing here can wedge.
    """
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return _progress_payload(job)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Cancel a pending job, or request a cooperative stop of a running one.

    Same route for both -- db.request_cancel decides which applies from the
    job's current status. See worker.JobCancelled for exactly how far a stop
    on a RUNNING job goes (it cannot preempt the chunk currently in flight).
    """
    result = db.request_cancel(DB_PATH, job_id)
    if result is None:
        raise HTTPException(404, "no such job")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/revive")
def revive_job(job_id: str):
    """Explicit operator override: return a FAILED or CANCELLED job to
    'pending' with a fresh attempt budget, reusing whatever chunk summaries
    db.revive_job left on disk (worker.run_one's ordinary resume check decides
    how many of those actually get reused once the job is claimed -- this
    route only performs the status transition, it does not predict the
    outcome). See templates/job.html for what the operator is told about that
    BEFORE they click, and db.revive_job's docstring for the full reasoning,
    including why a cancelled job is revivable and a done one is not.

    409, not a silent no-op, for a job that is not in a revivable state --
    reachable if the job page is stale (e.g. an auto-retry claimed the job
    between page load and this click) or if this is ever hit directly.
    """
    result = db.revive_job(DB_PATH, job_id)
    if result is None:
        raise HTTPException(404, "no such job")
    if result != "revived":
        raise HTTPException(409, f"job is {result}, not revivable")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/reorder")
async def reorder_jobs(request: Request):
    """Save a new queue order for pending jobs. Takes effect on the NEXT claim
    (see claim_next_pending) -- a job already running finishes or is stopped
    separately, it is not interrupted by a reorder.

    Reads raw form data rather than typed FastAPI params because the field
    names are dynamic (one `priority_<job id>` input per pending row in
    templates/index.html) -- there is no fixed set of parameters to declare.
    """
    form = await request.form()
    pending = [j for j in db.list_jobs(DB_PATH) if j["status"] == "pending"]

    def sort_key(j):
        # A missing or unparsable field must not crash the page -- fall back
        # to the job's existing priority so a partial submission still
        # produces a sensible (if partially unreordered) result.
        raw = form.get(f"priority_{j['id']}")
        try:
            rank = float(raw)
        except (TypeError, ValueError):
            rank = float(j["priority"])
        return (rank, j["created_at"], j["id"])

    ordered_ids = [j["id"] for j in sorted(pending, key=sort_key)]
    db.reorder_pending(DB_PATH, ordered_ids)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/ack")
def acknowledge_all():
    """Dismiss the 'N jobs finished' banner on the index page."""
    db.mark_all_seen(DB_PATH)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}/result", response_class=PlainTextResponse)
def job_result(job_id: str):
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job["status"] != "done":
        raise HTTPException(409, f"job is {job['status']}")
    return job["result"]


@app.get("/jobs/{job_id}/text", response_class=HTMLResponse)
def job_text(request: Request, job_id: str):
    """The result in a plain <textarea>, for one-click select-all-and-copy.

    Distinct from /jobs/{id}/result (a bare text/plain body, useful for curl or
    piping) and from job.html's rendered <pre> (useful for reading). This page
    exists purely so a human can click into a box, Ctrl+A, Ctrl+C, and paste the
    summary into an email or case file without the browser chrome around a raw
    text/plain response getting in the way.
    """
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job["status"] != "done":
        raise HTTPException(409, f"job is {job['status']}")
    return TEMPLATES.TemplateResponse(request, "output.html", {"job": job})


@app.get("/health")
def health():
    jobs = db.list_jobs(DB_PATH)
    return {
        "ok": True,
        "llama_url": LLAMA_URLS[0],  # kept for backward compat with existing callers
        "endpoints": _endpoint_rows(),
        "counts": {s: sum(1 for j in jobs if j["status"] == s)
                   for s in ("pending", "running", "done", "failed", "cancelled")},
    }


def _endpoint_rows():
    """ENDPOINT_STATE as a list, with a display label/class precomputed.

    Precomputed here rather than in the template to keep the templates in the
    project's existing minimal no-JS style -- plain values in, plain markup
    out, no template-side branching on tri-state reachability.
    """
    rows = []
    for url in LLAMA_URLS:
        st = ENDPOINT_STATE[url]
        if st["reachable"] is True:
            label, css = "reachable", "done"
        elif st["reachable"] is False:
            label, css = "unreachable", "failed"
        else:
            label, css = "unknown", "pending"  # never probed yet (e.g. just started)
        rows.append({"url": url, "label": label, "css": css, **st})
    return rows


def _backoff_seconds(streak):
    """Capped, growing backoff: `streak` failures in a row -> seconds to wait."""
    return min(WORKER_BACKOFF_CAP_S, WORKER_BACKOFF_BASE_S * streak)


def _probe_endpoint(client, state):
    """Update `state` from one reachability probe. Returns True if reachable.

    Split out from _worker_loop so the health-aware-routing decision is a
    plain function a test can call directly, without driving the whole async
    loop.
    """
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    try:
        client.assert_reachable()
    except worker.BackendUnavailable as exc:
        state["reachable"] = False
        state["last_error"] = str(exc)
        state["current_job"] = None
        return False
    state["reachable"] = True
    state["last_error"] = None
    return True


async def _worker_loop(base_url):
    """One background worker per inference endpoint (job-level fan-out).

    Job-level, not chunk-level: chunk-level fan-out only reduces the wall-clock
    of a SINGLE document (same total work, spread wider), while job-level fan-out
    is what multiplies aggregate throughput -- the ~1.8x measured on two nodes.
    See docs/DESIGN-NOTES.md section G. Chunk-level is deliberately out of scope
    here.

    Each worker owns one LlamaClient for its whole lifetime (not one per job),
    so the per-model reasoning-kwargs detection it caches after its first call
    is reused rather than re-probed every job.

    HEALTH-AWARE ROUTING: probe /health BEFORE claiming a job, not after.
    run_one() already has its own assert_reachable() call (F36) -- but that
    only protects a job already claimed, and still ends with that job marked
    FAILED. Without this pre-claim probe, a persistently dead endpoint would
    sit in a claim-then-immediately-fail loop, taking jobs away from endpoints
    that could actually run them. With it, a dead node just stops claiming --
    costing 1/R of throughput, never failing the queue -- and resumes the
    moment its health probe passes again.
    """
    client = worker.LlamaClient(base_url)
    state = ENDPOINT_STATE[base_url]
    unreachable_streak = 0
    consecutive_errors = 0

    def _on_claim(job):
        state["current_job"] = job["id"]

    # EVERY iteration is guarded. Without this, any exception escaping run_one
    # kills this asyncio task SILENTLY -- an unretrieved task exception prints
    # nothing -- and the queue then stops forever with the current job frozen in
    # 'running'. Observed 2026-08-17: a job sat 'running' for seven minutes with
    # the model server idle and not one line in the log. Now that there is one
    # of these per endpoint, losing this guard on just one endpoint would be
    # just as silent -- R-1 of them would keep working and mask it.
    #
    # run_one already turns per-job failures into failed jobs. This catches the
    # rest: a corrupt database, a disk-full write, a bug in the queue itself.
    while True:
        if not _probe_endpoint(client, state):
            unreachable_streak += 1
            await asyncio.sleep(_backoff_seconds(unreachable_streak))
            continue
        unreachable_streak = 0

        try:
            did_work = await asyncio.to_thread(
                worker.run_one, DB_PATH, base_url, client, _on_claim)
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 -- the loop must never die
            consecutive_errors += 1
            print(f"[worker {base_url}] iteration failed ({consecutive_errors}): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            state["current_job"] = None
            # Back off so a persistent fault does not spin the CPU, but never
            # give up: the whole point of a queue is that it keeps trying.
            await asyncio.sleep(_backoff_seconds(consecutive_errors))
            continue
        state["current_job"] = None
        if not did_work:
            await asyncio.sleep(WORKER_IDLE_POLL_S)

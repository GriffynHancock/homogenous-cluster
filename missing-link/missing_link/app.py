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
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from missing_link import db, extract, worker

DB_PATH = os.environ.get("MISSING_LINK_DB", "/opt/missing-link/jobs.sqlite")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")
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

    task = None
    if not os.environ.get("MISSING_LINK_NO_WORKER"):
        task = asyncio.create_task(_worker_loop())
    try:
        yield
    finally:
        if task is not None:
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
         "unseen": db.count_unseen(DB_PATH)},
    )


@app.post("/jobs")
async def submit(kind: str = Form(...),
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

    job_id = db.create_job(DB_PATH, kind, text)
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
        {"batch_id": batch_id, "docs": docs, "kinds": sorted(worker.PROMPTS)})


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
    instructions = {
        kind: (form.get(f"instruction_{kind}") or "").strip() or None
        for kind in worker.PROMPTS
    }

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
    return TEMPLATES.TemplateResponse(
        request, "job.html",
        {"job": job,
         "estimate": _estimate_for(job) if job["status"] in ("pending", "running") else None,
         "sections": db.get_chunk_summaries(DB_PATH, job_id)})


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
        "llama_url": LLAMA_URL,
        "counts": {s: sum(1 for j in jobs if j["status"] == s)
                   for s in ("pending", "running", "done", "failed", "cancelled")},
    }


async def _worker_loop():
    """Single background worker.

    Deliberately ONE worker: the cluster serves one request at a time until the
    MoE batching question is measured (see STATUS.md). Running the blocking
    HTTP call in a thread keeps the event loop free to serve the status page,
    which matters because a job holds that thread for hours.
    """
    # EVERY iteration is guarded. Without this, any exception escaping run_one
    # kills this asyncio task SILENTLY -- an unretrieved task exception prints
    # nothing -- and the queue then stops forever with the current job frozen in
    # 'running'. Observed 2026-08-17: a job sat 'running' for seven minutes with
    # the model server idle and not one line in the log.
    #
    # run_one already turns per-job failures into failed jobs. This catches the
    # rest: a corrupt database, a disk-full write, a bug in the queue itself.
    consecutive_errors = 0
    while True:
        try:
            did_work = await asyncio.to_thread(worker.run_one, DB_PATH, LLAMA_URL)
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 -- the loop must never die
            consecutive_errors += 1
            print(f"[worker] iteration failed ({consecutive_errors}): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            # Back off so a persistent fault does not spin the CPU, but never
            # give up: the whole point of a queue is that it keeps trying.
            await asyncio.sleep(min(60, 5 * consecutive_errors))
            continue
        if not did_work:
            await asyncio.sleep(5)

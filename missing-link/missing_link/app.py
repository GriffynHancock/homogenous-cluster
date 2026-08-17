"""Missing Link web API and UI.

Deliberately minimal: no auth, no priorities, no distributed workers. It runs
on an isolated network by design (see the security posture in CLAUDE.md --
securing the network is the organisation's responsibility, and this app makes
no claim to help).

The point of this layer is that slowness stops being a defect. A chat window
makes a user wait; a job queue lets them submit and leave.
"""
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from missing_link import db, worker

DB_PATH = os.environ.get("MISSING_LINK_DB", "/opt/missing-link/jobs.sqlite")
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
         "rate": _rate_note(), "backlog": db.pending_chunk_backlog(DB_PATH)},
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
        # Documents come from scanners and Windows desktops; latin-1 and cp1252
        # are common. Never fail a multi-hour job on a stray byte.
        text = raw.decode("utf-8", errors="replace")

    text = text.strip()
    if not text:
        raise HTTPException(400, "no document supplied")

    job_id = db.create_job(DB_PATH, kind, text)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


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
    return TEMPLATES.TemplateResponse(
        request, "job.html",
        {"job": job, "estimate": _estimate_for(job) if job["status"] in ("pending", "running") else None})


@app.get("/jobs/{job_id}/result", response_class=PlainTextResponse)
def job_result(job_id: str):
    job = db.get_job(DB_PATH, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job["status"] != "done":
        raise HTTPException(409, f"job is {job['status']}")
    return job["result"]


@app.get("/health")
def health():
    jobs = db.list_jobs(DB_PATH)
    return {
        "ok": True,
        "llama_url": LLAMA_URL,
        "counts": {s: sum(1 for j in jobs if j["status"] == s)
                   for s in ("pending", "running", "done", "failed")},
    }


async def _worker_loop():
    """Single background worker.

    Deliberately ONE worker: the cluster serves one request at a time until the
    MoE batching question is measured (see STATUS.md). Running the blocking
    HTTP call in a thread keeps the event loop free to serve the status page,
    which matters because a job holds that thread for hours.
    """
    while True:
        did_work = await asyncio.to_thread(worker.run_one, DB_PATH, LLAMA_URL)
        if not did_work:
            await asyncio.sleep(5)

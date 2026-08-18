import os, tempfile, asyncio, contextlib, pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    # No background worker in tests: it would race with assertions and try to
    # reach a llama-server that is not running.
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c
    os.unlink(path)


def _reload_app(monkeypatch, llama_urls=None):
    """Import (or reload) app.py with a fresh temp db and no background worker.

    Same wiring as the `client` fixture above, but returned rather than yielded
    as a TestClient, and with the ability to set LLAMA_URLS first -- LLAMA_URLS
    is read once at module import time, so tests that need a particular
    endpoint list must set it BEFORE the reload, not after.

    Returns (app_mod, db_path); the caller must os.unlink(db_path) when done.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    if llama_urls is not None:
        monkeypatch.setenv("LLAMA_URLS", llama_urls)
    else:
        monkeypatch.delenv("LLAMA_URLS", raising=False)
    import importlib
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    return app_mod, path


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Missing Link" in r.text


def test_submit_creates_job_and_redirects(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                    follow_redirects=False)
    assert r.status_code == 303
    job_url = r.headers["location"]
    assert client.get(job_url).status_code == 200


def test_submit_rejects_unknown_kind(client):
    r = client.post("/jobs", data={"kind": "nope", "document": "x"})
    assert r.status_code == 400


def test_submit_rejects_empty_document(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "   "})
    assert r.status_code == 400


def test_api_list_omits_document_body(client):
    client.post("/jobs", data={"kind": "summarise", "document": "some text"},
                follow_redirects=False)
    rows = client.get("/api/jobs").json()
    assert len(rows) == 1
    assert "document" not in rows[0], "list view must not ship whole documents"


def test_api_get_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_result_409_while_pending(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "x"},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/jobs/{job_id}/result").status_code == 409


def test_upload_is_accepted(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": ""},
                    files={"upload": ("doc.txt", b"uploaded content", "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/api/jobs/{job_id}").json()["document"] == "uploaded content"


def test_upload_survives_bad_encoding(client):
    """Scanned/Windows documents carry non-UTF-8 bytes. A multi-hour job must
    not be lost to a stray byte."""
    r = client.post("/jobs", data={"kind": "summarise", "document": ""},
                    files={"upload": ("doc.txt", b"caf\xe9 na\xefve", "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 303


def test_health_reports_counts(client):
    client.post("/jobs", data={"kind": "summarise", "document": "x"},
                follow_redirects=False)
    h = client.get("/health").json()
    assert h["ok"] is True
    assert h["counts"]["pending"] == 1
    assert "cancelled" in h["counts"]


# --- navigation: every route reachable by clicking ---------------------------

def test_index_has_nav_links_to_every_top_level_page(client):
    r = client.get("/")
    assert 'href="/"' in r.text
    assert 'href="/health"' in r.text
    assert 'href="/api/jobs"' in r.text


def test_index_has_batch_upload_form(client):
    r = client.get("/")
    assert 'action="/batch"' in r.text
    assert "multiple" in r.text


# --- batch upload -------------------------------------------------------------

def test_batch_upload_accepts_good_and_refuses_bad_without_failing_the_batch(client):
    files = [
        ("files", ("doc1.txt", b"Plain text content, easily long enough to preview.",
                    "text/plain")),
        ("files", ("image.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")),
    ]
    r = client.post("/batch", files=files, follow_redirects=False)
    assert r.status_code == 303
    batch_url = r.headers["location"]

    review = client.get(batch_url)
    assert review.status_code == 200
    assert "doc1.txt" in review.text
    assert "image.jpg" in review.text
    # The refused file's reason must be legible on the page, not just a status.
    assert "not supported" in review.text or "JPEG" in review.text
    # The accepted file gets tick boxes; the refused one does not.
    assert 'type="checkbox"' in review.text


def test_batch_view_404_for_unknown_batch(client):
    assert client.get("/batch/nonexistent").status_code == 404


def test_batch_confirm_creates_a_job_per_ticked_workflow_with_instruction(client):
    files = [("files", ("doc.txt", b"Some document text to summarise later.",
                        "text/plain"))]
    r = client.post("/batch", files=files, follow_redirects=False)
    batch_id = r.headers["location"].rsplit("/", 1)[-1]

    doc_id = db_module_doc_id(client, batch_id)
    data = {f"wf_{doc_id}": ["summarise", "qa"],
            "instruction_summarise": "Focus on dates.",
            "instruction_qa": ""}
    r = client.post(f"/batch/{batch_id}/confirm", data=data, follow_redirects=False)
    assert r.status_code == 303

    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 2
    kinds = {j["kind"] for j in jobs}
    assert kinds == {"summarise", "qa"}
    summarise_job = next(j for j in jobs if j["kind"] == "summarise")
    full = client.get(f"/api/jobs/{summarise_job['id']}").json()
    assert full["instruction"] == "Focus on dates."
    qa_job = next(j for j in jobs if j["kind"] == "qa")
    full_qa = client.get(f"/api/jobs/{qa_job['id']}").json()
    assert full_qa["instruction"] is None, "blank instruction box must store as None"


def test_batch_confirm_404_for_unknown_batch(client):
    r = client.post("/batch/nonexistent/confirm", data={})
    assert r.status_code == 404


def test_batch_confirm_400_when_nothing_ticked(client):
    files = [("files", ("doc.txt", b"Some document text.", "text/plain"))]
    r = client.post("/batch", files=files, follow_redirects=False)
    batch_id = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/batch/{batch_id}/confirm", data={})
    assert r.status_code == 400


def db_module_doc_id(client, batch_id):
    """Pull the first document id out of the rendered review page's checkboxes."""
    import re
    review = client.get(f"/batch/{batch_id}")
    m = re.search(r'name="wf_([a-f0-9]+)"', review.text)
    assert m, "expected at least one ticked-workflow checkbox on the review page"
    return m.group(1)


# --- output page: raw text box ------------------------------------------------

def test_job_text_page_404_for_missing_job(client):
    assert client.get("/jobs/nonexistent/text").status_code == 404


def test_job_text_page_409_while_pending(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "x"},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/jobs/{job_id}/text").status_code == 409


def test_job_text_page_shows_result_in_a_textarea(client, monkeypatch):
    from missing_link import db as db_mod, worker

    r = client.post("/jobs", data={"kind": "summarise", "document": "x"},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]

    class FakeClient:
        def complete(self, prompt, max_tokens=512):
            return "the final summary text"

    import os
    assert worker.run_one(os.environ["MISSING_LINK_DB"], "http://x", FakeClient()) is True

    r = client.get(f"/jobs/{job_id}/text")
    assert r.status_code == 200
    assert "<textarea" in r.text
    assert "the final summary text" in r.text


def test_job_page_links_to_text_and_json_views_once_done(client):
    from missing_link import worker
    import os

    r = client.post("/jobs", data={"kind": "summarise", "document": "x"},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]

    class FakeClient:
        def complete(self, prompt, max_tokens=512):
            return "done"

    worker.run_one(os.environ["MISSING_LINK_DB"], "http://x", FakeClient())

    r = client.get(f"/jobs/{job_id}")
    assert f"/jobs/{job_id}/text" in r.text
    assert f"/api/jobs/{job_id}" in r.text


# --- queue control: cancel / stop / reorder (Part 2) --------------------------

def _submit(client, doc="x"):
    r = client.post("/jobs", data={"kind": "summarise", "document": doc},
                    follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[-1]


def test_cancel_pending_job_via_route(client):
    job_id = _submit(client)
    r = client.post(f"/jobs/{job_id}/cancel", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


def test_cancel_missing_job_404s(client):
    assert client.post("/jobs/nope/cancel").status_code == 404


def test_reorder_changes_which_pending_job_is_listed_where(client):
    from missing_link import db
    from missing_link.app import DB_PATH

    first = _submit(client, "first")
    second = _submit(client, "second")

    r = client.post("/jobs/reorder",
                    data={f"priority_{first}": "9", f"priority_{second}": "1"},
                    follow_redirects=False)
    assert r.status_code == 303

    assert db.get_job(DB_PATH, second)["priority"] < db.get_job(DB_PATH, first)["priority"]
    # And the new order is what the worker will actually claim next.
    assert db.claim_next_pending(DB_PATH)["id"] == second


def test_index_renders_reorder_form_only_with_multiple_pending(client):
    r = client.get("/")
    assert "Save order" not in r.text, "no reorder form with 0 pending jobs"
    _submit(client, "only one")
    r = client.get("/")
    assert "Save order" not in r.text, "no reorder form with exactly 1 pending job"
    _submit(client, "a second one")
    r = client.get("/")
    assert "Save order" in r.text


# --- completion notification marker (Part 3) -----------------------------------

def test_unseen_banner_appears_after_completion_and_clears_on_ack(client):
    from missing_link import db
    from missing_link.app import DB_PATH

    job_id = _submit(client)
    r = client.get("/")
    assert "finished since you last checked" not in r.text  # still pending

    db.claim_next_pending(DB_PATH)
    db.complete_job(DB_PATH, job_id, "the result", {})

    r = client.get("/")
    assert "finished since you last checked" in r.text
    assert "NEW" in r.text

    r = client.post("/jobs/ack", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/")
    assert "finished since you last checked" not in r.text


def test_viewing_a_finished_job_marks_it_seen(client):
    from missing_link import db
    from missing_link.app import DB_PATH

    job_id = _submit(client)
    db.claim_next_pending(DB_PATH)
    db.complete_job(DB_PATH, job_id, "the result", {})
    assert db.get_job(DB_PATH, job_id)["seen_at"] is None

    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert db.get_job(DB_PATH, job_id)["seen_at"] is not None


# --- fan out across R inference endpoints --------------------------------------

def test_llama_urls_falls_back_to_the_single_llama_url(monkeypatch):
    """No LLAMA_URLS set -> the pre-fan-out single-endpoint behaviour, exactly:
    one worker, one server."""
    monkeypatch.setenv("LLAMA_URL", "http://only-node:8080")
    app_mod, path = _reload_app(monkeypatch)
    try:
        assert app_mod.LLAMA_URLS == ["http://only-node:8080"]
    finally:
        os.unlink(path)


def test_llama_urls_parses_comma_separated_list(monkeypatch):
    app_mod, path = _reload_app(
        monkeypatch, llama_urls=" http://a:8080 ,http://b:8080,, ")
    try:
        # Whitespace stripped, blank entries (trailing comma) dropped.
        assert app_mod.LLAMA_URLS == ["http://a:8080", "http://b:8080"]
    finally:
        os.unlink(path)


def test_health_reports_every_configured_endpoint(monkeypatch):
    app_mod, path = _reload_app(
        monkeypatch, llama_urls="http://a:8080,http://b:8080")
    try:
        with TestClient(app_mod.app) as c:
            h = c.get("/health").json()
            assert [e["url"] for e in h["endpoints"]] == \
                ["http://a:8080", "http://b:8080"]
            # Never probed yet (no worker running in this test) -- must read as
            # unknown, not silently claim reachability it has not checked.
            assert all(e["reachable"] is None for e in h["endpoints"])
    finally:
        os.unlink(path)


def test_index_page_lists_every_configured_endpoint(monkeypatch):
    app_mod, path = _reload_app(
        monkeypatch, llama_urls="http://a:8080,http://b:8080")
    try:
        with TestClient(app_mod.app) as c:
            r = c.get("/")
            assert "http://a:8080" in r.text
            assert "http://b:8080" in r.text
    finally:
        os.unlink(path)


def test_backoff_grows_then_caps(monkeypatch):
    app_mod, path = _reload_app(monkeypatch)
    try:
        assert app_mod._backoff_seconds(1) == app_mod.WORKER_BACKOFF_BASE_S
        assert app_mod._backoff_seconds(2) == app_mod.WORKER_BACKOFF_BASE_S * 2
        # Must not grow without bound -- a long outage must not turn into an
        # ever-longer wait before the worker checks again.
        assert app_mod._backoff_seconds(10_000) == app_mod.WORKER_BACKOFF_CAP_S
    finally:
        os.unlink(path)


def test_probe_endpoint_marks_unreachable_and_clears_current_job(monkeypatch):
    app_mod, path = _reload_app(monkeypatch, llama_urls="http://x:8080")
    try:
        from missing_link import worker as worker_mod

        class DeadClient:
            def assert_reachable(self, timeout=20):
                raise worker_mod.BackendUnavailable("http://x:8080 did not answer")

        state = {"reachable": None, "current_job": "stale-job",
                  "last_checked": None, "last_error": None}
        ok = app_mod._probe_endpoint(DeadClient(), state)

        assert ok is False
        assert state["reachable"] is False
        # A dead endpoint cannot be working on anything -- leaving a stale job
        # id here would show it as still busy on something it will never finish.
        assert state["current_job"] is None
        assert "did not answer" in state["last_error"]
        assert state["last_checked"] is not None
    finally:
        os.unlink(path)


def test_probe_endpoint_marks_reachable_and_clears_stale_error(monkeypatch):
    app_mod, path = _reload_app(monkeypatch, llama_urls="http://x:8080")
    try:
        class HealthyClient:
            def assert_reachable(self, timeout=20):
                pass

        state = {"reachable": False, "current_job": None,
                  "last_checked": None, "last_error": "previous outage"}
        ok = app_mod._probe_endpoint(HealthyClient(), state)

        assert ok is True
        assert state["reachable"] is True
        assert state["last_error"] is None
    finally:
        os.unlink(path)


def test_worker_loop_backs_off_while_dead_and_resumes_without_failing_the_job(
        monkeypatch):
    """The end-to-end property fan-out promises for a dead node: it must not
    claim (and thereby fail) jobs while down, must not spin hot polling
    /health, and must resume claiming the moment it becomes reachable again --
    with the job it eventually picks up completing normally, never marked
    failed just because the endpoint was briefly unreachable at submit time.
    """
    app_mod, path = _reload_app(monkeypatch, llama_urls="http://dead-node:8080")
    try:
        from missing_link import worker as worker_mod
        url = "http://dead-node:8080"
        db_mod = app_mod.db
        db_mod.init_db(app_mod.DB_PATH)  # no TestClient here, so lifespan never ran
        job_id = db_mod.create_job(app_mod.DB_PATH, "summarise", "hello world")

        # Shrink the backoff so this test does not sleep for real seconds.
        monkeypatch.setattr(app_mod, "WORKER_BACKOFF_BASE_S", 0)
        monkeypatch.setattr(app_mod, "WORKER_BACKOFF_CAP_S", 0)

        probes = {"n": 0}

        class FlakyClient:
            """Unreachable for the first 3 probes, then healthy."""

            def __init__(self, base_url):
                self.base_url = base_url

            def assert_reachable(self, timeout=20):
                probes["n"] += 1
                if probes["n"] <= 3:
                    raise worker_mod.BackendUnavailable("down")

            def complete(self, prompt, max_tokens=512):
                return "a summary"

        monkeypatch.setattr(worker_mod, "LlamaClient", FlakyClient)

        async def drive():
            task = asyncio.create_task(app_mod._worker_loop(url))
            try:
                for _ in range(500):
                    if db_mod.get_job(app_mod.DB_PATH, job_id)["status"] == "done":
                        return
                    await asyncio.sleep(0.001)
                pytest.fail("job never completed after the endpoint recovered")
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(drive())

        job = db_mod.get_job(app_mod.DB_PATH, job_id)
        assert probes["n"] >= 4, "must keep retrying the health probe, not give up"
        assert job["status"] == "done", (
            "a job must be picked up once its endpoint recovers, not failed "
            "just because that endpoint was briefly unreachable")
        assert app_mod.ENDPOINT_STATE[url]["reachable"] is True
    finally:
        os.unlink(path)


# --- guidance: text box + file upload, per workflow ----------------------------

def test_submit_stores_typed_guidance_for_the_selected_kind(client):
    r = client.post("/jobs", data={
        "kind": "summarise", "document": "hello world",
        "instruction_summarise": "Focus on dates.",
        "instruction_report": "This box must be ignored.",
    }, follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["instruction"] == "Focus on dates."


def test_submit_with_blank_guidance_stores_none(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/api/jobs/{job_id}").json()["instruction"] is None


def test_submit_accepts_a_guidance_file_and_extracts_it(client):
    r = client.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                    files={"guidance_file_summarise":
                          ("style.txt", b"Keep it under 3 sentences.", "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert "Keep it under 3 sentences." in job["instruction"]


def test_submit_combines_typed_guidance_and_guidance_file(client):
    r = client.post("/jobs", data={
        "kind": "summarise", "document": "hello world",
        "instruction_summarise": "Focus on dates.",
    }, files={"guidance_file_summarise":
             ("style.txt", b"Use bullet points.", "text/plain")},
                    follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert "Focus on dates." in job["instruction"]
    assert "Use bullet points." in job["instruction"]


def test_submit_refuses_an_unreadable_guidance_file(client):
    """Same posture as a document upload (F38): a guidance file that cannot be
    read is refused with a reason, never silently degraded."""
    r = client.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                    files={"guidance_file_summarise":
                          ("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")})
    assert r.status_code == 400
    assert "guidance file" in r.text


def test_submit_refuses_oversized_guidance_naming_the_limit(client):
    from missing_link import worker
    too_long = "word " * (worker.MAX_INSTRUCTION_WORDS + 500)
    r = client.post("/jobs", data={
        "kind": "summarise", "document": "hello world",
        "instruction_summarise": too_long,
    })
    assert r.status_code == 400
    assert str(worker.MAX_INSTRUCTION_WORDS) in r.text


def test_batch_confirm_combines_typed_guidance_and_guidance_file(client):
    files = [("files", ("doc.txt", b"Some document text to summarise later.",
                        "text/plain"))]
    r = client.post("/batch", files=files, follow_redirects=False)
    batch_id = r.headers["location"].rsplit("/", 1)[-1]
    doc_id = db_module_doc_id(client, batch_id)

    r = client.post(
        f"/batch/{batch_id}/confirm",
        data={f"wf_{doc_id}": ["summarise"], "instruction_summarise": "Be terse."},
        files={"guidance_file_summarise":
              ("style.txt", b"Use headings.", "text/plain")},
        follow_redirects=False)
    assert r.status_code == 303

    jobs = client.get("/api/jobs").json()
    job = client.get(f"/api/jobs/{jobs[0]['id']}").json()
    assert "Be terse." in job["instruction"]
    assert "Use headings." in job["instruction"]


def test_batch_confirm_refuses_an_unreadable_guidance_file(client):
    files = [("files", ("doc.txt", b"Some document text.", "text/plain"))]
    r = client.post("/batch", files=files, follow_redirects=False)
    batch_id = r.headers["location"].rsplit("/", 1)[-1]
    doc_id = db_module_doc_id(client, batch_id)

    r = client.post(
        f"/batch/{batch_id}/confirm",
        data={f"wf_{doc_id}": ["summarise"]},
        files={"guidance_file_summarise":
              ("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")})
    assert r.status_code == 400


def test_index_and_batch_pages_show_a_guidance_box_per_workflow(client):
    r = client.get("/")
    for k in ("summarise", "report", "qa"):
        assert f'name="instruction_{k}"' in r.text
        assert f'name="guidance_file_{k}"' in r.text

    files = [("files", ("doc.txt", b"Some document text.", "text/plain"))]
    br = client.post("/batch", files=files, follow_redirects=False)
    batch_id = br.headers["location"].rsplit("/", 1)[-1]
    review = client.get(f"/batch/{batch_id}")
    for k in ("summarise", "report", "qa"):
        assert f'name="instruction_{k}"' in review.text
        assert f'name="guidance_file_{k}"' in review.text
    # File inputs need a multipart form, or the browser silently drops them.
    assert 'enctype="multipart/form-data"' in review.text


# --- live progress polling endpoint (Feature 1) ---------------------------------

def test_job_progress_404_for_missing_job(client):
    assert client.get("/jobs/nonexistent/progress").status_code == 404


def test_job_progress_reports_zero_of_n_for_a_fresh_pending_job(client):
    job_id = _submit(client, doc="word " * 20000)  # multi-chunk document
    r = client.get(f"/jobs/{job_id}/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["chunks_done"] == 0
    assert body["chunks_expected"] > 1
    assert body["elapsed_s"] is None
    assert body["endpoint"] is None


def test_job_progress_reflects_persisted_chunks_and_endpoint_mid_run(client):
    from missing_link import db

    job_id = _submit(client, doc="word " * 20000)
    from missing_link.app import DB_PATH
    job = db.claim_next_pending(DB_PATH)
    db.set_job_endpoint(DB_PATH, job_id, "http://node5:8080")
    db.save_chunk_summaries(
        DB_PATH, job_id,
        [{"index": 0, "start": 0, "end": 10, "summary": "s0"}],
        model="m")

    body = client.get(f"/jobs/{job_id}/progress").json()
    assert body["status"] == "running"
    assert body["chunks_done"] == 1
    assert body["chunks_expected"] > 1
    assert body["endpoint"] == "http://node5:8080"
    assert body["eta_seconds"] is not None
    assert body["eta_basis"] in ("measured", "estimate")


def test_job_progress_reports_tok_s_and_job_tier_eta_once_enough_chunks_are_timed(client):
    """The operator's own request: once THIS job has enough of its own timed
    chunks, the ETA must be calibrated on THIS job (basis 'job'), separate
    from the cluster-wide average -- and prefill/generation tok/s must be
    reported as two distinct numbers, never blended."""
    from missing_link import db, worker
    from missing_link.app import DB_PATH

    job_id = _submit(client, doc="word " * 20000)
    db.claim_next_pending(DB_PATH)
    for i in range(worker.MIN_JOB_TIMED_CHUNKS):
        db.save_chunk_summaries(
            DB_PATH, job_id,
            [{"index": i, "start": i * 10, "end": i * 10 + 10, "summary": f"s{i}",
              "prompt_n": 4096, "prompt_ms": 4000.0,
              "predicted_n": 200, "predicted_ms": 2000.0}],
            model="m")

    body = client.get(f"/jobs/{job_id}/progress").json()
    assert body["eta_basis"] == "job"
    assert body["tok_s"]["n_timed"] == worker.MIN_JOB_TIMED_CHUNKS
    assert body["tok_s"]["last_prefill_tok_s"] == pytest.approx(4096 / 4.0)
    assert body["tok_s"]["last_gen_tok_s"] == pytest.approx(200 / 2.0)
    assert body["tok_s"]["last_prefill_tok_s"] != body["tok_s"]["last_gen_tok_s"]


def test_job_progress_tok_s_is_none_before_any_chunk_is_timed(client):
    job_id = _submit(client, doc="word " * 20000)
    body = client.get(f"/jobs/{job_id}/progress").json()
    assert body["tok_s"] is None


def test_running_job_page_shows_chunk_progress_and_tok_s(client):
    from missing_link import db, worker
    from missing_link.app import DB_PATH

    job_id = _submit(client, doc="word " * 20000)
    db.claim_next_pending(DB_PATH)
    for i in range(worker.MIN_JOB_TIMED_CHUNKS):
        db.save_chunk_summaries(
            DB_PATH, job_id,
            [{"index": i, "start": i * 10, "end": i * 10 + 10, "summary": f"s{i}",
              "prompt_n": 4096, "prompt_ms": 4000.0,
              "predicted_n": 200, "predicted_ms": 2000.0}],
            model="m")

    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert "Prefill" in r.text and "Generation" in r.text
    assert "measured from this job's own chunks" in r.text


def test_job_progress_endpoint_never_touches_llama_server(client, monkeypatch):
    """The polling endpoint must read ONLY sqlite. Force any outbound HTTP
    call to explode, so this test fails loudly if that constraint is ever
    broken by a future edit."""
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("job_progress must never make an HTTP request")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    job_id = _submit(client, doc="word " * 20000)
    r = client.get(f"/jobs/{job_id}/progress")
    assert r.status_code == 200


def test_running_job_page_renders_live_progress_box(client):
    from missing_link import db
    from missing_link.app import DB_PATH

    job_id = _submit(client, doc="word " * 20000)
    db.claim_next_pending(DB_PATH)
    db.set_job_endpoint(DB_PATH, job_id, "http://node6:8080")

    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert 'id="live-progress"' in r.text
    assert "http://node6:8080" in r.text
    assert f"/jobs/{job_id}/progress" in r.text  # reachable by clicking


def test_failed_job_page_shows_error_prominently_with_endpoint(client):
    from missing_link import db
    from missing_link.app import DB_PATH

    job_id = _submit(client)
    db.claim_next_pending(DB_PATH)
    db.set_job_endpoint(DB_PATH, job_id, "http://node7:8080")
    db.fail_job(DB_PATH, job_id, "BackendUnavailable: did not answer /health")

    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert "This job failed" in r.text
    assert "http://node7:8080" in r.text
    assert "BackendUnavailable" in r.text

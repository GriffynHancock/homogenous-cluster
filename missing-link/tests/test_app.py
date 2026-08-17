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

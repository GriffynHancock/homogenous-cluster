import os, tempfile, pytest
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

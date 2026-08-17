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

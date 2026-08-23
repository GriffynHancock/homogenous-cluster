"""The shared-credential gate (F54).

WHAT THESE TESTS ARE ACTUALLY FOR. F54 found this service bound to
`0.0.0.0:8000` with no security scheme at all, on a LAN that is about to carry a
class of students, exposing `POST /corpus/{doc_id}/delete` against the corpus
F52 re-profiled. So the assertion that matters is NOT "the middleware returns
401". It is **that the destructive thing did not happen** -- the document is
still in the corpus, the job is still pending -- because a 401 in front of a
route that had already run would look identical from the outside.

That distinction is this project's own lesson (F34: 41 tests passed against a
pipeline that had never processed a document). Every route test below therefore
checks the STATE after the refusal, through a separate authenticated read, not
just the status code.

The second thing these tests hold is the fail-open default. `auth.py` argues for
it; a default that is only argued for and never asserted is one refactor away
from silently becoming fail-closed and locking the operator out of a running
queue mid-job.
"""
import base64
import importlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from missing_link import auth


TOKEN = "s3cret-token-for-tests"


def _basic(user, password):
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


@pytest.fixture
def gated(monkeypatch):
    """A TestClient for an app reloaded WITH the gate on.

    The final reload is not tidiness: `missing_link.app` keeps AUTH_TOKEN and
    its middleware stack as module state, so without it every later test in the
    session would run against an app object that still demands a credential its
    fixtures know nothing about.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    monkeypatch.setenv("ML_AUTH_TOKEN", TOKEN)
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    try:
        with TestClient(app_mod.app) as c:
            yield c
    finally:
        monkeypatch.delenv("ML_AUTH_TOKEN", raising=False)
        importlib.reload(app_mod)
        os.unlink(path)


@pytest.fixture
def ungated(monkeypatch):
    """A TestClient for an app reloaded with NO credential in the environment."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setenv("MISSING_LINK_DB", path)
    monkeypatch.setenv("MISSING_LINK_NO_WORKER", "1")
    monkeypatch.delenv("ML_AUTH_TOKEN", raising=False)
    from missing_link import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c
    os.unlink(path)


def _auth_headers():
    return {"Authorization": "Bearer " + TOKEN}


def _add_doc(client):
    """Put one real document in the corpus, authenticated, and return its id.

    Goes through POST /corpus rather than db.add_corpus_document so the upload
    path itself is exercised through the middleware -- a gate that quietly broke
    multipart bodies would pass every header-only test.
    """
    r = client.post("/corpus", data={"genre": "legislative"},
                    files=[("files", ("clause.txt", b"A clause. " * 200, "text/plain"))],
                    headers=_auth_headers(), follow_redirects=False)
    assert r.status_code == 303, r.text
    listing = client.get("/api/corpus", headers=_auth_headers())
    assert listing.status_code == 200
    docs = listing.json()
    assert len(docs) == 1, docs
    return docs[0]["id"]


# --- load_token -------------------------------------------------------------

def test_absent_variable_means_no_gate():
    assert auth.load_token({}) is None


def test_empty_and_whitespace_are_unset_not_a_token():
    # `ML_AUTH_TOKEN=` in an EnvironmentFile yields "", not an absent variable.
    # If that counted as a token, the gate would be on with a secret of "".
    assert auth.load_token({auth.ENV_VAR: ""}) is None
    assert auth.load_token({auth.ENV_VAR: "   \n"}) is None


def test_token_is_stripped():
    assert auth.load_token({auth.ENV_VAR: " abc \n"}) == "abc"


# --- credential_ok ----------------------------------------------------------

def test_bearer_accepted_and_scheme_is_case_insensitive():
    assert auth.credential_ok("Bearer " + TOKEN, TOKEN)
    assert auth.credential_ok("bearer " + TOKEN, TOKEN)
    assert auth.credential_ok("BEARER " + TOKEN, TOKEN)


def test_basic_accepted_as_either_half():
    assert auth.credential_ok(_basic("anything", TOKEN), TOKEN)
    assert auth.credential_ok(_basic(TOKEN, ""), TOKEN)


def test_wrong_secret_is_refused_in_both_schemes():
    assert not auth.credential_ok("Bearer nope", TOKEN)
    assert not auth.credential_ok(_basic("ml", "nope"), TOKEN)


def test_a_near_miss_is_refused():
    # Guards against any future switch to a prefix/startswith comparison.
    assert not auth.credential_ok("Bearer " + TOKEN[:-1], TOKEN)
    assert not auth.credential_ok("Bearer " + TOKEN + "x", TOKEN)


def test_malformed_credentials_are_refused_and_never_raise():
    # This parses unauthenticated bytes off the LAN. An exception here would
    # turn a bad request into a server fault, which is a worse hole than the
    # one being closed.
    for header in ("", "Basic", "Basic !!!not-base64!!!", "Basic " + TOKEN,
                   "Digest abc", "Bearer", "Bearer ", TOKEN,
                   "Basic " + base64.b64encode(b"\xff\xfe").decode("ascii")):
        assert not auth.credential_ok(header, TOKEN), header


def test_no_token_configured_means_no_header_is_ever_accepted():
    # The middleware short-circuits before this is reached when the gate is
    # off; credential_ok itself must not treat "no configured secret" as
    # "everything matches".
    assert not auth.credential_ok("Bearer anything", None)
    assert not auth.credential_ok("Bearer ", "")


# --- the open path ----------------------------------------------------------

def test_health_stays_open_and_reports_that_the_gate_is_on(gated):
    r = gated.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["auth"] is True


def test_health_reports_an_absent_gate_so_a_monitor_can_see_the_lock_fell_off(ungated):
    r = ungated.get("/health")
    assert r.status_code == 200
    assert r.json()["auth"] is False


def test_health_leaks_no_document_or_job_text(gated):
    _add_doc(gated)
    body = gated.get("/health").text
    assert "clause.txt" not in body
    assert "A clause." not in body


def test_open_paths_is_exactly_health(gated):
    # A route added to OPEN_PATHS is a hole by definition, so widening it should
    # require editing a test that says so out loud.
    assert auth.OPEN_PATHS == frozenset({"/health"})


# --- refusals, asserted on STATE not just status ----------------------------

def test_the_root_page_needs_a_credential_and_offers_a_browser_challenge(gated):
    r = gated.get("/")
    assert r.status_code == 401
    # Without this header a browser shows a bare 401 and gives the operator no
    # way to supply the credential at all -- the UI would simply be unreachable.
    assert r.headers["www-authenticate"].startswith('Basic realm="Missing Link"')


def test_corpus_delete_without_a_credential_does_not_delete(gated):
    doc_id = _add_doc(gated)

    r = gated.post(f"/corpus/{doc_id}/delete", follow_redirects=False)
    assert r.status_code == 401

    # The assertion that F54 is actually about.
    still = gated.get(f"/api/corpus/{doc_id}", headers=_auth_headers())
    assert still.status_code == 200
    assert still.json()["id"] == doc_id


def test_job_cancel_without_a_credential_does_not_cancel(gated):
    r = gated.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                   headers=_auth_headers(), follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]

    assert gated.post(f"/jobs/{job_id}/cancel", follow_redirects=False).status_code == 401

    job = gated.get(f"/api/jobs/{job_id}", headers=_auth_headers()).json()
    assert job["status"] == "pending"


def test_job_reorder_without_a_credential_does_not_reorder(gated):
    ids = []
    for text in ("first document", "second document"):
        r = gated.post("/jobs", data={"kind": "summarise", "document": text},
                       headers=_auth_headers(), follow_redirects=False)
        ids.append(r.headers["location"].rsplit("/", 1)[-1])

    before = [j["id"] for j in gated.get("/api/jobs", headers=_auth_headers()).json()]
    r = gated.post("/jobs/reorder",
                   data={f"priority_{ids[0]}": "99", f"priority_{ids[1]}": "1"},
                   follow_redirects=False)
    assert r.status_code == 401
    after = [j["id"] for j in gated.get("/api/jobs", headers=_auth_headers()).json()]
    assert after == before


def test_corpus_upload_without_a_credential_stores_nothing(gated):
    r = gated.post("/corpus", data={"genre": "legislative"},
                   files=[("files", ("x.txt", b"words " * 500, "text/plain"))],
                   follow_redirects=False)
    assert r.status_code == 401
    assert gated.get("/api/corpus", headers=_auth_headers()).json() == []


def test_every_mutating_route_refuses_an_anonymous_request(gated):
    """Enumerated from GET /openapi.json, which is authoritative for this app.

    A route missing from this list is a route nobody checked. The list is
    asserted against the live OpenAPI document below so it cannot silently rot.
    """
    for method, path in MUTATING_ROUTES:
        r = gated.request(method, path, follow_redirects=False)
        assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


def test_reading_a_document_or_a_result_needs_a_credential(gated):
    doc_id = _add_doc(gated)
    # These serve the corpus text itself. Leaving them open would mean the LAN
    # could read every document without being able to delete one, which is not
    # a coherent place to stop.
    for path in (f"/corpus/{doc_id}/text", "/api/corpus", "/api/jobs", "/corpus"):
        assert gated.get(path).status_code == 401, path


def test_a_wrong_credential_is_refused_like_none_at_all(gated):
    assert gated.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert gated.get("/", headers={"Authorization": _basic("ml", "wrong")}).status_code == 401


# --- acceptance -------------------------------------------------------------

def test_a_browser_style_basic_credential_drives_the_whole_ui(gated):
    """One Basic header, every kind of request the UI makes.

    This is the argument for Basic over Bearer, asserted: the browser attaches
    the same header to the page load, the multipart upload, the form POST and
    the same-origin fetch, so no template needed changing.
    """
    headers = {"Authorization": _basic("ml", TOKEN)}

    assert gated.get("/", headers=headers).status_code == 200

    up = gated.post("/corpus", data={"genre": "legislative"},
                    files=[("files", ("a.txt", b"A clause. " * 200, "text/plain"))],
                    headers=headers, follow_redirects=False)
    assert up.status_code == 303

    sub = gated.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                     headers=headers, follow_redirects=False)
    assert sub.status_code == 303
    job_id = sub.headers["location"].rsplit("/", 1)[-1]

    # The job page's live-progress poll -- the one same-origin fetch in the UI.
    assert gated.get(f"/jobs/{job_id}/progress", headers=headers).status_code == 200

    # And the mutation the whole change exists to stop, now permitted.
    doc_id = gated.get("/api/corpus", headers=headers).json()[0]["id"]
    dele = gated.post(f"/corpus/{doc_id}/delete", headers=headers, follow_redirects=False)
    assert dele.status_code == 303
    assert gated.get("/api/corpus", headers=headers).json() == []


def test_a_bearer_credential_works_for_scripts(gated):
    assert gated.get("/api/jobs", headers=_auth_headers()).status_code == 200


# --- fail-open, asserted rather than merely documented ----------------------

def test_with_no_token_configured_the_app_behaves_exactly_as_before(ungated):
    """Deploying this code without setting ML_AUTH_TOKEN must change nothing.

    The ordering risk this protects against is real and named in auth.py: a
    `git pull` plus `systemctl restart` that lands ahead of the environment file
    must not leave the operator staring at a 401 with a job running behind it.
    """
    assert ungated.get("/").status_code == 200
    assert ungated.get("/api/jobs").status_code == 200
    r = ungated.post("/jobs", data={"kind": "summarise", "document": "hello world"},
                     follow_redirects=False)
    assert r.status_code == 303


# --- the route inventory, checked against the app's own OpenAPI -------------

MUTATING_ROUTES = [
    ("POST", "/jobs"),
    ("POST", "/jobs/ack"),
    ("POST", "/jobs/reorder"),
    ("POST", "/jobs/no-such-job/cancel"),
    ("POST", "/jobs/no-such-job/revive"),
    ("POST", "/batch"),
    ("POST", "/batch/no-such-batch/confirm"),
    ("POST", "/corpus"),
    ("POST", "/corpus/no-such-doc/delete"),
]


def test_the_mutating_route_list_matches_the_apps_own_openapi(gated):
    """Guard against a new POST route being added and nobody testing it.

    Reads the live OpenAPI document rather than a hand-kept list -- the same
    reason CLAUDE.md says to read GET /openapi.json before calling this API.
    """
    spec = gated.get("/openapi.json", headers=_auth_headers()).json()
    live = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops
            if m.upper() not in ("GET", "HEAD", "OPTIONS")}
    # Compare templated forms, since MUTATING_ROUTES carries concrete ids.
    tested = {
        ("POST", "/jobs"), ("POST", "/jobs/ack"), ("POST", "/jobs/reorder"),
        ("POST", "/jobs/{job_id}/cancel"), ("POST", "/jobs/{job_id}/revive"),
        ("POST", "/batch"), ("POST", "/batch/{batch_id}/confirm"),
        ("POST", "/corpus"), ("POST", "/corpus/{doc_id}/delete"),
    }
    assert live == tested, f"untested mutating routes: {live ^ tested}"


def test_openapi_itself_is_not_readable_anonymously(gated):
    # It is the map of every route on the box. Free to close, so closed.
    assert gated.get("/openapi.json").status_code == 401

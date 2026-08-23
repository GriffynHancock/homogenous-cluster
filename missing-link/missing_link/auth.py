"""One shared credential in front of Missing Link. Nothing more than that.

WHY THIS EXISTS, AND WHAT IT DELIBERATELY IS NOT
------------------------------------------------
F54 found the live instance bound to `0.0.0.0:8000` with **no security scheme
at all** -- `POST /corpus/{doc_id}/delete`, `POST /jobs/reorder` and
`POST /jobs/{id}/cancel` reachable from any browser on the LAN. The same LAN is
about to carry a class of cybersecurity students, and the corpus they could
delete is the measuring instrument for this project's core work (F52).

The operator's scope for this change, verbatim: *"this is all running in a DMZ
so its not high security, just want to remove low hanging fruits"*, and *"we
will do a hardening run later"*.

So this module is a **door lock, not a security system**. There is no identity
here: no users, no roles, no sessions, no database, no password hashing, no
lockout, no audit trail. Everyone who can operate the queue shares one secret
read from the environment, exactly like `ML_PORT`, `MISSING_LINK_DB` and
`LLAMA_URLS` are. `CLAUDE.md` is explicit that this project does not write
security guidance and does not imply its tooling makes a network safe -- this
closes one specific hole and claims nothing else.

WHY HTTP BASIC, WHEN THE REST OF THE PROJECT WOULD REACH FOR A BEARER TOKEN
--------------------------------------------------------------------------
Because the primary client is a browser and the UI is server-rendered HTML
forms. Every mutating route in this app is reached by a plain
`<form method="post">` (see templates/index.html, corpus.html, job.html) plus
one same-origin `fetch` on the job page. A bearer token cannot be attached to
either without inventing a login page and a cookie -- i.e. the session system
this change is explicitly not allowed to build. A `WWW-Authenticate: Basic`
challenge makes the browser do it: it prompts once, then attaches the header to
every subsequent request to this origin, form POSTs and same-origin `fetch`
included, with no template change at all.

Both forms are accepted, because scripts should not have to base64 anything:

    curl -u ml:$ML_AUTH_TOKEN  http://host:8000/api/jobs      # Basic
    curl -H "Authorization: Bearer $ML_AUTH_TOKEN" ...        # Bearer

For Basic, the token may be either the username or the password; the other half
is ignored. There is no user list to check a username against, so pretending to
check one would be theatre.

WHAT IS LEFT OPEN, AND WHY
--------------------------
`/health` only. It is what `start-ui.sh` polls to decide the service came up,
and it is the natural probe for an out-of-band monitor. F39 is the standing
warning against a probe that shares fate with what it measures; adding a
credential to the probe path would add a *second* way for a healthy service to
look dead (a monitor whose token drifted out of sync reports an outage that is
not happening). `/health` returns queue counts and endpoint reachability -- no
document text, no job text -- so leaving it open leaks nothing that the LAN
could not learn by watching the network.

Everything else, INCLUDING the read-only routes, requires the credential. That
is not scope creep past "stop deletions": with Basic auth there is no partial
state worth having. The browser is prompted once for the whole origin, so
protecting only the POSTs would cost the same one prompt while still serving
`/corpus/{doc_id}/text` -- the full text of the documents -- to anyone who
asked. Given the project's own framing (legally sensitive documents), serving
the corpus to the LAN for free is not a defensible default when closing it is
free.

FAIL-OPEN WHEN UNSET, AND THE HONEST COST OF THAT
-------------------------------------------------
No `ML_AUTH_TOKEN` in the environment means no gate, exactly as before. That is
a deliberate trade and it has a real downside: if the line is ever lost from
`/etc/default/missing-link`, the service silently reverts to wide open.

It is chosen anyway because the alternative is worse in this specific
deployment. Fail-closed-on-unset means the moment this code is deployed ahead
of the environment file -- the ordinary order of a `git pull` plus a
`systemctl restart` -- the service rejects its own UI and the operator's first
symptom is a dead dashboard with a running job behind it. Reversibility is a
standing rule here (`CLAUDE.md`, "prefer reversible changes"): deleting one line
turns this off, adding one line turns it on.

The silent-revert risk is answered by making the state VISIBLE rather than by
making startup fail: the app logs a loud line at startup either way, and
`GET /health` reports `"auth": true|false`, so "is the lock actually on?" is a
question anything can answer at any time without guessing from behaviour.
"""
import base64
import binascii
import hmac
import os

ENV_VAR = "ML_AUTH_TOKEN"

#: Sent on every 401 so a browser puts up its own credential prompt. The realm
#: string is what the browser shows the user, so it names the service.
REALM = "Missing Link"

#: The only unauthenticated path. See the module docstring for why this one and
#: nothing else.
OPEN_PATHS = frozenset({"/health"})


def load_token(environ=None):
    """The shared secret, or None if the gate is off.

    Whitespace-only is treated as unset, because `ML_AUTH_TOKEN=` in an
    EnvironmentFile yields an empty string rather than an absent variable, and
    "the operator commented it out" and "the operator set it to nothing" should
    not mean different things.
    """
    raw = (environ if environ is not None else os.environ).get(ENV_VAR)
    if raw is None:
        return None
    token = raw.strip()
    return token or None


def _basic_matches(encoded, token):
    """True if a Basic credential carries `token` as either half.

    Malformed base64, or bytes that are not UTF-8, are a failed attempt and not
    an exception: this runs on unauthenticated input from the LAN, so anything
    that raises here would be a way to make the server the error rather than
    the request.
    """
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    # compare_digest on both halves unconditionally -- `or` would short-circuit
    # and leak which half matched through timing. Cheap here, and it keeps the
    # rule "constant-time compare" true of the whole function rather than of
    # one line in it.
    return hmac.compare_digest(username, token) | hmac.compare_digest(password, token)


def credential_ok(header_value, token):
    """True if an `Authorization` header value presents the shared secret.

    Accepts `Bearer <token>` and `Basic <base64>`; the scheme name is matched
    case-insensitively because RFC 7235 says it is case-insensitive and curl,
    browsers and requests do not all agree on capitalisation.
    """
    if not token or not header_value:
        return False
    scheme, _, rest = header_value.partition(" ")
    rest = rest.strip()
    if not rest:
        return False
    scheme = scheme.lower()
    if scheme == "bearer":
        return hmac.compare_digest(rest, token)
    if scheme == "basic":
        return _basic_matches(rest, token)
    return False


class SharedCredentialMiddleware:
    """Pure-ASGI gate. Rejects before the route is ever resolved.

    Written as raw ASGI rather than as a `BaseHTTPMiddleware` subclass on
    purpose: `BaseHTTPMiddleware` buffers the request through an anyio task
    group, and this app streams multi-megabyte multipart uploads through
    `POST /corpus` and `POST /batch`. A gate that changes how uploads are
    carried is doing more than gating.

    Non-HTTP scopes (`lifespan`, and websockets if any ever appear) pass
    straight through -- there is no `Authorization` header to inspect on a
    lifespan message, and swallowing it would break startup.
    """

    def __init__(self, app, token=None, open_paths=OPEN_PATHS):
        self.app = app
        self.token = token
        self.open_paths = frozenset(open_paths)

    def _authorised(self, scope):
        if not self.token:
            return True
        if scope.get("path") in self.open_paths:
            return True
        for name, value in scope.get("headers") or ():
            # ASGI guarantees header names are already lowercased bytes.
            if name == b"authorization":
                try:
                    decoded = value.decode("latin-1")
                except UnicodeDecodeError:  # pragma: no cover - latin-1 cannot fail
                    return False
                return credential_ok(decoded, self.token)
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._authorised(scope):
            await self.app(scope, receive, send)
            return
        body = b"unauthorised: this Missing Link instance requires a credential\n"
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                # Without this a browser shows the bare 401 body and gives the
                # operator no way to supply the credential at all.
                (b"www-authenticate",
                 'Basic realm="{}", charset="UTF-8"'.format(REALM).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

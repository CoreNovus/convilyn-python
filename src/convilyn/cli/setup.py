"""``convilyn setup`` — sign in and save a Convilyn API key.

Three ways in, one outcome: a session used EXACTLY ONCE to mint a ``ck_`` key,
then discarded. ``google`` / ``github`` run the RFC 8252 brokered-PKCE loopback
flow through a browser; ``email`` posts to ``/api/v1/auth/signin`` with a
password read from the terminal with echo off.

Originally browser-only; the password path was added because requiring a
third-party identity provider to use your own Convilyn account is a gap, not a
policy.

Mints a real ``ck_`` API key the same way the web console does, without the
user copying anything by hand. The browser path opens the system browser at the
backend's desktop-OAuth authorize endpoint, catches the redirect on a loopback
HTTP server bound to an ephemeral port, and exchanges the resulting code for a
short-lived session. Either path then uses that session **exactly once** to mint
a ``ck_`` key via ``POST /api/v1/console/keys``, and discards it.

**Why mint-then-discard rather than persist the session**: the SDK's only
credential *kind* stays ``ck_`` — ``resolve_auth`` needed one new, lowest-
priority source (the local credentials file), not a second ``AuthStrategy``
that knows how to refresh a JWT pair. See
:mod:`convilyn._internal.credentials` and
:func:`convilyn._internal.auth.resolve_auth`.

The session access/refresh tokens are NEVER logged and NEVER written to
disk — only the minted ``ck_`` key is persisted, mirroring the backend's own
P0-5 discipline in ``app/api/v1/identity/desktop_auth.py``.

**The two browser providers require the backend to have registered a
``convilyn-cli`` OAuth client** (``Settings.desktop_oauth_clients``) with a
loopback redirect URI, and ``desktop_oauth_enabled=True`` for the target
environment. Until that configuration exists, the authorize/token calls fail
cleanly with a 404/400 and this command reports ``EXIT_API_ERROR`` — the SDK
code itself has no dependency on when that configuration lands.

``email`` has no such dependency: ``/api/v1/auth/signin`` is the web console's
own route and is enabled wherever the console is. That makes it the working
path on an environment where desktop OAuth is switched off — which is not a
hypothetical, it is exactly the state prod was in until 2026-08-29.
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import platform
import re
import secrets
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import click
import httpx

from convilyn import Convilyn
from convilyn._internal import credentials
from convilyn._internal.http import resolve_base_url
from convilyn.cli._banner import print_banner, should_show_banner
from convilyn.cli._browser import open_url_with_fallback
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_INTERRUPTED, EXIT_USAGE
from convilyn.cli._output import make_renderer, should_colorize, write_line
from convilyn.exceptions import APIError

_CLIENT_ID = "convilyn-cli"
_CALLBACK_PATH = "/callback"
_DEFAULT_TIMEOUT_SECONDS = 180.0

#: Every way this CLI can obtain a session, in prompt order.
#:
#: ``google`` / ``github`` are the two the backend's desktop-OAuth endpoint
#: accepts (``OAuthProviderLiteral``); ``email`` is not an OAuth provider at all
#: — it posts to ``/api/v1/auth/signin``, the same route the web console uses
#: for a Convilyn account with a password.
#:
#: Listing all three under one ``--provider`` flag is deliberate: from the
#: user's side the question is "how do I sign in", and having a separate flag
#: for the password path would make the answer depend on knowing which of the
#: three happens to be OAuth. What the three share is the shape that matters
#: here — each yields a session that is used exactly ONCE to mint a ``ck_`` key
#: and is then discarded.
_OAUTH_PROVIDERS = ("google", "github")
_PROVIDERS = (*_OAUTH_PROVIDERS, "email")

#: Printed after a successful setup. Four, not everything — a wall of links is
#: read as decoration and skipped, so this is the shortest set that covers what
#: someone with a brand-new key actually has to decide: which lane to use, that
#: offline conversion needs no key at all, what costs credits, and how to manage
#: the key that was just written to their disk.
#:
#: `test_setup.py::test_every_welcome_link_is_a_real_docs_page` pins each path
#: against `frontend-docs/content/en/`, so a page renamed or removed there fails
#: here rather than shipping a 404 to every new user. That check is offline —
#: it reads the repo, not the network — because a gate that needs the internet
#: is a gate that gets skipped.
_DOCS_BASE = "https://docs.convilyn.com/en"
_WELCOME_LINKS: tuple[tuple[str, str], ...] = (
    ("Choosing a lane", f"{_DOCS_BASE}/usage-guide"),
    ("Convert offline", f"{_DOCS_BASE}/local-conversion"),
    ("Credits & pricing", f"{_DOCS_BASE}/credits"),
    ("Managing API keys", f"{_DOCS_BASE}/api-keys"),
)

# Same allowlist as the backend's `_NAME_PATTERN` (app/schemas/console/keys.py)
# — anything else is replaced with '-' so the mint request never 422s on the
# name alone.
_NAME_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_- ")

#: The console's own limits, mirrored so a bad `--key-name` fails here rather
#: than as a 422 after a completed browser sign-in.
_NAME_MAX_LEN = 50
_NAME_RE = re.compile(r"^[A-Za-z0-9_\- ]+$")


def _validate_key_name(ctx: Any, param: Any, value: str | None) -> str | None:
    """Reject a ``--key-name`` the console would reject, before the browser opens.

    Mirrors ``app/schemas/console/keys.py`` (``^[A-Za-z0-9_\\- ]+$``, 1-50). The
    server would catch it anyway — with a 422 at the END of a full sign-in round
    trip, which is the most expensive possible moment to find a typo.
    """
    if value is None:
        return None
    if not 1 <= len(value) <= _NAME_MAX_LEN:
        raise click.BadParameter(
            f"--key-name must be 1-{_NAME_MAX_LEN} characters (got {len(value)})."
        )
    if not _NAME_RE.match(value):
        raise click.BadParameter(
            "--key-name may contain only letters, digits, spaces, '-' and '_'."
        )
    return value


_PAGE_CSS = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
    "background:#0b0b0f;color:#e7e7ea;margin:0;"
    "display:flex;align-items:center;justify-content:center;min-height:100vh}"
    ".card{max-width:32rem;padding:2.5rem;text-align:left;line-height:1.6}"
    "h1{font-size:1.4rem;margin:0 0 .25rem}"
    ".rule{height:3px;border-radius:2px;margin:0 0 1.5rem;width:4rem;"
    "background:linear-gradient(90deg,#7c3aed,#a855f7 25%,#e11d48 55%,#f59e0b)}"
    "p{margin:.6rem 0;color:#b9b9c2}"
    "ul{margin:.6rem 0 0;padding-left:1.1rem;color:#b9b9c2}"
    "li{margin:.35rem 0}"
    "code{background:#1a1a22;padding:.15rem .4rem;border-radius:4px;color:#e7e7ea}"
    ".ok{color:#4ade80}.bad{color:#fb7185}"
)


def _callback_html(error: str | None) -> str:
    """The page the browser lands on, reflecting what actually happened.

    This used to be one constant reading "Signed in to Convilyn", served
    unconditionally — **including when the callback was rejected**. A state
    mismatch or a missing code set the error and the browser was still told it
    had succeeded, while the terminal said the opposite. The browser is where
    the user is looking at that moment, so that is the surface that has to be
    right.

    It also said nothing beyond "you can close this window". The user has just
    approved something; what they cannot see from here is *what* was approved —
    that a machine-scoped API key is being created rather than a password being
    stored, and that the key never travels through this browser. Saying so is
    the difference between an OAuth screen that reassures and one that just
    ends.

    Self-contained by necessity: this is served by a loopback HTTP server with
    no network access and no assets, so the styling is inline and the brand
    gradient is the same four stops the terminal banner uses.
    """
    if error:
        body = (
            f"<h1 class='bad'>Sign-in was not completed</h1><div class='rule'></div>"
            f"<p>{html.escape(error)}</p>"
            "<p>Nothing was saved. Return to your terminal — it has the details, "
            "and you can run <code>convilyn setup</code> again.</p>"
        )
    else:
        body = (
            "<h1 class='ok'>Signed in to Convilyn</h1><div class='rule'></div>"
            "<p>You can close this window and return to your terminal.</p>"
            "<ul>"
            "<li>An API key is being created for <strong>this machine</strong>, "
            "named after it so you can recognise and revoke it later.</li>"
            "<li>The key is written to a local credentials file. Your sign-in "
            "session is used once to create it and then discarded — it is never "
            "saved.</li>"
            "<li>The key does not pass through this browser page.</li>"
            "<li>Run <code>convilyn doctor</code> any time to see which key is in "
            "use and where it is stored.</li>"
            "</ul>"
        )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Convilyn</title>"
        f"<style>{_PAGE_CSS}</style></head>"
        f"<body><div class='card'>{body}</div></body></html>"
    )


@click.command(
    help="Log in and save a Convilyn API key locally (no manual key copy-paste).",
)
@click.option(
    "--provider",
    type=click.Choice(_PROVIDERS),
    default=None,
    help=(
        "How to sign in: 'google' or 'github' open a browser; 'email' asks for "
        "your Convilyn email and password here in the terminal. Prompted "
        "interactively when omitted on a TTY."
    ),
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Don't try to launch a browser — just print the login URL (headless/SSH).",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=float,
    default=_DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Seconds to wait for the browser login to complete.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Sign in again even if a working key is already saved (mints a new key).",
)
@click.option(
    "--key-name",
    default=None,
    callback=_validate_key_name,
    help=(
        "Name for the API key this creates, as it will appear in the web "
        "console. Defaults to cli-<hostname>. Use it when that name is already "
        "taken and you would rather choose than accept a timestamped one."
    ),
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout (also suppresses the banner).",
)
def setup_command(
    provider: str | None,
    no_browser: bool,
    timeout_seconds: float,
    force: bool,
    key_name: str | None,
    json_output: bool,
) -> None:
    """Sign in, mint a `ck_` API key, and save it for future commands."""
    renderer = make_renderer(json_output=json_output)

    if not force and _reuse_existing_key(renderer=renderer, json_output=json_output):
        return

    if provider is None:
        if not _stdin_is_interactive():
            raise click.UsageError("--provider is required when not running interactively")
        provider = click.prompt("Sign in with", type=click.Choice(_PROVIDERS), default="google")
    assert provider is not None  # narrows for type-checking; guaranteed by the branch above

    if should_show_banner(json_output=json_output):
        print_banner()

    base_url = resolve_base_url(None)

    if provider == "email":
        access_token = _password_session(base_url=base_url)
    else:
        access_token = _browser_session(
            base_url=base_url,
            provider=provider,
            no_browser=no_browser,
            timeout_seconds=timeout_seconds,
        )

    try:
        key = _mint_key(base_url=base_url, access_token=access_token, key_name=key_name)
    except (httpx.HTTPError, APIError) as exc:
        click.echo(f"Login failed: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc

    path = credentials.write_credentials(key, source="setup")

    tier = _verify_key(base_url=base_url, key=key)
    _print_welcome(tier=tier, json_output=json_output)
    renderer.final(
        {
            "command": "setup",
            "status": "ok",
            "provider": provider,
            "tier": tier,
            "credentials_path": str(path),
            # Complements the welcome block rather than repeating it: the
            # headline above already says setup succeeded, so this line
            # carries the one fact it does not — where the key landed.
            "summary": f"Key saved to {path} (signed in with {provider})",
        }
    )


def _reuse_existing_key(*, renderer: Any, json_output: bool) -> bool:
    """Short-circuit when a saved key still works. Returns True if we did.

    Re-running ``convilyn setup`` should not cost the user another sign-in.
    But "a credentials file exists" is the wrong question — a key that has been
    revoked from the console, or that belongs to an account the user has since
    left, is exactly the case where they need to log in again and the file tells
    you nothing. So the key is **used** before it is trusted: one call to the
    same tier lookup that already runs after a fresh mint. If it does not
    authenticate, this returns False and the normal login proceeds.

    The failure direction is chosen deliberately. `_verify_key` is best-effort
    and returns ``None`` on any failure, network outages included, so an offline
    machine falls through to a login it also cannot complete — annoying, and the
    right way round: the alternative is telling someone they are set up when
    nothing has confirmed it, and then failing on their first real command with
    an error that points at the wrong thing.

    ``--force`` skips this entirely, which is the escape hatch for "I want a new
    key on this machine" (a shared box, a rotated credential, switching account).

    The key itself is never printed — not here, not in the JSON payload. What is
    reported is that one exists and that it works.
    """
    existing = credentials.read_credentials()
    if not existing:
        return False

    tier = _verify_key(base_url=resolve_base_url(None), key=existing)
    if tier is None:
        click.echo(
            "A saved key was found but it did not authenticate — signing in again.",
            err=True,
        )
        return False

    _print_welcome(tier=tier, json_output=json_output, returning=True)
    renderer.final(
        {
            "command": "setup",
            "status": "ok",
            "provider": None,
            "tier": tier,
            "credentials_path": str(credentials.credentials_path()),
            "reused_existing_key": True,
            "summary": f"Key at {credentials.credentials_path()} (nothing changed)",
        }
    )
    return True


def _stdin_is_interactive() -> bool:
    """Whether we can prompt the user at all.

    One implementation because two call sites ask it: choosing a provider when
    --provider was omitted, and reading a password for --provider email.
    Both must refuse rather than block on a prompt nothing will answer.
    """
    return sys.stdin.isatty()


def _print_welcome(*, tier: str | None, json_output: bool, returning: bool = False) -> None:
    """Confirm success in green and point at what to read next.

    Suppressed in ``--json`` mode, where stdout must stay a single valid
    object. Otherwise it always PRINTS — only the colour is conditional. That
    split is deliberate and is the opposite of the banner's: the banner is
    decoration and disappears when it cannot be rendered as intended, whereas
    these four links are the answer to "I have a key, now what?", which a user
    reading a captured log needs just as much as one at a terminal.

    Every URL is verified to resolve before being shipped. A welcome message
    that hands someone four 404s is worse than one that hands them none — it
    reads as a product that has been abandoned.

    ``returning=True`` is the "you were already signed in" wording. Saying
    "setup complete" to someone who did not just complete anything reads as a
    command that ignored them — and it hides the one fact they need if they ran
    this BECAUSE something was wrong: that nothing changed, and ``--force`` is
    how to change it.
    """
    if json_output:
        return

    green, dim, reset = ("\x1b[1;32m", "\x1b[2m", "\x1b[0m") if should_colorize() else ("", "", "")
    plan = f" You are on the {tier} plan." if tier else ""
    headline = (
        "Already signed in — your saved key works."
        if returning
        else "Setup complete — welcome to Convilyn."
    )

    write_line("", sys.stdout)
    write_line(f"{green}{headline}{reset}{plan}", sys.stdout)
    if returning:
        write_line(f"{dim}Run `convilyn setup --force` to sign in again.{reset}", sys.stdout)
    write_line("", sys.stdout)
    write_line("Where to go next:", sys.stdout)
    for label, url in _WELCOME_LINKS:
        write_line(f"  {label:<22} {dim}{url}{reset}", sys.stdout)
    write_line("", sys.stdout)


def _browser_session(
    *, base_url: str, provider: str, no_browser: bool, timeout_seconds: float
) -> str:
    """Run the RFC 8252 loopback OAuth flow; return a short-lived access token."""
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    server = http.server.HTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{_CALLBACK_PATH}"
    callback_thread = threading.Thread(target=server.handle_request, daemon=True)

    authorize_url = _build_authorize_url(
        base_url=base_url,
        provider=provider,
        code_challenge=challenge,
        state=state,
        redirect_uri=redirect_uri,
    )

    try:
        callback_thread.start()
        # The thread must be listening BEFORE the browser (or, in tests, a
        # synchronous fake that completes the whole redirect inline) can fire
        # its request — `HTTPServer.__init__` already bound + listened, but
        # nothing calls `accept()` until this thread runs `handle_request()`.
        # Opening first raced the callback and cost a real BrokenPipeError.
        open_url_with_fallback(
            authorize_url,
            purpose=f"sign in with {provider}",
            attempt_open=not no_browser,
        )
        callback_thread.join(timeout=timeout_seconds)
        if callback_thread.is_alive():
            click.echo("Timed out waiting for the browser login to complete.", err=True)
            raise SystemExit(EXIT_USAGE)
    except KeyboardInterrupt:
        raise SystemExit(EXIT_INTERRUPTED) from None
    finally:
        server.server_close()

    handler = server.RequestHandlerClass  # the closure holds the captured result
    result = handler.result  # type: ignore[attr-defined]

    if result.error:
        click.echo(f"Login failed: {result.error}", err=True)
        raise SystemExit(EXIT_USAGE)

    try:
        return _exchange_token(
            base_url=base_url,
            code=result.code,
            code_verifier=verifier,
            state=state,
        )
    except (httpx.HTTPError, APIError) as exc:
        click.echo(f"Login failed: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc


def _password_session(*, base_url: str) -> str:
    """Sign in with a Convilyn email + password; return a short-lived access token.

    Not an OAuth leg — this posts to ``/api/v1/auth/signin``, the same route the
    web console uses. No loopback server, no browser, no PKCE: there is no
    third party to redirect through, so there is nothing for those to protect.

    The password is read with echo off and is **never** stored, logged, or
    retried — it exists only for the duration of this one request, and the
    session it returns is discarded the moment a ``ck_`` key is minted, exactly
    as in the browser path.
    """
    if not _stdin_is_interactive():
        raise click.UsageError(
            "--provider email needs an interactive terminal to ask for your "
            "password. On a headless host use --provider google or github with "
            "--no-browser, which prints a URL you can open anywhere."
        )

    email = click.prompt("Email", type=str)
    password = click.prompt("Password", type=str, hide_input=True)

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/auth/signin",
            json={"email": email, "password": password},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        click.echo(f"Login failed: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc

    if response.status_code >= 400:
        # Deliberately relays the server's own message rather than flattening
        # every 4xx to "wrong password". The backend distinguishes cases the
        # user must act on differently — an unverified email, a locked account
        # after repeated failures — and collapsing them sends someone retyping
        # a password that was never the problem.
        click.echo(f"Login failed: {_error_message(response)}", err=True)
        raise SystemExit(EXIT_API_ERROR)

    # `response_model_by_alias` default — the wire is camelCase, matching
    # `_exchange_token` above and `docs/contracts/auth/auth_openapi.yaml`.
    return response.json()["accessToken"]


# ── Helpers (each testable in isolation; SRP) ─────────────────────────


class _CallbackResult:
    __slots__ = ("code", "state", "error")

    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None


def _make_handler(expected_state: str) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a one-shot ``BaseHTTPRequestHandler`` bound to *expected_state*.

    The parsed outcome is exposed as a class attribute (``.result``) rather
    than via a constructor arg, because ``HTTPServer`` instantiates the
    handler itself per request — there is no seam to pass an object in.
    """
    result = _CallbackResult()

    class Handler(http.server.BaseHTTPRequestHandler):
        # Class attribute, not instance — see docstring above.
        result: _CallbackResult

        def do_GET(self) -> None:  # noqa: N802 - stdlib-mandated method name
            parsed = urlsplit(self.path)
            if parsed.path != _CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            returned_state = (params.get("state") or [None])[0]
            code = (params.get("code") or [None])[0]
            # Validate state locally BEFORE trusting anything from this
            # request — defense in depth against a stray localhost request
            # racing the real callback.
            if returned_state != expected_state:
                result.error = "state mismatch (unexpected or forged callback)"
            elif not code:
                result.error = "no authorization code in callback"
            else:
                result.code = code
                result.state = returned_state
            # 400 on rejection, not 200. The browser page and the terminal must
            # agree about what happened, and the status code is the half a
            # script or a proxy can see.
            self.send_response(200 if result.error is None else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_callback_html(result.error).encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:
            return  # silence BaseHTTPRequestHandler's default stderr access log

    Handler.result = result
    return Handler


def _generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` — S256 per RFC 7636.

    Backend rejects anything but S256 (`desktop_auth.py::is_s256_challenge`).
    """
    verifier = secrets.token_urlsafe(64)[:128]  # RFC 7636 §4.1: 43-128 chars
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_authorize_url(
    *, base_url: str, provider: str, code_challenge: str, state: str, redirect_uri: str
) -> str:
    query = urlencode(
        {
            "provider": provider,
            "code_challenge": code_challenge,
            "state": state,
            "client_id": _CLIENT_ID,
            "redirect_uri": redirect_uri,
        }
    )
    return f"{base_url.rstrip('/')}/api/v1/auth/desktop/authorize?{query}"


def _exchange_token(*, base_url: str, code: str | None, code_verifier: str, state: str) -> str:
    """Redeem the callback code for a session; return the access token."""
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/v1/auth/desktop/token",
        json={
            "code": code,
            "code_verifier": code_verifier,
            "state": state,
            "client_id": _CLIENT_ID,
            "device_label": _device_label(),
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise APIError(response.status_code, "invalid_grant", _error_message(response))
    # `response_model_by_alias=True` on this endpoint — the wire uses
    # camelCase (`accessToken`), not the Python field name.
    return response.json()["accessToken"]


def _mint_key(*, base_url: str, access_token: str, key_name: str | None = None) -> str:
    """Mint a `ck_` key, then let the caller discard the session that authorized it.

    ``key_name`` is ``--key-name``; ``None`` falls back to :func:`_default_key_name`
    (``cli-<hostname>``). The flag exists because the name used to be hardcoded
    with no way to change it, which left a colliding name with no local remedy:
    ``--force`` skips only the LOCAL saved-key reuse and then mints under the same
    name, and there is no ``convilyn keys`` subcommand to revoke with.

    **Retries once under a disambiguated name on 409.** The default name derives
    from ``platform.node()``, which is deterministic, so it collides on every
    re-run from the same machine — and the console rejects a duplicate active
    name with ``409``. That made ``convilyn setup`` a one-shot command: anyone
    whose first run half-completed (browser closed, token exchange fine, write
    interrupted) hit ``Login failed: HTTP 409 ... An active key with this name
    already exists`` on every subsequent attempt, with no way forward that the
    message named.

    A 409 cannot be treated as "already authorized". The console shows a key's
    secret exactly once, at mint, so the existing key's value is not recoverable
    here — reporting success without writing a credential produces precisely the
    state this defect leaves behind: a machine that believes it is set up and
    has no usable key. Minting under a distinct name is what actually ends with
    the user authorized, and it is the same collision-avoidance the repo's own
    e2e harness already uses (``e2e-doceval-<epoch>``).

    The pre-existing key is left ALONE rather than revoked. It may be in use by
    CI, another checkout, or a teammate on a shared box, and silently killing a
    live credential to tidy a name is not a trade this command gets to make.
    """
    name = key_name or _default_key_name()
    response = _post_key(base_url=base_url, access_token=access_token, name=name)
    if response.status_code == 409:
        response = _post_key(
            base_url=base_url, access_token=access_token, name=_disambiguated(name)
        )
        if response.status_code == 409:
            # Both names taken. The server's own message ends "Revoke it or pick
            # another name", and until `--key-name` existed the second half was
            # not doable from a terminal at all: the name was hardcoded and there
            # is no `convilyn keys` subcommand to revoke with. Say what to do.
            raise APIError(
                response.status_code,
                "key_mint_failed",
                f"{_error_message(response)} "
                f"Both '{name}' and a timestamped variant are taken. "
                "Re-run with `--key-name <something else>`, or revoke the "
                "existing key in the web console under Settings > API keys.",
            )
    if response.status_code >= 400:
        raise APIError(response.status_code, "key_mint_failed", _error_message(response))
    return response.json()["key"]


def _post_key(*, base_url: str, access_token: str, name: str) -> httpx.Response:
    """One POST to the key-mint endpoint. Split out so :func:`_mint_key` can
    issue the retry above without duplicating the request shape."""
    return httpx.post(
        f"{base_url.rstrip('/')}/api/v1/console/keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": name},
        timeout=30.0,
    )


def _verify_key(*, base_url: str, key: str) -> str | None:
    """Best-effort tier lookup with the freshly minted key — advisory only,
    mirrors `doctor.py::_check_account_tier`'s reasoning for why a failure
    here should not undo a successful mint."""
    try:
        with Convilyn(api_key=key, base_url=base_url) as client:
            return client.account.get_plan().tier
    except Exception:
        return None


def _sanitize_key_name(raw: str) -> str:
    """Coerce ``raw`` into a name the console will accept. Sanitize ONLY.

    This used to also prepend ``cli-``, and doing both in one function is what
    produced ``cli-cli-<host>-<epoch>`` when :func:`_mint_key` fed it a name that
    already carried the prefix. Naming and sanitizing are separate jobs; the
    caller composes, this clamps.
    """
    cleaned = "".join(c if c in _NAME_ALLOWED else "-" for c in raw).strip() or "cli"
    return cleaned[:_NAME_MAX_LEN]


def _default_key_name() -> str:
    """The name used when ``--key-name`` is not given."""
    return _sanitize_key_name(f"cli-{platform.node()}")


def _disambiguated(name: str) -> str:
    """``name`` with a timestamp suffix that SURVIVES the length clamp.

    The suffix is the only part making the retry unique, so the base is trimmed
    to make room for it rather than the whole string being clipped from the
    right. Clipping the whole string is what the first version of this retry
    did, and past ~42 characters of hostname it removed the timestamp entirely —
    leaving a deterministic name that collides exactly like the one it was
    retrying, which is the failure the retry exists to prevent.
    """
    suffix = f"-{int(time.time())}"
    return f"{name[: _NAME_MAX_LEN - len(suffix)]}{suffix}"


def _device_label() -> str:
    return f"cli:{platform.system()}"[:64]


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or detail)
    return str(detail)

"""``convilyn setup`` — browser-based login (RFC 8252 brokered PKCE).

Mints a real ``ck_`` API key the same way the web console does, without the
user copying anything by hand: opens the system browser at the backend's
desktop-OAuth authorize endpoint, catches the redirect on a loopback HTTP
server bound to an ephemeral port, exchanges the resulting code for a
short-lived session, uses that session **exactly once** to mint a ``ck_``
key via ``POST /api/v1/console/keys``, then discards the session.

**Why mint-then-discard rather than persist the session**: the SDK's only
credential *kind* stays ``ck_`` — ``resolve_auth`` needed one new, lowest-
priority source (the local credentials file), not a second ``AuthStrategy``
that knows how to refresh a JWT pair. See
:mod:`convilyn._internal.credentials` and
:func:`convilyn._internal.auth.resolve_auth`.

The session access/refresh tokens are NEVER logged and NEVER written to
disk — only the minted ``ck_`` key is persisted, mirroring the backend's own
P0-5 discipline in ``app/api/v1/identity/desktop_auth.py``.

**This command requires the backend to have registered a ``convilyn-cli``
OAuth client** (``Settings.desktop_oauth_clients``) with a loopback redirect
URI, and ``desktop_oauth_enabled=True`` for the target environment. Until
that configuration exists, the authorize/token calls fail cleanly with a
404/400 and this command reports ``EXIT_API_ERROR`` — the SDK code itself
has no dependency on when that configuration lands.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import platform
import secrets
import sys
import threading
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import click
import httpx

from convilyn import Convilyn
from convilyn._internal import credentials
from convilyn._internal.http import resolve_base_url
from convilyn.cli._banner import print_banner, should_show_banner
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_INTERRUPTED, EXIT_USAGE
from convilyn.cli._output import make_renderer
from convilyn.exceptions import APIError

_CLIENT_ID = "convilyn-cli"
_CALLBACK_PATH = "/callback"
_DEFAULT_TIMEOUT_SECONDS = 180.0

# Same allowlist as the backend's `_NAME_PATTERN` (app/schemas/console/keys.py)
# — anything else is replaced with '-' so the mint request never 422s on the
# name alone.
_NAME_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_- ")

_CALLBACK_HTML = (
    "<html><body style='font-family: sans-serif; text-align: center; padding-top: 4rem;'>"
    "<h2>Signed in to Convilyn</h2>"
    "<p>You can close this window and return to the terminal.</p>"
    "</body></html>"
)


@click.command(
    help="Log in via your browser and save a Convilyn API key locally (no manual key copy-paste).",
)
@click.option(
    "--provider",
    type=click.Choice(["google", "github"]),
    default=None,
    help="OAuth provider to sign in with. Prompted interactively when omitted on a TTY.",
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
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout (also suppresses the banner).",
)
def setup_command(
    provider: str | None,
    no_browser: bool,
    timeout_seconds: float,
    json_output: bool,
) -> None:
    """Browser-based login: mint a `ck_` API key and save it for future commands."""
    renderer = make_renderer(json_output=json_output)

    if provider is None:
        if not sys.stdin.isatty():
            raise click.UsageError("--provider is required when not running interactively")
        provider = click.prompt(
            "Sign in with", type=click.Choice(["google", "github"]), default="google"
        )
    assert provider is not None  # narrows for type-checking; guaranteed by the branch above

    if should_show_banner(json_output=json_output):
        print_banner()

    base_url = resolve_base_url(None)
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

    # Always shown, in BOTH human and --json mode, and always to stderr — this
    # is operator-essential information (the fallback when a browser can't be
    # launched, mirroring what `modal setup` prints), not structured event
    # data, so it is not gated behind the renderer's json/human split.
    click.echo(f"Opening your browser to sign in with {provider}…", err=True)
    click.echo(
        f"If it doesn't open automatically, visit this URL:\n\n  {authorize_url}\n", err=True
    )

    try:
        callback_thread.start()
        if not no_browser:
            webbrowser.open(authorize_url)

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
        access_token = _exchange_token(
            base_url=base_url,
            code=result.code,
            code_verifier=verifier,
            state=state,
        )
        key = _mint_key(base_url=base_url, access_token=access_token)
    except (httpx.HTTPError, APIError) as exc:
        click.echo(f"Login failed: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc

    path = credentials.write_credentials(key, source="setup")

    tier = _verify_key(base_url=base_url, key=key)
    renderer.final(
        {
            "command": "setup",
            "status": "ok",
            "provider": provider,
            "tier": tier,
            "credentials_path": str(path),
            "summary": f"✓ Logged in ({provider}) — key saved to {path}",
        }
    )


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
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_CALLBACK_HTML.encode("utf-8"))

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


def _mint_key(*, base_url: str, access_token: str) -> str:
    """Mint a `ck_` key scoped to this machine, then let the caller discard
    the session token that authorized it."""
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/v1/console/keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": _sanitize_key_name(platform.node())},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise APIError(response.status_code, "key_mint_failed", _error_message(response))
    return response.json()["key"]


def _verify_key(*, base_url: str, key: str) -> str | None:
    """Best-effort tier lookup with the freshly minted key — advisory only,
    mirrors `doctor.py::_check_account_tier`'s reasoning for why a failure
    here should not undo a successful mint."""
    try:
        with Convilyn(api_key=key, base_url=base_url) as client:
            return client.account.get_plan().tier
    except Exception:
        return None


def _sanitize_key_name(hostname: str) -> str:
    cleaned = "".join(c if c in _NAME_ALLOWED else "-" for c in hostname).strip() or "cli"
    return f"cli-{cleaned}"[:50]


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

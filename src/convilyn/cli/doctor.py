"""``convilyn doctor`` — print SDK environment diagnostics.

Mirrors the pattern of ``brew doctor`` / ``gh auth status`` / ``stripe
status``: a quick, scriptable readout of "is this client likely to
work in this environment?". Exits non-zero if any required piece is
missing so CI / agent loops can branch on the result.
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass

import click
import httpx

from convilyn import __version__
from convilyn._internal.auth import ENV_API_KEY
from convilyn._internal.http import DEFAULT_BASE_URL, ENV_BASE_URL, resolve_base_url
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_USAGE
from convilyn.cli._output import OutputRenderer, make_renderer

# The two statuses that mean "the backend understood the request and refused
# these credentials", as opposed to "the backend is having a bad time". 401 is a
# rejected key; 403 is a key that is real but not entitled to this call. Both
# are answers about the caller's setup, which is what `doctor` reports on.
_AUTH_REJECTED = frozenset({401, 403})


@dataclass(frozen=True)
class Check:
    """One diagnostic line — printed by both renderers.

    ``exit_code`` is what a **FAIL** of this particular check means, and it
    travels with the check because only the check knows. It replaced a line in
    :func:`doctor_command` that matched on ``c.name == "Backend health"`` — so
    every other kind of failure was an `EXIT_USAGE`, including a rejected API
    key, which `_exit_codes` documents as the textbook `EXIT_API_ERROR`
    ("auth, 4xx, 5xx after retries"). Matching on a display string also means
    renaming a check silently reclassifies it.
    """

    name: str
    status: str  # "OK" / "WARN" / "FAIL" / "SKIP"
    detail: str
    exit_code: int = EXIT_USAGE


@click.command(
    help="Show SDK environment + backend connectivity diagnostics.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout.",
)
@click.option(
    "--ping",
    is_flag=True,
    help="Probe the backend health endpoint (default: skip).",
)
def doctor_command(json_output: bool, ping: bool) -> None:
    """Print Python / SDK / env / connectivity diagnostics."""
    renderer = make_renderer(json_output=json_output)
    checks = _collect_checks(ping=ping)
    _emit_checks(renderer, checks)

    failures = [c for c in checks if c.status == "FAIL"]
    if failures:
        # Each check carries what its own failure means (see `Check`). An API
        # failure outranks a config one: if the backend rejected us, that is the
        # thing to report even when something local is also unset.
        if any(c.exit_code == EXIT_API_ERROR for c in failures):
            raise SystemExit(EXIT_API_ERROR)
        raise SystemExit(EXIT_USAGE)


# ── Diagnostic engine (testable) ─────────────────────────────────────


def _collect_checks(*, ping: bool) -> list[Check]:
    """Gather every diagnostic line. Pure-ish — only IO is import / env."""
    checks: list[Check] = []
    checks.append(_check_python())
    checks.append(_check_sdk_version())
    for module in ("httpx", "pydantic", "click"):
        checks.append(_check_dependency(module))
    checks.append(_check_api_key())
    checks.append(_check_base_url())
    if ping:
        checks.append(_check_backend_health())
        # Tier check only runs once /api/v1/health has confirmed the
        # backend is reachable AND an API key is present — otherwise
        # the round-trip is guaranteed to fail. Mostly advisory, with one
        # exception: see `_check_account_tier` on 401/403.
        if os.environ.get(ENV_API_KEY):
            checks.append(_check_account_tier())
    else:
        checks.append(
            Check(
                "Backend health",
                "SKIP",
                "skipped (re-run with --ping to probe /api/v1/health)",
            )
        )
    return checks


def _check_python() -> Check:
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 10):
        return Check(
            "Python",
            "FAIL",
            f"need ≥ 3.10, got {platform.python_version()}",
        )
    return Check("Python", "OK", platform.python_version())


def _check_sdk_version() -> Check:
    return Check("convilyn SDK", "OK", __version__)


def _check_dependency(module_name: str) -> Check:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return Check(module_name, "FAIL", "not installed")
    version = getattr(module, "__version__", "unknown")
    return Check(module_name, "OK", str(version))


def _check_api_key() -> Check:
    key = os.environ.get(ENV_API_KEY)
    if not key:
        return Check(
            ENV_API_KEY,
            "FAIL",
            f"not set — export {ENV_API_KEY}=ck_… before calling the API",
        )
    return Check(ENV_API_KEY, "OK", _mask_secret(key))


def _check_base_url() -> Check:
    # Use the same resolver the client uses so doctor never reports a URL
    # the client would not actually dial (explicit arg n/a here → env → default).
    url = resolve_base_url(None)
    is_default = url == DEFAULT_BASE_URL
    return Check(ENV_BASE_URL, "OK", f"{url}{' (default)' if is_default else ''}")


def _check_backend_health() -> Check:
    """Probe ``GET /api/v1/health`` against the configured base URL."""
    base_url = resolve_base_url(None)
    url = f"{base_url.rstrip('/')}/api/v1/health"
    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        return Check(
            "Backend health", "FAIL", f"{type(exc).__name__}: {exc}", exit_code=EXIT_API_ERROR
        )
    if response.status_code >= 400:
        return Check(
            "Backend health",
            "FAIL",
            f"HTTP {response.status_code} {response.reason_phrase}",
            exit_code=EXIT_API_ERROR,
        )
    return Check("Backend health", "OK", f"{response.status_code} {response.reason_phrase}")


def _check_account_tier() -> Check:
    """Report the caller's current billing tier via ``client.account.get_plan()``.

    Mostly advisory — it surfaces the tier so a free user sees what they'll hit
    when calling Pro-only endpoints (workflow fork / publish), and a degraded
    tier query should not fail a diagnostic whose required checks all passed.

    **401 and 403 are the exception, and they are the whole point of the
    command.** The original wording — "any error here is non-fatal" — was right
    about a 5xx and wrong about a rejected key: it classified "the backend
    hiccuped" and "your credentials are refused" as the same event, so
    ``doctor --ping`` printed ``All checks passed.`` and exited 0 against a key
    the API rejects. Verified against both dev and prod, the latter with a
    fabricated key. ``doctor`` is what people put in the first step of CI, so
    that answer carries a broken key all the way downstream before it surfaces.

    So the split is on the status code, which is the thing that actually
    distinguishes the two cases. There is deliberately no ``--strict`` flag:
    once 401/403 is a failure there is no second reading left for a flag to
    select, and a flag with one meaning is the speculative configuration
    ``engineering-principles.md`` P2 refuses.

    Imports the SDK lazily so importing the doctor module doesn't pull
    in the entire client surface — keeps the CLI snappy.
    """
    try:
        from convilyn import APIError, Convilyn  # local import — see docstring
    except ImportError as exc:  # pragma: no cover — defensive
        return Check("Account tier", "WARN", f"SDK import failed: {exc}")

    # Match the URL `_check_backend_health` probes so doctor + tier
    # check agree on which environment is under inspection.
    base_url = resolve_base_url(None)
    try:
        with Convilyn(base_url=base_url) as client:
            plan = client.account.get_plan()
        return Check("Account tier", "OK", f"tier={plan.tier}")
    except APIError as exc:
        if exc.status_code in _AUTH_REJECTED:
            return Check(
                "Account tier",
                "FAIL",
                f"the API rejected this key: HTTP {exc.status_code} {exc.code} — "
                f"check {ENV_API_KEY} is a key for {base_url}",
                exit_code=EXIT_API_ERROR,
            )
        return Check(
            "Account tier",
            "WARN",
            f"tier query failed: HTTP {exc.status_code} {exc.code}",
        )
    except Exception as exc:
        # Network / config issues — non-fatal so the doctor still
        # exits OK on a valid env even when this advisory call breaks.
        return Check("Account tier", "WARN", f"{type(exc).__name__}: {exc}")


def _mask_secret(value: str) -> str:
    """Mask a secret for diagnostics — reveal only the tier prefix.

    Shows at most the first 3 characters (e.g. ``ck_`` / ``cvl``) so the
    operator can confirm *which* key is configured, then an ellipsis. No
    suffix is shown: leaking the trailing characters of an API key
    needlessly narrows its entropy if the diagnostic output is captured
    in logs or a screenshot.
    """
    if len(value) <= 4:
        return "***"
    return f"{value[:3]}…"


# ── Rendering ────────────────────────────────────────────────────────


#: Check status → the renderer event kind that draws it. A FAIL used to render
#: through the "warn" kind, so the glyph on a failed check was `!` — the same
#: one an advisory line gets. `_output._GLYPHS` has carried an "error" kind (✗)
#: the whole time; nothing was reaching for it.
_EVENT_KIND = {"OK": "ok", "FAIL": "error"}


def _emit_checks(renderer: OutputRenderer, checks: list[Check]) -> None:
    """Drive both renderers from the same check list (DRY across modes)."""
    for check in checks:
        renderer.event(
            _EVENT_KIND.get(check.status, "warn"),
            message=f"[{check.status}] {check.name}: {check.detail}",
        )

    payload = {
        "command": "doctor",
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
        "summary": _doctor_summary(checks),
    }
    renderer.final(payload)


def _doctor_summary(checks: list[Check]) -> str:
    """The last line, which has to agree with what was printed above it.

    "All checks passed." was emitted whenever nothing had status FAIL — so a run
    that printed a WARN line ended by saying every check passed, contradicting
    its own output two lines earlier. Warnings are now counted, and the sentence
    claiming a clean run is reserved for one.
    """
    failed = sum(1 for c in checks if c.status == "FAIL")
    warned = sum(1 for c in checks if c.status == "WARN")
    if not failed and not warned:
        return "All checks passed."
    parts = []
    if failed:
        parts.append(f"{failed} failed")
    if warned:
        parts.append(f"{warned} warned")
    passed = sum(1 for c in checks if c.status == "OK")
    parts.append(f"{passed} passed")
    return ", ".join(parts) + "."

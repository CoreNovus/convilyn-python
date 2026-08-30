"""Shared "open a URL, print a fallback" idiom for interactive CLI commands.

Extracted from ``cli/setup.py``'s browser-based login flow once a second call
site needed the same behaviour (an actionable billing link on
``InsufficientCreditsError``) — a relocation of existing logic, not a new
abstraction: nothing about the shape varies between the two callers.
"""

from __future__ import annotations

import webbrowser
from urllib.parse import urlsplit

import click

#: Schemes this will hand to the OS. `https` everywhere; `http` only for a
#: loopback host, which is what a local dev API returns.
#:
#: The list is short because the hazard is not "an odd scheme renders badly" —
#: `webbrowser.open` on Windows falls through to `os.startfile`, which OPENS
#: whatever it is given: a `file:` path, a UNC share, or a bare local path to an
#: executable. On Linux `xdg-open` is similarly permissive. So the scheme check
#: is the difference between showing a page and launching a program.
_SAFE_SCHEMES = frozenset({"https", "http"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _is_dispatchable(url: str) -> bool:
    """Whether this URL may be handed to the OS browser launcher.

    Deliberately a small allowlist rather than a denylist of bad schemes: the
    set of things `os.startfile` will happily run is open-ended, and a denylist
    of it is a list someone has to keep winning.

    Refuses userinfo (`https://user:pw@host`) — it is a phishing shape that
    renders as a different host than it resolves to, and nothing we send uses it.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in _SAFE_SCHEMES or not parts.netloc:
        return False
    if "@" in parts.netloc:
        return False
    if parts.scheme == "http":
        return (parts.hostname or "").lower() in _LOOPBACK_HOSTS
    return True


def open_url_with_fallback(url: str, *, intro: str, attempt_open: bool = True) -> None:
    """Best-effort ``webbrowser.open(url)``, with the URL always printed too.

    Always shown to stderr, in both human and ``--json`` mode — this is
    operator-essential information (the fallback when a browser can't be
    launched, e.g. headless/SSH), not structured event data, so it is never
    gated behind a renderer's json/human split.

    ``attempt_open=False`` skips the launch attempt entirely (mirrors
    ``setup.py``'s ``--no-browser`` for headless/SSH sessions) while still
    printing the URL. Otherwise, ``webbrowser.open`` failures (no display, no
    registered handler) are swallowed: the printed URL above is the real
    fallback, and a launch failure must never crash the command that is
    reporting an unrelated billing refusal.

    **The URL is validated before it is dispatched**, because two of the three
    call sites pass ``exc.upgrade_url`` — a string out of the SERVER's 402
    response body, which this client does not get to assume is well-formed. It
    was handed straight to ``webbrowser.open``; on Windows that reaches
    ``os.startfile``, which opens a local path or a UNC share as readily as a
    web page. A refused URL is still PRINTED, so a legitimate link with an
    unexpected shape is never silently lost — only never launched.
    """
    click.echo(intro, err=True)
    click.echo(f"If it doesn't open automatically, visit this URL:\n\n  {url}\n", err=True)
    if not attempt_open:
        return
    if not _is_dispatchable(url):
        click.echo("(not opening it automatically — unexpected link format)", err=True)
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass

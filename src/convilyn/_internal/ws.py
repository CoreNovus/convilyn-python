"""WebSocket transport abstraction for AI workflow event streaming.

Provides the SOLID DIP seam: :class:`convilyn.resources.goals.AsyncGoals`
depends on the abstract :class:`WSTransport` Protocol, not on the
``websockets`` library directly. Production wiring injects
:class:`WebsocketsTransport`; tests inject a fake that yields canned
messages.

The default implementation pins ``ping_interval=240s`` so the
server's idle timeout never trips a live stream; it also
sets a tight ``ping_timeout`` so a half-open connection surfaces as a
``WebSocketError`` quickly instead of hanging forever.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

ENV_WS_URL = "CONVILYN_WS_URL"


@runtime_checkable
class WSTransport(Protocol):
    """Minimal contract an AI workflow event transport must satisfy.

    Implementations may raise any exception subclass; the calling
    resource translates them to :class:`convilyn.exceptions.WebSocketError`
    so users only see one error type.
    """

    async def connect(self, url: str) -> None: ...

    async def send(self, payload: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


class WebsocketsTransport:
    """Default :class:`WSTransport` impl wrapping the ``websockets`` library.

    Keepalive defaults leave margin under the server's idle timeout: a
    ping every 4 minutes leaves margin for a slow round-trip on the
    pong reply.
    """

    __slots__ = ("_ping_interval", "_ping_timeout", "_ws")

    def __init__(
        self,
        *,
        ping_interval: float = 240.0,
        ping_timeout: float = 30.0,
    ) -> None:
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._ws: object | None = None

    async def connect(self, url: str) -> None:
        # Lazy import keeps the SDK importable even if ``websockets`` is
        # somehow stripped (e.g. a downstream build cuts optional deps);
        # only callers that actually open a stream pay the import cost.
        from websockets.asyncio.client import connect as ws_connect

        self._ws = await ws_connect(
            url,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )

    async def send(self, payload: str) -> None:
        if self._ws is None:
            raise RuntimeError("WebsocketsTransport.send called before connect")
        await self._ws.send(payload)  # type: ignore[attr-defined]

    async def recv(self) -> str:
        if self._ws is None:
            raise RuntimeError("WebsocketsTransport.recv called before connect")
        message = await self._ws.recv()  # type: ignore[attr-defined]
        # ``websockets`` returns str for text frames and bytes for binary;
        # the backend only emits text JSON, so decode if we somehow see
        # bytes rather than silently mis-typing the return.
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return message

    async def close(self) -> None:
        if self._ws is None:
            return
        await self._ws.close()  # type: ignore[attr-defined]
        self._ws = None


def resolve_ws_url(
    *,
    explicit: str | None,
    fallback: str | None,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the WebSocket base URL from the call-site precedence chain.

    Precedence (first non-empty wins):

    1. ``explicit`` — the ``ws_url=`` kwarg passed to ``events()``
    2. ``fallback`` — the ``ws_url`` set on the client constructor
    3. ``CONVILYN_WS_URL`` environment variable

    Raises:
        ValueError: none of the three sources supplied a URL. We do NOT
            try to derive it from the HTTP ``base_url`` — production
            and dev environments have different hostnames (custom domain
            so silent guessing leads to
            confusing connect failures.
    """
    source = env if env is not None else os.environ
    for candidate in (explicit, fallback, source.get(ENV_WS_URL)):
        if candidate:
            _require_wss(candidate)
            return candidate
    raise ValueError(
        "No WebSocket URL configured. Pass `ws_url=...` to events(), set it on "
        "the client constructor, or export the CONVILYN_WS_URL environment "
        "variable. The URL has the form 'wss://<host>' for the hosted API or "
        "'wss://<host>/<stage>' for a self-managed gateway."
    )


_WS_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_wss(ws_url: str) -> None:
    """Reject a ``ws://`` (cleartext) URL that isn't a loopback dev target.

    The auth token is carried on the WebSocket connect; over plain ws it
    would travel in cleartext. wss is required for any non-loopback host.

    Raises:
        ValueError: a non-loopback WS URL uses a scheme other than wss.
    """
    parts = urlsplit(ws_url)
    if parts.scheme.lower() == "wss":
        return
    if (parts.hostname or "").lower() in _WS_LOOPBACK_HOSTS:
        return
    raise ValueError(
        f"Refusing an insecure WebSocket URL (scheme={parts.scheme!r}): the auth "
        "token would be sent in cleartext. Use wss:// (loopback may use ws)."
    )


def build_ws_connect_url(base_url: str, *, token: str) -> str:
    """Append the auth ``?token=`` query param to a WS base URL.

    Kept as a free function so tests can verify the exact wire URL the
    transport will dial without touching network code.
    """
    # The WebSocket endpoint reads ``?token=`` from the connect
    # request — they do not honour Sec-WebSocket-Protocol or custom
    # headers. The token is URL-encoded by ``yarl`` / the underlying
    # client library; we don't pre-encode here because ``ck_`` keys are
    # already URL-safe (base64url alphabet).
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}token={token}"

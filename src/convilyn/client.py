"""Async root client.

Wires up auth + HTTP transport, exposes the lifecycle contract
(``aclose``, async-context-manager), and attaches every resource
accessor: ``client.files``, ``client.convert``, ``client.goals``,
``client.workflows``, ``client.account``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import TracebackType

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

from convilyn._internal.auth import AuthStrategy, resolve_auth
from convilyn._internal.http import DEFAULT_TIMEOUT, HTTPClient, resolve_base_url
from convilyn._internal.resilience import ExponentialBackoffRetry, NoRetry, RetryPolicy
from convilyn._internal.throttle import AutoThrottleArg, resolve_auto_throttle
from convilyn._internal.ws import WebsocketsTransport, WSTransport
from convilyn.resources import (
    AsyncAccount,
    AsyncBuilder,
    AsyncConvert,
    AsyncFiles,
    AsyncGoals,
    AsyncUserWorkflows,
    AsyncWorkflows,
)


class AsyncConvilyn:
    """Asynchronous Convilyn client.

    The async client is the primary surface: every resource method is
    natively async. ``convilyn.Convilyn`` is a thin synchronous wrapper
    around an instance of this class.

    The WebSocket URL (``ws_url``) is a separate knob from ``base_url``
    because production runs the WS API behind a distinct hostname
    (``wss://ws.convilyn.com``); other environments may use a different host.
    Falls back to ``CONVILYN_WS_URL`` if unset; ``events()`` raises a
    clear error if neither source supplied one.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ws_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auth: AuthStrategy | None = None,
        max_retries: int | None = None,
        retry_policy: RetryPolicy | None = None,
        disable_idempotency: bool = False,
        ws_transport_factory: Callable[[], WSTransport] | None = None,
        auto_throttle: AutoThrottleArg = None,
    ) -> None:
        self._auth: AuthStrategy = auth or resolve_auth(api_key)
        resolved_policy = _resolve_retry_policy(max_retries, retry_policy)
        self._http = HTTPClient(
            auth=self._auth,
            base_url=resolve_base_url(base_url),
            timeout=timeout,
            retry_policy=resolved_policy,
            idempotency_enabled=not disable_idempotency,
            auto_throttle=resolve_auto_throttle(auto_throttle),
        )
        self._ws_url = ws_url
        self.files = AsyncFiles(self._http)
        self.convert = AsyncConvert(self._http, self.files)
        self.goals = AsyncGoals(
            self._http,
            ws_url=ws_url,
            ws_transport_factory=ws_transport_factory or WebsocketsTransport,
        )
        self.workflows = AsyncWorkflows(self._http)
        self.user_workflows = AsyncUserWorkflows(self._http)
        self.account = AsyncAccount(self._http)
        self.builder = AsyncBuilder(self._http)

    @property
    def ws_url(self) -> str | None:
        return self._ws_url

    @property
    def base_url(self) -> str:
        return self._http.base_url

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"AsyncConvilyn(base_url={self.base_url!r}, auth={self._auth!r})"


def _resolve_retry_policy(max_retries: int | None, retry_policy: RetryPolicy | None) -> RetryPolicy:
    """Combine the two retry knobs into a single :class:`RetryPolicy` instance.

    Precedence: explicit ``retry_policy`` wins. Otherwise ``max_retries``
    creates an ``ExponentialBackoffRetry`` with that attempt cap;
    ``max_retries=0`` collapses to :class:`NoRetry`. ``None`` for both
    keeps the SDK default (5 attempts with exponential backoff).
    """
    if retry_policy is not None:
        return retry_policy
    if max_retries is None:
        return ExponentialBackoffRetry()
    if max_retries <= 0:
        return NoRetry()
    return ExponentialBackoffRetry(max_attempts=max_retries)

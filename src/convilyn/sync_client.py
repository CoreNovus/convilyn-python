"""Synchronous root client — thin wrapper around :class:`AsyncConvilyn`.

The sync client exists so users who don't want an event loop (CLI
scripts, notebooks, simple jobs) can use Convilyn with a one-liner. It
holds an internal :class:`AsyncConvilyn` and runs every call on one
private, long-lived event loop (:class:`LoopRunner`) so the httpx
connection pool and its final ``aclose()`` share a single loop —
per-call ``asyncio.run`` orphaned pooled connections and crashed at
interpreter teardown on Windows.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import TracebackType

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

from convilyn._internal.auth import AuthStrategy
from convilyn._internal.http import DEFAULT_TIMEOUT
from convilyn._internal.loop_runner import LoopRunner
from convilyn._internal.resilience import RetryPolicy
from convilyn._internal.throttle import AutoThrottleArg
from convilyn._internal.ws import WSTransport
from convilyn.client import AsyncConvilyn
from convilyn.resources import (
    Account,
    Builder,
    Convert,
    Files,
    Goals,
    UserWorkflows,
    Workflows,
)


class Convilyn:
    """Synchronous Convilyn client.

    Mirrors :class:`AsyncConvilyn` but exposes blocking methods. The
    streaming ``events()`` surface is intentionally NOT mirrored here:
    a sync iterator over a WebSocket would need a thread + queue bridge
    that complicates the simplest sync path. Streaming users should
    construct an :class:`AsyncConvilyn` directly; non-streaming sync
    users keep ``client.goals.wait(...)``.
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
        self._async = AsyncConvilyn(
            api_key=api_key,
            base_url=base_url,
            ws_url=ws_url,
            timeout=timeout,
            auth=auth,
            max_retries=max_retries,
            retry_policy=retry_policy,
            disable_idempotency=disable_idempotency,
            ws_transport_factory=ws_transport_factory,
            auto_throttle=auto_throttle,
        )
        self._runner = LoopRunner()
        self.files = Files(self._async.files, run=self._runner.run)
        self.convert = Convert(self._async.convert, run=self._runner.run)
        self.goals = Goals(self._async.goals, run=self._runner.run)
        self.workflows = Workflows(self._async.workflows, run=self._runner.run)
        self.user_workflows = UserWorkflows(self._async.user_workflows, run=self._runner.run)
        self.account = Account(self._async.account, run=self._runner.run)
        self.builder = Builder(self._async.builder, run=self._runner.run)

    @property
    def base_url(self) -> str:
        return self._async.base_url

    @property
    def async_client(self) -> AsyncConvilyn:
        """Escape hatch for callers that need the underlying async client."""
        return self._async

    def close(self) -> None:
        """Close the underlying async client, then the private loop.

        Idempotent — the CLI calls this in a ``finally`` and users may
        also exit the context manager.
        """
        if self._runner.closed:
            return
        try:
            self._runner.run(self._async.aclose())
        finally:
            self._runner.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Convilyn(base_url={self.base_url!r})"

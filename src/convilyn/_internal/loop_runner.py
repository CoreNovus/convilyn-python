"""Private persistent event loop for the synchronous client.

The sync surface used to wrap every call in its own ``asyncio.run``.
That gives each call a fresh loop, but the underlying
``httpx.AsyncClient`` connection pool outlives the call — connections
opened on loop A become orphans once loop A closes, and their
transports touch the dead loop at teardown. On Windows
(``ProactorEventLoop``) this surfaces as
``RuntimeError: Event loop is closed`` when the interpreter exits
. Running every call AND the final ``aclose()`` on one
long-lived private loop removes the orphaned-transport class entirely.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

# Callable signature shared by LoopRunner.run and asyncio.run — the
# sync resource wrappers accept either, so constructing a wrapper
# directly (without a root client) still works.
CoroRunner = Callable[[Coroutine[Any, Any, Any]], Any]

_NESTED_LOOP_MESSAGE = (
    "Synchronous Convilyn methods cannot be called from inside a running "
    "event loop; use AsyncConvilyn from async code instead."
)


class LoopRunner:
    """Owns one private event loop; every coroutine it runs — including
    the client's final ``aclose()`` — executes on that same loop.

    The loop is created lazily on the first call so constructing a
    client (e.g. for ``--dry-run`` or ``repr``) allocates nothing.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coro.close()
            raise RuntimeError(_NESTED_LOOP_MESSAGE)
        if self._closed:
            coro.close()
            raise RuntimeError("Convilyn client is closed.")
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        """Idempotent: shut down async generators, then close the loop."""
        if self._closed:
            return
        self._closed = True
        if self._loop is None:
            return
        try:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        finally:
            self._loop.close()
            self._loop = None

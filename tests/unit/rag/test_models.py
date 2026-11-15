"""`rag._models.call` — calling a caller's model without blocking the event loop."""

from __future__ import annotations

import threading

from convilyn.rag import _models


class TestCall:
    async def test_an_async_method_is_awaited(self) -> None:
        async def answer(value: int) -> int:
            return value + 1

        assert await _models.call(answer, 1) == 2

    async def test_a_synchronous_method_runs_off_the_event_loop_thread(self) -> None:
        caller = threading.get_ident()

        def where() -> int:
            return threading.get_ident()

        assert await _models.call(where) != caller

    async def test_a_synchronous_method_that_returns_an_awaitable_is_awaited(self) -> None:
        async def later() -> str:
            return "done"

        def returns_awaitable() -> object:
            return later()

        assert await _models.call(returns_awaitable) == "done"

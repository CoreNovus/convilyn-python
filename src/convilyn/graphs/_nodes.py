"""How a graph step calls the functions a caller supplied.

A caller may hand over plain functions or ``async`` ones. Each step is written
once, as a coroutine that calls the caller's functions only through the
``call`` it is given, and :func:`node` makes it runnable both ways by choosing
which ``call`` to give it. Nothing is kept anywhere else, so a caller's
function runs exactly as it would outside the graph.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from langchain_core.runnables import RunnableLambda

from convilyn.graphs.errors import GraphError

#: Calls a caller's function with the given arguments and returns its result.
Call = Callable[..., Awaitable[Any]]
Step = Callable[[Any, Call], Coroutine[Any, Any, dict[str, Any]]]

_NEEDS_ASYNC = "this graph was built with an async function; run it with ainvoke or astream"


async def _call_async(function: Callable[..., Any], *arguments: Any) -> Any:
    value = function(*arguments)
    return await value if inspect.isawaitable(value) else value


async def _call_sync(function: Callable[..., Any], *arguments: Any) -> Any:
    """Never waits, so a step awaiting it runs to its end without an event loop.

    An ``async`` function returns a coroutine without running a line of its
    body; that coroutine is closed unstarted and refused.
    """
    value = function(*arguments)
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError(_NEEDS_ASYNC)
    return value


def text(value: object) -> str:
    """``value`` when it is a string; a caller's function returned it otherwise."""
    if not isinstance(value, str):
        raise GraphError("a function this graph was built with must return a string")
    return value


def node(step: Step) -> RunnableLambda[Any, dict[str, Any]]:
    """Wrap ``step`` so the graph can run with ``invoke`` as well as ``ainvoke``."""

    def run(state: Any) -> dict[str, Any]:
        pending = step(state, _call_sync)
        try:
            pending.send(None)
        except StopIteration as finished:
            return finished.value
        finally:
            pending.close()
        raise TypeError(_NEEDS_ASYNC)  # pragma: no cover - a step awaited outside `call`

    async def arun(state: Any) -> dict[str, Any]:
        return await step(state, _call_async)

    return RunnableLambda(run, afunc=arun, name=step.__name__)

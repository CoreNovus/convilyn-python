"""Adapting caller-supplied callbacks that may be sync or async.

A callback is the caller's code, so the SDK does not get to insist it be one
shape or the other: ``run_interactive`` takes ``on_slot`` / ``on_preview``, and
a user writing a script will hand over a plain function while a user inside an
async application will hand over a coroutine function. Both are reasonable and
neither should have to wrap the other.

Lifted out of :mod:`convilyn.resources.goals`, where it lived as a private
helper, when the file-size ratchet asked that module to shed something rather
than grow. It is a relocation, not a new abstraction — no parameter, no
interface, no indirection appeared — and this is the honest home for it: there
is nothing about goals in "await it if it is awaitable".
"""

from __future__ import annotations

import inspect
from typing import Any


async def maybe_await(value: Any) -> Any:
    """Return ``value``, awaiting it first if it is awaitable.

    Lets a resource accept either a sync or an async callback — a sync callback
    returns a plain value; an async one returns a coroutine we await.
    """
    if inspect.isawaitable(value):
        return await value
    return value

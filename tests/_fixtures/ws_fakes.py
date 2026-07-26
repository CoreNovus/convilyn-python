"""Shared WS fakes for goal-lane event-stream tests.

Extracted from ``test_events.py`` so multiple test modules
(``test_events.py``, ``test_cli_goals.py``, …) can reuse the same
deterministic transport + envelope builder without coupling test
files to each other's private helpers.

These are pure helpers, not pytest fixtures — keep them here rather
than in ``conftest.py`` so the import is explicit at the call site.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class FakeWSTransport:
    """Deterministic ``WSTransport`` fake driven by a script.

    Pass a list of recv payloads at construction; ``recv()`` pops them
    in order. After the script is exhausted, ``recv()`` raises
    ``ConnectionError`` so tests for mid-stream drops are exercised by
    simply not pre-loading a terminal frame.

    Set ``raise_interrupt_after`` to force the iterator to raise
    :class:`KeyboardInterrupt` after the N-th ``recv()`` call — used to
    verify SIGINT handling in the CLI's ``events`` command.
    """

    def __init__(
        self,
        script: list[str],
        *,
        fail_on_connect: bool = False,
        raise_interrupt_after: int | None = None,
    ) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        for frame in script:
            self._queue.put_nowait(frame)
        self.fail_on_connect = fail_on_connect
        self.raise_interrupt_after = raise_interrupt_after
        self._recv_count = 0
        self.connected_url: str | None = None
        self.sent: list[str] = []
        self.close_calls = 0

    async def connect(self, url: str) -> None:
        if self.fail_on_connect:
            raise ConnectionRefusedError("simulated connect failure")
        self.connected_url = url

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        self._recv_count += 1
        if self.raise_interrupt_after is not None and self._recv_count > self.raise_interrupt_after:
            raise KeyboardInterrupt
        if self._queue.empty():
            raise ConnectionError("simulated mid-stream close")
        return self._queue.get_nowait()

    async def close(self) -> None:
        self.close_calls += 1


def make_envelope(
    type_: str,
    *,
    seq: int,
    job_spec_id: str = "job_test",
    data: dict[str, Any] | None = None,
) -> str:
    """Build a raw JSON-encoded server-side WS frame.

    Mirrors the wire envelope documented in ``ws_publisher.py`` on the
    backend (``schemaVersion=2``, alias-style camelCase keys).
    """
    return json.dumps(
        {
            "type": type_,
            "schemaVersion": 2,
            "jobSpecId": job_spec_id,
            "emittedAt": "2026-05-20T12:00:00Z",
            "seq": seq,
            "data": data or {},
        }
    )

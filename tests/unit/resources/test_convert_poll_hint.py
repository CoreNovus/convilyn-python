"""Whose cadence wins while waiting — the platform's, or the caller's.

The hint is followed in both directions, because following only the shortening
half would trade a shorter wait for more requests. And it is followed only when
the caller expressed no preference: someone who passed ``poll_interval=5`` did
so to make fewer requests, and spending their quota for them is not the
platform's call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, File
from convilyn.resources.convert import (
    DEFAULT_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

API_BASE = "https://api.convilyn.com"

pytestmark = pytest.mark.asyncio


def _file() -> File:
    return File.model_validate(
        {
            "fileId": "file_xyz",
            "fileName": "report.docx",
            "fileSize": 12345,
            "mimeType": "application/octet-stream",
            "createdAt": "2026-09-14T12:00:00Z",
        }
    )


def _job(status: str, **overrides: Any) -> dict:
    payload = {
        "jobId": "job_test",
        "status": status,
        "processorType": "document_conversion",
        "progress": 0 if status == "queued" else (50 if status == "processing" else 100),
        "params": {"target_format": "pdf"},
        "resultFiles": None,
        "error": None,
        "retryCount": 0,
        "createdAt": "2026-09-14T12:00:00Z",
        "updatedAt": "2026-09-14T12:00:01Z",
    }
    payload.update(overrides)
    return payload


async def _intervals_waited(*, hint_ms: int | None, poll_interval: float | None = None) -> list:
    """Drive one processing poll then a completed one; return the sleeps taken."""
    recorded: list[float] = []

    async def record(seconds: float) -> None:
        recorded.append(seconds)

    processing = _job("processing", suggestedPollIntervalMs=hint_ms)
    async with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE}/api/v1/jobs").mock(
            return_value=httpx.Response(202, json=_job("queued"))
        )
        mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
            side_effect=[
                httpx.Response(200, json=processing),
                httpx.Response(200, json=_job("completed", progress=100)),
            ]
        )
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with patch("convilyn.resources.convert.asyncio.sleep", record):
                await client.convert.create_and_wait(
                    file=_file(),
                    target_format="pdf",
                    **({} if poll_interval is None else {"poll_interval": poll_interval}),
                )
    return recorded


class TestTheHintIsFollowedBothWays:
    async def test_a_short_hint_shortens_the_wait(self) -> None:
        assert await _intervals_waited(hint_ms=300) == [0.3]

    async def test_a_long_hint_lengthens_it(self) -> None:
        """The half that pays for the other one: fewer polls while queued."""
        assert await _intervals_waited(hint_ms=1_500) == [1.5]


class TestBounds:
    async def test_a_hint_below_the_floor_is_clamped(self) -> None:
        assert await _intervals_waited(hint_ms=50) == [MIN_POLL_INTERVAL]

    async def test_a_hint_above_the_ceiling_is_clamped(self) -> None:
        assert await _intervals_waited(hint_ms=60_000) == [MAX_POLL_INTERVAL]


class TestWithoutAHint:
    async def test_an_older_deployment_changes_nothing(self) -> None:
        assert await _intervals_waited(hint_ms=None) == [DEFAULT_POLL_INTERVAL]


class TestTheCallerWins:
    async def test_an_explicit_interval_is_not_overridden(self) -> None:
        """Passing 2 s is usually a request to make fewer requests, not an
        invitation for the server to choose."""
        assert await _intervals_waited(hint_ms=300, poll_interval=2.0) == [2.0]

"""Retry + Idempotency-Key — logic / boundary / error / object-state.

Tests assert on observable transport behaviour (route call counts,
header presence, sleep arguments) so the orchestration can change as
long as the contract holds.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    Convilyn,
    ExponentialBackoffRetry,
    NoRetry,
    RetryExhaustedError,
    RetryPolicy,
)
from convilyn._internal.resilience import (
    MUTATING_METHODS,
    _parse_retry_after,
    generate_idempotency_key,
)

API_BASE = "https://api.convilyn.com"


# ── 1. Logic — happy path retry + idempotency ───────────────────────


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_503_then_200_returns_success(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/test").mock(
                side_effect=[
                    httpx.Response(503, json={"code": "SVR", "message": "down"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    response = await client._http.request("POST", "/api/v1/test", json={})
        assert response.status_code == 200
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_idempotency_key_reused_across_retries(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Idempotency-Key"))
            # 503 first two times, 200 third
            return httpx.Response(503 if len(captured) < 3 else 200, json={"ok": True})

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/test").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    await client._http.request("POST", "/api/v1/test", json={})

        # All three attempts saw the SAME Idempotency-Key (forward-compat for backend dedup)
        assert len(captured) == 3
        assert all(k is not None for k in captured)
        assert captured[0] == captured[1] == captured[2]

    @pytest.mark.asyncio
    async def test_get_does_not_get_idempotency_key(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json={"ok": True})

        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/test").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.request("GET", "/api/v1/test")
        assert captured == [None]

    @pytest.mark.asyncio
    async def test_mutating_methods_all_get_key(self) -> None:
        # Sanity check the spec — every mutating verb gets a key.
        assert MUTATING_METHODS == frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ── 2. Boundary — limits + server hints ─────────────────────────────


class TestRetryBoundary:
    @pytest.mark.asyncio
    async def test_max_retries_zero_raises_immediately(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/test").mock(
                return_value=httpx.Response(503, json={"code": "X", "message": "x"})
            )
            async with AsyncConvilyn(
                api_key="ck_test",
                max_retries=0,  # pragma: allowlist secret
            ) as client:
                with pytest.raises(APIError):
                    await client._http.request("POST", "/api/v1/test", json={})
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_after_max_attempts(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/test").mock(
                return_value=httpx.Response(503, json={"code": "X", "message": "x"})
            )
            async with AsyncConvilyn(
                api_key="ck_test",
                max_retries=2,  # pragma: allowlist secret
            ) as client:
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    with pytest.raises(RetryExhaustedError) as info:
                        await client._http.request("POST", "/api/v1/test", json={})
        # 2 retries means 3 total attempts (initial + 2)
        assert route.call_count == 3
        assert info.value.attempt_count == 3

    @pytest.mark.asyncio
    async def test_retry_after_header_overrides_backoff(self) -> None:
        delays: list[float] = []

        async def _record_sleep(d: float) -> None:
            delays.append(d)

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/test").mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "7"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn._internal.http.asyncio.sleep", new=_record_sleep):
                    await client._http.request("POST", "/api/v1/test", json={})
        # The single sleep should equal the Retry-After value (no jitter applied).
        assert delays == [7.0]

    def test_parse_retry_after_handles_garbage(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "not-a-number"})
        assert _parse_retry_after(resp) is None


# ── 3. Error — non-retryable codes + network exceptions ─────────────


class TestRetryErrors:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_4xx_not_retried(self, status: int) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/test").mock(
                return_value=httpx.Response(status, json={"code": "X", "message": "x"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError):
                    await client._http.request("POST", "/api/v1/test", json={})
        # Only one attempt — no retry for client-side bugs.
        assert route.call_count == 1

    def test_policy_does_not_retry_transport_exceptions(self) -> None:
        # Conservative default — a network failure on POST may have reached
        # the server, so the SDK does not retry by default.
        policy = ExponentialBackoffRetry()
        assert policy.should_retry(None, httpx.ConnectError("boom"), attempt=0) is False

    def test_jitter_stays_within_range(self) -> None:
        policy = ExponentialBackoffRetry(base_delay=1.0, max_delay=10.0, jitter=0.25)
        for attempt in range(5):
            for _ in range(20):
                delay = policy.next_delay(None, attempt)
                exponential = min(1.0 * (2**attempt), 10.0)
                # ±25% around the exponential value
                assert exponential * 0.75 <= delay <= exponential * 1.25 + 1e-9


# ── 4. Object-state — config knobs are respected ────────────────────


class TestRetryObjectState:
    @pytest.mark.asyncio
    async def test_disable_idempotency_omits_header(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json={"ok": True})

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/test").mock(side_effect=_capture)
            async with AsyncConvilyn(
                api_key="ck_test",  # pragma: allowlist secret
                disable_idempotency=True,
            ) as client:
                await client._http.request("POST", "/api/v1/test", json={})
        assert captured == [None]

    @pytest.mark.asyncio
    async def test_user_provided_idempotency_key_respected(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json={"ok": True})

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/test").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.request(
                    "POST", "/api/v1/test", json={}, headers={"Idempotency-Key": "user-supplied"}
                )
        assert captured == ["user-supplied"]

    def test_custom_retry_policy_replaces_default(self) -> None:
        class AlwaysOnce(RetryPolicy):
            def should_retry(self, response, exc, attempt) -> bool:
                return False

            def next_delay(self, response, attempt) -> float:
                return 0.0

        client = Convilyn(api_key="ck_test", retry_policy=AlwaysOnce())  # pragma: allowlist secret
        try:
            assert isinstance(client._async._http._retry_policy, AlwaysOnce)
        finally:
            client.close()

    def test_no_retry_policy_short_circuits(self) -> None:
        policy = NoRetry()
        assert policy.should_retry(httpx.Response(503), None, 0) is False
        assert policy.next_delay(httpx.Response(503), 0) == 0.0

    def test_generate_idempotency_key_is_unique(self) -> None:
        keys = {generate_idempotency_key() for _ in range(100)}
        assert len(keys) == 100  # vanishingly low collision odds for UUID4

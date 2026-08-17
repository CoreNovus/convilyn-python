"""HTTPClient + auto_throttle integration — wire behaviour, not pure decisions.

Pairs with :mod:`tests.unit._internal.test_throttle` (pure functions).
This file proves the transport actually calls the throttle policy and
re-issues the request when the policy says to.

Sleep is patched so the suite never blocks on real time. respx is used
to mock the wire response and observe call counts.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, AutoThrottleConfig, QuotaExceededError

API_BASE = "https://api.convilyn.corenovus.com"


def _quota_exceeded_payload(retry_after_seconds: float | None = None) -> dict:
    details: dict = {}
    if retry_after_seconds is not None:
        details["retry_after_seconds"] = retry_after_seconds
    return {
        "detail": {
            "code": "QUOTA_EXCEEDED",
            "message": "Monthly cap reached",
            "details": details,
        }
    }


# ── 1. Logic — auto_throttle retries once after a 402 QUOTA_EXCEEDED ──


class TestAutoThrottleLogic:
    @pytest.mark.asyncio
    async def test_retry_once_then_success(self) -> None:
        responses = [
            httpx.Response(402, json=_quota_exceeded_payload(retry_after_seconds=1)),
            httpx.Response(200, json={"tier": "free"}),
        ]
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                side_effect=responses
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
                auto_throttle=True,
            ) as client:
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    response = await client._http.request(
                        "POST",
                        "/api/v1/workflows/cost-preview",
                        json={"toolNames": []},
                    )
        assert response.status_code == 200
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_disabled_by_default_no_retry(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(402, json=_quota_exceeded_payload())
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
            ) as client:
                with pytest.raises(QuotaExceededError):
                    await client._http.request(
                        "POST",
                        "/api/v1/workflows/cost-preview",
                        json={"toolNames": []},
                    )
        assert route.call_count == 1


# ── 2. Boundary — max_retries cap, second 402 raises ──────────────


class TestAutoThrottleBoundary:
    @pytest.mark.asyncio
    async def test_second_quota_exceeded_raises(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(
                    402, json=_quota_exceeded_payload(retry_after_seconds=1)
                )
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
                auto_throttle=True,
            ) as client:
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    with pytest.raises(QuotaExceededError):
                        await client._http.request(
                            "POST",
                            "/api/v1/workflows/cost-preview",
                            json={"toolNames": []},
                        )
        # max_retries=1 (default) → 1 initial + 1 retry = 2 total
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_sleep_over_cap_skips_retry(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(
                    402,
                    json=_quota_exceeded_payload(retry_after_seconds=999.0),
                )
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
                auto_throttle=AutoThrottleConfig(max_sleep=5.0),
            ) as client:
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    with pytest.raises(QuotaExceededError):
                        await client._http.request(
                            "POST",
                            "/api/v1/workflows/cost-preview",
                            json={"toolNames": []},
                        )
        # Server's hint exceeds cap → no retry attempted.
        assert route.call_count == 1


# ── 3. Error — non-QuotaExceededError errors still raise unchanged ──


class TestAutoThrottleError:
    @pytest.mark.asyncio
    async def test_plan_required_not_retried(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(
                    402,
                    json={
                        "detail": {
                            "code": "PRO_TIER_REQUIRED",
                            "message": "Upgrade",
                        }
                    },
                )
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
                auto_throttle=True,
            ) as client:
                from convilyn import PlanRequiredError

                with pytest.raises(PlanRequiredError):
                    await client._http.request(
                        "POST",
                        "/api/v1/workflows/cost-preview",
                        json={"toolNames": []},
                    )
        assert route.call_count == 1


# ── 4. Object-state — soft_limit header emits warning, request returns ──


class TestSoftLimitHeader:
    @pytest.mark.asyncio
    async def test_soft_limit_header_emits_warning_without_blocking(self) -> None:
        import warnings as _warnings

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(
                    200,
                    json={"tier": "pro"},
                    headers={"X-Quota-State": "soft_limit"},
                )
            )
            async with AsyncConvilyn(
                api_key="ck_t",  # pragma: allowlist secret
                base_url=API_BASE,
                max_retries=0,
            ) as client:
                with _warnings.catch_warnings(record=True) as captured:
                    _warnings.simplefilter("always")
                    response = await client._http.request(
                        "POST",
                        "/api/v1/workflows/cost-preview",
                        json={"toolNames": []},
                    )
        assert response.status_code == 200
        assert any("soft_limit" in str(w.message) for w in captured)

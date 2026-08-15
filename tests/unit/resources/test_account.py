"""Account resource + billing-aware exception dispatch — 4-category tests.

Two seams under test:
* :func:`convilyn._internal.http._decode_error` — translates the wire
  envelope into the right typed exception.
* :class:`convilyn.resources.account.AsyncAccount` — call shape +
  response parsing.

CLI behaviour lives in :mod:`tests.test_cli_account`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    CostEstimate,
    Plan,
    PlanRequiredError,
    QuotaExceededError,
    RateLimitError,
    UsageHistoryEntry,
)

API_BASE = "https://api.convilyn.corenovus.com"


def _cost_preview_wire(
    *,
    tier: str = "free",
    state: str = "ok",
    estimated_micro_u: int = 100,
    threshold_micro_u: int = 1_000_000,
    upgrade_url: str | None = None,
) -> dict:
    return {
        "estimatedMicroU": estimated_micro_u,
        "estimatedUsd": 0.01,
        "estimatedTotalMicroU": estimated_micro_u,
        "estimatedMinMicroU": estimated_micro_u // 25 if estimated_micro_u else 0,
        "estimatedMaxMicroU": estimated_micro_u,
        "tools": [],
        "quotaCheck": {
            "state": state,
            "tier": tier,
            "estimatedMicroU": estimated_micro_u,
            "thresholdMicroU": threshold_micro_u,
            "upgradeUrl": upgrade_url,
        },
    }


@pytest.fixture
def client() -> AsyncConvilyn:
    return AsyncConvilyn(
        api_key="ck_test_account",  # pragma: allowlist secret
        base_url=API_BASE,
    )


# ── 1. Logic — happy path for get_quota + get_plan ─────────────────


class TestAccountLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_quota_returns_cost_estimate(self, client: AsyncConvilyn) -> None:
        route = respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                200, json=_cost_preview_wire(tier="pro", estimated_micro_u=50_000)
            )
        )
        estimate = await client.account.get_quota(tools=["pdf-mcp:extract_text"], max_iterations=10)
        assert isinstance(estimate, CostEstimate)
        assert estimate.estimated_micro_u == 50_000
        assert estimate.quota_check.tier == "pro"
        assert estimate.quota_check.state == "ok"
        # Verify body shape sent to backend.
        import json

        body = json.loads(route.calls[0].request.content)
        assert body == {"toolNames": ["pdf-mcp:extract_text"], "maxIterations": 10}

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_plan_derives_tier_from_cost_preview(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(200, json=_cost_preview_wire(tier="pro"))
        )
        plan = await client.account.get_plan()
        assert isinstance(plan, Plan)
        assert plan.tier == "pro"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_plan_supports_business_tier(self, client: AsyncConvilyn) -> None:
        """Regression: a business-tier caller must round-trip, not raise a
        validation error (PlanTier previously omitted "business")."""
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(200, json=_cost_preview_wire(tier="business"))
        )
        plan = await client.account.get_plan()
        assert plan.tier == "business"


# ── 2. Boundary — empty tools, quota soft-limit verdict ────────────


class TestAccountBoundary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_quota_no_tools_sends_empty_list(self, client: AsyncConvilyn) -> None:
        import json

        route = respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(200, json=_cost_preview_wire())
        )
        await client.account.get_quota()
        body = json.loads(route.calls[0].request.content)
        assert body == {"toolNames": []}

    @pytest.mark.asyncio
    @respx.mock
    async def test_soft_limit_state_surfaces_through_quota_check(
        self, client: AsyncConvilyn
    ) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                200,
                json=_cost_preview_wire(
                    tier="pro",
                    state="soft_limit",
                    estimated_micro_u=900_000,
                    upgrade_url="/billing/top-up",
                ),
            )
        )
        estimate = await client.account.get_quota()
        assert estimate.quota_check.state == "soft_limit"
        assert estimate.quota_check.upgrade_url == "/billing/top-up"


# ── 3. Error — exception dispatcher routes 402 codes to typed exceptions ──


class TestExceptionDispatch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_402_tier_required_raises_plan_required_error(
        self, client: AsyncConvilyn
    ) -> None:
        # FastAPI default envelope (detail-wrapped).
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={
                    "detail": {
                        "code": "TIER_REQUIRED",
                        "message": "Pro tier required for this action",
                        "upgrade_url": "/pricing",
                    }
                },
            )
        )
        with pytest.raises(PlanRequiredError) as exc_info:
            await client.account.get_quota()
        assert exc_info.value.status_code == 402
        assert exc_info.value.code == "TIER_REQUIRED"
        assert exc_info.value.upgrade_url == "/pricing"
        # Subclass relationship preserved — APIError catches it too.
        assert isinstance(exc_info.value, APIError)

    @pytest.mark.asyncio
    @respx.mock
    async def test_402_pro_tier_required_alias_raises_plan_required(
        self, client: AsyncConvilyn
    ) -> None:
        """Legacy code variant ``PRO_TIER_REQUIRED`` routes to the same exception."""
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={
                    "detail": {
                        "code": "PRO_TIER_REQUIRED",
                        "message": "Upgrade to Pro",
                    }
                },
            )
        )
        with pytest.raises(PlanRequiredError):
            await client.account.get_quota()

    @pytest.mark.asyncio
    @respx.mock
    async def test_402_quota_exceeded_raises_quota_exceeded(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={
                    "detail": {
                        "code": "QUOTA_EXCEEDED",
                        "message": "Monthly cap reached",
                        "estimatedMicroU": 1_200_000,
                        "thresholdMicroU": 1_000_000,
                        "upgrade_url": "/billing/top-up",
                    }
                },
            )
        )
        with pytest.raises(QuotaExceededError) as exc_info:
            await client.account.get_quota()
        assert exc_info.value.status_code == 402
        assert exc_info.value.estimated_micro_u == 1_200_000
        assert exc_info.value.threshold_micro_u == 1_000_000
        assert exc_info.value.upgrade_url == "/billing/top-up"

    @pytest.mark.asyncio
    @respx.mock
    async def test_402_unknown_code_falls_back_to_api_error(self, client: AsyncConvilyn) -> None:
        """Forward-compat: a new 402 code the SDK doesn't know about
        falls through to the base :class:`APIError`. Catches future
        backend additions without forcing an SDK release."""
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={"detail": {"code": "FUTURE_NEW_CODE", "message": "..."}},
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.account.get_quota()
        # Must NOT be one of the typed subclasses.
        assert not isinstance(exc_info.value, PlanRequiredError)
        assert not isinstance(exc_info.value, QuotaExceededError)
        assert exc_info.value.code == "FUTURE_NEW_CODE"

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_still_routes_to_rate_limit_error(self) -> None:
        """Regression guard: the new 402 dispatch must not steal 429.

        Uses a no-retry client so we exercise the first-response dispatch
        path directly rather than the retry-exhausted wrapping (which
        subclasses APIError as ``RetryExhaustedError``).
        """
        no_retry_client = AsyncConvilyn(
            api_key="ck_test_account",  # pragma: allowlist secret
            base_url=API_BASE,
            max_retries=0,
        )
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                429,
                json={"detail": {"code": "RATE_LIMITED", "message": "Slow down"}},
            )
        )
        try:
            with pytest.raises(RateLimitError):
                await no_retry_client.account.get_quota()
        finally:
            await no_retry_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_envelope_normalisation_handles_flat_shape(
        self, client: AsyncConvilyn
    ) -> None:
        """Backwards-compat: legacy endpoints emitting flat ``{code, message}``
        still dispatch correctly."""
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={"code": "TIER_REQUIRED", "message": "Upgrade"},
            )
        )
        with pytest.raises(PlanRequiredError):
            await client.account.get_quota()

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_unrecognised_envelope_degrades_to_the_status_code(
        self, client: AsyncConvilyn
    ) -> None:
        """The falsifiable half of dropping the ``{"error": {...}}`` branch.

        The flattener carried a third branch that unwrapped a top-level
        ``error`` dict, documented as "older endpoints". Nothing produces that
        shape: across ``docs/contracts/**`` every declared error body is flat
        or ``detail``-wrapped, and the only object-valued ``error`` keys are
        ``ConvertJobResponse.error`` / ``JobResponse.error`` — fields on **200**
        job-status bodies, which this path never sees because it runs only on
        non-2xx.

        What the removal must not do is crash. An envelope the SDK does not
        recognise is reported with the status-derived code, so a caller still
        gets a typed error carrying the HTTP status rather than a ``KeyError``
        or a code invented from an arbitrary sub-dict.
        """
        respx.post(f"{API_BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(
                402,
                json={"error": {"code": "QUOTA_EXCEEDED", "message": "."}},
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.account.get_quota()

        assert exc_info.value.code == "HTTP_402"


# ── 4. Object-state — model round-trips, sync facade, extra="allow" ──


class TestAccountObjectState:
    def test_cost_estimate_model_round_trip(self) -> None:
        estimate = CostEstimate.model_validate(_cost_preview_wire(tier="pro"))
        assert estimate.estimated_micro_u == 100
        assert estimate.quota_check.tier == "pro"
        # Frozen — mutation rejected.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            estimate.estimated_micro_u = 999  # type: ignore[misc]

    def test_quota_check_extra_field_tolerated(self) -> None:
        """OCP: a forward-compat field added by the backend round-trips."""
        wire = _cost_preview_wire()
        wire["quotaCheck"]["futureField"] = {"foo": "bar"}
        estimate = CostEstimate.model_validate(wire)
        dumped = estimate.model_dump(mode="json", by_alias=True)
        assert dumped["quotaCheck"]["futureField"] == {"foo": "bar"}

    def test_sync_account_attached_to_convilyn(self) -> None:
        """The sync :class:`Convilyn` exposes the same ``client.account`` surface."""
        from convilyn import Convilyn
        from convilyn.resources.account import Account

        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        assert isinstance(sync_client.account, Account)
        sync_client.close()


# ── 5. usage_history — 4-category coverage for the new R7 method ──


def _usage_row(
    *,
    metric: str = "goal_lane_request",
    period_start: str = "2026-04-01T00:00:00+00:00",
    period_end: str = "2026-05-01T00:00:00+00:00",
    used: int = 1,
    limit: int | None = 5,
) -> dict:
    return {
        "metric": metric,
        "period_start": period_start,
        "period_end": period_end,
        "used": used,
        "limit": limit,
    }


class TestUsageHistoryLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_typed_entries(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/payment/usage/history").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _usage_row(metric="goal_lane_request", used=2),
                    _usage_row(metric="ai_tool_call", used=15, limit=30),
                ],
            )
        )
        entries = await client.account.usage_history()
        assert len(entries) == 2
        assert all(isinstance(e, UsageHistoryEntry) for e in entries)
        assert entries[0].metric == "goal_lane_request"
        assert entries[0].used == 2
        assert entries[1].limit == 30

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_history_yields_empty_list(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/payment/usage/history").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert await client.account.usage_history() == []


class TestUsageHistoryBoundary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_since_filter_keeps_only_recent_periods(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/payment/usage/history").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _usage_row(period_start="2026-01-01T00:00:00+00:00"),
                    _usage_row(period_start="2026-04-01T00:00:00+00:00"),
                ],
            )
        )
        cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        entries = await client.account.usage_history(since=cutoff)
        assert len(entries) == 1
        assert entries[0].period_start.year == 2026
        assert entries[0].period_start.month == 4

    @pytest.mark.asyncio
    @respx.mock
    async def test_unlimited_paid_metric_carries_none_limit(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/payment/usage/history").mock(
            return_value=httpx.Response(200, json=[_usage_row(limit=None)])
        )
        entries = await client.account.usage_history()
        assert entries[0].limit is None


class TestUsageHistoryErrors:
    @pytest.mark.asyncio
    @respx.mock
    async def test_401_raises_api_error(self) -> None:
        unauth_client = AsyncConvilyn(
            api_key="ck_test_account",  # pragma: allowlist secret
            base_url=API_BASE,
            max_retries=0,
        )
        respx.get(f"{API_BASE}/api/v1/payment/usage/history").mock(
            return_value=httpx.Response(
                401, json={"detail": {"code": "UNAUTHORIZED", "message": "Sign in"}}
            )
        )
        try:
            with pytest.raises(APIError) as exc_info:
                await unauth_client.account.usage_history()
            assert exc_info.value.status_code == 401
        finally:
            await unauth_client.aclose()


class TestUsageHistoryObjectState:
    def test_entry_is_frozen(self) -> None:
        entry = UsageHistoryEntry.model_validate(_usage_row())
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            entry.used = 999  # type: ignore[misc]

    def test_sync_facade_exposes_usage_history(self) -> None:
        from convilyn import Convilyn

        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        assert hasattr(sync_client.account, "usage_history")
        sync_client.close()

"""Account resource — caller-side billing tier + quota queries.

Attached to :class:`convilyn.AsyncConvilyn` as ``client.account``.
Exposes three read-only verbs:

* :meth:`AsyncAccount.get_plan`       — what tier am I on right now?
* :meth:`AsyncAccount.get_quota`      — would a workflow with these tools fit
                                         my plan's monthly cost cap?
* :meth:`AsyncAccount.usage_history`  — past usage periods for billing
                                         retrospectives + MTD breakdowns.

``get_plan`` / ``get_quota`` call the same backend endpoint today
(``POST /api/v1/workflows/cost-preview``); when a dedicated
``/billing/plan`` endpoint lands, :meth:`get_plan` will switch
transparently. ``usage_history`` calls
``GET /api/v1/payment/usage/history`` directly.

Pure-data return — actions (upgrade, top-up) belong on the website,
not in the SDK.

Design follows the OpenAI / Stripe convention used throughout the rest
of the SDK: behaviour on the resource, data on the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.types import CostEstimate, Plan, UsageHistoryEntry


class AsyncAccount:
    """Asynchronous account-tier resource.

    Constructor takes the shared :class:`HTTPClient` so the resource
    inherits auth, retry, idempotency, and base-URL handling for free.
    DIP-aligned — the resource depends on the HTTPClient Protocol, not
    on ``httpx`` directly.
    """

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    # ── Public API ───────────────────────────────────────────────

    async def get_quota(
        self,
        *,
        tools: Sequence[str] | None = None,
        max_iterations: int | None = None,
    ) -> CostEstimate:
        """Estimate workflow cost + check the verdict against my tier.

        Read-only: the call has no side effects. Authenticated callers
        only; the backend rejects anonymous requests with 401.

        Args:
            tools: Fully-qualified tool ids (e.g. ``"pdf-mcp:extract_text"``).
                Empty list ⇒ zero-cost estimate, useful when you just
                want the tier signal.
            max_iterations: Iteration cap from the draft spec. Defaults
                to the backend's ``DEFAULT_MAX_ITERATIONS`` when None.

        Returns:
            :class:`CostEstimate` with both the cost breakdown and a
            :attr:`CostEstimate.quota_check` verdict. The verdict's
            ``state`` is ``"ok"`` / ``"soft_limit"`` / ``"quota_exceeded"``.

        Raises:
            convilyn.APIError: server errors. Auth failures raise the
                base :class:`APIError`; tier mismatches on Pro-only
                endpoints raise :class:`convilyn.PlanRequiredError`
                (subclass), but ``cost-preview`` itself is not gated.
        """
        body: dict[str, Any] = {"toolNames": list(tools or [])}
        if max_iterations is not None:
            body["maxIterations"] = max_iterations
        response = await self._http.request(
            "POST", "/api/v1/workflows/cost-preview", json=body
        )
        return CostEstimate.model_validate(response.json())

    async def get_plan(self) -> Plan:
        """Return the caller's current billing tier.

        Today this is a thin convenience over :meth:`get_quota` (it's
        the only existing endpoint that returns the tier alongside a
        public-readable response). When a dedicated ``/billing/plan``
        endpoint ships, this method will switch transparently — the
        :class:`Plan` model is ``extra="allow"``, so new fields surface
        without breaking callers.
        """
        estimate = await self.get_quota(tools=[], max_iterations=1)
        return Plan(tier=estimate.quota_check.tier)

    async def usage_history(
        self,
        *,
        since: datetime | None = None,
    ) -> list[UsageHistoryEntry]:
        """List the caller's past usage periods (one row per metric+period).

        Wraps ``GET /api/v1/payment/usage/history``. The backend returns
        every period the caller has ever owned — Free monthly resets,
        Pro subscription cycles, top-up windows. Use ``since`` to
        restrict to entries whose ``period_start`` is on or after the
        given timestamp; the default ``None`` returns the full history.

        ``since`` filtering happens client-side because the backend does
        not currently honour a query param — the SDK does the work so
        callers don't need to reach for slicing primitives.

        Returns:
            A list of :class:`UsageHistoryEntry`, preserving the
            server's ordering (newest period first per the backend's
            ``get_periods_by_identity`` contract).

        Raises:
            convilyn.APIError: server errors (incl. 401 when the caller
                is anonymous — this endpoint is authenticated-only).
        """
        response = await self._http.request(
            "GET", "/api/v1/payment/usage/history"
        )
        rows = response.json()
        entries = [UsageHistoryEntry.model_validate(row) for row in rows]
        if since is None:
            return entries
        return [e for e in entries if e.period_start >= since]


class Account:
    """Synchronous facade around :class:`AsyncAccount`.

    Mirrors the async surface 1:1; each call runs the underlying
    coroutine via the injected runner (the root sync client's shared
    private loop). Same restriction as
    :class:`convilyn.resources.workflows.Workflows` — switch to
    :class:`convilyn.AsyncConvilyn` for high-throughput sync callers.
    """

    def __init__(
        self, async_account: AsyncAccount, run: CoroRunner | None = None
    ) -> None:
        self._async = async_account
        self._run: CoroRunner = run if run is not None else asyncio.run

    def get_quota(self, **kwargs: Any) -> CostEstimate:
        return self._run(self._async.get_quota(**kwargs))

    def get_plan(self) -> Plan:
        return self._run(self._async.get_plan())

    def usage_history(
        self, *, since: datetime | None = None
    ) -> list[UsageHistoryEntry]:
        return self._run(self._async.usage_history(since=since))

"""Account resource — caller-side billing tier + quota queries.

Attached to :class:`convilyn.AsyncConvilyn` as ``client.account``.
Exposes three read-only verbs:

* :meth:`AsyncAccount.get_plan`       — what tier am I on right now?
* :meth:`AsyncAccount.get_quota`      — would a workflow with these tools fit
                                         my plan's monthly cost cap?
* :meth:`AsyncAccount.usage_history`  — past usage periods for billing
                                         retrospectives + MTD breakdowns.
* :meth:`AsyncAccount.get_balance`    — how many credits do I have left?

``get_plan`` / ``get_quota`` both call ``POST /api/v1/workflows/cost-preview``.
That is the canonical tier source for the SDK data plane: it is the endpoint
that accepts the consumer ``ck_`` key (via ``get_current_user_or_api_key``) and
returns the caller's tier in ``quotaCheck.tier``. (The web app reads
``GET /api/v1/payment/subscription`` for the current plan, but that route is
JWT/session-only and rejects ``ck_`` keys, so the SDK cannot use it.)
``usage_history`` calls ``GET /api/v1/payment/usage/history`` directly, and
``get_balance`` calls ``GET /api/v1/credits/balance``.

Those two answer different questions and neither substitutes for the other:
``usage_history`` returns run COUNTS for quota metrics — the credits period is
not in its tracked set, so it carries no balance row at all. Before the balance
route shipped, the only ``ck_``-reachable balance was a side effect of
quoting a workflow the caller did not intend to run, which is a side door, not
an API.

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
from convilyn.types import CostEstimate, CreditBalance, Plan, UsageHistoryEntry


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

        This estimates a **tool palette** for the chat Builder, in insured
        (pre-margin) µU. It is NOT the price of a workflow and is NOT comparable
        to a credit balance — see :class:`~convilyn.CostEstimate` for why, and
        for the endpoint that answers that question instead.

        Args:
            tools: Fully-qualified tool ids (e.g. ``"pdf-mcp:extract_text"``).
                An empty list is **not** a zero-cost estimate — this said so
                for months and it was never true. The estimator has no zero
                branch: the per-iteration LLM cost is unconditional and is
                multiplied by the full iteration cap, so ``tools=[]`` with the
                default cap returns the flat per-iteration cost × that cap
                (US$1.00 at the backend's current constants). If you want only
                the tier signal, use :meth:`get_plan`, which passes
                ``max_iterations=1`` and is 20× cheaper to read.
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
        response = await self._http.request("POST", "/api/v1/workflows/cost-preview", json=body)
        return CostEstimate.model_validate(response.json())

    async def get_plan(self) -> Plan:
        """Return the caller's current billing tier.

        Thin convenience over :meth:`get_quota`: ``cost-preview`` is the
        SDK's canonical tier source (the ``ck_``-accepting endpoint that
        returns ``quotaCheck.tier``). The tier may be ``"free"``,
        ``"pro"``, or ``"business"`` — all valid :data:`convilyn.types.PlanTier`
        values. The :class:`Plan` model is ``extra="allow"``, so any future
        fields surface without breaking callers.
        """
        estimate = await self.get_quota(tools=[], max_iterations=1)
        return Plan(tier=estimate.quota_check.tier)

    async def usage_history(
        self,
        *,
        since: datetime | None = None,
    ) -> list[UsageHistoryEntry]:
        """List the caller's past usage periods (one row per metric+period).

        Wraps ``GET /api/v1/payment/usage/history``. Rows are run COUNTS
        for the quota metrics the authenticated side tracks — Free
        monthly resets, Pro subscription cycles. **Not credits:** the
        credits period is never in that set, so no row here reports
        spend. :py:meth:`get_balance` is the credits question.

        **At most 50 rows, newest first, and there is no cursor.** Use
        ``since`` to restrict to entries whose ``period_start`` is on or
        after the given timestamp; the default ``None`` returns whatever
        the server sent, which is not necessarily everything the account
        has ever owned. Receiving exactly 50 means older periods exist.

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
        response = await self._http.request("GET", "/api/v1/payment/usage/history")
        rows = response.json()
        entries = [UsageHistoryEntry.model_validate(row) for row in rows]
        if since is None:
            return entries
        return [e for e in entries if e.period_start >= since]

    async def get_balance(self) -> CreditBalance:
        """How many credits does this account have left?

        Wraps ``GET /api/v1/credits/balance``. Read
        :py:attr:`~convilyn.CreditBalance.balance_credits` — the two-bucket
        TOTAL; the ``period`` / ``topup`` split is exposed because the buckets
        behave differently at renewal, not because a caller should add them up.

        **Not comparable to :meth:`get_quota`.** This docstring used to say
        "when comparing against a quote from :meth:`get_quota`", and that
        comparison is wrong in unit (credits vs µU), in margin (charged vs
        insured) and in subject (a workflow vs a Builder tool palette). To ask
        "can I afford this workflow", call ``POST /credits/workflow-quote``,
        which returns ``costCredits``, ``balanceCredits`` and a server-computed
        ``sufficient`` — one unit, one round-trip. The SDK does not wrap it yet.

        Read-only, like the rest of this resource: topping up is a website
        action, not an SDK one. Note that ``/credits/ledger`` is JWT-only, so a
        key-authenticated caller can read this balance but cannot itemise how it
        was spent.

        Returns:
            A :class:`~convilyn.CreditBalance`.

        Raises:
            convilyn.APIError: server errors, including 401 when the caller is
                anonymous — this endpoint is authenticated-only.
        """
        response = await self._http.request("GET", "/api/v1/credits/balance")
        return CreditBalance.model_validate(response.json())


class Account:
    """Synchronous facade around :class:`AsyncAccount`.

    Mirrors the async surface 1:1; each call runs the underlying
    coroutine via the injected runner (the root sync client's shared
    private loop). Same restriction as
    :class:`convilyn.resources.workflows.Workflows` — switch to
    :class:`convilyn.AsyncConvilyn` for high-throughput sync callers.
    """

    def __init__(self, async_account: AsyncAccount, run: CoroRunner | None = None) -> None:
        self._async = async_account
        self._run: CoroRunner = run if run is not None else asyncio.run

    def get_quota(self, **kwargs: Any) -> CostEstimate:
        return self._run(self._async.get_quota(**kwargs))

    def get_plan(self) -> Plan:
        return self._run(self._async.get_plan())

    def get_balance(self) -> CreditBalance:
        return self._run(self._async.get_balance())

    def usage_history(self, *, since: datetime | None = None) -> list[UsageHistoryEntry]:
        return self._run(self._async.usage_history(since=since))

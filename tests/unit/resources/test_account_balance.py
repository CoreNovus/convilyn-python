"""``account.get_balance()`` — the balance a ``ck_`` key could not ask for.

Round-6 external testing reported the SDK as having no way to read a credit
balance. The backend route shipped with #4079; what was missing was this method.

The two account reads answer different questions and neither substitutes for the
other: ``usage_history()`` returns run COUNTS for quota metrics and carries no
balance row at all, because the credits period is not in its tracked set. Before
the route existed the only ``ck_``-reachable balance was a side effect of
quoting a workflow the caller did not intend to run.

Categories: logic (the call shape and the two-bucket parse); boundary (absent
optional buckets, and a Free-tier fractional field); error (the server rejecting the
credential); object-state (the model is frozen, like every other wire type here).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn, CreditBalance

API_BASE = "https://api.convilyn.com"


def _balance_wire(**overrides: object) -> dict[str, object]:
    wire: dict[str, object] = {
        "balanceCredits": 1_250,
        "periodCredits": 1_000,
        "topupCredits": 250,
        "periodEnd": "2026-09-01T00:00:00Z",
        "lastGrantAt": "2026-08-01T00:00:00Z",
        "freeTierMonthSpentCredits": None,
    }
    wire.update(overrides)
    return wire


@pytest.fixture
async def client():
    c = AsyncConvilyn(api_key="ck_test", base_url=API_BASE)  # pragma: allowlist secret
    try:
        yield c
    finally:
        await c.aclose()


class TestLogic:
    @respx.mock
    async def test_it_calls_the_balance_route(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(200, json=_balance_wire())
        )

        await client.account.get_balance()

        assert route.called

    @respx.mock
    async def test_the_total_is_what_it_says(self, client: AsyncConvilyn) -> None:
        """``balanceCredits`` is the authoritative number — it is served as the
        total, not derived here from the two buckets. A client that added them
        up would be re-implementing a sum the server already did, and would
        disagree with it the moment a third bucket exists."""
        respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(200, json=_balance_wire(balanceCredits=1_250))
        )

        assert (await client.account.get_balance()).balance_credits == 1250

    @respx.mock
    async def test_the_two_buckets_survive_the_parse(self, client: AsyncConvilyn) -> None:
        """One structural compare rather than two asserts: the split is exposed
        because the buckets behave differently at renewal, so both must arrive."""
        respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(200, json=_balance_wire())
        )

        balance = await client.account.get_balance()

        assert (balance.period_credits, balance.topup_credits) == (1000, 250)


class TestBoundary:
    @respx.mock
    async def test_absent_buckets_are_none_not_zero(self, client: AsyncConvilyn) -> None:
        """The distinction this SDK already draws for
        ``InsufficientCreditsError``'s operands: *unknown, never zero*. A caller
        told ``topup_credits == 0`` would believe their wallet is empty."""
        respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(
                200,
                json={"balanceCredits": 7, "periodEnd": "2026-09-01T00:00:00Z"},
            )
        )

        balance = await client.account.get_balance()

        assert (balance.period_credits, balance.topup_credits) == (None, None)

    @respx.mock
    async def test_the_free_cap_counter_keeps_its_fraction(self, client: AsyncConvilyn) -> None:
        """Fractional by design (#4179) — it accumulates charges, and rounding it
        to whole credits would make the Free cap unauditable. Typed ``float``
        rather than ``int`` for exactly this."""
        respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(200, json=_balance_wire(freeTierMonthSpentCredits=12.34))
        )

        assert (await client.account.get_balance()).free_tier_month_spent_credits == 12.34


class TestErrorHandling:
    @respx.mock
    async def test_a_rejected_credential_surfaces_as_401(self) -> None:
        """A key is always supplied — the client refuses to construct without one
        (`AuthError`), so "anonymous" is not a state this route can be called in.
        What is under test is the SERVER rejecting the credential."""
        anon = AsyncConvilyn(
            api_key="ck_test_balance",  # pragma: allowlist secret
            base_url=API_BASE,
            max_retries=0,
        )
        respx.get(f"{API_BASE}/api/v1/credits/balance").mock(
            return_value=httpx.Response(
                401, json={"detail": {"code": "UNAUTHORIZED", "message": "Sign in"}}
            )
        )
        try:
            with pytest.raises(APIError) as exc_info:
                await anon.account.get_balance()
            assert exc_info.value.status_code == 401
        finally:
            await anon.aclose()


class TestObjectState:
    def test_the_model_is_frozen(self) -> None:
        from pydantic import ValidationError

        balance = CreditBalance.model_validate(_balance_wire())

        with pytest.raises(ValidationError):
            balance.balance_credits = 999  # type: ignore[misc]

    def test_the_sync_facade_exposes_it(self) -> None:
        """The sync facade mirrors the async surface 1:1, and a method added to
        one and not the other is a silent gap — nothing else checks it."""
        from convilyn import Convilyn

        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert hasattr(sync_client.account, "get_balance")
        finally:
            sync_client.close()

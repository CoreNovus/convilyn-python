"""``_decode_error`` — the billing refusal taxonomy (#4081).

Four refusals ride the paid path and each wants a different next step from the
caller: top up, leave the Free plan, pick another workflow, retry later. Before
these types the caller had to string-match ``exc.code`` to tell them apart,
which is matching on something we reserve the right to change.

Sister to :mod:`test_http`, which owns ``raw_request`` and the error-envelope
*shapes*. This file owns the *dispatch* — which class a decoded envelope becomes
— so it calls :func:`_decode_error` directly rather than paying for a transport
round-trip per case. One end-to-end case at the bottom proves the typed error
really reaches a caller through ``request()``; the rest would only re-prove
respx.

Four categories per `.claude/rules/unit-testing`: logic (which class), boundary
(zero, absent, unknown code), error (the taxonomy promise), object-state (the
operands survive decoding).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    ChargeUnavailableError,
    ConvilynError,
    FreeTierBlockedError,
    InsufficientCreditsError,
    QuotaExceededError,
    SpecNotPricedError,
)
from convilyn._internal.http import _decode_error

API_BASE = "https://api.convilyn.corenovus.com"


def _refusal(status: int, code: str, **extra: Any) -> httpx.Response:
    """A refusal in the shape the backend really sends.

    ``HTTPException(detail={...})`` — so the code and message sit one level
    down, under ``detail``, which is what ``_flatten_error_envelope`` unwraps.
    Built here rather than inline so every case below is the same shape and a
    difference between two tests is a difference in the DATA.
    """
    return httpx.Response(
        status,
        json={"detail": {"code": code, "message": f"refused: {code}", **extra}},
        request=httpx.Request("POST", f"{API_BASE}/api/v1/jobs/goal"),
    )


# ── logic — which class a refusal becomes ────────────────────────────────────


class TestBillingRefusalDispatch:
    def test_402_insufficient_credits_is_its_own_type(self) -> None:
        decoded = _decode_error(_refusal(402, "INSUFFICIENT_CREDITS"))

        assert isinstance(decoded, InsufficientCreditsError)

    def test_402_quota_exceeded_is_still_the_quota_type(self) -> None:
        """The 402 collision, from the other side: adding the balance type must
        not capture the quota one. They share a status and mean different
        things — a quota resets, a balance does not."""
        decoded = _decode_error(_refusal(402, "QUOTA_EXCEEDED"))

        assert isinstance(decoded, QuotaExceededError)

    @pytest.mark.parametrize("code", ["spec_not_allowed_on_free", "free_cost_cap_exceeded"])
    def test_403_free_gates_are_one_type(self, code: str) -> None:
        """Both Free gates land on one class: the caller's next step is the same
        for either, and `code` still says which."""
        decoded = _decode_error(_refusal(403, code))

        assert isinstance(decoded, FreeTierBlockedError)

    def test_409_spec_not_priced_is_the_permanent_type(self) -> None:
        decoded = _decode_error(_refusal(409, "SPEC_NOT_PRICED"))

        assert isinstance(decoded, SpecNotPricedError)

    def test_409_charge_unavailable_is_the_transient_type(self) -> None:
        """Same status as the row above and the OPPOSITE answer to "should I
        retry" — which is the whole reason they are two classes."""
        decoded = _decode_error(_refusal(409, "CHARGE_UNAVAILABLE"))

        assert isinstance(decoded, ChargeUnavailableError)


# ── object-state — the operands survive decoding ─────────────────────────────


class TestInsufficientCreditsCarriesTheNumbers:
    def test_required_credits_is_read_from_the_nested_details(self) -> None:
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 6, "availableCredits": 2},
            )
        )

        assert decoded.required_credits == 6  # type: ignore[union-attr]

    def test_available_credits_is_read_from_the_nested_details(self) -> None:
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 6, "availableCredits": 2},
            )
        )

        assert decoded.available_credits == 2  # type: ignore[union-attr]

    def test_the_untyped_details_dict_is_still_available(self) -> None:
        """Typing the two known operands must not hide anything else the server
        sent — `details` stays the fallback for fields this build does not
        model."""
        decoded = _decode_error(
            _refusal(402, "INSUFFICIENT_CREDITS", details={"somethingNew": "kept"})
        )

        assert decoded.details["somethingNew"] == "kept"

    def test_free_tier_blocked_carries_the_upgrade_url(self) -> None:
        decoded = _decode_error(
            _refusal(403, "free_cost_cap_exceeded", upgradeUrl="/pricing"),
        )

        assert decoded.upgrade_url == "/pricing"  # type: ignore[union-attr]

    def test_shortfall_is_the_difference(self) -> None:
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 6, "availableCredits": 2},
            )
        )

        assert decoded.shortfall_credits == 4  # type: ignore[union-attr]


# ── boundary — zero, absent, unknown ─────────────────────────────────────────


class TestBillingRefusalBoundaries:
    def test_a_zero_balance_decodes_as_zero_not_as_unknown(self) -> None:
        """The load-bearing case, and the one an ``a or b`` alias idiom gets
        wrong: ``availableCredits`` is ``0`` for exactly the caller this error
        exists for, and ``0 or None`` is ``None``. The number would read as
        "the server did not send it" on the most common refusal there is.
        """
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 6, "availableCredits": 0},
            )
        )

        assert decoded.available_credits == 0  # type: ignore[union-attr]

    def test_a_zero_balance_still_computes_a_shortfall(self) -> None:
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 6, "availableCredits": 0},
            )
        )

        assert decoded.shortfall_credits == 6  # type: ignore[union-attr]

    def test_absent_operands_leave_the_shortfall_unknown(self) -> None:
        """Unknown, not zero. A refusal that sent no operands must not be
        rendered as "short by 0 credits", which reads as nothing being wrong."""
        decoded = _decode_error(_refusal(402, "INSUFFICIENT_CREDITS"))

        assert decoded.shortfall_credits is None  # type: ignore[union-attr]

    def test_the_shortfall_never_goes_negative(self) -> None:
        """A refusal whose operands disagree with itself still renders a number
        a caller can print."""
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"requiredCredits": 2, "availableCredits": 9},
            )
        )

        assert decoded.shortfall_credits == 0  # type: ignore[union-attr]

    def test_snake_case_operands_are_accepted(self) -> None:
        decoded = _decode_error(
            _refusal(
                402,
                "INSUFFICIENT_CREDITS",
                details={"required_credits": 5, "available_credits": 1},
            )
        )

        assert decoded.required_credits == 5  # type: ignore[union-attr]

    @pytest.mark.parametrize("status", [402, 403, 409])
    def test_an_unmodelled_code_is_still_a_plain_api_error(self, status: int) -> None:
        """Forward-compat, asserted on every status this file touches rather
        than only on 402: a refusal signal the SDK cannot name must arrive as an
        ``APIError`` with `code` intact, never as an unhandled crash and never
        as a type asserting a remediation nobody verified."""
        decoded = _decode_error(_refusal(status, "SOME_FUTURE_SIGNAL"))

        assert type(decoded) is APIError


# ── error — the taxonomy promise QUICKSTART makes ────────────────────────────


class TestTheTaxonomyPromiseHolds:
    @pytest.mark.parametrize(
        "status,code",
        [
            (402, "INSUFFICIENT_CREDITS"),
            (403, "free_cost_cap_exceeded"),
            (409, "SPEC_NOT_PRICED"),
            (409, "CHARGE_UNAVAILABLE"),
        ],
    )
    def test_every_new_refusal_is_still_an_api_error(self, status: int, code: str) -> None:
        """Existing ``except APIError:`` blocks keep catching these — the new
        types are opt-in precision, not a break."""
        assert isinstance(_decode_error(_refusal(status, code)), APIError)

    @pytest.mark.parametrize(
        "status,code",
        [
            (402, "INSUFFICIENT_CREDITS"),
            (403, "free_cost_cap_exceeded"),
            (409, "SPEC_NOT_PRICED"),
            (409, "CHARGE_UNAVAILABLE"),
        ],
    )
    def test_every_new_refusal_is_still_a_convilyn_error(self, status: int, code: str) -> None:
        """QUICKSTART §4's promise: one ``except ConvilynError`` handles them
        all."""
        assert isinstance(_decode_error(_refusal(status, code)), ConvilynError)


# ── the one end-to-end case — it reaches the caller ──────────────────────────


class TestTheTypedErrorReachesTheCaller:
    @pytest.mark.asyncio
    async def test_request_raises_the_typed_refusal(self) -> None:
        """Everything above decodes a response in isolation. This proves the
        decode is actually wired into the path a caller uses."""
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=_refusal(
                    402,
                    "INSUFFICIENT_CREDITS",
                    details={"requiredCredits": 4, "availableCredits": 0},
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(InsufficientCreditsError) as exc_info:
                    await client._http.request("POST", "/api/v1/jobs/goal", json={})

        assert exc_info.value.shortfall_credits == 4

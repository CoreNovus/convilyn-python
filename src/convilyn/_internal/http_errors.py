"""Wire error envelope → the SDK's exception taxonomy.

Split out of :mod:`convilyn._internal.http`, which had grown to 791 lines — 9
short of the 800-line budget — while doing two unrelated jobs: moving bytes over
a socket, and deciding which :class:`~convilyn.exceptions.APIError` subclass a
non-2xx body names. This half is pure: it takes a response and returns an
exception, touches no socket, and holds the three status→class dispatch tables
that nothing else reads.

The transport keeps :func:`~convilyn._internal.http.server_reason`, which
answers a different question (did the BODY explain, or is this a status label?)
and is imported by ``resources/goals.py``.
"""

from __future__ import annotations

from typing import Any

import httpx

from convilyn.exceptions import (
    APIError,
    ChargeUnavailableError,
    FreeTierBlockedError,
    InsufficientCreditsError,
    PlanRequiredError,
    QuotaExceededError,
    RateLimitError,
    SpecNotPricedError,
)

# Backend emits any of these on 402 to signal a plan-tier mismatch.
# Listed here so adding a new tier (e.g. "ENTERPRISE_TIER_REQUIRED") is
# one-line + does not affect any other dispatch — OCP-honoured.
_PLAN_REQUIRED_CODES: frozenset[str] = frozenset(
    {
        "TIER_REQUIRED",
        "PRO_TIER_REQUIRED",
        "BUSINESS_TIER_REQUIRED",
        "ENTERPRISE_TIER_REQUIRED",
    }
)

# Backend emits either of these on 403 when a Free-plan gate refuses a run
# before any charge (``services/billing/quote.py`` ``QuoteFreeBlocked``). Same
# shape and same rationale as the frozenset above: a third Free gate is one
# line here and changes no dispatch.
_FREE_TIER_BLOCKED_CODES: frozenset[str] = frozenset(
    {
        "spec_not_allowed_on_free",
        "free_cost_cap_exceeded",
    }
)

# 409 billing refusals, keyed to their class. A dict rather than an `if`/`elif`
# chain because the two mean OPPOSITE things about retrying — permanent vs
# transient — and a reader should see that as two rows, not as an order of
# tests (`coding-style.md` O).
_BILLING_CONFLICT_ERRORS: dict[str, type[APIError]] = {
    "SPEC_NOT_PRICED": SpecNotPricedError,
    "CHARGE_UNAVAILABLE": ChargeUnavailableError,
}


def _decode_error(response: httpx.Response) -> APIError:
    """Translate an httpx response into the appropriate :class:`APIError` subclass.

    Normalises the two envelope shapes the API declares — the flat
    ``{code, message, ...}`` and FastAPI's default
    ``{"detail": {code, message, ...}}``. Whichever arrives, the dispatch
    logic below sees the same flat dict.

    Status-aware dispatch:
        * 429 → :class:`RateLimitError`
        * 402 + code in :data:`_PLAN_REQUIRED_CODES` → :class:`PlanRequiredError`
        * 402 + code = ``QUOTA_EXCEEDED`` → :class:`QuotaExceededError`
        * 402 + code = ``INSUFFICIENT_CREDITS`` → :class:`InsufficientCreditsError`
        * 403 + code in :data:`_FREE_TIER_BLOCKED_CODES` → :class:`FreeTierBlockedError`
        * 409 + code in :data:`_BILLING_CONFLICT_ERRORS` → that class
        * Otherwise → base :class:`APIError`

    Unknown codes fall through to ``APIError`` — forward-compat for signals the
    SDK doesn't know about yet. That is deliberate on EVERY status here, not
    just 402: a 403 the SDK cannot name is still a 403, and inventing a type for
    it would assert a remediation nobody verified.
    """
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    inner = _flatten_error_envelope(payload)
    code = inner.get("code") or f"HTTP_{status}"
    message = inner.get("message") or response.reason_phrase or "Request failed"
    details = inner.get("details")

    if status == 429:
        return RateLimitError(status, code, message, details)

    if status == 402:
        upgrade_url = inner.get("upgrade_url") or inner.get("upgradeUrl")
        if code in _PLAN_REQUIRED_CODES:
            return PlanRequiredError(
                status,
                code,
                message,
                details,
                upgrade_url=upgrade_url,
            )
        if code == "QUOTA_EXCEEDED":
            return QuotaExceededError(
                status,
                code,
                message,
                details,
                estimated_micro_u=_coerce_int(
                    inner.get("estimatedMicroU") or inner.get("estimated_micro_u")
                ),
                threshold_micro_u=_coerce_int(
                    inner.get("thresholdMicroU") or inner.get("threshold_micro_u")
                ),
                upgrade_url=upgrade_url,
            )
        if code == "INSUFFICIENT_CREDITS":
            # These ride INSIDE `details`, not beside it — the refusal is built
            # as `detail={code, message, details={requiredCredits, ...}}`
            # (`app/services/billing/goal_lane_confirm.py::charge_confirmed_run`),
            # so `inner` holds the dict and not the numbers. Reading them off
            # `inner` would silently produce None on every real refusal while
            # looking exactly like the QUOTA_EXCEEDED branch above.
            credit_detail = details if isinstance(details, dict) else {}
            return InsufficientCreditsError(
                status,
                code,
                message,
                details,
                required_credits=_coerce_int(
                    _first_present(credit_detail, "requiredCredits", "required_credits")
                ),
                available_credits=_coerce_int(
                    _first_present(credit_detail, "availableCredits", "available_credits")
                ),
                upgrade_url=_first_present(credit_detail, "upgradeUrl", "upgrade_url"),
            )

    if status == 403 and code in _FREE_TIER_BLOCKED_CODES:
        return FreeTierBlockedError(
            status,
            code,
            message,
            details,
            upgrade_url=inner.get("upgrade_url") or inner.get("upgradeUrl"),
        )

    if status == 409:
        conflict = _BILLING_CONFLICT_ERRORS.get(code)
        if conflict is not None:
            return conflict(status, code, message, details)

    return APIError(status, code, message, details)


def _flatten_error_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap FastAPI's ``{"detail": ...}``; leave a flat payload alone.

    Three shapes, not two. This docstring used to say *"those are the only two
    shapes the API produces … enumerated from the published contracts, which is
    the agreed description of the wire rather than a sample of it"* — and the
    enumeration was of what the contracts DECLARE, never of what the service
    EMITS. The two diverge, so the confident sourcing was the part that made the
    claim hard to doubt:

    * ``{code, message, details}`` — flat, the declared envelope
    * ``{"detail": {code, message, ...}}`` — the framework's wrapped-dict form
    * ``{"detail": "<plain string>"}`` — a plain-string detail, which several
      AI-workflow create paths return; it collapses a typed error's ``code`` and
      ``details`` into its message string on the way out

    The third fell through to ``payload`` unchanged, so ``code`` and ``message``
    both missed and :func:`_decode_error` substituted ``HTTP_<status>`` and the
    reason phrase. **The server's actual explanation was discarded and replaced
    with "Bad Request"** — for :meth:`~convilyn.resources.goals.AsyncGoals.understand`
    that meant a caller who sent too many files was told the platform does not
    support understanding at all.

    A string ``detail`` carries no code, so only ``message`` is recovered here;
    ``code`` still falls back to the status-derived one, which remains the
    truthful answer when the wire genuinely did not send one.

    Any OTHER shape is still returned unchanged rather than unwrapped on a
    guess. That reasoning was right and is kept — the defect was the closed set,
    not the caution.
    """
    nested = payload.get("detail")
    if isinstance(nested, str):
        return {"message": nested}
    return nested if isinstance(nested, dict) else payload


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    """The first key that is PRESENT, not the first that is truthy.

    ``payload.get("a") or payload.get("a_snake")`` is the idiom used elsewhere
    in this module for camel/snake aliases, and it is wrong for any field whose
    zero value is meaningful. ``availableCredits`` is exactly that field: it is
    ``0`` for the caller :class:`InsufficientCreditsError` exists for — someone
    whose balance is empty — and ``0 or None`` is ``None``, so the number would
    read as "the server did not send it" on the most common refusal there is.

    Returns ``None`` when no key is present, which :func:`_coerce_int` then
    passes through as the genuine "unknown".
    """
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _coerce_int(value: Any) -> int | None:
    """Best-effort ``int`` coercion for wire fields that may arrive as str."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

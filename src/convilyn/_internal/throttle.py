"""Auto-throttle policy — sleep + retry on :class:`QuotaExceededError`.

Layered above the transport's :class:`RetryPolicy`: the policy retries
transient transport failures (5xx / 429 / 408), while this module
specifically targets the 402 ``QUOTA_EXCEEDED`` envelope where the
remediation is *waiting for the quota window* rather than re-issuing the
same request faster.

Design choices
--------------

* **Bounded retry**: defaults to ``max_retries=1`` so a misconfigured
  caller cannot get stuck in an infinite sleep+retry loop against a
  permanently-overrun account.
* **Bounded sleep**: ``max_sleep`` caps any server-supplied delay; if
  the server tells the SDK to wait longer than that, the SDK gives up
  immediately and re-raises so the caller's UX path stays predictable.
* **Forward-compat reset hints**: today's backend does not emit
  ``Retry-After`` on 402, but when it does the SDK reads
  ``details["retry_after_seconds"]`` or ``details["reset_at"]``
  transparently — both fields are forward-compat wiring.

Soft-limit signalling is handled in the same module for symmetry: the
backend may emit ``X-Quota-State: soft_limit`` on any response, and the
SDK turns that into a stdlib :class:`logging.Logger` warning plus a
:class:`UserWarning` so callers can opt into ``warnings.filterwarnings``
gates without intercepting the logger.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from convilyn.exceptions import QuotaExceededError

logger = logging.getLogger("convilyn.throttle")

#: Header the backend (will) emit to flag "soft limit" without blocking the call.
SOFT_LIMIT_HEADER: Final[str] = "X-Quota-State"
SOFT_LIMIT_VALUE: Final[str] = "soft_limit"

#: Defaults — kept module-level so :class:`AutoThrottleConfig`'s ``True`` form
#: and tests can reference the same canonical values.
DEFAULT_MAX_RETRIES: Final[int] = 1
DEFAULT_MAX_SLEEP_SECONDS: Final[float] = 60.0
DEFAULT_FALLBACK_SLEEP_SECONDS: Final[float] = 5.0


@dataclass(frozen=True)
class AutoThrottleConfig:
    """Knobs for the :class:`QuotaExceededError` retry loop.

    Set via ``Convilyn(auto_throttle=AutoThrottleConfig(...))`` or
    ``Convilyn(auto_throttle={"max_retries": 2, ...})``.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    max_sleep: float = DEFAULT_MAX_SLEEP_SECONDS
    fallback_sleep: float = DEFAULT_FALLBACK_SLEEP_SECONDS

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.max_sleep <= 0:
            raise ValueError("max_sleep must be > 0")
        if self.fallback_sleep < 0:
            raise ValueError("fallback_sleep must be >= 0")


AutoThrottleArg = bool | AutoThrottleConfig | dict[str, Any] | None


def resolve_auto_throttle(value: AutoThrottleArg) -> AutoThrottleConfig | None:
    """Normalise the ctor knob into an :class:`AutoThrottleConfig` or ``None``.

    * ``None`` / ``False`` → disabled.
    * ``True`` → default config.
    * ``dict`` → ``AutoThrottleConfig(**dict)``.
    * :class:`AutoThrottleConfig` → returned verbatim.
    """
    if value is None or value is False:
        return None
    if value is True:
        return AutoThrottleConfig()
    if isinstance(value, AutoThrottleConfig):
        return value
    if isinstance(value, dict):
        return AutoThrottleConfig(**value)
    raise TypeError(
        f"auto_throttle must be bool, dict, or AutoThrottleConfig — got {type(value).__name__}"
    )


def compute_quota_sleep(error: QuotaExceededError, config: AutoThrottleConfig) -> float | None:
    """Return seconds to sleep before retrying, or ``None`` to give up.

    Resolution order (first non-empty wins):

    1. ``details["retry_after_seconds"]`` — numeric seconds.
    2. ``details["reset_at"]`` — ISO-8601 timestamp; subtracted from now.
    3. ``config.fallback_sleep`` — when neither hint is present.

    A negative or zero delta from ``reset_at`` collapses to 0.0 (retry
    immediately). Any computed sleep that exceeds ``config.max_sleep``
    returns ``None`` so the caller re-raises without spinning.
    """
    details = error.details or {}
    sleep_s = _sleep_from_retry_after(details)
    if sleep_s is None:
        sleep_s = _sleep_from_reset_at(details)
    if sleep_s is None:
        sleep_s = config.fallback_sleep
    if sleep_s > config.max_sleep:
        return None
    return max(0.0, sleep_s)


def _sleep_from_retry_after(details: dict[str, Any]) -> float | None:
    raw = details.get("retry_after_seconds")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _sleep_from_reset_at(details: dict[str, Any]) -> float | None:
    raw = details.get("reset_at")
    if not isinstance(raw, str):
        return None
    try:
        # ``datetime.fromisoformat`` only learned the "Z" suffix in 3.11;
        # we target 3.12 so it works, but normalise defensively in case
        # the server emits offset-less strings.
        reset_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if reset_dt.tzinfo is None:
        reset_dt = reset_dt.replace(tzinfo=timezone.utc)
    return (reset_dt - datetime.now(timezone.utc)).total_seconds()


def emit_soft_limit_warning(*, source: str, details: str | None = None) -> None:
    """Log + emit a :class:`UserWarning` for a soft-limit signal.

    Two channels so callers have either entry point:
    * ``logging.getLogger("convilyn.throttle")`` for structured log piping.
    * stdlib :mod:`warnings` so ``warnings.filterwarnings`` gates work.
    """
    message = f"Convilyn quota soft_limit reached (source={source})"
    if details:
        message = f"{message}: {details}"
    logger.warning(message)
    warnings.warn(message, stacklevel=3)


def response_has_soft_limit_header(headers: Any) -> bool:
    """True when an httpx response carries ``X-Quota-State: soft_limit``.

    Tolerant of casing and surrounding whitespace; the case-insensitivity
    is already provided by :class:`httpx.Headers`, but the value still
    needs trimming because some gateways add trailing spaces.
    """
    raw = headers.get(SOFT_LIMIT_HEADER)
    if not raw:
        return False
    return raw.strip().lower() == SOFT_LIMIT_VALUE

"""Retry policy + idempotency primitives for the Convilyn SDK transport.

This module exposes only **decision** primitives — the retry loop itself
lives in :mod:`convilyn._internal.http` so a custom :class:`RetryPolicy`
can replace one piece (the decision) without rewriting transport
plumbing.

Design follows the SOLID DIP seam established elsewhere in the SDK:
:class:`HTTPClient` depends on the :class:`RetryPolicy` abstract Protocol,
not the concrete :class:`ExponentialBackoffRetry` implementation. Users
who want a different cadence (linear backoff, fixed delay, decorrelated
jitter…) implement the Protocol and inject it via
``Convilyn(retry_policy=...)``.
"""

from __future__ import annotations

import random
import uuid
from typing import Protocol, runtime_checkable

import httpx

# HTTP verbs that mutate server state. Auto-Idempotency-Key applies to
# these; GET/HEAD/OPTIONS are inherently idempotent at the protocol level.
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Default retryable status codes. 408 is a server-driven retry hint; 429
# is the rate-limit signal (paired with `Retry-After`); 5xx is "the server
# probably will succeed if we ask again". 4xx outside this set is a client
# bug and retry would just amplify the broken request.
DEFAULT_RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


@runtime_checkable
class RetryPolicy(Protocol):
    """A decision policy for whether and how long to wait before retrying.

    Implementations are pure decision logic — they do not perform the
    sleep or the retry themselves. The transport calls
    :py:meth:`should_retry` first; if ``True`` it calls
    :py:meth:`next_delay`, sleeps, and re-issues the request.
    """

    def should_retry(
        self,
        response: httpx.Response | None,
        exc: BaseException | None,
        attempt: int,
    ) -> bool: ...

    def next_delay(
        self,
        response: httpx.Response | None,
        attempt: int,
    ) -> float: ...


class ExponentialBackoffRetry:
    """Capped exponential backoff with jitter and ``Retry-After`` awareness.

    Defaults match the industry baseline (httpx 'Retries' transport,
    Stripe Python, OpenAI Python): five attempts, 200 ms base, 30 s cap,
    ±25 % jitter. The server's ``Retry-After`` header overrides the
    computed delay when present — respecting that header is what makes a
    SDK polite at the API-gateway tier.

    By default this policy retries only HTTP transport responses; httpx
    transport-level exceptions (connect / read / network) are NOT
    retried because the SDK cannot tell whether a mutating POST already
    reached the server. Users who know their requests are safe to
    re-send can subclass and override :py:meth:`should_retry`.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        base_delay: float = 0.2,
        max_delay: float = 30.0,
        jitter: float = 0.25,
        retryable_statuses: frozenset[int] = DEFAULT_RETRYABLE_STATUSES,
    ) -> None:
        if max_attempts < 0:
            raise ValueError("max_attempts must be non-negative")
        if base_delay <= 0 or max_delay <= 0:
            raise ValueError("base_delay and max_delay must be positive")
        if not 0 <= jitter < 1:
            raise ValueError("jitter must be in [0, 1)")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.retryable_statuses = retryable_statuses

    def should_retry(
        self,
        response: httpx.Response | None,
        exc: BaseException | None,
        attempt: int,
    ) -> bool:
        if attempt >= self.max_attempts:
            return False
        if exc is not None:
            # Conservative default: do not retry transport exceptions
            # because a mutating POST may have reached the server.
            return False
        if response is None:
            return False
        return response.status_code in self.retryable_statuses

    def next_delay(
        self,
        response: httpx.Response | None,
        attempt: int,
    ) -> float:
        """Return the delay before the next attempt (in seconds).

        Server-provided ``Retry-After`` always wins. Without it we use
        exponential backoff: ``base_delay * 2^attempt``, capped at
        ``max_delay``, then perturbed by ±``jitter`` × the base value.
        """
        server_hint = _parse_retry_after(response)
        if server_hint is not None:
            return min(server_hint, self.max_delay)
        # Exponential: attempt 0 → base, attempt 1 → 2×base, ...
        exponential = self.base_delay * (2**attempt)
        capped = min(exponential, self.max_delay)
        spread = capped * self.jitter
        return capped + random.uniform(-spread, spread)


def _parse_retry_after(response: httpx.Response | None) -> float | None:
    """Decode a ``Retry-After`` header value (seconds) — None if absent / malformed.

    HTTP allows date-format ``Retry-After`` too, but in practice the
    The Convilyn API and storage service both emit seconds; supporting the date
    form would add complexity for no current benefit and is left for a
    future release.
    """
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def generate_idempotency_key() -> str:
    """Return a fresh UUID v4 suitable for use as an ``Idempotency-Key``.

    The header value is intentionally opaque — backend dedup logic only
    needs a stable token across retries of the same logical request.
    The SDK reuses the same key inside a retry loop so a future backend
    that does honour the header can deduplicate transparently; today the
    backend ignores the header on ``POST /jobs``, so this is
    forward-compatibility wiring rather than active de-duplication.
    """
    return str(uuid.uuid4())


class NoRetry:
    """A policy that never retries — useful as a sentinel and in tests.

    Equivalent to ``ExponentialBackoffRetry(max_attempts=0)`` but cheaper
    to construct and explicit at the call site.
    """

    def should_retry(
        self,
        response: httpx.Response | None,
        exc: BaseException | None,
        attempt: int,
    ) -> bool:
        return False

    def next_delay(
        self,
        response: httpx.Response | None,
        attempt: int,
    ) -> float:
        return 0.0

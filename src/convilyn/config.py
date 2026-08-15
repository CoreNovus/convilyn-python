"""Public configuration types for the Convilyn client.

These are the *public* knobs you pass to ``Convilyn(...)`` /
``AsyncConvilyn(...)`` to tune resilience behaviour:

* :class:`RetryPolicy` — the abstract Protocol a custom retry cadence
  implements (inject via ``Convilyn(retry_policy=...)``).
* :class:`ExponentialBackoffRetry` — the default, capped-exponential-with-
  jitter policy (the one used when you pass nothing or just ``max_retries``).
* :class:`NoRetry` — a sentinel policy that never retries.
* :class:`AutoThrottleConfig` — knobs for the opt-in ``QuotaExceededError``
  sleep-and-retry loop (``Convilyn(auto_throttle=...)``).

They are re-exported from the top-level ``convilyn`` namespace and are
covered by the SDK's semver guarantee (see ``docs/STABILITY.md``). Import
them from ``convilyn`` or ``convilyn.config`` — never from
``convilyn._internal``, which is implementation detail with no stability
guarantee and may move between releases.
"""

from __future__ import annotations

from convilyn._internal.resilience import (
    ExponentialBackoffRetry,
    NoRetry,
    RetryPolicy,
)
from convilyn._internal.throttle import AutoThrottleConfig

__all__ = [
    "AutoThrottleConfig",
    "ExponentialBackoffRetry",
    "NoRetry",
    "RetryPolicy",
]

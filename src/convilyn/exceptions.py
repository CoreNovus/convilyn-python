"""Typed exceptions raised by the Convilyn SDK.

Hierarchy::

    ConvilynError                 base; everything the SDK raises
    ├── AuthError                 missing / malformed credentials
    ├── APIError                  HTTP 4xx / 5xx from the Convilyn API
    │   ├── RateLimitError        HTTP 429
    │   └── (further specific types land alongside resource modules)
    └── (transport-level errors are exposed as `httpx` exceptions verbatim)

The SDK never raises a bare ``Exception``; callers can rely on catching
``ConvilynError`` to handle any SDK-originated failure.
"""

from __future__ import annotations

from typing import Any


class ConvilynError(Exception):
    """Base class for every error this SDK raises."""


class AuthError(ConvilynError):
    """Authentication / authorization failed before any HTTP call.

    Examples: no API key configured; malformed key prefix.
    """


class APIError(ConvilynError):
    """Server returned a non-success HTTP response.

    The Convilyn API uses the envelope ``{code, message, details}``; those
    fields are exposed as attributes for typed handling.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"HTTP {status_code} {code}: {message}")


class RateLimitError(APIError):
    """HTTP 429 — the SDK or caller exceeded the API rate limit.

    Future commits will surface ``X-RateLimit-Reset`` here so callers can
    implement adaptive throttling.
    """


class PlanRequiredError(APIError):
    """HTTP 402 with ``code=TIER_REQUIRED`` — caller's plan doesn't include this action.

    Surfaced by Pro-tier-gated endpoints (workflow fork / publish, builder
    chat sessions, etc.). The :attr:`upgrade_url` points at the in-app
    pricing CTA so a higher-level caller can render the prompt without
    hardcoding the URL.

    Subclasses :class:`APIError`, so existing ``except APIError:`` blocks
    keep catching it — opt into the typed handler only when you need to
    distinguish "wrong plan" from "server error".
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        required_plan: str = "pro",
        upgrade_url: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.required_plan = required_plan
        self.upgrade_url = upgrade_url


class QuotaExceededError(APIError):
    """HTTP 402 with ``code=QUOTA_EXCEEDED`` — caller hit the monthly cost cap.

    Distinct from :class:`PlanRequiredError` because the remediation is
    different: a plan-upgrade purchase, a credit top-up, or simply
    waiting until the next billing period. The :attr:`estimated_micro_u`
    / :attr:`threshold_micro_u` pair lets callers render an "X / Y" usage bar
    without an extra round-trip.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        estimated_micro_u: int | None = None,
        threshold_micro_u: int | None = None,
        upgrade_url: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.estimated_micro_u = estimated_micro_u
        self.threshold_micro_u = threshold_micro_u
        self.upgrade_url = upgrade_url


class S3UploadError(APIError):
    """The storage upload step of a file upload returned a non-success status.

    Subclasses :class:`APIError` so callers catching ``APIError`` see it,
    while still being distinguishable for callers who want to handle
    storage-specific failures (e.g. retry policy distinct from the Convilyn API).
    """


class JobFailedError(ConvilynError):
    """A platform job (``client.convert.wait``, ``client.goals.wait``...)
    finished with status ``failed``.

    The :pyattr:`code` and :pyattr:`message` mirror the wire-side
    :class:`convilyn.types.JobError`; :pyattr:`job_id` and
    :pyattr:`processor_type` help callers correlate against logs.
    """

    def __init__(
        self,
        *,
        job_id: str,
        processor_type: str,
        code: str,
        message: str,
    ) -> None:
        self.job_id = job_id
        self.processor_type = processor_type
        self.code = code
        self.message = message
        super().__init__(f"Job {job_id} ({processor_type}) failed [{code}]: {message}")


class JobTimeoutError(ConvilynError):
    """A polling helper exceeded its ``timeout`` before the job reached a
    terminal status.

    The job is still alive on the backend — the caller can resume polling
    with :py:meth:`convilyn.resources.convert.AsyncConvert.retrieve`.
    """

    def __init__(self, *, job_id: str, elapsed: float, timeout: float) -> None:
        self.job_id = job_id
        self.elapsed = elapsed
        self.timeout = timeout
        super().__init__(
            f"Job {job_id} did not reach a terminal status within {timeout}s "
            f"(elapsed {elapsed:.1f}s)"
        )


class GoalJobFailedError(ConvilynError):
    """An AI workflow (agentic) job finished with status ``failed``.

    Mirrors :class:`JobFailedError` — separate class so callers can
    distinguish file conversion failures from AI workflow
    failures via ``except``.
    """

    def __init__(
        self,
        *,
        job_spec_id: str,
        code: str | None,
        message: str | None,
    ) -> None:
        self.job_spec_id = job_spec_id
        self.code = code or "GOAL_JOB_FAILED"
        self.message = message or "Job failed without a structured error message"
        super().__init__(f"GoalJob {job_spec_id} failed [{self.code}]: {self.message}")


class GoalJobTimeoutError(ConvilynError):
    """An AI workflow polling helper exceeded its ``timeout`` before reaching
    a terminal status (or a ``slots_pending`` HITL stop).

    The job is still alive on the backend — the caller can resume
    polling with
    :py:meth:`convilyn.resources.goals.AsyncGoals.retrieve`.
    """

    def __init__(
        self,
        *,
        job_spec_id: str,
        elapsed: float,
        timeout: float,
        reason: str = "total",
    ) -> None:
        self.job_spec_id = job_spec_id
        self.elapsed = elapsed
        self.timeout = timeout
        #: ``"total"`` — the overall ``timeout`` budget lapsed; ``"idle"`` —
        #: ``idle_timeout`` lapsed with no status/progress change (the job may
        #: still be healthy on a long phase; check ``retrieve()`` before
        #: assuming failure).
        self.reason = reason
        if reason == "idle":
            message = (
                f"GoalJob {job_spec_id} showed no status/progress change for "
                f"{timeout}s (elapsed {elapsed:.1f}s total)"
            )
        else:
            message = (
                f"GoalJob {job_spec_id} did not reach a terminal status within "
                f"{timeout}s (elapsed {elapsed:.1f}s)"
            )
        super().__init__(message)


class WebSocketError(ConvilynError):
    """Raised when the AI workflow event stream cannot proceed.

    Covers four conditions:

    * the SDK has no WebSocket URL to dial (no ``ws_url`` ctor arg, no
      ``CONVILYN_WS_URL`` env var, no per-call override);
    * the transport's ``connect`` step raises (DNS, TLS, 4xx upgrade);
    * a mid-stream message cannot be parsed into a :class:`GoalEvent`
      (malformed JSON, unknown ``type`` value);
    * the connection drops mid-stream (server close, network error).

    The original payload — when available — is attached on
    :pyattr:`payload` so callers can log it for debugging.
    """

    def __init__(self, message: str, *, payload: str | None = None) -> None:
        self.payload = payload
        super().__init__(message)


class RetryExhaustedError(APIError):
    """The retry policy ran out of attempts before the request succeeded.

    Wraps the final :class:`APIError` so callers see the last
    server-side status / code (the ones that triggered the final retry
    decision). ``attempt_count`` is the total number of attempts that
    were made — including the one that produced this error.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        attempt_count: int = 0,
    ) -> None:
        self.attempt_count = attempt_count
        super().__init__(status_code, code, message, details)

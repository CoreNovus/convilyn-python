"""Typed exceptions raised by the Convilyn SDK.

Hierarchy::

    ConvilynError                 base; everything the SDK raises
    ├── AuthError                 missing / malformed credentials
    ├── APIError                  HTTP 4xx / 5xx from the Convilyn API
    │   ├── RateLimitError        HTTP 429
    │   ├── PlanRequiredError     402 — your plan does not include this action
    │   ├── QuotaExceededError    402 — the monthly cost cap is spent
    │   ├── InsufficientCreditsError  402 — your BALANCE cannot fund this run
    │   ├── FreeTierBlockedError  403 — a Free-plan gate refused the run
    │   ├── SpecNotPricedError    409 — this workflow has no price configured
    │   ├── ChargeUnavailableError    409 — billing could not charge right now
    │   └── (further specific types land alongside resource modules)
    └── (transport-level errors are exposed as `httpx` exceptions verbatim)

The four billing refusals below arrive on the SAME paid path and each wants a
different next step from the caller — top up, upgrade, pick another workflow,
retry later. Before they existed a caller had to string-match ``exc.code`` to
tell them apart, which is matching on something we reserve the right to change.

The SDK never raises a bare ``Exception``; callers can rely on catching
``ConvilynError`` to handle any SDK-originated failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Annotation-only, and deliberately so: `types` imports nothing from this
    # module today, but a runtime import here would create the edge that makes
    # a future cycle possible in a PUBLISHED package, where the failure mode is
    # an ImportError on the user's machine.
    from convilyn.types import JobErrorDetail


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


class InsufficientCreditsError(APIError):
    """HTTP 402 with ``code=INSUFFICIENT_CREDITS`` — your balance cannot fund this run.

    **Not the same thing as :class:`QuotaExceededError`, and they share a status
    code.** A quota is a ceiling you were given; a balance is money you hold. The
    remediation differs — a quota resets at the next period, a balance does not
    refill on its own — so the SDK gives them separate types rather than leaving
    the caller to branch on ``code``::

        except InsufficientCreditsError as exc:
            print(f"short by {exc.shortfall_credits} credits")   # may be None
        except QuotaExceededError:
            ...                                                  # wait, or upgrade

    :attr:`required_credits` / :attr:`available_credits` come from the refusal
    body, so "how short am I" needs no second round-trip. Both are ``None`` when
    the server did not send them — read them as *unknown*, never as zero, and
    keep :attr:`APIError.details` as the untyped fallback for anything this
    build does not model.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        required_credits: int | None = None,
        available_credits: int | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.required_credits = required_credits
        self.available_credits = available_credits

    @property
    def shortfall_credits(self) -> int | None:
        """How many credits short this run is, or ``None`` if unknown.

        Derived rather than stored: the server sends the two operands, and a
        third wire field that must agree with them is a field that can disagree
        with them. Clamped at zero — a negative shortfall is not a number any
        caller wants to render, and it would mean the refusal disagreed with its
        own operands.
        """
        if self.required_credits is None or self.available_credits is None:
            return None
        return max(0, self.required_credits - self.available_credits)


class FreeTierBlockedError(APIError):
    """HTTP 403 — a Free-plan gate refused the run before any charge.

    Two server codes land here, and they are one class because the caller's next
    step is the same for both: leave the Free plan (or, for the cap, fund the
    run from a top-up).

    * ``spec_not_allowed_on_free`` — this workflow is not offered on Free.
    * ``free_cost_cap_exceeded``   — Free's monthly processing cap is spent.

    Branch on :attr:`APIError.code` when you want to say which; catch the type
    when you only need to know it is a plan gate rather than a server fault.
    :attr:`upgrade_url` is the in-app CTA, so callers do not hardcode it.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        upgrade_url: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.upgrade_url = upgrade_url


class SpecNotPricedError(APIError):
    """HTTP 409 with ``code=SPEC_NOT_PRICED`` — this workflow has no price configured.

    **Permanent for this workflow**, and that is what separates it from
    :class:`ChargeUnavailableError` on the same status code: retrying will not
    help, and no amount of credit changes it. Pick another workflow, or report
    it — a priced workflow reaching this state is a catalogue defect, not a
    caller mistake.
    """


class ChargeUnavailableError(APIError):
    """HTTP 409 with ``code=CHARGE_UNAVAILABLE`` — billing could not charge right now.

    **Transient.** The run was refused because the charge could not be recorded,
    not because it was unaffordable and not because the workflow is unpriced
    (:class:`SpecNotPricedError`). Retrying later is the correct response; the
    SDK does not retry it automatically, because a repeated charge attempt is
    the caller's decision to make, not a transport concern.
    """


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

    :pyattr:`detail` is present when the server had specifics to hand over —
    today, a multi-sheet workbook refused for CSV. ``message`` stays the canned
    sentence for the code, so ``detail`` is what lets you say *why* in your own
    words and your own locale::

        except JobFailedError as exc:
            if exc.detail and exc.detail.reason == "MULTI_SHEET_WORKBOOK":
                print(f"{exc.detail.sheet_count} sheets; try "
                      f"{' / '.join(exc.detail.faithful_targets or [])}")

    Branch on ``code`` first and treat an unrecognised ``detail.reason`` as
    absent: the server may know refusals this build does not.
    """

    def __init__(
        self,
        *,
        job_id: str,
        processor_type: str,
        code: str,
        message: str,
        detail: JobErrorDetail | None = None,
    ) -> None:
        self.job_id = job_id
        self.processor_type = processor_type
        self.code = code
        self.message = message
        self.detail = detail
        # The prefix is unchanged and stays first. Callers do match on this
        # string, so the operands are appended rather than interpolated into
        # the existing sentence.
        #
        # Keyed on the REASON, not on "a sheet_count is present". Those are the
        # same set today and would not stay that way: a future reason carrying
        # a sheet count for some other purpose would inherit this sentence and
        # assert something false about CSV. A reason this build does not
        # recognise gets no sentence — the structured fields are on `detail`
        # either way, which is the point of sending operands.
        summary = f"Job {job_id} ({processor_type}) failed [{code}]: {message}"
        if (
            detail is not None
            and detail.reason == "MULTI_SHEET_WORKBOOK"
            and detail.sheet_count is not None
        ):
            targets = ", ".join(detail.faithful_targets or []) or "another format"
            summary += (
                f" This workbook has {detail.sheet_count} sheets and CSV holds one table;"
                f" convert to {targets} to keep every sheet, or upload a single-sheet workbook."
            )
        super().__init__(summary)


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


class UnderstandUnavailableError(ConvilynError):
    """:py:meth:`convilyn.resources.goals.AsyncGoals.understand` could not run.

    Raised when the connected Convilyn platform does not (yet) support
    schema-grounded understanding (the ``output_schema`` path), so no grounded,
    schema-validated result could be produced. It is raised **instead of**
    returning an ungrounded / unvalidated answer — an answer that was
    not grounded by the platform is never silently returned as if it were.

    The capability ships behind a platform rollout. Until it reaches the
    environment you are calling, catch this and fall back to a workflow you
    authored (``goals.run(user_workflow_id=...)``) or the fixed-schema
    ``goals.extract(...)``.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "The connected Convilyn platform does not support schema-grounded "
            "understanding (goals.understand) yet; no grounded result was returned."
        )


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

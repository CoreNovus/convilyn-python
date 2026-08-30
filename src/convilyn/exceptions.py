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
    ├── JobFailedError            a conversion job finished `failed`
    ├── JobTimeoutError           a conversion poll helper gave up
    ├── GoalJobFailedError        a workflow job finished `failed`
    ├── GoalJobTimeoutError       a workflow poll helper gave up
    ├── GoalArtifactUnusableError the workflow SUCCEEDED and its output is
    │                             unusable — no artifact of the kind asked
    │                             for, an unreadable one, or one over the
    │                             in-memory cap
    ├── UnderstandUnavailableError  this platform does not serve the shape
    └── (transport-level errors are exposed as `httpx` exceptions verbatim)

The four billing refusals below arrive on the SAME paid path and each wants a
different next step from the caller — top up, upgrade, pick another workflow,
retry later. Before they existed a caller had to string-match ``exc.code`` to
tell them apart, which is matching on something we reserve the right to change.

The SDK never raises a bare ``Exception``; callers can rely on catching
``ConvilynError`` to handle any SDK-originated failure.

What deliberately stays a builtin is the caller's own mistake — an empty file
list, a schema that is not a dict, a `page_range` on an image. Python already
names those, and `docs/QUICKSTART.md` §4 says so. The line is not "did it come
from us": it is whether the SDK is describing the platform's answer or the
caller's argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    # Annotation-only, and deliberately so: `types` imports nothing from this
    # module today, but a runtime import here would create the edge that makes
    # a future cycle possible in a PUBLISHED package, where the failure mode is
    # an ImportError on the user's machine.
    from convilyn.types import GoalErrorDetail, JobErrorDetail


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

    :attr:`upgrade_url` points at the account's top-up page when the server
    sends one, mirroring :attr:`PlanRequiredError.upgrade_url` — the SDK does
    not construct or guess it.
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
        upgrade_url: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.required_credits = required_credits
        self.available_credits = available_credits
        self.upgrade_url = upgrade_url

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

    :pyattr:`detail` carries the operands when the server had them.
    ``PROCESSING_LIMIT`` covers four unrelated ceilings, so this is what tells
    them apart::

        except GoalJobFailedError as exc:
            if exc.detail and exc.detail.reason == "ITERATION_LIMIT":
                print(f"stopped at {exc.detail.reached}/{exc.detail.limit} steps")
            if exc.retryable:
                job = await client.goals.retry(exc.job_spec_id)

    :pyattr:`suggested_action` is the server's own CTA for this ``code``, so
    every client does not keep its own copy of the mapping. Branch on ``code``
    first and treat an unrecognised ``detail.reason`` or ``suggested_action``
    as absent.
    """

    def __init__(
        self,
        *,
        job_spec_id: str,
        code: str | None,
        message: str | None,
        detail: GoalErrorDetail | None = None,
        suggested_action: str | None = None,
    ) -> None:
        self.job_spec_id = job_spec_id
        self.code = code or "GOAL_JOB_FAILED"
        self.message = message or "Job failed without a structured error message"
        self.detail = detail
        self.suggested_action = suggested_action
        super().__init__(f"GoalJob {job_spec_id} failed [{self.code}]: {self.message}")

    @property
    def retryable(self) -> bool | None:
        """Whether re-running THIS job is the server's suggested next step, or
        ``None`` when the server did not say.

        Convenience over :pyattr:`suggested_action`, and deliberately not a
        field the server sends. It is **not** a second implementation of the
        server's decision: the table being protected is *code to action*, and
        that stays on the server — ``action == "retry"`` is the definition of
        that enum member, nothing more.

        **Tri-state, and the third state is the point.** This returned a bare
        ``bool``, so "the server said do not retry" and "the server said
        nothing" were the same answer — ``False``. That is the reading this
        package's own :class:`InsufficientCreditsError` docstring already
        forbids for its operands: *read them as unknown, never as zero*. The
        same discipline applies to a verdict.

        It was not academic. Until the backend began sending
        ``suggestedAction`` on a failed job, ``suggested_action`` was ``None``
        on every failure — so ``retryable`` was structurally ``False`` for
        every job this SDK has ever seen fail, and a caller branching on it
        would never once have retried.

        Branch explicitly::

            if exc.retryable:
                job = await client.goals.retry(exc.job_spec_id)
            elif exc.retryable is None:
                ...   # no guidance — decide from `code` yourself

        Read it rather than assuming. ``UPGRADE_REQUIRED`` is not retryable but
        IS actionable, which is why the server sends an action rather than a
        bool. And ``goals.retry(job_spec_id)`` reuses the same job rather than
        opening — and charging for — a new one.
        """
        if self.suggested_action is None:
            return None
        return self.suggested_action == "retry"


class GoalJobTimeoutError(ConvilynError):
    """An AI workflow polling helper exceeded its ``timeout`` before reaching
    a terminal status (or a ``slots_pending`` HITL stop).

    The job may still be alive on the backend, and the caller can resume polling
    with :py:meth:`convilyn.resources.goals.AsyncGoals.retrieve`.

    **This timeout does not refund anything.** The run was charged when you
    confirmed it — the confirm charge is a reservation taken up front — and
    there is no refund path for a run that never reaches a terminal state. A
    successful run reconciles its reservation against actual cost afterwards,
    and a run the platform records as FAILED is reversed in full; a run that
    simply stops producing status does neither, because nothing has told the
    settle path what happened. So the credits are spent whether or not you ever
    see a result. This paragraph exists because this class previously said only
    "the job is still alive", and the word "billed" appeared nowhere in the SDK.

    **"Still alive" is a possibility, not a diagnosis — distinguish it before
    you re-poll.** Compare :py:attr:`convilyn.GoalJob.updated_at` across two
    ``retrieve()`` calls a few seconds apart:

    * it ADVANCES ⇒ the run is progressing and a longer ``timeout`` is the fix;
    * it is FROZEN ⇒ the run is wedged, and there is no self-service exit.
      Re-polling will not unwedge it, and
      :py:meth:`~convilyn.resources.goals.AsyncGoals.retry` will not either:
      retry requires the job to be in ``failed`` status, and a wedged run is
      still ``executing``. Calling ``understand()`` again creates a NEW job
      spec and is charged again.

    The frozen case is observed, not hypothetical: a real 23-page PDF has been
    seen with ``updated_at`` static from 6s after creation through 895s, no
    terminal status, ``code`` and ``suggested_action`` both ``None``, and one
    credit spent.
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


#: What is wrong with the artifact a finished job produced.
#:
#: Closed, unlike the wire-side ``JobErrorDetail.reason``: that one is ``str``
#: because the SERVER may know values this build does not, and this one is
#: produced only here.
GoalArtifactProblem = Literal["missing", "unparsable", "too_large"]

#: The output shape the calling method promised.
GoalArtifactKind = Literal["json", "markdown"]

_ARTIFACT_KIND_LABEL: dict[str, str] = {"json": "JSON", "markdown": "Markdown"}


class GoalArtifactUnusableError(ConvilynError):
    """The job ran, the platform is satisfied, and the output cannot be handed back.

    This is not a failed job and not an argument mistake, which is why neither of
    the existing types fits. ``goals.extract()`` / ``understand()`` /
    ``to_markdown()`` all promise a shape; when the run finishes and no artifact
    of that shape can be returned, the caller did nothing wrong and has already
    paid. A ``ValueError`` says the opposite, and ``except ConvilynError:`` — the
    documented catch-all — never saw it.

    One class, three reasons, because a caller who does not recognise a future
    ``reason`` still catches this; three classes would mean they do not. Read
    :attr:`reason` to branch:

    ``"missing"``
        No artifact of the requested kind exists. A ``partial`` run (some tasks
        failed) is the common cause — :attr:`job_status` says whether that is
        what happened.
    ``"unparsable"``
        The artifact was fetched and cannot be decoded. :attr:`detail` carries
        the decoder's own words.
    ``"too_large"``
        The artifact is real and fine, and bigger than this method's in-memory
        cap. :attr:`job_spec_id` and :attr:`artifact_id` are the two arguments
        :py:meth:`~convilyn.resources.goals.AsyncGoals.download_artifact_to`
        needs — carried here because these three methods never return a job
        handle, so before this type the advice in the message named a call the
        caller had no way to make.

    Nothing here validates: an exception whose ``__init__`` can raise replaces
    the real failure with a second one.
    """

    def __init__(
        self,
        *,
        job_spec_id: str,
        kind: GoalArtifactKind,
        reason: GoalArtifactProblem,
        job_status: str | None = None,
        artifact_id: str | None = None,
        size_bytes: int | None = None,
        max_bytes: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.job_spec_id = job_spec_id
        self.kind = kind
        self.reason = reason
        #: The terminal status the job reported — ``"completed"`` or
        #: ``"partial"``. Set for every reason; it is the only way these methods
        #: can tell a caller that some tasks failed.
        self.job_status = job_status
        #: ``None`` for ``"missing"`` — there is no artifact to name.
        self.artifact_id = artifact_id
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        #: The decoder's message, for ``"unparsable"``.
        self.detail = detail
        super().__init__(self._summary())

    def _summary(self) -> str:
        """Compose the message here so the wording has one authority."""
        label = _ARTIFACT_KIND_LABEL.get(self.kind, self.kind)
        if self.reason == "too_large":
            return (
                f"GoalJob {self.job_spec_id} artifact {self.artifact_id} is "
                f"{self.size_bytes} bytes, over this method's {self.max_bytes}-byte "
                f"in-memory cap; fetch it with goals.download_artifact_to("
                f"{self.job_spec_id!r}, {self.artifact_id!r}, to=...) instead."
            )
        if self.reason == "unparsable":
            return (
                f"GoalJob {self.job_spec_id} artifact {self.artifact_id} is not "
                f"valid {label}: {self.detail}"
            )
        status = f" Job status: {self.job_status}." if self.job_status else ""
        return (
            f"GoalJob {self.job_spec_id} did not fail, but produced no {label} "
            f"artifact — there is no result to return.{status}"
        )


class UnderstandUnavailableError(ConvilynError):
    """:py:meth:`convilyn.resources.goals.AsyncGoals.understand` could not run.

    Raised when the platform has no pipeline for the SHAPE you asked for, so
    no grounded, schema-validated result could be produced. It is raised
    **instead of** returning an ungrounded / unvalidated answer — an answer that
    was not grounded by the platform is never silently returned as if it were.

    The shapes that reach it today are multi-file and mixed-modality requests:
    one call, one file, one modality is what the understanding pipelines serve.
    Fall back to a workflow you authored (``goals.run(user_workflow_id=...)``)
    or the fixed-schema ``goals.extract(...)``.

    This used to say the capability "ships behind a platform rollout", and that
    was withdrawn rather than reworded: the switch it referred to was
    ``structured_understanding_enabled``, which was never turned ON — it was
    DELETED, because removing it was the launch. A reader who believed the old
    wording would have concluded the whole method was unavailable and stopped
    calling it, which is the opposite of what the platform does now.
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

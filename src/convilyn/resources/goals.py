"""Goals resource — agentic AI workflow execution.

Wraps Convilyn's AI workflow job API:

* ``POST /api/v1/jobs/goal``       — create an AI workflow job
* ``GET  /api/v1/jobs/goal/{id}``  — full status (drives the polling
                                      loop and the terminal return value)

HITL slot filling, WebSocket events, cancel, and retry share the
polling cadence and request shaping established here, so this resource
is extensible without rewriting the orchestration.

Design follows the same SOLID seams as
:class:`convilyn.resources.convert.AsyncConvert` (the OpenAI / Stripe
"data on model, behaviour on resource" convention).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
import warnings
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from convilyn._internal.download import download_url_to_path
from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn._internal.ws import (
    WebsocketsTransport,
    WSTransport,
    build_ws_connect_url,
    resolve_ws_url,
)
from convilyn.exceptions import (
    APIError,
    GoalJobFailedError,
    GoalJobTimeoutError,
    UnderstandUnavailableError,
    WebSocketError,
)
from convilyn.types import Artifact, ArtifactDownload, GoalEvent, GoalJob, PendingSlot

# ── Tunables ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_POLL_TIMEOUT = 300.0
MAX_POLL_INTERVAL = 5.0
STALE_PROGRESS_BACKOFF_AFTER = 3
BACKOFF_FACTOR = 1.5

# extract() runs this fixed document-extraction workflow. The id is
# the workflow_id the catalog exposes (catalog.py: workflow_id = spec.spec_id),
# so start(workflow_id=...) resolves it deterministically — no NLP fallback. A
# caller-supplied output schema is not yet supported; extract() returns this
# workflow's fixed JSON shape.
EXTRACT_WORKFLOW_ID = "goal_lane.personal_document_actions"

# extract() buffers the JSON artifact in memory to parse it; refuse an absurdly
# large blob (stream those with download_artifact_to() instead).
MAX_EXTRACT_JSON_BYTES = 8 * 1024 * 1024

# run_interactive() stops the poll loop on these non-terminal HITL states (in
# addition to the terminal + slots_pending stops wait() already handles): both
# are submittable via confirm() (backend _SUBMITTABLE = {READY,
# READY_WITH_PREVIEW}); ready has no preview, ready_with_preview offers one.
_INTERACTIVE_STOP_STATUSES = frozenset({"ready", "ready_with_preview"})

# run_interactive() safety bound: cap the number of fill/confirm rounds so a
# callback that never satisfies the agent cannot loop forever.
DEFAULT_MAX_INTERACTIVE_ROUNDS = 100


async def _maybe_await(value: Any) -> Any:
    """Return ``value``, awaiting it first if it is awaitable.

    Lets ``run_interactive`` accept either sync or async callbacks — a sync
    callback returns a plain value; an async one returns a coroutine we await.
    """
    if inspect.isawaitable(value):
        return await value
    return value


class AsyncGoals:
    """Asynchronous AI workflow resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.goals``.
    Exposes ``start``, ``retrieve``, ``wait``, the ``run`` shorthand, the
    ``extract`` one-call document→JSON sugar, and ``run_interactive`` (the
    callback-driven HITL loop), along with ``fill_slot``, ``cancel``,
    ``retry``, and the WebSocket event stream.
    """

    def __init__(
        self,
        http: HTTPClient,
        *,
        ws_url: str | None = None,
        ws_transport_factory: Callable[[], WSTransport] | None = None,
    ) -> None:
        self._http = http
        self._ws_url = ws_url
        # DIP seam: production wiring passes WebsocketsTransport; tests
        # pass a fake. Default keeps the resource directly usable without
        # going through AsyncConvilyn.
        self._ws_transport_factory = ws_transport_factory or WebsocketsTransport

    # ── Public API ───────────────────────────────────────────────

    async def start(
        self,
        *,
        workflow_id: str | None = None,
        user_workflow_id: str | None = None,
        goal_text: str | None = None,
        files: list[str] | None = None,
        slots: dict[str, Any] | None = None,
        llm_config_id: str | None = None,
    ) -> GoalJob:
        """Create an AI workflow job and return the initial ``GoalJob`` state.

        Exactly one workflow source must be supplied — ``workflow_id`` (a
        built-in catalog workflow, e.g. ``"goal_lane.video_subtitle"``),
        ``user_workflow_id`` (a workflow you authored in the Builder, shaped
        ``uw_...``), or ``goal_text`` (natural-language, resolved by NLP). The
        backend rejects more-than-one or none. ``files`` is required only when
        just ``goal_text`` is given (the NLP path needs inputs to reason over);
        a ``workflow_id`` / ``user_workflow_id`` run may start with no files and
        collect them via checkpoints. The SDK validates this client-side so the
        error surfaces before the network round-trip.

        ``user_workflow_id`` runs your compiled UserWorkflow through the same
        Goal Lane agent. Per the compile-time contract it has no required slots
        (the agent never pauses to ask you) and the resulting job's spec id is
        ``user_<ownerPrefix>.<workflowId>``. Discover and manage the ``uw_``
        workflows you own via ``client.user_workflows``
        (:class:`convilyn.resources.user_workflows.AsyncUserWorkflows` —
        ``list`` / ``get`` / ``runs`` / ``export`` / ``delete``).

        ``llm_config_id`` optionally pins this run to one of your stored
        BYO-LLM provider configs (created in the console); omit it to use
        your account default. It is honoured only when BYO-LLM is enabled
        for your account — otherwise the run uses the platform provider.
        """
        self._validate_start_inputs(
            workflow_id=workflow_id,
            user_workflow_id=user_workflow_id,
            goal_text=goal_text,
            files=files,
        )
        payload: dict[str, Any] = {"fileIds": files or []}
        if workflow_id is not None:
            payload["workflowId"] = workflow_id
        if user_workflow_id is not None:
            payload["userWorkflowId"] = user_workflow_id
        if goal_text is not None:
            payload["goalText"] = goal_text
        if slots is not None:
            # The create endpoint takes ``slotAnswers: [{slotId, value}]`` (the
            # backend has no ``slots`` field and would silently drop it). Reshape
            # the friendly ``dict[slot_id, value]`` form here — same wire shape as
            # ``fill_slots`` — so pre-seeding slots at create time actually works.
            payload["slotAnswers"] = [
                {"slotId": slot_id, "value": value} for slot_id, value in slots.items()
            ]
        if llm_config_id is not None:
            payload["llmConfigId"] = llm_config_id
        return await self._create_job(payload=payload)

    async def retrieve(self, job_spec_id: str) -> GoalJob:
        """Fetch the current state of an AI workflow job."""
        return await self._poll_once(job_spec_id)

    async def wait(
        self,
        job_spec_id: str,
        *,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        idle_timeout: float | None = None,
    ) -> GoalJob:
        """Poll until the job reaches a terminal state or stops for HITL.

        Two stopping conditions return the job to the caller:

        * Terminal status (``completed`` / ``partial`` / ``cancelled``) —
          the job is done; ``partial`` means some tasks failed but the
          workflow as a whole reached its end.
        * HITL pending (``slots_pending``) — the agent is asking for user
          input; answer them with ``fill_slot()`` / ``fill_slots()`` then ``confirm()``.

        Long-running workflows: an agentic run's analyze/execute phases can
        legitimately take many minutes, so a flat ``timeout`` forces a
        choice between giving up on healthy jobs and waiting forever on
        wedged ones. ``idle_timeout`` resolves that: raise the total
        ``timeout`` generously (or to your hard deadline) and set
        ``idle_timeout`` to the longest you will tolerate WITHOUT any
        status or progress change — a job that is advancing keeps the
        loop alive, a stalled one surfaces quickly::

            job = await client.goals.wait(job_id, timeout=1800, idle_timeout=120)

        Raises:
            GoalJobFailedError: terminal status is ``failed``.
            GoalJobTimeoutError: ``timeout`` elapsed (``reason="total"``),
                or ``idle_timeout`` passed with no observable change
                (``reason="idle"`` — the job may still be healthy on a
                long phase; ``retrieve()`` before assuming failure).
        """
        return await self._wait_loop(
            job_spec_id=job_spec_id,
            timeout=timeout,
            initial_interval=poll_interval,
            idle_timeout=idle_timeout,
        )

    async def run(
        self,
        *,
        workflow_id: str | None = None,
        user_workflow_id: str | None = None,
        goal_text: str | None = None,
        files: list[str] | None = None,
        slots: dict[str, Any] | None = None,
        llm_config_id: str | None = None,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        idle_timeout: float | None = None,
    ) -> GoalJob:
        """Shortcut — ``start()`` then ``wait()``."""
        job = await self.start(
            workflow_id=workflow_id,
            user_workflow_id=user_workflow_id,
            goal_text=goal_text,
            files=files,
            slots=slots,
            llm_config_id=llm_config_id,
        )
        return await self.wait(
            job.job_spec_id,
            timeout=timeout,
            poll_interval=poll_interval,
            idle_timeout=idle_timeout,
        )

    async def extract(
        self,
        files: list[str],
        *,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        idle_timeout: float | None = None,
    ) -> Any:
        """One call: document file(s) → parsed structured JSON.

        .. deprecated::
            Use :meth:`understand` instead. ``understand(files, schema=...)``
            is the grounded, schema-constrained successor: you supply the JSON
            Schema you want and the platform returns a result that conforms to
            it and is re-grounded against the inputs BEFORE it is returned.
            ``extract()`` predates that guarantee — it runs a single fixed
            workflow (``EXTRACT_WORKFLOW_ID``) and returns whatever shape that
            workflow happens to emit, with no caller control over the schema.
            It remains for back-compat and is a thin wrapper over the same
            ``run() → artifacts() → parse`` machinery ``understand`` reuses.

        Sugar over ``start()`` → ``wait()`` → ``artifacts()`` for the common
        "image/PDF → one JSON object" case. It is **not** a new inference
        product — the understanding comes from the same platform workflow;
        this only collapses the run-then-fetch-then-parse dance into one method.

        Returns the parsed JSON of the job's primary JSON artifact (usually a
        ``dict``).

        Raises:
            ValueError: ``files`` is empty, the job stopped for slot input, it
                produced no JSON artifact, or that artifact exceeds the
                in-memory size cap / is not valid JSON.
            GoalJobFailedError, GoalJobTimeoutError: propagated from ``run()``.
        """
        warnings.warn(
            "goals.extract() is deprecated; use goals.understand(files, schema=...) "
            "for grounded, schema-constrained extraction. extract() runs a fixed "
            "workflow with no caller control over the output shape.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not files:
            raise ValueError("extract() requires at least one file id")
        job = await self.run(
            workflow_id=EXTRACT_WORKFLOW_ID,
            files=files,
            timeout=timeout,
            poll_interval=poll_interval,
            idle_timeout=idle_timeout,
        )
        if job.needs_input:
            raise ValueError(
                "extract() expected a single-step workflow, but the job stopped for "
                "slot input; use start()/fill_slot() for interactive workflows."
            )
        return await self._fetch_json_artifact(job.job_spec_id)

    async def understand(
        self,
        files: list[str],
        *,
        schema: dict[str, Any],
        instructions: str | None = None,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        idle_timeout: float | None = None,
    ) -> Any:
        """Grounded, schema-constrained understanding of file(s).

        Runs the platform's schema-constrained understanding over ``files`` and
        returns a result that conforms to ``schema`` (a **JSON Schema dict** —
        language-neutral, so the SDK adds no validation dependency) and is
        grounded by the platform BEFORE it is returned. Reliability is the
        *server's* guarantee, not per-call prompt tuning: unlike a freeform
        ``goal_text`` run, the shape is fixed by your ``schema`` and the values
        are re-grounded against the inputs. ``instructions`` optionally steer
        the extraction in natural language.

        Requires backend structured-understanding support. When the connected
        platform does not yet provide it, this raises
        :class:`~convilyn.UnderstandUnavailableError` rather than returning an
        ungrounded / unvalidated result — an answer that was not grounded by
        the platform is never silently returned as if it were.

        Raises:
            ValueError / TypeError: ``files`` is empty or ``schema`` is not a
                dict; the job stopped for slot input, produced no JSON artifact,
                or that artifact exceeds the in-memory cap / is not valid JSON.
            UnderstandUnavailableError: the backend does not (yet) accept a
                schema-constrained understanding request.
            GoalJobFailedError, GoalJobTimeoutError: the job failed / timed out.
        """
        if not files:
            raise ValueError("understand() requires at least one file id")
        if not isinstance(schema, dict):
            raise TypeError("understand() requires a JSON Schema dict for `schema`")
        payload: dict[str, Any] = {"fileIds": files, "outputSchema": schema}
        if instructions is not None:
            payload["instructions"] = instructions
        try:
            job = await self._create_job(payload=payload)
        except APIError as exc:
            if _is_understand_unsupported(exc):
                raise UnderstandUnavailableError() from exc
            raise
        # The goal-job FSM only enqueues execution on confirm (backend
        # _SUBMITTABLE = {READY, …}); a schema-routed create lands READY with
        # no slots, so confirm here or the job parks forever (first observed
        # live 2026-07-21 — 300s poll timeout at status=ready).
        if job.status == "ready":
            job = await self.confirm(job.job_spec_id)
        job = await self._wait_loop(
            job_spec_id=job.job_spec_id,
            timeout=timeout,
            initial_interval=poll_interval,
            idle_timeout=idle_timeout,
        )
        if job.needs_input:
            raise ValueError(
                "understand() expected a single-step job, but it stopped for slot "
                "input; use start()/fill_slot() for interactive workflows."
            )
        return await self._fetch_json_artifact(job.job_spec_id)

    async def run_interactive(
        self,
        *,
        on_slot: Callable[[PendingSlot, GoalJob], Any],
        workflow_id: str | None = None,
        user_workflow_id: str | None = None,
        goal_text: str | None = None,
        files: list[str] | None = None,
        on_preview: Callable[[GoalJob], Any] | None = None,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        idle_timeout: float | None = None,
        max_rounds: int = DEFAULT_MAX_INTERACTIVE_ROUNDS,
    ) -> GoalJob:
        """Drive the whole human-in-the-loop lifecycle to a terminal state.

        Collapses the hand-rolled ``slots_pending → fill_slot → confirm →
        wait`` loop (with an optional preview approval) into one call. Starts
        the job, then reacts to each stop:

        * ``slots_pending`` → calls ``on_slot(slot, job)`` for every pending
          slot and submits the answers via ``fill_slots``;
        * ``ready`` → ``confirm`` (queues execution);
        * ``ready_with_preview`` → calls ``on_preview(job)`` (default: approve),
          then ``confirm`` if it returns truthy, else ``cancel`` and return;
        * terminal → returns the job.

        ``on_slot`` / ``on_preview`` may be sync or async. ``on_slot`` returns
        the slot's answer — for a ``file`` slot that is the ``file_id`` from
        :py:meth:`convilyn.resources.files.AsyncFiles.upload` (or a list of
        ids); see :py:meth:`fill_slots` for the value contract. ``on_preview``
        returns a bool.

        Note: compiled/silent-mode workflows never stop for input, so
        ``run_interactive`` just runs them to completion (``on_slot`` is never
        called). It is for workflows that DO ask for input.

        Raises:
            GoalJobFailedError: the job ended ``failed``.
            GoalJobTimeoutError: a single wait exceeded ``timeout`` /
                ``idle_timeout``, or the loop hit ``max_rounds`` interaction
                rounds (``reason="rounds"`` — a runaway-callback guard).
        """
        if on_slot is None:
            raise TypeError("run_interactive() requires an `on_slot` callback")
        job = await self.start(
            workflow_id=workflow_id,
            user_workflow_id=user_workflow_id,
            goal_text=goal_text,
            files=files,
        )
        for _ in range(max_rounds):
            job = await self._wait_loop(
                job_spec_id=job.job_spec_id,
                timeout=timeout,
                initial_interval=poll_interval,
                idle_timeout=idle_timeout,
                extra_stop_statuses=_INTERACTIVE_STOP_STATUSES,
            )
            if job.is_terminal:
                return job
            if job.status == "slots_pending":
                answers = {
                    slot.slot_id: await _maybe_await(on_slot(slot, job))
                    for slot in job.pending_slots
                }
                job = await self.fill_slots(job.job_spec_id, answers)
                continue
            if job.status == "ready_with_preview":
                approved = True
                if on_preview is not None:
                    approved = bool(await _maybe_await(on_preview(job)))
                if not approved:
                    return await self.cancel(job.job_spec_id)
                job = await self.confirm(job.job_spec_id)
                continue
            if job.status == "ready":
                job = await self.confirm(job.job_spec_id)
                continue
            # A stop status we don't drive — return rather than spin forever.
            return job
        raise GoalJobTimeoutError(
            job_spec_id=job.job_spec_id,
            elapsed=float(max_rounds),
            timeout=float(max_rounds),
            reason="rounds",
        )

    # ── Private steps (extensible) ───────────────────────────────

    @staticmethod
    def _validate_start_inputs(
        *,
        workflow_id: str | None,
        user_workflow_id: str | None,
        goal_text: str | None,
        files: list[str] | None,
    ) -> None:
        """Mirror the backend's XOR + fileIds-required rules client-side.

        Exactly one workflow source (``workflow_id`` / ``user_workflow_id`` /
        ``goal_text``) must be given; ``files`` is required only on the
        ``goal_text``-only (NLP) path. Doing the check here keeps the round-trip
        count honest — a misuse turns into ``ValueError``/``TypeError`` before
        the SDK even opens a socket.
        """
        sources = {
            "workflow_id": workflow_id,
            "user_workflow_id": user_workflow_id,
            "goal_text": goal_text,
        }
        provided = [name for name, value in sources.items() if value is not None]
        if not provided:
            raise TypeError(
                "start() requires exactly one of `workflow_id`, `user_workflow_id`, or `goal_text`"
            )
        if len(provided) > 1:
            raise TypeError(
                "start() accepts exactly one of `workflow_id`, `user_workflow_id`, "
                f"or `goal_text` — not multiple (got {', '.join(sorted(provided))})"
            )
        # Only the NLP path (goal_text alone) requires files; an explicit
        # workflow_id / user_workflow_id run may collect files via checkpoints.
        if goal_text is not None and not files:
            raise ValueError("files is required when only `goal_text` is provided")

    async def _create_job(self, *, payload: dict[str, Any]) -> GoalJob:
        response = await self._http.request("POST", "/api/v1/jobs/goal", json=payload)
        return GoalJob.model_validate(response.json())

    async def _poll_once(self, job_spec_id: str) -> GoalJob:
        response = await self._http.request("GET", f"/api/v1/jobs/goal/{job_spec_id}")
        return GoalJob.model_validate(response.json())

    async def _wait_loop(
        self,
        *,
        job_spec_id: str,
        timeout: float,
        initial_interval: float,
        idle_timeout: float | None = None,
        extra_stop_statuses: frozenset[str] = frozenset(),
    ) -> GoalJob:
        """Polling loop with stale-progress backoff.

        Mirrors :py:meth:`convilyn.resources.convert.AsyncConvert._wait_loop`
        — same cadence shape, different stopping conditions. The goal-
        lane backend will eventually surface ``suggestedPollIntervalMs``
        in the lightweight ``/status`` endpoint; a future commit can
        thread that hint in here.

        ``extra_stop_statuses`` lets a caller stop the poll on additional
        non-terminal states (``run_interactive`` passes ``ready`` /
        ``ready_with_preview`` so it can drive the HITL loop). ``wait()``
        passes none, so its public behaviour is unchanged.
        """
        start = time.monotonic()
        interval = initial_interval
        stale_count = 0
        last_progress = -1
        last_status: str | None = None
        last_change = start
        while True:
            job = await self._poll_once(job_spec_id)
            if job.is_terminal:
                return self._finalise(job)
            if job.needs_input:
                # HITL stop — answer via fill_slot()/confirm() from the
                # API to answer the slots and resume.
                return job
            if job.status in extra_stop_statuses:
                # Caller-requested non-terminal stop (e.g. ready /
                # ready_with_preview for the interactive driver).
                return job

            if job.status != last_status:
                last_status = job.status
                last_change = time.monotonic()

            if job.progress == last_progress:
                stale_count += 1
                if stale_count >= STALE_PROGRESS_BACKOFF_AFTER:
                    interval = min(interval * BACKOFF_FACTOR, MAX_POLL_INTERVAL)
                    stale_count = 0
            else:
                last_progress = job.progress
                last_change = time.monotonic()
                stale_count = 0

            now = time.monotonic()
            elapsed = now - start
            if elapsed + interval > timeout:
                raise GoalJobTimeoutError(job_spec_id=job_spec_id, elapsed=elapsed, timeout=timeout)
            if idle_timeout is not None and (now - last_change) + interval > idle_timeout:
                raise GoalJobTimeoutError(
                    job_spec_id=job_spec_id,
                    elapsed=elapsed,
                    timeout=idle_timeout,
                    reason="idle",
                )
            await asyncio.sleep(interval)

    @staticmethod
    def _finalise(job: GoalJob) -> GoalJob:
        """Translate a ``failed`` terminal into :class:`GoalJobFailedError`.

        ``partial`` and ``cancelled`` are returned as-is — they're
        terminal but not strictly failures from the SDK's perspective.
        Callers that want strict "everything succeeded" should branch
        on ``job.status == "completed"`` themselves.
        """
        if job.status == "failed":
            raise GoalJobFailedError(
                job_spec_id=job.job_spec_id,
                code=job.error_code,
                message=job.error_message,
            )
        return job

    # ── HITL actions ─────────────────────────────────────────────

    async def fill_slot(
        self,
        job_spec_id: str,
        *,
        slot_id: str,
        value: Any,
        expected_version: int | None = None,
    ) -> GoalJob:
        """Answer a single slot the agent is waiting on.

        Sugar around :py:meth:`fill_slots` — calls the same
        ``PATCH /slots`` endpoint with a one-element payload.
        """
        return await self.fill_slots(
            job_spec_id,
            {slot_id: value},
            expected_version=expected_version,
        )

    async def fill_slots(
        self,
        job_spec_id: str,
        answers: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> GoalJob:
        """Answer one or more slots in a single PATCH.

        The friendly ``dict[slot_id, value]`` form is reshaped to the
        backend's ``answers: list[{slotId, value}]`` wire shape inside
        the SDK so the public API stays Pythonic.

        ``expected_version`` is optional. Omitted (the default), the
        server conditions the write on the version it just read —
        concurrent writers still conflict, but you don't need to track
        versions. Pass ``job.item_version`` from a fresh ``retrieve()``
        for strict read-your-write optimistic locking; a mismatch
        surfaces as :class:`APIError` with status 409.

        **File-type slots (``PendingSlot.slot_type == "file"``).** The value
        is the ``file_id`` string returned by
        :py:meth:`convilyn.resources.files.AsyncFiles.upload` — pass one id
        for a single-file slot, or a **list** of ids for a slot that accepts
        several files::

            f = client.files.upload(path="claim.pdf")
            job = client.goals.fill_slot(job.job_spec_id, slot_id="claim_doc",
                                         value=f.file_id)          # one file
            job = client.goals.fill_slots(job.job_spec_id,
                                          {"receipts": [a.file_id, b.file_id]})  # many

        Each id must belong to the caller — referencing a file you do not own
        is rejected with :class:`APIError` 403 (an IDOR guard), so upload the
        files under the same account/key that runs the job.
        """
        if not answers:
            raise ValueError("answers must contain at least one slot answer")
        body: dict[str, Any] = {
            "answers": [{"slotId": slot_id, "value": value} for slot_id, value in answers.items()],
        }
        if expected_version is not None:
            body["expectedVersion"] = expected_version
        response = await self._http.request(
            "PATCH", f"/api/v1/jobs/goal/{job_spec_id}/slots", json=body
        )
        return GoalJob.model_validate(response.json())

    async def confirm(self, job_spec_id: str, *, expected_version: int | None = None) -> GoalJob:
        """Confirm a job whose slots are filled, queueing it for execution.

        ``expected_version`` is optional — the server re-reads the
        current version at submit time regardless (its own usage
        accounting bumps the version between your read and the submit),
        so most callers should simply omit it.

        The backend returns a ``JobSubmissionResponse`` from this call
        (a 202-style submission ack with the SQS message id); the SDK
        translates that back into a ``GoalJob`` via a follow-up
        :py:meth:`retrieve` so the public return type stays uniform
        across all action methods.
        """
        body: dict[str, Any] = {}
        if expected_version is not None:
            body["expectedVersion"] = expected_version
        # Always send a JSON object (``{}`` when empty): older backend
        # builds declare the confirm body as a required parameter and
        # reject a body-less POST with 422 before the handler runs.
        await self._http.request(
            "POST",
            f"/api/v1/jobs/goal/{job_spec_id}/confirm",
            json=body,
        )
        return await self._poll_once(job_spec_id)

    async def cancel(self, job_spec_id: str) -> GoalJob:
        """Cancel a running or queued job.

        Backend rate-limits this to 10 calls per 300 seconds per IP
        (mirrors slots / retry); transient 429s are auto-retried by
        the SDK's resilience layer.
        """
        await self._http.request("POST", f"/api/v1/jobs/goal/{job_spec_id}/cancel")
        return await self._poll_once(job_spec_id)

    async def retry(
        self,
        job_spec_id: str,
        *,
        rerun_mode: Literal["retry_same_thread", "fresh_rerun"] = "retry_same_thread",
        reason: str | None = None,
    ) -> GoalJob:
        """Retry a failed job.

        ``rerun_mode`` selects whether to resume the existing run
        thread (cheaper, picks up from the last resume boundary) or to
        start a fresh execution. ``reason`` is an optional audit string
        capped at 512 chars by the platform.
        """
        body: dict[str, Any] = {"rerunMode": rerun_mode}
        if reason is not None:
            body["reason"] = reason
        await self._http.request("POST", f"/api/v1/jobs/goal/{job_spec_id}/retry", json=body)
        return await self._poll_once(job_spec_id)

    # ── Output artifacts ─────────────────────────────────────────

    async def artifacts(self, job_spec_id: str) -> list[Artifact]:
        """List a completed job's output artifacts with fresh download URLs.

        Available once the job is terminal-successful (``completed`` or
        ``partial``); any other status surfaces the backend's 400 as
        :class:`~convilyn.APIError`. Each artifact's ``download_url`` is
        presigned and valid for one hour — re-call this method for fresh
        URLs rather than persisting one.
        """
        response = await self._http.request("GET", f"/api/v1/jobs/goal/{job_spec_id}/artifacts")
        payload = response.json()
        return [Artifact.model_validate(item) for item in payload.get("artifacts") or []]

    async def download_artifact_url(self, job_spec_id: str, artifact_id: str) -> ArtifactDownload:
        """Mint a fresh presigned download URL for one artifact.

        Prefer this over reusing a stale :py:attr:`Artifact.download_url`
        when more than an hour may have passed since ``artifacts()``.
        """
        response = await self._http.request(
            "GET",
            f"/api/v1/jobs/goal/{job_spec_id}/artifacts/{artifact_id}/download",
        )
        return ArtifactDownload.model_validate(response.json())

    async def download_artifact_to(
        self,
        job_spec_id: str,
        artifact_id: str,
        *,
        to: str | os.PathLike[str],
    ) -> Path:
        """Download one artifact to ``to`` and return the path.

        Mints a fresh presigned URL first, then streams the body to disk
        (shares :func:`convilyn._internal.download.download_url_to_path`
        with ``convert.download_to`` — same size-capped streaming and
        symlink refusal).
        """
        info = await self.download_artifact_url(job_spec_id, artifact_id)
        return await download_url_to_path(self._http, info.download_url, to)

    async def _fetch_json_artifact(self, job_spec_id: str) -> Any:
        """Fetch + parse a completed job's primary JSON artifact.

        Shared by ``extract()`` and ``understand()`` (both return the JSON body).
        """
        art = _select_json_artifact(await self.artifacts(job_spec_id))
        if art is None:
            raise ValueError(f"job {job_spec_id} produced no JSON artifact to extract")
        if art.size_bytes > MAX_EXTRACT_JSON_BYTES:
            raise ValueError(
                f"artifact {art.artifact_id} is {art.size_bytes} bytes, over the "
                f"{MAX_EXTRACT_JSON_BYTES}-byte extract() in-memory cap; use "
                "download_artifact_to() to stream it to disk instead."
            )
        info = await self.download_artifact_url(job_spec_id, art.artifact_id)
        response = await self._http.external_get(info.download_url)
        try:
            return json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"artifact {art.artifact_id} is not valid JSON: {exc}") from exc

    async def events(
        self,
        job_spec_id: str,
        *,
        ws_url: str | None = None,
    ) -> AsyncIterator[GoalEvent]:
        """Stream AI workflow execution events for a job.

        Opens a WebSocket to the configured ``ws_url``, sends the
        subscribe envelope, then yields each :class:`GoalEvent`
        received. The iterator terminates — and the connection is
        closed — when the server emits a terminal event
        (``completed`` / ``failed`` / ``cancelled``).

        Disconnects mid-stream surface as :class:`WebSocketError` so
        the caller can decide whether to inspect job state via
        :meth:`retrieve` and resubscribe. The SDK does NOT auto-reconnect
        because the backend does not replay missed events; a silent
        reconnect would hide gaps.

        .. note::
            **WebSocket streaming is not available with a ``ck_`` consumer key
            in v1** — the backend WS gateway does not authenticate ``ck_`` keys,
            so the connection is denied. Use :meth:`wait` polling (standard HTTP
            auth, works today) for the supported real-time path. This method is
            wired and ready for when the gateway gains ``ck_`` support.

        Args:
            job_spec_id: The job to subscribe to. Must be a UUID — the
                backend validates this and would otherwise close the
                connection immediately.
            ws_url: Per-call override. Overrides the constructor and
                env-var defaults.

        Yields:
            GoalEvent: one per server-pushed message, in order.

        Raises:
            WebSocketError: connection refused, transport failure,
                unparseable message, unknown event type, or a missing
                WS URL configuration.
        """
        url = build_ws_connect_url(
            resolve_ws_url(explicit=ws_url, fallback=self._ws_url),
            token=self._http.auth.bearer_token(),
        )
        transport = self._ws_transport_factory()
        try:
            try:
                await transport.connect(url)
                await transport.send(json.dumps({"action": "subscribe", "jobSpecId": job_spec_id}))
            except Exception as exc:
                # Connect / subscribe failures all surface as one
                # exception type — callers don't need to distinguish
                # DNS failure from a malformed handshake at this layer.
                # The auth token rides in the connect URL, so scrub it from
                # the exception text before it can reach a log or traceback.
                raise WebSocketError(
                    f"Failed to open event stream for job {job_spec_id}: "
                    f"{_redact_ws_token(str(exc))}. Note: the WebSocket gateway "
                    "does not accept ck_ consumer keys in v1 — use wait() polling "
                    "(HTTP auth) instead."
                ) from exc

            while True:
                try:
                    raw = await transport.recv()
                except Exception as exc:
                    raise WebSocketError(
                        f"Event stream dropped for job {job_spec_id}: {_redact_ws_token(str(exc))}"
                    ) from exc

                event = _parse_event(raw)
                yield event
                if event.is_terminal:
                    return
        finally:
            # close() is idempotent in the default transport; tests'
            # fake transport implementation should mirror that.
            await transport.close()


_WS_TOKEN_RE = re.compile(r"(token=)[^&\s\"']+", re.IGNORECASE)


def _redact_ws_token(text: str) -> str:
    """Strip a ``token=<value>`` query param from text before it is surfaced.

    The auth token travels in the WebSocket connect URL; a transport
    exception may embed that URL verbatim, so scrub it out of any string
    that could land in a log line or traceback.
    """
    return _WS_TOKEN_RE.sub(r"\1***", text)


def _select_json_artifact(artifacts: list[Artifact]) -> Artifact | None:
    """Pick the JSON artifact ``extract()`` / ``understand()`` should return.

    Prefers an ``application/json`` artifact (the primary one when flagged),
    else None — both promise JSON, so a job with no JSON output is an error
    rather than a silent fallback to some other artifact type.
    """
    json_arts = [a for a in artifacts if (a.mime_type or "").lower() == "application/json"]
    if not json_arts:
        return None
    return next((a for a in json_arts if a.is_primary), json_arts[0])


#: Create-time statuses that mean the backend did not accept a schema-constrained
#: understanding job (``understand()``). A well-formed ``output_schema`` request
#: only succeeds once the platform maps it to the structured-understanding gate;
#: until then the create is rejected (no workflow source / unrecognised field),
#: which the SDK surfaces as ``UnderstandUnavailableError`` rather than a silent
#: ungrounded result. 402 / 429 / 5xx are real conditions and propagate as-is.
_UNDERSTAND_UNSUPPORTED_STATUSES = frozenset({400, 404, 422, 501})


def _is_understand_unsupported(exc: APIError) -> bool:
    return exc.status_code in _UNDERSTAND_UNSUPPORTED_STATUSES


def _parse_event(raw: str) -> GoalEvent:
    """Translate a raw WS frame into a :class:`GoalEvent`.

    Wrapping both JSON-decode and Pydantic-validate in one helper keeps
    the streaming loop focused; both failure modes surface as
    :class:`WebSocketError` with the original payload attached.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebSocketError(f"Server sent non-JSON message: {exc}", payload=raw) from exc

    try:
        return GoalEvent.model_validate(payload)
    except ValidationError as exc:
        raise WebSocketError(
            f"Server sent unrecognised event envelope: {exc.errors()}",
            payload=raw,
        ) from exc


class Goals:
    """Synchronous facade around :class:`AsyncGoals`.

    Mirrors the async surface 1:1 so callers can switch styles without
    code change. Each call runs the underlying coroutine via the
    injected runner (the root sync client's shared private loop).
    """

    def __init__(self, async_goals: AsyncGoals, run: CoroRunner | None = None) -> None:
        self._async = async_goals
        self._run: CoroRunner = run if run is not None else asyncio.run

    def start(self, **kwargs: Any) -> GoalJob:
        return self._run(self._async.start(**kwargs))

    def retrieve(self, job_spec_id: str) -> GoalJob:
        return self._run(self._async.retrieve(job_spec_id))

    def wait(self, job_spec_id: str, **kwargs: Any) -> GoalJob:
        return self._run(self._async.wait(job_spec_id, **kwargs))

    def run(self, **kwargs: Any) -> GoalJob:
        return self._run(self._async.run(**kwargs))

    def extract(self, files: list[str], **kwargs: Any) -> Any:
        return self._run(self._async.extract(files, **kwargs))

    def understand(self, files: list[str], **kwargs: Any) -> Any:
        return self._run(self._async.understand(files, **kwargs))

    def run_interactive(self, **kwargs: Any) -> GoalJob:
        return self._run(self._async.run_interactive(**kwargs))

    def fill_slot(self, job_spec_id: str, **kwargs: Any) -> GoalJob:
        return self._run(self._async.fill_slot(job_spec_id, **kwargs))

    def fill_slots(
        self,
        job_spec_id: str,
        answers: dict[str, Any],
        **kwargs: Any,
    ) -> GoalJob:
        return self._run(self._async.fill_slots(job_spec_id, answers, **kwargs))

    def confirm(self, job_spec_id: str, **kwargs: Any) -> GoalJob:
        return self._run(self._async.confirm(job_spec_id, **kwargs))

    def cancel(self, job_spec_id: str) -> GoalJob:
        return self._run(self._async.cancel(job_spec_id))

    def retry(self, job_spec_id: str, **kwargs: Any) -> GoalJob:
        return self._run(self._async.retry(job_spec_id, **kwargs))

    def artifacts(self, job_spec_id: str) -> list[Artifact]:
        return self._run(self._async.artifacts(job_spec_id))

    def download_artifact_url(self, job_spec_id: str, artifact_id: str) -> ArtifactDownload:
        return self._run(self._async.download_artifact_url(job_spec_id, artifact_id))

    def download_artifact_to(
        self,
        job_spec_id: str,
        artifact_id: str,
        *,
        to: str | os.PathLike[str],
    ) -> Path:
        return self._run(self._async.download_artifact_to(job_spec_id, artifact_id, to=to))

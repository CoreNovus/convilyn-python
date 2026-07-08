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
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from pydantic import ValidationError

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn._internal.ws import (
    WebsocketsTransport,
    WSTransport,
    build_ws_connect_url,
    resolve_ws_url,
)
from convilyn.exceptions import GoalJobFailedError, GoalJobTimeoutError, WebSocketError
from convilyn.types import GoalEvent, GoalJob

# ── Tunables ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_POLL_TIMEOUT = 300.0
MAX_POLL_INTERVAL = 5.0
STALE_PROGRESS_BACKOFF_AFTER = 3
BACKOFF_FACTOR = 1.5


class AsyncGoals:
    """Asynchronous AI workflow resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.goals``.
    Exposes ``start``, ``retrieve``, ``wait``, and the ``run`` shorthand,
    along with ``fill_slot``, ``cancel``, ``retry``, and the WebSocket
    event stream.
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
        goal_text: str | None = None,
        files: list[str] | None = None,
        slots: dict[str, Any] | None = None,
        llm_config_id: str | None = None,
    ) -> GoalJob:
        """Create an AI workflow job and return the initial ``GoalJob`` state.

        Exactly one of ``workflow_id`` or ``goal_text`` must be supplied
        — the backend rejects either-both or either-neither. ``files``
        is required when only ``goal_text`` is given (the backend enforces
        this; the SDK validates it client-side so the error surfaces
        before the network round-trip).

        ``llm_config_id`` optionally pins this run to one of your stored
        BYO-LLM provider configs (created in the console); omit it to use
        your account default. It is honoured only when BYO-LLM is enabled
        for your account — otherwise the run uses the platform provider.
        """
        self._validate_start_inputs(workflow_id=workflow_id, goal_text=goal_text, files=files)
        payload: dict[str, Any] = {"fileIds": files or []}
        if workflow_id is not None:
            payload["workflowId"] = workflow_id
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
    ) -> GoalJob:
        """Poll until the job reaches a terminal state or stops for HITL.

        Two stopping conditions return the job to the caller:

        * Terminal status (``completed`` / ``partial`` / ``cancelled``) —
          the job is done; ``partial`` means some tasks failed but the
          workflow as a whole reached its end.
        * HITL pending (``slots_pending``) — the agent is asking for user
          input; answer them with ``fill_slot()`` / ``fill_slots()`` then ``confirm()``.

        Raises:
            GoalJobFailedError: terminal status is ``failed``.
            GoalJobTimeoutError: ``timeout`` elapsed before either
                stopping condition was met.
        """
        return await self._wait_loop(
            job_spec_id=job_spec_id,
            timeout=timeout,
            initial_interval=poll_interval,
        )

    async def run(
        self,
        *,
        workflow_id: str | None = None,
        goal_text: str | None = None,
        files: list[str] | None = None,
        slots: dict[str, Any] | None = None,
        llm_config_id: str | None = None,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> GoalJob:
        """Shortcut — ``start()`` then ``wait()``."""
        job = await self.start(
            workflow_id=workflow_id,
            goal_text=goal_text,
            files=files,
            slots=slots,
            llm_config_id=llm_config_id,
        )
        return await self.wait(job.job_spec_id, timeout=timeout, poll_interval=poll_interval)

    # ── Private steps (extensible) ───────────────────────────────

    @staticmethod
    def _validate_start_inputs(
        *,
        workflow_id: str | None,
        goal_text: str | None,
        files: list[str] | None,
    ) -> None:
        """Mirror the backend's XOR + fileIds-required rules client-side.

        Doing the check here keeps the round-trip count honest — a
        misuse turns into ``ValueError``/``TypeError`` before the SDK
        even opens a socket.
        """
        if workflow_id is None and goal_text is None:
            raise TypeError("start() requires either `workflow_id` or `goal_text`")
        if workflow_id is not None and goal_text is not None:
            raise TypeError("start() accepts either `workflow_id` or `goal_text`, not both")
        if workflow_id is None and not files:
            raise ValueError("files is required when `workflow_id` is not provided")

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
    ) -> GoalJob:
        """Polling loop with stale-progress backoff.

        Mirrors :py:meth:`convilyn.resources.convert.AsyncConvert._wait_loop`
        — same cadence shape, different stopping conditions. The goal-
        lane backend will eventually surface ``suggestedPollIntervalMs``
        in the lightweight ``/status`` endpoint; a future commit can
        thread that hint in here.
        """
        start = time.monotonic()
        interval = initial_interval
        stale_count = 0
        last_progress = -1
        while True:
            job = await self._poll_once(job_spec_id)
            if job.is_terminal:
                return self._finalise(job)
            if job.needs_input:
                # HITL stop — answer via fill_slot()/confirm() from the
                # API to answer the slots and resume.
                return job

            if job.progress == last_progress:
                stale_count += 1
                if stale_count >= STALE_PROGRESS_BACKOFF_AFTER:
                    interval = min(interval * BACKOFF_FACTOR, MAX_POLL_INTERVAL)
                    stale_count = 0
            else:
                last_progress = job.progress
                stale_count = 0

            elapsed = time.monotonic() - start
            if elapsed + interval > timeout:
                raise GoalJobTimeoutError(job_spec_id=job_spec_id, elapsed=elapsed, timeout=timeout)
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

        Pass ``expected_version`` to enable optimistic locking; the
        backend returns 409 (surfaced as :class:`APIError`) when the
        stored ``itemVersion`` has moved on.
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

        The backend returns a ``JobSubmissionResponse`` from this call
        (a 202-style submission ack with the SQS message id); the SDK
        translates that back into a ``GoalJob`` via a follow-up
        :py:meth:`retrieve` so the public return type stays uniform
        across all action methods.
        """
        body: dict[str, Any] = {}
        if expected_version is not None:
            body["expectedVersion"] = expected_version
        await self._http.request(
            "POST",
            f"/api/v1/jobs/goal/{job_spec_id}/confirm",
            json=body or None,
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

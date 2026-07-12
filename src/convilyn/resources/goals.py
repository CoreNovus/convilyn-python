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
import os
import re
import time
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
from convilyn.exceptions import GoalJobFailedError, GoalJobTimeoutError, WebSocketError
from convilyn.types import Artifact, ArtifactDownload, GoalEvent, GoalJob

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
        idle_timeout: float | None = None,
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
        response = await self._http.request(
            "GET", f"/api/v1/jobs/goal/{job_spec_id}/artifacts"
        )
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

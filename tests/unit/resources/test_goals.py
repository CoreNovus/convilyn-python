"""Goals.start/wait/run — logic / boundary / error / object-state.

Mirrors test_convert.py: respx mocks the HTTP layer, assertions land
on observable wire behaviour (route call counts, payload shape,
typed-exception identity) so the orchestration can evolve without
test churn.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    Convilyn,
    GoalJob,
    GoalJobFailedError,
    GoalJobTimeoutError,
)
from convilyn.resources.goals import AsyncGoals

API_BASE = "https://api.convilyn.corenovus.com"


# ── Fixtures ─────────────────────────────────────────────────────────


def _job_response(status: str, **overrides: Any) -> dict:
    """Build a canonical GoalJobResponse wire payload."""
    base = {
        "jobSpecId": "job_test",
        "status": status,
        "progress": 0 if status in ("queued", "created", "analyzing") else 100,
        "fileIds": ["file_abc"],
        "pendingSlots": [],
        "filledSlots": {},
        "pendingInterrupts": [],
        "createdAt": "2026-05-20T12:00:00Z",
        "updatedAt": "2026-05-20T12:00:01Z",
    }
    base.update(overrides)
    return base


def _completed_job() -> dict:
    return _job_response(
        "completed",
        progress=100,
        completedAt="2026-05-20T12:00:05Z",
    )


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestGoalsLogic:
    @pytest.mark.asyncio
    async def test_run_returns_completed_job(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert isinstance(job, GoalJob)
        assert job.status == "completed"
        assert job.is_terminal
        # The POST payload must carry the workflow_id (not goal_text) path.
        sent = create.calls.last.request.read().decode().replace(" ", "")
        assert '"workflowId":"doc_analyzer"' in sent
        assert '"fileIds":["file_abc"]' in sent

    @pytest.mark.asyncio
    async def test_goal_text_path_sends_correct_payload(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    await client.goals.run(
                        goal_text="summarise the document",
                        files=["file_abc"],
                    )

        sent = create.calls.last.request.read().decode().replace(" ", "")
        assert '"goalText":"summarisethedocument"' in sent
        assert '"fileIds":["file_abc"]' in sent
        assert "workflowId" not in sent

    @pytest.mark.asyncio
    async def test_llm_config_id_serialised_when_provided(self) -> None:
        """BYO-LLM (#1856): an explicit llm_config_id is sent as ``llmConfigId``."""
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.start(
                    workflow_id="doc_analyzer",
                    files=["file_abc"],
                    llm_config_id="cfg_openai_1",
                )

        sent = create.calls.last.request.read().decode().replace(" ", "")
        assert '"llmConfigId":"cfg_openai_1"' in sent

    @pytest.mark.asyncio
    async def test_llm_config_id_absent_when_omitted(self) -> None:
        """No llm_config_id → the key is omitted (default-config path)."""
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.start(workflow_id="doc_analyzer", files=["file_abc"])

        sent = create.calls.last.request.read().decode()
        assert "llmConfigId" not in sent

    @pytest.mark.asyncio
    async def test_slots_serialised_as_slot_answers(self) -> None:
        """start(slots=) must send ``slotAnswers=[{slotId,value}]`` — the create
        endpoint has no ``slots`` field, so the old ``slots`` payload was
        silently dropped and pre-seeded slots never reached the backend."""
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("ready"))
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.start(
                    workflow_id="goal_lane.ad_creative_analyzer",
                    files=["file_abc"],
                    slots={"industry_vertical": "saas"},
                )

        sent = create.calls.last.request.read().decode().replace(" ", "")
        assert '"slotAnswers":[{"slotId":"industry_vertical","value":"saas"}]' in sent
        assert '"slots":' not in sent


# ── 2. Boundary — input validation ──────────────────────────────────


class TestGoalsBoundary:
    @pytest.mark.asyncio
    async def test_neither_workflow_nor_goal_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="`workflow_id` or `goal_text`"):
                await client.goals.start(files=["file_abc"])

    @pytest.mark.asyncio
    async def test_both_workflow_and_goal_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="not both"):
                await client.goals.start(workflow_id="x", goal_text="y", files=["file_abc"])

    @pytest.mark.asyncio
    async def test_goal_text_without_files_raises_value_error(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="files is required"):
                await client.goals.start(goal_text="summarise this")

    @pytest.mark.asyncio
    async def test_workflow_id_path_can_omit_files(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued", fileIds=[]))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.start(workflow_id="doc_analyzer")
        assert job.status == "queued"


# ── 3. Error — failed / 404 / timeout each surface typed exceptions ─


class TestGoalsErrors:
    @pytest.mark.asyncio
    async def test_failed_status_raises_goal_job_failed_error(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(
                    200,
                    json=_job_response(
                        "failed",
                        errorCode="WORKFLOW_FAILED",
                        errorMessage="bad input",
                    ),
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(GoalJobFailedError) as info:
                        await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])
        assert info.value.code == "WORKFLOW_FAILED"
        assert info.value.job_spec_id == "job_test"

    @pytest.mark.asyncio
    async def test_retrieve_404_raises_api_error(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_missing").mock(
                return_value=httpx.Response(404, json={"code": "JOB_NOT_FOUND", "message": "..."})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.retrieve("job_missing")
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wait_timeout_raises_goal_job_timeout_error(self) -> None:
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_slow").mock(
                return_value=httpx.Response(200, json=_job_response("executing"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(GoalJobTimeoutError) as info:
                        await client.goals.wait("job_slow", timeout=0.01, poll_interval=0.5)
        assert info.value.job_spec_id == "job_slow"


# ── 4. Object-state — HITL stop + partial terminal + sync wrapper ──


class TestGoalsObjectState:
    @pytest.mark.asyncio
    async def test_slots_pending_stops_polling_returns_job(self) -> None:
        """``slots_pending`` is a non-terminal stop — ``wait()`` returns
        the job so the caller can fill the slot via a follow-up commit's
        API instead of spinning forever.
        """
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(
                    200,
                    json=_job_response(
                        "slots_pending",
                        pendingSlots=[
                            {
                                "slotId": "language",
                                "slotType": "choice",
                                "question": "Output language?",
                                "options": ["en", "zh"],
                                "required": True,
                            }
                        ],
                    ),
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert job.status == "slots_pending"
        assert job.needs_input
        assert not job.is_terminal
        assert len(job.pending_slots) == 1
        assert job.pending_slots[0].slot_id == "language"

    @pytest.mark.asyncio
    async def test_partial_terminal_returns_without_raising(self) -> None:
        """``partial`` is terminal-but-not-failure — return, do not raise.

        Strict callers can branch on ``status == "completed"`` themselves.
        """
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("partial", progress=100))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert job.status == "partial"
        assert job.is_terminal

    def test_sync_wrapper_returns_same_goal_job_type(self) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )

            client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
            try:
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])
            finally:
                client.close()

        assert isinstance(job, GoalJob)
        assert job.is_terminal

    def test_goals_resource_construction(self) -> None:
        """Smoke — `client.goals` is wired on both clients."""
        client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert client.goals is not None
            assert isinstance(client.async_client.goals, AsyncGoals)
        finally:
            client.close()


# ── R2 commit 2: HITL slot filling + confirm / cancel / retry ───────


class TestFillSlots:
    """``fill_slot`` / ``fill_slots`` translate the Pythonic ``dict``
    shape into the backend's ``list[{slotId, value}]`` wire format and
    return the refreshed :class:`GoalJob`.
    """

    @pytest.mark.asyncio
    async def test_fill_slots_sends_list_payload(self) -> None:
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(
                200,
                json=_job_response("ready", filledSlots={"language": "en", "tone": "formal"}),
            )

        async with respx.mock(assert_all_called=True) as mock:
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.fill_slots(
                    "job_test", {"language": "en", "tone": "formal"}
                )

        # Wire body must be {"answers": [{"slotId": ..., "value": ...}, ...]}
        import json as _json

        body = _json.loads(captured[0])
        assert "answers" in body
        assert isinstance(body["answers"], list)
        assert {"slotId": "language", "value": "en"} in body["answers"]
        assert {"slotId": "tone", "value": "formal"} in body["answers"]
        # Response wired through to GoalJob
        assert job.filled_slots == {"language": "en", "tone": "formal"}

    @pytest.mark.asyncio
    async def test_fill_slot_equals_single_key_fill_slots(self) -> None:
        """``fill_slot`` is a one-shot alias for ``fill_slots({k: v})``."""
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(200, json=_job_response("ready"))

        async with respx.mock(assert_all_called=True) as mock:
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.fill_slot("job_test", slot_id="x", value=1)

        import json as _json

        body = _json.loads(captured[0])
        assert body["answers"] == [{"slotId": "x", "value": 1}]

    @pytest.mark.asyncio
    async def test_expected_version_included_when_supplied(self) -> None:
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(200, json=_job_response("ready"))

        async with respx.mock(assert_all_called=True) as mock:
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.fill_slots("job_test", {"x": 1}, expected_version=7)

        import json as _json

        body = _json.loads(captured[0])
        assert body["expectedVersion"] == 7

    @pytest.mark.asyncio
    async def test_empty_answers_raises_value_error(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="at least one slot"):
                await client.goals.fill_slots("job_test", {})

    @pytest.mark.asyncio
    async def test_version_conflict_surfaces_as_api_error(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(
                return_value=httpx.Response(
                    409,
                    json={
                        "code": "VERSION_CONFLICT",
                        "message": "item moved on",
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.fill_slots("job_test", {"x": 1}, expected_version=1)
        assert info.value.status_code == 409


# ── confirm / cancel / retry ─────────────────────────────────────────


class TestConfirmCancelRetry:
    """``confirm``, ``cancel``, ``retry`` hit the right endpoint and
    return a refreshed :class:`GoalJob` via an internal poll.
    """

    @pytest.mark.asyncio
    async def test_confirm_posts_and_refetches_job(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            confirm_route = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(
                    202,
                    json={
                        "jobSpecId": "job_test",
                        "status": "queued",
                        "messageId": "msg_abc",
                        "submittedAt": "2026-05-20T12:00:00Z",
                    },
                )
            )
            get_route = mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("queued"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.confirm("job_test")

        assert confirm_route.called
        assert get_route.called
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_cancel_returns_refreshed_goal_job(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/cancel").mock(
                return_value=httpx.Response(
                    202,
                    json={"jobSpecId": "job_test", "status": "cancelled"},
                )
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("cancelled", progress=42))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.cancel("job_test")

        assert job.status == "cancelled"
        assert job.is_terminal

    @pytest.mark.asyncio
    async def test_retry_sends_default_rerun_mode(self) -> None:
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(
                202,
                json={
                    "jobSpecId": "job_test",
                    "status": "queued",
                    "messageId": "msg_x",
                    "submittedAt": "2026-05-20T12:00:00Z",
                },
            )

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/retry").mock(side_effect=_capture)
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("queued"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.retry("job_test", reason="user requested")

        import json as _json

        body = _json.loads(captured[0])
        assert body["rerunMode"] == "retry_same_thread"
        assert body["reason"] == "user requested"

    @pytest.mark.asyncio
    async def test_cancel_409_propagates(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/cancel").mock(
                return_value=httpx.Response(
                    409,
                    json={"code": "CONFLICT", "message": "already terminal"},
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.cancel("job_test")
        assert info.value.status_code == 409

    def test_sync_wrappers_present(self) -> None:
        client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            for method_name in ("fill_slot", "fill_slots", "confirm", "cancel", "retry"):
                assert callable(getattr(client.goals, method_name))
        finally:
            client.close()


class TestGoalJobWireNullCoercion:
    """Regression: the backend sends `null` for empty collections (e.g.
    `pendingInterrupts: null`); GoalJob must coerce them to empty containers
    rather than fail validation against the non-optional list/dict types."""

    def test_null_collections_coerce_to_empty(self) -> None:
        job = GoalJob.model_validate(
            {
                "jobSpecId": "job_x",
                "status": "ready",
                "createdAt": "2026-07-04T00:00:00Z",
                "updatedAt": "2026-07-04T00:00:00Z",
                "pendingInterrupts": None,
                "fileIds": None,
                "pendingSlots": None,
                "filledSlots": None,
            }
        )
        assert job.pending_interrupts == []
        assert job.file_ids == []
        assert job.pending_slots == []
        assert job.filled_slots == {}


# ── WS token redaction (never leak the bearer in an error string) ───


class TestWsTokenRedaction:
    def test_token_query_param_scrubbed(self):
        from convilyn.resources.goals import _redact_ws_token

        raw = "connect failed: wss://gw.example.com/v1?token=ck_secretvalue123 timeout"
        out = _redact_ws_token(raw)

        assert "ck_secretvalue123" not in out and "token=***" in out

    def test_non_token_text_unchanged(self):
        from convilyn.resources.goals import _redact_ws_token

        assert _redact_ws_token("plain DNS failure, no url") == "plain DNS failure, no url"

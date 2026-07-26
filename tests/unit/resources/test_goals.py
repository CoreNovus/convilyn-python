"""Goals.start/wait/run — logic / boundary / error / object-state.

Mirrors test_convert.py: respx mocks the HTTP layer, assertions land
on observable wire behaviour (route call counts, payload shape,
typed-exception identity) so the orchestration can evolve without
test churn.
"""

from __future__ import annotations

import json
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
    UnderstandUnavailableError,
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

    @pytest.mark.asyncio
    async def test_user_workflow_id_serialised_as_user_workflow_id(self) -> None:
        """start(user_workflow_id=) must send ``userWorkflowId`` (#2546) — the
        typed uw_* surface so callers no longer need the raw-request escape hatch."""
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued", fileIds=[]))
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.start(user_workflow_id="uw_abc123")

        sent = create.calls.last.request.read().decode().replace(" ", "")
        assert '"userWorkflowId":"uw_abc123"' in sent
        assert '"workflowId"' not in sent
        assert '"goalText"' not in sent


# ── 2. Boundary — input validation ──────────────────────────────────


class TestGoalsBoundary:
    @pytest.mark.asyncio
    async def test_neither_workflow_nor_goal_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="exactly one of"):
                await client.goals.start(files=["file_abc"])

    @pytest.mark.asyncio
    async def test_both_workflow_and_goal_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="not multiple"):
                await client.goals.start(workflow_id="x", goal_text="y", files=["file_abc"])

    @pytest.mark.asyncio
    async def test_workflow_id_and_user_workflow_id_raises_typeerror(self) -> None:
        """The three workflow sources are mutually exclusive (#2546)."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="not multiple"):
                await client.goals.start(workflow_id="wf", user_workflow_id="uw_abc")

    @pytest.mark.asyncio
    async def test_user_workflow_id_and_goal_text_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="not multiple"):
                await client.goals.start(
                    user_workflow_id="uw_abc", goal_text="y", files=["file_abc"]
                )

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

    @pytest.mark.asyncio
    async def test_user_workflow_id_path_can_omit_files(self) -> None:
        """A uw_* run may start with no files (collected via checkpoints) — #2546."""
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("queued", fileIds=[]))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.start(user_workflow_id="uw_abc123")
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
        # Regression (B1): confirm without expected_version must send an
        # EMPTY JSON OBJECT, not a body-less POST — backends that declare
        # the body as a required param 422 on a missing body.
        assert confirm_route.calls.last.request.read() == b"{}"

    @pytest.mark.asyncio
    async def test_confirm_sends_expected_version_when_supplied(self) -> None:
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
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("queued"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.confirm("job_test", expected_version=4)

        import json as _json

        assert _json.loads(confirm_route.calls.last.request.read()) == {"expectedVersion": 4}

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


# ── idle_timeout — status-aware waiting (B5) ─────────────────────────


class _FakeClock:
    """Deterministic monotonic clock advanced by the patched sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


class TestIdleTimeout:
    @pytest.mark.asyncio
    async def test_idle_timeout_raises_with_idle_reason(self) -> None:
        """A job with no status/progress change for idle_timeout raises
        reason='idle' long before the total timeout budget lapses."""
        clock = _FakeClock()
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_stuck").mock(
                return_value=httpx.Response(200, json=_job_response("executing", progress=40))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with (
                    patch("convilyn.resources.goals.time.monotonic", clock.monotonic),
                    patch("convilyn.resources.goals.asyncio.sleep", clock.sleep),
                ):
                    with pytest.raises(GoalJobTimeoutError) as info:
                        await client.goals.wait(
                            "job_stuck",
                            timeout=1000.0,
                            poll_interval=1.0,
                            idle_timeout=2.0,
                        )

        assert info.value.reason == "idle"
        assert info.value.timeout == 2.0
        assert "no status/progress change" in str(info.value)

    @pytest.mark.asyncio
    async def test_progress_changes_keep_idle_loop_alive(self) -> None:
        """A job that keeps advancing never trips idle_timeout even when
        each phase individually exceeds it in wall-clock terms."""
        clock = _FakeClock()
        responses = [
            httpx.Response(200, json=_job_response("executing", progress=p))
            for p in (10, 20, 30, 40)
        ] + [httpx.Response(200, json=_completed_job())]
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_moving").mock(side_effect=responses)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with (
                    patch("convilyn.resources.goals.time.monotonic", clock.monotonic),
                    patch("convilyn.resources.goals.asyncio.sleep", clock.sleep),
                ):
                    job = await client.goals.wait(
                        "job_moving",
                        timeout=1000.0,
                        poll_interval=1.0,
                        idle_timeout=2.0,
                    )

        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_total_timeout_unchanged_reason_is_total(self) -> None:
        """Backward compat: without idle_timeout the flat budget still
        raises, and the exception's reason defaults to 'total'."""
        clock = _FakeClock()
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_slow").mock(
                return_value=httpx.Response(200, json=_job_response("executing"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with (
                    patch("convilyn.resources.goals.time.monotonic", clock.monotonic),
                    patch("convilyn.resources.goals.asyncio.sleep", clock.sleep),
                ):
                    with pytest.raises(GoalJobTimeoutError) as info:
                        await client.goals.wait("job_slow", timeout=3.0, poll_interval=1.0)

        assert info.value.reason == "total"

    def test_sync_wrapper_forwards_idle_timeout(self) -> None:
        with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_done").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )
            client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
            try:
                job = client.goals.wait("job_done", idle_timeout=60.0)
            finally:
                client.close()
        assert job.is_terminal


# ── Output artifacts (list / presign / download-to-disk) ────────────


def _artifact_payload(**overrides: Any) -> dict:
    base = {
        "artifactId": "art_1",
        "fileName": "summary.xlsx",
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sizeBytes": 2048,
        "downloadUrl": "https://storage.example.com/bucket/summary.xlsx?sig=abc",
        "artifactType": "spreadsheet",
        "platform": None,
        "metadata": None,
        "isPrimary": True,
        "description": "Vendor-grouped expense summary",
    }
    base.update(overrides)
    return base


class TestArtifacts:
    @pytest.mark.asyncio
    async def test_artifacts_returns_typed_list(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "artifacts": [
                            _artifact_payload(),
                            _artifact_payload(artifactId="art_2", isPrimary=False),
                        ],
                        "total": 2,
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                artifacts = await client.goals.artifacts("job_test")

        assert len(artifacts) == 2
        assert artifacts[0].artifact_id == "art_1"
        assert artifacts[0].is_primary
        assert artifacts[0].download_url and artifacts[0].download_url.startswith("https://")

    @pytest.mark.asyncio
    async def test_artifacts_not_downloadable_surfaces_api_error(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
                return_value=httpx.Response(
                    400,
                    json={"code": "BAD_REQUEST", "message": "Job is not in a downloadable state"},
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.artifacts("job_test")
        assert info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_download_artifact_url_returns_presign_info(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_1/download").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "downloadUrl": "https://storage.example.com/bucket/summary.xlsx?sig=xyz",
                        "fileName": "summary.xlsx",
                        "sizeBytes": 2048,
                        "mimeType": "application/octet-stream",
                        "expiresAt": "2026-07-11T13:00:00Z",
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                info = await client.goals.download_artifact_url("job_test", "art_1")

        assert info.file_name == "summary.xlsx"
        assert info.download_url.endswith("sig=xyz")

    @pytest.mark.asyncio
    async def test_download_artifact_to_streams_to_disk(self, tmp_path) -> None:
        storage_url = "https://storage.example.com/bucket/summary.xlsx?sig=xyz"
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_1/download").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "downloadUrl": storage_url,
                        "fileName": "summary.xlsx",
                        "sizeBytes": 9,
                        "mimeType": "application/octet-stream",
                        "expiresAt": "2026-07-11T13:00:00Z",
                    },
                )
            )
            mock.get(storage_url).mock(return_value=httpx.Response(200, content=b"PK\x03\x04zip"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                target = tmp_path / "out.xlsx"
                returned = await client.goals.download_artifact_to("job_test", "art_1", to=target)

        assert returned == target
        assert target.read_bytes().startswith(b"PK")

    @pytest.mark.asyncio
    async def test_download_artifact_to_refuses_symlink_target(self, tmp_path) -> None:
        link = tmp_path / "out.xlsx"
        try:
            link.symlink_to(tmp_path / "elsewhere.xlsx")
        except OSError as exc:  # Windows without symlink privilege / Developer Mode
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_1/download").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "downloadUrl": "https://storage.example.com/x?sig=1",
                        "fileName": "out.xlsx",
                        "sizeBytes": 1,
                        "mimeType": "application/octet-stream",
                        "expiresAt": "2026-07-11T13:00:00Z",
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="symlink"):
                    await client.goals.download_artifact_to("job_test", "art_1", to=link)

    def test_sync_wrappers_present(self) -> None:
        client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            for method_name in ("artifacts", "download_artifact_url", "download_artifact_to"):
                assert callable(getattr(client.goals, method_name))
        finally:
            client.close()


# ── extract() — one-call document → JSON sugar (SDK E-4) ────────────


def _json_artifact_payload(**overrides: Any) -> dict:
    base = _artifact_payload(
        artifactId="art_json",
        fileName="document_actions.json",
        mimeType="application/json",
        sizeBytes=64,
        downloadUrl="https://storage.example.com/bucket/doc.json?sig=abc",
        artifactType="json",
        isPrimary=True,
    )
    base.update(overrides)
    return base


def _mock_extract_chain(mock: respx.Router, *, artifacts: list[dict], storage_json: Any) -> Any:
    """Wire the full start→poll→artifacts→download→storage chain; return the POST route."""
    post = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
        return_value=httpx.Response(200, json=_job_response("analyzing"))
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
        return_value=httpx.Response(200, json=_completed_job())
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": artifacts, "total": len(artifacts)})
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_json/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "downloadUrl": "https://storage.example.com/bucket/doc.json?sig=xyz",
                "fileName": "document_actions.json",
                "sizeBytes": 64,
                "mimeType": "application/json",
                "expiresAt": "2026-07-12T13:00:00Z",
            },
        )
    )
    mock.get("https://storage.example.com/bucket/doc.json?sig=xyz").mock(
        return_value=httpx.Response(200, content=json.dumps(storage_json).encode("utf-8"))
    )
    return post


class TestExtract:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_and_targets_extract_workflow(self) -> None:
        from convilyn.resources.goals import EXTRACT_WORKFLOW_ID

        result = {"documents": [{"doc_type": "bill", "amount": 1200}]}
        async with respx.mock(assert_all_called=True) as mock:
            post = _mock_extract_chain(
                mock, artifacts=[_json_artifact_payload()], storage_json=result
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                parsed = await client.goals.extract(["file_abc"])

        assert parsed == result
        assert json.loads(post.calls.last.request.content)["workflowId"] == EXTRACT_WORKFLOW_ID

    @pytest.mark.asyncio
    async def test_emits_deprecation_warning_steering_to_understand(self) -> None:
        # extract() is superseded by understand(); the call still works (thin
        # wrapper, behaviour unchanged) but warns to steer migration.
        async with respx.mock(assert_all_called=True) as mock:
            _mock_extract_chain(
                mock, artifacts=[_json_artifact_payload()], storage_json={"documents": []}
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.warns(DeprecationWarning, match="use goals.understand"):
                    await client.goals.extract(["file_abc"])

    @pytest.mark.asyncio
    async def test_empty_files_raises_before_network(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="at least one file"):
                await client.goals.extract([])

    @pytest.mark.asyncio
    async def test_no_json_artifact_raises(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(200, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
                return_value=httpx.Response(
                    200, json={"artifacts": [_artifact_payload()], "total": 1}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="no JSON artifact"):
                    await client.goals.extract(["file_abc"])

    @pytest.mark.asyncio
    async def test_oversized_json_artifact_raises(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(200, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "artifacts": [_json_artifact_payload(sizeBytes=64 * 1024 * 1024)],
                        "total": 1,
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="in-memory cap"):
                    await client.goals.extract(["file_abc"])

    def test_select_json_artifact_prefers_primary(self) -> None:
        from convilyn.resources.goals import _select_json_artifact
        from convilyn.types import Artifact

        arts = [
            Artifact.model_validate(_json_artifact_payload(artifactId="j1", isPrimary=False)),
            Artifact.model_validate(_json_artifact_payload(artifactId="j2", isPrimary=True)),
        ]
        assert _select_json_artifact(arts).artifact_id == "j2"

    def test_select_json_artifact_none_when_no_json(self) -> None:
        from convilyn.resources.goals import _select_json_artifact
        from convilyn.types import Artifact

        arts = [Artifact.model_validate(_artifact_payload())]
        assert _select_json_artifact(arts) is None

    def test_sync_wrapper_present(self) -> None:
        client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert callable(client.goals.extract)
        finally:
            client.close()


# ── run_interactive() — callback-driven HITL loop (SDK F-5) ─────────


def _pending_slot_payload(**overrides: Any) -> dict:
    base = {
        "slotId": "which_doc",
        "slotType": "text",
        "question": "Which document?",
        "options": None,
        "required": True,
        "isDisambiguation": False,
        "suggestedValue": None,
        "suggestedConfidence": None,
    }
    base.update(overrides)
    return base


def _slots_pending_job() -> dict:
    return _job_response("slots_pending", pendingSlots=[_pending_slot_payload()])


def _confirm_ack() -> dict:
    # confirm() ignores this body and re-reads via GET; only the 202 matters.
    return {"jobSpecId": "job_test", "status": "executing", "messageId": "m1"}


class TestRunInteractive:
    @pytest.mark.asyncio
    async def test_slot_then_confirm_runs_to_completion(self) -> None:
        seen: list[str] = []

        async def on_slot(slot, job):
            seen.append(slot.slot_id)
            return "invoice"

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_slots_pending_job()),  # wait -> slots_pending
                    httpx.Response(200, json=_job_response("ready")),  # wait -> ready
                    httpx.Response(200, json=_job_response("executing")),  # confirm's poll
                    httpx.Response(200, json=_completed_job()),  # wait -> completed
                ]
            )
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(202, json=_confirm_ack())
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.run_interactive(workflow_id="claim_flow", on_slot=on_slot)

        assert job.status == "completed"
        assert seen == ["which_doc"]

    @pytest.mark.asyncio
    async def test_preview_approved_confirms(self) -> None:
        previews: list[str] = []

        async def on_preview(job):
            previews.append(job.status)
            return True

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("ready_with_preview")),
                    httpx.Response(200, json=_job_response("executing")),  # confirm's poll
                    httpx.Response(200, json=_completed_job()),
                ]
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(202, json=_confirm_ack())
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.run_interactive(
                    workflow_id="claim_flow",
                    on_slot=lambda s, j: "x",
                    on_preview=on_preview,
                )

        assert job.status == "completed"
        assert previews == ["ready_with_preview"]

    @pytest.mark.asyncio
    async def test_preview_rejected_cancels(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("ready_with_preview")),
                    httpx.Response(200, json=_job_response("cancelled")),  # cancel's poll
                ]
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/cancel").mock(
                return_value=httpx.Response(200, json=_job_response("cancelled"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.run_interactive(
                    workflow_id="claim_flow",
                    on_slot=lambda s, j: "x",
                    on_preview=lambda j: False,
                )

        assert job.status == "cancelled"

    @pytest.mark.asyncio
    async def test_no_input_runs_straight_to_completion(self) -> None:
        # Silent-mode workflows never stop for input — on_slot is never called.
        def on_slot(slot, job):
            raise AssertionError("on_slot must not be called for a no-input run")

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.run_interactive(workflow_id="silent_flow", on_slot=on_slot)

        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_max_rounds_guard_raises(self) -> None:
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            # Never leaves slots_pending — the guard must fire, not loop forever.
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_slots_pending_job())
            )
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(
                return_value=httpx.Response(200, json=_slots_pending_job())
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(GoalJobTimeoutError) as info:
                    await client.goals.run_interactive(
                        workflow_id="loop_flow", on_slot=lambda s, j: "x", max_rounds=2
                    )
        assert info.value.reason == "rounds"

    @pytest.mark.asyncio
    async def test_none_on_slot_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="on_slot"):
                await client.goals.run_interactive(workflow_id="f", on_slot=None)

    @pytest.mark.asyncio
    async def test_sync_callback_is_supported(self) -> None:
        # A plain (non-async) on_slot must work — _maybe_await passes it through.
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("analyzing"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_slots_pending_job()),
                    httpx.Response(200, json=_job_response("ready")),
                    httpx.Response(200, json=_job_response("executing")),
                    httpx.Response(200, json=_completed_job()),
                ]
            )
            mock.patch(f"{API_BASE}/api/v1/jobs/goal/job_test/slots").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(202, json=_confirm_ack())
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.run_interactive(
                    workflow_id="f", on_slot=lambda slot, job: "answer"
                )
        assert job.status == "completed"

    def test_sync_wrapper_present(self) -> None:
        client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert callable(client.goals.run_interactive)
        finally:
            client.close()


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


# ── understand() — grounded, schema-constrained understanding (#2528) ──

_SCHEMA = {
    "type": "object",
    "properties": {"vendor": {"type": "string"}, "total": {"type": "number"}},
    "required": ["vendor", "total"],
}


class TestUnderstand:
    @pytest.mark.asyncio
    async def test_returns_grounded_json_and_sends_output_schema(self) -> None:
        result = {"vendor": "Acme", "total": 1200}
        async with respx.mock(assert_all_called=True) as mock:
            post = _mock_extract_chain(
                mock, artifacts=[_json_artifact_payload()], storage_json=result
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                parsed = await client.goals.understand(
                    ["file_abc"], schema=_SCHEMA, instructions="grab vendor + total"
                )

        assert parsed == result
        body = json.loads(post.calls.last.request.content)
        assert body["outputSchema"] == _SCHEMA
        assert body["fileIds"] == ["file_abc"]
        assert body["instructions"] == "grab vendor + total"
        # understand() is schema-driven — it never sends a workflow source.
        assert "workflowId" not in body
        assert "goalText" not in body

    @pytest.mark.asyncio
    async def test_ready_job_is_confirmed_before_waiting(self) -> None:
        # The FSM only enqueues execution on confirm — a schema-routed create
        # lands READY with no slots, so understand() must confirm or the job
        # parks forever (live regression, 2026-07-21 #2696 window). The
        # confirm is driven by the shared _wait_loop auto_confirm_ready seam
        # (#2856), so it fires off the POLLED status, not the create response.
        result = {"vendor": "Acme", "total": 1200}
        async with respx.mock(assert_all_called=True) as mock:
            _mock_extract_chain(mock, artifacts=[_json_artifact_payload()], storage_json=result)
            # Override the create route: job comes back READY, not analyzing.
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            # Poll #1 sees ready → auto-confirm (its refetch sees analyzing)
            # → poll #2 completes.
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("ready")),
                    httpx.Response(200, json=_job_response("analyzing")),
                    httpx.Response(200, json=_completed_job()),
                ]
            )
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(200, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    parsed = await client.goals.understand(["file_abc"], schema=_SCHEMA)

        assert parsed == result
        assert confirm.called

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 404, 422, 501])
    async def test_unsupported_backend_raises_understand_unavailable(self, status: int) -> None:
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(status, json={"code": "x", "message": "no"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError):
                    await client.goals.understand(["file_abc"], schema=_SCHEMA)

    @pytest.mark.asyncio
    async def test_real_conditions_propagate_not_swallowed(self) -> None:
        # A 402 (quota) is a real, actionable condition — it must NOT be masked
        # as "understand unavailable".
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(402, json={"code": "TIER_REQUIRED", "message": "pay"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.understand(["file_abc"], schema=_SCHEMA)
        assert info.value.status_code == 402
        assert not isinstance(info.value, UnderstandUnavailableError)

    @pytest.mark.asyncio
    async def test_empty_files_raises_before_network(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="at least one file"):
                await client.goals.understand([], schema=_SCHEMA)

    @pytest.mark.asyncio
    async def test_non_dict_schema_raises(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="JSON Schema dict"):
                await client.goals.understand(["file_abc"], schema="not-a-dict")  # type: ignore[arg-type]

    def test_sync_understand_is_wired(self) -> None:
        client = Convilyn(api_key="ck_x", base_url=API_BASE)  # pragma: allowlist secret
        assert hasattr(client.goals, "understand")


# ── READY-park auto-confirm (#2856) ──────────────────────────────────


class TestReadyAutoConfirm:
    """run() drives a submittable ``ready`` job through confirm via the
    shared ``_wait_loop`` seam; ``wait()`` stays a passive observer."""

    @pytest.mark.asyncio
    async def test_run_confirms_ready_job_and_converges(self) -> None:
        # READY → confirm → running → completed: the run() driver must not
        # park at ready (live regression: plain goals.run() 300s poll
        # timeout at status=ready, 2026-07-21 #2696 window).
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("created"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("ready")),
                    httpx.Response(200, json=_job_response("executing")),
                    httpx.Response(200, json=_completed_job()),
                ]
            )
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(200, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert (job.status, confirm.call_count) == ("completed", 1)

    @pytest.mark.asyncio
    async def test_ready_stall_times_out_with_single_confirm(self) -> None:
        # A job that stays ready after confirm surfaces through the normal
        # timeout — exactly ONE confirm attempt, never a confirm storm.
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("created"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(200, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(GoalJobTimeoutError):
                        await client.goals.run(
                            workflow_id="doc_analyzer", files=["file_abc"], timeout=0.05
                        )

        assert confirm.call_count == 1

    @pytest.mark.asyncio
    async def test_confirm_409_race_is_benign_and_polling_continues(self) -> None:
        # Someone else confirmed first (OCC race): the 409 is swallowed and
        # the driver keeps polling to the terminal state.
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("created"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("ready")),
                    httpx.Response(200, json=_completed_job()),
                ]
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(
                    409, json={"code": "CONFLICT", "message": "already queued"}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    job = await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_confirm_server_error_propagates(self) -> None:
        # A non-409 confirm failure is a real error — surfaced, not swallowed.
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("created"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(500, json={"code": "ERR", "message": "boom"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(APIError) as info:
                        await client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])

        assert info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_wait_stays_passive_on_ready(self) -> None:
        # wait() must never execute the caller's cost-consent step: a ready
        # job under plain wait() is observed, not confirmed.
        async with respx.mock as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("ready"))
            )
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(200, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(GoalJobTimeoutError):
                        await client.goals.wait("job_test", timeout=0.05)

        assert confirm.call_count == 0

    @pytest.mark.asyncio
    async def test_run_never_confirms_ready_with_preview(self) -> None:
        # ready_with_preview carries a preview the caller must approve —
        # run() must not auto-approve it; the stall surfaces as a timeout.
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(201, json=_job_response("created"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("ready_with_preview"))
            )
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/job_test/confirm").mock(
                return_value=httpx.Response(200, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                    with pytest.raises(GoalJobTimeoutError):
                        await client.goals.run(
                            workflow_id="doc_analyzer", files=["file_abc"], timeout=0.05
                        )

        assert confirm.call_count == 0


# ── 5. Boundary — poll-interval floor (server-load / cost guard) ──


class _LoopBreakerError(Exception):
    """Sentinel to break the otherwise-unbounded loop under a patched sleep."""


class TestGoalsPollIntervalFloor:
    """A caller-supplied ``poll_interval`` can never drive an unbounded request rate.

    ``poll_interval=0`` used to spin: the stale-progress backoff is multiplicative
    (``min(0 * BACKOFF_FACTOR, MAX) == 0``) so a zero never grew, and
    ``asyncio.sleep(0)`` yields without waiting. One misconfigured client then
    polled as fast as the network allowed for the whole timeout window — and this
    is an edge/IoT product, so a device fleet does that from many IPs at once,
    where per-IP WAF limits do not aggregate.
    """

    @pytest.mark.asyncio
    async def test_zero_poll_interval_is_clamped_to_the_floor(self) -> None:
        from convilyn.resources.goals import MIN_POLL_INTERVAL

        slept: list[float] = []

        async def _record(duration: float) -> None:
            slept.append(duration)
            if len(slept) >= 3:
                raise _LoopBreakerError

        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_spin").mock(
                return_value=httpx.Response(200, json=_job_response("executing"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", side_effect=_record):
                    with pytest.raises(_LoopBreakerError):
                        await client.goals.wait(
                            "job_spin", timeout=3600.0, poll_interval=0.0
                        )

        assert slept, "the loop never slept — it was spinning"
        assert min(slept) >= MIN_POLL_INTERVAL, (
            f"poll_interval=0 produced sleeps of {slept}; every wait must be at "
            f"least MIN_POLL_INTERVAL ({MIN_POLL_INTERVAL}s) or one client can "
            "saturate the polled endpoint."
        )

    @pytest.mark.asyncio
    async def test_generous_poll_interval_is_left_alone(self) -> None:
        """The floor clamps only downward — it must not override a slower cadence."""
        slept: list[float] = []

        async def _record(duration: float) -> None:
            slept.append(duration)
            raise _LoopBreakerError

        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/goal/job_slow").mock(
                return_value=httpx.Response(200, json=_job_response("executing"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.goals.asyncio.sleep", side_effect=_record):
                    with pytest.raises(_LoopBreakerError):
                        await client.goals.wait(
                            "job_slow", timeout=3600.0, poll_interval=2.5
                        )

        assert slept == [2.5]

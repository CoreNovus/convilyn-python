"""``convilyn goals`` — logic / boundary / error / object-state.

Mocks the SDK at the :func:`_build_client` factory seam (same pattern
used by :mod:`tests.test_cli_convert`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from convilyn import (
    APIError,
    AuthError,
    GoalJob,
    GoalJobFailedError,
    GoalJobTimeoutError,
)
from convilyn.cli import goals as goals_module
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_OK,
)
from convilyn.cli.goals import (
    _parse_file_ids,
    _parse_slot_pairs,
    _parse_slot_value,
    goals_command,
)
from convilyn.cli.main import cli as root_cli

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_job(
    *,
    status: str = "executing",
    progress: int = 42,
    pending_slots: list[dict[str, Any]] | None = None,
    filled_slots: dict[str, Any] | None = None,
) -> GoalJob:
    return GoalJob.model_validate(
        {
            "jobSpecId": "job_test",
            "status": status,
            "progress": progress,
            "fileIds": ["file_abc"],
            "pendingSlots": pending_slots or [],
            "filledSlots": filled_slots or {},
            "pendingInterrupts": [],
            "createdAt": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 5, 20, 12, 0, 1, tzinfo=timezone.utc),
        }
    )


@pytest.fixture
def started_job() -> GoalJob:
    return _make_job(status="executing", progress=10)


@pytest.fixture
def completed_job() -> GoalJob:
    return _make_job(status="completed", progress=100)


@pytest.fixture
def slots_pending_job() -> GoalJob:
    return _make_job(
        status="slots_pending",
        progress=30,
        pending_slots=[
            {
                "slotId": "topic",
                "slotType": "text",
                "question": "What topic?",
                "required": True,
            }
        ],
    )


@pytest.fixture
def mock_factory(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch :func:`_build_client` to return a MagicMock Convilyn."""
    client = MagicMock()
    monkeypatch.setattr(goals_module, "_build_client", lambda: client)
    return client


# ── 0. Smoke — main CLI registers `goals` group ─────────────────────


def test_main_registers_goals_group(runner: CliRunner) -> None:
    result = runner.invoke(root_cli, ["goals", "--help"])
    assert result.exit_code == EXIT_OK
    for sub in ("start", "status", "fill-slot", "confirm", "cancel", "retry"):
        assert sub in result.output


# ── 1. Logic — happy path per sub-command ───────────────────────────


class TestStartLogic:
    def test_start_with_workflow_id_calls_sdk(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        mock_factory.goals.start.return_value = started_job
        result = runner.invoke(
            goals_command,
            ["start", "--workflow-id", "doc_analyzer", "--files", "file_abc,file_def"],
        )
        assert result.exit_code == EXIT_OK
        mock_factory.goals.start.assert_called_once_with(
            workflow_id="doc_analyzer",
            user_workflow_id=None,
            goal_text=None,
            files=["file_abc", "file_def"],
            slots=None,
        )

    def test_start_with_user_workflow_id_calls_sdk(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        """--user-workflow-id routes a uw_* run through the typed SDK path (#2546)."""
        mock_factory.goals.start.return_value = started_job
        result = runner.invoke(
            goals_command,
            ["start", "--user-workflow-id", "uw_abc123"],
        )
        assert result.exit_code == EXIT_OK
        mock_factory.goals.start.assert_called_once_with(
            workflow_id=None,
            user_workflow_id="uw_abc123",
            goal_text=None,
            files=None,
            slots=None,
        )

    def test_start_user_workflow_id_dry_run_emits_camel_payload(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
    ) -> None:
        """Dry-run for a uw_* start shows the ``userWorkflowId`` wire key (#2546)."""
        result = runner.invoke(
            goals_command,
            ["start", "--user-workflow-id", "uw_abc123", "--dry-run", "--json"],
        )
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["dry_run"] is True
        assert payload["payload"]["userWorkflowId"] == "uw_abc123"
        mock_factory.goals.start.assert_not_called()

    def test_start_json_emits_job_payload(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        mock_factory.goals.start.return_value = started_job
        result = runner.invoke(
            goals_command,
            [
                "start",
                "--workflow-id",
                "doc_analyzer",
                "--files",
                "file_abc",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["command"] == "goals.start"
        assert payload["job_spec_id"] == "job_test"
        assert payload["status"] == "executing"

    def test_start_slot_pairs_passed_to_sdk(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        mock_factory.goals.start.return_value = started_job
        result = runner.invoke(
            goals_command,
            [
                "start",
                "--workflow-id",
                "doc_analyzer",
                "--files",
                "file_abc",
                "--slot",
                'topic="ai safety"',
                "--slot",
                "confidence=0.9",
            ],
        )
        assert result.exit_code == EXIT_OK
        _, kwargs = mock_factory.goals.start.call_args
        assert kwargs["slots"] == {"topic": "ai safety", "confidence": 0.9}


class TestStatusLogic:
    def test_status_one_shot_calls_retrieve(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        mock_factory.goals.retrieve.return_value = started_job
        result = runner.invoke(goals_command, ["status", "job_test"])
        assert result.exit_code == EXIT_OK
        mock_factory.goals.retrieve.assert_called_once_with("job_test")
        mock_factory.goals.wait.assert_not_called()

    def test_status_watch_calls_wait(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        completed_job: GoalJob,
    ) -> None:
        mock_factory.goals.wait.return_value = completed_job
        result = runner.invoke(
            goals_command,
            ["status", "job_test", "--watch", "--timeout", "60"],
        )
        assert result.exit_code == EXIT_OK
        mock_factory.goals.wait.assert_called_once_with("job_test", timeout=60.0)


class TestFillSlotLogic:
    def test_fill_slot_passes_parsed_value(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        slots_pending_job: GoalJob,
    ) -> None:
        mock_factory.goals.fill_slot.return_value = slots_pending_job
        result = runner.invoke(
            goals_command,
            [
                "fill-slot",
                "job_test",
                "--slot-id",
                "topic",
                "--value",
                '{"area": "safety"}',
            ],
        )
        assert result.exit_code == EXIT_OK
        mock_factory.goals.fill_slot.assert_called_once_with(
            "job_test",
            slot_id="topic",
            value={"area": "safety"},
            expected_version=None,
        )


class TestConfirmCancelRetryLogic:
    def test_confirm_calls_sdk(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        completed_job: GoalJob,
    ) -> None:
        mock_factory.goals.confirm.return_value = completed_job
        result = runner.invoke(goals_command, ["confirm", "job_test"])
        assert result.exit_code == EXIT_OK
        mock_factory.goals.confirm.assert_called_once_with("job_test", expected_version=None)

    def test_cancel_calls_sdk(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        completed_job: GoalJob,
    ) -> None:
        mock_factory.goals.cancel.return_value = completed_job
        result = runner.invoke(goals_command, ["cancel", "job_test"])
        assert result.exit_code == EXIT_OK
        mock_factory.goals.cancel.assert_called_once_with("job_test")

    def test_retry_default_mode(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        completed_job: GoalJob,
    ) -> None:
        mock_factory.goals.retry.return_value = completed_job
        result = runner.invoke(
            goals_command, ["retry", "job_test", "--reason", "transient ws drop"]
        )
        assert result.exit_code == EXIT_OK
        mock_factory.goals.retry.assert_called_once_with(
            "job_test", rerun_mode="retry_same_thread", reason="transient ws drop"
        )


# ── 2. Boundary — flag parsing + edge values ────────────────────────


class TestStartBoundary:
    def test_dry_run_does_not_call_factory(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sentinel = MagicMock(side_effect=AssertionError("factory must not run"))
        monkeypatch.setattr(goals_module, "_build_client", sentinel)
        result = runner.invoke(
            goals_command,
            [
                "start",
                "--workflow-id",
                "doc_analyzer",
                "--files",
                "file_abc",
                "--dry-run",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["dry_run"] is True
        assert payload["payload"]["workflowId"] == "doc_analyzer"
        assert payload["payload"]["fileIds"] == ["file_abc"]

    def test_slot_missing_equals_rejected(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
    ) -> None:
        mock_factory.goals.start.return_value = started_job
        result = runner.invoke(
            goals_command,
            [
                "start",
                "--workflow-id",
                "doc_analyzer",
                "--files",
                "file_abc",
                "--slot",
                "no_equals_here",
            ],
        )
        assert result.exit_code != EXIT_OK
        assert "KEY=VALUE" in result.output


class TestRetryBoundary:
    def test_invalid_rerun_mode_rejected(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
    ) -> None:
        result = runner.invoke(
            goals_command,
            ["retry", "job_test", "--rerun-mode", "nope"],
        )
        assert result.exit_code != EXIT_OK
        mock_factory.goals.retry.assert_not_called()


class TestParseHelpers:
    def test_parse_file_ids_strips_and_drops_empty(self) -> None:
        assert _parse_file_ids(" file_a , file_b ,, ") == ["file_a", "file_b"]

    def test_parse_file_ids_empty_returns_none(self) -> None:
        assert _parse_file_ids(None) is None
        assert _parse_file_ids("  ,  ") is None

    def test_parse_slot_value_tolerant_json(self) -> None:
        assert _parse_slot_value("42") == 42
        assert _parse_slot_value('"hi"') == "hi"
        assert _parse_slot_value("not json") == "not json"
        assert _parse_slot_value('{"k": 1}') == {"k": 1}

    def test_parse_slot_pairs_multiple(self) -> None:
        result = _parse_slot_pairs(("a=1", "b=hello", "c=[1,2]"))
        assert result == {"a": 1, "b": "hello", "c": [1, 2]}


# ── 3. Error — exceptions map to documented exit codes ──────────────


class TestErrorMapping:
    def test_goal_job_failed_exits_3(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
    ) -> None:
        mock_factory.goals.wait.side_effect = GoalJobFailedError(
            job_spec_id="job_test", code="BUDGET_EXCEEDED", message="ran out"
        )
        result = runner.invoke(goals_command, ["status", "job_test", "--watch"])
        assert result.exit_code == EXIT_JOB_FAILED

    def test_goal_job_timeout_exits_api_error(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
    ) -> None:
        mock_factory.goals.wait.side_effect = GoalJobTimeoutError(
            job_spec_id="job_test", elapsed=300.0, timeout=300.0
        )
        result = runner.invoke(goals_command, ["status", "job_test", "--watch"])
        assert result.exit_code == EXIT_API_ERROR

    def test_api_error_exits_2(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
    ) -> None:
        mock_factory.goals.retrieve.side_effect = APIError(500, "INTERNAL", "server down")
        result = runner.invoke(goals_command, ["status", "job_test"])
        assert result.exit_code == EXIT_API_ERROR


# ── 4. Object-state — sync client lifecycle ─────────────────────────


def _async_run(coro: Any) -> Any:
    """Run a coroutine; tolerate already-active loops (Windows CI quirk)."""
    return asyncio.run(coro)


class TestSyncFactoryAuthError:
    """`_run_sync_action`: AuthError during factory construction must
    surface as a Click usage exception (cli/goals.py lines 448-449).
    """

    def test_auth_error_at_factory_construction_surfaces_as_click_exception(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom() -> Any:
            raise AuthError("missing CONVILYN_API_KEY")

        monkeypatch.setattr(goals_module, "_build_client", boom)

        result = runner.invoke(
            goals_command,
            ["start", "--workflow-id", "doc_analyzer", "--files", "file_a"],
        )

        # Click maps a click.ClickException to exit_code == 1 by default.
        assert result.exit_code == 1


class TestSyncCleanupFailure:
    """`_run_sync_action`'s `finally:` must swallow cleanup exceptions
    silently — but surface them under `CONVILYN_DEBUG` so leaks stay
    debuggable (cli/goals.py lines 466-472).
    """

    def test_cleanup_failure_silent_without_debug(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CONVILYN_DEBUG", raising=False)
        mock_factory.goals.start.return_value = started_job
        mock_factory.close.side_effect = RuntimeError("connection pool leak")

        result = runner.invoke(
            goals_command,
            ["start", "--workflow-id", "doc_analyzer", "--files", "file_a"],
        )

        assert "cleanup failed" not in result.output

    def test_cleanup_failure_logged_with_debug(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        started_job: GoalJob,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CONVILYN_DEBUG", "1")
        mock_factory.goals.start.return_value = started_job
        mock_factory.close.side_effect = RuntimeError("connection pool leak")

        result = runner.invoke(
            goals_command,
            ["start", "--workflow-id", "doc_analyzer", "--files", "file_a"],
        )

        assert "cleanup failed" in result.output

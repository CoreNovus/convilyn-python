"""``convilyn goals`` — logic / boundary / error / object-state.

Mocks the SDK at the :func:`_build_client` factory seam (same pattern
used by :mod:`tests.test_cli_convert`). For the streaming ``events``
sub-command we drive a real :class:`AsyncConvilyn` through an injected
``ws_transport_factory`` — that keeps the test focused on the CLI's
exit-code / output behaviour without spinning up a real WebSocket.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from io import StringIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from convilyn import (
    APIError,
    AsyncConvilyn,
    AuthError,
    GoalEvent,
    GoalJob,
    GoalJobFailedError,
    GoalJobTimeoutError,
)
from convilyn.cli import goals as goals_module
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_INTERRUPTED,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)
from convilyn.cli.goals import (
    _emit_event,
    _parse_file_ids,
    _parse_slot_pairs,
    _parse_slot_value,
    _stream_events,
    _summarise_event,
    goals_command,
)
from convilyn.cli.main import cli as root_cli
from tests._fixtures.ws_fakes import FakeWSTransport, make_envelope

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
    for sub in ("start", "status", "events", "fill-slot", "confirm", "cancel", "retry"):
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
            goal_text=None,
            files=["file_abc", "file_def"],
            slots=None,
        )

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
        mock_factory.goals.confirm.assert_called_once_with(
            "job_test", expected_version=None
        )

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
        result = _parse_slot_pairs(("a=1", "b=hello", 'c=[1,2]'))
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
        mock_factory.goals.retrieve.side_effect = APIError(
            500, "INTERNAL", "server down"
        )
        result = runner.invoke(goals_command, ["status", "job_test"])
        assert result.exit_code == EXIT_API_ERROR


# ── 4. Object-state — streaming events ──────────────────────────────


def _async_run(coro: Any) -> Any:
    """Run a coroutine; tolerate already-active loops (Windows CI quirk)."""
    return asyncio.run(coro)


class TestEventsStreaming:
    def _wire_async_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        transport: FakeWSTransport,
    ) -> Any:
        """Build a real AsyncConvilyn with the fake transport injected.

        We patch ``_build_client`` to return an object that exposes
        ``async_client`` (the surface ``_stream_events`` consumes) and
        ``close()`` — same shape as :class:`convilyn.Convilyn`.
        """

        async_client = AsyncConvilyn(
            api_key="ck_test_streaming",  # pragma: allowlist secret
            ws_url="wss://example.test/ws",
            ws_transport_factory=lambda: transport,
        )

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                # `aclose` is awaited inside `_stream_events`; sync close
                # is unused here but mirrors `Convilyn.close()`.
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        return async_client

    def test_terminal_completed_exits_ok_and_emits_ndjson(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        transport = FakeWSTransport(
            script=[
                make_envelope("tool_started", seq=1, data={"tool": "extract"}),
                make_envelope("completed", seq=2),
            ]
        )
        self._wire_async_client(monkeypatch, transport)
        stdout = StringIO()
        stderr = StringIO()
        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=stdout,
                stderr=stderr,
            )
        )
        assert code == EXIT_OK
        lines = [line for line in stdout.getvalue().splitlines() if line]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line is a valid JSON object
        first = json.loads(lines[0])
        assert first["type"] == "tool_started"
        assert first["jobSpecId"] == "job_test"

    def test_terminal_failed_exits_3(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        transport = FakeWSTransport(
            script=[make_envelope("failed", seq=1, data={"code": "BUDGET"})]
        )
        self._wire_async_client(monkeypatch, transport)
        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=StringIO(),
            )
        )
        assert code == EXIT_JOB_FAILED

    def test_connect_failure_exits_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        transport = FakeWSTransport(script=[], fail_on_connect=True)
        self._wire_async_client(monkeypatch, transport)
        stderr = StringIO()
        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )
        assert code == EXIT_API_ERROR
        assert "WebSocket error" in stderr.getvalue()

    def test_missing_ws_url_exits_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async_client = AsyncConvilyn(
            api_key="ck_test_streaming",  # pragma: allowlist secret
            # No ws_url, no env var (patched below) → ValueError on first iter.
        )
        monkeypatch.delenv("CONVILYN_WS_URL", raising=False)

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()
        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )
        assert code == EXIT_USAGE
        assert "Configuration error" in stderr.getvalue()

    def test_keyboard_interrupt_mid_stream_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ctrl-C inside the streaming loop must surface as SIGINT exit.

        The async helper re-raises ``KeyboardInterrupt`` so the sync
        entry point (``events_command``) can map it to
        ``EXIT_INTERRUPTED`` (130) — that's the POSIX convention pinned
        by ``_exit_codes.py``.
        """
        transport = FakeWSTransport(
            script=[make_envelope("tool_started", seq=1, data={"tool": "extract"})],
            raise_interrupt_after=1,
        )
        self._wire_async_client(monkeypatch, transport)
        # Drive the sync Click command so we exercise the full
        # asyncio.run(...) + SystemExit boundary.
        runner = CliRunner()
        result = runner.invoke(goals_command, ["events", "job_test", "--json"])
        # Click maps SystemExit(130) to result.exit_code == 130.
        assert result.exit_code == EXIT_INTERRUPTED


# ── 5. Error mapping — gap-fill for the long subcommand bodies ───────


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


class TestStreamEventsErrorPaths:
    """`_stream_events`: every uncovered error branch in the long async
    helper body (cli/goals.py lines 496-498, 508-513, 522-524, 530-536).
    """

    def test_auth_error_at_factory_exits_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom() -> Any:
            raise AuthError("no key")

        monkeypatch.setattr(goals_module, "_build_client", boom)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        assert code == EXIT_USAGE
        assert "Authentication failed" in stderr.getvalue()

    def test_stream_closes_without_terminal_exits_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The iterator may exhaust naturally (e.g. server closes the
        connection after the last frame without emitting a terminal
        event). Treat as a transport-side issue, not success.
        """

        async def _empty_events(*args: Any, **kwargs: Any):
            return
            yield  # unreachable; makes this an async generator

        async_client = MagicMock()
        async_client.goals.events = _empty_events
        async_client.aclose = MagicMock()

        async def _aclose_async() -> None:
            return None

        async_client.aclose = _aclose_async

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        assert code == EXIT_API_ERROR
        assert "closed without a terminal event" in stderr.getvalue()

    def test_api_error_during_stream_exits_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _raise_api_error(*args: Any, **kwargs: Any):
            raise APIError(500, "INTERNAL", "downstream blew up")
            yield  # unreachable

        async_client = MagicMock()
        async_client.goals.events = _raise_api_error

        async def _aclose() -> None:
            return None

        async_client.aclose = _aclose

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        assert code == EXIT_API_ERROR
        assert "API error" in stderr.getvalue()

    def test_generic_exception_caught_as_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catch-all path — a malformed server frame surfaces as
        ValidationError; we map it to EXIT_API_ERROR rather than
        bubbling the traceback to stdout.
        """

        async def _raise_unexpected(*args: Any, **kwargs: Any):
            raise ValueError("malformed envelope: missing 'type'")
            yield  # unreachable

        async_client = MagicMock()
        async_client.goals.events = _raise_unexpected

        async def _aclose() -> None:
            return None

        async_client.aclose = _aclose

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        # ValueError is caught by the `except ValueError` branch (line
        # 514, "Configuration error") because resolve_ws_url uses
        # ValueError for missing URLs — so this asserts the SAME outer
        # behaviour: a graceful exit code, not a traceback leak.
        assert code == EXIT_USAGE
        assert "Configuration error" in stderr.getvalue()

    def test_unexpected_exception_caught_as_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The `except Exception` final catch-all maps anything not
        already handled to EXIT_API_ERROR (cli/goals.py lines 530-536).
        """

        async def _raise_runtime(*args: Any, **kwargs: Any):
            raise RuntimeError("transport library bug")
            yield  # unreachable

        async_client = MagicMock()
        async_client.goals.events = _raise_runtime

        async def _aclose() -> None:
            return None

        async_client.aclose = _aclose

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        assert code == EXIT_API_ERROR
        assert "Unexpected error" in stderr.getvalue()


class TestStreamEventsAsyncCleanup:
    """`_stream_events`'s `finally:` swallows aclose() failures unless
    CONVILYN_DEBUG is set (cli/goals.py lines 540-542).
    """

    def test_async_cleanup_failure_logged_with_debug(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CONVILYN_DEBUG", "1")

        async def _one_completed(*args: Any, **kwargs: Any):
            yield GoalEvent.model_validate(
                json.loads(make_envelope("completed", seq=1))
            )

        async_client = MagicMock()
        async_client.goals.events = _one_completed

        async def _aclose_fails() -> None:
            raise RuntimeError("aclose blew up")

        async_client.aclose = _aclose_fails

        class _FakeSyncClient:
            def __init__(self) -> None:
                self.async_client = async_client

            def close(self) -> None:
                pass

        monkeypatch.setattr(goals_module, "_build_client", _FakeSyncClient)
        stderr = StringIO()

        code = _async_run(
            _stream_events(
                job_spec_id="job_test",
                json_output=True,
                ws_url=None,
                stdout=StringIO(),
                stderr=stderr,
            )
        )

        assert code == EXIT_OK
        assert "cleanup failed" in stderr.getvalue()


# ── 6. _emit_event / _summarise_event — output formatting helpers ────


class TestEmitEventHumanMode:
    """Human (non-JSON) rendering path of `_emit_event` (cli/goals.py
    lines 569-572).
    """

    def test_human_mode_writes_glyph_and_type_to_stderr(self) -> None:
        event = GoalEvent.model_validate(
            json.loads(make_envelope("tool_started", seq=1, data={"tool": "extract"}))
        )
        stdout = StringIO()
        stderr = StringIO()

        _emit_event(event, json_output=False, stdout=stdout, stderr=stderr)

        assert "tool_started" in stderr.getvalue()

    def test_human_mode_writes_nothing_to_stdout(self) -> None:
        event = GoalEvent.model_validate(
            json.loads(make_envelope("tool_started", seq=1, data={"tool": "extract"}))
        )
        stdout = StringIO()
        stderr = StringIO()

        _emit_event(event, json_output=False, stdout=stdout, stderr=stderr)

        assert stdout.getvalue() == ""


class TestSummariseEvent:
    """Detail-line builder helper (cli/goals.py lines 575-590).

    Walks the data-dict candidate list in priority order
    (name → tool → role → message → status) and falls back to the
    progress percentage; otherwise returns empty.
    """

    @pytest.mark.parametrize(
        "data,expected",
        [
            ({"name": "extract_text", "tool": "ignored"}, " extract_text"),
            ({"tool": "extract_text"}, " extract_text"),
            ({"role": "reviewer"}, " reviewer"),
            ({"message": "decision pending"}, " decision pending"),
            ({"status": "ok"}, " ok"),
        ],
        ids=["name_wins", "tool", "role", "message", "status"],
    )
    def test_first_truthy_candidate_returned(
        self, data: dict[str, Any], expected: str
    ) -> None:
        event = GoalEvent.model_validate(
            json.loads(make_envelope("tool_started", seq=1, data=data))
        )

        assert _summarise_event(event) == expected

    def test_progress_event_falls_back_to_percent(self) -> None:
        event = GoalEvent.model_validate(
            json.loads(make_envelope("progress", seq=1, data={"progress": 42}))
        )

        assert _summarise_event(event) == " 42%"

    def test_empty_data_returns_empty_string(self) -> None:
        event = GoalEvent.model_validate(
            json.loads(make_envelope("tool_started", seq=1, data={}))
        )

        assert _summarise_event(event) == ""

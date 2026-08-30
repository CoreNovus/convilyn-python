"""``convilyn goals understand`` — logic / boundary / error / object-state.

Its own module, mirroring the source split: the command lives in
:mod:`convilyn.cli.goals_understand`, not in the already-large
:mod:`convilyn.cli.goals`.

Mocks the SDK at the ``convilyn.cli.goals._build_client`` factory seam — the
SAME seam every other ``goals`` sub-command uses. That is deliberate and is the
reason ``goals_understand`` reaches its factory through the module rather than
binding the name: one patch redirects the whole group, so this fixture is
byte-identical to :mod:`tests.unit.cli.test_goals`'s ``mock_factory``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from convilyn import (
    APIError,
    AuthError,
    GoalArtifactUnusableError,
    GoalJobFailedError,
    GoalJobTimeoutError,
    InsufficientCreditsError,
    UnderstandUnavailableError,
)
from convilyn.cli import goals as goals_module
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)
from convilyn.cli.goals import goals_command
from convilyn.cli.goals_understand import (
    _load_schema_file,
    _understand_payload,
    _understand_to_payload,
)
from convilyn.cli.main import cli as root_cli

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_factory(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch the group-wide :func:`_build_client` to return a MagicMock."""
    client = MagicMock()
    monkeypatch.setattr(goals_module, "_build_client", lambda: client)
    return client


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    """A minimal, valid JSON Schema on disk."""
    target = tmp_path / "schema.json"
    target.write_text(
        json.dumps({"type": "object", "properties": {"total": {"type": "number"}}}),
        encoding="utf-8",
    )
    return target


# ── 0. Smoke — the split module is registered on the group ───────────


def test_group_registers_understand(runner: CliRunner) -> None:
    """The command lives in another module; prove the wiring, not the import."""
    result = runner.invoke(root_cli, ["goals", "--help"])

    assert "understand" in result.output


def test_understand_help_is_reachable_from_the_root_cli(runner: CliRunner) -> None:
    result = runner.invoke(root_cli, ["goals", "understand", "--help"])

    assert result.exit_code == EXIT_OK


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestUnderstandLogic:
    def test_calls_sdk_with_parsed_file_ids(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {"total": 42}

        runner.invoke(
            goals_command,
            ["understand", "--files", "file_a,file_b", "--schema-file", str(schema_file)],
        )

        assert mock_factory.goals.understand.call_args.args[0] == ["file_a", "file_b"]

    def test_passes_schema_dict_from_file(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {}

        runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert mock_factory.goals.understand.call_args.kwargs["schema"]["type"] == "object"

    def test_instructions_forwarded_when_supplied(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {}

        runner.invoke(
            goals_command,
            [
                "understand",
                "--files",
                "file_a",
                "--schema-file",
                str(schema_file),
                "--instructions",
                "totals only",
            ],
        )

        assert mock_factory.goals.understand.call_args.kwargs["instructions"] == "totals only"

    def test_json_mode_emits_result_under_result_key(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {"total": 42}

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file), "--json"],
        )

        assert json.loads(result.output)["result"] == {"total": 42}

    def test_json_mode_exits_zero(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {"total": 42}

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file), "--json"],
        )

        assert result.exit_code == EXIT_OK

    def test_human_mode_prints_the_result_not_just_a_tick(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        """The result IS this command's output; a bare tick would hide it."""
        mock_factory.goals.understand.return_value = {"total": 42}

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert '"total": 42' in result.output


# ── 2. Boundary — --dry-run and the schema file ──────────────────────


class TestUnderstandDryRun:
    def test_dry_run_makes_no_sdk_call(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file), "--dry-run"],
        )

        assert mock_factory.goals.understand.call_count == 0

    def test_dry_run_json_reports_the_payload(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        result = runner.invoke(
            goals_command,
            [
                "understand",
                "--files",
                "file_a",
                "--schema-file",
                str(schema_file),
                "--dry-run",
                "--json",
            ],
        )

        assert json.loads(result.output)["payload"]["fileIds"] == ["file_a"]

    def test_dry_run_still_validates_the_schema_file(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A bad schema must fail in --dry-run too, or the preview lies."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(bad), "--dry-run"],
        )

        assert result.exit_code == EXIT_USAGE


class TestUnderstandSchemaFileBoundary:
    def test_missing_file_raises_click_exception(self, tmp_path: Path) -> None:
        with pytest.raises(click.ClickException):
            _load_schema_file(tmp_path / "nope.json")

    def test_invalid_json_raises_click_exception(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{", encoding="utf-8")

        with pytest.raises(click.ClickException):
            _load_schema_file(target)

    def test_json_array_is_rejected(self, tmp_path: Path) -> None:
        """understand() needs an object; a list would TypeError mid-call."""
        target = tmp_path / "list.json"
        target.write_text("[1, 2]", encoding="utf-8")

        with pytest.raises(click.ClickException):
            _load_schema_file(target)

    def test_valid_object_returns_the_dict(self, schema_file: Path) -> None:
        assert _load_schema_file(schema_file)["type"] == "object"

    def test_missing_schema_file_exits_usage(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(tmp_path / "nope.json")],
        )

        assert result.exit_code == EXIT_USAGE

    def test_missing_schema_file_never_reaches_the_network(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        tmp_path: Path,
    ) -> None:
        runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(tmp_path / "nope.json")],
        )

        assert mock_factory.goals.understand.call_count == 0

    def test_blank_files_value_exits_usage(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        result = runner.invoke(
            goals_command,
            ["understand", "--files", " , ", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_USAGE


# ── 3. Error — exceptions map to documented exit codes ───────────────


class TestUnderstandErrorMapping:
    def test_understand_unavailable_exits_api_error(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = UnderstandUnavailableError()

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_API_ERROR

    def test_goal_job_failed_exits_3(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = GoalJobFailedError(
            job_spec_id="job_test", code="BUDGET_EXCEEDED", message="ran out"
        )

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_JOB_FAILED

    def test_timeout_exits_api_error(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = GoalJobTimeoutError(
            job_spec_id="job_test", elapsed=300.0, timeout=300.0
        )

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_API_ERROR

    def test_api_error_exits_2(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = APIError(500, "INTERNAL", "server down")

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_API_ERROR

    def test_auth_error_exits_usage(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = AuthError("bad key")

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_USAGE

    def test_insufficient_credits_exits_usage_and_opens_the_link(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[str] = []
        monkeypatch.setattr("convilyn.cli._browser.webbrowser.open", opened.append)
        mock_factory.goals.understand.side_effect = InsufficientCreditsError(
            402,
            "INSUFFICIENT_CREDITS",
            "Not enough credits to run this workflow.",
            required_credits=12,
            available_credits=3,
            upgrade_url="https://app.test.example/billing",
        )

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_USAGE
        assert "Insufficient credits" in result.output
        assert "https://app.test.example/billing" in result.output
        assert opened == ["https://app.test.example/billing"]

    def test_slot_stop_value_error_exits_usage(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        """understand() raises ValueError when the job stopped for slot input."""
        mock_factory.goals.understand.side_effect = ValueError("stopped for slot input")

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_USAGE

    def test_an_unusable_result_exits_job_failed_not_usage(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        """A run that succeeded and produced nothing usable is not a usage
        error: the invocation was fine and it was charged. Without its own
        handler this escapes every `except` in the command as a traceback,
        because it is not an `APIError` and no longer a `ValueError`."""
        mock_factory.goals.understand.side_effect = GoalArtifactUnusableError(
            job_spec_id="job_x", kind="json", reason="missing", job_status="partial"
        )

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_JOB_FAILED

    def test_the_unusable_result_message_reaches_the_user(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.side_effect = GoalArtifactUnusableError(
            job_spec_id="job_x", kind="json", reason="missing", job_status="partial"
        )

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert "No usable result" in result.output

    def test_factory_auth_error_exits_usage(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
        schema_file: Path,
    ) -> None:
        """A bad key at CLIENT CONSTRUCTION, before any call, is still usage."""

        def _boom() -> None:
            raise AuthError("no api key configured")

        monkeypatch.setattr(goals_module, "_build_client", _boom)

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file)],
        )

        assert result.exit_code == EXIT_USAGE

    def test_cleanup_failure_does_not_flip_a_successful_exit(
        self,
        runner: CliRunner,
        mock_factory: MagicMock,
        schema_file: Path,
    ) -> None:
        mock_factory.goals.understand.return_value = {"total": 1}
        mock_factory.close.side_effect = RuntimeError("socket already gone")

        result = runner.invoke(
            goals_command,
            ["understand", "--files", "file_a", "--schema-file", str(schema_file), "--json"],
        )

        assert result.exit_code == EXIT_OK


# ── 4. Object-state — payload shapes ─────────────────────────────────


class TestUnderstandPayloadShape:
    def test_payload_omits_instructions_when_absent(self) -> None:
        payload = _understand_payload(file_ids=["f"], schema={"type": "object"}, instructions=None)

        assert "instructions" not in payload

    def test_payload_carries_output_schema_key(self) -> None:
        payload = _understand_payload(file_ids=["f"], schema={"type": "object"}, instructions=None)

        assert payload["outputSchema"] == {"type": "object"}

    def test_json_payload_has_no_summary_duplicate(self) -> None:
        payload = _understand_to_payload(["f"], {"total": 1}, json_output=True)

        assert "summary" not in payload

    def test_human_payload_summary_is_the_indented_result(self) -> None:
        payload = _understand_to_payload(["f"], {"total": 1}, json_output=False)

        assert payload["summary"] == '{\n  "total": 1\n}'

"""``convilyn feedback`` — logic / boundary / error / object-state.

Patches ``_build_client`` (the DI seam every other command test uses) and mocks
the wire with respx, so the assertions are about what is actually sent rather
than about which helper was called.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn import Convilyn
from convilyn.cli import feedback as feedback_module
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
from convilyn.cli.feedback import SURVEY_PATH, feedback_command

API_BASE = "https://api.convilyn.com"

ACK = {
    "response_id": "sr_1",
    "status": "received",
    "reward_granted": False,
    "reward_credits": 0,
    "reward_state": "disabled",
}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _build() -> Convilyn:
        return Convilyn(api_key="ck_test")  # pragma: allowlist secret

    monkeypatch.setattr(feedback_module, "_build_client", _build)


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend there is a terminal. `CliRunner`'s stdin never is one."""
    monkeypatch.setattr(feedback_module, "_stdin_is_interactive", lambda: True)


@pytest.fixture
def not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_module, "_stdin_is_interactive", lambda: False)


# ── 1. Logic ────────────────────────────────────────────────────────


class TestFeedbackLogic:
    @respx.mock
    def test_flags_post_the_closed_enum_body(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        result = runner.invoke(
            feedback_command,
            ["--purpose", "ecommerce", "--current-method", "manual"],
        )
        assert result.exit_code == EXIT_OK
        assert json.loads(route.calls[0].request.content) == {
            "purpose": "ecommerce",
            "current_method": "manual",
            "source": "cli",
        }

    @respx.mock
    def test_prompts_collect_the_answers_when_a_terminal_exists(
        self, runner: CliRunner, patched_client: None, interactive: None
    ) -> None:
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        # 3 = E-commerce listings, 1 = By hand, then the optional free text.
        result = runner.invoke(feedback_command, [], input="3\n1\nfaster batches\n")
        assert result.exit_code == EXIT_OK
        assert json.loads(route.calls[0].request.content) == {
            "purpose": "ecommerce",
            "current_method": "manual",
            "source": "cli",
            "suggestion": "faster batches",
        }

    @respx.mock
    def test_json_mode_emits_exactly_one_object_on_stdout(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        respx.post(f"{API_BASE}{SURVEY_PATH}").mock(return_value=httpx.Response(200, json=ACK))
        result = runner.invoke(
            feedback_command,
            ["--purpose", "education", "--current-method", "outsourced", "--json"],
        )
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.stdout)  # raises if it is not exactly one object
        assert payload["command"] == "feedback"
        assert payload["response_id"] == "sr_1"

    @respx.mock
    def test_the_summary_reports_credits_from_the_response(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        """42, not 30: a hardcoded grant size would pass a looser assertion."""
        respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(
                200,
                json={
                    **ACK,
                    "reward_granted": True,
                    "reward_credits": 42,
                    "reward_state": "granted",
                },
            )
        )
        result = runner.invoke(
            feedback_command,
            ["--purpose", "voiceAudio", "--current-method", "manual", "--json"],
        )
        assert "42" in json.loads(result.stdout)["summary"]

    @respx.mock
    def test_it_claims_no_credits_when_the_grant_is_switched_off(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        """`disabled` is live today, so "stored, no reward" must not mention credits."""
        respx.post(f"{API_BASE}{SURVEY_PATH}").mock(return_value=httpx.Response(200, json=ACK))
        result = runner.invoke(
            feedback_command,
            ["--purpose", "voiceAudio", "--current-method", "manual", "--json"],
        )
        assert "credits" not in json.loads(result.stdout)["summary"]


# ── 2. Boundary ─────────────────────────────────────────────────────


class TestFeedbackBoundary:
    @respx.mock
    def test_purpose_other_is_dropped_when_the_purpose_is_not_other(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        """A value nothing can interpret is worse than an absent one."""
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        runner.invoke(
            feedback_command,
            [
                "--purpose",
                "jobSearch",
                "--purpose-other",
                "ignored",
                "--current-method",
                "manual",
            ],
        )
        assert "purpose_other" not in json.loads(route.calls[0].request.content)

    @respx.mock
    def test_the_tool_name_rides_only_with_othertool(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        runner.invoke(
            feedback_command,
            ["--purpose", "jobSearch", "--current-method", "otherTool", "--tool", " Acrobat "],
        )
        assert json.loads(route.calls[0].request.content)["current_method_tool"] == "Acrobat"

    @respx.mock
    def test_a_long_suggestion_is_truncated_to_the_server_bound(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        runner.invoke(
            feedback_command,
            [
                "--purpose",
                "jobSearch",
                "--current-method",
                "manual",
                "--suggestion",
                "x" * 5000,
            ],
        )
        sent = json.loads(route.calls[0].request.content)["suggestion"]
        assert len(sent) == feedback_module.MAX_SUGGESTION_LEN

    @respx.mock
    def test_non_ascii_answers_survive_the_wire(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        """A cp950 console is the reason `_output.write_line` exists; the wire
        has its own encoding, and this pins that the two do not interfere."""
        route = respx.post(f"{API_BASE}{SURVEY_PATH}").mock(
            return_value=httpx.Response(200, json=ACK)
        )
        runner.invoke(
            feedback_command,
            ["--purpose", "jobSearch", "--current-method", "manual", "--suggestion", "更快的批次"],
        )
        assert json.loads(route.calls[0].request.content)["suggestion"] == "更快的批次"


# ── 3. Error ────────────────────────────────────────────────────────


class TestFeedbackErrors:
    @respx.mock
    def test_it_refuses_by_flag_name_when_there_is_no_terminal(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        route = respx.post(f"{API_BASE}{SURVEY_PATH}")
        result = runner.invoke(feedback_command, [])
        assert result.exit_code == EXIT_USAGE
        assert "--purpose" in result.output
        assert not route.called

    @respx.mock
    def test_json_never_prompts_even_with_a_terminal(
        self, runner: CliRunner, patched_client: None, interactive: None
    ) -> None:
        """The condition is a TTY *and* not --json, because a prompt would
        otherwise write to the stdout `--json` promises is one JSON object."""
        route = respx.post(f"{API_BASE}{SURVEY_PATH}")
        result = runner.invoke(feedback_command, ["--json"], input="3\n1\n\n")
        assert result.exit_code == EXIT_USAGE
        assert not route.called

    @respx.mock
    def test_a_server_error_maps_to_the_api_exit_code(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        respx.post(f"{API_BASE}{SURVEY_PATH}").mock(return_value=httpx.Response(500, json={}))
        result = runner.invoke(
            feedback_command,
            ["--purpose", "jobSearch", "--current-method", "manual"],
        )
        assert result.exit_code == EXIT_API_ERROR

    def test_an_unknown_purpose_is_refused_before_anything_is_sent(
        self, runner: CliRunner, patched_client: None, not_interactive: None
    ) -> None:
        """The set is closed on the server too — a 422 is a worse way to learn it."""
        result = runner.invoke(
            feedback_command,
            ["--purpose", "notACategory", "--current-method", "manual"],
        )
        assert result.exit_code != EXIT_OK


# ── 4. Object-state ─────────────────────────────────────────────────


class TestFeedbackObjectState:
    def test_the_command_is_registered_on_the_top_level_group(self) -> None:
        from convilyn.cli.main import cli

        assert "feedback" in cli.commands

    def test_the_purpose_set_matches_the_nine_categories_plus_two_escapes(self) -> None:
        """A closed set the server also enforces; drift shows up as a 422."""
        assert len(feedback_module.PURPOSE_LABELS) == 11
        assert list(feedback_module.PURPOSE_LABELS)[-2:] == ["conversionOnly", "other"]

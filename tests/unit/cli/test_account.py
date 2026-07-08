"""``convilyn account`` CLI — 4-category tests.

Patches the ``_build_client`` factory in :mod:`convilyn.cli.account` so
tests inject a ``MagicMock`` rather than spinning up an HTTP-level fake.
Mirrors the test pattern in :mod:`tests.test_cli_goals`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from convilyn import (
    APIError,
    CostEstimate,
    Plan,
    PlanRequiredError,
    QuotaCheck,
    QuotaExceededError,
    ToolCostEstimate,
)
from convilyn.cli import account as account_module
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
from convilyn.cli.account import account_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_estimate(*, tier: str = "free", state: str = "ok", micro_u: int = 100) -> CostEstimate:
    return CostEstimate(
        estimatedMicroU=micro_u,
        estimatedUsd=0.01,
        estimatedTotalMicroU=micro_u,
        estimatedMinMicroU=micro_u // 25 if micro_u else 0,
        estimatedMaxMicroU=micro_u,
        tools=[ToolCostEstimate(toolName="pdf:extract", perInvocationMicroU=10)],
        quotaCheck=QuotaCheck(
            state=state,
            tier=tier,
            estimatedMicroU=micro_u,
            thresholdMicroU=1_000_000,
        ),
    )


@pytest.fixture
def mock_factory(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(account_module, "_build_client", lambda: client)
    return client


# ── 1. Logic — happy path ───────────────────────────────────────────


class TestPlanLogic:
    def test_plan_emits_tier(self, runner: CliRunner, mock_factory: MagicMock) -> None:
        mock_factory.account.get_plan.return_value = Plan(tier="pro")
        result = runner.invoke(account_command, ["plan", "--json"])
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["command"] == "account.plan"
        assert payload["tier"] == "pro"


class TestQuotaLogic:
    def test_quota_with_tools_passes_to_sdk(
        self, runner: CliRunner, mock_factory: MagicMock
    ) -> None:
        mock_factory.account.get_quota.return_value = _make_estimate(
            tier="pro", state="ok", micro_u=50_000
        )
        result = runner.invoke(
            account_command,
            ["quota", "--tool", "pdf-mcp:extract_text", "--max-iter", "10", "--json"],
        )
        assert result.exit_code == EXIT_OK
        mock_factory.account.get_quota.assert_called_once_with(
            tools=["pdf-mcp:extract_text"], max_iterations=10
        )
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["tier"] == "pro"
        assert payload["state"] == "ok"
        assert payload["estimated_micro_u"] == 50_000


# ── 2. Boundary — no tools, missing required arg ────────────────────


class TestBoundary:
    def test_quota_no_tools_runs_with_empty_list(
        self, runner: CliRunner, mock_factory: MagicMock
    ) -> None:
        mock_factory.account.get_quota.return_value = _make_estimate()
        result = runner.invoke(account_command, ["quota"])
        assert result.exit_code == EXIT_OK
        mock_factory.account.get_quota.assert_called_once_with(tools=[], max_iterations=None)


# ── 3. Error — billing exceptions map to EXIT_USAGE; transport → EXIT_API_ERROR ──


class TestErrors:
    def test_plan_required_exits_usage(self, runner: CliRunner, mock_factory: MagicMock) -> None:
        mock_factory.account.get_quota.side_effect = PlanRequiredError(
            402, "TIER_REQUIRED", "Upgrade to Pro", upgrade_url="/pricing"
        )
        result = runner.invoke(account_command, ["quota"])
        assert result.exit_code == EXIT_USAGE
        assert "Plan required" in result.output

    def test_quota_exceeded_exits_usage(self, runner: CliRunner, mock_factory: MagicMock) -> None:
        mock_factory.account.get_quota.side_effect = QuotaExceededError(
            402,
            "QUOTA_EXCEEDED",
            "Cap reached",
            estimated_micro_u=1_200_000,
            threshold_micro_u=1_000_000,
        )
        result = runner.invoke(account_command, ["quota"])
        assert result.exit_code == EXIT_USAGE
        assert "Quota exceeded" in result.output

    def test_generic_api_error_exits_api_error(
        self, runner: CliRunner, mock_factory: MagicMock
    ) -> None:
        mock_factory.account.get_quota.side_effect = APIError(500, "INTERNAL", "Server error")
        result = runner.invoke(account_command, ["quota"])
        assert result.exit_code == EXIT_API_ERROR


# ── 4. Object-state — main CLI registers `account` group ────────────


class TestRegistration:
    def test_main_cli_registers_account_group(self, runner: CliRunner) -> None:
        from convilyn.cli.main import cli as root_cli

        result = runner.invoke(root_cli, ["account", "--help"])
        assert result.exit_code == EXIT_OK
        for sub in ("plan", "quota"):
            assert sub in result.output

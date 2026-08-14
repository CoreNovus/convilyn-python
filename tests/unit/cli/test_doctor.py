"""``convilyn doctor`` — logic / boundary / error / object-state."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
from convilyn.cli.doctor import doctor_command

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def env_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVILYN_API_KEY", "ck_dummy_key_value")  # pragma: allowlist secret


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestDoctorLogic:
    def test_with_api_key_exits_ok(self, runner: CliRunner, env_with_key: None) -> None:
        result = runner.invoke(doctor_command, [])
        assert result.exit_code == EXIT_OK
        # Stdout / stderr combine in CliRunner by default.
        assert "convilyn SDK" in result.output

    def test_json_output_is_parseable(self, runner: CliRunner, env_with_key: None) -> None:
        result = runner.invoke(doctor_command, ["--json"])
        assert result.exit_code == EXIT_OK
        # The very last line of output should be the JSON document.
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["command"] == "doctor"
        assert isinstance(payload["checks"], list)


# ── 2. Boundary — --ping toggle ─────────────────────────────────────


class TestDoctorPing:
    def test_ping_success_path(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.test.convilyn.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.test.convilyn.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            result = runner.invoke(doctor_command, ["--ping"])
        assert result.exit_code == EXIT_OK
        assert "200" in result.output


# ── 3. Error — missing config + ping failure ────────────────────────


class TestDoctorErrors:
    def test_missing_api_key_exits_usage(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        result = runner.invoke(doctor_command, [])
        assert result.exit_code == EXIT_USAGE
        assert "CONVILYN_API_KEY" in result.output

    def test_ping_unreachable_exits_api_error(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.test.convilyn.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.test.convilyn.com/api/v1/health").mock(
                side_effect=httpx.ConnectError("boom")
            )
            result = runner.invoke(doctor_command, ["--ping"])
        assert result.exit_code == EXIT_API_ERROR


# ── 4. Object-state — config knobs reflected in output ──────────────


class TestDoctorObjectState:
    def test_api_key_is_masked_in_output(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CONVILYN_API_KEY",
            "ck_very_secret_token_value_12345",  # pragma: allowlist secret
        )
        result = runner.invoke(doctor_command, [])
        assert "very_secret_token_value" not in result.output
        assert "ck_" in result.output  # tier prefix retained
        assert "2345" not in result.output  # trailing chars NOT leaked

    def test_no_ping_marks_health_as_skipped(self, runner: CliRunner, env_with_key: None) -> None:
        result = runner.invoke(doctor_command, ["--json"])
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        health = [c for c in payload["checks"] if c["name"] == "Backend health"][0]
        assert health["status"] == "SKIP"

    def test_account_tier_reported_when_ping_and_key_present(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --ping is on AND an API key is set, the tier signal is
        surfaced as an advisory line so a free user can see the upgrade
        prompt context up front."""
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.test.convilyn.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.test.convilyn.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            # The tier check calls /cost-preview under the hood.
            mock.post("https://api.test.convilyn.com/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "estimatedMicroU": 0,
                        "estimatedUsd": 0.0,
                        "estimatedTotalMicroU": 0,
                        "estimatedMinMicroU": 0,
                        "estimatedMaxMicroU": 0,
                        "tools": [],
                        "quotaCheck": {
                            "state": "ok",
                            "tier": "pro",
                            "estimatedMicroU": 0,
                            "thresholdMicroU": 1_000_000,
                        },
                    },
                )
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        assert result.exit_code == EXIT_OK
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        tier_check = [c for c in payload["checks"] if c["name"] == "Account tier"]
        assert tier_check, "tier check missing from --ping output"
        assert tier_check[0]["status"] == "OK"
        assert "pro" in tier_check[0]["detail"]

    def test_account_tier_failure_is_advisory_not_fatal(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tier-query failure must NOT flip the doctor's exit code.
        It surfaces as WARN so the operator notices, but doctor still
        exits OK if every required check passed."""
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.test.convilyn.com")
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.test.convilyn.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post("https://api.test.convilyn.com/api/v1/workflows/cost-preview").mock(
                side_effect=httpx.ConnectError("transient")
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        # Doctor stays OK — required gates passed; tier is advisory.
        assert result.exit_code == EXIT_OK
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        tier_check = [c for c in payload["checks"] if c["name"] == "Account tier"][0]
        assert tier_check["status"] == "WARN"

    def test_account_tier_skipped_without_api_key(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No API key + --ping: tier check should not fire (it would
        guaranteed-fail and waste a network call)."""
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.test.convilyn.com")
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.test.convilyn.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        # Missing API key already triggers EXIT_USAGE; tier-check
        # shouldn't appear in the payload regardless.
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        names = {c["name"] for c in payload["checks"]}
        assert "Account tier" not in names

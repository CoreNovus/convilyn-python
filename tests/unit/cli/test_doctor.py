"""``convilyn doctor`` — logic / boundary / error / object-state."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn._internal import credentials
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
from convilyn.cli.doctor import doctor_command

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def env_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVILYN_API_KEY", "ck_dummy_key_value")  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def isolated_credentials_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every doctor test gets its own throwaway credentials dir — otherwise a
    real `~/.config/convilyn/credentials.json` on the machine running the
    suite (e.g. one left by a manual `convilyn setup` run) would leak into
    tests that expect "no key configured"."""
    root = tmp_path / "convilyn-config"
    monkeypatch.setattr(credentials, "config_root", lambda: root)
    return root


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
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
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
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
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
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            # The tier check calls /cost-preview under the hood.
            mock.post("https://api.example.com/api/v1/workflows/cost-preview").mock(
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
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post("https://api.example.com/api/v1/workflows/cost-preview").mock(
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
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        # Missing API key already triggers EXIT_USAGE; tier-check
        # shouldn't appear in the payload regardless.
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        names = {c["name"] for c in payload["checks"]}
        assert "Account tier" not in names


# ── 5. A rejected key is a failure, not an advisory (#4108) ──────────


class TestRejectedKeyIsFatal:
    """`doctor --ping` used to print `All checks passed.` and exit 0 while the
    API was answering 401.

    Every `APIError` was classified WARN, on the stated reasoning that "a
    degraded tier query shouldn't block the doctor". That reasoning holds for a
    5xx and inverts for a 401: telling somebody their setup is fine when the API
    refuses their key is the one answer `doctor` must never give, and it is the
    command people put in the first step of CI.

    The split is on the status code because that is the thing that actually
    distinguishes "the backend hiccuped" from "your credentials are refused".
    `TestTransientTierFailureStaysAdvisory` below is the other half — without
    it, "make everything FAIL" would pass this class.
    """

    BASE = "https://api.example.com"

    def _run(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ) -> tuple[int, dict]:
        monkeypatch.setenv("CONVILYN_BASE_URL", self.BASE)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{self.BASE}/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post(f"{self.BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(status, json={"detail": "nope"})
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        return result.exit_code, payload

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_exits_api_error(
        self,
        runner: CliRunner,
        env_with_key: None,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ) -> None:
        exit_code, _ = self._run(runner, monkeypatch, status)
        assert exit_code == EXIT_API_ERROR

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_is_reported_as_a_failure(
        self,
        runner: CliRunner,
        env_with_key: None,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ) -> None:
        _, payload = self._run(runner, monkeypatch, status)
        tier = [c for c in payload["checks"] if c["name"] == "Account tier"][0]
        assert tier["status"] == "FAIL"

    def test_the_summary_does_not_claim_success(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exit code and the printed sentence have to agree."""
        _, payload = self._run(runner, monkeypatch, 401)
        assert payload["summary"] != "All checks passed."


class TestTransientTierFailureStaysAdvisory:
    """The boundary the fix deliberately does not cross.

    A 5xx, or a transport error, still leaves the tier line advisory and the
    doctor exiting 0 — the required checks did pass, and an unreachable
    *optional* signal is not a broken environment.
    """

    BASE = "https://api.example.com"

    def test_server_error_stays_warn_and_exits_ok(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONVILYN_BASE_URL", self.BASE)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{self.BASE}/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post(f"{self.BASE}/api/v1/workflows/cost-preview").mock(
                return_value=httpx.Response(503, json={"detail": "later"})
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        tier = [c for c in payload["checks"] if c["name"] == "Account tier"][0]
        assert (result.exit_code, tier["status"]) == (EXIT_OK, "WARN")


class TestSummaryCountsWarnings:
    """`All checks passed.` was printed whenever nothing had status FAIL, so a
    run that printed a WARN line ended by contradicting its own output."""

    BASE = "https://api.example.com"

    def test_a_warning_is_named_in_the_summary(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONVILYN_BASE_URL", self.BASE)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{self.BASE}/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post(f"{self.BASE}/api/v1/workflows/cost-preview").mock(
                side_effect=httpx.ConnectError("transient")
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert "warned" in payload["summary"]

    def test_a_clean_run_still_says_so(self, runner: CliRunner, env_with_key: None) -> None:
        """The other half: the sentence must still exist for a clean run."""
        result = runner.invoke(doctor_command, ["--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["summary"] == "All checks passed."


# ── 6. The credentials-file source (`convilyn setup`) ────────────────


class TestApiKeySourceReporting:
    """`_check_api_key` reports which of `resolve_auth`'s sources actually
    supplied the key — same precedence, so this check can never disagree
    with what the client itself does."""

    def _payload(self, runner: CliRunner) -> dict:
        result = runner.invoke(doctor_command, ["--json"])
        return json.loads(result.output.strip().splitlines()[-1])

    def test_file_only_resolves_via_setup(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        result = runner.invoke(doctor_command, [])
        assert result.exit_code == EXIT_OK
        payload = self._payload(runner)
        key_check = [c for c in payload["checks"] if c["name"] == "CONVILYN_API_KEY"][0]
        assert key_check["status"] == "OK"
        assert "source=file" in key_check["detail"]

    def test_env_beats_file_in_the_report_too(self, runner: CliRunner, env_with_key: None) -> None:
        # A user with CONVILYN_API_KEY already exported sees no change after
        # running `convilyn setup` — the non-conflict guarantee, verified
        # from the doctor side too.
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        payload = self._payload(runner)
        key_check = [c for c in payload["checks"] if c["name"] == "CONVILYN_API_KEY"][0]
        assert "source=env" in key_check["detail"]

    def test_neither_source_still_suggests_setup(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        result = runner.invoke(doctor_command, [])
        assert result.exit_code == EXIT_USAGE
        assert "convilyn setup" in result.output

    def test_file_key_lets_the_ping_tier_check_run(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mirrors `test_account_tier_skipped_without_api_key`'s env-only
        # case, but for the file source: --ping should attempt the tier
        # lookup when the ONLY resolvable key is the file one.
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://api.example.com")
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.example.com/api/v1/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post("https://api.example.com/api/v1/workflows/cost-preview").mock(
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
                            "tier": "free",
                            "estimatedMicroU": 0,
                            "thresholdMicroU": 1_000_000,
                        },
                    },
                )
            )
            result = runner.invoke(doctor_command, ["--ping", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        names = {c["name"] for c in payload["checks"]}
        assert "Account tier" in names


class TestCredentialsFilePermissionCheck:
    """`_check_credentials_file_permissions` — WARN on loose perms, SKIP
    when there's nothing to check."""

    def _payload(self, runner: CliRunner) -> dict:
        result = runner.invoke(doctor_command, ["--json"])
        return json.loads(result.output.strip().splitlines()[-1])

    def test_no_file_is_skipped(self, runner: CliRunner, env_with_key: None) -> None:
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "SKIP"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission model")
    def test_0600_file_is_ok(self, runner: CliRunner, env_with_key: None) -> None:
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "OK"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission model")
    def test_loose_permissions_warn_but_do_not_fail_the_run(
        self, runner: CliRunner, env_with_key: None
    ) -> None:
        path = credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        os.chmod(path, 0o644)
        result = runner.invoke(doctor_command, [])
        assert result.exit_code == EXIT_OK  # a WARN never flips the exit code
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "WARN"
        assert "chmod 600" in check["detail"]

    def test_windows_reads_the_acl_and_reports_a_clean_one_as_ok(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This asserted `SKIP` until #4823, and the SKIP was the defect.

        It was justified as "nothing this check can meaningfully assert on
        Windows", which is true of Unix permission bits and false of the
        question the check is named after. The ACL was always readable; nobody
        was reading it, and every Windows user got a status indistinguishable
        from a pass.
        """
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(credentials, "broad_principals_with_access", frozenset)
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "OK"

    def test_windows_warns_and_names_who_can_read_it(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The finding must say WHO, not just that something is wrong.

        The remedy differs by principal — `Users` means a redirected
        `%APPDATA%`, `Everyone` means something else again — so a bare "your
        permissions are wrong" leaves the user with nowhere to go.
        """
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(
            credentials, "broad_principals_with_access", lambda: frozenset({"users"})
        )
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "WARN"
        assert "users" in check["detail"]

    def test_windows_skips_when_the_acl_cannot_be_read(
        self, runner: CliRunner, env_with_key: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`None` (not measured) must not render as OK.

        This is the distinction the original code could not make. An empty set
        means "read it, it is clean"; `None` means "could not read it". A check
        that shows those the same way has a green that means nothing.
        """
        credentials.write_credentials("ck_from_setup")  # pragma: allowlist secret
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(credentials, "broad_principals_with_access", lambda: None)
        payload = self._payload(runner)
        check = [c for c in payload["checks"] if c["name"] == "Credentials file"][0]
        assert check["status"] == "SKIP"
        assert "icacls" in check["detail"]

"""``convilyn setup`` — logic / boundary / error / object-state.

The loopback callback server is real (bound to an actual ephemeral port) —
only the network calls to the backend go through `respx`. The "browser" is
simulated by monkeypatching `webbrowser.open` to a function that reads
`state` + `redirect_uri` straight out of the authorize URL it was asked to
open and immediately GETs the callback, exactly as a real browser redirect
would — this way the test never has to guess the internally-generated PKCE
values.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn._internal import credentials
from convilyn.cli import setup as setup_cli
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_USAGE
from convilyn.cli.setup import setup_command

BASE = "https://api.example.com"


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_credentials_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "convilyn-config"
    monkeypatch.setattr(credentials, "config_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVILYN_BASE_URL", BASE)


def _real_browser(code: str = "test-dac", state_override: str | None = None):
    """A `webbrowser.open` replacement that completes the loopback callback
    the way a real browser redirect would, reading `state` + `redirect_uri`
    out of the URL it's given rather than assuming any internal value."""

    def _open(url: str) -> bool:
        query = parse_qs(urlsplit(url).query)
        state = state_override if state_override is not None else query["state"][0]
        redirect_uri = query["redirect_uri"][0]
        httpx.get(redirect_uri, params={"code": code, "state": state}, timeout=5.0)
        return True

    return _open


def _allow_loopback_passthrough(mock: respx.MockRouter) -> None:
    """`respx.mock()` intercepts ALL httpx traffic in scope, including the
    fake browser's real GET to the loopback callback server — let that one
    hit the real (local) network instead of raising `AllMockedAssertionError`."""
    mock.route(host="127.0.0.1").pass_through()


def _mock_token_and_key_endpoints(mock: respx.MockRouter, *, tier: str | None = "free") -> None:
    _allow_loopback_passthrough(mock)
    mock.post(f"{BASE}/api/v1/auth/desktop/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "accessToken": "session-access-token",  # pragma: allowlist secret
                "refreshToken": "session-refresh-token",  # pragma: allowlist secret
                "expiresAt": "2026-01-01T00:00:00Z",
            },
        )
    )
    mock.post(f"{BASE}/api/v1/console/keys").mock(
        return_value=httpx.Response(
            201,
            json={
                "keyId": "key_123",
                "name": "cli-test",
                "prefix": "ck_",
                "displayTail": "abcd",
                "createdAt": "2026-01-01T00:00:00Z",
                "revokedAt": None,
                "lastUsedAt": None,
                "expiresAt": None,
                "scopes": ["read", "write"],
                "key": "ck_minted_test_key",  # pragma: allowlist secret
            },
        )
    )
    if tier is not None:
        mock.post(f"{BASE}/api/v1/workflows/cost-preview").mock(
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
                        "tier": tier,
                        "estimatedMicroU": 0,
                        "thresholdMicroU": 1_000_000,
                    },
                },
            )
        )


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestSetupLogic:
    def test_full_flow_mints_and_persists_a_key(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert result.exit_code == 0
        assert credentials.read_credentials() == "ck_minted_test_key"  # pragma: allowlist secret

    def test_json_output_reports_the_result(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock, tier="pro")
            result = runner.invoke(setup_command, ["--provider", "github", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["command"] == "setup"
        assert payload["status"] == "ok"
        assert payload["provider"] == "github"
        assert payload["tier"] == "pro"
        assert "credentials_path" in payload

    def test_session_tokens_are_never_persisted(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            runner.invoke(setup_command, ["--provider", "google"])
        raw = (isolated_credentials_root / "credentials.json").read_text(encoding="utf-8")
        assert "session-access-token" not in raw
        assert "session-refresh-token" not in raw


# ── 2. Boundary — --no-browser / --json suppresses the banner ────────


class TestSetupBoundary:
    def test_no_browser_never_calls_webbrowser_open(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(setup_cli.webbrowser, "open", calls.append)
        result = runner.invoke(
            setup_command, ["--provider", "google", "--no-browser", "--timeout", "0.05"]
        )
        assert calls == []
        assert result.exit_code == EXIT_USAGE  # nothing drove the callback -> timeout

    def test_json_mode_suppresses_the_banner(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            result = runner.invoke(setup_command, ["--provider", "google", "--json"])
        # The very last line must be the JSON document — a banner line
        # before it would not break this, but any banner content mixed
        # into stdout would corrupt a single-document JSON pipe.
        last_line = result.output.strip().splitlines()[-1]
        json.loads(last_line)  # does not raise


# ── 3. Error — callback / exchange / mint failures ────────────────────


class TestSetupErrors:
    def test_state_mismatch_fails_the_flow(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            setup_cli.webbrowser, "open", _real_browser(state_override="wrong-state")
        )
        result = runner.invoke(setup_command, ["--provider", "google"])
        assert result.exit_code == EXIT_USAGE
        assert "Login failed" in result.output

    def test_token_exchange_rejected_exits_api_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            mock.post(f"{BASE}/api/v1/auth/desktop/token").mock(
                return_value=httpx.Response(400, json={"detail": "invalid_grant"})
            )
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert result.exit_code == EXIT_API_ERROR

    def test_key_mint_failure_exits_api_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            mock.post(f"{BASE}/api/v1/auth/desktop/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "accessToken": "session-access-token",  # pragma: allowlist secret
                        "refreshToken": "session-refresh-token",  # pragma: allowlist secret
                        "expiresAt": "2026-01-01T00:00:00Z",
                    },
                )
            )
            mock.post(f"{BASE}/api/v1/console/keys").mock(
                return_value=httpx.Response(500, json={"detail": "server error"})
            )
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert result.exit_code == EXIT_API_ERROR
        # A failed mint must not leave a credentials file behind.
        assert credentials.read_credentials() is None

    def test_provider_required_when_noninteractive(self, runner: CliRunner) -> None:
        # CliRunner's stdin is not a TTY, so omitting --provider must fail
        # fast rather than hang on a prompt nothing will answer.
        result = runner.invoke(setup_command, [])
        assert result.exit_code != 0
        assert "--provider" in result.output


# ── 4. Object-state — the credentials file content ────────────────────


class TestSetupObjectState:
    def test_written_credentials_are_source_setup(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            runner.invoke(setup_command, ["--provider", "google"])
        data = json.loads((isolated_credentials_root / "credentials.json").read_text())
        assert data["source"] == "setup"
        assert data["api_key"] == "ck_minted_test_key"  # pragma: allowlist secret

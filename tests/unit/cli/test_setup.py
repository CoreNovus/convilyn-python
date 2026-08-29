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


# ── 5. Password sign-in — the third way in ───────────────────────────


def _mock_signin_and_key_endpoints(
    mock: respx.MockRouter, *, signin: httpx.Response | None = None
) -> None:
    """Same key-mint + tier mocks as the browser path, but the session comes
    from `/auth/signin` instead of `/auth/desktop/token`."""
    _mock_token_and_key_endpoints(mock)
    mock.post(f"{BASE}/api/v1/auth/signin").mock(
        return_value=signin
        or httpx.Response(
            200,
            json={
                "accessToken": "session-access-token",  # pragma: allowlist secret
                "refreshToken": "session-refresh-token",  # pragma: allowlist secret
                "expiresAt": "2026-01-01T00:00:00Z",
                "user": {"id": "u_1", "email": "a@example.com"},
            },
        )
    )


class TestPasswordSignIn:
    """`--provider email` reaches the same outcome without a browser."""

    def test_it_mints_a_key_from_an_email_and_password(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The command refuses non-interactively (it has nowhere to ask for a
        # password), and CliRunner's stdin is not a TTY — so the TTY check is
        # what has to be relaxed, not the prompt.
        monkeypatch.setattr(setup_cli, "_stdin_is_interactive", lambda: True)
        with respx.mock(assert_all_called=False) as mock:
            _mock_signin_and_key_endpoints(mock)
            result = runner.invoke(
                setup_command, ["--provider", "email"], input="a@example.com\nhunter2\n"
            )
        assert result.exit_code == 0, result.output
        assert credentials.read_credentials() == "ck_minted_test_key"  # pragma: allowlist secret

    def test_no_browser_is_opened(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """The point of this path: it works where a browser cannot be reached."""
        calls: list[str] = []
        monkeypatch.setattr(setup_cli.webbrowser, "open", calls.append)
        monkeypatch.setattr(setup_cli, "_stdin_is_interactive", lambda: True)
        with respx.mock(assert_all_called=False) as mock:
            _mock_signin_and_key_endpoints(mock)
            runner.invoke(setup_command, ["--provider", "email"], input="a@example.com\npw\n")
        assert calls == []

    def test_the_password_is_never_echoed_or_persisted(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        """The one property that would be catastrophic to get wrong.

        `hide_input=True` keeps it off the screen; this asserts the stronger
        thing — that it reaches neither the captured output nor the file on
        disk. A password echoed into a terminal scrollback or a log is a
        disclosed password.
        """
        monkeypatch.setattr(setup_cli, "_stdin_is_interactive", lambda: True)
        secret = "correct-horse-battery-staple"  # pragma: allowlist secret
        with respx.mock(assert_all_called=False) as mock:
            _mock_signin_and_key_endpoints(mock)
            result = runner.invoke(
                setup_command, ["--provider", "email"], input=f"a@example.com\n{secret}\n"
            )
        assert secret not in result.output
        assert secret not in (isolated_credentials_root / "credentials.json").read_text(
            encoding="utf-8"
        )

    def test_a_rejected_login_relays_the_server_reason(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unverified email and a wrong password need different actions.

        Flattening every 4xx to "wrong password" sends someone retyping a
        password that was never the problem, so the server's own message is
        relayed rather than replaced.
        """
        monkeypatch.setattr(setup_cli, "_stdin_is_interactive", lambda: True)
        with respx.mock(assert_all_called=False) as mock:
            _mock_signin_and_key_endpoints(
                mock,
                signin=httpx.Response(403, json={"detail": "Email not verified"}),
            )
            result = runner.invoke(
                setup_command, ["--provider", "email"], input="a@example.com\npw\n"
            )
        assert result.exit_code == EXIT_API_ERROR
        assert "Email not verified" in result.output
        assert credentials.read_credentials() is None

    def test_it_refuses_without_a_tty_and_names_the_alternative(self, runner: CliRunner) -> None:
        """CliRunner's stdin is not a TTY — the real headless case.

        The refusal has to say what DOES work there, or a headless user is
        told no with nowhere to go.
        """
        result = runner.invoke(setup_command, ["--provider", "email"])
        assert result.exit_code != 0
        assert "--no-browser" in result.output


# ── 6. The welcome block ─────────────────────────────────────────────


class TestWelcomeBlock:
    def test_it_names_every_link(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock, tier="pro")
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert "welcome to Convilyn" in result.output
        assert "pro plan" in result.output
        for _label, url in setup_cli._WELCOME_LINKS:
            assert url in result.output

    def test_json_mode_keeps_stdout_a_single_object(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The welcome block is suppressed, not merely de-colored, under --json.

        `JsonRenderer` exists so a caller can pipe stdout to `jq`; prose
        printed beside it breaks that whatever it says.
        """
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            result = runner.invoke(setup_command, ["--provider", "google", "--json"])
        assert "welcome to Convilyn" not in result.output
        json.loads(result.output.strip().splitlines()[-1])

    def test_it_still_prints_without_color(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The opposite of the banner, deliberately.

        The banner is decoration and disappears when it cannot be rendered as
        intended. These links are the answer to "I have a key, now what?",
        which a user reading a captured log needs just as much — so under
        NO_COLOR the text stays and only the escapes go.
        """
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert "welcome to Convilyn" in result.output
        assert "\x1b[" not in result.output


# ── 7. Re-running when a key is already saved ────────────────────────


def _mock_tier_lookup(mock: respx.MockRouter, *, tier: str | None) -> None:
    """`_verify_key`'s only network call, isolated.

    `tier=None` mocks the *failure* — a key that no longer authenticates,
    which is the case the short-circuit must not mistake for success.
    """
    _allow_loopback_passthrough(mock)
    if tier is None:
        mock.post(f"{BASE}/api/v1/workflows/cost-preview").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid API key"})
        )
        return
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


class TestReRunWithASavedKey:
    """Re-running `setup` must not cost the user another sign-in."""

    def test_a_working_saved_key_short_circuits(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials.write_credentials("ck_existing", source="setup")  # pragma: allowlist secret
        opened: list[str] = []
        monkeypatch.setattr(setup_cli.webbrowser, "open", opened.append)

        with respx.mock(assert_all_called=False) as mock:
            _mock_tier_lookup(mock, tier="pro")
            mint = mock.post(f"{BASE}/api/v1/console/keys")
            result = runner.invoke(setup_command, ["--provider", "google"])

        assert result.exit_code == 0, result.output
        assert "Already signed in" in result.output
        assert opened == [], "no browser should open when the saved key works"
        assert not mint.called, "no new key should be minted"
        assert credentials.read_credentials() == "ck_existing"  # pragma: allowlist secret

    def test_it_never_prints_the_saved_key(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.webbrowser, "open", lambda _url: True)
        credentials.write_credentials(
            "ck_super_secret_value",  # pragma: allowlist secret
            source="setup",
        )
        with respx.mock(assert_all_called=False) as mock:
            _mock_tier_lookup(mock, tier="free")
            result = runner.invoke(setup_command, ["--provider", "google"])
        assert "ck_super_secret_value" not in result.output  # pragma: allowlist secret

    def test_a_saved_key_that_no_longer_authenticates_falls_through_to_login(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case the whole check exists for.

        "A credentials file exists" is the wrong question — a key revoked from
        the console leaves the file exactly as it was. Trusting the file would
        tell the user they are set up and then fail on their first real
        command, pointing at the wrong thing.
        """
        credentials.write_credentials("ck_revoked", source="setup")  # pragma: allowlist secret
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())

        with respx.mock(assert_all_called=False) as mock:
            # The tier lookup 401s for the OLD key, so the short-circuit
            # declines; the rest of the flow then mints a fresh one.
            _mock_token_and_key_endpoints(mock, tier=None)
            _mock_tier_lookup(mock, tier=None)
            result = runner.invoke(setup_command, ["--provider", "google"])

        assert result.exit_code == 0, result.output
        assert "did not authenticate" in result.output
        assert credentials.read_credentials() == "ck_minted_test_key"  # pragma: allowlist secret

    def test_force_signs_in_again_even_when_the_saved_key_works(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The escape hatch: a shared machine, a rotated credential, a
        different account. Without it, a user with a valid key has no way to
        replace it from the CLI."""
        credentials.write_credentials("ck_existing", source="setup")  # pragma: allowlist secret
        monkeypatch.setattr(setup_cli.webbrowser, "open", _real_browser())

        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock, tier="pro")
            result = runner.invoke(setup_command, ["--provider", "google", "--force"])

        assert result.exit_code == 0, result.output
        assert credentials.read_credentials() == "ck_minted_test_key"  # pragma: allowlist secret

    def test_json_mode_reports_that_the_key_was_reused(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A machine caller has to be able to tell the two outcomes apart —
        both are `status: ok`, but only one of them signed anybody in."""
        monkeypatch.setattr(setup_cli.webbrowser, "open", lambda _url: True)
        credentials.write_credentials("ck_existing", source="setup")  # pragma: allowlist secret
        with respx.mock(assert_all_called=False) as mock:
            _mock_tier_lookup(mock, tier="pro")
            result = runner.invoke(setup_command, ["--provider", "google", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["reused_existing_key"] is True
        assert payload["tier"] == "pro"
        assert "ck_existing" not in result.output  # pragma: allowlist secret

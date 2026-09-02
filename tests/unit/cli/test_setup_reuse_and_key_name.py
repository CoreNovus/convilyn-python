"""``convilyn setup`` — saved-key reuse, the callback page, and `--key-name`.

Split from `test_setup.py` (#4707 file-size ratchet: extract a module rather
than raise the 800-line ceiling). See that file's docstring for the shared
context (the real loopback callback server, the simulated browser). This file
is self-contained, matching this test directory's own convention — every
`test_*.py` here carries its own copy of the fixtures/mocks it needs rather
than sharing a `conftest.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn._internal import credentials
from convilyn.cli import _browser as browser_cli
from convilyn.cli import setup as setup_cli
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
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


def _mock_token_only(mock: respx.MockRouter, *, tier: str | None = "free") -> None:
    """Everything `_mock_token_and_key_endpoints` does EXCEPT the key mint.

    The key-name tests each need their own mint route — to read the request body,
    or to drive a 409 then a success — and respx takes the first matching route,
    so a shared mint mock would shadow theirs.
    """
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
        monkeypatch.setattr(browser_cli.webbrowser, "open", opened.append)

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
        monkeypatch.setattr(browser_cli.webbrowser, "open", lambda _url: True)
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
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())

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
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())

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
        monkeypatch.setattr(browser_cli.webbrowser, "open", lambda _url: True)
        credentials.write_credentials("ck_existing", source="setup")  # pragma: allowlist secret
        with respx.mock(assert_all_called=False) as mock:
            _mock_tier_lookup(mock, tier="pro")
            result = runner.invoke(setup_command, ["--provider", "google", "--json"])
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["reused_existing_key"] is True
        assert payload["tier"] == "pro"
        assert "ck_existing" not in result.output  # pragma: allowlist secret


# ── 8. The browser callback page ─────────────────────────────────────


class TestCallbackPage:
    """What the user is looking at when the redirect lands."""

    def test_a_rejected_callback_does_not_render_a_success_page(self) -> None:
        """The defect this replaced.

        One constant said "Signed in to Convilyn" and was served
        unconditionally — including when the state check rejected the callback.
        The terminal reported failure while the browser reported success, and
        the browser is where the user is looking at that moment.
        """
        page = setup_cli._callback_html("state mismatch (unexpected or forged callback)")
        assert "Signed in to Convilyn" not in page
        assert "not completed" in page
        assert "state mismatch" in page

    def test_a_successful_callback_says_what_was_approved(self) -> None:
        """ "You can close this window" alone does not tell the user what they
        just agreed to. The three facts that matter are that the credential is
        a machine-scoped key rather than a stored password, that the sign-in
        session is discarded, and that the key does not pass through the
        browser."""
        page = setup_cli._callback_html(None)
        assert "Signed in to Convilyn" in page
        assert "this machine" in page
        assert "discarded" in page
        assert "does not pass through this browser" in page

    def test_the_error_text_is_html_escaped(self) -> None:
        """The reason string is ours today, but it is rendered into a page the
        browser executes — escaping it is the cheap half of never having to
        re-audit that."""
        page = setup_cli._callback_html("<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_the_page_needs_no_network(self) -> None:
        """It is served by a loopback server with no outbound access, so an
        external stylesheet, font or image would render as a broken page on the
        one screen that has to reassure."""
        page = setup_cli._callback_html(None)
        for scheme in ("http://", "https://", "//fonts.", "src="):
            assert scheme not in page, scheme

    def test_a_rejected_callback_answers_400(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The status code is the half a script or a proxy can see, and it has
        to agree with the page."""
        seen: dict[str, int] = {}

        def _forge(url: str) -> bool:
            query = parse_qs(urlsplit(url).query)
            redirect_uri = query["redirect_uri"][0]
            response = httpx.get(
                redirect_uri, params={"code": "x", "state": "wrong-state"}, timeout=5.0
            )
            seen["status"] = response.status_code
            return True

        monkeypatch.setattr(browser_cli.webbrowser, "open", _forge)
        with respx.mock(assert_all_called=False) as mock:
            _mock_token_and_key_endpoints(mock)
            result = runner.invoke(setup_command, ["--provider", "google"])

        assert seen["status"] == 400
        assert result.exit_code == EXIT_USAGE


# ── 9. The key NAME — the thing that made setup one-shot per machine ──


class TestKeyName:
    """The mint name was `cli-<hostname>`, hardcoded, with no flag to change it.

    `platform.node()` is deterministic, so the name collided on every re-run from
    the same machine; the console refuses a duplicate active name with 409; and
    the CLI ships no `keys` subcommand, so the 409's own advice ("Revoke it or
    pick another name") was un-actionable from the terminal in both halves. That
    combination is what made a single interrupted login permanent.

    `--force` did not help and was never going to: it only skips the LOCAL
    saved-key reuse, then mints under the same name.
    """

    def test_key_name_flag_is_sent_verbatim(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            _mock_token_only(mock)
            mint = mock.post(f"{BASE}/api/v1/console/keys").mock(
                return_value=httpx.Response(
                    200, json={"key": "ck_named"}
                )  # pragma: allowlist secret
            )
            result = runner.invoke(
                setup_command, ["--provider", "google", "--key-name", "laptop ci 2"]
            )

        assert result.exit_code == EXIT_OK
        assert json.loads(mint.calls[0].request.content)["name"] == "laptop ci 2"

    def test_an_illegal_key_name_is_refused_before_any_network_call(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r"""Client-side, against the backend's own contract.

        `app/schemas/console/keys.py` pins `^[A-Za-z0-9_\- ]+$`, 1-50. Letting a
        bad name through buys a 422 at the END of a browser round trip, which is
        the most expensive moment to discover a typo.
        """
        calls: list[str] = []
        monkeypatch.setattr(browser_cli.webbrowser, "open", calls.append)

        result = runner.invoke(setup_command, ["--provider", "google", "--key-name", "bad/name"])

        assert result.exit_code != EXIT_OK
        assert calls == [], "must not open a browser for a name we already know is invalid"
        assert "--key-name" in result.output

    def test_a_too_long_key_name_is_refused(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(browser_cli.webbrowser, "open", lambda url: True)
        result = runner.invoke(setup_command, ["--provider", "google", "--key-name", "a" * 51])
        assert result.exit_code != EXIT_OK

    def test_the_default_name_is_not_double_prefixed(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        monkeypatch.setattr(setup_cli.platform, "node", lambda: "workstation")
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            _mock_token_only(mock)
            mint = mock.post(f"{BASE}/api/v1/console/keys").mock(
                return_value=httpx.Response(200, json={"key": "ck_d"})  # pragma: allowlist secret
            )
            runner.invoke(setup_command, ["--provider", "google"])

        assert json.loads(mint.calls[0].request.content)["name"] == "cli-workstation"

    def test_the_retry_name_keeps_its_whole_timestamp_on_a_long_hostname(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        """Regression. The retry truncated the SUFFIX instead of the hostname.

        The retry name was built as ``_sanitize_key_name(f"{name}-{epoch}")`` where
        ``name`` was already ``cli-<host>`` and the helper re-prefixed and clipped
        to 50. That spends 8 characters on ``cli-cli-`` and clips from the RIGHT,
        so the timestamp — the only part making the name unique — is the first
        thing lost. Past ~42 characters of hostname it disappears entirely and the
        retry name becomes deterministic, i.e. it collides too, which is the exact
        failure the retry exists to prevent.

        A domain-joined `platform.node()` this long is ordinary.
        """
        long_host = "build-agent-" + "x" * 40  # 52 chars, over the 50 cap on its own
        monkeypatch.setattr(setup_cli.platform, "node", lambda: long_host)
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            _mock_token_only(mock)
            mint = mock.post(f"{BASE}/api/v1/console/keys")
            mint.side_effect = [
                httpx.Response(
                    409, json={"detail": "An active key with this name already exists."}
                ),
                httpx.Response(200, json={"key": "ck_retry"}),  # pragma: allowlist secret
            ]
            result = runner.invoke(setup_command, ["--provider", "google"])

        assert result.exit_code == EXIT_OK
        first, second = (json.loads(c.request.content)["name"] for c in mint.calls)
        assert first != second
        assert len(second) <= 50, "the backend rejects anything longer"
        # The suffix is what makes it unique; it must survive intact.
        assert re.search(r"-\d{10}$", second), f"timestamp truncated away: {second!r}"

    def test_both_names_collide_names_the_flag_that_fixes_it(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, isolated_credentials_root: Path
    ) -> None:
        """A dead end must say what to do. The backend's own message ends
        "Revoke it or pick another name", and until `--key-name` existed the
        second half was impossible from the terminal."""
        monkeypatch.setattr(browser_cli.webbrowser, "open", _real_browser())
        with respx.mock(assert_all_called=False) as mock:
            _allow_loopback_passthrough(mock)
            _mock_token_only(mock)
            mock.post(f"{BASE}/api/v1/console/keys").mock(
                return_value=httpx.Response(
                    409, json={"detail": "An active key with this name already exists."}
                )
            )
            result = runner.invoke(setup_command, ["--provider", "google"])

        assert result.exit_code == EXIT_API_ERROR
        assert "--key-name" in result.output

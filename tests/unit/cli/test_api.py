"""``convilyn api`` — logic / boundary / error / object-state.

Patches ``_build_client`` (the same DI seam used by ``convilyn
convert`` tests) and uses respx to mock the HTTP layer end-to-end —
this keeps the assertions on observable wire behaviour rather than
internal calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from convilyn import Convilyn
from convilyn.cli import api as api_module
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_OK, EXIT_USAGE
from convilyn.cli.api import api_command

API_BASE = "https://api.convilyn.corenovus.com"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Have the CLI factory build a real :class:`Convilyn` against respx."""

    def _build() -> Convilyn:
        return Convilyn(api_key="ck_test")  # pragma: allowlist secret

    monkeypatch.setattr(api_module, "_build_client", _build)


# ── 1. Logic — happy paths ──────────────────────────────────────────


class TestApiLogic:
    def test_get_pretty_prints_json_body(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/abc").mock(
                return_value=httpx.Response(200, json={"jobId": "abc", "status": "ok"})
            )
            result = runner.invoke(api_command, ["GET", "/api/v1/jobs/abc"])

        assert result.exit_code == EXIT_OK
        # Pretty-printed JSON is multi-line with sorted keys.
        assert '"jobId"' in result.output
        assert "  " in result.output  # indentation

    def test_post_sends_body(self, runner: CliRunner, patched_client: None) -> None:
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(200, json={"received": True})

        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/echo").mock(side_effect=_capture)
            result = runner.invoke(
                api_command,
                ["POST", "/api/v1/echo", "--data", '{"key": "value"}'],
            )

        assert result.exit_code == EXIT_OK
        # The captured body must be valid JSON re-serialised from --data.
        assert json.loads(captured[0]) == {"key": "value"}

    def test_query_params_serialised(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        captured_urls: list[str] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        with respx.mock(assert_all_called=True) as mock:
            mock.get(host="api.convilyn.corenovus.com").mock(side_effect=_capture)
            result = runner.invoke(
                api_command,
                ["GET", "/api/v1/jobs", "--query", "page=2", "--query", "limit=10"],
            )

        assert result.exit_code == EXIT_OK
        assert "page=2" in captured_urls[0]
        assert "limit=10" in captured_urls[0]


# ── 2. Boundary — flag validation ───────────────────────────────────


class TestApiBoundary:
    def test_data_and_input_mutually_exclusive(
        self, runner: CliRunner, patched_client: None, tmp_path: Path
    ) -> None:
        body_file = tmp_path / "body.json"
        body_file.write_text("{}")
        result = runner.invoke(
            api_command,
            [
                "POST",
                "/api/v1/x",
                "--data",
                "{}",
                "--input",
                str(body_file),
            ],
        )
        assert result.exit_code != EXIT_OK
        assert "mutually exclusive" in result.output

    def test_invalid_data_json_rejected(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        result = runner.invoke(
            api_command, ["POST", "/api/v1/x", "--data", "not-json"]
        )
        assert result.exit_code != EXIT_OK
        assert "not valid JSON" in result.output

    def test_invalid_query_format_rejected(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        result = runner.invoke(
            api_command,
            ["GET", "/api/v1/x", "--query", "no_equals_sign"],
        )
        assert result.exit_code != EXIT_OK
        assert "NAME=VALUE" in result.output

    def test_invalid_header_format_rejected(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        result = runner.invoke(
            api_command,
            ["GET", "/api/v1/x", "--header", "no_colon_separator"],
        )
        assert result.exit_code != EXIT_OK
        assert "Name: Value" in result.output

    def test_input_dash_reads_stdin(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        captured: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(200, json={})

        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/echo").mock(side_effect=_capture)
            result = runner.invoke(
                api_command,
                ["POST", "/api/v1/echo", "--input", "-"],
                input='{"from": "stdin"}',
            )
        assert result.exit_code == EXIT_OK
        assert json.loads(captured[0]) == {"from": "stdin"}


# ── 3. Error — 4xx / 5xx still surface the body ─────────────────────


class TestApiErrors:
    def test_404_exits_api_error_but_prints_body(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/missing").mock(
                return_value=httpx.Response(
                    404, json={"code": "NOT_FOUND", "message": "not found"}
                )
            )
            result = runner.invoke(api_command, ["GET", "/api/v1/missing"])
        assert result.exit_code == EXIT_API_ERROR
        # Body must still be printed for inspection.
        assert "NOT_FOUND" in result.output

    def test_auth_error_at_construct_time(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.exceptions import AuthError

        def _raise() -> Convilyn:
            raise AuthError("missing CONVILYN_API_KEY")

        monkeypatch.setattr(api_module, "_build_client", _raise)
        result = runner.invoke(api_command, ["GET", "/api/v1/jobs"])
        assert result.exit_code == EXIT_USAGE


# ── 4. Object-state — flags compose correctly ───────────────────────


class TestApiObjectState:
    def test_json_flag_emits_single_line(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/abc").mock(
                return_value=httpx.Response(200, json={"a": 1, "b": 2})
            )
            result = runner.invoke(
                api_command, ["GET", "/api/v1/jobs/abc", "--json"]
            )
        assert result.exit_code == EXIT_OK
        # Single line of JSON in --json mode.
        assert len(result.output.strip().splitlines()) == 1
        payload = json.loads(result.output.strip())
        assert payload == {"a": 1, "b": 2}

    def test_include_prints_status_line_and_headers(
        self, runner: CliRunner, patched_client: None
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/abc").mock(
                return_value=httpx.Response(
                    200, json={"ok": True}, headers={"X-Custom-Header": "yes"}
                )
            )
            result = runner.invoke(
                api_command, ["GET", "/api/v1/jobs/abc", "--include"]
            )
        assert result.exit_code == EXIT_OK
        assert "200" in result.output  # status line
        assert "x-custom-header" in result.output.lower()  # header
        assert '"ok"' in result.output  # body

    def test_output_file_writes_and_silences_stdout(
        self,
        runner: CliRunner,
        patched_client: None,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "out.json"
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/abc").mock(
                return_value=httpx.Response(200, json={"saved": True})
            )
            result = runner.invoke(
                api_command,
                ["GET", "/api/v1/jobs/abc", "-o", str(target)],
            )
        assert result.exit_code == EXIT_OK
        assert target.exists()
        assert json.loads(target.read_text()) == {"saved": True}
        # Stdout should be empty when writing to a file.
        assert result.output.strip() == ""

    def test_header_passthrough(self, runner: CliRunner, patched_client: None) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("X-Trace-Id"))
            return httpx.Response(200, json={})

        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/x").mock(side_effect=_capture)
            result = runner.invoke(
                api_command,
                ["GET", "/api/v1/x", "--header", "X-Trace-Id: abc123"],
            )
        assert result.exit_code == EXIT_OK
        assert captured == ["abc123"]

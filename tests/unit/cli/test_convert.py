"""``convilyn convert`` — logic / boundary / error / object-state.

Mocks the SDK at the ``Convilyn(...)`` constructor — that's the same
seam the CLI uses, so tests stay realistic without spinning up respx
for every code path. Network behaviour is covered separately in the
files / convert / resilience suites.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from convilyn import ConvertJob, File, JobFailedError, ResultFile
from convilyn.cli import convert as convert_module
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_JOB_FAILED, EXIT_OK, EXIT_USAGE
from convilyn.cli.convert import convert_command

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    target = tmp_path / "report.docx"
    target.write_bytes(b"DOCX content")
    return target


@pytest.fixture
def completed_job() -> ConvertJob:
    """A fake ConvertJob in 'completed' state with one result file."""
    return ConvertJob.model_validate(
        {
            "jobId": "job_test",
            "status": "completed",
            "processorType": "document_conversion",
            "progress": 100,
            "resultFiles": [
                {
                    "filename": "report.pdf",
                    "size": 4096,
                    "mimetype": "application/pdf",
                    "url": "https://example.com/output.pdf",
                }
            ],
            "createdAt": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 5, 20, 12, 0, 5, tzinfo=timezone.utc),
        }
    )


@pytest.fixture
def fake_file_obj() -> File:
    return File.model_validate(
        {
            "fileId": "file_xyz",
            "fileName": "report.docx",
            "fileSize": 12,
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "createdAt": "2026-05-20T12:00:00Z",
        }
    )


@pytest.fixture
def mock_client(
    fake_file_obj: File,
    completed_job: ConvertJob,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    """Patch the CLI's client factory to return a fully-mocked Convilyn."""
    client = MagicMock()
    client.files.upload.return_value = fake_file_obj
    client.convert.create_and_wait.return_value = completed_job

    def _fake_download(job: object, *, to: Path, overwrite: bool = False) -> Path:
        # Mirrors the real signature so the CLI's call shape is under test. A
        # double that accepted **kwargs would keep passing if the command
        # stopped forwarding `--overwrite` at all.
        path = Path(to)
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} exists; pass overwrite=True to replace it")
        path.write_bytes(b"%PDF-1.4 fake")
        return path

    client.convert.download_to.side_effect = _fake_download
    monkeypatch.setattr(convert_module, "_build_client", lambda: client)
    return client


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestOverwrite:
    """`convilyn convert` gained `--overwrite` when `download_to` stopped
    replacing files silently. Without the flag a second run of the same command
    would have had no way to finish — and `convilyn local convert` has carried
    the identically-named flag all along, which is the consistency #4005 is about
    reaching the CLI as well as the library.
    """

    def test_a_rerun_without_the_flag_refuses(
        self, runner: CliRunner, sample_file: Path, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "already.pdf"
        out.write_bytes(b"the file the user already had")

        result = runner.invoke(
            convert_command, [str(sample_file), "--to", "pdf", "--output", str(out)]
        )

        assert result.exit_code != EXIT_OK

    def test_the_refused_run_leaves_the_file_alone(
        self, runner: CliRunner, sample_file: Path, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "already.pdf"
        out.write_bytes(b"the file the user already had")

        runner.invoke(convert_command, [str(sample_file), "--to", "pdf", "--output", str(out)])

        assert out.read_bytes() == b"the file the user already had"

    def test_the_flag_lets_the_rerun_through(
        self, runner: CliRunner, sample_file: Path, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "already.pdf"
        out.write_bytes(b"stale")

        result = runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf", "--output", str(out), "--overwrite"],
        )

        assert result.exit_code == EXIT_OK

    def test_the_flag_is_forwarded_rather_than_accepted_and_dropped(
        self, runner: CliRunner, sample_file: Path, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        """A flag the command parses and never passes on would look identical
        from the outside on a fresh path."""
        out = tmp_path / "fresh.pdf"

        runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf", "--output", str(out), "--overwrite"],
        )

        assert mock_client.convert.download_to.call_args.kwargs["overwrite"] is True


class TestConvertLogic:
    def test_default_output_path_and_exit_ok(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf"],
        )
        assert result.exit_code == EXIT_OK
        # SDK called with correct args
        mock_client.files.upload.assert_called_once_with(str(sample_file))
        mock_client.convert.create_and_wait.assert_called_once()
        # Output path derived from input filename
        expected_output = sample_file.with_suffix(".pdf")
        assert expected_output.exists()

    def test_json_output_is_single_object(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
    ) -> None:
        result = runner.invoke(convert_command, [str(sample_file), "--to", "pdf", "--json"])
        assert result.exit_code == EXIT_OK
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["command"] == "convert"
        assert payload["status"] == "completed"
        assert payload["file_id"] == "file_xyz"
        assert payload["job_id"] == "job_test"

    def test_explicit_output_path_honoured(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "elsewhere.pdf"
        result = runner.invoke(
            convert_command, [str(sample_file), "--to", "pdf", "-o", str(target)]
        )
        assert result.exit_code == EXIT_OK
        assert target.exists()

    def test_a_spelling_the_worker_cannot_read_is_normalised(
        self,
        runner: CliRunner,
        tmp_path: Path,
        mock_client: MagicMock,
    ) -> None:
        """Regression: the CLI used to send the raw suffix.

        It had its own extension guess that returned `Path.suffix` verbatim,
        and `_resolve_source` short-circuits on any non-None source_format — so
        the SDK's own mapping was unreachable from the command line, and a
        `.htm` upload reached a worker that builds `DocumentFormat("htm")` and
        raises. One derivation now serves both entry points.
        """
        page = tmp_path / "page.htm"
        page.write_bytes(b"<html></html>")
        result = runner.invoke(convert_command, [str(page), "--to", "pdf"])
        assert result.exit_code == EXIT_OK
        assert mock_client.convert.create_and_wait.call_args.kwargs["source_format"] == "html"


# ── 2. Boundary — required flags, missing file, dry-run ─────────────


class TestConvertBoundary:
    def test_missing_to_flag_exits_usage(self, runner: CliRunner, sample_file: Path) -> None:
        result = runner.invoke(convert_command, [str(sample_file)])
        # Click rejects with exit code 2 for usage errors (its convention).
        assert result.exit_code != EXIT_OK
        assert "--to" in result.output

    def test_missing_input_file_exits_usage(self, runner: CliRunner) -> None:
        result = runner.invoke(convert_command, ["/nonexistent.docx", "--to", "pdf"])
        assert result.exit_code != EXIT_OK

    def test_dry_run_skips_api_calls(
        self,
        runner: CliRunner,
        sample_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The factory should NEVER be called in dry-run mode.
        sentinel = MagicMock(side_effect=AssertionError("client factory must not be called"))
        monkeypatch.setattr(convert_module, "_build_client", sentinel)
        result = runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf", "--dry-run", "--json"],
        )
        assert result.exit_code == EXIT_OK
        # Dry-run output file should NOT exist.
        assert not sample_file.with_suffix(".pdf").exists()
        # The JSON document should carry the dry_run flag.
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["dry_run"] is True

    @pytest.mark.parametrize(
        ("filename", "target", "expected"),
        [
            ("report.docx", "pdf", "document_conversion"),
            ("photo.png", "webp", "image_conversion"),
            ("clip.mp4", "mp3", "media_processing"),
        ],
    )
    def test_dry_run_names_the_processor_it_would_reach(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        filename: str,
        target: str,
        expected: str,
    ) -> None:
        """Seeing the lane before spending anything is the point of the split.

        `convert` reaches only free processors, so what this proves is not that
        the value is right for one file but that it is *derived* — three
        different inputs, three different answers, no flag involved.
        """
        monkeypatch.setattr(
            convert_module,
            "_build_client",
            MagicMock(side_effect=AssertionError("client factory must not be called")),
        )
        source = tmp_path / filename
        source.write_bytes(b"content")
        result = runner.invoke(
            convert_command, [str(source), "--to", target, "--dry-run", "--json"]
        )
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["processor_type"] == expected

    def test_an_unconvertible_pair_is_refused_before_any_upload(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
    ) -> None:
        result = runner.invoke(convert_command, [str(sample_file), "--to", "mp4"])
        assert result.exit_code == EXIT_USAGE
        mock_client.files.upload.assert_not_called()

    def test_a_file_with_no_extension_asks_for_source_format(
        self,
        runner: CliRunner,
        tmp_path: Path,
        mock_client: MagicMock,
    ) -> None:
        nameless = tmp_path / "receipt"
        nameless.write_bytes(b"content")
        result = runner.invoke(convert_command, [str(nameless), "--to", "pdf"])
        assert result.exit_code == EXIT_USAGE
        assert "--source-format" in result.output


# ── 3. Error — typed exceptions map to documented exit codes ────────


class TestConvertErrors:
    def test_job_failed_maps_to_exit_3(
        self,
        runner: CliRunner,
        sample_file: Path,
        fake_file_obj: File,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = MagicMock()
        client.files.upload.return_value = fake_file_obj
        client.convert.create_and_wait.side_effect = JobFailedError(
            job_id="job_x",
            processor_type="document_conversion",
            code="CONVERSION_FAILED",
            message="bad format",
        )
        monkeypatch.setattr(convert_module, "_build_client", lambda: client)

        result = runner.invoke(convert_command, [str(sample_file), "--to", "pdf"])
        assert result.exit_code == EXIT_JOB_FAILED

    def test_auth_error_at_construct_time(
        self,
        runner: CliRunner,
        sample_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from convilyn.exceptions import AuthError

        def _raise():
            raise AuthError("API key not set")

        monkeypatch.setattr(convert_module, "_build_client", _raise)
        result = runner.invoke(convert_command, [str(sample_file), "--to", "pdf"])
        # ClickException defaults to exit 1 (usage error tier).
        assert result.exit_code == EXIT_USAGE

    def test_api_error_during_upload_maps_to_exit_2(
        self,
        runner: CliRunner,
        sample_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from convilyn import APIError

        client = MagicMock()
        client.files.upload.side_effect = APIError(500, "X", "server down")
        monkeypatch.setattr(convert_module, "_build_client", lambda: client)

        result = runner.invoke(convert_command, [str(sample_file), "--to", "pdf"])
        assert result.exit_code == EXIT_API_ERROR


# ── 4. Object-state — JSON vs human stream segregation ──────────────


class TestConvertObjectState:
    def test_json_mode_emits_parseable_single_object(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
    ) -> None:
        # Renderer-level stream separation is covered by
        # test_cli_output.py; here we only assert the JSON document is
        # the last line of combined output and parses cleanly.
        result = runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf", "--json"],
        )
        assert result.exit_code == EXIT_OK
        last_line = result.output.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["command"] == "convert"

    def test_human_mode_writes_visible_output(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
    ) -> None:
        result = runner.invoke(
            convert_command,
            [str(sample_file), "--to", "pdf"],
        )
        assert result.exit_code == EXIT_OK
        # Progress narration + summary together produce multiple lines.
        assert len(result.output.strip().splitlines()) >= 2

    def test_result_file_attribute_unused_does_not_break(
        self,
        runner: CliRunner,
        sample_file: Path,
        mock_client: MagicMock,
    ) -> None:
        # Smoke: instantiating ResultFile from a payload that omits
        # optional fields still works (Pydantic validation).
        rf = ResultFile.model_validate(
            {
                "filename": "x.pdf",
                "size": 1,
                "mimetype": "application/pdf",
                "url": "https://example.com/x",
            }
        )
        assert rf.url == "https://example.com/x"

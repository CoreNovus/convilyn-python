"""``convilyn local`` — logic / boundary / error / object-state.

No respx: these commands never reach the network. The seam under test is the
filesystem, so fixtures are real files in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from convilyn.cli._exit_codes import EXIT_JOB_FAILED, EXIT_OK, EXIT_USAGE
from convilyn.cli.local import local_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text("Chapter One\n\nSome prose.\n", encoding="utf-8")
    return path


def _payload(output: str) -> dict:
    """Stdout / stderr combine in CliRunner by default; the JSON is the last line."""
    return json.loads(output.strip().splitlines()[-1])


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestConvertLogic:
    def test_writes_the_output(self, runner: CliRunner, sample: Path) -> None:
        result = runner.invoke(local_command, ["convert", str(sample), "--to", "md"])

        assert result.exit_code == EXIT_OK
        assert sample.with_suffix(".md").is_file()

    def test_needs_no_api_key(
        self, runner: CliRunner, sample: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of this group: no credential is ever read."""
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)

        result = runner.invoke(local_command, ["convert", str(sample), "--to", "md"])

        assert result.exit_code == EXIT_OK

    def test_batch_converts_every_input(
        self, runner: CliRunner, sample: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "table.csv"
        other.write_text("a,b\n1,2\n", encoding="utf-8")
        out = tmp_path / "out"

        result = runner.invoke(
            local_command,
            ["batch", str(sample), str(other), "--to", "md", "--out-dir", str(out)],
        )

        assert result.exit_code == EXIT_OK
        assert sorted(p.name for p in out.iterdir()) == ["notes.md", "table.md"]


# ── 2. Boundary — dry-run, filters, and what is not written ──────────


class TestBoundary:
    def test_dry_run_writes_nothing(self, runner: CliRunner, sample: Path) -> None:
        result = runner.invoke(
            local_command, ["convert", str(sample), "--to", "md", "--dry-run", "--json"]
        )

        assert result.exit_code == EXIT_OK
        assert not sample.with_suffix(".md").exists()
        assert _payload(result.output)["dry_run"] is True

    def test_dry_run_reports_the_route_it_would_take(self, runner: CliRunner, sample: Path) -> None:
        result = runner.invoke(
            local_command, ["convert", str(sample), "--to", "md", "--dry-run", "--json"]
        )

        assert _payload(result.output)["engine"] == "structured"

    def test_formats_can_be_filtered_by_source(self, runner: CliRunner) -> None:
        result = runner.invoke(local_command, ["formats", "--from", "docx", "--json"])

        assert [r["source_format"] for r in _payload(result.output)["routes"]] == ["docx"]

    def test_available_only_hides_what_cannot_run(self, runner: CliRunner) -> None:
        result = runner.invoke(local_command, ["formats", "--available-only", "--json"])

        assert all(r["available"] for r in _payload(result.output)["routes"])

    def test_an_existing_output_is_not_replaced_without_the_flag(
        self, runner: CliRunner, sample: Path
    ) -> None:
        runner.invoke(local_command, ["convert", str(sample), "--to", "md"])

        result = runner.invoke(local_command, ["convert", str(sample), "--to", "md"])

        assert result.exit_code == EXIT_JOB_FAILED


# ── 3. Error — each failure maps to one documented exit code ─────────


class TestErrors:
    def test_an_unsupported_route_is_a_usage_error(self, runner: CliRunner, tmp_path: Path) -> None:
        odd = tmp_path / "thing.zzz"
        odd.write_text("x", encoding="utf-8")

        result = runner.invoke(local_command, ["convert", str(odd), "--to", "md"])

        assert result.exit_code == EXIT_USAGE

    def test_a_permanently_unavailable_route_refuses_instead_of_crashing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Regression: this printed a Python traceback at the user.

        `png -> psd` is unavailable with **nothing missing** — Pillow is installed
        and this build of it cannot write PSD. The command guarded on
        `route.missing`, which is empty for that state, so it fell through to
        `local.convert()`, whose `UnsupportedRouteError` is not in the `except`
        clause below it. Every other refusal in this group prints one line; this
        one dumped a stack.

        Asserted on the absence of a traceback rather than on the message, because
        the message belongs to the route and is already pinned in
        `tests/unit/local/test_images.py`.
        """
        from PIL import Image

        source = tmp_path / "photo.png"
        Image.new("RGB", (4, 4)).save(source)

        result = runner.invoke(local_command, ["convert", str(source), "--to", "psd"])

        assert result.exit_code == EXIT_USAGE
        assert "Traceback" not in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_a_missing_input_is_rejected_before_the_command_body(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Click's own `Path(exists=True)` refuses it, and exits **2**.

        That is Click's UsageError convention, not this SDK's taxonomy — where 2
        means "API error", a thing an offline command cannot have. The mismatch
        predates this group (`cli/convert.py` behaves identically) and changing
        it would alter a documented exit code for every command, so the
        behaviour is asserted as it is rather than quietly diverged from.
        """
        result = runner.invoke(
            local_command, ["convert", str(tmp_path / "absent.txt"), "--to", "md"]
        )

        assert result.exit_code == 2
        assert "does not exist" in result.output

    def test_a_colliding_batch_is_a_usage_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """Two inputs whose outputs would collide — the caller's arguments."""
        (tmp_path / "same.txt").write_text("a", encoding="utf-8")
        (tmp_path / "same.csv").write_text("b\n", encoding="utf-8")

        result = runner.invoke(
            local_command,
            [
                "batch",
                str(tmp_path / "same.txt"),
                str(tmp_path / "same.csv"),
                "--out-dir",
                str(tmp_path / "out"),
                "--overwrite",
            ],
        )

        assert result.exit_code == EXIT_USAGE

    def test_a_batch_with_a_failure_exits_non_zero(
        self, runner: CliRunner, sample: Path, tmp_path: Path
    ) -> None:
        bad = tmp_path / "bad.zzz"
        bad.write_text("x", encoding="utf-8")

        result = runner.invoke(
            local_command,
            ["batch", str(sample), str(bad), "--out-dir", str(tmp_path / "out")],
        )

        assert result.exit_code == EXIT_JOB_FAILED


# ── 4. Object-state — JSON vs human stream segregation ───────────────


class TestObjectState:
    def test_json_mode_emits_exactly_one_document(self, runner: CliRunner, sample: Path) -> None:
        result = runner.invoke(local_command, ["convert", str(sample), "--to", "md", "--json"])

        assert json.loads(result.output.strip())["ok"] is True

    def test_the_convert_payload_names_its_command(self, runner: CliRunner, sample: Path) -> None:
        result = runner.invoke(local_command, ["convert", str(sample), "--to", "md", "--json"])

        assert _payload(result.output)["command"] == "local convert"

    def test_doctor_reports_every_package_and_tool(self, runner: CliRunner) -> None:
        result = runner.invoke(local_command, ["doctor", "--json"])
        payload = _payload(result.output)

        assert {p["name"] for p in payload["packages"]} >= {"pdfplumber", "PIL"}
        assert {t["name"] for t in payload["tools"]} == {"libreoffice", "calibre"}

    def test_doctor_marks_the_optional_package_as_optional(self, runner: CliRunner) -> None:
        result = runner.invoke(local_command, ["doctor", "--json"])
        pillow = next(p for p in _payload(result.output)["packages"] if p["name"] == "PIL")

        assert pillow["optional"] is True

    def test_doctor_never_offers_an_extra_for_an_external_tool(self, runner: CliRunner) -> None:
        """No extra installs LibreOffice; suggesting one would be a dead end."""
        result = runner.invoke(local_command, ["doctor", "--json"])

        assert all(t["extra"] is None for t in _payload(result.output)["tools"])

    def test_every_final_payload_carries_its_own_summary(
        self, runner: CliRunner, sample: Path
    ) -> None:
        """Omitting it falls through to a formatter that emits a check glyph,
        which raises UnicodeEncodeError on a cp950 console."""
        for argv in (
            ["convert", str(sample), "--to", "md", "--json"],
            ["formats", "--json"],
            ["doctor", "--json"],
        ):
            payload = _payload(runner.invoke(local_command, argv).output)
            assert payload.get("summary"), argv


# ── 5. `local pdf` — page operations from the shell ──────────────────


class TestPdfGroup:
    """Every sub-command is reachable and reports rather than tracebacks.

    The operations themselves are covered in `tests/unit/local/test_pdf.py`;
    what is asserted here is the wiring — that the group is registered, that a
    refusal exits non-zero with one line, and that `--json` stays parseable.
    """

    @staticmethod
    def _document(path: Path, pages: int = 4) -> Path:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for index in range(pages):
            writer.add_blank_page(width=100 + index, height=200)
        with path.open("wb") as handle:
            writer.write(handle)
        return path

    def test_the_group_is_registered(self, runner: CliRunner) -> None:
        result = runner.invoke(local_command, ["pdf", "--help"])

        assert result.exit_code == 0
        for name in ("merge", "select", "split", "rotate", "compress", "protect", "unlock"):
            assert name in result.output

    def test_select_writes_the_named_file(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf")
        target = tmp_path / "out.pdf"

        result = runner.invoke(
            local_command, ["pdf", "select", str(source), str(target), "--pages", "1-2"]
        )

        assert result.exit_code == 0
        assert target.is_file()

    def test_split_writes_a_file_per_page(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf")

        result = runner.invoke(
            local_command, ["pdf", "split", str(source), "--out-dir", str(tmp_path / "pages")]
        )

        assert result.exit_code == 0
        assert len(list((tmp_path / "pages").glob("*.pdf"))) == 4

    def test_merge_joins_documents(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf")
        target = tmp_path / "joined.pdf"

        result = runner.invoke(
            local_command, ["pdf", "merge", str(source), str(source), "-o", str(target)]
        )

        assert result.exit_code == 0
        assert target.is_file()

    def test_info_reports_the_page_count_as_json(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf", pages=7)

        result = runner.invoke(local_command, ["pdf", "info", str(source), "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["result"]["pages"] == 7

    def test_a_bad_page_range_exits_with_one_line(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf")

        result = runner.invoke(
            local_command, ["pdf", "select", str(source), str(tmp_path / "o.pdf"), "--pages", "x"]
        )

        assert result.exit_code == EXIT_USAGE
        assert "Traceback" not in result.output
        assert "Cannot select this PDF" in result.output

    def test_a_rotation_that_is_not_a_quarter_turn_is_rejected_by_click(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Caught at the argument, so the file is never opened."""
        source = self._document(tmp_path / "in.pdf")

        result = runner.invoke(
            local_command,
            ["pdf", "rotate", str(source), str(tmp_path / "o.pdf"), "--degrees", "45"],
        )

        assert result.exit_code != 0
        assert not (tmp_path / "o.pdf").exists()

    def test_protect_then_unlock_round_trips(self, runner: CliRunner, tmp_path: Path) -> None:
        source = self._document(tmp_path / "in.pdf")
        locked = tmp_path / "locked.pdf"
        opened = tmp_path / "open.pdf"

        assert (
            runner.invoke(
                local_command,
                ["pdf", "protect", str(source), str(locked), "--password", "hunter2"],
            ).exit_code
            == 0
        )
        assert (
            runner.invoke(
                local_command,
                ["pdf", "unlock", str(locked), str(opened), "--password", "hunter2"],
            ).exit_code
            == 0
        )
        assert opened.is_file()

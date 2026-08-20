"""``out=`` names the file **and** the format — on both entry points.

The library has always documented it that way: *``to`` names the format and the
output sits beside the input; ``out`` names the path and the format comes from
its suffix.* The CLI honoured only the first half of that sentence, so
``--out logo.png`` still planned a conversion to Markdown and reported

    No route from svg to md. This engine cannot read svg at all.

which sends the reader to the wrong question entirely — they asked for a PNG.

The interesting part is *why* the CLI could not do better: :func:`plan` accepted
only ``to``, so there was no way to ask it the question ``convert(out=...)``
answers. The CLI planned against ``md`` and then converted against the suffix —
it planned one thing and did another, and the misleading message is that gap
surfacing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from convilyn import local
from convilyn.cli._exit_codes import EXIT_OK, EXIT_USAGE
from convilyn.cli.local import local_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def a_png(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path)
    return path


class TestPlanAnswersTheSameQuestionConvertDoes:
    """``plan`` is documented as "the route ``convert`` would take"."""

    def test_out_alone_determines_the_target(self, a_png: Path, tmp_path: Path) -> None:
        route = local.plan(a_png, out=tmp_path / "photo.webp")

        assert route.target_format == "webp"

    def test_to_still_works_alone(self, a_png: Path) -> None:
        assert local.plan(a_png, to="webp").target_format == "webp"

    def test_to_wins_when_both_are_given(self, a_png: Path, tmp_path: Path) -> None:
        """Same precedence ``convert`` uses, so the two cannot disagree."""
        route = local.plan(a_png, to="jpg", out=tmp_path / "photo.webp")

        assert route.target_format == "jpg"

    def test_neither_still_defaults_to_markdown(self, tmp_path: Path) -> None:
        """Unchanged: ``plan(src)`` has always meant "to Markdown"."""
        doc = tmp_path / "notes.txt"
        doc.write_text("hello", encoding="utf-8")

        assert local.plan(doc).target_format == "md"

    def test_an_extensionless_out_is_refused_rather_than_guessed(
        self, a_png: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="cannot infer a target format"):
            local.plan(a_png, out=tmp_path / "photo")

    def test_plan_and_convert_agree_on_the_target(self, a_png: Path, tmp_path: Path) -> None:
        """The property the whole ticket is about: planning and doing must match."""
        destination = tmp_path / "photo.webp"

        planned = local.plan(a_png, out=destination)
        result = local.convert(a_png, out=destination)

        assert planned.target_format == result.target_format


class TestTheCliDerivesItToo:
    def test_out_alone_converts_to_the_suffix_format(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        destination = tmp_path / "photo.webp"

        result = runner.invoke(local_command, ["convert", str(a_png), "--out", str(destination)])

        assert result.exit_code == EXIT_OK
        assert destination.is_file()

    def test_the_refusal_names_the_format_the_user_asked_for(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The reported symptom. An unreadable source with ``--out logo.png``
        used to be reported as ``svg -> md``, which is not what was asked for and
        points at the wrong problem."""
        svg = tmp_path / "logo.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")

        result = runner.invoke(
            local_command, ["convert", str(svg), "--out", str(tmp_path / "logo.png")]
        )

        assert "to md" not in result.output

    def test_an_extensionless_out_is_one_line_not_a_traceback(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        """``_resolve_target`` raises ``ValueError`` here, and nothing in the
        command caught it before — every other refusal in this group prints one
        line."""
        result = runner.invoke(local_command, ["convert", str(a_png), "--out", str(tmp_path / "x")])

        assert result.exit_code == EXIT_USAGE
        assert "Traceback" not in result.output

    def test_dry_run_reports_the_engine_the_derived_target_needs(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        """``--dry-run`` exists to show what would happen, and it used to show the
        wrong thing: planning against ``md`` made a png→webp request report the
        *document* engine.

        Asserted on ``engine`` rather than on a target-format field, because the
        payload has no such field and inventing one to make this test convenient
        would be widening a documented `--json` shape for no caller.
        """
        import json

        result = runner.invoke(
            local_command,
            ["convert", str(a_png), "--out", str(tmp_path / "photo.webp"), "--dry-run", "--json"],
        )

        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["engine"] == "image"

    def test_out_dir_writes_into_it_keeping_the_source_name(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        """`batch` has always taken `--out-dir`; reaching for it on `convert` was
        the reported mistake, and Click could only answer "did you mean --out?".

        They are not rival spellings — `--out` names a file, `--out-dir` names a
        directory — so this is `batch`'s vocabulary made available where it also
        means something, not a second way to say one thing.
        """
        build = tmp_path / "build"

        result = runner.invoke(
            local_command, ["convert", str(a_png), "--to", "webp", "--out-dir", str(build)]
        )

        assert result.exit_code == EXIT_OK
        assert (build / "photo.webp").is_file()

    def test_out_dir_uses_the_derived_target_for_the_suffix(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The name needs the format the route settled on, which is why the path
        is resolved after planning rather than from `--to` alone."""
        doc = tmp_path / "notes.txt"
        doc.write_text("hello", encoding="utf-8")
        build = tmp_path / "build"

        runner.invoke(local_command, ["convert", str(doc), "--out-dir", str(build)])

        assert (build / "notes.md").is_file()

    def test_out_and_out_dir_together_are_refused(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        """Refused rather than ranked: picking a winner means the other silently
        does nothing, which is how a converter writes somewhere unintended."""
        result = runner.invoke(
            local_command,
            [
                "convert",
                str(a_png),
                "--to",
                "webp",
                "--out",
                str(tmp_path / "x.webp"),
                "--out-dir",
                str(tmp_path / "build"),
            ],
        )

        assert result.exit_code == EXIT_USAGE

    def test_nothing_is_written_when_both_are_given(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        build = tmp_path / "build"

        runner.invoke(
            local_command,
            [
                "convert",
                str(a_png),
                "--to",
                "webp",
                "--out",
                str(tmp_path / "x.webp"),
                "--out-dir",
                str(build),
            ],
        )

        assert not (tmp_path / "x.webp").exists()
        assert not build.exists()

    def test_dry_run_names_the_destination_it_would_write(
        self, runner: CliRunner, a_png: Path, tmp_path: Path
    ) -> None:
        import json

        destination = tmp_path / "photo.webp"

        result = runner.invoke(
            local_command,
            ["convert", str(a_png), "--out", str(destination), "--dry-run", "--json"],
        )

        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["output"] == str(destination)

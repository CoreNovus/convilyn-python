"""The once-ever pointer at ``convilyn feedback`` — logic / boundary / error.

Every test passes an explicit ``cache_root``, so nothing here touches the real
user cache directory and the suite stays hermetic. That matters more than usual
in this tree: `tests/` is exported to the public mirror, which runs its own CI
with no sibling directories and no developer's home to read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.cli._nudge import SHOW_ON_RUN, note_successful_conversion


def _run(tmp_path: Path, times: int, *, json_output: bool = False) -> None:
    for _ in range(times):
        note_successful_conversion(json_output=json_output, cache_root=tmp_path)


class TestNudgeLogic:
    def test_it_says_nothing_before_the_threshold(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, SHOW_ON_RUN - 1)
        assert "convilyn feedback" not in capsys.readouterr().err

    def test_it_offers_the_survey_on_the_nth_success(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, SHOW_ON_RUN)
        assert "convilyn feedback" in capsys.readouterr().err

    def test_it_offers_it_once_and_not_again(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`== SHOW_ON_RUN`, not `>=`: a hint on every conversion is nagging."""
        _run(tmp_path, SHOW_ON_RUN)
        capsys.readouterr()
        _run(tmp_path, 5)
        assert "convilyn feedback" not in capsys.readouterr().err

    def test_it_writes_to_stderr_so_stdout_stays_the_result(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, SHOW_ON_RUN)
        assert capsys.readouterr().out == ""


class TestNudgeBoundary:
    def test_json_output_suppresses_it_entirely(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A caller who asked for machine output asked not to be talked to."""
        _run(tmp_path, SHOW_ON_RUN * 3, json_output=True)
        assert capsys.readouterr().err == ""

    def test_it_creates_the_cache_directory_when_absent(self, tmp_path: Path) -> None:
        nested = tmp_path / "does" / "not" / "exist"
        note_successful_conversion(json_output=False, cache_root=nested)
        assert nested.exists()


class TestNudgeErrors:
    def test_a_corrupt_counter_starts_over_rather_than_raising(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Hand-edited or truncated. Losing the count costs one delayed hint."""
        (tmp_path / "feedback-nudge.count").write_text("not a number", encoding="utf-8")
        _run(tmp_path, SHOW_ON_RUN)
        assert "convilyn feedback" in capsys.readouterr().err

    def test_an_unwritable_cache_root_never_fails_the_conversion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This runs after a conversion has already succeeded and been reported,
        so a hint that cannot be written must not turn success into an error."""

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "mkdir", _explode)
        note_successful_conversion(json_output=False, cache_root=tmp_path)  # must not raise

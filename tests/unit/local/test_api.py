"""``convilyn.local`` API — logic / boundary / error / object-state.

No respx anywhere: this namespace never opens a connection, so mocking HTTP
would describe a call that cannot happen. Fixtures are real files in ``tmp_path``
and the seam under test is the engine itself.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from convilyn import local


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text("Chapter One\n\nSome prose with a [bracket].\n", encoding="utf-8")
    return path


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "table.csv"
    path.write_text("name,qty\nbolt,4\n", encoding="utf-8")
    return path


# ── 1. Logic — the happy path, and where output lands ────────────────


class TestConvertLogic:
    def test_writes_beside_the_input_by_default(self, sample_txt: Path) -> None:
        result = local.convert(sample_txt, to="md")

        assert result.output == sample_txt.with_suffix(".md")

    def test_the_output_is_markdown(self, sample_txt: Path) -> None:
        local.convert(sample_txt, to="md")

        assert "## Chapter One" in sample_txt.with_suffix(".md").read_text(encoding="utf-8")

    def test_out_names_the_file_and_infers_the_format(self, sample_txt: Path) -> None:
        destination = sample_txt.parent / "elsewhere" / "renamed.md"

        result = local.convert(sample_txt, out=destination)

        assert result.output == destination and destination.is_file()

    def test_warnings_reach_the_caller(self, sample_txt: Path) -> None:
        """Plain text carries no structure, so headings are inferred — say so."""
        result = local.convert(sample_txt, to="md")

        assert any("best_effort" in w for w in result.warnings)


class TestConvertManyLogic:
    def test_every_input_produces_a_result(
        self, sample_txt: Path, sample_csv: Path, tmp_path: Path
    ) -> None:
        results = local.convert_many([sample_txt, sample_csv], to="md", out_dir=tmp_path / "out")

        assert len(results) == 2

    def test_outputs_land_in_the_target_directory(
        self, sample_txt: Path, sample_csv: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"

        local.convert_many([sample_txt, sample_csv], to="md", out_dir=out)

        assert sorted(p.name for p in out.iterdir()) == ["notes.md", "table.md"]

    def test_progress_is_reported_per_file(self, sample_txt: Path, tmp_path: Path) -> None:
        seen: list[str] = []

        local.convert_many(
            [sample_txt],
            to="md",
            out_dir=tmp_path / "out",
            on_progress=lambda e: seen.append(e.phase),
        )

        assert seen == ["start", "done"]


# ── 1b. The row cap (#3997) ──────────────────────────────────────────
#
# The cap existed as a module constant no caller could reach, so "give me the
# first 200 rows of this 500,000-row export" and "give me all of it" were both
# unsayable. These pin the option end to end — through the projected engine, not
# against it — and pin the refusal, which is the half that keeps the option
# honest on a source it cannot apply to.


@pytest.fixture
def long_csv(tmp_path: Path) -> Path:
    path = tmp_path / "transactions.csv"
    path.write_text("id,amount\n" + "".join(f"tx{i},{i}\n" for i in range(60)), encoding="utf-8")
    return path


def _data_rows(markdown: Path) -> int:
    """Body rows of the one table, less its header and delimiter lines."""
    lines = markdown.read_text(encoding="utf-8").splitlines()
    return len([line for line in lines if line[:1] == "|"]) - 2


class TestRowCap:
    def test_the_option_is_worth_having(self) -> None:
        """Non-vacuity. Every refusal test below passes on an empty table — with
        nothing admitted, everything is refused — so this runs first and fails
        loudly rather than letting the suite go quietly green on a dead option.
        """
        from convilyn.local._runners import ROW_CAPPED_EXTRACTORS

        assert ROW_CAPPED_EXTRACTORS

    def test_a_cap_limits_the_rows_converted(self, long_csv: Path) -> None:
        local.convert(long_csv, to="md", max_rows=10)

        assert _data_rows(long_csv.with_suffix(".md")) == 10

    def test_the_header_is_not_charged_to_the_cap(self, long_csv: Path) -> None:
        """The reported defect, from the public side: asking for 10 gave 9."""
        result = local.convert(long_csv, to="md", max_rows=10)
        converted = _data_rows(long_csv.with_suffix(".md"))

        assert any(f"first {converted} data rows" in w for w in result.warnings)

    def test_zero_reads_the_whole_file(self, long_csv: Path) -> None:
        """The other unsayable intent: a cap the caller cannot lift is a ceiling."""
        result = local.convert(long_csv, to="md", max_rows=0)

        assert _data_rows(long_csv.with_suffix(".md")) == 60
        assert not any("truncated" in w for w in result.warnings)

    def test_a_source_with_no_rows_is_refused(self, sample_txt: Path) -> None:
        """Refused, not ignored. Silently dropping the cap here would rebuild the
        invisibility this option exists to remove, one layer further up.
        """
        with pytest.raises(ValueError, match="no rows to cap"):
            local.convert(sample_txt, to="md", max_rows=10)

    def test_the_refusal_names_the_sources_that_do_honour_it(self, sample_txt: Path) -> None:
        """An error naming only what is wrong leaves the reader to guess the fix."""
        with pytest.raises(ValueError, match="csv"):
            local.convert(sample_txt, to="md", max_rows=10)

    def test_in_a_batch_the_refusal_is_one_result_not_a_stopped_run(
        self, sample_txt: Path, long_csv: Path, tmp_path: Path
    ) -> None:
        """A mixed batch converts what it can and reports what it could not —
        which is what `convert_many` promises for every other per-file failure.
        """
        results = local.convert_many(
            [long_csv, sample_txt], to="md", out_dir=tmp_path / "o", max_rows=5
        )

        assert [r.ok for r in results] == [True, False]


# ── 2. Boundary — argument resolution and pre-flight refusals ────────


class TestBoundary:
    def test_neither_to_nor_out_is_rejected(self, sample_txt: Path) -> None:
        with pytest.raises(ValueError, match="to=|out="):
            local.convert(sample_txt)

    def test_an_extensionless_out_cannot_infer_a_format(self, sample_txt: Path) -> None:
        with pytest.raises(ValueError, match="cannot infer"):
            local.convert(sample_txt, out=sample_txt.parent / "noextension")

    def test_an_existing_output_is_not_replaced_silently(self, sample_txt: Path) -> None:
        local.convert(sample_txt, to="md")

        with pytest.raises(FileExistsError):
            local.convert(sample_txt, to="md")

    def test_overwrite_opts_in(self, sample_txt: Path) -> None:
        local.convert(sample_txt, to="md")

        assert local.convert(sample_txt, to="md", overwrite=True).ok is True

    def test_a_batch_whose_outputs_collide_is_refused_before_writing(self, tmp_path: Path) -> None:
        """Two stems that match would have the second silently replace the first.

        Refused up front rather than per item, because a half-run batch that
        reports converting both files is worse than one that did nothing.
        """
        (tmp_path / "same.txt").write_text("a", encoding="utf-8")
        (tmp_path / "same.csv").write_text("b\n", encoding="utf-8")
        out = tmp_path / "out"

        with pytest.raises(ValueError, match="same output file"):
            local.convert_many(
                [tmp_path / "same.txt", tmp_path / "same.csv"],
                to="md",
                out_dir=out,
                overwrite=True,
            )

        assert not out.exists()


# ── 3. Error — typed failures, and what a batch does with them ───────


class TestErrors:
    def test_an_unknown_extension_is_an_unsupported_route(self, tmp_path: Path) -> None:
        odd = tmp_path / "thing.zzz"
        odd.write_text("x", encoding="utf-8")

        with pytest.raises(local.UnsupportedRouteError):
            local.convert(odd, to="md")

    def test_markdown_as_a_source_is_unsupported(self, tmp_path: Path) -> None:
        """Known format, but not a conversion this engine performs."""
        source = tmp_path / "already.md"
        source.write_text("# hi", encoding="utf-8")

        with pytest.raises(local.UnsupportedRouteError):
            local.convert(source, out=tmp_path / "out.md")

    def test_a_missing_file_is_reported_as_such(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            local.convert(tmp_path / "absent.txt", to="md")

    def test_a_batch_records_a_failure_instead_of_raising(self, tmp_path: Path) -> None:
        good = tmp_path / "good.txt"
        good.write_text("hello", encoding="utf-8")
        bad = tmp_path / "bad.zzz"
        bad.write_text("x", encoding="utf-8")

        results = local.convert_many([good, bad], to="md", out_dir=tmp_path / "out")

        assert [r.ok for r in results] == [True, False]

    def test_raise_on_error_opts_back_into_stopping(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zzz"
        bad.write_text("x", encoding="utf-8")

        with pytest.raises(local.UnsupportedRouteError):
            local.convert_many([bad], to="md", out_dir=tmp_path / "out", raise_on_error=True)

    def test_a_failed_result_carries_a_code_and_a_message(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zzz"
        bad.write_text("x", encoding="utf-8")

        (result,) = local.convert_many([bad], to="md", out_dir=tmp_path / "out")

        assert result.error is not None and result.error.code == "UnsupportedRouteError"


# ── 4. Object-state — introspection never raises; async yields ───────


class TestIntrospectionNeverRaises:
    def test_capabilities_answers_on_any_machine(self) -> None:
        assert local.capabilities().routes

    @pytest.mark.parametrize(
        ("name", "expected"), [("a.docx", "docx"), ("a.PDF", "pdf"), ("a.zzz", None), ("a", None)]
    )
    def test_detect_format(self, name: str, expected: str | None) -> None:
        assert local.detect_format(name) == expected

    def test_plan_returns_a_route_without_converting(self, sample_txt: Path) -> None:
        route = local.plan(sample_txt, to="md")

        assert route.source_format == "txt"
        assert not sample_txt.with_suffix(".md").exists()


class TestAsyncWrappers:
    def test_convert_is_synchronous(self) -> None:
        """Pinned deliberately: an async `convert` would block the event loop.

        See this module's own rationale — the wrappers exist so the loop keeps
        running, which requires the synchronous implementation to be the real one.
        """
        assert not asyncio.iscoroutinefunction(local.convert)
        assert asyncio.iscoroutinefunction(local.aconvert)

    def test_aconvert_runs_off_the_calling_thread(self, sample_txt: Path) -> None:
        caller = threading.get_ident()
        observed: list[int] = []

        original = local.api._run

        def spy(*args, **kwargs):
            observed.append(threading.get_ident())
            return original(*args, **kwargs)

        local.api._run = spy
        try:
            asyncio.run(local.aconvert(sample_txt, to="md"))
        finally:
            local.api._run = original

        assert observed and observed[0] != caller

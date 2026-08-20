"""``convilyn.local._probe`` — logic / boundary / error / object-state."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from convilyn.local._probe import executable_path, package_available

# ── 1. Logic — the two questions it answers ──────────────────────────


class TestPackageAvailableLogic:
    def test_a_present_package_resolves(self) -> None:
        assert package_available("json") is True

    def test_an_absent_package_does_not(self) -> None:
        assert package_available("definitely_not_a_real_package_9f3a") is False

    def test_a_submodule_resolves(self) -> None:
        assert package_available("importlib.util") is True


class TestExecutablePathLogic:
    def test_path_lookup_wins(self, tmp_path: Path) -> None:
        """A tool the user put on PATH is the one they meant."""
        on_path = tmp_path / "on-path"
        on_path.write_text("", encoding="utf-8")

        found = executable_path("soffice", which=lambda _: str(on_path))

        assert found == on_path

    def test_falls_back_to_a_known_install_location(self, tmp_path: Path) -> None:
        """The case a bare `shutil.which` gets wrong on Windows and macOS.

        The `chmod` is load-bearing and its absence is why this test spent its
        first weeks failing on POSIX and passing on Windows: `executable_path`
        requires `os.access(X_OK)`, `write_text` leaves mode 0644, and on
        Windows `os.access(X_OK)` is true for any file that exists. A fixture
        that is not executable does not stand in for a program.
        """
        installed = tmp_path / "soffice"
        installed.write_text("", encoding="utf-8")
        installed.chmod(0o755)

        found = executable_path("soffice", extra_locations=(installed,), which=lambda _: None)

        assert found == installed


# ── 2. Boundary — nothing found, nothing to search ───────────────────


class TestBoundary:
    def test_absent_everywhere_is_none(self, tmp_path: Path) -> None:
        found = executable_path(
            "soffice", extra_locations=(tmp_path / "nope",), which=lambda _: None
        )

        assert found is None

    def test_no_extra_locations_is_not_an_error(self) -> None:
        assert executable_path("soffice", which=lambda _: None) is None

    def test_a_directory_is_not_an_executable(self, tmp_path: Path) -> None:
        """`is_file` matters: a directory named `soffice` is not the program."""
        (tmp_path / "soffice").mkdir()

        found = executable_path(
            "soffice", extra_locations=(tmp_path / "soffice",), which=lambda _: None
        )

        assert found is None

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Windows has no execute bit: os.access(X_OK) is true for any existing file",
    )
    def test_a_non_executable_file_is_not_the_program(self, tmp_path: Path) -> None:
        """The `os.access(X_OK)` half of the same guard, and it had no test.

        `is_file` was pinned above; the permission check beside it was not, which
        is how a fixture missing its `chmod` could look like a defect in
        `executable_path` rather than in the fixture. Skipped rather than
        rewritten on Windows because the assertion is not true there — stating
        the asymmetry beats asserting something weaker on both.
        """
        readable = tmp_path / "soffice"
        readable.write_text("", encoding="utf-8")
        readable.chmod(0o644)

        found = executable_path("soffice", extra_locations=(readable,), which=lambda _: None)

        assert found is None


# ── 3. Error — a broken import system is an answer, not a crash ──────


class TestErrors:
    @pytest.mark.parametrize("raised", [ImportError("broken parent"), ValueError("bad spec")])
    def test_a_raising_finder_reports_unavailable(self, raised: Exception) -> None:
        """A namespace package with a broken parent raises rather than returning None.

        Unavailable is the honest answer; propagating would make "what can I
        convert" fail on a machine where the answer is simply "less".
        """

        def explode(_name: str) -> object | None:
            raise raised

        assert package_available("anything_9f3a", find_spec=explode) is False


# ── 4. Object-state — the probe is not consulted when it need not be ─


class TestObjectState:
    def test_an_already_imported_module_skips_the_finder(self) -> None:
        """`sys.modules` is authoritative and cheaper than a filesystem search."""
        calls: list[str] = []

        def counting(name: str) -> object | None:
            calls.append(name)
            return None

        assert package_available("json", find_spec=counting) is True
        assert calls == []

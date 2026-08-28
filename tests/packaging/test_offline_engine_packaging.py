"""Packaging invariants for the offline conversion engine.

Two properties that hold today, are invisible when they stop holding, and are
each one careless import away from breaking:

1. **``import convilyn`` costs no optional dependency.** The release pipeline
   installs the wheel with no extras and runs ``convilyn --version``; if any
   module reachable from the package root imported a parser, that step would
   fail on a user's machine rather than here.
2. **The generated engine is generated.** ``src/convilyn/local/_engine/`` is
   projected from Convilyn's server-side engine and regenerated whenever it
   changes. A hand edit there is silently lost at the next regeneration, so the
   files carry a DO-NOT-EDIT header and this asserts every one of them does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "convilyn"
_ENGINE = _SRC / "local" / "_engine"

#: Every optional dependency. Importing the package must pull none of them.
_OPTIONAL = (
    "pdfplumber",
    "pypdf",
    "PIL",
    "docx",
    "pptx",
    "openpyxl",
    "defusedxml",
)

_HEADER_MARKER = "DO NOT EDIT"


def _modules_after_importing(statement: str) -> set[str]:
    """Modules present in a FRESH interpreter after running ``statement``.

    A subprocess, deliberately: this suite has already imported the parsers for
    the engine's own tests, so measuring in-process would report them present no
    matter what the package does.
    """
    probe = f"{statement}\nimport sys, json; print(json.dumps(sorted(sys.modules)))"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    import json

    return set(json.loads(result.stdout.strip().splitlines()[-1]))


class TestImportCostsNoOptionalDependency:
    @pytest.mark.parametrize(
        "statement",
        [
            "import convilyn",
            "import convilyn.cli.main",
            "import convilyn.local",
            # Named separately from `convilyn.local` even though that package
            # re-exports it: a submodule imported directly does not re-run the
            # parent's `__init__`, so the two are genuinely different entry
            # points and only one of them was ever measured.
            "import convilyn.local.pdf",
        ],
    )
    def test_no_parser_is_pulled(self, statement: str) -> None:
        loaded = _modules_after_importing(statement)

        pulled = sorted(name for name in _OPTIONAL if name in loaded)

        assert pulled == [], (
            f"`{statement}` imported {pulled}. Every extractor must be imported "
            f"lazily inside its registry entry, or the CLI stops working on a "
            f"wheel installed without extras."
        )

    def test_the_probe_can_actually_see_an_import(self) -> None:
        """Guard the guard: a probe that reports nothing would pass every case."""
        loaded = _modules_after_importing("import json, pathlib")

        assert {"json", "pathlib"} <= loaded


class TestTheGeneratedEngineIsPristine:
    def test_the_engine_directory_exists(self) -> None:
        """Vacuity guard — the two checks below iterate over it."""
        assert _ENGINE.is_dir(), f"missing projected engine at {_ENGINE}"

    def test_there_are_generated_modules_to_check(self) -> None:
        assert len(list(_ENGINE.rglob("*.py"))) >= 13

    @pytest.mark.parametrize(
        "module",
        sorted(p for p in _ENGINE.rglob("*.py") if p.name != "__init__.py"),
        ids=lambda p: p.name,
    )
    def test_every_projected_module_declares_itself_generated(self, module: Path) -> None:
        head = module.read_text(encoding="utf-8")[:400]

        assert _HEADER_MARKER in head, (
            f"{module.name} carries no DO-NOT-EDIT header. Either it was hand "
            f"written into a generated tree, or the generator stopped stamping — "
            f"both lose work at the next regeneration."
        )

    def test_the_package_inits_are_hand_written(self) -> None:
        """They are the two files the generator deliberately does not own."""
        for init in _ENGINE.rglob("__init__.py"):
            assert _HEADER_MARKER not in init.read_text(encoding="utf-8")

    def test_projected_modules_use_lf_endings(self) -> None:
        """A generated artifact that differs by the author's OS is not generated."""
        crlf = [p.name for p in _ENGINE.rglob("*.py") if b"\r\n" in p.read_bytes()]

        assert crlf == [], f"CRLF in generated files: {crlf}"

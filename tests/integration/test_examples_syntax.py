"""Documentation anti-rot — keep README / QUICKSTART / examples honest.

The four-category breakdown is loose here because the unit under test
is "the docs directory" rather than a single function, but each test
still maps to one category from the unit-testing skill:

* logic       — every example file parses as Python (or has the
                expected shape for a shell script)
* boundary    — non-empty markdown files exist where the README
                claims they exist
* error       — code blocks in QUICKSTART import only public SDK
                surface (so renames trigger CI, not a user)
* object-state — AGENT.md mentions the SOLID seams the codebase
                advertises; missing seams indicate doc rot
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

import convilyn

SDK_CLIENT_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = SDK_CLIENT_ROOT / "examples"
DOCS_DIR = SDK_CLIENT_ROOT / "docs"
README = DOCS_DIR / "README.md"
QUICKSTART = DOCS_DIR / "QUICKSTART.md"
AGENT = SDK_CLIENT_ROOT / "AGENT.md"

#: The body of a fenced ```python block — the only place in a markdown file
#: where a line is code a reader would run, rather than prose about code.
_PYTHON_BLOCK = re.compile(r"^```(?:python|py)\n(.*?)^```", re.MULTILINE | re.DOTALL)


# ── 1. Logic — every example parses ─────────────────────────────────


class TestExampleSyntax:
    @pytest.mark.parametrize(
        "example_path",
        sorted(EXAMPLES_DIR.glob("*.py")),
        ids=lambda p: p.name,
    )
    def test_python_example_parses(self, example_path: Path) -> None:
        source = example_path.read_text(encoding="utf-8")
        # ast.parse raises SyntaxError if the file is broken — that
        # bubbles up cleanly here.
        ast.parse(source, filename=str(example_path))


# ── 2. Boundary — required files exist + non-empty ─────────────────


class TestDocFilesExist:
    @pytest.mark.parametrize("doc_path", [README, QUICKSTART, AGENT])
    def test_doc_exists_and_has_content(self, doc_path: Path) -> None:
        assert doc_path.exists(), f"missing required doc: {doc_path}"
        assert doc_path.stat().st_size > 256, (
            f"{doc_path.name} is suspiciously small ({doc_path.stat().st_size} B); "
            "did someone truncate it?"
        )

    def test_shell_example_has_shebang(self) -> None:
        shell_example = EXAMPLES_DIR / "04_convert_cli.sh"
        first_line = shell_example.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#!"), (
            f"shell example must start with a shebang; got {first_line!r}"
        )

    def test_sample_input_exists(self) -> None:
        sample = EXAMPLES_DIR / "sample.txt"
        assert sample.exists()
        assert sample.stat().st_size > 0


# ── 3. Error — public-surface imports in QUICKSTART remain valid ────


class TestQuickstartImportsAreValid:
    """If QUICKSTART code blocks reference a symbol that no longer
    exists on ``convilyn`` (e.g. someone renamed ``Convilyn`` to
    ``ConvilynClient``), this test fails before a user gets confused.
    """

    @pytest.fixture
    def quickstart_imports(self) -> set[str]:
        """Names imported in QUICKSTART's **Python code blocks**.

        Scoped to fenced ```python blocks, not the whole file. The scan used to
        read every line, and prose is where a doc legitimately writes an import
        down in order to say it does **not** work:

            `from convilyn import UnsupportedRouteError` raises `ImportError`,
            and that is deliberate rather than an oversight

        That sentence turned this test red while being exactly right, which is
        the failure `turbo-lane-cost-classes.md` records at length — a text scan
        tripping over the document's own explanation of itself.

        Narrowing loses nothing this test ever caught. The defect that prompted
        it (#4108: QUICKSTART listing `UnsupportedRouteError` among the
        top-level exceptions) lived in a **prose list** and was invisible here
        from the day it was written. `test_quickstart_exception_list.py` is what
        catches that; this fixture's job is the imports a reader copies out of a
        code block, which is what its name says.
        """
        text = QUICKSTART.read_text(encoding="utf-8")
        # Capture imports like "from convilyn import X, Y, Z" — we only
        # care about top-level convilyn (not convilyn._internal).
        # Restrict to a single line so multi-line greedy matches don't
        # accidentally pick up follow-on code.
        pattern = re.compile(r"from convilyn import ([\w, ]+)")
        names: set[str] = set()
        for block in _PYTHON_BLOCK.findall(text):
            for match in pattern.finditer(block):
                for raw in match.group(1).split(","):
                    names.add(raw.strip())
        return names

    def test_the_extraction_finds_the_documented_imports(
        self, quickstart_imports: set[str]
    ) -> None:
        """Non-vacuity. Narrowing a scanner is how one stops finding anything,
        and `assert not missing` passes on an empty set."""
        assert {"Convilyn", "ConvilynError", "AsyncConvilyn"} <= quickstart_imports

    @staticmethod
    def _importable(name: str) -> bool:
        """Would ``from convilyn import <name>`` actually work?

        Two ways it can, and the check has to cover both. An attribute is the
        obvious one. A **submodule** is the other: ``from convilyn import local``
        succeeds because the import system falls back to importing
        ``convilyn.local``, but `hasattr` is False until something has done so.

        Asking only about attributes therefore reported a documented, working
        import as broken — which is the opposite of this class's job.
        """
        if hasattr(convilyn, name):
            return True
        try:
            return importlib.util.find_spec(f"convilyn.{name}") is not None
        except (ImportError, AttributeError, ValueError):
            return False

    def test_every_documented_import_exists(self, quickstart_imports: set[str]) -> None:
        missing = {name for name in quickstart_imports if not self._importable(name)}
        assert not missing, (
            f"QUICKSTART references symbols that no longer exist on `convilyn`: "
            f"{sorted(missing)}. Either restore the symbol or update the doc."
        )

    def test_the_check_rejects_something_that_does_not_exist(self) -> None:
        """Guard the guard: a check that accepts anything proves nothing."""
        assert self._importable("no_such_symbol_9f3a") is False


# ── 4. Object-state — AGENT.md cross-references real seams ─────────


class TestAgentDocStayInSync:
    """AGENT.md advertises specific SOLID seams that contributors are
    expected to extend. If the seam disappears, the doc must be
    updated; this test catches the silent drift.
    """

    REQUIRED_SEAMS = (
        "AuthStrategy",
        "RetryPolicy",
        "OutputRenderer",
        "_build_client",
        "raw_request",
        "WSTransport",
        "bearer_token",
        "ws_transport_factory",
    )

    def test_seam_mentions_present(self) -> None:
        text = AGENT.read_text(encoding="utf-8")
        missing = [name for name in self.REQUIRED_SEAMS if name not in text]
        assert not missing, (
            f"AGENT.md no longer mentions these SOLID seams: {missing}. "
            "Either re-document them or update the test if the seam was "
            "intentionally removed."
        )

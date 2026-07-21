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
        text = QUICKSTART.read_text(encoding="utf-8")
        # Capture imports like "from convilyn import X, Y, Z" — we only
        # care about top-level convilyn (not convilyn._internal).
        # Restrict to a single line so multi-line greedy matches don't
        # accidentally pick up follow-on code.
        pattern = re.compile(r"from convilyn import ([\w, ]+)")
        names: set[str] = set()
        for match in pattern.finditer(text):
            for raw in match.group(1).split(","):
                names.add(raw.strip())
        return names

    def test_every_documented_import_exists(self, quickstart_imports: set[str]) -> None:
        missing = {name for name in quickstart_imports if not hasattr(convilyn, name)}
        assert not missing, (
            f"QUICKSTART references symbols that no longer exist on `convilyn`: "
            f"{sorted(missing)}. Either restore the symbol or update the doc."
        )


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

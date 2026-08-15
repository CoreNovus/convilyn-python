"""The runner table — logic / boundary / error / object-state.

Two properties are worth holding by machine rather than by review, and both are
about what happens when the **next** family arrives (media conversion, #3813):

* every ``Engine`` has a row, so adding an engine without a runner fails here
  instead of as a ``KeyError`` on a user's machine;
* ``api.py`` performs no engine dispatch of its own, so the table stays the only
  place that decides.

The second is asserted over ``api.py``'s AST rather than its text. A substring
search cannot tell code from the prose describing it, and this module's own
docstrings quote the pattern they forbid.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_args

import pytest
from PIL import Image

from convilyn import local
from convilyn.local import api
from convilyn.local._runners import RUNNERS, run_document, run_image
from convilyn.local.types import Engine

_ENGINES = frozenset(get_args(Engine))


# ── 0. Vacuity guards ────────────────────────────────────────────────


class TestTheTableIsWorthAsserting:
    def test_the_engine_literal_is_not_empty(self) -> None:
        """Everything below quantifies over this; an empty set passes silently."""
        assert len(_ENGINES) >= 2

    def test_more_than_one_family_exists(self) -> None:
        """With one runner, "the table picks the right one" cannot fail."""
        assert len({id(runner) for runner in RUNNERS.values()}) >= 2


# ── 1. Logic — the table covers the Literal, exactly ─────────────────


class TestEveryEngineHasARunner:
    def test_the_table_and_the_literal_agree(self) -> None:
        """Both directions: a missing row AND a row for a dead engine.

        The second half matters as much as the first — a row keyed on an engine the
        Literal no longer has is dead code that reads as coverage.
        """
        assert set(RUNNERS) == _ENGINES, (
            f"missing rows: {sorted(_ENGINES - set(RUNNERS))}; "
            f"rows for unknown engines: {sorted(set(RUNNERS) - _ENGINES)}"
        )

    @pytest.mark.parametrize("engine", sorted(_ENGINES))
    def test_every_row_is_callable_with_the_runner_signature(self, engine: str) -> None:
        """One call shape for every family, including the arguments some ignore.

        ``asset_namespace`` is used only by the document family — the other two
        write a single file and have no companions to place. ``max_rows`` is the
        same again: only a row-based source can act on a row cap. Both are still
        on every row, because the alternative is for the caller to decide who
        wants which, and that branch is what this table exists to remove.
        """
        runner = RUNNERS[engine]  # type: ignore[index]

        assert callable(runner)
        assert list(inspect.signature(runner).parameters) == [
            "source",
            "route",
            "output",
            "asset_namespace",
            "max_rows",
        ]

    @pytest.mark.parametrize("engine", ["structured", "office-suite", "ebook"])
    def test_the_three_document_engines_share_one_family(self, engine: str) -> None:
        """Provenance differs; the work does not. One function, three rows."""
        assert RUNNERS[engine] is run_document  # type: ignore[index]

    def test_the_image_engine_has_its_own_family(self) -> None:
        assert RUNNERS["image"] is run_image


# ── 2. Boundary — api.py no longer dispatches or renders ─────────────


def _api_tree() -> ast.Module:
    return ast.parse(Path(api.__file__).read_text(encoding="utf-8"))


class TestApiDoesNotDispatchOnEngine:
    def test_no_conditional_compares_an_engine_attribute(self) -> None:
        """`if route.engine == "image"` is what the table replaced.

        Matched structurally: any `if` whose test compares against an attribute
        named `engine`, whichever side it appears on.
        """
        offenders: list[int] = []
        for node in ast.walk(_api_tree()):
            if not isinstance(node, ast.If):
                continue
            for compare in [n for n in ast.walk(node.test) if isinstance(n, ast.Compare)]:
                operands = [compare.left, *compare.comparators]
                if any(isinstance(o, ast.Attribute) and o.attr == "engine" for o in operands):
                    offenders.append(node.lineno)

        assert offenders == [], (
            f"api.py branches on an engine at line(s) {offenders}. Dispatch belongs "
            f"in _runners.RUNNERS; a family is a row, not a branch."
        )

    def test_the_ast_scan_can_actually_see_a_branch(self) -> None:
        """Vacuity guard: prove the matcher fires on the pattern it forbids."""
        planted = ast.parse('if route.engine == "image":\n    pass\n')

        found = [
            n
            for n in ast.walk(planted)
            if isinstance(n, ast.Compare)
            and any(
                isinstance(o, ast.Attribute) and o.attr == "engine"
                for o in [n.left, *n.comparators]
            )
        ]

        assert found, "the matcher does not recognise the pattern it is scanning for"

    def test_api_no_longer_renders_or_writes_assets(self) -> None:
        """Rendering is the document family's job, not the dispatcher's.

        Asserted on the imports: `render` and `ASSET_DIR` are how that work is
        reached, so their absence from `api.py` is what makes the separation real
        rather than stylistic.
        """
        imported = {
            alias.name
            for node in ast.walk(_api_tree())
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        assert {"render", "ASSET_DIR"} & imported == set(), (
            f"api.py still imports {sorted({'render', 'ASSET_DIR'} & imported)}; "
            f"rendering and asset-writing live in _runners.run_document"
        )


# ── 3. Object state — a failure names the engine that failed ─────────


class TestFailedConversionsReportTheirRealEngine:
    def test_a_failed_image_conversion_is_not_reported_as_structured(self, tmp_path: Path) -> None:
        """The regression: every batch failure claimed `engine="structured"`."""
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"this is not a PNG")

        results = local.convert_many([broken], to="webp", out_dir=tmp_path / "out")

        assert results[0].ok is False
        assert results[0].engine == "image"

    def test_a_failed_document_conversion_still_reports_its_own_engine(
        self, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.docx"
        broken.write_bytes(b"not a docx")

        results = local.convert_many([broken], to="md", out_dir=tmp_path / "out")

        assert results[0].ok is False
        assert results[0].engine == "structured"

    def test_a_conversion_with_no_route_reports_no_engine(self, tmp_path: Path) -> None:
        """`None` beats a fabricated value: an unknown extension has no engine."""
        odd = tmp_path / "thing.zzz"
        odd.write_text("x", encoding="utf-8")

        results = local.convert_many([odd], to="md", out_dir=tmp_path / "out")

        assert results[0].ok is False
        assert results[0].engine is None

    def test_a_successful_conversion_always_names_an_engine(self, tmp_path: Path) -> None:
        """The optional field must not leak into the success path."""
        source = tmp_path / "photo.png"
        Image.new("RGB", (4, 4)).save(source)

        result = local.convert(source, to="webp")

        assert result.ok is True
        assert result.engine == "image"

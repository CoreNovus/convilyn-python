"""The frozen public surface of ``convilyn.graphs``.

A sibling of ``test_rag_surface.py``: a namespace reached as
``convilyn.graphs`` never appears in ``convilyn.__all__``, so without this file
its stability promise (see ``docs/STABILITY.md``) would be checked by nothing.
Changing the set below IS the act of changing that surface: pair any edit with
a CHANGELOG entry.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typing_extensions import is_typeddict

from convilyn import graphs

_SDK_ROOT = Path(__file__).resolve().parents[2]

FROZEN_ALL = {
    "CollectInput",
    "CollectOutput",
    "CollectState",
    "CorrectiveRagInput",
    "CorrectiveRagOutput",
    "CorrectiveRagState",
    "Document",
    "ExecutedStep",
    "FanoutInput",
    "FanoutOutput",
    "FanoutState",
    "GraphError",
    "GraphInfo",
    "PlanExecuteInput",
    "PlanExecuteOutput",
    "PlanExecuteState",
    "RefineInput",
    "RefineOutput",
    "RefineState",
    "UseCase",
    "UseCaseInput",
    "UseCaseOutput",
    "UseCaseState",
    "as_node",
    "as_tool",
    "available",
    "collect",
    "corrective_rag",
    "fanout",
    "plan_execute",
    "refine",
    "use_case",
}


class TestExportSet:
    def test_all_matches_the_frozen_set(self) -> None:
        actual = set(graphs.__all__)

        assert actual == FROZEN_ALL, (
            f"public surface drifted — added {sorted(actual - FROZEN_ALL)}, "
            f"removed {sorted(FROZEN_ALL - actual)}. If intentional, update "
            f"FROZEN_ALL here AND add a CHANGELOG entry."
        )

    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in graphs.__all__ if not hasattr(graphs, name)]

        assert missing == []

    def test_no_implicit_public_exports(self) -> None:
        public = {
            name
            for name, value in vars(graphs).items()
            if not name.startswith("_") and not inspect.ismodule(value)
        }

        assert public - set(graphs.__all__) == set()


class TestSignatures:
    """Names and signatures are frozen from the first release."""

    @pytest.mark.parametrize(
        ("name", "parameters"),
        [
            ("collect", "(*, fields, extract, check=None, interrupt=False)"),
            (
                "use_case",
                "(*, cases, model, tools=(), extract=None, check=None, route=None, middleware=(), "
                "max_clarify_turns=1, interrupt=False)",
            ),
            ("refine", "(*, generate, evaluate, max_rounds=3)"),
            ("corrective_rag", "(*, retrieve, grade, rewrite, generate, max_retries=2)"),
            ("fanout", "(*, branches, classify, synthesize)"),
            ("plan_execute", "(*, plan, execute, replan, max_steps)"),
            ("as_node", "(graph, *, inputs=None, outputs=None)"),
            ("as_tool", "(graph, *, name, description)"),
            ("available", "()"),
        ],
    )
    def test_function_takes_exactly_these_parameters(self, name: str, parameters: str) -> None:
        signature = inspect.signature(getattr(graphs, name))
        bare = signature.replace(
            parameters=[
                p.replace(annotation=inspect.Parameter.empty) for p in signature.parameters.values()
            ],
            return_annotation=inspect.Signature.empty,
        )

        assert str(bare) == parameters


class TestLayering:
    """A leading underscore means internal. That is the whole rule."""

    PUBLIC_MODULES = frozenset({"__init__", "errors", "types"})

    def test_every_module_is_public_or_underscored(self) -> None:
        modules = {p.stem for p in Path(graphs.__file__).parent.glob("*.py")}

        ambiguous = {m for m in modules if not m.startswith("_") and m not in self.PUBLIC_MODULES}

        assert ambiguous == set()

    def test_the_public_modules_all_exist(self) -> None:
        """Vacuity guard: the set above must describe real files."""
        present = {p.stem for p in Path(graphs.__file__).parent.glob("*.py")}

        assert self.PUBLIC_MODULES <= present


class TestStateTypesWorkOnEverySupportedPython:
    """Below Python 3.12 pydantic refuses a schema built from ``typing.TypedDict``.

    That is the rule it applies (to the class's metaclass), checked here on
    whichever Python runs the suite, so the 3.11 failure shows up on 3.13 too.
    """

    def test_no_state_type_is_a_typing_typeddict(self) -> None:
        refused = [
            name
            for name, value in vars(graphs.types).items()
            if is_typeddict(value) and type(value).__module__ == "typing"
        ]

        assert refused == []

    def test_the_check_above_sees_the_state_types(self) -> None:
        """Vacuity guard: the check must be looking at real state types."""
        found = [name for name, value in vars(graphs.types).items() if is_typeddict(value)]

        assert "RefineState" in found


class TestDescriptionsAreFrozen:
    def test_a_graph_description_rejects_mutation(self) -> None:
        info = graphs.available()[0]

        with pytest.raises(ValidationError):
            info.name = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("use_when", [None, ""], ids=["absent", "empty"])
    def test_a_graph_description_needs_the_situation_it_is_for(self, use_when: Any) -> None:
        fields = {"name": "g", "summary": "s", "inputs": (), "outputs": (), "requires": ()}
        given = {} if use_when is None else {"use_when": use_when}

        with pytest.raises(ValidationError, match="use_when"):
            graphs.GraphInfo(**fields, **given)


class TestTheFrameworkStaysOptional:
    """A user who never asked for graphs must not pay for, or trip over, them."""

    _BLOCKED = "import sys\nsys.modules['langgraph'] = None\nsys.modules['langchain_core'] = None\n"

    def _run(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
            [sys.executable, "-c", self._BLOCKED + body],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_the_package_and_cli_import_without_the_framework(self) -> None:
        done = self._run("import convilyn, convilyn.cli.main\nprint('ok')")

        assert done.stdout.strip() == "ok"

    def test_importing_the_namespace_without_it_names_the_install_command(self) -> None:
        done = self._run("from convilyn import graphs")

        assert 'pip install "convilyn[graphs]"' in done.stderr

    def test_importing_the_namespace_without_its_schema_validator_names_the_install_command(
        self,
    ) -> None:
        done = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
            [
                sys.executable,
                "-c",
                "import sys\nsys.modules['jsonschema'] = None\nfrom convilyn import graphs",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

        assert 'pip install "convilyn[graphs]"' in done.stderr

    def test_importing_the_namespace_without_its_agent_library_names_the_install_command(
        self,
    ) -> None:
        done = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
            [
                sys.executable,
                "-c",
                "import sys\nsys.modules['langchain'] = None\nfrom convilyn import graphs",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

        assert 'pip install "convilyn[graphs]"' in done.stderr

    def test_the_description_types_import_without_the_framework(self) -> None:
        """``types`` and ``errors`` are plain data and must stay that way."""
        sources = {
            name: (Path(graphs.__file__).parent / f"{name}.py").read_text(encoding="utf-8")
            for name in ("types", "errors")
        }

        offenders = sorted(
            name
            for name, text in sources.items()
            if "langgraph" in text or "langchain_core" in text
        )

        assert offenders == []


class TestStabilityDocCoversThisNamespace:
    def test_stability_doc_lists_the_namespace_as_provisional(self) -> None:
        doc = (_SDK_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")

        listed = next(line for line in doc.splitlines() if "**Currently provisional:**" in line)
        assert "`convilyn.graphs`" in listed

    @pytest.mark.parametrize("info", graphs.available(), ids=lambda info: info.name)
    def test_tools_doc_describes_every_graph(self, info: graphs.GraphInfo) -> None:
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        assert f"### `graphs.{info.name}`" in doc

    @pytest.mark.parametrize("info", graphs.available(), ids=lambda info: info.name)
    def test_the_which_graph_table_says_when_to_use_every_graph_in_the_same_words(
        self, info: graphs.GraphInfo
    ) -> None:
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        assert f"| {info.use_when} | `graphs.{info.name}` |" in doc

    @pytest.mark.parametrize("info", graphs.available(), ids=lambda info: info.name)
    def test_changelog_names_every_graph(self, info: graphs.GraphInfo) -> None:
        changelog = (_SDK_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        assert f"`graphs.{info.name}(" in changelog

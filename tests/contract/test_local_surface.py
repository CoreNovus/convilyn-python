"""The frozen public surface of ``convilyn.local``.

Semver-covered from 1.3.0 (see ``docs/STABILITY.md``). Changing the set below IS
the act of changing that surface: pair any edit with a CHANGELOG entry and the
right version bump.

This is a sibling of ``test_public_surface.py``, not a replacement. That file
freezes ``convilyn.__all__``, and a namespace reached as ``convilyn.local`` never
appears there — ``test_no_implicit_public_exports`` filters module-valued
attributes. So without this file the namespace would carry a semver promise that
nothing checked.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from convilyn import local
from convilyn.exceptions import ConvilynError

_SDK_ROOT = Path(__file__).resolve().parents[2]

#: The generated tree. Named once so the scan below and its vacuity guard cannot
#: disagree about what they are measuring.
_ENGINE_PKG = "convilyn.local._engine"

# ── The frozen export set ────────────────────────────────────────────
# Adding a name here is a MINOR bump; removing or renaming one is MAJOR.

FROZEN_ALL = {
    "Capabilities",
    "ConversionFailedError",
    "ConversionResult",
    "Engine",
    "LocalError",
    "LocalErrorInfo",
    "MissingDependencyError",
    "PdfOperationError",
    "ProgressCallback",
    "ProgressEvent",
    "ProgressPhase",
    "Requirement",
    "RequirementKind",
    "Route",
    "UnavailableKind",
    "UnsupportedRouteError",
    "aconvert",
    "aconvert_many",
    "capabilities",
    "convert",
    "convert_many",
    "detect_format",
    "pdf",
    "plan",
}

#: Implementation detail that must never be reachable as ``convilyn.local.X``.
INTERNAL_DENYLIST = (
    "build_routes",
    "build_capabilities",
    "find_tool",
    "package_available",
    "executable_path",
    "extractor_for",
    "render",
)

#: Frozen public models — immutability is part of the contract.
FROZEN_MODELS = (
    local.Capabilities,
    local.ConversionResult,
    local.LocalErrorInfo,
    local.ProgressEvent,
    local.Requirement,
    local.Route,
)


class TestExportSet:
    def test_all_matches_the_frozen_set(self) -> None:
        actual = set(local.__all__)

        assert actual == FROZEN_ALL, (
            f"public surface drifted — added {sorted(actual - FROZEN_ALL)}, "
            f"removed {sorted(FROZEN_ALL - actual)}. If intentional, update "
            f"FROZEN_ALL here AND add a CHANGELOG entry."
        )

    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in local.__all__ if not hasattr(local, name)]

        assert missing == [], f"__all__ names nothing importable: {missing}"

    def test_no_implicit_public_exports(self) -> None:
        public = {
            name
            for name, value in vars(local).items()
            if not name.startswith("_") and not inspect.ismodule(value)
        }

        assert public - set(local.__all__) == set()


class TestInternalsStayInternal:
    @pytest.mark.parametrize("name", INTERNAL_DENYLIST)
    def test_implementation_symbol_is_not_public(self, name: str) -> None:
        assert not hasattr(local, name)

    def test_the_generated_engine_is_not_reachable_from_the_namespace(self) -> None:
        """`_engine` is regenerated from upstream; nothing may depend on it."""
        assert "_engine" not in local.__all__


class TestLayering:
    """A leading underscore means internal. That is the whole rule.

    Written as a test because the alternative — a reader consulting
    `INTERNAL_DENYLIST` to find out whether `routes` is public — is exactly the
    confusion this naming exists to remove, and it drifted once already.
    """

    #: Modules that carry semver-covered symbols.
    PUBLIC_MODULES = frozenset({"__init__", "api", "errors", "pdf", "types"})

    def test_every_module_is_public_or_underscored(self) -> None:
        package = Path(local.__file__).parent
        modules = {p.stem for p in package.glob("*.py")}

        ambiguous = {m for m in modules if not m.startswith("_") and m not in self.PUBLIC_MODULES}

        assert ambiguous == set(), (
            f"these modules are neither underscore-prefixed nor listed as public: "
            f"{sorted(ambiguous)}. Rename to `_{sorted(ambiguous)[0]}` if internal, "
            f"or add to PUBLIC_MODULES and freeze its surface above."
        )

    def test_the_public_modules_all_exist(self) -> None:
        """Vacuity guard: the set above must describe real files."""
        package = Path(local.__file__).parent
        present = {p.stem for p in package.glob("*.py")}

        assert self.PUBLIC_MODULES <= present

    def test_the_hand_written_surface_uses_only_the_engine_s_public_names(self) -> None:
        """`_engine` is regenerated; only its PUBLIC names are safe to depend on.

        `_engine/**` is machine-projected from upstream and may be rewritten in
        any release. Its *public* names are the contract the projection keeps —
        the manifest even renames some deliberately (`twin` → `bridge`). A
        private helper carries no such promise, so a hand-written module reaching
        for one couples this package to a detail the next re-projection is free
        to move, and it would break at import rather than at review.

        Nothing forbids that today: the orphan check in
        `scripts/oss/project_local_engine.py` is scoped to `target_root`, so
        every file in this directory sits outside it. The rule held by
        discipline; this holds it by construction.

        AST rather than a string search, deliberately — the latter cannot tell an
        import from the prose in `__init__.py` that describes one.
        """
        package = Path(local.__file__).parent
        private: list[str] = []
        sites = 0

        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if not node.module.startswith(_ENGINE_PKG):
                    continue
                sites += 1
                # Segments after `_engine` are submodules; a leading underscore on
                # any of them is as private as an underscored symbol.
                _, _, tail = node.module.partition(f"{_ENGINE_PKG}.")
                reached = [p for p in tail.split(".") if p] + [a.name for a in node.names]
                private += [
                    f"{path.name}: {node.module} -> {p}" for p in reached if p.startswith("_")
                ]

        assert sites >= 5, (
            f"only {sites} `_engine` import site(s) found — the scan is not measuring "
            f"the coupling it exists to bound. Did this package stop importing the engine?"
        )
        assert private == [], (
            f"hand-written modules import private `_engine` names: {private}. "
            f"Depend on the engine's public surface, or promote the helper upstream "
            f"and re-project."
        )

    def test_discovery_cannot_start_a_subprocess(self) -> None:
        """`_tools` answers "do I have it"; only `_run` invokes anything.

        The route builder imports the first and must not be able to reach the
        second — that separation is what keeps listing formats cheap and safe.
        """
        from convilyn.local import _tools

        assert "subprocess" not in vars(_tools)


#: Every exception this namespace exports, discovered rather than listed.
#:
#: A hand-written list is a second place to remember, and the thing it would be
#: guarding is precisely "somebody added an error and forgot" — so the list has
#: to come from the surface itself or it cannot catch the case it exists for.
EXPORTED_ERRORS = tuple(
    value
    for name in sorted(FROZEN_ALL)
    if isinstance(value := getattr(local, name, None), type) and issubclass(value, Exception)
)


class TestErrorTaxonomy:
    def test_the_error_set_is_not_empty(self) -> None:
        """Vacuity guard: an empty tuple would make both tests below pass."""
        assert len(EXPORTED_ERRORS) >= 4

    @pytest.mark.parametrize("error", EXPORTED_ERRORS, ids=lambda e: e.__name__)
    def test_every_error_is_catchable_as_a_convilyn_error(self, error: type) -> None:
        """A caller already handling SDK failures keeps working unchanged."""
        assert issubclass(error, ConvilynError)

    @pytest.mark.parametrize(
        "error",
        [e for e in EXPORTED_ERRORS if e is not local.LocalError],
        ids=lambda e: e.__name__,
    )
    def test_every_specific_error_is_catchable_as_a_local_error(self, error: type) -> None:
        assert issubclass(error, local.LocalError)


class TestModelsAreFrozen:
    @pytest.mark.parametrize("model", FROZEN_MODELS, ids=lambda m: m.__name__)
    def test_model_rejects_mutation(self, model: type) -> None:
        instance = model.model_construct()
        field = next(iter(model.model_fields))

        with pytest.raises(ValidationError):
            setattr(instance, field, None)


class TestTheNamespaceIsOffline:
    def test_no_transport_module_is_reachable(self) -> None:
        """`convilyn.local` must not acquire a network path by accident.

        Asserted on the namespace's own attributes rather than on `sys.modules`,
        which the rest of the SDK legitimately populates in the same process.
        """
        for name in local.__all__:
            value = getattr(local, name)
            # A module carries `__name__`, not `__module__`. Reading only the
            # latter made every re-exported submodule answer "" and skip the
            # check — so `pdf` was invisible to it, which is how this was found.
            origin = value.__name__ if inspect.ismodule(value) else getattr(value, "__module__", "")
            assert origin.startswith(("convilyn.local", "collections", "typing")), (
                f"{name} comes from {origin}; the offline namespace must not "
                f"re-export anything from the networked half of the SDK"
            )

    def test_the_transport_check_can_see_a_module(self) -> None:
        """Vacuity guard for the test above, and it is not hypothetical.

        The check read `__module__`, which a module does not have, so a
        re-exported submodule scored `""` and was waved through. Assert that at
        least one entry in `__all__` IS a module and that it resolves to a real
        dotted name, or the loop above silently stops covering the case it was
        extended to cover.
        """
        modules = [
            getattr(local, name) for name in local.__all__ if inspect.ismodule(getattr(local, name))
        ]

        assert modules, "no submodule is re-exported; the branch above is unreachable"
        assert all(m.__name__.startswith("convilyn.local.") for m in modules)


class TestStabilityDocCoversThisNamespace:
    """The promise and the check must name the same thing."""

    def test_stability_doc_names_the_namespace(self) -> None:
        doc = (_SDK_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")

        assert "convilyn.local" in doc, (
            "docs/STABILITY.md does not mention convilyn.local, but this file "
            "freezes it as a semver-covered surface. One of the two is wrong."
        )

"""The frozen public surface of ``convilyn.rag``.

A sibling of ``test_local_surface.py``: a namespace reached as ``convilyn.rag``
never appears in ``convilyn.__all__``, so without this file its stability
promise (see ``docs/STABILITY.md``) would be checked by nothing. Changing the
set below IS the act of changing that surface: pair any edit with a CHANGELOG
entry.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from convilyn import rag

_SDK_ROOT = Path(__file__).resolve().parents[2]

FROZEN_ALL = {
    "AsyncCorpus",
    "Condition",
    "Corpus",
    "Embedder",
    "FuseOptions",
    "Hit",
    "IndexEntry",
    "IndexIdentity",
    "Language",
    "LanguageInfo",
    "Measure",
    "NormalizeOptions",
    "Passage",
    "RagError",
    "Reranker",
    "Recipe",
    "Scalar",
    "SearchOptions",
    "SearchResult",
    "SourceInfo",
    "SplitOptions",
    "Store",
    "StoreError",
    "StoreInfo",
    "TokenizerFamily",
    "aopen",
    "estimate_tokens",
    "fuse",
    "languages",
    "merge_adjacent",
    "normalize",
    "normalizer_stamp",
    "open",
    "preset",
    "split",
    "store",
    "stores",
    "token_estimator",
}

#: Every exported model, discovered rather than listed, so a model added to the
#: surface cannot be forgotten here.
EXPORTED_MODELS = tuple(
    value
    for name in sorted(FROZEN_ALL)
    if isinstance(value := getattr(rag, name, None), type) and issubclass(value, BaseModel)
)


class TestExportSet:
    def test_all_matches_the_frozen_set(self) -> None:
        actual = set(rag.__all__)

        assert actual == FROZEN_ALL, (
            f"public surface drifted — added {sorted(actual - FROZEN_ALL)}, "
            f"removed {sorted(FROZEN_ALL - actual)}. If intentional, update "
            f"FROZEN_ALL here AND add a CHANGELOG entry."
        )

    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in rag.__all__ if not hasattr(rag, name)]

        assert missing == []

    def test_no_implicit_public_exports(self) -> None:
        public = {
            name
            for name, value in vars(rag).items()
            if not name.startswith("_") and not inspect.ismodule(value)
        }

        assert public - set(rag.__all__) == set()


class TestLayering:
    """A leading underscore means internal. That is the whole rule."""

    PUBLIC_MODULES = frozenset({"__init__", "errors", "recipe", "types"})

    def test_every_module_is_public_or_underscored(self) -> None:
        package = Path(rag.__file__).parent
        modules = {p.stem for p in package.glob("*.py")}

        ambiguous = {m for m in modules if not m.startswith("_") and m not in self.PUBLIC_MODULES}

        assert ambiguous == set()

    def test_the_public_modules_all_exist(self) -> None:
        """Vacuity guard: the set above must describe real files."""
        present = {p.stem for p in Path(rag.__file__).parent.glob("*.py")}

        assert self.PUBLIC_MODULES <= present


class TestModelsAreFrozen:
    def test_the_model_set_is_not_empty(self) -> None:
        """Vacuity guard for the parametrized test below."""
        assert len(EXPORTED_MODELS) >= 5

    @pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda m: m.__name__)
    def test_model_rejects_mutation(self, model: type[BaseModel]) -> None:
        instance = model.model_construct()
        field = next(iter(model.model_fields))

        with pytest.raises(ValidationError):
            setattr(instance, field, None)


class TestTheFoundationIsOffline:
    def test_every_export_originates_in_this_namespace(self) -> None:
        """Splitting and folding must not acquire a network path by accident."""
        origins = {name: getattr(getattr(rag, name), "__module__", "") for name in rag.__all__}

        # Type aliases report the standard-library module that builds them.
        allowed = ("convilyn.rag", "typing", "collections", "types")
        foreign = {n: o for n, o in origins.items() if not o.startswith(allowed)}

        assert foreign == {}

    def test_importing_the_namespace_loads_no_transport(self) -> None:
        """No module of this package imports the HTTP client at module scope."""
        package = Path(rag.__file__).parent
        sources = {p.name: p.read_text(encoding="utf-8") for p in package.glob("*.py")}

        offenders = sorted(name for name, text in sources.items() if "import httpx" in text)

        assert offenders == []


class TestStabilityDocCoversThisNamespace:
    def test_stability_doc_names_the_namespace(self) -> None:
        doc = (_SDK_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")

        assert "convilyn.rag" in doc

    def test_stability_doc_lists_the_namespace_as_provisional(self) -> None:
        doc = (_SDK_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")

        listed = next(line for line in doc.splitlines() if "**Currently provisional:**" in line)
        assert "`convilyn.rag`" in listed

    def test_tools_doc_records_the_basis_of_this_namespace_s_defaults(self) -> None:
        """Every default states where it came from; the page that says so must exist."""
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        assert all(grade in doc for grade in ("**measured**", "**published**", "**derived**"))

    @pytest.mark.parametrize("tokenizer", ["qwen3", "xlm_r", "cl100k"])
    def test_changelog_names_every_tokenizer_choice(self, tokenizer: str) -> None:
        """A tokenizer a caller can name must be one the documentation tells them about."""
        changelog = (_SDK_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        assert f'"{tokenizer}"' in changelog

    @pytest.mark.parametrize("info", rag.languages(), ids=lambda info: info.code)
    def test_tools_doc_lists_every_preset_language_with_its_basis(
        self, info: rag.LanguageInfo
    ) -> None:
        """A language a caller can name must be one the documentation grades."""
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        assert f"| `{info.code}` | {info.name} | {info.sizes} |" in doc

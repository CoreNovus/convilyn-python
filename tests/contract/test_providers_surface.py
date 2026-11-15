"""The frozen public surface of ``convilyn.providers``.

A sibling of ``test_rag_surface.py``: a namespace reached as
``convilyn.providers`` never appears in ``convilyn.__all__``, so without this
file its stability promise (see ``docs/STABILITY.md``) would be checked by
nothing. Changing the set below IS the act of changing that surface: pair any
edit with a CHANGELOG entry.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import convilyn
from convilyn import providers

_SDK_ROOT = Path(__file__).resolve().parents[2]

FROZEN_ALL = {
    "AsyncEmbedder",
    "AsyncReranker",
    "EmbedKind",
    "EmbedResult",
    "Embedder",
    "ErrorCategory",
    "OverflowPolicy",
    "ProviderError",
    "ProviderInfo",
    "RankedDocument",
    "RerankResult",
    "Reranker",
    "aembedder",
    "areranker",
    "available",
    "embedder",
    "reranker",
}

EXPORTED_MODELS = tuple(
    value
    for name in sorted(FROZEN_ALL)
    if isinstance(value := getattr(providers, name, None), type) and issubclass(value, BaseModel)
)

FACTORY_OPTIONS = [
    "name",
    "model",
    "api_key",
    "base_url",
    "dimensions",
    "max_inputs",
    "max_input_tokens",
    "on_overflow",
    "measure",
    "retry",
    "timeout",
    "http_client",
]

RERANKER_OPTIONS = [
    "name",
    "model",
    "api_key",
    "base_url",
    "max_documents",
    "retry",
    "timeout",
    "http_client",
]

FACTORIES = [providers.embedder, providers.aembedder, providers.reranker, providers.areranker]


def _provisional_line() -> str:
    doc = (_SDK_ROOT / "docs" / "STABILITY.md").read_text(encoding="utf-8")
    return next(line for line in doc.splitlines() if "**Currently provisional:**" in line)


class TestExportSet:
    def test_all_matches_the_frozen_set(self) -> None:
        actual = set(providers.__all__)

        assert actual == FROZEN_ALL, (
            f"public surface drifted — added {sorted(actual - FROZEN_ALL)}, "
            f"removed {sorted(FROZEN_ALL - actual)}. If intentional, update "
            f"FROZEN_ALL here AND add a CHANGELOG entry."
        )

    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in providers.__all__ if not hasattr(providers, name)]

        assert missing == []

    def test_no_implicit_public_exports(self) -> None:
        public = {
            name
            for name, value in vars(providers).items()
            if not name.startswith("_") and not inspect.ismodule(value)
        }

        assert public - set(providers.__all__) == set()

    def test_the_namespace_is_not_reexported_from_the_top_level(self) -> None:
        assert "providers" not in convilyn.__all__


class TestFactories:
    @pytest.mark.parametrize("factory", [providers.embedder, providers.aembedder])
    def test_the_two_factories_take_the_same_options(self, factory: object) -> None:
        assert list(inspect.signature(factory).parameters) == FACTORY_OPTIONS  # type: ignore[arg-type]

    @pytest.mark.parametrize("factory", [providers.reranker, providers.areranker])
    def test_the_two_reranker_factories_take_the_same_options(self, factory: object) -> None:
        assert list(inspect.signature(factory).parameters) == RERANKER_OPTIONS  # type: ignore[arg-type]

    def test_a_reranker_is_configured_with_the_names_an_embedder_uses(self) -> None:
        """One factory shape: an option both kinds have is spelled the same."""
        assert set(RERANKER_OPTIONS) - set(FACTORY_OPTIONS) == {"max_documents"}

    @pytest.mark.parametrize("factory", FACTORIES, ids=lambda factory: factory.__name__)
    def test_every_option_but_the_provider_is_named_at_the_call(self, factory: object) -> None:
        kinds = {p.kind for p in list(inspect.signature(factory).parameters.values())[1:]}  # type: ignore[arg-type]

        assert kinds == {inspect.Parameter.KEYWORD_ONLY}

    @pytest.mark.parametrize(
        ("sync", "asynchronous"),
        [
            (providers.Embedder, providers.AsyncEmbedder),
            (providers.Reranker, providers.AsyncReranker),
        ],
        ids=["embedders", "rerankers"],
    )
    def test_the_sync_and_async_forms_offer_the_same_work(
        self, sync: type, asynchronous: type
    ) -> None:
        def offered(kind: type) -> set[str]:
            names = {name for base in kind.__mro__[:-1] for name in vars(base)}
            return {name for name in names if not name.startswith("_")} - {"close", "aclose"}

        assert offered(sync) == offered(asynchronous)


class TestLayering:
    """A leading underscore means internal. That is the whole rule."""

    PUBLIC_MODULES = frozenset({"__init__", "errors", "types"})

    def test_every_module_is_public_or_underscored(self) -> None:
        package = Path(providers.__file__).parent
        entries = {p.stem for p in package.glob("*.py")} | {
            p.name for p in package.iterdir() if p.is_dir() and p.name != "__pycache__"
        }

        ambiguous = {m for m in entries if not m.startswith("_") and m not in self.PUBLIC_MODULES}

        assert ambiguous == set()

    def test_the_public_modules_all_exist(self) -> None:
        """Vacuity guard: the set above must describe real files."""
        present = {p.stem for p in Path(providers.__file__).parent.glob("*.py")}

        assert self.PUBLIC_MODULES <= present


class TestModelsAreFrozen:
    def test_the_model_set_is_not_empty(self) -> None:
        """Vacuity guard for the parametrized test below."""
        assert len(EXPORTED_MODELS) >= 2

    @pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda m: m.__name__)
    def test_model_rejects_mutation(self, model: type[BaseModel]) -> None:
        instance = model.model_construct()
        field = next(iter(model.model_fields))

        with pytest.raises(ValidationError):
            setattr(instance, field, None)


class TestErrorsAreOneTree:
    def test_a_provider_error_is_a_convilyn_error(self) -> None:
        assert issubclass(providers.ProviderError, convilyn.ConvilynError)

    def test_a_provider_error_keeps_its_category_and_status(self) -> None:
        error = providers.ProviderError("message", category="auth", status=401)

        assert (str(error), error.category, error.status) == ("message", "auth", 401)


class TestNoModelRunsHere:
    def test_no_module_imports_a_model_runtime(self) -> None:
        """A connector calls a service. It never loads a model or a tokenizer."""
        package = Path(providers.__file__).parent
        runtimes = (
            "torch",
            "transformers",
            "tokenizers",
            "tiktoken",
            "sentence_transformers",
            "onnxruntime",
        )
        offenders = sorted(
            f"{path.name}: {runtime}"
            for path in package.rglob("*.py")
            for runtime in runtimes
            if f"import {runtime}" in path.read_text(encoding="utf-8")
        )

        assert offenders == []


class TestDocsCoverThisNamespace:
    def test_stability_doc_lists_the_namespace_as_provisional(self) -> None:
        assert "`convilyn.providers`" in _provisional_line()

    def test_tools_doc_states_where_the_text_goes(self) -> None:
        """The trust boundary is the one thing a user must not have to infer."""
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        assert "## `convilyn.providers` today" in doc and "leaves your machine" in doc

    @pytest.mark.parametrize("row", providers.available(), ids=lambda row: row.name)
    def test_tools_doc_lists_every_provider_with_its_source(
        self, row: providers.ProviderInfo
    ) -> None:
        doc = (_SDK_ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")

        sources = {row.source, row.rerank_source or row.source}

        assert f"| `{row.name}` |" in doc and all(source in doc for source in sources)

    @pytest.mark.parametrize("row", providers.available(), ids=lambda row: row.name)
    def test_changelog_names_every_provider(self, row: providers.ProviderInfo) -> None:
        changelog = (_SDK_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        assert f'"{row.name}"' in changelog

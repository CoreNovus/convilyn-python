"""The frozen public surface of ``convilyn.rag.bench``.

A sibling of ``test_rag_surface.py``: changing the set below IS the act of
changing that surface; pair any edit with a CHANGELOG entry.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from convilyn.rag import bench

FROZEN_ALL = {
    "Check",
    "Comparison",
    "Pin",
    "PinCheck",
    "Question",
    "QuestionResult",
    "Report",
    "Support",
    "Variants",
    "arun",
    "check",
    "compare",
    "compare_variants",
    "pin",
    "questions",
    "run",
}

EXPORTED_MODELS = tuple(
    value
    for name in sorted(FROZEN_ALL)
    if isinstance(value := getattr(bench, name, None), type) and issubclass(value, BaseModel)
)


class TestExportSet:
    def test_all_matches_the_frozen_set(self) -> None:
        assert set(bench.__all__) == FROZEN_ALL

    def test_no_implicit_public_exports(self) -> None:
        public = {
            name
            for name, value in vars(bench).items()
            if not name.startswith("_") and not inspect.ismodule(value)
        }

        assert public - set(bench.__all__) == set()

    def test_every_module_is_public_or_underscored(self) -> None:
        modules = {p.stem for p in Path(bench.__file__).parent.glob("*.py")}

        assert {m for m in modules if not m.startswith("_")} == {"types"}


class TestModelsAreFrozen:
    def test_the_model_set_is_not_empty(self) -> None:
        """Vacuity guard for the parametrized test below."""
        assert len(EXPORTED_MODELS) == 9

    @pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda m: m.__name__)
    def test_model_rejects_mutation(self, model: type[BaseModel]) -> None:
        instance = model.model_construct()
        field = next(iter(model.model_fields))

        with pytest.raises(ValidationError):
            setattr(instance, field, None)

"""`Recipe` and its per-stage options — plain, frozen, validated data."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from convilyn import rag


class TestSplitOptionsLogic:
    def test_defaults(self) -> None:
        options = rag.SplitOptions()

        assert options.model_dump() == {
            "target": 384,
            "max": 512,
            "min": 128,
            "section_depth": 3,
            "tokenizer": None,
            "language": None,
        }

    def test_recipe_groups_options_by_stage(self) -> None:
        recipe = rag.Recipe(split=rag.SplitOptions(target=200, max=300, min=50))

        assert recipe.model_dump() == {
            "split": {
                "target": 200,
                "max": 300,
                "min": 50,
                "section_depth": 3,
                "tokenizer": None,
                "language": None,
            },
            "normalize": {"fold_width": True, "fold_case": True, "strip_invisible": True},
            "search": {"limit": 10, "candidates": 100},
            "fuse": {"method": "min_max", "weights": None, "rrf_k": 60},
        }


class TestSplitOptionsBoundary:
    @pytest.mark.parametrize(
        "sizes",
        [
            {"min": 100, "target": 100, "max": 100},
            {"min": 0, "target": 1, "max": 1},
        ],
        ids=["all-equal", "smallest"],
    )
    def test_sizes_on_the_ordering_boundary_are_accepted(self, sizes: dict[str, int]) -> None:
        options = rag.SplitOptions(**sizes)

        assert (options.min, options.target, options.max) == (
            sizes["min"],
            sizes["target"],
            sizes["max"],
        )

    @pytest.mark.parametrize("depth", [0, 6])
    def test_section_depth_bounds_are_accepted(self, depth: int) -> None:
        options = rag.SplitOptions(section_depth=depth)

        assert options.section_depth == depth


class TestSplitOptionsErrors:
    @pytest.mark.parametrize(
        "sizes",
        [
            {"min": 200, "target": 100, "max": 300},
            {"min": 10, "target": 400, "max": 300},
        ],
        ids=["min-above-target", "target-above-max"],
    )
    def test_out_of_order_sizes_are_refused(self, sizes: dict[str, int]) -> None:
        with pytest.raises(ValidationError, match="min <= target <= max"):
            rag.SplitOptions(**sizes)

    @pytest.mark.parametrize(
        "field",
        [{"target": 0}, {"max": 0}, {"min": -1}, {"section_depth": 7}, {"section_depth": -1}],
        ids=["target-zero", "max-zero", "min-negative", "depth-high", "depth-negative"],
    )
    def test_out_of_range_values_are_refused(self, field: dict[str, int]) -> None:
        with pytest.raises(ValidationError):
            rag.SplitOptions(**field)


class TestRecipeState:
    def test_recipe_rejects_mutation(self) -> None:
        recipe = rag.Recipe()

        with pytest.raises(ValidationError):
            recipe.split = rag.SplitOptions(target=10, max=20, min=1)  # type: ignore[misc]

    def test_deriving_a_recipe_leaves_the_original_unchanged(self) -> None:
        original = rag.Recipe()

        original.model_copy(update={"split": rag.SplitOptions(target=10, max=20, min=1)})

        assert original == rag.Recipe()

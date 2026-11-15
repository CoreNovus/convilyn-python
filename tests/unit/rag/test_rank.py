"""`rag.fuse` and `rag.merge_adjacent` — pure functions over hits.

Expected scores are computed by hand from the definitions in the docstrings.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from convilyn import rag


def hit(name: str, score: float, *, source: str = "doc", ordinal: int = 0) -> rag.Hit:
    passage = rag.Passage(
        passage_id=name,
        source_id=source,
        ordinal=ordinal,
        section=(f"section {name}",),
        text=f"text {name}",
        digest="0" * 64,
    )
    return rag.Hit(passage=passage, score=score, title=f"title {source}", meta={"from": name})


def names(hits: tuple[rag.Hit, ...]) -> list[str]:
    return [one.passage.passage_id for one in hits]


def scores(hits: tuple[rag.Hit, ...]) -> dict[str, float]:
    return {one.passage.passage_id: round(one.score, 9) for one in hits}


LEXICAL = [hit("a", 10.0), hit("b", 6.0), hit("c", 2.0)]
VECTOR = [hit("c", 0.9), hit("a", 0.7), hit("d", 0.5)]
RRF = rag.FuseOptions(method="rrf")


class TestMinMax:
    def test_scores_are_scaled_per_ranking_and_averaged(self) -> None:
        result = rag.fuse(LEXICAL, VECTOR)

        # a: (1 + 0.5) / 2, b: (0.5 + 0) / 2, c: (0 + 1) / 2, d: (0 + 0) / 2
        assert scores(result) == {"a": 0.75, "b": 0.25, "c": 0.5, "d": 0.0}

    def test_the_result_is_best_first(self) -> None:
        result = rag.fuse(LEXICAL, VECTOR)

        assert names(result) == ["a", "c", "b", "d"]

    def test_weights_shift_the_balance(self) -> None:
        options = rag.FuseOptions(weights=(1.0, 3.0))

        result = rag.fuse(LEXICAL, VECTOR, options=options)

        # c: 0.25 * 0 + 0.75 * 1, a: 0.25 * 1 + 0.75 * 0.5
        assert names(result)[:2] == ["c", "a"]

    def test_only_the_proportion_of_the_weights_matters(self) -> None:
        small = rag.fuse(LEXICAL, VECTOR, options=rag.FuseOptions(weights=(1.0, 3.0)))

        large = rag.fuse(LEXICAL, VECTOR, options=rag.FuseOptions(weights=(10.0, 30.0)))

        assert scores(small) == scores(large)

    def test_a_weight_of_zero_ignores_that_ranking_s_order(self) -> None:
        result = rag.fuse(LEXICAL, VECTOR, options=rag.FuseOptions(weights=(1.0, 0.0)))

        assert names(result)[:3] == ["a", "b", "c"]

    def test_a_ranking_whose_scores_are_all_equal_gives_each_hit_full_marks(self) -> None:
        flat = [hit("x", 3.0), hit("y", 3.0)]

        result = rag.fuse(flat)

        assert scores(result) == {"x": 1.0, "y": 1.0}

    def test_negative_scores_are_scaled_like_any_other(self) -> None:
        result = rag.fuse([hit("x", -1.0), hit("y", -3.0)])

        assert scores(result) == {"x": 1.0, "y": 0.0}

    def test_scores_further_apart_than_a_float_holds_are_scaled_in_proportion(self) -> None:
        wide = [hit("a", 1e308), hit("b", 5e307), hit("c", -1e308), hit("d", -5e307)]

        result = rag.fuse(wide)

        assert scores(result) == {"a": 1.0, "b": 0.75, "d": 0.25, "c": 0.0}

    def test_scores_near_the_smallest_float_are_scaled_in_proportion(self) -> None:
        tiny = [hit("a", 5e-324), hit("b", 0.0)]

        result = rag.fuse(tiny)

        assert scores(result) == {"a": 1.0, "b": 0.0}

    def test_weights_each_near_the_float_limit_are_a_proportion_like_any_other(self) -> None:
        options = rag.FuseOptions(weights=(1.7e308, 1.7e308))

        result = rag.fuse(LEXICAL, VECTOR, options=options)

        assert scores(result) == scores(rag.fuse(LEXICAL, VECTOR))

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "infinite"])
    def test_a_score_that_cannot_be_scaled_is_refused(self, bad: float) -> None:
        with pytest.raises(ValueError, match="use method rrf"):
            rag.fuse([hit("x", bad), hit("y", 1.0)])


class TestReciprocalRank:
    def test_each_ranking_gives_one_over_k_plus_rank(self) -> None:
        result = rag.fuse(LEXICAL, VECTOR, options=RRF)

        expected = {
            "a": round((1 / 61 + 1 / 62) / 2, 9),
            "b": round((1 / 62) / 2, 9),
            "c": round((1 / 63 + 1 / 61) / 2, 9),
            "d": round((1 / 63) / 2, 9),
        }
        assert scores(result) == expected

    def test_the_scores_themselves_are_not_read(self) -> None:
        unscaled = [hit("x", float("nan")), hit("y", float("inf"))]

        result = rag.fuse(unscaled, options=RRF)

        assert names(result) == ["x", "y"]

    def test_k_flattens_the_difference_between_places(self) -> None:
        options = rag.FuseOptions(method="rrf", rrf_k=1)

        result = rag.fuse(LEXICAL, options=options)

        assert scores(result)["a"] == 0.5


class TestPassagesInOneRankingOnly:
    def test_a_passage_earns_nothing_from_a_ranking_that_does_not_hold_it(self) -> None:
        result = rag.fuse([hit("only", 5.0), hit("both", 1.0)], [hit("both", 1.0)])

        # only: (1 + 0) / 2; both: (0 + 1) / 2
        assert scores(result) == {"only": 0.5, "both": 0.5}

    def test_an_empty_ranking_contributes_nothing_but_still_has_its_weight(self) -> None:
        result = rag.fuse(LEXICAL, [])

        assert scores(result)["a"] == 0.5

    def test_no_rankings_give_no_hits(self) -> None:
        assert rag.fuse() == ()


class TestTies:
    def test_equal_scores_are_ordered_by_the_best_rank_in_any_ranking(self) -> None:
        result = rag.fuse([hit("p", 1.0), hit("q", 0.0)], [hit("q", 1.0), hit("p", 0.0)])

        assert names(result) == ["p", "q"]

    def test_the_order_is_the_same_on_every_call(self) -> None:
        first = rag.fuse(LEXICAL, VECTOR, options=RRF)

        second = rag.fuse(LEXICAL, VECTOR, options=RRF)

        assert names(first) == names(second)

    def test_equal_best_ranks_follow_the_order_of_the_rankings(self) -> None:
        result = rag.fuse([hit("p", 1.0)], [hit("q", 1.0)])

        assert names(result) == ["p", "q"]

    def test_a_later_better_rank_is_what_counts(self) -> None:
        result = rag.fuse(
            [hit("x", 1.0), hit("p", 0.0), hit("q", 0.0)],
            [hit("q", 1.0), hit("x", 0.5), hit("p", 0.0)],
            options=rag.FuseOptions(weights=(1.0, 0.0)),
        )

        # p and q tie at 0; q's best rank (first, in the second ranking) beats p's (second).
        assert names(result) == ["x", "q", "p"]


class TestWhatIsKept:
    def test_the_hit_kept_is_the_one_from_the_first_ranking_that_holds_it(self) -> None:
        other = rag.Hit(passage=LEXICAL[0].passage, score=0.1, title="another title")

        result = rag.fuse([other], LEXICAL)

        assert result[0].title == "another title"

    def test_the_hits_passed_are_not_changed(self) -> None:
        rag.fuse(LEXICAL, VECTOR)

        assert LEXICAL[0].score == 10.0


class TestRefusals:
    def test_a_weight_per_ranking_is_required_when_weights_are_given(self) -> None:
        with pytest.raises(ValueError, match="2 weights for 3 rankings"):
            rag.fuse(LEXICAL, VECTOR, [], options=rag.FuseOptions(weights=(1.0, 1.0)))

    @pytest.mark.parametrize(
        "weights",
        [(), (0.0, 0.0), (-1.0, 2.0), (float("nan"), 1.0), (float("inf"), 1.0)],
        ids=["none", "all-zero", "negative", "nan", "infinite"],
    )
    def test_weights_that_cannot_be_a_proportion_are_refused(
        self, weights: tuple[float, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="weights must be finite numbers"):
            rag.FuseOptions(weights=weights)

    def test_an_unknown_method_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            rag.FuseOptions(method="borda")  # type: ignore[arg-type]

    @pytest.mark.parametrize("k", [0, -1], ids=str)
    def test_a_k_that_is_not_positive_is_refused(self, k: int) -> None:
        with pytest.raises(ValidationError):
            rag.FuseOptions(rrf_k=k)

    def test_one_hit_in_place_of_a_ranking_is_refused(self) -> None:
        with pytest.raises(TypeError, match="ranking 1 must be a sequence of hits, not one hit"):
            rag.fuse(LEXICAL, VECTOR[0])  # type: ignore[arg-type]

    def test_something_other_than_a_hit_in_a_ranking_is_refused(self) -> None:
        with pytest.raises(TypeError, match="ranking 0 holds a str, not a Hit"):
            rag.fuse(["a"])  # type: ignore[list-item]

    @pytest.mark.parametrize(
        "update",
        [{"weights": (0.0,)}, {"weights": (-1.0, 2.0)}, {"weights": (float("nan"), 1.0)}],
        ids=["all-zero", "negative", "nan"],
    )
    def test_weights_that_skipped_validation_are_still_refused(
        self, update: dict[str, object]
    ) -> None:
        options = rag.FuseOptions().model_copy(update=update)
        rankings = [LEXICAL] * len(update["weights"])  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="weights must be finite numbers"):
            rag.fuse(*rankings, options=options)

    def test_a_k_that_skipped_validation_is_still_refused(self) -> None:
        options = rag.FuseOptions.model_construct(method="rrf", rrf_k=-1, weights=None)

        with pytest.raises(ValueError, match="rrf_k must be above 0"):
            rag.fuse(LEXICAL, options=options)

    def test_a_method_that_skipped_validation_is_still_refused(self) -> None:
        options = rag.FuseOptions.model_construct(method="borda", rrf_k=60, weights=None)

        with pytest.raises(ValueError, match='method must be "min_max" or "rrf"'):
            rag.fuse(LEXICAL, options=options)

    def test_a_ranking_that_holds_a_passage_twice_is_refused(self) -> None:
        with pytest.raises(
            ValueError, match="ranking 0 holds one passage twice; list each passage once"
        ):
            rag.fuse([hit("a", 1.0), hit("a", 0.5)])


class TestRecipe:
    def test_a_recipe_carries_the_fusion_settings(self) -> None:
        recipe = rag.Recipe(fuse=rag.FuseOptions(method="rrf", rrf_k=10))

        assert rag.Recipe.model_validate_json(recipe.model_dump_json()) == recipe

    def test_the_default_is_min_max_with_equal_weights(self) -> None:
        assert (rag.Recipe().fuse.method, rag.Recipe().fuse.weights) == ("min_max", None)


def run(*parts: tuple[str, int, float], source: str = "doc") -> list[rag.Hit]:
    return [hit(name, score, source=source, ordinal=ordinal) for name, ordinal, score in parts]


class TestMergeAdjacent:
    def test_neighbouring_passages_of_one_document_become_one_hit(self) -> None:
        hits = run(("p2", 2, 0.9), ("p3", 3, 0.5))

        result = rag.merge_adjacent(hits)

        assert len(result) == 1

    def test_the_joined_text_is_in_the_order_of_the_document(self) -> None:
        hits = run(("p3", 3, 0.9), ("p2", 2, 0.5))

        result = rag.merge_adjacent(hits)

        assert result[0].passage.text == "text p2\n\ntext p3"

    def test_the_joined_hit_keeps_the_score_and_labels_of_the_best_ranked_member(self) -> None:
        hits = run(("p3", 3, 0.9), ("p2", 2, 0.5))

        result = rag.merge_adjacent(hits)

        assert (result[0].score, result[0].meta) == (0.9, {"from": "p3"})

    def test_the_joined_hit_starts_where_the_first_passage_starts(self) -> None:
        hits = run(("p3", 3, 0.9), ("p2", 2, 0.5))

        result = rag.merge_adjacent(hits)

        assert (result[0].passage.ordinal, result[0].passage.section) == (2, ("section p2",))

    def test_the_joined_passage_has_an_identity_of_its_own(self) -> None:
        hits = run(("p2", 2, 0.9), ("p3", 3, 0.5))

        result = rag.merge_adjacent(hits)

        assert result[0].passage.passage_id not in {"p2", "p3"}

    def test_a_run_of_three_is_joined_whole(self) -> None:
        hits = run(("p1", 1, 0.1), ("p3", 3, 0.3), ("p2", 2, 0.2))

        result = rag.merge_adjacent(hits)

        assert result[0].passage.text == "text p1\n\ntext p2\n\ntext p3"

    def test_the_joined_hit_takes_the_place_of_its_best_ranked_member(self) -> None:
        hits = [hit("x", 0.9, source="other"), *run(("p5", 5, 0.8)), hit("y", 0.7, source="y")]
        hits.append(hit("p6", 0.1, ordinal=6))

        result = rag.merge_adjacent(hits)

        assert [one.passage.source_id for one in result] == ["other", "doc", "y"]

    def test_a_gap_keeps_passages_apart(self) -> None:
        hits = run(("p2", 2, 0.9), ("p4", 4, 0.5))

        result = rag.merge_adjacent(hits)

        assert names(result) == ["p2", "p4"]

    def test_neighbouring_ordinals_of_different_documents_are_not_joined(self) -> None:
        hits = [hit("a", 0.9, source="one", ordinal=1), hit("b", 0.5, source="two", ordinal=2)]

        result = rag.merge_adjacent(hits)

        assert names(result) == ["a", "b"]

    def test_a_hit_with_no_neighbour_is_returned_as_it_was(self) -> None:
        single = hit("solo", 0.4, ordinal=9)

        result = rag.merge_adjacent([single])

        assert result[0] is single

    def test_no_hits_give_no_hits(self) -> None:
        assert rag.merge_adjacent([]) == ()

    def test_the_same_passage_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="^hits holds one passage twice"):
            rag.merge_adjacent([hit("a", 1.0), hit("a", 0.5)])

    def test_one_hit_in_place_of_a_list_is_refused(self) -> None:
        with pytest.raises(TypeError, match="^hits must be a sequence of hits, not one hit"):
            rag.merge_adjacent(hit("a", 1.0))  # type: ignore[arg-type]

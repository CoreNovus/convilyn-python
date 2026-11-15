"""Matching and metrics, on cases computed by hand."""

from __future__ import annotations

import math

import pytest

from convilyn.rag.bench import Support
from convilyn.rag.bench._score import covers, first_ranks, grams, scores


class TestGrams:
    def test_text_becomes_its_character_pairs_without_spacing(self) -> None:
        assert grams("ab c") == {"ab", "bc"}

    def test_folding_makes_case_and_width_irrelevant(self) -> None:
        assert grams("ＡＢ") == grams("ab")

    def test_a_single_character_has_no_pair(self) -> None:
        assert grams("退") == frozenset()

    def test_only_hits_that_could_answer_are_split_into_pairs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.rag.bench import _score

        seen: list[str] = []
        original = _score.grams
        monkeypatch.setattr(_score, "grams", lambda text: seen.append(text) or original(text))
        hits = [("other", "x" * 1000), ("a", "five days")]

        first_ranks([Support(source_id="a", excerpt="five days")], hits, 0.5)

        assert seen == ["five days", "five days"]

    def test_empty_text_has_no_grams(self) -> None:
        assert grams("  ") == frozenset()


class TestCovers:
    @pytest.mark.parametrize(("cover", "expected"), [(0.5, True), (0.6, False)])
    def test_the_share_of_pairs_held_is_compared_with_cover(
        self, cover: float, expected: bool
    ) -> None:
        assert covers(frozenset({"ab", "bc"}), frozenset({"ab", "zz"}), cover) is expected

    def test_an_excerpt_with_no_pairs_is_never_covered(self) -> None:
        assert covers(frozenset(), frozenset({"ab"}), 0.5) is False


class TestFirstRanks:
    def test_the_first_hit_from_the_document_counts(self) -> None:
        hits = [("b", "other"), ("a", "anything"), ("a", "again")]

        assert first_ranks([Support(source_id="a")], hits, 0.5) == [2]

    def test_an_excerpt_must_be_covered_too(self) -> None:
        hits = [("a", "shipping times"), ("a", "refunds take five days")]

        assert first_ranks([Support(source_id="a", excerpt="five days")], hits, 0.5) == [2]

    def test_an_item_no_hit_supports_has_no_rank(self) -> None:
        assert first_ranks([Support(source_id="a")], [("b", "text")], 0.5) == [None]


class TestScores:
    def test_one_item_found_first_scores_one_everywhere(self) -> None:
        assert scores([1], (1, 5)) == {
            "recall@1": 1.0,
            "recall@5": 1.0,
            "mrr@1": 1.0,
            "mrr@5": 1.0,
            "ndcg@1": 1.0,
            "ndcg@5": 1.0,
        }

    def test_an_item_at_three_and_one_missing(self) -> None:
        result = scores([3, None], (1, 5))

        ideal = 1 + 1 / math.log2(3)
        assert result == pytest.approx(
            {
                "recall@1": 0.0,
                "recall@5": 0.5,
                "mrr@1": 0.0,
                "mrr@5": 1 / 3,
                "ndcg@1": 0.0,
                "ndcg@5": (1 / math.log2(4)) / ideal,
            }
        )

    def test_one_passage_covering_every_item_is_ideal(self) -> None:
        assert scores([1, 1], (10,))["ndcg@10"] == pytest.approx(1.0)

    def test_tied_items_take_the_following_places(self) -> None:
        dcg = 1 + 1 / math.log2(3) + 1 / math.log2(6)
        ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)

        assert scores([1, 1, 5], (10,))["ndcg@10"] == pytest.approx(dcg / ideal)

    def test_more_items_than_the_cut_off_can_still_score_one(self) -> None:
        assert scores([1, 1, 1], (2,)) == pytest.approx(
            {"recall@2": 1.0, "mrr@2": 1.0, "ndcg@2": 1.0}
        )

    def test_a_hit_is_split_into_pairs_once_for_several_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.rag.bench import _score

        seen: list[str] = []
        original = _score.grams
        monkeypatch.setattr(_score, "grams", lambda text: seen.append(text) or original(text))
        support = [
            Support(source_id="a", excerpt="five days"),
            Support(source_id="a", excerpt="two weeks"),
        ]

        ranks = first_ranks(support, [("a", "five days or two weeks")], 0.5)

        assert (ranks, seen.count("five days or two weeks")) == ([1, 1], 1)

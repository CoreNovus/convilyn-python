"""The fixed sequence of a provider call — the pure steps."""

from __future__ import annotations

import math

import pytest

from convilyn.providers import ProviderError
from convilyn.providers import _steps as steps


class TestCheckTexts:
    def test_a_list_of_strings_is_returned_as_a_tuple(self) -> None:
        assert steps.check_texts(["a", "b"]) == ("a", "b")

    def test_any_sequence_is_accepted(self) -> None:
        assert steps.check_texts(("a",)) == ("a",)

    def test_whitespace_is_a_text(self) -> None:
        assert steps.check_texts([" "]) == (" ",)

    def test_one_string_is_refused_with_the_way_to_pass_it(self) -> None:
        with pytest.raises(TypeError, match=r"pass \[text\]"):
            steps.check_texts("refund policy")

    def test_no_texts_are_refused(self) -> None:
        with pytest.raises(ValueError, match="texts must not be empty"):
            steps.check_texts([])

    def test_an_empty_text_is_refused_by_position(self) -> None:
        with pytest.raises(ValueError, match="text 1 is empty"):
            steps.check_texts(["a", ""])

    @pytest.mark.parametrize("item", [None, 3, b"a"], ids=["none", "number", "bytes"])
    def test_a_text_that_is_not_a_string_is_refused_by_position(self, item: object) -> None:
        with pytest.raises(TypeError, match="text 1 must be a string"):
            steps.check_texts(["a", item])  # type: ignore[list-item]


class TestFit:
    def test_texts_within_the_limit_are_unchanged(self) -> None:
        result = steps.fit(("abc", "de"), limit=3, measure=len, policy="error")

        assert result == ("abc", "de")

    def test_a_text_over_the_limit_is_refused_by_position_and_size(self) -> None:
        with pytest.raises(ValueError, match="text 1 measures 4 tokens and the limit is 3"):
            steps.fit(("abc", "abcd"), limit=3, measure=len, policy="error")

    def test_a_refused_text_is_not_echoed(self) -> None:
        with pytest.raises(ValueError) as caught:
            steps.fit(("secret words",), limit=3, measure=len, policy="error")

        assert "secret" not in str(caught.value)

    def test_truncate_keeps_the_longest_beginning_that_fits(self) -> None:
        result = steps.fit(("abcdefgh", "ab"), limit=3, measure=len, policy="truncate")

        assert result == ("abc", "ab")

    def test_truncate_follows_the_measure_not_the_length(self) -> None:
        words = "one two three four five"

        result = steps.fit(
            (words,), limit=2, measure=lambda text: len(text.split()), policy="truncate"
        )

        assert result == ("one two ",)

    def test_truncate_keeps_one_character_when_even_that_is_over(self) -> None:
        result = steps.fit(("abc",), limit=1, measure=lambda text: 2 * len(text), policy="truncate")

        assert result == ("a",)

    @pytest.mark.parametrize("policy", ["error", "truncate"])
    def test_without_a_limit_nothing_is_checked(self, policy: str) -> None:
        result = steps.fit(("a" * 100,), limit=None, measure=len, policy=policy)  # type: ignore[arg-type]

        assert result == ("a" * 100,)

    @pytest.mark.parametrize("policy", ["error", "truncate"])
    def test_without_a_measure_nothing_is_checked(self, policy: str) -> None:
        result = steps.fit(("a" * 100,), limit=3, measure=None, policy=policy)  # type: ignore[arg-type]

        assert result == ("a" * 100,)


class TestBatches:
    def test_texts_are_cut_into_requests_in_order(self) -> None:
        result = steps.batches(("a", "b", "c", "d", "e"), 2)

        assert result == [
            steps.Batch(start=0, texts=("a", "b")),
            steps.Batch(start=2, texts=("c", "d")),
            steps.Batch(start=4, texts=("e",)),
        ]

    def test_exactly_one_request_s_worth_is_one_request(self) -> None:
        assert len(steps.batches(("a", "b"), 2)) == 1

    def test_one_more_than_a_request_s_worth_is_two(self) -> None:
        assert len(steps.batches(("a", "b", "c"), 2)) == 2


class TestRefusal:
    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (400, "invalid_request"),
            (401, "auth"),
            (403, "auth"),
            (404, "invalid_request"),
            (408, "unavailable"),
            (413, "invalid_request"),
            (422, "invalid_request"),
            (429, "rate_limited"),
            (500, "unavailable"),
            (503, "unavailable"),
            (504, "unavailable"),
        ],
    )
    def test_a_status_maps_to_a_category(self, status: int, category: str) -> None:
        error = steps.refusal(status)

        assert (error.category, error.status) == (category, status)

    def test_the_error_is_a_provider_error(self) -> None:
        assert isinstance(steps.refusal(500), ProviderError)

    def test_a_failure_without_a_status_has_none(self) -> None:
        assert steps.failure("connection").status is None


class TestUnit:
    def test_a_vector_is_scaled_to_length_one(self) -> None:
        assert steps.unit([3.0, 4.0]) == (0.6, 0.8)

    def test_integers_are_accepted(self) -> None:
        assert steps.unit([0, 2]) == (0.0, 1.0)

    def test_a_unit_vector_keeps_its_length(self) -> None:
        result = steps.unit([0.1] * 1536)

        assert math.sqrt(sum(value * value for value in result)) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        "vector",
        [
            [0.0, 0.0],
            [float("nan"), 1.0],
            [1.0, float("nan")],
            [0.5, float("nan"), 1.0],
            [float("inf"), 1.0],
            [1.0, float("-inf")],
        ],
        ids=["zeros", "nan-first", "nan-last", "nan-in-the-middle", "infinity", "infinity-last"],
    )
    def test_a_vector_without_a_direction_is_a_bad_response(self, vector: list[float]) -> None:
        with pytest.raises(ProviderError) as caught:
            steps.unit(vector)

        assert caught.value.category == "bad_response"

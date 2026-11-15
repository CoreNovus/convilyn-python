"""The Cohere rerank format — the request built and the response read."""

from __future__ import annotations

import pytest

from convilyn.providers import ProviderError
from convilyn.providers._steps import Batch
from convilyn.providers._wires import cohere_rerank as wire

BATCH = Batch(start=0, texts=("a", "b"))


def scored(index: object, score: object) -> dict[str, object]:
    return {"index": index, "relevance_score": score}


class TestRequest:
    def test_the_address_is_the_rerank_path(self) -> None:
        assert wire.url("https://api.cohere.com/v2", "m") == "https://api.cohere.com/v2/rerank"

    def test_the_body_asks_for_a_score_for_every_document(self) -> None:
        body = wire.body(BATCH, model="m", query="q")

        assert body == {"model": "m", "query": "q", "documents": ["a", "b"], "top_n": 2}


class TestRead:
    def test_scores_come_back_by_position_in_the_request(self) -> None:
        payload = {"results": [scored(1, 0.9), scored(0, 0.2)]}

        assert wire.read(payload, expected=2) == [0.2, 0.9]

    def test_a_whole_number_score_is_accepted(self) -> None:
        payload = {"results": [scored(0, 1), scored(1, 0)]}

        assert wire.read(payload, expected=2) == [1.0, 0.0]

    def test_a_score_outside_zero_to_one_is_still_a_score(self) -> None:
        payload = {"results": [scored(0, -3.5), scored(1, 12.0)]}

        assert wire.read(payload, expected=2) == [-3.5, 12.0]

    def test_what_else_the_answer_carries_is_ignored(self) -> None:
        payload = {
            "id": "x",
            "results": [{**scored(0, 0.5), "document": {"text": "a"}}, scored(1, 0.1)],
            "meta": {"billed_units": {"search_units": 1}},
        }

        assert wire.read(payload, expected=2) == [0.5, 0.1]


class TestReadRefuses:
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"results": None},
            {"results": [scored(0, 0.5)]},
            {"results": [scored(0, 0.5), scored(1, 0.5), scored(2, 0.5)]},
            {"results": [scored(0, 0.5), "x"]},
            {"results": [scored(0, 0.5), scored(0, 0.4)]},
            {"results": [scored(0, 0.5), scored(2, 0.4)]},
            {"results": [scored(0, 0.5), scored(-1, 0.4)]},
            {"results": [scored(0, 0.5), scored("1", 0.4)]},
            {"results": [scored(0, 0.5), scored(True, 0.4)]},
            {"results": [scored(0, 0.5), {"index": 1}]},
            {"results": [scored(0, 0.5), scored(1, "0.4")]},
            {"results": [scored(0, 0.5), scored(1, None)]},
            {"results": [scored(0, 0.5), scored(1, True)]},
            {"results": [scored(0, 0.5), scored(1, float("nan"))]},
            {"results": [scored(0, 0.5), scored(1, float("inf"))]},
            {"results": [scored(0, 0.5), scored(1, 10**400)]},
        ],
        ids=[
            "null",
            "list",
            "no-results",
            "results-null",
            "a-document-left-out",
            "too-many",
            "item-not-mapping",
            "position-scored-twice",
            "position-not-sent",
            "position-negative",
            "position-text",
            "position-boolean",
            "no-score",
            "score-text",
            "score-null",
            "score-boolean",
            "score-not-a-number",
            "score-infinite",
            "score-too-large-for-a-float",
        ],
    )
    def test_anything_that_does_not_score_each_document_sent_once(self, payload: object) -> None:
        with pytest.raises(ProviderError) as caught:
            wire.read(payload, expected=2)

        assert caught.value.category == "bad_response"

    def test_a_refused_score_keeps_no_conversion_error(self) -> None:
        with pytest.raises(ProviderError) as caught:
            wire.read({"results": [scored(0, 10**400)]}, expected=1)

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

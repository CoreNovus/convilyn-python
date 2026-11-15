"""The OpenAI embeddings format — the request built and the response read."""

from __future__ import annotations

import pytest

from convilyn.providers import ProviderError
from convilyn.providers._steps import Batch
from convilyn.providers._wires import openai_embeddings as wire

BATCH = Batch(start=0, texts=("a", "b"))


def item(index: int, vector: list[float]) -> dict[str, object]:
    return {"object": "embedding", "index": index, "embedding": vector}


class TestBody:
    def test_the_smallest_request(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind="document", kind_style=None)

        assert body == {"model": "m", "input": ["a", "b"], "encoding_format": "float"}

    def test_dimensions_are_sent_when_asked_for(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=256, kind="document", kind_style=None)

        assert body["dimensions"] == 256

    @pytest.mark.parametrize(
        ("kind", "expected"), [("document", "search_document"), ("query", "search_query")]
    )
    def test_a_model_that_tells_kinds_apart_is_told(self, kind: str, expected: str) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind=kind, kind_style="field")  # type: ignore[arg-type]

        assert body["input_type"] == expected

    def test_a_model_that_does_not_is_not_sent_the_kind(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind="query", kind_style=None)

        assert "input_type" not in body


class TestRead:
    def test_vectors_come_back_in_request_order(self) -> None:
        payload = {"data": [item(0, [1.0]), item(1, [2.0])], "model": "m"}

        vectors, _, _ = wire.read(payload, expected=2)

        assert vectors == [[1.0], [2.0]]

    def test_items_returned_out_of_order_are_put_back(self) -> None:
        payload = {"data": [item(1, [2.0]), item(0, [1.0])]}

        vectors, _, _ = wire.read(payload, expected=2)

        assert vectors == [[1.0], [2.0]]

    def test_the_model_and_token_count_are_read(self) -> None:
        payload = {"data": [item(0, [1.0])], "model": "m-2", "usage": {"total_tokens": 7}}

        _, model, tokens = wire.read(payload, expected=1)

        assert (model, tokens) == ("m-2", 7)

    @pytest.mark.parametrize(
        "extra",
        [{}, {"model": 3}, {"usage": None}, {"usage": {"total_tokens": "7"}}, {"usage": {}}],
        ids=["absent", "model-not-text", "usage-null", "tokens-not-a-number", "usage-empty"],
    )
    def test_a_missing_or_odd_model_or_count_is_none(self, extra: dict[str, object]) -> None:
        payload = {"data": [item(0, [1.0])], **extra}

        _, model, tokens = wire.read(payload, expected=1)

        assert (model if "model" in extra else None, tokens) == (None, None)

    def test_integer_components_are_accepted(self) -> None:
        vectors, _, _ = wire.read({"data": [item(0, [1, 0])]}, expected=1)

        assert vectors == [[1, 0]]


class TestReadRefuses:
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "text",
            {},
            {"data": None},
            {"data": {}},
            {"data": [item(0, [1.0])]},
            {"data": [item(0, [1.0]), item(1, [1.0]), item(2, [1.0])]},
            {"data": [item(0, [1.0]), "x"]},
            {"data": [item(0, [1.0]), item(0, [2.0])]},
            {"data": [item(0, [1.0]), item(2, [2.0])]},
            {"data": [item(0, [1.0]), item(-1, [2.0])]},
            {"data": [item(0, [1.0]), {"index": "1", "embedding": [2.0]}]},
            {"data": [item(0, [1.0]), {"index": True, "embedding": [2.0]}]},
            {"data": [item(0, [1.0]), {"index": 1}]},
            {"data": [item(0, [1.0]), item(1, [])]},
            {"data": [item(0, [1.0]), {"index": 1, "embedding": "AAAA"}]},
            {"data": [item(0, [1.0]), {"index": 1, "embedding": [1.0, "2"]}]},
            {"data": [item(0, [1.0]), {"index": 1, "embedding": [1.0, None]}]},
            {"data": [item(0, [1.0]), {"index": 1, "embedding": [1.0, True]}]},
        ],
        ids=[
            "null",
            "list",
            "text",
            "no-data",
            "data-null",
            "data-mapping",
            "too-few",
            "too-many",
            "item-not-mapping",
            "index-repeated",
            "index-too-large",
            "index-negative",
            "index-text",
            "index-boolean",
            "no-embedding",
            "empty-embedding",
            "encoded-embedding",
            "text-component",
            "null-component",
            "boolean-component",
        ],
    )
    def test_anything_that_does_not_account_for_the_texts_sent(self, payload: object) -> None:
        with pytest.raises(ProviderError) as caught:
            wire.read(payload, expected=2)

        assert caught.value.category == "bad_response"

"""The Gemini embeddings format — the address, the request built and the response read."""

from __future__ import annotations

import pytest

from convilyn.providers import ProviderError
from convilyn.providers._steps import Batch
from convilyn.providers._wires import gemini_embeddings as wire

BATCH = Batch(start=0, texts=("a", "b"))
BASE = "https://generativelanguage.googleapis.com/v1beta"


def texts_of(body: dict[str, object]) -> list[str]:
    requests: list[dict[str, dict[str, list[dict[str, str]]]]] = body["requests"]  # type: ignore[assignment]
    return [request["content"]["parts"][0]["text"] for request in requests]


class TestAddress:
    def test_the_model_is_part_of_the_address(self) -> None:
        address = wire.url(BASE, "gemini-embedding-001")

        assert address == f"{BASE}/models/gemini-embedding-001:batchEmbedContents"

    @pytest.mark.parametrize(
        "model",
        [
            "models/gemini-embedding-001",
            "../other",
            "a/b",
            "a?key=1",
            "a#b",
            "a:b",
            "a b",
            "a%2Fb",
            ".hidden",
            "名前",
        ],
        ids=[
            "resource-name",
            "parent",
            "slash",
            "query",
            "fragment",
            "colon",
            "space",
            "encoded-slash",
            "leading-dot",
            "non-ascii",
        ],
    )
    def test_a_model_that_could_be_read_as_another_path_is_refused(self, model: str) -> None:
        with pytest.raises(ValueError, match="model must be the model's name alone"):
            wire.url(BASE, model)

    def test_a_model_name_of_ordinary_length_is_accepted(self) -> None:
        assert wire.url(BASE, "m" * 256).endswith(":batchEmbedContents")

    def test_a_model_name_too_long_to_be_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="model must be the model's name alone"):
            wire.url(BASE, "m" * 257)

    def test_the_key_travels_in_a_header(self) -> None:
        assert wire.headers("k") == {"x-goog-api-key": "k"}


class TestBody:
    def test_the_smallest_request(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind="document", kind_style=None)

        assert body == {
            "requests": [
                {"model": "models/m", "content": {"parts": [{"text": "a"}]}},
                {"model": "models/m", "content": {"parts": [{"text": "b"}]}},
            ]
        }

    def test_the_size_asked_for_is_sent_with_every_text(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=768, kind="document", kind_style=None)

        assert [request["outputDimensionality"] for request in body["requests"]] == [768, 768]

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("document", "RETRIEVAL_DOCUMENT"), ("query", "RETRIEVAL_QUERY")],
    )
    def test_a_model_that_takes_a_task_is_sent_it(self, kind: str, expected: str) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind=kind, kind_style="field")  # type: ignore[arg-type]

        assert [request["taskType"] for request in body["requests"]] == [expected, expected]

    def test_a_task_in_a_field_leaves_the_text_as_it_is(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind="query", kind_style="field")

        assert texts_of(body) == ["a", "b"]

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("document", ["title: none | text: a", "title: none | text: b"]),
            ("query", ["task: search result | query: a", "task: search result | query: b"]),
        ],
    )
    def test_a_model_told_in_words_has_them_put_before_the_text(
        self, kind: str, expected: list[str]
    ) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind=kind, kind_style="prefix")  # type: ignore[arg-type]

        assert texts_of(body) == expected

    def test_a_model_told_in_words_is_sent_no_task_field(self) -> None:
        body = wire.body(BATCH, model="m", dimensions=None, kind="query", kind_style="prefix")

        assert ["taskType" in request for request in body["requests"]] == [False, False]


class TestRead:
    def test_vectors_come_back_in_request_order(self) -> None:
        payload = {"embeddings": [{"values": [1.0]}, {"values": [2.0]}]}

        vectors, _, _ = wire.read(payload, expected=2)

        assert vectors == [[1.0], [2.0]]

    def test_the_answer_names_no_model(self) -> None:
        _, model, _ = wire.read({"embeddings": [{"values": [1.0]}]}, expected=1)

        assert model is None

    def test_the_token_count_is_read(self) -> None:
        payload = {"embeddings": [{"values": [1.0]}], "usageMetadata": {"promptTokenCount": 7}}

        _, _, tokens = wire.read(payload, expected=1)

        assert tokens == 7

    @pytest.mark.parametrize(
        "usage",
        [None, {}, {"promptTokenCount": "7"}, {"promptTokenCount": -1}, {"promptTokenCount": True}],
        ids=["null", "empty", "text", "negative", "boolean"],
    )
    def test_a_missing_or_odd_count_is_none(self, usage: object) -> None:
        payload = {"embeddings": [{"values": [1.0]}], "usageMetadata": usage}

        _, _, tokens = wire.read(payload, expected=1)

        assert tokens is None


class TestReadRefuses:
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"embeddings": None},
            {"embeddings": [{"values": [1.0]}]},
            {"embeddings": [{"values": [1.0]}, {"values": [1.0]}, {"values": [1.0]}]},
            {"embeddings": [{"values": [1.0]}, "x"]},
            {"embeddings": [{"values": [1.0]}, {}]},
            {"embeddings": [{"values": [1.0]}, {"values": []}]},
            {"embeddings": [{"values": [1.0]}, {"values": [1.0, "2"]}]},
            {"embeddings": [{"values": [1.0]}, {"values": [1.0, True]}]},
        ],
        ids=[
            "null",
            "list",
            "no-embeddings",
            "embeddings-null",
            "too-few",
            "too-many",
            "item-not-mapping",
            "no-values",
            "empty-values",
            "text-component",
            "boolean-component",
        ],
    )
    def test_anything_that_cannot_be_matched_to_the_texts_sent(self, payload: object) -> None:
        with pytest.raises(ProviderError) as caught:
            wire.read(payload, expected=2)

        assert caught.value.category == "bad_response"

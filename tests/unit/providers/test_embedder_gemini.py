"""`providers.embedder("gemini", ...)`: a second wire format behind the same embedder.

No test here reaches a network: every request is answered by respx.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator

import httpx
import pytest
import respx

from convilyn import providers

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
URL_001 = f"{BASE}/gemini-embedding-001:batchEmbedContents"
URL_2 = f"{BASE}/gemini-embedding-2:batchEmbedContents"
KEY = "gm-test-0123456789abcdef"  # pragma: allowlist secret


def answer(request: httpx.Request) -> httpx.Response:
    """Answer like the service: one embedding per request, [position, length of text]."""
    requests = json.loads(request.content)["requests"]
    embeddings = [
        {"values": [float(index + 1), float(len(item["content"]["parts"][0]["text"]))]}
        for index, item in enumerate(requests)
    ]
    return httpx.Response(
        200, json={"embeddings": embeddings, "usageMetadata": {"promptTokenCount": len(requests)}}
    )


def sent(route: respx.Route) -> list[dict[str, object]]:
    return [item for call in route.calls for item in json.loads(call.request.content)["requests"]]


def texts(route: respx.Route) -> list[str]:
    return [item["content"]["parts"][0]["text"] for item in sent(route)]  # type: ignore[index]


@pytest.fixture
def api() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL_001).mock(side_effect=answer)


@pytest.fixture
def newer() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL_2).mock(side_effect=answer)


@pytest.fixture
def embedder() -> Iterator[providers.Embedder]:
    with providers.embedder("gemini", model="gemini-embedding-001", api_key=KEY) as made:
        yield made


class TestEmbed:
    def test_each_text_gets_a_unit_length_vector(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        result = embedder.embed(["abc", "abcd"])

        assert [round(math.hypot(*vector), 9) for vector in result.vectors] == [1.0, 1.0]

    def test_the_key_travels_in_the_provider_s_header(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"])

        assert api.calls.last.request.headers["x-goog-api-key"] == KEY

    def test_the_key_is_not_sent_as_a_bearer_token_too(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"])

        assert "authorization" not in api.calls.last.request.headers

    def test_the_key_is_not_part_of_the_address(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"])

        assert str(api.calls.last.request.url) == URL_001

    def test_every_text_names_the_model_of_the_address(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a", "b"])

        assert [item["model"] for item in sent(api)] == ["models/gemini-embedding-001"] * 2

    def test_the_result_names_the_model_asked_for(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        assert embedder.embed(["a"]).model == "gemini-embedding-001"

    def test_the_result_counts_the_tokens_the_provider_reported(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        assert embedder.embed(["a", "b", "c"]).tokens == 3

    def test_dimensions_are_sent_as_the_provider_s_size_parameter(self, api: respx.Route) -> None:
        with providers.embedder(
            "gemini", model="gemini-embedding-001", api_key=KEY, dimensions=2
        ) as made:
            made.embed(["a"])

        assert sent(api)[0]["outputDimensionality"] == 2

    def test_texts_beyond_the_row_s_limit_go_in_several_requests(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"] * 101)

        assert api.call_count == 2


class TestKinds:
    def test_a_document_is_sent_with_the_document_task(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"])

        assert sent(api)[0]["taskType"] == "RETRIEVAL_DOCUMENT"

    def test_a_query_is_sent_with_the_query_task(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"], kind="query")

        assert sent(api)[0]["taskType"] == "RETRIEVAL_QUERY"

    def test_the_newer_model_is_told_a_document_in_words(self, newer: respx.Route) -> None:
        with providers.embedder("gemini", model="gemini-embedding-2", api_key=KEY) as made:
            made.embed(["refunds"])

        assert texts(newer) == ["title: none | text: refunds"]

    def test_the_newer_model_is_told_a_query_in_words(self, newer: respx.Route) -> None:
        with providers.embedder("gemini", model="gemini-embedding-2", api_key=KEY) as made:
            made.embed(["refunds"], kind="query")

        assert texts(newer) == ["task: search result | query: refunds"]

    def test_the_newer_model_is_sent_no_task_field(self, newer: respx.Route) -> None:
        with providers.embedder("gemini", model="gemini-embedding-2", api_key=KEY) as made:
            made.embed(["a"], kind="query")

        assert "taskType" not in sent(newer)[0]

    def test_a_model_the_row_does_not_know_is_sent_as_is(self) -> None:
        with respx.mock() as mock:
            route = mock.post(f"{BASE}/some-future-model:batchEmbedContents").mock(
                side_effect=answer
            )
            with providers.embedder("gemini", model="some-future-model", api_key=KEY) as made:
                made.embed(["a"], kind="query")

        assert sent(route) == [
            {"model": "models/some-future-model", "content": {"parts": [{"text": "a"}]}}
        ]


class TestLimits:
    def test_each_model_has_its_own_token_limit(self) -> None:
        older = providers.embedder("gemini", model="gemini-embedding-001", api_key=KEY)
        newer = providers.embedder("gemini", model="gemini-embedding-2", api_key=KEY)

        assert (older._settings.max_input_tokens, newer._settings.max_input_tokens) == (2048, 8192)

    def test_a_model_the_row_does_not_know_has_no_token_limit(self) -> None:
        made = providers.embedder("gemini", model="some-future-model", api_key=KEY)

        assert made._settings.max_input_tokens is None

    def test_a_limit_passed_in_outranks_the_model_s(self) -> None:
        made = providers.embedder(
            "gemini", model="gemini-embedding-001", api_key=KEY, max_input_tokens=500
        )

        assert made._settings.max_input_tokens == 500

    def test_the_model_s_limit_refuses_a_long_text_when_a_measure_is_given(
        self, api: respx.Route
    ) -> None:
        with providers.embedder(
            "gemini", model="gemini-embedding-001", api_key=KEY, measure=len
        ) as made:
            with pytest.raises(
                ValueError, match="text 0 measures 2049 tokens and the limit is 2048"
            ):
                made.embed(["a" * 2049])


class TestConfiguration:
    def test_the_key_is_read_from_the_row_s_environment_variable(
        self, api: respx.Route, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with providers.embedder("gemini", model="gemini-embedding-001") as made:
            made.embed(["a"])

        assert api.calls.last.request.headers["x-goog-api-key"] == KEY

    @pytest.mark.parametrize(
        "model", ["models/gemini-embedding-001", "../x", "a?key=1", "a/b"], ids=str
    )
    def test_a_model_that_would_change_the_address_is_refused_when_configuring(
        self, model: str
    ) -> None:
        with pytest.raises(ValueError, match="model must be the model's name alone"):
            providers.embedder("gemini", model=model, api_key=KEY)

    def test_base_url_replaces_the_provider_s_own_address(self) -> None:
        with respx.mock() as mock:
            route = mock.post(
                "https://proxy.example.com/g/models/gemini-embedding-001:batchEmbedContents"
            ).mock(side_effect=answer)
            with providers.embedder(
                "gemini",
                model="gemini-embedding-001",
                api_key=KEY,
                base_url="https://proxy.example.com/g",
            ) as made:
                made.embed(["a"])

        assert route.call_count == 1

    def test_a_provider_with_no_embeddings_is_refused_with_the_ones_that_have_them(self) -> None:
        with pytest.raises(
            ValueError,
            match=(
                "no provider 'cohere' for embeddings; "
                "choose one of: gemini, mixroute, openai, openai_compatible"
            ),
        ):
            providers.embedder("cohere", model="m", api_key=KEY)


class TestFailures:
    def test_an_answer_with_too_few_embeddings_is_a_bad_response(
        self, embedder: providers.Embedder
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL_001).respond(200, json={"embeddings": [{"values": [1.0]}]})
            with pytest.raises(providers.ProviderError) as caught:
                embedder.embed(["a", "b"])

        assert caught.value.category == "bad_response"

    def test_an_error_carries_neither_the_key_nor_the_reply(self) -> None:
        marker = "KEYMARKER0123456789"
        with respx.mock() as mock:
            mock.post(URL_001).respond(400, text=f"PRIVATE-REPLY {marker}")
            with providers.embedder("gemini", model="gemini-embedding-001", api_key=marker) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.embed(["a"])

        shown = f"{caught.value!s}{caught.value!r}"
        assert not any(secret in shown for secret in (marker, "PRIVATE-REPLY", "googleapis"))

    def test_the_settings_repr_does_not_show_the_key(self) -> None:
        marker = "KEYMARKER0123456789"
        made = providers.embedder("gemini", model="gemini-embedding-001", api_key=marker)

        assert marker not in repr(made._settings)


class TestAsync:
    async def test_the_async_embedder_speaks_the_same_format(self) -> None:
        with respx.mock() as mock:
            route = mock.post(URL_001).mock(side_effect=answer)
            async with providers.aembedder(
                "gemini", model="gemini-embedding-001", api_key=KEY
            ) as made:
                result = await made.embed(["a", "b"], kind="query")

        assert (len(result.vectors), sent(route)[0]["taskType"]) == (2, "RETRIEVAL_QUERY")

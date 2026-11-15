"""`providers.reranker` / `providers.areranker` against a mocked HTTP layer.

No test here reaches a network: every request is answered by respx.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from convilyn import providers
from convilyn._internal.resilience import NoRetry

URL = "https://api.cohere.com/v2/rerank"
KEY = "co-test-0123456789abcdef"  # pragma: allowlist secret
MARKER = "KEYMARKER0123456789"


class Instant:
    """Retries a passing failure, without waiting."""

    def should_retry(
        self, response: httpx.Response | None, exc: BaseException | None, attempt: int
    ) -> bool:
        return attempt < 3 and response is not None and response.status_code in (429, 500, 503)

    def next_delay(self, response: httpx.Response | None, attempt: int) -> float:
        return 0.0


def by_length(request: httpx.Request) -> httpx.Response:
    """Answer like the service: the longer a document, the higher its score, best first."""
    documents = json.loads(request.content)["documents"]
    results = [
        {"index": index, "relevance_score": len(text) / 100} for index, text in enumerate(documents)
    ]
    results.sort(key=lambda item: -item["relevance_score"])
    return httpx.Response(200, json={"id": "x", "results": results})


def bodies(route: respx.Route) -> list[dict[str, object]]:
    return [json.loads(call.request.content) for call in route.calls]


def positions(result: providers.RerankResult) -> list[int]:
    return [ranked.index for ranked in result.ranking]


@pytest.fixture
def api() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL).mock(side_effect=by_length)


@pytest.fixture
def reranker() -> Iterator[providers.Reranker]:
    with providers.reranker("cohere", model="m", api_key=KEY, retry=Instant()) as made:
        yield made


class TestRerank:
    def test_documents_come_back_by_position_best_first(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        result = reranker.rerank("q", ["aa", "aaaa", "a"])

        assert positions(result) == [1, 0, 2]

    def test_each_document_carries_its_score(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        result = reranker.rerank("q", ["aa", "aaaa"])

        assert [ranked.score for ranked in result.ranking] == [0.04, 0.02]

    def test_the_request_carries_the_model_the_query_and_the_documents(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        reranker.rerank("q", ["a", "b"])

        assert bodies(api) == [{"model": "m", "query": "q", "documents": ["a", "b"], "top_n": 2}]

    def test_the_key_is_sent_as_a_bearer_token(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        reranker.rerank("q", ["a"])

        assert api.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    def test_top_n_keeps_the_best_few(self, api: respx.Route, reranker: providers.Reranker) -> None:
        result = reranker.rerank("q", ["aa", "aaaa", "a"], top_n=2)

        assert positions(result) == [1, 0]

    def test_top_n_larger_than_the_list_returns_them_all(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        result = reranker.rerank("q", ["aa", "a"], top_n=10)

        assert positions(result) == [0, 1]

    def test_documents_with_the_same_score_keep_the_order_given(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        result = reranker.rerank("q", ["bb", "aa", "cc"])

        assert positions(result) == [0, 1, 2]

    def test_the_result_is_frozen(self, api: respx.Route, reranker: providers.Reranker) -> None:
        result = reranker.rerank("q", ["a"])

        with pytest.raises(Exception, match="frozen"):
            result.ranking = ()  # type: ignore[misc]

    def test_a_reranker_says_what_it_is_for(self, reranker: providers.Reranker) -> None:
        assert (reranker.info.name, reranker.model) == ("cohere", "m")


class TestBatching:
    def test_documents_beyond_the_limit_go_in_several_requests(self, api: respx.Route) -> None:
        with providers.reranker("cohere", model="m", api_key=KEY, max_documents=2) as made:
            made.rerank("q", ["a", "b", "c", "d", "e"])

        assert [body["documents"] for body in bodies(api)] == [["a", "b"], ["c", "d"], ["e"]]

    def test_every_request_carries_the_same_query(self, api: respx.Route) -> None:
        with providers.reranker("cohere", model="m", api_key=KEY, max_documents=2) as made:
            made.rerank("q", ["a", "b", "c"])

        assert [body["query"] for body in bodies(api)] == ["q", "q"]

    def test_the_answers_are_merged_into_one_ranking_by_original_position(
        self, api: respx.Route
    ) -> None:
        with providers.reranker("cohere", model="m", api_key=KEY, max_documents=2) as made:
            result = made.rerank("q", ["aa", "a", "aaaaa", "aaa", "aaaa"])

        assert positions(result) == [2, 4, 3, 0, 1]

    def test_top_n_is_applied_after_all_requests_are_in(self, api: respx.Route) -> None:
        with providers.reranker("cohere", model="m", api_key=KEY, max_documents=2) as made:
            result = made.rerank("q", ["aa", "a", "aaaaa", "aaa", "aaaa"], top_n=2)

        assert positions(result) == [2, 4]

    def test_the_row_s_limit_applies_when_none_is_passed(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        reranker.rerank("q", ["a"] * 1001)

        assert [len(body["documents"]) for body in bodies(api)] == [1000, 1]  # type: ignore[arg-type]


class TestInputs:
    def test_one_string_for_the_documents_is_refused(self, reranker: providers.Reranker) -> None:
        with pytest.raises(TypeError, match="documents must be a sequence of strings"):
            reranker.rerank("q", "abc")

    def test_no_documents_is_refused(self, reranker: providers.Reranker) -> None:
        with pytest.raises(ValueError, match="documents must not be empty"):
            reranker.rerank("q", [])

    def test_an_empty_document_is_refused_by_position(self, reranker: providers.Reranker) -> None:
        with pytest.raises(ValueError, match="document 1 is empty"):
            reranker.rerank("q", ["a", ""])

    def test_an_empty_query_is_refused(self, reranker: providers.Reranker) -> None:
        with pytest.raises(ValueError, match="query is empty"):
            reranker.rerank("", ["a"])

    def test_a_query_that_is_not_text_is_refused(self, reranker: providers.Reranker) -> None:
        with pytest.raises(TypeError, match="query must be a string, got list"):
            reranker.rerank(["q"], ["a"])  # type: ignore[arg-type]

    @pytest.mark.parametrize("top_n", [0, -1, 1.5, True], ids=str)
    def test_a_top_n_that_is_not_a_positive_integer_is_refused(
        self, reranker: providers.Reranker, top_n: object
    ) -> None:
        with pytest.raises(ValueError, match="top_n must be a positive integer"):
            reranker.rerank("q", ["a"], top_n=top_n)  # type: ignore[arg-type]

    def test_nothing_is_sent_when_an_input_is_refused(
        self, api: respx.Route, reranker: providers.Reranker
    ) -> None:
        with pytest.raises(ValueError):
            reranker.rerank("q", ["a", ""])

        assert api.call_count == 0


class TestFailures:
    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (401, "auth"),
            (403, "auth"),
            (400, "invalid_request"),
            (404, "invalid_request"),
            (422, "invalid_request"),
            (429, "rate_limited"),
            (500, "unavailable"),
        ],
    )
    def test_a_refusal_is_reported_by_category_and_status(self, status: int, category: str) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(status)
            with providers.reranker("cohere", model="m", api_key=KEY, retry=NoRetry()) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.rerank("q", ["a"])

        assert (caught.value.category, caught.value.status) == (category, status)

    def test_a_passing_failure_is_retried(self, reranker: providers.Reranker) -> None:
        with respx.mock() as mock:
            answer = {"results": [{"index": 0, "relevance_score": 0.5}]}
            route = mock.post(URL).mock(
                side_effect=[httpx.Response(503), httpx.Response(200, json=answer)]
            )
            result = reranker.rerank("q", ["a"])

        assert (route.call_count, positions(result)) == (2, [0])

    def test_a_request_that_got_no_answer_is_a_connection_failure(
        self, reranker: providers.Reranker
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(providers.ProviderError) as caught:
                reranker.rerank("q", ["a"])

        assert (caught.value.category, caught.value.__cause__) == ("connection", None)

    @pytest.mark.parametrize(
        "results",
        [
            [{"index": 5, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}],
            [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}],
            [{"index": 0, "relevance_score": 0.5}],
        ],
        ids=["a-position-not-sent", "the-same-position-twice", "a-document-left-out"],
    )
    def test_an_answer_that_does_not_match_the_documents_is_a_bad_response(
        self, reranker: providers.Reranker, results: list[dict[str, object]]
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(200, json={"results": results})
            with pytest.raises(providers.ProviderError) as caught:
                reranker.rerank("q", ["a", "b"])

        assert caught.value.category == "bad_response"

    def test_an_answer_that_is_not_json_is_a_bad_response(
        self, reranker: providers.Reranker
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(200, text="PRIVATE-REPLY not json")
            with pytest.raises(providers.ProviderError) as caught:
                reranker.rerank("q", ["a"])

        assert (caught.value.category, caught.value.__context__) == ("bad_response", None)

    def test_a_bad_answer_stops_the_call_before_the_next_request(self) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).respond(200, json={"results": []})
            with providers.reranker("cohere", model="m", api_key=KEY, max_documents=1) as made:
                with pytest.raises(providers.ProviderError):
                    made.rerank("q", ["a", "b", "c"])

        assert route.call_count == 1


class TestNothingLeaks:
    @pytest.mark.parametrize("status", [400, 401, 429, 500])
    def test_an_error_carries_neither_the_key_nor_the_reply_nor_the_texts(
        self, status: int
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(status, text=f"PRIVATE-REPLY {MARKER} SECRET-QUERY")
            with providers.reranker("cohere", model="m", api_key=MARKER, retry=NoRetry()) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.rerank("SECRET-QUERY", ["SECRET-DOCUMENT"])

        shown = f"{caught.value!s}{caught.value!r}"
        assert not any(
            secret in shown for secret in (MARKER, "PRIVATE-REPLY", "SECRET", "cohere.com")
        )

    def test_the_reranker_s_repr_does_not_show_the_key(self) -> None:
        made = providers.reranker("cohere", model="m", api_key=MARKER)

        assert repr(made) == "Reranker(provider='cohere', model='m')"

    def test_the_settings_repr_does_not_show_the_key(self) -> None:
        made = providers.reranker("cohere", model="m", api_key=MARKER)

        assert MARKER not in repr(made._settings)

    def test_a_redirect_is_not_followed_even_by_a_client_set_to(self) -> None:
        elsewhere = "https://elsewhere.invalid/collect"
        with respx.mock(assert_all_called=False) as mock:
            mock.post(URL).respond(307, headers={"Location": elsewhere})
            other = mock.post(elsewhere).mock(side_effect=by_length)
            with httpx.Client(follow_redirects=True) as client:
                made = providers.reranker("cohere", model="m", api_key=MARKER, http_client=client)
                with pytest.raises(providers.ProviderError):
                    made.rerank("q", ["a"])

        assert other.call_count == 0

    def test_a_client_made_for_this_machine_ignores_the_environment(self) -> None:
        with respx.mock() as mock:
            mock.post("http://localhost:8080/rerank").mock(side_effect=by_length)
            with providers.reranker(
                "cohere", model="m", api_key=KEY, base_url="http://localhost:8080"
            ) as made:
                made.rerank("q", ["a"])

                assert made._sender._client is not None and made._sender._client.trust_env is False


class TestConfiguration:
    def test_the_key_is_read_from_the_row_s_environment_variable(
        self, api: respx.Route, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CO_API_KEY", MARKER)
        with providers.reranker("cohere", model="m") as made:
            made.rerank("q", ["a"])

        assert api.calls.last.request.headers["authorization"] == f"Bearer {MARKER}"

    def test_no_key_anywhere_is_refused_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CO_API_KEY", raising=False)

        with pytest.raises(ValueError, match="set CO_API_KEY"):
            providers.reranker("cohere", model="m")

    def test_a_key_the_http_layer_would_reject_is_refused_first(self) -> None:
        with pytest.raises(ValueError, match="the API key contains"):
            providers.reranker("cohere", model="m", api_key=f"{MARKER}\nmore")

    def test_a_provider_that_routes_reranking_is_reached_at_its_own_address(self) -> None:
        with respx.mock() as mock:
            route = mock.post("https://api.mixroute.ai/v1/rerank").mock(side_effect=by_length)
            with providers.reranker("mixroute", model="rerank-v4.0-fast", api_key=KEY) as made:
                made.rerank("q", ["a"])

        assert route.call_count == 1

    def test_base_url_replaces_the_provider_s_own_address(self) -> None:
        with respx.mock() as mock:
            route = mock.post("https://proxy.example.com/co/rerank").mock(side_effect=by_length)
            with providers.reranker(
                "cohere", model="m", api_key=KEY, base_url="https://proxy.example.com/co/"
            ) as made:
                made.rerank("q", ["a"])

        assert route.call_count == 1

    def test_a_compatible_server_is_not_sent_the_format_owner_s_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CO_API_KEY", MARKER)
        with respx.mock() as mock:
            route = mock.post("https://rerank.internal.example/rerank").mock(side_effect=by_length)
            with providers.reranker(
                "cohere_compatible", model="m", base_url="https://rerank.internal.example"
            ) as made:
                made.rerank("q", ["a"])

        assert "authorization" not in route.calls.last.request.headers

    def test_a_compatible_server_without_an_address_is_refused(self) -> None:
        with pytest.raises(ValueError, match="has no address of its own; pass base_url="):
            providers.reranker("cohere_compatible", model="m")

    def test_an_address_that_is_not_private_is_refused(self) -> None:
        with pytest.raises(ValueError, match="base_url must use https"):
            providers.reranker("cohere", model="m", api_key=KEY, base_url="http://example.com")

    def test_a_provider_with_no_reranking_is_refused_with_the_ones_that_have_it(self) -> None:
        with pytest.raises(
            ValueError,
            match="no provider 'openai' for reranking; choose one of: cohere, cohere_compatible",
        ):
            providers.reranker("openai", model="m", api_key=KEY)

    def test_a_model_that_is_not_a_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            providers.reranker("cohere", model="", api_key=KEY)

    @pytest.mark.parametrize("limit", [0, -1, 1.5, True], ids=str)
    def test_a_limit_that_is_not_a_positive_integer_is_refused(self, limit: object) -> None:
        with pytest.raises(ValueError, match="max_documents must be a positive integer"):
            providers.reranker("cohere", model="m", api_key=KEY, max_documents=limit)  # type: ignore[arg-type]

    def test_a_timeout_that_is_not_positive_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timeout must be a positive number"):
            providers.reranker("cohere", model="m", api_key=KEY, timeout=0)

    def test_an_async_client_is_refused_by_the_sync_factory(self) -> None:
        with pytest.raises(TypeError, match="use areranker"):
            providers.reranker("cohere", model="m", api_key=KEY, http_client=httpx.AsyncClient())  # type: ignore[arg-type]

    def test_a_sync_client_is_refused_by_the_async_factory(self) -> None:
        with pytest.raises(TypeError, match="use reranker"):
            providers.areranker("cohere", model="m", api_key=KEY, http_client=httpx.Client())  # type: ignore[arg-type]

    def test_a_client_passed_in_is_not_closed(self, api: respx.Route) -> None:
        with httpx.Client() as client:
            with providers.reranker("cohere", model="m", api_key=KEY, http_client=client) as made:
                made.rerank("q", ["a"])

            assert client.is_closed is False

    def test_closing_twice_is_harmless(self, api: respx.Route) -> None:
        made = providers.reranker("cohere", model="m", api_key=KEY)
        made.rerank("q", ["a"])
        made.close()

        made.close()

        assert made._sender._client is None


class TestAsync:
    async def test_documents_come_back_by_position_best_first(self, api: respx.Route) -> None:
        async with providers.areranker("cohere", model="m", api_key=KEY) as made:
            result = await made.rerank("q", ["aa", "aaaa", "a"])

        assert positions(result) == [1, 0, 2]

    async def test_several_requests_are_merged_and_cut(self, api: respx.Route) -> None:
        async with providers.areranker("cohere", model="m", api_key=KEY, max_documents=2) as made:
            result = await made.rerank("q", ["aa", "a", "aaaaa", "aaa"], top_n=3)

        assert positions(result) == [2, 3, 0]

    async def test_a_refusal_is_reported_by_category(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(401)
            async with providers.areranker("cohere", model="m", api_key=KEY) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    await made.rerank("q", ["a"])

        assert caught.value.category == "auth"

    async def test_a_client_passed_in_is_used_and_not_closed(self, api: respx.Route) -> None:
        async with httpx.AsyncClient() as client:
            async with providers.areranker(
                "cohere", model="m", api_key=KEY, http_client=client
            ) as made:
                await made.rerank("q", ["a"])

            assert client.is_closed is False

    async def test_closing_twice_is_harmless(self, api: respx.Route) -> None:
        made = providers.areranker("cohere", model="m", api_key=KEY)
        await made.rerank("q", ["a"])
        await made.aclose()

        await made.aclose()

        assert made._sender._client is None

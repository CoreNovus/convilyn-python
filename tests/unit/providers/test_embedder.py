"""`providers.embedder` / `providers.aembedder` against a mocked HTTP layer.

No test here reaches a network: every request is answered by respx.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator

import httpx
import pytest
import respx

from convilyn import providers, rag
from convilyn._internal.resilience import NoRetry

URL = "https://api.openai.com/v1/embeddings"
KEY = "sk-test-0123456789abcdef"  # pragma: allowlist secret


class Instant:
    """Retries like the default policy, without waiting."""

    def __init__(self, attempts: int = 3) -> None:
        self.attempts = attempts

    def should_retry(
        self, response: httpx.Response | None, exc: BaseException | None, attempt: int
    ) -> bool:
        return (
            attempt < self.attempts
            and response is not None
            and response.status_code in (429, 500, 503)
        )

    def next_delay(self, response: httpx.Response | None, attempt: int) -> float:
        return 0.0


class RetryingEverything(Instant):
    def should_retry(
        self, response: httpx.Response | None, exc: BaseException | None, attempt: int
    ) -> bool:
        return attempt < self.attempts


def answer(request: httpx.Request) -> httpx.Response:
    """Answer like the service: one vector per text, [position, length of text]."""
    texts = json.loads(request.content)["input"]
    data = [
        {"object": "embedding", "index": index, "embedding": [float(index + 1), float(len(text))]}
        for index, text in enumerate(texts)
    ]
    return httpx.Response(
        200, json={"data": data, "model": "served-model", "usage": {"total_tokens": len(texts)}}
    )


def bodies(route: respx.Route) -> list[dict[str, object]]:
    return [json.loads(call.request.content) for call in route.calls]


@pytest.fixture
def api() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL).mock(side_effect=answer)


@pytest.fixture
def embedder() -> Iterator[providers.Embedder]:
    with providers.embedder("openai", model="m", api_key=KEY, retry=Instant()) as made:
        yield made


class TestEmbed:
    def test_each_text_gets_a_unit_length_vector(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        result = embedder.embed(["abc", "abcd"])

        assert [round(math.hypot(*vector), 9) for vector in result.vectors] == [1.0, 1.0]

    def test_vectors_are_in_the_order_of_the_texts(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        result = embedder.embed(["a", "bbbbbbbbb"])

        # The mock's second component is the text's length, so the longer text
        # has the vector that leans further towards it.
        assert result.vectors[0][1] < result.vectors[1][1]

    def test_the_result_names_the_model_the_provider_used(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        assert embedder.embed(["a"]).model == "served-model"

    def test_the_result_counts_the_tokens_the_provider_reported(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        assert embedder.embed(["a", "b", "c"]).tokens == 3

    def test_the_request_carries_the_model_the_texts_and_the_format(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a", "b"])

        assert bodies(api) == [{"model": "m", "input": ["a", "b"], "encoding_format": "float"}]

    def test_the_key_is_sent_as_a_bearer_token(
        self, api: respx.Route, embedder: providers.Embedder
    ) -> None:
        embedder.embed(["a"])

        assert api.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    def test_dimensions_are_sent_and_checked(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, dimensions=2) as embedder:
            embedder.embed(["a"])

        assert bodies(api)[0]["dimensions"] == 2

    def test_a_model_without_a_reported_name_keeps_the_requested_one(self) -> None:
        data = [{"index": 0, "embedding": [1.0]}]
        with respx.mock() as mock:
            mock.post(URL).respond(200, json={"data": data})
            with providers.embedder("openai", model="asked", api_key=KEY) as embedder:
                result = embedder.embed(["a"])

        assert (result.model, result.tokens) == ("asked", None)


class TestBatching:
    def test_texts_are_split_at_the_limit_per_request(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, max_inputs=2) as embedder:
            embedder.embed(["a", "b", "c", "d", "e"])

        assert [len(body["input"]) for body in bodies(api)] == [2, 2, 1]  # type: ignore[arg-type]

    def test_the_row_s_limit_applies_when_none_is_passed(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY) as embedder:
            embedder.embed(["t"] * 2049)

        assert [len(body["input"]) for body in bodies(api)] == [2048, 1]  # type: ignore[arg-type]

    def test_results_of_several_requests_are_joined_in_order(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, max_inputs=2) as embedder:
            result = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

        lengths = [
            round(vector[1] / vector[0] * (index % 2 + 1))
            for index, vector in enumerate(result.vectors)
        ]
        assert lengths == [1, 2, 3, 4, 5]

    def test_tokens_are_summed_across_requests(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, max_inputs=2) as embedder:
            assert embedder.embed(["a", "b", "c"]).tokens == 3

    def test_a_request_that_reports_no_tokens_makes_the_total_unknown(self) -> None:
        replies = [
            httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
            ),
            httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}),
            httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
            ),
        ]
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=replies)
            with providers.embedder("openai", model="m", api_key=KEY, max_inputs=1) as embedder:
                result = embedder.embed(["a", "b", "c"])

        assert result.tokens is None


class TestKinds:
    def test_a_kind_aware_model_is_told_documents_from_queries(self) -> None:
        with respx.mock() as mock:
            route = mock.post("https://api.mixroute.ai/v1/embeddings").mock(side_effect=answer)
            with providers.embedder("mixroute", model="embed-v4.0", api_key=KEY) as embedder:
                embedder.embed(["a"])
                embedder.embed(["a"], kind="query")

        assert [body["input_type"] for body in bodies(route)] == ["search_document", "search_query"]

    def test_another_model_of_the_same_provider_is_not_sent_the_kind(self) -> None:
        with respx.mock() as mock:
            route = mock.post("https://api.mixroute.ai/v1/embeddings").mock(side_effect=answer)
            with providers.embedder(
                "mixroute", model="text-embedding-3-small", api_key=KEY
            ) as embedder:
                embedder.embed(["a"], kind="query")

        assert "input_type" not in bodies(route)[0]

    def test_an_unknown_kind_is_refused(self, embedder: providers.Embedder) -> None:
        with pytest.raises(ValueError, match='kind must be "document" or "query"'):
            embedder.embed(["a"], kind="passage")  # type: ignore[arg-type]


class TestOverflow:
    def test_with_a_measure_a_long_text_is_refused_before_any_request(
        self, api: respx.Route
    ) -> None:
        with (
            providers.embedder(
                "openai", model="m", api_key=KEY, measure=len, max_input_tokens=3
            ) as embedder,
            pytest.raises(ValueError, match="text 1 measures 5 tokens"),
        ):
            embedder.embed(["ab", "abcde"])

        assert api.call_count == 0

    def test_truncate_sends_the_beginning_of_a_long_text(self, api: respx.Route) -> None:
        with providers.embedder(
            "openai",
            model="m",
            api_key=KEY,
            measure=len,
            max_input_tokens=3,
            on_overflow="truncate",
        ) as embedder:
            embedder.embed(["ab", "abcde"])

        assert bodies(api)[0]["input"] == ["ab", "abc"]

    def test_truncate_without_a_measure_counts_with_the_row_s_tokenizer(
        self, api: respx.Route
    ) -> None:
        text = "refund " * 200
        with providers.embedder(
            "openai", model="m", api_key=KEY, max_input_tokens=10, on_overflow="truncate"
        ) as embedder:
            embedder.embed([text])

        (sent,) = bodies(api)[0]["input"]  # type: ignore[misc]
        assert rag.token_estimator("cl100k")(sent) == 10

    def test_without_a_measure_a_long_text_is_left_to_the_provider(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, max_input_tokens=3) as embedder:
            embedder.embed(["a" * 1000])

        assert bodies(api)[0]["input"] == ["a" * 1000]

    def test_the_row_s_token_limit_applies_when_none_is_passed(self, api: respx.Route) -> None:
        with (
            providers.embedder("openai", model="m", api_key=KEY, measure=len) as embedder,
            pytest.raises(ValueError, match="the limit is 8192"),
        ):
            embedder.embed(["a" * 8193])


class TestRetries:
    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_a_passing_failure_is_retried(self, embedder: providers.Embedder, status: int) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=[httpx.Response(status), answer])
            route.side_effect = [
                httpx.Response(status),
                httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}),
            ]
            embedder.embed(["a"])

        assert route.call_count == 2

    def test_a_refusal_is_not_retried(self, embedder: providers.Embedder) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).respond(400)
            with pytest.raises(providers.ProviderError):
                embedder.embed(["a"])

        assert route.call_count == 1

    def test_retries_stop_at_the_policy_s_count(self) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).respond(503)
            with providers.embedder("openai", model="m", api_key=KEY, retry=Instant(2)) as embedder:
                with pytest.raises(providers.ProviderError):
                    embedder.embed(["a"])

        assert route.call_count == 3

    def test_a_policy_may_retry_a_request_that_got_no_answer(self) -> None:
        reply = httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=[httpx.ConnectError("down"), reply])
            with providers.embedder(
                "openai", model="m", api_key=KEY, retry=RetryingEverything()
            ) as embedder:
                embedder.embed(["a"])

        assert route.call_count == 2

    def test_the_default_policy_waits_between_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waits: list[float] = []
        monkeypatch.setattr("convilyn.providers._transport.time.sleep", waits.append)
        reply = httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        with respx.mock() as mock:
            mock.post(URL).mock(
                side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), reply]
            )
            with providers.embedder("openai", model="m", api_key=KEY) as embedder:
                embedder.embed(["a"])

        assert waits == [2.0]


class TestFailures:
    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (400, "invalid_request"),
            (401, "auth"),
            (403, "auth"),
            (404, "invalid_request"),
            (429, "rate_limited"),
            (500, "unavailable"),
            (504, "unavailable"),
        ],
    )
    def test_a_refusal_is_reported_by_category_and_status(self, status: int, category: str) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(status)
            with providers.embedder("openai", model="m", api_key=KEY, retry=NoRetry()) as embedder:
                with pytest.raises(providers.ProviderError) as caught:
                    embedder.embed(["a"])

        assert (caught.value.category, caught.value.status) == (category, status)

    def test_a_request_that_got_no_answer_is_a_connection_failure(
        self, embedder: providers.Embedder
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(providers.ProviderError) as caught:
                embedder.embed(["a"])

        assert (caught.value.category, caught.value.status) == ("connection", None)

    def test_a_connection_failure_does_not_carry_the_request(
        self, embedder: providers.Embedder
    ) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(providers.ProviderError) as caught:
                embedder.embed(["a"])

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

    def test_a_timeout_is_a_connection_failure(self, embedder: providers.Embedder) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
            with pytest.raises(providers.ProviderError) as caught:
                embedder.embed(["a"])

        assert caught.value.category == "connection"

    @pytest.mark.parametrize(
        "reply",
        [
            httpx.Response(200, text="<html>gateway</html>"),
            httpx.Response(200, json={"data": []}),
            httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0, 0.0]}]}),
            httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]}),
        ],
        ids=["not-json", "no-vectors", "zero-vector", "wrong-dimensions"],
    )
    def test_an_unusable_answer_is_a_bad_response(self, reply: httpx.Response) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(return_value=reply)
            with providers.embedder("openai", model="m", api_key=KEY, dimensions=2) as embedder:
                with pytest.raises(providers.ProviderError) as caught:
                    embedder.embed(["a"])

        assert caught.value.category == "bad_response"

    def test_vectors_of_different_sizes_in_one_answer_are_a_bad_response(
        self, embedder: providers.Embedder
    ) -> None:
        data = [{"index": 0, "embedding": [1.0, 2.0]}, {"index": 1, "embedding": [1.0]}]
        with respx.mock() as mock:
            mock.post(URL).respond(200, json={"data": data})
            with pytest.raises(providers.ProviderError) as caught:
                embedder.embed(["a", "b"])

        assert caught.value.category == "bad_response"

    def test_a_provider_error_is_a_convilyn_error(self, embedder: providers.Embedder) -> None:
        from convilyn import ConvilynError

        with respx.mock() as mock:
            mock.post(URL).respond(401)
            with pytest.raises(ConvilynError):
                embedder.embed(["a"])


class TestNothingLeaks:
    """The key, the text sent and the provider's reply stay out of what is raised."""

    @pytest.mark.parametrize("status", [400, 401, 429, 500])
    def test_an_error_carries_neither_the_key_nor_the_reply_nor_the_text(self, status: int) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(
                status, json={"error": {"message": f"bad key {KEY} for PRIVATE-BODY"}}
            )
            with providers.embedder("openai", model="m", api_key=KEY, retry=NoRetry()) as embedder:
                with pytest.raises(providers.ProviderError) as caught:
                    embedder.embed(["CUSTOMER-TEXT"])

        shown = f"{caught.value!s} {caught.value!r} {caught.value.args}"
        assert [
            word
            for word in (KEY, "PRIVATE-BODY", "CUSTOMER-TEXT", "api.openai.com")
            if word in shown
        ] == []

    def test_the_embedder_s_repr_does_not_show_the_key(self, embedder: providers.Embedder) -> None:
        assert repr(embedder) == "Embedder(provider='openai', model='m')"

    def test_the_settings_repr_does_not_show_the_key(self, embedder: providers.Embedder) -> None:
        assert KEY not in repr(embedder._settings)

    def test_a_redirect_is_not_followed_with_the_key(self, embedder: providers.Embedder) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(URL).respond(
                307, headers={"Location": "https://elsewhere.invalid/v1/embeddings"}
            )
            elsewhere = mock.post("https://elsewhere.invalid/v1/embeddings").respond(200)
            with pytest.raises(providers.ProviderError):
                embedder.embed(["a"])

        assert elsewhere.call_count == 0


class TestConfiguration:
    def test_the_key_is_read_from_the_row_s_environment_variable(
        self, api: respx.Route, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        with providers.embedder("openai", model="m") as embedder:
            embedder.embed(["a"])

        assert api.calls.last.request.headers["authorization"] == "Bearer sk-from-env"

    def test_a_key_argument_outranks_the_environment(
        self, api: respx.Route, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        with providers.embedder("openai", model="m", api_key=KEY) as embedder:
            embedder.embed(["a"])

        assert api.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    def test_no_key_anywhere_is_refused_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(ValueError, match="pass api_key=, set OPENAI_API_KEY"):
            providers.embedder("openai", model="m")

    def test_an_empty_key_in_the_environment_counts_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "")

        with pytest.raises(ValueError, match="no API key"):
            providers.embedder("openai", model="m")

    def test_a_client_that_authenticates_itself_needs_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=answer)
            with httpx.Client(headers={"X-Signed": "yes"}) as client:
                providers.embedder("openai", model="m", http_client=client).embed(["a"])

        request = route.calls.last.request
        assert (request.headers["x-signed"], "authorization" in request.headers) == ("yes", False)

    def test_a_client_passed_in_is_not_closed(self, api: respx.Route) -> None:
        with httpx.Client() as client:
            with providers.embedder(
                "openai", model="m", api_key=KEY, http_client=client
            ) as embedder:
                embedder.embed(["a"])

            assert client.is_closed is False

    def test_closing_twice_is_harmless(self, api: respx.Route) -> None:
        embedder = providers.embedder("openai", model="m", api_key=KEY)
        embedder.embed(["a"])
        embedder.close()

        embedder.close()

    def test_an_embedder_can_be_used_again_after_closing(self, api: respx.Route) -> None:
        embedder = providers.embedder("openai", model="m", api_key=KEY)
        embedder.embed(["a"])
        embedder.close()

        assert len(embedder.embed(["a"]).vectors) == 1
        embedder.close()

    def test_a_compatible_server_is_reached_at_the_address_given(self) -> None:
        with respx.mock() as mock:
            route = mock.post("http://localhost:8080/v1/embeddings").mock(side_effect=answer)
            with providers.embedder(
                "openai_compatible", model="m", base_url="http://localhost:8080/v1/"
            ) as embedder:
                embedder.embed(["a"])

        assert (route.call_count, "authorization" in route.calls.last.request.headers) == (1, False)

    def test_a_compatible_server_without_an_address_is_refused(self) -> None:
        with pytest.raises(ValueError, match="pass base_url="):
            providers.embedder("openai_compatible", model="m")

    def test_base_url_replaces_a_provider_s_own_address(self) -> None:
        with respx.mock() as mock:
            route = mock.post("https://proxy.example.com/openai/embeddings").mock(
                side_effect=answer
            )
            with providers.embedder(
                "openai", model="m", api_key=KEY, base_url="https://proxy.example.com/openai"
            ) as embedder:
                embedder.embed(["a"])

        assert route.call_count == 1

    @pytest.mark.parametrize(
        "base_url",
        ["http://api.example.com/v1", "ftp://localhost/v1", "api.example.com/v1", "file:///etc"],
        ids=["plain-http", "ftp-loopback", "no-scheme", "file"],
    )
    def test_an_address_that_is_not_private_is_refused(self, base_url: str) -> None:
        with pytest.raises(ValueError, match="base_url must use https"):
            providers.embedder("openai_compatible", model="m", base_url=base_url)

    def test_a_refused_address_is_not_echoed(self) -> None:
        marker = "PLACEHOLDER-IN-ADDRESS"
        address = "http://" + "@".join([f"user:{marker}", "example.com/v1"])

        with pytest.raises(ValueError) as caught:
            providers.embedder("openai_compatible", model="m", base_url=address)

        assert marker not in str(caught.value)

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
    def test_plain_http_is_accepted_for_this_machine(self, host: str) -> None:
        embedder = providers.embedder(
            "openai_compatible", model="m", base_url=f"http://{host}:1/v1"
        )

        assert embedder.model == "m"

    def test_an_unknown_provider_is_refused_with_the_choices(self) -> None:
        with pytest.raises(
            ValueError,
            match=(
                "no provider 'acme' for embeddings; "
                "choose one of: gemini, mixroute, openai, openai_compatible"
            ),
        ):
            providers.embedder("acme", model="m")

    def test_a_very_long_provider_name_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            providers.embedder("x" * 100_000, model="m")

        assert len(str(caught.value)) < 300

    @pytest.mark.parametrize("model", ["", None, 3])
    def test_a_model_that_is_not_a_name_is_refused(self, model: object) -> None:
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            providers.embedder("openai", model=model, api_key=KEY)  # type: ignore[arg-type]

    @pytest.mark.parametrize("option", ["dimensions", "max_inputs", "max_input_tokens"])
    @pytest.mark.parametrize("value", [0, -1, 1.5, True, "8"])
    def test_a_limit_that_is_not_a_positive_integer_is_refused(
        self, option: str, value: object
    ) -> None:
        with pytest.raises(ValueError, match=f"{option} must be a positive integer"):
            providers.embedder("openai", model="m", api_key=KEY, **{option: value})  # type: ignore[arg-type]

    def test_an_unknown_overflow_policy_is_refused(self) -> None:
        with pytest.raises(ValueError, match='on_overflow must be "error" or "truncate"'):
            providers.embedder("openai", model="m", api_key=KEY, on_overflow="drop")  # type: ignore[arg-type]

    @pytest.mark.parametrize("timeout", [0, -1.0, float("nan"), float("inf")])
    def test_a_timeout_that_is_not_positive_is_refused(self, timeout: float) -> None:
        with pytest.raises(ValueError, match="timeout must be a positive number"):
            providers.embedder("openai", model="m", api_key=KEY, timeout=timeout)

    def test_an_async_client_is_refused_by_the_sync_factory(self) -> None:
        with pytest.raises(TypeError, match="use aembedder"):
            providers.embedder("openai", model="m", api_key=KEY, http_client=httpx.AsyncClient())  # type: ignore[arg-type]

    def test_the_embedder_reports_its_provider_row(self, embedder: providers.Embedder) -> None:
        assert embedder.info.name == "openai"

    def test_the_timeout_is_given_to_the_client_made_here(self, api: respx.Route) -> None:
        with providers.embedder("openai", model="m", api_key=KEY, timeout=7.5) as embedder:
            embedder.embed(["a"])

            assert (
                embedder._sender._client is not None
                and embedder._sender._client.timeout == httpx.Timeout(7.5)
            )


class TestAsync:
    async def test_the_async_embedder_returns_what_the_sync_one_does(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=answer)
            with providers.embedder("openai", model="m", api_key=KEY, max_inputs=2) as sync:
                expected = sync.embed(["a", "bb", "ccc"], kind="query")
            async with providers.aembedder("openai", model="m", api_key=KEY, max_inputs=2) as made:
                result = await made.embed(["a", "bb", "ccc"], kind="query")

        assert result == expected

    async def test_a_passing_failure_is_retried(self) -> None:
        reply = httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=[httpx.Response(503), reply])
            async with providers.aembedder(
                "openai", model="m", api_key=KEY, retry=Instant()
            ) as made:
                await made.embed(["a"])

        assert route.call_count == 2

    async def test_a_refusal_is_reported_by_category(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(401)
            async with providers.aembedder("openai", model="m", api_key=KEY) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    await made.embed(["a"])

        assert caught.value.category == "auth"

    async def test_a_request_that_got_no_answer_is_a_connection_failure(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=httpx.ConnectError("refused"))
            async with providers.aembedder("openai", model="m", api_key=KEY) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    await made.embed(["a"])

        assert caught.value.category == "connection"

    async def test_a_client_passed_in_is_used_and_not_closed(self) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=answer)
            async with httpx.AsyncClient(headers={"X-Signed": "yes"}) as client:
                async with providers.aembedder(
                    "openai", model="m", api_key=KEY, http_client=client
                ) as made:
                    await made.embed(["a"])
                still_open = not client.is_closed

        assert (route.calls.last.request.headers["x-signed"], still_open) == ("yes", True)

    async def test_closing_twice_is_harmless(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=answer)
            made = providers.aembedder("openai", model="m", api_key=KEY)
            await made.embed(["a"])
            await made.aclose()

            await made.aclose()

    async def test_a_sync_client_is_refused_by_the_async_factory(self) -> None:
        with pytest.raises(TypeError, match="use embedder"):
            providers.aembedder("openai", model="m", api_key=KEY, http_client=httpx.Client())  # type: ignore[arg-type]

    async def test_the_async_embedder_s_repr_does_not_show_the_key(self) -> None:
        made = providers.aembedder("openai", model="m", api_key=KEY)

        assert repr(made) == "AsyncEmbedder(provider='openai', model='m')"

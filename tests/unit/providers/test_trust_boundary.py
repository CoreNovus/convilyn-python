"""What must hold when text and a key leave this machine.

The key is the developer's, the texts are their users', and the answer comes
from a server nobody here controls. Each test below is one way that could go
wrong. No test reaches a network.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from convilyn import providers
from convilyn.providers import _steps as steps
from convilyn.providers._wires import openai_embeddings as wire

URL = "https://api.openai.com/v1/embeddings"
MARKER = "KEYMARKER0123456789"
ONE_VECTOR = {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}


def one_vector(request: httpx.Request) -> httpx.Response:
    count = len(json.loads(request.content)["input"])
    data = [{"index": index, "embedding": [1.0, 0.0]} for index in range(count)]
    return httpx.Response(200, json={"data": data})


class TestTheKey:
    @pytest.mark.parametrize(
        "padding", ["\n", "\r\n", "  ", "\t"], ids=["lf", "crlf", "spaces", "tab"]
    )
    def test_whitespace_around_a_key_is_dropped(self, padding: str) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=one_vector)
            with providers.embedder(
                "openai", model="m", api_key=f"{padding}{MARKER}{padding}"
            ) as made:
                made.embed(["a"])

        assert route.calls.last.request.headers["authorization"] == f"Bearer {MARKER}"

    @pytest.mark.parametrize(
        "broken",
        [f"{MARKER}\nmore", f"{MARKER}\x00", f"{MARKER} more", f"{MARKER}é", f"{MARKER}\x7f"],
        ids=["newline-inside", "nul", "space-inside", "non-ascii", "delete"],
    )
    def test_a_key_the_http_layer_would_reject_is_refused_first(self, broken: str) -> None:
        with pytest.raises(ValueError, match="the API key contains"):
            providers.embedder("openai", model="m", api_key=broken)

    def test_a_refused_key_is_not_shown(self) -> None:
        with pytest.raises(ValueError) as caught:
            providers.embedder("openai", model="m", api_key=f"{MARKER}\nmore")

        assert MARKER not in f"{caught.value!s}{caught.value!r}"

    def test_a_key_from_the_environment_is_checked_the_same_way(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", f"{MARKER} more")

        with pytest.raises(ValueError, match="the API key contains"):
            providers.embedder("openai", model="m")

    def test_a_blank_key_argument_falls_back_to_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", MARKER)
        with respx.mock() as mock:
            route = mock.post(URL).mock(side_effect=one_vector)
            with providers.embedder("openai", model="m", api_key=" \n") as made:
                made.embed(["a"])

        assert route.calls.last.request.headers["authorization"] == f"Bearer {MARKER}"

    def test_a_key_of_only_whitespace_counts_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(ValueError, match="no API key"):
            providers.embedder("openai", model="m", api_key=" \n")


class TestRedirects:
    def test_a_client_that_follows_redirects_is_not_allowed_to_here(self) -> None:
        elsewhere = "https://elsewhere.invalid/collect"
        with respx.mock(assert_all_called=False) as mock:
            mock.post(URL).respond(307, headers={"Location": elsewhere})
            other = mock.post(elsewhere).mock(side_effect=one_vector)
            with httpx.Client(follow_redirects=True) as client:
                made = providers.embedder("openai", model="m", api_key=MARKER, http_client=client)
                with pytest.raises(providers.ProviderError):
                    made.embed(["a"])

        assert other.call_count == 0

    async def test_an_async_client_that_follows_redirects_is_not_allowed_to_either(self) -> None:
        elsewhere = "https://elsewhere.invalid/collect"
        with respx.mock(assert_all_called=False) as mock:
            mock.post(URL).respond(307, headers={"Location": elsewhere})
            other = mock.post(elsewhere).mock(side_effect=one_vector)
            async with httpx.AsyncClient(follow_redirects=True) as client:
                made = providers.aembedder("openai", model="m", api_key=MARKER, http_client=client)
                with pytest.raises(providers.ProviderError):
                    await made.embed(["a"])

        assert other.call_count == 0

    def test_a_redirect_is_reported_as_a_rejected_request(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(307, headers={"Location": "https://elsewhere.invalid/"})
            with providers.embedder("openai", model="m", api_key=MARKER) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.embed(["a"])

        assert (caught.value.category, caught.value.status) == ("invalid_request", 307)


class TestEnvironmentProxies:
    """Plain http is only ever this machine, so nothing in between may be handed it."""

    def test_a_client_made_for_this_machine_ignores_the_environment(self) -> None:
        with respx.mock() as mock:
            mock.post("http://localhost:8080/v1/embeddings").mock(side_effect=one_vector)
            with providers.embedder(
                "openai_compatible", model="m", base_url="http://localhost:8080/v1"
            ) as made:
                made.embed(["a"])

                assert made._sender._client is not None and made._sender._client.trust_env is False

    def test_a_client_made_for_an_encrypted_address_may_use_it(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).mock(side_effect=one_vector)
            with providers.embedder("openai", model="m", api_key=MARKER) as made:
                made.embed(["a"])

                assert made._sender._client is not None and made._sender._client.trust_env is True

    async def test_the_async_client_follows_the_same_rule(self) -> None:
        with respx.mock() as mock:
            mock.post("http://127.0.0.1:8080/v1/embeddings").mock(side_effect=one_vector)
            async with providers.aembedder(
                "openai_compatible", model="m", base_url="http://127.0.0.1:8080/v1"
            ) as made:
                await made.embed(["a"])

                assert made._sender._client is not None and made._sender._client.trust_env is False


class TestTheAddress:
    @pytest.mark.parametrize(
        "base_url",
        ["http://localhost:abc/v1", "https://exa\tmple.com/v1", "https://[::1/v1", 3],
        ids=["bad-port", "tab-in-host", "broken-bracket", "not-text"],
    )
    def test_an_address_the_http_layer_cannot_read_is_refused_when_configuring(
        self, base_url: object
    ) -> None:
        with pytest.raises(ValueError, match="base_url is not a valid address"):
            providers.embedder("openai_compatible", model="m", base_url=base_url)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://example.com/v1?key=1",
            "https://example.com/v1#part",
            "https://example.com/v1?",
            "https://example.com/v1#",
        ],
        ids=["query", "fragment", "empty-query", "empty-fragment"],
    )
    def test_an_address_with_a_query_or_a_fragment_is_refused(self, base_url: str) -> None:
        with pytest.raises(ValueError, match="must not contain a query or a fragment"):
            providers.embedder("openai_compatible", model="m", base_url=base_url)

    @pytest.mark.parametrize(
        "base_url",
        [
            "https:///v1",
            "http://localhost.example.com/v1",
            "http://127.0.0.1.example.com/v1",
            "http://localhost@example.com/v1",
            "http://0.0.0.0/v1",
            "HTTP://example.com/v1",
            "//example.com/v1",
        ],
        ids=[
            "https-without-host",
            "localhost-as-subdomain",
            "loopback-as-subdomain",
            "localhost-as-userinfo",
            "any-address",
            "upper-case-http",
            "no-scheme",
        ],
    )
    def test_an_address_that_only_looks_private_is_refused(self, base_url: str) -> None:
        with pytest.raises(ValueError, match="base_url must use https"):
            providers.embedder("openai_compatible", model="m", base_url=base_url)

    @pytest.mark.parametrize("userinfo", [f"user:{MARKER}", "user", f":{MARKER}"])
    def test_an_address_carrying_credentials_is_refused(self, userinfo: str) -> None:
        address = "https://" + "@".join([userinfo, "example.com/v1"])

        with pytest.raises(ValueError, match="must not contain a user name or password"):
            providers.embedder("openai_compatible", model="m", base_url=address)

    def test_refused_credentials_are_not_echoed(self) -> None:
        address = "https://" + "@".join([f"user:{MARKER}", "example.com/v1"])

        with pytest.raises(ValueError) as caught:
            providers.embedder("openai_compatible", model="m", base_url=address)

        assert MARKER not in f"{caught.value!s}{caught.value!r}"

    @pytest.mark.parametrize(
        "host",
        ["xn--ls8h.example.com", "xn--zz.example.com"],
        ids=["forbidden-character", "undecodable-label"],
    )
    def test_a_host_name_that_cannot_be_decoded_is_refused_like_any_unreadable_address(
        self, host: str
    ) -> None:
        with pytest.raises(ValueError, match="base_url is not a valid address"):
            providers.embedder("openai_compatible", model="m", base_url=f"https://{host}/v1")

    def test_the_decoding_error_is_not_kept_on_the_refusal(self) -> None:
        with pytest.raises(ValueError) as caught:
            providers.embedder(
                "openai_compatible", model="m", base_url="https://xn--zz.example.com/v1"
            )

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

    def test_an_address_object_is_refused_like_any_other_non_text(self) -> None:
        with pytest.raises(ValueError, match="base_url is not a valid address"):
            providers.embedder(
                "openai_compatible",
                model="m",
                base_url=httpx.URL("https://example.com/v1"),  # type: ignore[arg-type]
            )

    def test_an_invalid_address_is_not_echoed(self) -> None:
        with pytest.raises(ValueError) as caught:
            providers.embedder(
                "openai_compatible", model="m", base_url=f"http://user:{MARKER}@localhost:abc/v1"
            )

        assert MARKER not in f"{caught.value!s}{caught.value!r}"


class TestAHostileAnswer:
    """Whatever the server sends, the caller sees a ProviderError and nothing else."""

    @pytest.mark.parametrize(
        "content",
        [
            "[" * 100_000 + "]" * 100_000,
            '{"data": [{"index": 0, "embedding": [1' + "0" * 400 + ", 1]}]}",
            '{"data": [{"index": 0, "embedding": [1e999, 1]}]}',
            '{"data": [{"index": 0, "embedding": [NaN, 1]}]}',
            '{"data": [{"index": 0, "embedding": [1, NaN]}]}',
            '{"data": [{"index": 0, "embedding": [0.5, NaN, 1]}]}',
            '{"data": [{"index": 0, "embedding": [1, -Infinity]}]}',
        ],
        ids=[
            "nested-too-deep",
            "integer-too-large",
            "float-too-large",
            "not-a-number-first",
            "not-a-number-last",
            "not-a-number-in-the-middle",
            "infinity-last",
        ],
    )
    def test_it_is_a_bad_response(self, content: str) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(200, content=content.encode())
            with providers.embedder("openai", model="m", api_key=MARKER) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.embed(["a"])

        assert caught.value.category == "bad_response"

    def test_a_bad_answer_stops_the_requests_that_would_have_followed(self) -> None:
        with respx.mock() as mock:
            route = mock.post(URL).respond(200, json={"data": []})
            with providers.embedder("openai", model="m", api_key=MARKER, max_inputs=1) as made:
                with pytest.raises(providers.ProviderError):
                    made.embed(["a", "b", "c"])

        assert route.call_count == 1

    def test_the_reply_is_not_kept_on_the_error(self) -> None:
        with respx.mock() as mock:
            mock.post(URL).respond(200, text="PRIVATE-REPLY not json")
            with providers.embedder("openai", model="m", api_key=MARKER) as made:
                with pytest.raises(providers.ProviderError) as caught:
                    made.embed(["a"])

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

    @pytest.mark.parametrize("tokens", [-1, 2**60, 10**300, 1.5, True], ids=str)
    def test_a_token_count_that_cannot_be_one_is_not_believed(self, tokens: object) -> None:
        payload = {**ONE_VECTOR, "usage": {"total_tokens": tokens}}

        assert wire.read(payload, expected=1)[2] is None

    def test_a_model_name_too_long_to_be_one_is_not_believed(self) -> None:
        payload = {**ONE_VECTOR, "model": "m" * 100_000}

        assert wire.read(payload, expected=1)[1] is None


class TestVectorsOfAnyScale:
    @pytest.mark.parametrize("scale", [1e-200, 1e200], ids=["tiny", "huge"])
    def test_a_legitimate_vector_is_scaled_whatever_its_magnitude(self, scale: float) -> None:
        assert steps.unit([3 * scale, 4 * scale]) == pytest.approx((0.6, 0.8))

    def test_an_integer_too_large_for_a_float_is_a_bad_response(self) -> None:
        with pytest.raises(providers.ProviderError):
            steps.unit([10**400, 1])

    def test_the_conversion_error_is_not_kept_on_the_error(self) -> None:
        with pytest.raises(providers.ProviderError) as caught:
            steps.unit([10**400, 1])

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

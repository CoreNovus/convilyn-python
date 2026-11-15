"""The provider rows: complete, sourced, and usable as data."""

from __future__ import annotations

import datetime
import typing

import pytest

from convilyn import providers, rag
from convilyn.providers._profiles import PROFILES
from convilyn.providers._wires import EMBED_WIRES, RERANK_WIRES

ROWS = providers.available()
EMBEDDING_ROWS = tuple(row for row in ROWS if row.embed_wire)
RERANKING_ROWS = tuple(row for row in ROWS if row.rerank_wire)


class TestAvailable:
    def test_the_providers_are_listed_in_name_order(self) -> None:
        assert [row.name for row in ROWS] == [
            "cohere",
            "cohere_compatible",
            "gemini",
            "mixroute",
            "openai",
            "openai_compatible",
        ]

    def test_the_rows_say_which_providers_embed(self) -> None:
        assert [row.name for row in EMBEDDING_ROWS] == [
            "gemini",
            "mixroute",
            "openai",
            "openai_compatible",
        ]

    def test_the_rows_say_which_providers_rerank(self) -> None:
        assert [row.name for row in RERANKING_ROWS] == ["cohere", "cohere_compatible", "mixroute"]

    def test_each_row_is_filed_under_its_own_name(self) -> None:
        assert {name: row.name for name, row in PROFILES.items()} == {
            name: name for name in PROFILES
        }

    def test_a_row_is_frozen(self) -> None:
        with pytest.raises(Exception, match="frozen"):
            ROWS[0].max_inputs = 1  # type: ignore[misc]

    def test_the_openai_row(self) -> None:
        row = PROFILES["openai"]

        assert (row.base_url, row.key_env, row.max_inputs, row.max_input_tokens, row.tokenizer) == (
            "https://api.openai.com/v1",
            "OPENAI_API_KEY",
            2048,
            8192,
            "cl100k",
        )

    def test_the_gemini_row(self) -> None:
        row = PROFILES["gemini"]

        assert (row.base_url, row.key_env, row.max_inputs, row.model_input_tokens) == (
            "https://generativelanguage.googleapis.com/v1beta",
            "GEMINI_API_KEY",
            100,
            (("gemini-embedding-2", 8192), ("gemini-embedding-001", 2048)),
        )

    def test_the_cohere_row(self) -> None:
        row = PROFILES["cohere"]

        assert (row.base_url, row.key_env, row.rerank_max_documents) == (
            "https://api.cohere.com/v2",
            "CO_API_KEY",
            1000,
        )

    def test_a_provider_that_routes_to_many_models_names_no_tokenizer(self) -> None:
        assert PROFILES["mixroute"].tokenizer is None

    def test_a_compatible_server_has_no_address_or_key_variable_of_its_own(self) -> None:
        row = PROFILES["openai_compatible"]

        assert (row.base_url, row.key_env) == (None, None)

    def test_a_model_is_told_the_kind_in_one_way_only(self) -> None:
        row = PROFILES["gemini"]

        assert set(row.kind_aware_models) & set(row.kind_prefix_models) == set()


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
class TestEveryRow:
    """A limit without a source cannot be re-checked, so a row without one fails."""

    def test_it_names_the_documentation_it_was_read_from(self, row: providers.ProviderInfo) -> None:
        assert row.source.startswith("https://")

    def test_it_records_a_real_date_it_was_checked(self, row: providers.ProviderInfo) -> None:
        assert datetime.date.fromisoformat(row.checked) <= datetime.date.today()

    def test_it_offers_something(self, row: providers.ProviderInfo) -> None:
        assert row.embed_wire or row.rerank_wire

    def test_its_address_is_encrypted_when_it_has_one(self, row: providers.ProviderInfo) -> None:
        assert row.base_url is None or row.base_url.startswith("https://")

    def test_its_tokenizer_is_one_the_estimate_knows(self, row: providers.ProviderInfo) -> None:
        assert row.tokenizer is None or row.tokenizer in typing.get_args(rag.TokenizerFamily)

    def test_a_reranking_source_is_documentation_too(self, row: providers.ProviderInfo) -> None:
        assert row.rerank_source is None or row.rerank_source.startswith("https://")


@pytest.mark.parametrize("row", EMBEDDING_ROWS, ids=lambda row: row.name)
class TestEveryEmbeddingRow:
    def test_its_wire_format_is_one_this_sdk_speaks(self, row: providers.ProviderInfo) -> None:
        assert row.embed_wire in EMBED_WIRES

    def test_it_says_how_many_texts_a_request_carries(self, row: providers.ProviderInfo) -> None:
        assert row.max_inputs is not None and row.max_inputs > 0

    def test_its_token_limits_are_positive(self, row: providers.ProviderInfo) -> None:
        limits = [row.max_input_tokens or 1, *(tokens for _, tokens in row.model_input_tokens)]

        assert min(limits) > 0

    def test_an_embedder_can_be_created_for_it(self, row: providers.ProviderInfo) -> None:
        embedder = providers.embedder(
            row.name, model="m", api_key="k", base_url=row.base_url or "https://example.com/v1"
        )

        assert embedder.info is row


@pytest.mark.parametrize("row", RERANKING_ROWS, ids=lambda row: row.name)
class TestEveryRerankingRow:
    def test_its_wire_format_is_one_this_sdk_speaks(self, row: providers.ProviderInfo) -> None:
        assert row.rerank_wire in RERANK_WIRES

    def test_it_says_how_many_documents_a_request_carries(
        self, row: providers.ProviderInfo
    ) -> None:
        assert row.rerank_max_documents is not None and row.rerank_max_documents > 0

    def test_a_reranker_can_be_created_for_it(self, row: providers.ProviderInfo) -> None:
        reranker = providers.reranker(
            row.name, model="m", api_key="k", base_url=row.base_url or "https://example.com"
        )

        assert reranker.info is row

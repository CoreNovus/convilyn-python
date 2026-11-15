"""The providers, as rows of data.

A provider is a row here, never a branch in code: the connector reads the row
and does the same thing for every one. Adding a provider that speaks a wire
format already supported is adding a row.

Every limit below was read from the documentation named in ``source`` (or
``rerank_source``) on the date in ``checked``; a test fails when a row has
neither. Limits go stale. When one does, the provider's own error is still
reported clearly, and the caller can override the number without waiting for a
release. Where a provider publishes no number, the comment says so and says
where the one used came from.
"""

from __future__ import annotations

from convilyn.providers.types import ProviderInfo

_OPENAI_SPEC = "https://github.com/openai/openai-openapi/blob/manual_spec/openapi.yaml"
_COHERE_RERANK = "https://docs.cohere.com/reference/rerank"

PROFILES: dict[str, ProviderInfo] = {
    row.name: row
    for row in (
        ProviderInfo(
            name="openai",
            embed_wire="openai_embeddings",
            base_url="https://api.openai.com/v1",
            key_env="OPENAI_API_KEY",
            # "any array must be 2048 dimensions or less"
            max_inputs=2048,
            # "must not exceed the max input tokens for the model (8192 tokens ...)"
            max_input_tokens=8192,
            tokenizer="cl100k",
            source=_OPENAI_SPEC,
            checked="2026-11-14",
        ),
        ProviderInfo(
            name="mixroute",
            embed_wire="openai_embeddings",
            base_url="https://api.mixroute.ai/v1",
            key_env="MIXROUTE_API_KEY",
            # Not published by the provider. This is the smallest cap among the
            # model families it routes to: 96 texts per call for the "embed-"
            # models (https://docs.cohere.com/reference/embed).
            max_inputs=96,
            max_input_tokens=None,
            # It routes to models with different tokenizers.
            tokenizer=None,
            # "input_type ... Cohere only"
            kind_aware_models=("embed-",),
            source="https://mixroute.ai/docs/api-reference/endpoint/embeddings",
            rerank_wire="cohere_rerank",
            # Not published by the provider. This is what the format's own
            # documentation recommends staying under (see the "cohere" row).
            rerank_max_documents=1000,
            rerank_source="https://mixroute.ai/docs/api-reference/endpoint/rerank",
            checked="2026-11-14",
        ),
        ProviderInfo(
            name="openai_compatible",
            embed_wire="openai_embeddings",
            base_url=None,
            key_env=None,
            # The format's own cap; a server may accept fewer, so set
            # ``max_inputs`` to what yours documents.
            max_inputs=2048,
            max_input_tokens=None,
            tokenizer=None,
            source=_OPENAI_SPEC,
            checked="2026-11-14",
        ),
        ProviderInfo(
            name="gemini",
            embed_wire="gemini_embeddings",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            key_env="GEMINI_API_KEY",
            # Not published for ``batchEmbedContents``. The API's own
            # description states it for the older ``batchEmbedText``: "The
            # current limit is 100 texts, over which an error will be thrown."
            # (https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta,
            # which is also where the request and response shapes were read.)
            max_inputs=100,
            # Stated per model, in the guide's model table.
            max_input_tokens=None,
            model_input_tokens=(("gemini-embedding-2", 8192), ("gemini-embedding-001", 2048)),
            # Its tokenizer is not one the estimate has figures for.
            tokenizer=None,
            # "task_type" is taken by this model ...
            kind_aware_models=("gemini-embedding-001",),
            # ... and "You cannot use the task_type field for the
            # gemini-embedding-2 model": the task is written before the text.
            kind_prefix_models=("gemini-embedding-2",),
            source="https://ai.google.dev/gemini-api/docs/embeddings",
            checked="2026-11-14",
        ),
        ProviderInfo(
            name="cohere",
            base_url="https://api.cohere.com/v2",
            key_env="CO_API_KEY",
            rerank_wire="cohere_rerank",
            # "we recommend against sending more than 1,000 documents in a
            # single request": a recommendation, not a refusal.
            rerank_max_documents=1000,
            source=_COHERE_RERANK,
            checked="2026-11-14",
        ),
        ProviderInfo(
            name="cohere_compatible",
            base_url=None,
            # No variable of its own: a server you run is not sent the key of
            # the service whose format it copies.
            key_env=None,
            rerank_wire="cohere_rerank",
            # The format's own recommendation; a server may accept fewer, so
            # set ``max_documents`` to what yours documents.
            rerank_max_documents=1000,
            source=_COHERE_RERANK,
            checked="2026-11-14",
        ),
    )
}

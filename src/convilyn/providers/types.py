"""The values the provider connectors hand back.

Frozen, like the rest of this SDK's public models, and without wire aliases:
nothing here is serialized to or from the Convilyn API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from convilyn.rag import TokenizerFamily

#: Whether a text is something to be found or something to find with. Some
#: models embed the two differently; the rest ignore it.
EmbedKind = Literal["document", "query"]

#: What to do with a text longer than the model accepts.
#:
#: * ``"error"`` — refuse, naming the text and the limit
#: * ``"truncate"`` — cut it to fit, and send the shortened text
OverflowPolicy = Literal["error", "truncate"]


class ProviderInfo(BaseModel):
    """One provider a connector can be created for, and what is known about it.

    A provider offers embeddings, reranking or both: ``embed_wire`` and
    ``rerank_wire`` say which. The limits are defaults a caller can override,
    not gates: a provider may change them, and a model the row does not know
    about is allowed through.
    """

    model_config = ConfigDict(frozen=True)

    #: The key it is created with: ``providers.embedder(name, ...)``.
    name: str
    #: Where requests go. ``None`` when the caller has to say.
    base_url: str | None
    #: The environment variable the key is read from when none is passed.
    key_env: str | None
    #: The documentation the row was read from.
    source: str
    #: The date that documentation was last checked, as ``YYYY-MM-DD``.
    checked: str

    #: The request and response format of its embeddings. ``None`` when there
    #: is no embedder for it.
    embed_wire: str | None = None
    #: How many texts one embedding request carries.
    max_inputs: int | None = None
    #: The most tokens one text may have, when one number holds for all of its
    #: models.
    max_input_tokens: int | None = None
    #: The same limit for models that have their own, as pairs of a model name
    #: prefix and the number. The first prefix a model starts with applies.
    model_input_tokens: tuple[tuple[str, int], ...] = ()
    #: The tokenizer its models use, when one answer fits all of them.
    tokenizer: TokenizerFamily | None = None
    #: Model name prefixes that are told, in a field of the request, whether a
    #: text is a document or a query.
    kind_aware_models: tuple[str, ...] = ()
    #: Model name prefixes that are told the same thing in words put before
    #: the text, because that is the form their documentation gives.
    kind_prefix_models: tuple[str, ...] = ()

    #: The request and response format of its reranking. ``None`` when there
    #: is no reranker for it.
    rerank_wire: str | None = None
    #: How many documents one reranking request carries.
    rerank_max_documents: int | None = None
    #: The documentation the reranking fields were read from, when it is not
    #: ``source``.
    rerank_source: str | None = None


class EmbedResult(BaseModel):
    """Embeddings for the texts passed, in the order they were passed."""

    model_config = ConfigDict(frozen=True)

    #: One vector per text, each scaled to length 1.
    vectors: tuple[tuple[float, ...], ...]
    #: The model the provider says it used.
    model: str
    #: Tokens the provider counted across all requests, when it reports them.
    tokens: int | None


class RankedDocument(BaseModel):
    """One document of a reranking, and how well it answers the query."""

    model_config = ConfigDict(frozen=True)

    #: The document's position in the list that was passed.
    index: int
    #: The model's score. Higher is more relevant. It orders the documents of
    #: one query and means nothing more: it is not a probability, and scores
    #: of different queries or models do not compare.
    score: float


class RerankResult(BaseModel):
    """The documents passed, put in order by a reranking model."""

    model_config = ConfigDict(frozen=True)

    #: The documents, most relevant first. Every document passed is here once,
    #: unless ``top_n`` asked for fewer.
    ranking: tuple[RankedDocument, ...]


__all__ = [
    "EmbedKind",
    "EmbedResult",
    "OverflowPolicy",
    "ProviderInfo",
    "RankedDocument",
    "RerankResult",
]

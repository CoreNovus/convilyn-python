"""Rerankers: one configured connection to one provider's reranking model.

A reranker reads a query beside each document and scores how well the document
answers it, which orders a shortlist better than the search that produced it.
:class:`AsyncReranker` and :class:`Reranker` share every step but the waiting,
as the embedders do, and go through the same sequence: check what was passed,
cut the documents into requests, send, check each answer, put the answers
together.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import httpx

from convilyn._internal.resilience import RetryPolicy
from convilyn.providers import _steps, _transport
from convilyn.providers._wires import RERANK_WIRES
from convilyn.providers._wires.shapes import RerankWire
from convilyn.providers.types import ProviderInfo, RankedDocument, RerankResult


@dataclass(frozen=True)
class Settings:
    """Everything one reranker was configured with, resolved and checked."""

    info: ProviderInfo
    model: str
    wire: RerankWire = field(repr=False)
    connection: _transport.Connection
    max_documents: int


def resolve(
    name: str,
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_documents: int | None,
    retry: RetryPolicy | None,
    timeout: float,
    has_client: bool,
) -> Settings:
    """Check the arguments of a factory against the provider's row."""
    info = _transport.row(
        name, lambda row: row.rerank_wire and row.rerank_max_documents, "reranking"
    )
    _transport.check_model(model)
    _transport.check_limit("max_documents", max_documents)

    wire = RERANK_WIRES[info.rerank_wire or ""]
    connection = _transport.connection(
        info,
        url=lambda base: wire.url(base, model),
        headers=wire.headers,
        api_key=api_key,
        base_url=base_url,
        retry=retry,
        timeout=timeout,
        has_client=has_client,
    )
    return Settings(
        info=info,
        model=model,
        wire=wire,
        connection=connection,
        max_documents=max_documents or info.rerank_max_documents or 0,
    )


class _Reranking:
    """What the two rerankers share: the steps before and after the sending."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def info(self) -> ProviderInfo:
        """The provider's row."""
        return self._settings.info

    @property
    def model(self) -> str:
        """The model this reranker asks for."""
        return self._settings.model

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider={self.info.name!r}, model={self.model!r})"

    def _plan(
        self, query: str, documents: Sequence[str], top_n: int | None
    ) -> list[tuple[_steps.Batch, dict[str, Any]]]:
        settings = self._settings
        if not isinstance(query, str):
            raise TypeError(f"query must be a string, got {type(query).__name__}")
        if not query:
            raise ValueError("query is empty; a provider cannot rank documents against nothing")
        _transport.check_limit("top_n", top_n)
        checked = _steps.check_texts(documents, "document")
        return [
            (batch, settings.wire.body(batch, model=settings.model, query=query))
            for batch in _steps.batches(checked, settings.max_documents)
        ]

    def _take(
        self, scored: list[RankedDocument], batch: _steps.Batch, response: httpx.Response
    ) -> None:
        """Check one answer and add it. A bad one stops the call before the next request."""
        scores = self._settings.wire.read(_transport.decoded(response), expected=len(batch.texts))
        scored.extend(
            RankedDocument.model_construct(index=batch.start + offset, score=score)
            for offset, score in enumerate(scores)
        )

    def _finish(self, scored: list[RankedDocument], top_n: int | None) -> RerankResult:
        """Order every document by score, and keep the first ``top_n``.

        Documents with the same score keep the order they were passed in, so
        the same call always returns the same ranking.
        """
        ranking = sorted(scored, key=lambda ranked: (-ranked.score, ranked.index))
        return RerankResult.model_construct(ranking=tuple(ranking[:top_n]))


class Reranker(_Reranking):
    """Orders documents by relevance to a query. Create it with ``providers.reranker``."""

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        super().__init__(settings)
        self._sender = _transport.Sender(settings.connection, http_client)

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> RerankResult:
        """Return ``documents`` by position, most relevant to ``query`` first.

        Every document is scored, in as many requests as the provider's limit
        needs; ``top_n`` keeps only that many of the best once all are in.
        """
        scored: list[RankedDocument] = []
        for batch, body in self._plan(query, documents, top_n):
            self._take(scored, batch, self._sender.send(body))
        return self._finish(scored, top_n)

    def close(self) -> None:
        """Close the HTTP client, if this reranker made it. Safe to call more than once."""
        self._sender.close()

    def __enter__(self) -> Reranker:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()


class AsyncReranker(_Reranking):
    """:class:`Reranker` for async code. Create it with ``providers.areranker``."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        self._sender = _transport.AsyncSender(settings.connection, http_client)

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> RerankResult:
        """Return ``documents`` by position, most relevant to ``query`` first."""
        scored: list[RankedDocument] = []
        for batch, body in self._plan(query, documents, top_n):
            self._take(scored, batch, await self._sender.send(body))
        return self._finish(scored, top_n)

    async def aclose(self) -> None:
        """Close the HTTP client, if this reranker made it. Safe to call more than once."""
        await self._sender.aclose()

    async def __aenter__(self) -> AsyncReranker:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        await self.aclose()

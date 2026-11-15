"""The factories of :mod:`convilyn.providers`.

Kept out of the package's ``__init__`` so that the namespace a user imports
holds the names it offers and nothing it merely needed to build them.
"""

from __future__ import annotations

import httpx

from convilyn import rag
from convilyn._internal.resilience import RetryPolicy
from convilyn.providers import _embedder, _reranker
from convilyn.providers._embedder import AsyncEmbedder, Embedder
from convilyn.providers._profiles import PROFILES
from convilyn.providers._reranker import AsyncReranker, Reranker
from convilyn.providers._transport import DEFAULT_TIMEOUT
from convilyn.providers.types import OverflowPolicy, ProviderInfo


def embedder(
    name: str,
    /,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
    max_inputs: int | None = None,
    max_input_tokens: int | None = None,
    on_overflow: OverflowPolicy = "error",
    measure: rag.Measure | None = None,
    retry: RetryPolicy | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    http_client: httpx.Client | None = None,
) -> Embedder:
    """Create an embedder for the provider ``name``.

    ``api_key`` defaults to the environment variable the provider's row names.
    ``base_url`` replaces the provider's address, and is required for
    ``"openai_compatible"``. ``dimensions`` asks for vectors of that size, from
    models that can produce them. ``max_inputs`` and ``max_input_tokens``
    override the row's limits. ``measure`` counts the tokens of a text exactly;
    with it, a text over the limit is refused by name, or cut when
    ``on_overflow`` is ``"truncate"``. ``retry`` replaces the retry policy.
    ``http_client`` is used instead of one made here, and is not closed.
    """
    if http_client is not None and not isinstance(http_client, httpx.Client):
        raise TypeError("embedder() takes an httpx.Client; use aembedder() with an AsyncClient")
    settings = _embedder.resolve(
        name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        dimensions=dimensions,
        max_inputs=max_inputs,
        max_input_tokens=max_input_tokens,
        on_overflow=on_overflow,
        measure=measure,
        retry=retry,
        timeout=timeout,
        has_client=http_client is not None,
    )
    return Embedder(settings, http_client)


def aembedder(
    name: str,
    /,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
    max_inputs: int | None = None,
    max_input_tokens: int | None = None,
    on_overflow: OverflowPolicy = "error",
    measure: rag.Measure | None = None,
    retry: RetryPolicy | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncEmbedder:
    """Create an embedder for async code. See :func:`embedder`."""
    if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
        raise TypeError("aembedder() takes an httpx.AsyncClient; use embedder() with a Client")
    settings = _embedder.resolve(
        name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        dimensions=dimensions,
        max_inputs=max_inputs,
        max_input_tokens=max_input_tokens,
        on_overflow=on_overflow,
        measure=measure,
        retry=retry,
        timeout=timeout,
        has_client=http_client is not None,
    )
    return AsyncEmbedder(settings, http_client)


def reranker(
    name: str,
    /,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    max_documents: int | None = None,
    retry: RetryPolicy | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    http_client: httpx.Client | None = None,
) -> Reranker:
    """Create a reranker for the provider ``name``.

    ``api_key`` defaults to the environment variable the provider's row names.
    ``base_url`` replaces the provider's address. ``max_documents`` overrides
    how many documents one request carries. ``retry`` replaces the retry
    policy. ``http_client`` is used instead of one made here, and is not closed.
    """
    if http_client is not None and not isinstance(http_client, httpx.Client):
        raise TypeError("reranker() takes an httpx.Client; use areranker() with an AsyncClient")
    settings = _reranker.resolve(
        name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_documents=max_documents,
        retry=retry,
        timeout=timeout,
        has_client=http_client is not None,
    )
    return Reranker(settings, http_client)


def areranker(
    name: str,
    /,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    max_documents: int | None = None,
    retry: RetryPolicy | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncReranker:
    """Create a reranker for async code. See :func:`reranker`."""
    if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
        raise TypeError("areranker() takes an httpx.AsyncClient; use reranker() with a Client")
    settings = _reranker.resolve(
        name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_documents=max_documents,
        retry=retry,
        timeout=timeout,
        has_client=http_client is not None,
    )
    return AsyncReranker(settings, http_client)


def available() -> tuple[ProviderInfo, ...]:
    """Describe every provider a connector can be created for.

    A row with an ``embed_wire`` can be passed to :func:`embedder`, and one
    with a ``rerank_wire`` to :func:`reranker`.
    """
    return tuple(PROFILES[name] for name in sorted(PROFILES))

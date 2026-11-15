"""Embedders: one configured connection to one provider's embedding model.

:class:`AsyncEmbedder` and :class:`Embedder` do the same thing and share every
step but the waiting. Each is created through its factory in
:mod:`convilyn.providers`, which resolves the settings once; an embedder then
holds no decision of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from reprlib import repr as shown
from types import TracebackType
from typing import Any

import httpx

from convilyn import rag
from convilyn._internal.resilience import RetryPolicy
from convilyn.providers import _steps, _transport
from convilyn.providers._wires import EMBED_WIRES
from convilyn.providers._wires.shapes import EmbedWire, KindStyle
from convilyn.providers.types import EmbedKind, EmbedResult, OverflowPolicy, ProviderInfo

_KINDS: tuple[EmbedKind, ...] = ("document", "query")
_POLICIES: tuple[OverflowPolicy, ...] = ("error", "truncate")


@dataclass(frozen=True)
class Settings:
    """Everything one embedder was configured with, resolved and checked."""

    info: ProviderInfo
    model: str
    wire: EmbedWire = field(repr=False)
    connection: _transport.Connection
    dimensions: int | None
    max_inputs: int
    max_input_tokens: int | None
    on_overflow: OverflowPolicy
    measure: rag.Measure | None
    kind_style: KindStyle


def resolve(
    name: str,
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    dimensions: int | None,
    max_inputs: int | None,
    max_input_tokens: int | None,
    on_overflow: OverflowPolicy,
    measure: rag.Measure | None,
    retry: RetryPolicy | None,
    timeout: float,
    has_client: bool,
) -> Settings:
    """Check the arguments of a factory against the provider's row."""
    info = _transport.row(name, lambda row: row.embed_wire and row.max_inputs, "embeddings")
    _transport.check_model(model)
    if on_overflow not in _POLICIES:
        raise ValueError(f'on_overflow must be "error" or "truncate", got {shown(on_overflow)}')
    _transport.check_limit("dimensions", dimensions)
    _transport.check_limit("max_inputs", max_inputs)
    _transport.check_limit("max_input_tokens", max_input_tokens)

    wire = EMBED_WIRES[info.embed_wire or ""]
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

    limit = max_input_tokens or _model_limit(info, model)
    if measure is None and on_overflow == "truncate" and limit is not None:
        # Nothing exact to count with, so count cautiously: a cut that is a
        # little short is the request that was made.
        measure = rag.token_estimator(info.tokenizer)
    return Settings(
        info=info,
        model=model,
        wire=wire,
        connection=connection,
        dimensions=dimensions,
        max_inputs=max_inputs or info.max_inputs or 0,
        max_input_tokens=limit,
        on_overflow=on_overflow,
        measure=measure,
        kind_style=_kind_style(info, model),
    )


def _model_limit(info: ProviderInfo, model: str) -> int | None:
    """Return the most tokens one text may have for ``model``, when the row says."""
    for prefix, tokens in info.model_input_tokens:
        if model.startswith(prefix):
            return tokens
    return info.max_input_tokens


def _kind_style(info: ProviderInfo, model: str) -> KindStyle:
    """Return how ``model`` is told a document from a query, if it is."""
    if model.startswith(info.kind_aware_models):
        return "field"
    if model.startswith(info.kind_prefix_models):
        return "prefix"
    return None


@dataclass
class _Gathered:
    """The answers taken in so far."""

    width: int | None
    vectors: list[tuple[float, ...]] = field(default_factory=list)
    model: str | None = None
    tokens: int | None = 0


class _Embedding:
    """What the two embedders share: the steps before and after the sending."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def info(self) -> ProviderInfo:
        """The provider's row."""
        return self._settings.info

    @property
    def model(self) -> str:
        """The model this embedder asks for."""
        return self._settings.model

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider={self.info.name!r}, model={self.model!r})"

    def _plan(
        self, texts: Sequence[str], kind: EmbedKind
    ) -> list[tuple[_steps.Batch, dict[str, Any]]]:
        settings = self._settings
        if kind not in _KINDS:
            raise ValueError(f'kind must be "document" or "query", got {shown(kind)}')
        fitted = _steps.fit(
            _steps.check_texts(texts),
            limit=settings.max_input_tokens,
            measure=settings.measure,
            policy=settings.on_overflow,
        )
        return [
            (
                batch,
                settings.wire.body(
                    batch,
                    model=settings.model,
                    dimensions=settings.dimensions,
                    kind=kind,
                    kind_style=settings.kind_style,
                ),
            )
            for batch in _steps.batches(fitted, settings.max_inputs)
        ]

    def _begin(self) -> _Gathered:
        return _Gathered(width=self._settings.dimensions)

    def _take(self, gathered: _Gathered, batch: _steps.Batch, response: httpx.Response) -> None:
        """Check one answer and add it. A bad one stops the call before the next request."""
        raw, said, counted = self._settings.wire.read(
            _transport.decoded(response), expected=len(batch.texts)
        )
        for vector in raw:
            gathered.width = gathered.width or len(vector)
            if len(vector) != gathered.width:
                raise _steps.failure("bad_response", response.status_code)
            gathered.vectors.append(_steps.unit(vector))
        gathered.model = gathered.model or said
        gathered.tokens = (
            None if gathered.tokens is None or counted is None else gathered.tokens + counted
        )

    def _finish(self, gathered: _Gathered) -> EmbedResult:
        return EmbedResult.model_construct(
            vectors=tuple(gathered.vectors),
            model=gathered.model or self._settings.model,
            tokens=gathered.tokens,
        )


class Embedder(_Embedding):
    """Embeds texts with one provider's model. Create it with ``providers.embedder``."""

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        super().__init__(settings)
        self._sender = _transport.Sender(settings.connection, http_client)

    def embed(self, texts: Sequence[str], *, kind: EmbedKind = "document") -> EmbedResult:
        """Return one unit-length vector per text, in the order given.

        ``kind`` says whether the texts are documents to be found or queries to
        find them with; it only changes the result for models that tell the
        two apart.
        """
        gathered = self._begin()
        for batch, body in self._plan(texts, kind):
            self._take(gathered, batch, self._sender.send(body))
        return self._finish(gathered)

    def close(self) -> None:
        """Close the HTTP client, if this embedder made it. Safe to call more than once."""
        self._sender.close()

    def __enter__(self) -> Embedder:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()


class AsyncEmbedder(_Embedding):
    """:class:`Embedder` for async code. Create it with ``providers.aembedder``."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        self._sender = _transport.AsyncSender(settings.connection, http_client)

    async def embed(self, texts: Sequence[str], *, kind: EmbedKind = "document") -> EmbedResult:
        """Return one unit-length vector per text, in the order given."""
        gathered = self._begin()
        for batch, body in self._plan(texts, kind):
            self._take(gathered, batch, await self._sender.send(body))
        return self._finish(gathered)

    async def aclose(self) -> None:
        """Close the HTTP client, if this embedder made it. Safe to call more than once."""
        await self._sender.aclose()

    async def __aenter__(self) -> AsyncEmbedder:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        await self.aclose()

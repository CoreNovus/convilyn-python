"""A corpus: the stages composed over one store.

Nothing here does work a caller could not do with the stages themselves —
``split``, ``normalize`` and a store's own methods. It keeps the settings in
one place and applies the same text fold when adding and when searching, which
is the part that is easy to get wrong by hand.

:class:`AsyncCorpus` is the implementation; :class:`Corpus` runs it on a
private event loop for code that is not async, the same arrangement as the
SDK's clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from collections.abc import Callable, Coroutine, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, TypeVar

from convilyn._internal.loop_runner import LoopRunner
from convilyn.rag import _config, _models
from convilyn.rag._filter import check_meta, parse_where
from convilyn.rag._measure import Measure
from convilyn.rag._models import Embedder, Reranker
from convilyn.rag._normalize import normalize, normalizer_stamp
from convilyn.rag._rank import fuse
from convilyn.rag._split import split
from convilyn.rag._stores import store as create_store
from convilyn.rag._stores.protocol import Store
from convilyn.rag._stores.sqlite import refuse_vectors_it_cannot_search
from convilyn.rag.errors import RagError, StoreError
from convilyn.rag.recipe import Recipe
from convilyn.rag.types import (
    Condition,
    Hit,
    IndexEntry,
    IndexIdentity,
    Passage,
    Scalar,
    SearchResult,
    SourceInfo,
)

T = TypeVar("T")


def _digest(text: str) -> str | None:
    """The digest a passage of ``text`` carries: SHA-256 of its UTF-8 bytes.

    ``None`` for text UTF-8 cannot encode (a lone surrogate): no passage can
    carry a digest of it, so such a passage is refused.
    """
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None


def _refuse_running_loop() -> None:
    """Point async code at the async corpus instead of blocking its event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "rag.open and Corpus cannot be used from inside a running event loop; "
        "use rag.aopen and AsyncCorpus from async code"
    )


class AsyncCorpus:
    """Documents in a store, searchable. Create one with :func:`convilyn.rag.aopen`."""

    def __init__(
        self,
        *,
        store: Store,
        recipe: Recipe,
        measure: Measure | None,
        embed: Embedder | None = None,
        rerank: Reranker | None = None,
    ) -> None:
        self._store = store
        self._recipe = recipe
        self._measure = measure
        self._embed = embed
        self._rerank = rerank
        # Set by `aopen` for the models a config file described.
        self._closers: tuple[Callable[[], object], ...] = ()

    @property
    def store(self) -> Store:
        """The store this corpus reads and writes."""
        return self._store

    @property
    def recipe(self) -> Recipe:
        """The settings this corpus was opened with."""
        return self._recipe

    async def add_markdown(
        self,
        markdown: str,
        *,
        source_id: str,
        title: str | None = None,
        meta: Mapping[str, Scalar] | None = None,
    ) -> SourceInfo:
        """Split ``markdown`` and store it as ``source_id``, replacing what that held."""
        passages = split(
            markdown,
            source_id=source_id,
            title=title,
            options=self._recipe.split,
            measure=self._measure,
        )
        return await self._put(source_id, passages, title, meta, markdown)

    async def add_passages(
        self,
        passages: Sequence[Passage],
        *,
        title: str | None = None,
        meta: Mapping[str, Scalar] | None = None,
    ) -> SourceInfo:
        """Store passages you cut yourself, as one document.

        All of them must carry the same ``source_id``, and they replace what
        that document held.
        """
        owners = {passage.source_id for passage in passages}
        if len(owners) != 1:
            raise ValueError(
                "add_passages takes the passages of exactly one source_id per call, "
                f"got {len(owners)}"
            )
        return await self._put(owners.pop(), passages, title, meta, None)

    async def remove(self, source_id: str) -> bool:
        """Drop a document. Returns whether it was there."""
        return await self._store.remove(source_id)

    async def sources(self) -> tuple[SourceInfo, ...]:
        """List the documents held, ordered by ``source_id``."""
        return await self._store.sources()

    async def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        where: Mapping[str, object] | None = None,
    ) -> SearchResult:
        """Return the passages that best match ``query``.

        ``limit`` defaults to the recipe's. ``where`` narrows the search to
        documents whose labels match: ``{"lang": "en"}``, or ``{"lang": ["en",
        "ja"]}`` for any of several.
        """
        result, failures = await self._search(query, limit, where)
        for step, error in failures:
            _models.fell_back(step, error, stacklevel=2)
        return result

    async def _search(
        self, query: str, limit: int | None, where: Mapping[str, object] | None
    ) -> tuple[SearchResult, list[tuple[str, Exception]]]:
        """The search, and the steps that failed with their errors.

        The public methods warn about the failures themselves, so that the
        warning names the caller's line rather than the event loop's.
        """
        wanted = self._recipe.search.limit if limit is None else limit
        if type(wanted) is not int or wanted <= 0:
            raise ValueError("limit must be a positive integer")
        conditions = parse_where(where)
        folded = normalize(query, self._recipe.normalize)
        failures: list[tuple[str, Exception]] = []
        if self._embed is None and self._rerank is None:
            hits = await self._store.lexical(folded, limit=wanted, where=conditions)
            return SearchResult(hits=hits), failures

        pool = max(wanted, self._recipe.search.candidates)
        skipped: list[Literal["embed", "rerank"]] = []
        rankings = [await self._store.lexical(folded, limit=pool, where=conditions)]
        if self._embed is not None:
            dense = await self._vector_hits(query, pool, conditions, failures)
            if dense is None:
                skipped.append("embed")
            else:
                rankings.append(dense)
        ranked = list(
            fuse(*rankings, options=self._recipe.fuse) if len(rankings) > 1 else rankings[0]
        )
        if self._rerank is not None and ranked:
            reordered = await self._reordered(query, ranked[:pool], failures)
            if reordered is None:
                skipped.append("rerank")
            else:
                ranked = reordered
        return SearchResult(hits=tuple(ranked[:wanted]), skipped=tuple(skipped)), failures

    async def aclose(self) -> None:
        """Close the store, and the models the corpus built from a config file.

        Each is closed even when one before it fails; the first failure is
        raised afterwards.
        """
        await _close_all(self._store, self._closers)

    async def __aenter__(self) -> AsyncCorpus:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _put(
        self,
        source_id: str,
        passages: Sequence[Passage],
        title: str | None,
        meta: Mapping[str, Scalar] | None,
        markdown: str | None,
    ) -> SourceInfo:
        """Store ``passages`` as ``source_id``.

        The document's digest is that of ``markdown`` when it was added as
        Markdown, else that of the passages' digests, taken once they are
        known to be the digests of their texts.
        """
        for position, passage in enumerate(passages):
            # A stored vector is reused by digest, so a digest that is not the
            # text's would keep the vector of text that is no longer there.
            expected = _digest(passage.text)
            if expected is None or passage.digest != expected:
                raise ValueError(
                    f"passage {position}'s digest is not the SHA-256 of its text; "
                    "build passages with rag.split, or set digest from the text"
                )
        # Tagged with the way it was added, so Markdown that happens to read
        # like a list of passage digests is not the same document.
        added = (
            b"markdown\0" + markdown.encode("utf-8")
            if markdown is not None
            else b"passages\0" + "\n".join(passage.digest for passage in passages).encode("utf-8")
        )
        source = SourceInfo(
            source_id=source_id,
            title=title,
            meta=check_meta(meta),
            passages=len(passages),
            digest=hashlib.sha256(added).hexdigest(),
        )
        vectors = await self._passage_vectors(source_id, passages)
        entries = [
            IndexEntry(
                passage=passage,
                folded=normalize(passage.text, self._recipe.normalize),
                vector=vector,
            )
            for passage, vector in zip(passages, vectors, strict=True)
        ]
        await self._store.write(source, entries)
        return source

    async def _passage_vectors(
        self, source_id: str, passages: Sequence[Passage]
    ) -> list[tuple[float, ...] | None]:
        """One vector per passage, embedding only those whose text is new.

        A passage whose text is unchanged since the document was last added
        keeps the vector stored for it. A failure here is raised: a document
        stored without the vectors its index promises would be found by
        keyword only, and nothing would say so.
        """
        if self._embed is None:
            return [None] * len(passages)
        known = await self._store.vectors(source_id)
        fresh = {p.digest: p.text for p in passages if p.digest not in known}
        if fresh:
            embedded = await _models.embed(self._embed, list(fresh.values()), "document")
            known = {
                **known,
                **{digest: tuple(vector) for digest, vector in zip(fresh, embedded, strict=True)},
            }
        return [known[passage.digest] for passage in passages]

    async def _vector_hits(
        self,
        query: str,
        pool: int,
        conditions: Sequence[Condition],
        failures: list[tuple[str, Exception]],
    ) -> tuple[Hit, ...] | None:
        """The vector search's hits, or ``None`` when the query could not be embedded.

        Any failure of the embedder counts, and so does a vector the store
        refuses as a vector (``ValueError`` or ``TypeError``, as the store
        contract states): the search then answers by keyword and the failure
        is recorded in ``failures``. Any other failure of the store is raised.
        """
        assert self._embed is not None
        try:
            vector = (await _models.embed(self._embed, [query], "query"))[0]
        except Exception as error:  # noqa: BLE001 — a failed embedding falls back to keyword search
            failures.append(("embed", error))
            return None
        try:
            return await self._store.dense(vector, limit=pool, where=conditions)
        except (ValueError, TypeError) as error:
            failures.append(("embed", error))
            return None

    async def _reordered(
        self, query: str, hits: Sequence[Hit], failures: list[tuple[str, Exception]]
    ) -> list[Hit] | None:
        """``hits`` in the reranker's order with its scores, or ``None`` when it failed."""
        assert self._rerank is not None
        try:
            ranked = await _models.rerank(self._rerank, query, [hit.passage.text for hit in hits])
        except Exception as error:  # noqa: BLE001 — a failed reranker leaves the fused order
            failures.append(("rerank", error))
            return None
        return [hits[index].model_copy(update={"score": score}) for index, score in ranked]


class Corpus:
    """:class:`AsyncCorpus` for code that is not async.

    Create one with :func:`convilyn.rag.open`.
    """

    def __init__(self, inner: AsyncCorpus, runner: LoopRunner) -> None:
        self._inner = inner
        self._runner = runner

    @property
    def store(self) -> Store:
        """The store this corpus reads and writes."""
        return self._inner.store

    @property
    def recipe(self) -> Recipe:
        """The settings this corpus was opened with."""
        return self._inner.recipe

    def add_markdown(
        self,
        markdown: str,
        *,
        source_id: str,
        title: str | None = None,
        meta: Mapping[str, Scalar] | None = None,
    ) -> SourceInfo:
        """Split ``markdown`` and store it as ``source_id``, replacing what that held."""
        return self._run(
            self._inner.add_markdown(markdown, source_id=source_id, title=title, meta=meta)
        )

    def add_passages(
        self,
        passages: Sequence[Passage],
        *,
        title: str | None = None,
        meta: Mapping[str, Scalar] | None = None,
    ) -> SourceInfo:
        """Store passages you cut yourself, as one document."""
        return self._run(self._inner.add_passages(passages, title=title, meta=meta))

    def remove(self, source_id: str) -> bool:
        """Drop a document. Returns whether it was there."""
        return self._run(self._inner.remove(source_id))

    def sources(self) -> tuple[SourceInfo, ...]:
        """List the documents held, ordered by ``source_id``."""
        return self._run(self._inner.sources())

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        where: Mapping[str, object] | None = None,
    ) -> SearchResult:
        """Return the passages that best match ``query``."""
        result, failures = self._run(self._inner._search(query, limit, where))
        for step, error in failures:
            _models.fell_back(step, error, stacklevel=2)
        return result

    def close(self) -> None:
        """Close the store and the private event loop. Safe to call more than once."""
        if self._runner.closed:
            return
        try:
            self._runner.run(self._inner.aclose())
        finally:
            self._runner.close()

    def __enter__(self) -> Corpus:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def _run(self, work: Coroutine[Any, Any, T]) -> T:
        try:
            _refuse_running_loop()
            if self._runner.closed:
                raise StoreError("the corpus is closed")
        except (RuntimeError, StoreError):
            work.close()
            raise
        return self._runner.run(work)


async def aopen(
    config: str | os.PathLike[str] | None = None,
    /,
    *,
    store: Store | None = None,
    recipe: Recipe | None = None,
    measure: Measure | None = None,
    embed: Embedder | None = None,
    rerank: Reranker | None = None,
) -> AsyncCorpus:
    """Open a corpus over ``store``.

    With no store, the index is a SQLite database in memory. ``measure``
    replaces the token estimate the recipe names, as it does for
    :func:`convilyn.rag.split`. Raises
    :class:`~convilyn.rag.errors.StoreError` when the store already holds an
    index built with different settings.

    ``embed`` adds vector search: passages are embedded as they are added,
    and a search runs keyword and vector search and fuses them by
    ``recipe.fuse``. Opening embeds one short text to learn the vector size,
    and the index records the model and the size, so it is refused under
    another embedder. ``rerank`` reorders the best ``recipe.search.candidates``
    passages. Either may be an object from :mod:`convilyn.providers` or one of
    your own of the same shape (:class:`~convilyn.rag.Embedder`,
    :class:`~convilyn.rag.Reranker`); the corpus calls them but does not
    close them.

    ``config`` names a TOML file with ``[store]``, ``[embed]``, ``[rerank]``
    and ``[recipe]`` tables (see ``docs/TOOLS.md``); a piece passed as an
    argument fills a table the file does not have. The embedder and reranker
    the file describes are built by the corpus, so closing it closes them.
    The file never holds a key: each connector reads the environment variable
    its provider names. Load only a file you trust like code: it chooses
    where that key is sent and which files are opened.

    The corpus owns the store from here on: closing the corpus closes it, and
    so does a failure to open.
    """
    return await _start(
        config,
        synchronous=False,
        store=store,
        recipe=recipe,
        measure=measure,
        embed=embed,
        rerank=rerank,
    )


async def _start(
    config: str | os.PathLike[str] | None,
    *,
    synchronous: bool,
    store: Store | None,
    recipe: Recipe | None,
    measure: Measure | None,
    embed: Embedder | None,
    rerank: Reranker | None,
) -> AsyncCorpus:
    """What :func:`aopen` and :func:`open` share.

    From the first line on, the store and the models built from ``config``
    are the corpus's: any failure before the corpus exists closes them.
    """
    closers: tuple[Callable[[], object], ...] = ()
    try:
        if config is not None:
            loaded = _config.parts(
                config,
                asynchronous=not synchronous,
                store=store,
                recipe=recipe,
                embed=embed,
                rerank=rerank,
            )
            store, recipe, embed, rerank = loaded.store, loaded.recipe, loaded.embed, loaded.rerank
            closers = loaded.closers
        if synchronous and (
            _models.is_async(getattr(embed, "embed", None))
            or _models.is_async(getattr(rerank, "rerank", None))
        ):
            raise TypeError(
                "rag.open takes a synchronous embedder and reranker; use rag.aopen for async "
                "ones (providers.aembedder, providers.areranker)"
            )
        if embed is not None and not isinstance(embed, Embedder):
            raise TypeError("embed must have a model name and an embed(texts, *, kind) method")
        if rerank is not None and not isinstance(rerank, Reranker):
            raise TypeError("rerank must have a rerank(query, documents, *, top_n) method")
        chosen = recipe or Recipe()
        store = create_store("sqlite") if store is None else store
        if embed is not None:
            # Before the first embedding call: opening already embeds one text.
            refuse_vectors_it_cannot_search(store)
        await store.prepare(await _identity(chosen, embed))
    except BaseException:
        # With no corpus, nothing else will close them. A failure to close
        # must not replace the reason opening failed.
        with contextlib.suppress(Exception):
            await _close_all(store, closers)
        raise
    corpus = AsyncCorpus(store=store, recipe=chosen, measure=measure, embed=embed, rerank=rerank)
    corpus._closers = closers
    return corpus


async def _close_all(store: Store | None, closers: Sequence[Callable[[], object]]) -> None:
    """Close ``store`` and run every closer, each even when one before it failed.

    The first failure is raised once all have run.
    """
    first: Exception | None = None
    steps: list[Callable[[], object]] = [store.close] if store is not None else []
    for close in [*steps, *closers]:
        try:
            await _models.call(close)
        except Exception as error:  # noqa: BLE001 — every step runs; the first failure is raised
            first = first or error
    if first is not None:
        raise first


async def _identity(recipe: Recipe, embed: Embedder | None) -> IndexIdentity:
    """How an index opened with ``recipe`` and ``embed`` is built."""
    normalizer = normalizer_stamp(recipe.normalize)
    if embed is None:
        return IndexIdentity(normalizer=normalizer)
    name = _models.model_name(embed)
    probe = await _models.embed(embed, ["dimensions"], "document")
    if not probe[0]:
        raise RagError("the embedder returned an empty vector")
    return IndexIdentity(normalizer=normalizer, embedding=name, dimensions=len(probe[0]))


def open(  # noqa: A001 — reached as `rag.open`, where the name is the natural one
    config: str | os.PathLike[str] | None = None,
    /,
    *,
    store: Store | None = None,
    recipe: Recipe | None = None,
    measure: Measure | None = None,
    embed: Embedder | None = None,
    rerank: Reranker | None = None,
) -> Corpus:
    """Open a corpus over ``store``, for code that is not async. See :func:`aopen`.

    ``embed`` and ``rerank`` must be synchronous here, such as
    ``providers.embedder(...)``. An ``async`` one would be bound to this
    corpus's private event loop, which closes with the corpus and would leave
    its connections open; use :func:`aopen` for those. Models described in
    ``config`` are built synchronous here.
    """
    _refuse_running_loop()
    runner = LoopRunner()
    try:
        inner = runner.run(
            _start(
                config,
                synchronous=True,
                store=store,
                recipe=recipe,
                measure=measure,
                embed=embed,
                rerank=rerank,
            )
        )
    except BaseException:
        runner.close()
        raise
    return Corpus(inner, runner)

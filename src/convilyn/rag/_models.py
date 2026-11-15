"""The embedding and reranking models a corpus calls: yours or a provider's.

A corpus never imports a connector. It asks for an object of the right shape,
so an embedder from :mod:`convilyn.providers` and one you wrote rank equal: an
object with a ``model`` name and an ``embed(texts, *, kind=...)`` method is an
embedder, and one with ``rerank(query, documents, *, top_n=None)`` is a
reranker. Either method may be synchronous or ``async``; a synchronous one runs
in a worker thread, so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import numbers
import operator
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from convilyn.rag.errors import RagError

#: Whether a text is something to be found or something to find with.
EmbedKind = Literal["document", "query"]


@runtime_checkable
class Embedder(Protocol):
    """What a corpus needs to embed texts. ``providers.embedder(...)`` is one."""

    @property
    def model(self) -> str:
        """The model's name. An index records it, and refuses another model's vectors."""
        ...

    def embed(self, texts: Sequence[str], *, kind: EmbedKind) -> Any:
        """Return one vector per text, in order.

        The result is a sequence of vectors, or an object whose ``vectors``
        attribute is one. It may be awaitable.
        """
        ...


@runtime_checkable
class Reranker(Protocol):
    """What a corpus needs to reorder a shortlist. ``providers.reranker(...)`` is one."""

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None) -> Any:
        """Return the documents' positions with their scores, best first.

        The result is a sequence of items that each have ``index`` and
        ``score`` (or are ``(index, score)`` pairs), or an object whose
        ``ranking`` attribute is one. It may be awaitable.
        """
        ...


async def call(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``method`` without blocking the event loop, and wait for its answer."""
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def is_async(method: object) -> bool:
    """Whether ``method`` is ``async``, so it must run on the caller's own loop."""
    return inspect.iscoroutinefunction(method)


def fell_back(step: str, error: Exception, *, stacklevel: int) -> None:
    """Say that ``step`` failed and the search went on without it.

    Only the kind of error is named, and a provider's category when it has
    one: never the message, which may hold what was sent. ``stacklevel`` is
    counted from the caller, as for :func:`warnings.warn`.
    """
    category = getattr(error, "category", None)
    reason = type(error).__name__ + (f" ({category})" if isinstance(category, str) else "")
    warnings.warn(
        f"rag search: the {step} step failed with {reason} and was skipped",
        RuntimeWarning,
        stacklevel=stacklevel + 1,
    )


def model_name(embedder: Embedder) -> str:
    """The name an index records for ``embedder``'s vectors."""
    name = embedder.model
    if not isinstance(name, str) or not name:
        raise ValueError("an embedder's model must be a non-empty string")
    return name


async def embed(embedder: Embedder, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
    """Return ``embedder``'s vectors for ``texts``, one per text, in order."""
    result = await call(embedder.embed, list(texts), kind=kind)
    vectors = [list(vector) for vector in getattr(result, "vectors", result)]
    if len(vectors) != len(texts):
        raise RagError(
            f"the embedder returned {len(vectors)} vectors for {len(texts)} texts; "
            "it must return one per text"
        )
    return vectors


async def rerank(
    reranker: Reranker, query: str, documents: Sequence[str]
) -> list[tuple[int, float]]:
    """Return ``(position, score)`` for every document ``reranker`` scored, best first.

    An answer that names a position not passed, names one twice, or gives a
    score that is not a finite number is refused: it would attach a score to
    the wrong document.
    """
    result = await call(reranker.rerank, query, list(documents))
    ranked: list[tuple[int, float]] = []
    seen: set[int] = set()
    for item in getattr(result, "ranking", result):
        pair = _pair(item)
        if pair is None or not 0 <= pair[0] < len(documents) or pair[0] in seen:
            raise RagError("the reranker's answer does not score the documents it was given")
        seen.add(pair[0])
        ranked.append(pair)
    return ranked


def _pair(item: object) -> tuple[int, float] | None:
    """``item`` as ``(position, score)``, or ``None`` when it cannot be one.

    An item is a pair, a mapping with ``index`` and ``score``, or an object
    with those attributes. A tuple is always read as a pair: every tuple has
    an ``index`` method. Integers and real numbers of any kind are accepted
    (numpy's included); booleans are not.
    """
    if isinstance(item, tuple):
        raw = item if len(item) == 2 else (None, None)
    elif isinstance(item, Mapping):
        raw = (item.get("index"), item.get("score"))
    else:
        raw = (getattr(item, "index", None), getattr(item, "score", None))
    index, score = raw
    if (
        isinstance(index, bool)
        or isinstance(score, bool)
        or not isinstance(index, numbers.Integral)
        or not isinstance(score, numbers.Real)
    ):
        return None
    try:
        value = float(score)
    except OverflowError:
        return None
    return (operator.index(index), value) if math.isfinite(value) else None

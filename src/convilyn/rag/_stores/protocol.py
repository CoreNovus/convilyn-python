"""What a store has to do.

A store keeps passages and answers searches over them. It does not cut
documents, fold text or rank across kinds of search: those happen once, in this
SDK, so that the same settings give comparable results on every store.

Every method that may wait on a disk or a network is ``async``. A store whose
engine is blocking runs it off the event loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from convilyn.rag.types import (
    Condition,
    Hit,
    IndexEntry,
    IndexIdentity,
    SourceInfo,
    StoreInfo,
)


@runtime_checkable
class Store(Protocol):
    """The interface every store implements, and the one to implement for your own."""

    def describe(self) -> StoreInfo:
        """Say what this store is and how it matches text."""
        ...

    async def prepare(self, identity: IndexIdentity) -> None:
        """Make the store ready, creating an empty index if there is none.

        Raises :class:`~convilyn.rag.errors.StoreError` when an index already
        exists and was built with a different ``identity``.
        """
        ...

    async def write(self, source: SourceInfo, entries: Sequence[IndexEntry]) -> None:
        """Store ``entries`` as the whole of ``source``, replacing what it held.

        All or nothing: a search never sees a document half replaced.
        """
        ...

    async def remove(self, source_id: str) -> bool:
        """Drop a document and its passages. Returns whether it was there."""
        ...

    async def sources(self) -> tuple[SourceInfo, ...]:
        """List the documents held, ordered by ``source_id``."""
        ...

    async def vectors(self, source_id: str) -> dict[str, tuple[float, ...]]:
        """Return the stored vectors of ``source_id``'s passages, by passage ``digest``.

        A passage stored without a vector is left out, and so is everything
        when the document is not held or the index holds no vectors. A corpus
        reads this to avoid embedding again a passage whose text is unchanged.
        """
        ...

    async def lexical(
        self, query: str, *, limit: int, where: Sequence[Condition] = ()
    ) -> tuple[Hit, ...]:
        """Return up to ``limit`` passages matching the words of ``query``, best first.

        ``query`` has already been folded the way the passages were. Only
        documents for which every condition in ``where`` holds are considered.
        A ``limit`` that is not a positive integer raises ``ValueError``.
        """
        ...

    async def dense(
        self, vector: Sequence[float], *, limit: int, where: Sequence[Condition] = ()
    ) -> tuple[Hit, ...]:
        """Return up to ``limit`` passages whose vectors are closest to ``vector``, best first.

        The store scales ``vector`` to length 1, as the stored ones already
        are (``IndexEntry`` scales them), so a hit's score is the cosine
        similarity. A passage stored without a vector is never returned.
        ``where`` is honoured as in :meth:`lexical`. A ``vector`` of another
        size than the index's ``dimensions`` or with no direction, a ``limit``
        that is not a positive integer, or a search of an index that holds no
        vectors, raises ``ValueError``.
        """
        ...

    async def close(self) -> None:
        """Release what the store holds. Safe to call more than once."""
        ...

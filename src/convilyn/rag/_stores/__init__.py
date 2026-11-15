"""The stores this SDK ships, by the key they are created with.

Adding a store is adding a row here: a class that satisfies
:class:`~convilyn.rag._stores.protocol.Store`, in a module of its own, that
passes the shared contract tests.
"""

from __future__ import annotations

from collections.abc import Callable
from reprlib import repr as shown

from convilyn.rag._stores import sqlite
from convilyn.rag._stores.protocol import Store
from convilyn.rag.types import StoreInfo

_FACTORIES: dict[str, Callable[..., Store]] = {sqlite.KIND: sqlite.SqliteStore}


def store(kind: str, /, **options: object) -> Store:
    """Create a store of ``kind``. ``options`` are that store's own.

    ``rag.store("sqlite")`` keeps the index in memory; ``rag.store("sqlite",
    path="kb.sqlite3")`` keeps it in a file. :func:`stores` lists the kinds.
    """
    if kind not in _FACTORIES:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown store {shown(kind)}; choose one of: {known}")
    return _FACTORIES[kind](**options)


def stores() -> tuple[StoreInfo, ...]:
    """Describe every kind of store :func:`store` can create."""
    return tuple(_FACTORIES[kind]().describe() for kind in sorted(_FACTORIES))


__all__ = ["Store", "store", "stores"]

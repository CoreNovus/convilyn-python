"""Errors raised by the retrieval toolkit.

A mistake in an argument raises a built-in ``ValueError`` or ``TypeError``, as
everywhere else in this SDK. The classes here are for what goes wrong at run
time, and their messages are fixed strings: they never carry the text of a
document, a query, or a path.
"""

from __future__ import annotations

from convilyn.exceptions import ConvilynError


class RagError(ConvilynError):
    """Base class for every error :mod:`convilyn.rag` raises at run time."""


class StoreError(RagError):
    """A store could not do what was asked.

    Raised when an index was built in a way the current settings do not match
    and has to be rebuilt, when the store has been closed, when a package the
    search needs is not installed (vector search in the SQLite store needs
    numpy, from the ``convilyn[rag]`` extra), and when the storage engine
    itself fails.
    """


__all__ = ["RagError", "StoreError"]

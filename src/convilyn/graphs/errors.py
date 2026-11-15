"""Errors raised while a graph runs.

A mistake in an argument raises a built-in ``ValueError`` or ``TypeError``, as
everywhere else in this SDK. The class here is for what goes wrong at run time,
and its messages are fixed strings: they never carry the text of a question, a
draft, or a document.
"""

from __future__ import annotations

from convilyn.exceptions import ConvilynError


class GraphError(ConvilynError):
    """A function the graph was built with returned something it cannot use."""


__all__ = ["GraphError"]

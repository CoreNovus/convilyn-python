"""What every wire format has to supply, as data.

A format is four functions: where the request goes, how the key is sent, what
the body is, and how the answer is read. The connectors call these and know
nothing else about a format, so supporting another one touches no connector.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

#: How a model is told a document from a query: in a field of the request, or
#: in words put before the text. ``None`` for a model that is not told.
KindStyle = Literal["field", "prefix"] | None


@dataclass(frozen=True)
class EmbedWire:
    """An embeddings format."""

    #: The address of a request, from the provider's base address and the model.
    url: Callable[[str, str], str]
    #: The headers that carry the key.
    headers: Callable[[str], dict[str, str]]
    #: The JSON body for one batch:
    #: ``body(batch, model=, dimensions=, kind=, kind_style=)``.
    body: Callable[..., dict[str, Any]]
    #: The vectors of an answer in request order, the model it names and the
    #: tokens it counted: ``read(payload, expected=)``.
    read: Callable[..., tuple[list[list[float]], str | None, int | None]]


@dataclass(frozen=True)
class RerankWire:
    """A reranking format."""

    #: The address of a request, from the provider's base address and the model.
    url: Callable[[str, str], str]
    #: The headers that carry the key.
    headers: Callable[[str], dict[str, str]]
    #: The JSON body for one batch of documents: ``body(batch, model=, query=)``.
    body: Callable[..., dict[str, Any]]
    #: The score of every document of the batch, by its position in the batch:
    #: ``read(payload, expected=)``.
    read: Callable[..., list[float]]


def bearer(key: str) -> dict[str, str]:
    """The key as a bearer token, the way most formats send it."""
    return {"Authorization": f"Bearer {key}"}


def is_vector(value: object) -> TypeGuard[list[float]]:
    """Whether ``value`` is a non-empty JSON list of numbers."""
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(type(number) in (float, int) for number in value)
    )

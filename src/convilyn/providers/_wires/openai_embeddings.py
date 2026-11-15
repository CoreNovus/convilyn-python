"""The OpenAI embeddings format: what is sent and what comes back.

Spoken by OpenAI itself and by the many services and self-hosted servers that
copy it. This module knows the shape of the request and the response and
nothing about who is on the other end.
"""

from __future__ import annotations

from typing import Any

from convilyn.providers._steps import Batch, failure
from convilyn.providers._wires.shapes import EmbedWire, KindStyle, bearer, is_vector
from convilyn.providers.types import EmbedKind

#: Appended to the base URL.
PATH = "/embeddings"

#: A model name or a token count outside these is not believed: the answer
#: comes from a server, and nothing downstream should have to doubt them.
_MAX_MODEL_NAME = 256
_MAX_TOKEN_COUNT = 2**53

#: The value sent for each kind of text, to models that tell them apart.
_INPUT_TYPES: dict[EmbedKind, str] = {"document": "search_document", "query": "search_query"}


def url(base: str, model: str) -> str:
    """Return where a request goes. The model travels in the body."""
    return base + PATH


def body(
    batch: Batch, *, model: str, dimensions: int | None, kind: EmbedKind, kind_style: KindStyle
) -> dict[str, Any]:
    """Build the JSON body for ``batch``."""
    payload: dict[str, Any] = {
        "model": model,
        "input": list(batch.texts),
        "encoding_format": "float",
    }
    if dimensions is not None:
        payload["dimensions"] = dimensions
    if kind_style == "field":
        payload["input_type"] = _INPUT_TYPES[kind]
    return payload


def read(payload: object, *, expected: int) -> tuple[list[list[float]], str | None, int | None]:
    """Return the vectors of a response in request order, its model and its token count.

    The format lets a server return the items in any order, each carrying the
    index of the text it answers. Anything that does not account for exactly
    the texts sent, once each, is refused rather than guessed at.
    """
    if not isinstance(payload, dict):
        raise failure("bad_response")
    items = payload.get("data")
    if not isinstance(items, list) or len(items) != expected:
        raise failure("bad_response")

    ordered: list[list[float] | None] = [None] * expected
    for item in items:
        if not isinstance(item, dict):
            raise failure("bad_response")
        index, vector = item.get("index"), item.get("embedding")
        if (
            type(index) is not int
            or not 0 <= index < expected
            or ordered[index] is not None
            or not is_vector(vector)
        ):
            raise failure("bad_response")
        ordered[index] = vector

    model = payload.get("model")
    usage = payload.get("usage")
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return (
        [vector for vector in ordered if vector is not None],
        model if isinstance(model, str) and len(model) <= _MAX_MODEL_NAME else None,
        tokens if type(tokens) is int and 0 <= tokens <= _MAX_TOKEN_COUNT else None,
    )


WIRE = EmbedWire(url=url, headers=bearer, body=body, read=read)

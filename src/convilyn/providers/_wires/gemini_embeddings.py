"""The Gemini API's own embeddings format: what is sent and what comes back.

One request carries one small request per text, and the answer lists one
embedding per text in the same order. Read from the API's machine-readable
description (``BatchEmbedContentsRequest`` and ``BatchEmbedContentsResponse``)
and from the embeddings guide; the provider's row names both.
"""

from __future__ import annotations

import re
from typing import Any

from convilyn.providers._steps import Batch, failure
from convilyn.providers._wires.shapes import EmbedWire, KindStyle, is_vector
from convilyn.providers.types import EmbedKind

#: The model is part of the address, so it is held to the characters a model
#: name is made of, and to a name's length: nothing in it may be read as
#: another path, or make an address too long to be one.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")

#: A token count outside this is not believed.
_MAX_TOKEN_COUNT = 2**53

#: The task named in the request, for models that take one.
_TASK_TYPES: dict[EmbedKind, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}

#: The words put before the text, for models that are told the task that way.
#: The guide's forms for search: a document with no title, and a query.
_PREFIXES: dict[EmbedKind, str] = {
    "document": "title: none | text: ",
    "query": "task: search result | query: ",
}


def url(base: str, model: str) -> str:
    """Return where a request for ``model`` goes."""
    if _MODEL_NAME.fullmatch(model) is None:
        raise ValueError(
            "model must be the model's name alone, at most 256 letters, digits, '.', '_' and '-' "
            '(for example "gemini-embedding-001")'
        )
    return f"{base}/models/{model}:batchEmbedContents"


def headers(key: str) -> dict[str, str]:
    """Return the header that carries the key. It never goes in the address."""
    return {"x-goog-api-key": key}


def body(
    batch: Batch, *, model: str, dimensions: int | None, kind: EmbedKind, kind_style: KindStyle
) -> dict[str, Any]:
    """Build the JSON body for ``batch``.

    The task and the size are written where the guide's examples write them,
    beside the content. The API's description also offers a nested
    ``embedContentConfig`` for them and marks these as the older spelling.
    """
    shared: dict[str, Any] = {"model": f"models/{model}"}
    if kind_style == "field":
        shared["taskType"] = _TASK_TYPES[kind]
    if dimensions is not None:
        shared["outputDimensionality"] = dimensions
    before = _PREFIXES[kind] if kind_style == "prefix" else ""
    return {
        "requests": [
            {**shared, "content": {"parts": [{"text": before + text}]}} for text in batch.texts
        ]
    }


def read(payload: object, *, expected: int) -> tuple[list[list[float]], str | None, int | None]:
    """Return the vectors of a response in request order and its token count.

    The answer carries no positions: the embeddings are in the order of the
    requests. One that does not hold exactly as many as were asked for cannot
    be matched to the texts, and is refused. It names no model.
    """
    if not isinstance(payload, dict):
        raise failure("bad_response")
    items = payload.get("embeddings")
    if not isinstance(items, list) or len(items) != expected:
        raise failure("bad_response")

    vectors: list[list[float]] = []
    for item in items:
        vector = item.get("values") if isinstance(item, dict) else None
        if not is_vector(vector):
            raise failure("bad_response")
        vectors.append(vector)

    usage = payload.get("usageMetadata")
    tokens = usage.get("promptTokenCount") if isinstance(usage, dict) else None
    return (
        vectors,
        None,
        tokens if type(tokens) is int and 0 <= tokens <= _MAX_TOKEN_COUNT else None,
    )


WIRE = EmbedWire(url=url, headers=headers, body=body, read=read)

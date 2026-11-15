"""The Cohere rerank format: what is sent and what comes back.

A query and a list of documents go in; a score for each document comes back,
each naming the position of the document it is for. Spoken by Cohere and by
services that copy it.
"""

from __future__ import annotations

import math
from typing import Any

from convilyn.providers._steps import Batch, failure
from convilyn.providers._wires.shapes import RerankWire, bearer

#: Appended to the base URL.
PATH = "/rerank"


def url(base: str, model: str) -> str:
    """Return where a request goes. The model travels in the body."""
    return base + PATH


def body(batch: Batch, *, model: str, query: str) -> dict[str, Any]:
    """Build the JSON body for the documents of ``batch``.

    Every document is asked for by number, rather than left to a default that
    differs between services: the caller's own cut is made after the answers
    of all requests are put together.
    """
    return {
        "model": model,
        "query": query,
        "documents": list(batch.texts),
        "top_n": len(batch.texts),
    }


def read(payload: object, *, expected: int) -> list[float]:
    """Return the score of every document of the request, by its position.

    An answer that scores a position that was not sent, scores one twice,
    leaves one out, or gives a score that is not a finite number, would attach
    a score to the wrong document or drop one quietly. It is refused.
    """
    if not isinstance(payload, dict):
        raise failure("bad_response")
    items = payload.get("results")
    if not isinstance(items, list) or len(items) != expected:
        raise failure("bad_response")

    scores: list[float | None] = [None] * expected
    for item in items:
        if not isinstance(item, dict):
            raise failure("bad_response")
        index, score = item.get("index"), _score(item.get("relevance_score"))
        if (
            type(index) is not int
            or not 0 <= index < expected
            or scores[index] is not None
            or score is None
        ):
            raise failure("bad_response")
        scores[index] = score
    return [score for score in scores if score is not None]


def _score(value: object) -> float | None:
    """Return ``value`` as a number that can be compared, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


WIRE = RerankWire(url=url, headers=bearer, body=body, read=read)

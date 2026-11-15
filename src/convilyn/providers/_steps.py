"""The fixed sequence every call goes through, minus the sending.

Check the inputs, deal with texts that are too long, cut the list into
requests, map a refusal to an error, scale the vectors. Each step is a pure
function, so the synchronous and asynchronous connectors share all of them and
differ only in how they wait. An embedder uses every step; a reranker uses the
ones that apply to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from convilyn._internal import vectors
from convilyn.providers.errors import ErrorCategory, ProviderError
from convilyn.providers.types import OverflowPolicy
from convilyn.rag import Measure


@dataclass(frozen=True)
class Batch:
    """The texts of one request, and where they sit in the caller's list."""

    start: int
    texts: tuple[str, ...]


def check_texts(texts: Sequence[str], noun: str = "text") -> tuple[str, ...]:
    """Return ``texts`` as a tuple, refusing what no provider can take.

    ``noun`` is what the caller knows them as, for the messages.
    """
    if isinstance(texts, str):
        raise TypeError(f"{noun}s must be a sequence of strings, not one string; pass [{noun}]")
    checked = tuple(texts)
    if not checked:
        raise ValueError(f"{noun}s must not be empty")
    for index, text in enumerate(checked):
        if not isinstance(text, str):
            raise TypeError(f"{noun} {index} must be a string, got {type(text).__name__}")
        if not text:
            raise ValueError(f"{noun} {index} is empty; a provider cannot take an empty {noun}")
    return checked


def fit(
    texts: tuple[str, ...], *, limit: int | None, measure: Measure | None, policy: OverflowPolicy
) -> tuple[str, ...]:
    """Apply ``policy`` to every text ``measure`` puts over ``limit``.

    With no limit or no measure nothing can be checked, and the texts go as
    they are: the provider then decides, and its refusal is reported.
    """
    if limit is None or measure is None:
        return texts
    fitted: list[str] = []
    for index, text in enumerate(texts):
        size = measure(text)
        if size <= limit:
            fitted.append(text)
        elif policy == "truncate":
            fitted.append(_longest_prefix(text, limit, measure))
        else:
            raise ValueError(
                f"text {index} measures {size} tokens and the limit is {limit}; shorten it, "
                f'or pass on_overflow="truncate" to send only its beginning'
            )
    return tuple(fitted)


def _longest_prefix(text: str, limit: int, measure: Measure) -> str:
    """Return the longest beginning of ``text`` that ``measure`` puts within ``limit``."""
    low, high = 1, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def batches(texts: tuple[str, ...], size: int) -> list[Batch]:
    """Cut ``texts`` into requests of at most ``size`` texts, keeping their order."""
    return [
        Batch(start=start, texts=texts[start : start + size])
        for start in range(0, len(texts), size)
    ]


#: The fixed sentence for each kind of failure. Nothing from the response, the
#: request or the configuration is ever added to these.
_MESSAGES: dict[ErrorCategory, str] = {
    "auth": "the provider refused the key; check that it is valid and has access to this model",
    "rate_limited": "the provider is limiting requests and retries ran out; retry later",
    "invalid_request": (
        "the provider rejected the request; a text longer than the model accepts, a model "
        "name it does not know and a parameter the model does not take are the usual causes"
    ),
    "unavailable": "the provider failed and retries ran out; retry later",
    "connection": (
        "the request to the provider did not complete or timed out; check the network and base_url"
    ),
    "bad_response": "the provider answered with something that is not a usable response",
}

_AUTH_STATUSES = frozenset({401, 403})
_RATE_STATUSES = frozenset({429})


def failure(category: ErrorCategory, status: int | None = None) -> ProviderError:
    """Build the error for ``category``."""
    return ProviderError(_MESSAGES[category], category=category, status=status)


def refusal(status: int) -> ProviderError:
    """Build the error for an HTTP status that is not a success."""
    if status in _AUTH_STATUSES:
        return failure("auth", status)
    if status in _RATE_STATUSES:
        return failure("rate_limited", status)
    if status >= 500 or status == 408:
        return failure("unavailable", status)
    return failure("invalid_request", status)


def unit(vector: Sequence[float]) -> tuple[float, ...]:
    """Scale ``vector`` to length 1, so a dot product is a cosine similarity.

    A vector of zeros has no direction and one with a non-finite number has no
    length; both mean the provider returned something unusable.
    """
    scaled: tuple[float, ...] = ()
    try:
        scaled = vectors.unit(vector)
    except ValueError:
        pass
    # Raised out here, so that the scaling's own error is not kept on this one.
    if not scaled:
        raise failure("bad_response")
    return scaled

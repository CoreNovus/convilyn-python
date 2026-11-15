"""Errors raised when a provider call fails.

A mistake in an argument raises a built-in ``ValueError`` or ``TypeError``, as
everywhere else in this SDK. :class:`ProviderError` is for what goes wrong
between this process and the service. Its message is a fixed sentence chosen by
``category``: it never carries the provider's response body, the text that was
sent, the URL, or the key.
"""

from __future__ import annotations

from typing import Literal

from convilyn.exceptions import ConvilynError

#: What kind of failure it was, so code can decide what to do without parsing
#: a message.
#:
#: * ``"auth"`` — the key was refused, or lacks access
#: * ``"rate_limited"`` — too many requests, and retries ran out
#: * ``"invalid_request"`` — the provider rejected what was sent
#: * ``"unavailable"`` — the provider failed, and retries ran out
#: * ``"connection"`` — the request never got an answer, or timed out
#: * ``"bad_response"`` — the provider answered with something unusable
ErrorCategory = Literal[
    "auth", "rate_limited", "invalid_request", "unavailable", "connection", "bad_response"
]


class ProviderError(ConvilynError):
    """A call to a provider failed.

    ``category`` says how; ``status`` is the HTTP status when there was one.
    """

    def __init__(self, message: str, *, category: ErrorCategory, status: int | None = None) -> None:
        super().__init__(message)
        self.category: ErrorCategory = category
        self.status = status


__all__ = ["ErrorCategory", "ProviderError"]

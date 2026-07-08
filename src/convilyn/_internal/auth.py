"""Authentication strategies for the Convilyn SDK.

The SDK is decoupled from any specific credential format via the
``AuthStrategy`` protocol. Today only API-key auth is wired up; future
releases may add ``JWTBearer``, ``OAuth``, etc. without breaking the
public client surface.

Key prefixes: ``ck_`` is the canonical **consumer** API key — minted in the
API Console (Settings → API); see the backend ``USER_API_KEY_PREFIX``. The
developer-portal tiers ``cvl_`` / ``cvi_`` belong to the **Author SDK**
(publishing workflows / tools), not the consumer data-plane — pasting one here
is a mistake the SDK rejects up front with a precise error (mirroring the
TypeScript consumer SDK's ``auth.ts``), instead of letting the API answer with
an opaque 401. Any *other* prefix is still accepted (forward-compat: a new
backend consumer-key tier never breaks an existing client).
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from convilyn.exceptions import AuthError

ENV_API_KEY = "CONVILYN_API_KEY"  # pragma: allowlist secret

#: The canonical consumer API-key prefix the backend mints today
#: (``app/core/security/api_key.py`` ``USER_API_KEY_PREFIX``).
CONSUMER_KEY_PREFIX = "ck_"  # pragma: allowlist secret

#: Author-SDK / developer-portal token prefixes — **not** consumer keys. The
#: consumer SDK recognises them only to raise a precise :class:`AuthError` when
#: one is pasted here by mistake.
AUTHOR_KEY_PREFIXES: tuple[str, ...] = ("cvl_", "cvi_")  # pragma: allowlist secret

#: Retained for back-compat; the recognised consumer prefix(es).
ACCEPTED_KEY_PREFIXES: tuple[str, ...] = (CONSUMER_KEY_PREFIX,)


def is_author_key(key: str) -> bool:
    """True when ``key`` is an Author-SDK / developer-portal token, not a consumer key."""
    return any(key.startswith(p) for p in AUTHOR_KEY_PREFIXES)


def _mask_key(key: str) -> str:
    """Mask a secret for safe display: ``ck_a…9f`` (or ``***`` when too short)."""
    return f"{key[:4]}…{key[-2:]}" if len(key) > 8 else "***"


@runtime_checkable
class AuthStrategy(Protocol):
    """Pluggable credential carrier.

    Implementations build the per-request HTTP headers that authorize a
    call. They MUST be cheap to invoke; ``headers()`` may be called once
    per request.

    ``bearer_token()`` exposes the same credential as a raw string for
    non-header transports — WebSocket connections in particular pass the
    token via the ``?token=`` query parameter, so headers aren't an option.
    Implementations return the same secret material that would appear in
    the ``Authorization: Bearer …`` header.
    """

    def headers(self) -> dict[str, str]: ...

    def bearer_token(self) -> str: ...


class APIKey:
    """Static API-key auth — sends ``Authorization: Bearer <key>``.

    The key must be a non-empty string; prefix validation is advisory only
    (a warning is emitted when the prefix is not recognised, but the
    request is still made — backends introduce new prefixes faster than
    SDK releases).
    """

    __slots__ = ("_key",)

    def __init__(self, key: str) -> None:
        if not key or not isinstance(key, str):
            raise AuthError("API key must be a non-empty string")
        if is_author_key(key):
            raise AuthError(
                f"{_mask_key(key)} looks like a Convilyn Author SDK / developer-portal "
                f'token (cvl_/cvi_), not a consumer API key. The consumer SDK '
                f'authenticates with a "{CONSUMER_KEY_PREFIX}" key — mint one under '
                f"Settings → API. (Author tokens publish workflows / tools; they do "
                f"not call the data-plane API.)"
            )
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def bearer_token(self) -> str:
        return self._key

    def __repr__(self) -> str:
        # Don't leak the key in logs.
        masked = f"{self._key[:6]}…" if len(self._key) > 6 else "…"
        return f"APIKey({masked!r})"


def resolve_auth(
    api_key: str | None,
    *,
    env: dict[str, str] | None = None,
) -> AuthStrategy:
    """Resolve an ``AuthStrategy`` from explicit args + environment.

    Precedence:
        1. ``api_key`` constructor argument
        2. ``CONVILYN_API_KEY`` environment variable

    Raises:
        AuthError: neither source supplied a credential.
    """
    source = env if env is not None else os.environ
    key = api_key or source.get(ENV_API_KEY)
    if not key:
        raise AuthError(
            "No API key supplied. Pass api_key=... or set the "
            f"{ENV_API_KEY} environment variable."
        )
    return APIKey(key)


def is_known_prefix(key: str) -> bool:
    """Check whether ``key`` starts with a known Convilyn prefix.

    Returned for diagnostic logging only — unknown prefixes are NOT
    rejected, because new backend key tiers may roll out before an SDK
    release acknowledges them.
    """
    return any(key.startswith(p) for p in ACCEPTED_KEY_PREFIXES)

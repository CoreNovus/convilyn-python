"""Reaching a provider: where a request goes, under which key, and the sending.

Shared by every connector, so that the rules about the key and the address are
written once. A connector resolves a :class:`Connection` when it is created and
hands requests to a :class:`Sender` or an :class:`AsyncSender`; those two differ
only in how they wait.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from reprlib import repr as shown
from typing import Any

import httpx

from convilyn._internal.resilience import ExponentialBackoffRetry, RetryPolicy
from convilyn.providers import _steps
from convilyn.providers._profiles import PROFILES
from convilyn.providers.types import ProviderInfo

#: Seconds a request may take when the connector makes its own HTTP client.
DEFAULT_TIMEOUT = 60.0

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Connection:
    """Where one connector's requests go, and how they are sent."""

    url: str
    headers: dict[str, str] = field(repr=False)
    retry: RetryPolicy
    timeout: float

    @property
    def trusts_environment(self) -> bool:
        """Whether a client made here may take a proxy from the environment.

        Not for plain http, which is only ever this machine: a proxy named in
        the environment would be handed the key and the text unencrypted.
        """
        return httpx.URL(self.url).scheme == "https"


def row(name: str, offers: Callable[[ProviderInfo], object], work: str) -> ProviderInfo:
    """Return the row of the provider ``name``, which has to offer ``work``."""
    if name not in PROFILES or not offers(PROFILES[name]):
        known = ", ".join(sorted(key for key, info in PROFILES.items() if offers(info)))
        raise ValueError(f"no provider {shown(name)} for {work}; choose one of: {known}")
    return PROFILES[name]


def check_model(model: str) -> None:
    """Refuse a model that is not a name."""
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")


def check_limit(label: str, value: int | None) -> None:
    """Refuse a limit that is given and is not a positive whole number."""
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"{label} must be a positive integer")


def connection(
    info: ProviderInfo,
    *,
    url: Callable[[str], str],
    headers: Callable[[str], dict[str, str]],
    api_key: str | None,
    base_url: str | None,
    retry: RetryPolicy | None,
    timeout: float,
    has_client: bool,
) -> Connection:
    """Check the address and the key of a factory call, and settle both.

    ``url`` turns the base address into the address of a request, and
    ``headers`` turns the key into the headers that carry it; both come from
    the provider's wire format.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")

    base = base_url or info.base_url
    if base is None:
        raise ValueError(f'provider "{info.name}" has no address of its own; pass base_url=')
    _require_private_channel(base)

    key = _checked_key(api_key) or _checked_key(
        os.environ.get(info.key_env) if info.key_env else None
    )
    if key is None and info.key_env is not None and not has_client:
        raise ValueError(
            f'no API key for provider "{info.name}": pass api_key=, set {info.key_env}, '
            f"or pass an http_client that authenticates on its own"
        )
    return Connection(
        url=url(base.rstrip("/")),
        headers={} if key is None else headers(key),
        retry=retry or ExponentialBackoffRetry(),
        timeout=timeout,
    )


def _checked_key(raw: str | None) -> str | None:
    """Return the key ready to send, or ``None`` when there is none.

    A key read from a file often ends in a newline, so surrounding whitespace is
    dropped. Anything else that is not a visible ASCII character is refused
    here, without showing the key: further down, the HTTP library would refuse
    it too and name the whole value in its error.
    """
    key = (raw or "").strip()
    if not key:
        return None
    if not all("!" <= character <= "~" for character in key):
        raise ValueError(
            "the API key contains a space, a control character or a non-ASCII character; "
            "check where it is read from"
        )
    return key


def _require_private_channel(base_url: str) -> None:
    """Refuse an address the key and the texts would travel to in the clear.

    Read with the HTTP library's own parser, so that the host checked here is
    the host the request goes to. No message repeats the address, which may
    hold credentials.
    """
    scheme = host = userinfo = None
    readable = isinstance(base_url, str)
    if readable:
        try:
            address = httpx.URL(base_url)
            # The host is decoded when it is read, and a name that cannot be
            # decoded fails there, with an error that repeats part of it.
            scheme, host, userinfo = address.scheme, address.host, address.userinfo
        except (httpx.InvalidURL, ValueError):
            readable = False
    if not readable:
        # Raised here rather than inside the handler, so that the parser's
        # error, which holds part of the address, is not kept on this one.
        raise ValueError("base_url is not a valid address")
    if "?" in base_url or "#" in base_url:
        raise ValueError("base_url must not contain a query or a fragment")
    encrypted = scheme == "https" and bool(host)
    this_machine = scheme == "http" and host in _LOOPBACK_HOSTS
    if not (encrypted or this_machine):
        raise ValueError(
            "base_url must use https: the key and your text would otherwise be sent "
            "unencrypted (http is accepted only for this machine: localhost, 127.0.0.1 or ::1)"
        )
    if userinfo:
        # The HTTP library would send these instead of the key, and name them
        # in its own request log.
        raise ValueError(
            "base_url must not contain a user name or password; pass api_key=, or an "
            "http_client that authenticates on its own"
        )


def decoded(response: httpx.Response) -> object:
    """Return the JSON a successful response carries, or refuse it."""
    payload: object = None
    readable = True
    try:
        payload = response.json()
    except (ValueError, RecursionError):
        readable = False
    if not readable:
        # Raised here rather than inside the handler, so that the parser's
        # error, which holds the whole reply, is not kept on this one.
        raise _steps.failure("bad_response", response.status_code)
    return payload


def _verdict(
    retry: RetryPolicy, response: httpx.Response | None, error: httpx.HTTPError | None, attempt: int
) -> float:
    """Return how long to wait before trying again, or raise what went wrong."""
    if retry.should_retry(response, error, attempt):
        return retry.next_delay(response, attempt)
    if response is None:
        # The HTTP library's error is not attached: it holds the request,
        # and with it the key.
        raise _steps.failure("connection")
    raise _steps.refusal(response.status_code)


class Sender:
    """Sends one connector's requests and waits for each answer."""

    def __init__(self, connection: Connection, http_client: httpx.Client | None) -> None:
        self._connection = connection
        self._client = http_client
        self._owns_client = http_client is None
        self._making_client = threading.Lock()

    def send(self, body: dict[str, Any]) -> httpx.Response:
        """Return the successful answer to ``body``, retrying as the policy says."""
        connection = self._connection
        with self._making_client:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=connection.timeout, trust_env=connection.trusts_environment
                )
            client = self._client
        attempt = 0
        while True:
            response: httpx.Response | None = None
            failure: httpx.HTTPError | None = None
            try:
                # Never followed, whatever the client's own setting: a redirect
                # would send the text to an address the caller did not name.
                response = client.post(
                    connection.url, json=body, headers=connection.headers, follow_redirects=False
                )
            except httpx.HTTPError as error:
                failure = error
            if response is not None and response.is_success:
                return response
            time.sleep(_verdict(connection.retry, response, failure, attempt))
            attempt += 1

    def close(self) -> None:
        """Close the HTTP client, if it was made here. Safe to call more than once."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


class AsyncSender:
    """:class:`Sender` for async code."""

    def __init__(self, connection: Connection, http_client: httpx.AsyncClient | None) -> None:
        self._connection = connection
        self._client = http_client
        self._owns_client = http_client is None

    async def send(self, body: dict[str, Any]) -> httpx.Response:
        """Return the successful answer to ``body``, retrying as the policy says."""
        connection = self._connection
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=connection.timeout, trust_env=connection.trusts_environment
            )
        client = self._client
        attempt = 0
        while True:
            response: httpx.Response | None = None
            failure: httpx.HTTPError | None = None
            try:
                response = await client.post(
                    connection.url, json=body, headers=connection.headers, follow_redirects=False
                )
            except httpx.HTTPError as error:
                failure = error
            if response is not None and response.is_success:
                return response
            await asyncio.sleep(_verdict(connection.retry, response, failure, attempt))
            attempt += 1

    async def aclose(self) -> None:
        """Close the HTTP client, if it was made here. Safe to call more than once."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

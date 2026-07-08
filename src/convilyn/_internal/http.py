"""HTTP transport for the Convilyn SDK.

Thin wrapper around ``httpx.AsyncClient`` that:

* injects auth + standard headers (``User-Agent``, ``Accept``);
* normalises the Convilyn error envelope ``{code, message, details}``
  into typed exceptions (``APIError`` / ``RateLimitError``);
* applies the injected :class:`RetryPolicy` on transient 5xx / 429 / 408
  responses, reusing the same ``Idempotency-Key`` across retries;
* auto-stamps an ``Idempotency-Key`` header on mutating verbs so the
  policy can safely retry without the caller worrying about duplicates
  (the backend may not honour it today — this is forward-compat wiring);
* leaves connection-pool management, redirects, and timeouts to httpx.

Resilience policy itself lives in :mod:`convilyn._internal.resilience`
to keep the SOLID DIP seam: HTTPClient depends on the abstract
``RetryPolicy`` Protocol, not the concrete
:class:`ExponentialBackoffRetry` implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from convilyn._internal.auth import AuthStrategy
from convilyn._internal.resilience import (
    MUTATING_METHODS,
    ExponentialBackoffRetry,
    RetryPolicy,
    generate_idempotency_key,
)
from convilyn._internal.throttle import (
    AutoThrottleConfig,
    compute_quota_sleep,
    emit_soft_limit_warning,
    response_has_soft_limit_header,
)
from convilyn._internal.urlpolicy import is_safe_url
from convilyn._version import __version__
from convilyn.exceptions import (
    APIError,
    PlanRequiredError,
    QuotaExceededError,
    RateLimitError,
    RetryExhaustedError,
    S3UploadError,
)

# Backend emits any of these on 402 to signal a plan-tier mismatch.
# Listed here so adding a new tier (e.g. "ENTERPRISE_TIER_REQUIRED") is
# one-line + does not affect any other dispatch — OCP-honoured.
_PLAN_REQUIRED_CODES: frozenset[str] = frozenset(
    {
        "TIER_REQUIRED",
        "PRO_TIER_REQUIRED",
        "BUSINESS_TIER_REQUIRED",
        "ENTERPRISE_TIER_REQUIRED",
    }
)

DEFAULT_BASE_URL = "https://api.convilyn.corenovus.com"
DEFAULT_TIMEOUT = 30.0
ENV_BASE_URL = "CONVILYN_BASE_URL"

# Hard ceiling on a single streamed download (2 GiB). Guards against a
# malformed or hostile response exhausting memory/disk; well above any
# real conversion output.
DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


def resolve_base_url(
    base_url: str | None,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the API base URL from explicit arg + environment.

    Precedence (first non-empty wins):

    1. ``base_url`` constructor argument
    2. ``CONVILYN_BASE_URL`` environment variable
    3. :data:`DEFAULT_BASE_URL`

    Mirrors :func:`convilyn._internal.auth.resolve_auth`. The ``env``
    parameter is an injectable seam for tests (defaults to ``os.environ``).
    The base URL is the host root — resource paths carry their own
    ``/api/v1`` prefix, so do NOT include it here.
    """
    source = env if env is not None else os.environ
    resolved = base_url or source.get(ENV_BASE_URL) or DEFAULT_BASE_URL
    _require_https_base_url(resolved)
    return resolved


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_https_base_url(base_url: str) -> None:
    """Reject an ``http://`` API base URL that isn't a loopback dev target.

    The API key travels in an ``Authorization`` header on every request;
    over plain http it would be sent in cleartext. https is required for
    any non-loopback host. localhost / 127.0.0.1 / ::1 are allowed so a
    local dev server (which has no cert) still works.

    Raises:
        ValueError: a non-loopback base URL uses a scheme other than https.
    """
    parts = urlsplit(base_url)
    if parts.scheme.lower() == "https":
        return
    if (parts.hostname or "").lower() in _LOOPBACK_HOSTS:
        return
    raise ValueError(
        f"Refusing an insecure base_url (scheme={parts.scheme!r}): the API key "
        "would be sent in cleartext. Use https:// (loopback hosts may use http)."
    )


def _require_safe_external_url(url: str) -> None:
    """Guard an externally-supplied absolute URL before the SDK dials it.

    ``external_put`` / ``external_post_form`` / ``external_get`` dial the
    storage URLs the service hands back. Those are treated as untrusted:
    a malformed or tampered response must never make the client open a
    connection to a downgraded, local, or internal address. Two checks:

    1. Scheme must be ``https`` — blocks ``http://`` downgrade and
       ``file://`` local reads.
    2. The host must resolve only to public addresses — blocks loopback,
       link-local, and private-range targets (SSRF / file-exfiltration).

    Any legitimate public https URL still passes, so no functionality is
    lost.

    Raises:
        ValueError: the scheme is not https, or the host resolves to a
            private/internal address.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme != "https":
        raise ValueError(
            f"Refusing to dial non-https URL (scheme={scheme!r}); only https "
            "storage URLs are followed."
        )
    if not is_safe_url(url):
        raise ValueError("Refusing to dial URL: host resolves to a private/internal address.")


class HTTPClient:
    """Async HTTP client with Convilyn-specific header + error semantics."""

    def __init__(
        self,
        *,
        auth: AuthStrategy,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        retry_policy: RetryPolicy | None = None,
        idempotency_enabled: bool = True,
        auto_throttle: AutoThrottleConfig | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent or f"convilyn-python/{__version__}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
        )
        # Defaulting here (not at the parameter) keeps the constructor
        # signature small while still letting callers pass `retry_policy=None`
        # to disable retries explicitly via a NoRetry policy.
        self._retry_policy: RetryPolicy = retry_policy or ExponentialBackoffRetry()
        self._idempotency_enabled = idempotency_enabled
        self._auto_throttle = auto_throttle
        # Inject a test seam for ``asyncio.sleep`` so the throttle-retry path
        # is unit-testable without real wall-clock waits. Defaults to the
        # real sleep so callers never see this knob unless they want it.
        self._sleep = sleep or asyncio.sleep

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def auth(self) -> AuthStrategy:
        """Read-only handle to the auth strategy.

        Exposed so non-HTTP transports (e.g. the WebSocket events stream
        in :class:`convilyn.resources.goals.AsyncGoals.events`) can pull
        the bearer token without monkey-patching this client.
        """
        return self._auth

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        headers.update(self._auth.headers())
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """Issue an HTTP request, applying the retry policy on transient failures.

        Auto-stamps an ``Idempotency-Key`` on mutating verbs (POST / PUT
        / PATCH / DELETE) when idempotency is enabled and the caller did
        not supply one. The same key is reused across every retry of
        this call so a future backend that honours the header can
        deduplicate transparently.

        Raises:
            APIError / RateLimitError: a non-retryable non-2xx response
                with the Convilyn error envelope decoded.
            RetryExhaustedError: the retry policy ran out of attempts.
        """
        response = await self._request_with_quota_throttle(
            method,
            path,
            headers=headers,
            json=json,
            params=params,
            content=content,
            quota_attempts_remaining=(
                self._auto_throttle.max_retries if self._auto_throttle else 0
            ),
        )
        _maybe_emit_soft_limit(response)
        return response

    async def raw_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """Like :py:meth:`request` but **never raises** on HTTP status.

        Returns the final :class:`httpx.Response` so callers can inspect
        the body / headers / status code directly. Used by the gh-style
        ``convilyn api`` CLI escape hatch — that command wants to surface
        4xx / 5xx bodies for debugging rather than translate them into
        SDK exceptions.

        Auth, retry, and Idempotency-Key wiring are all applied — the
        escape hatch inherits the SDK's reliability story.
        """
        response, _attempts = await self._do_request_with_retry(
            method, path, headers=headers, json=json, params=params, content=content
        )
        _maybe_emit_soft_limit(response)
        return response

    async def _request_with_quota_throttle(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None,
        json: Any | None,
        params: dict[str, Any] | None,
        content: bytes | None,
        quota_attempts_remaining: int,
    ) -> httpx.Response:
        """Run :py:meth:`_do_request_with_retry` with auto-throttle wrapping.

        Re-issues the request up to ``quota_attempts_remaining`` times when
        the response decodes to :class:`QuotaExceededError`, sleeping the
        amount returned by :func:`compute_quota_sleep` between attempts.
        Any other transport-level retries (5xx / 429 / 408) are handled
        by ``_do_request_with_retry`` itself — auto-throttle is a higher-
        level shim that targets the 402 ``QUOTA_EXCEEDED`` envelope only.
        """
        response, attempts = await self._do_request_with_retry(
            method, path, headers=headers, json=json, params=params, content=content
        )
        if response.status_code < 400:
            return response

        # Decode once so we can branch on QuotaExceededError specifically
        # without re-parsing the envelope in two places.
        if attempts > 1:
            decoded: APIError = _to_retry_exhausted(response, attempts)
        else:
            decoded = _decode_error(response)

        if (
            self._auto_throttle is not None
            and quota_attempts_remaining > 0
            and isinstance(decoded, QuotaExceededError)
        ):
            sleep_s = compute_quota_sleep(decoded, self._auto_throttle)
            if sleep_s is not None:
                await self._sleep(sleep_s)
                return await self._request_with_quota_throttle(
                    method,
                    path,
                    headers=headers,
                    json=json,
                    params=params,
                    content=content,
                    quota_attempts_remaining=quota_attempts_remaining - 1,
                )

        raise decoded

    async def _do_request_with_retry(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> tuple[httpx.Response, int]:
        """Shared retry-aware request loop used by ``request`` and ``raw_request``.

        Returns ``(response, attempt_count)`` where ``attempt_count`` is
        the total number of HTTP calls made (1 = success first try, 2+
        = retries fired). The caller decides what to do with non-2xx
        responses: :py:meth:`request` raises, :py:meth:`raw_request`
        returns. This split is the SRP / OCP seam for the transport.
        """
        merged_headers = self._default_headers()
        if headers:
            merged_headers.update(headers)
        self._apply_idempotency_key(method.upper(), merged_headers)

        attempt = 0
        while True:
            response = await self._client.request(
                method,
                path,
                headers=merged_headers,
                json=json,
                params=params,
                content=content,
            )
            if response.status_code < 400:
                return response, attempt + 1
            if self._retry_policy.should_retry(response, None, attempt):
                await asyncio.sleep(self._retry_policy.next_delay(response, attempt))
                attempt += 1
                continue
            # Non-retryable response — return; caller decides whether to raise.
            return response, attempt + 1

    def _apply_idempotency_key(self, method: str, headers: dict[str, str]) -> None:
        """Stamp an Idempotency-Key on mutating verbs unless the caller already did.

        Kept as a private method (not a free function) so subclasses can
        override the policy without monkey-patching the request loop.
        """
        if not self._idempotency_enabled:
            return
        if method not in MUTATING_METHODS:
            return
        if "Idempotency-Key" in headers:
            return
        headers["Idempotency-Key"] = generate_idempotency_key()

    async def external_put(
        self,
        url: str,
        *,
        content: bytes | AsyncIterator[bytes],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """PUT to an external absolute URL (e.g. a presigned storage URL).

        Does NOT inject Convilyn auth headers — the URL itself carries the
        authorization signature, and adding our own ``Authorization`` would
        confuse the upstream service.

        Uses the shared :class:`httpx.AsyncClient`, so connection pooling
        and HTTP/2 multiplexing carry over. httpx treats absolute URLs as
        absolute, so the SDK's ``base_url`` is bypassed automatically.

        Raises:
            ValueError: the URL is not https (scheme guard).
            S3UploadError: any non-2xx response from the upstream service.
        """
        _require_safe_external_url(url)
        response = await self._client.put(url, content=content, headers=headers or {})
        if response.status_code >= 400:
            raise S3UploadError(
                response.status_code,
                f"S3_HTTP_{response.status_code}",
                response.reason_phrase or "External upload failed",
                {"url": url},
            )
        return response

    async def external_post_form(
        self,
        url: str,
        *,
        fields: dict[str, str],
        file_content: bytes | AsyncIterator[bytes],
        filename: str,
        content_type: str,
    ) -> httpx.Response:
        """Multipart POST to an external presigned-POST URL (storage POST policy).

        The backend's upload presign issues a POST grant: every ``fields``
        entry must be copied VERBATIM into the form and the file part
        appended LAST (the storage service ignores form fields after the file part, so the
        ordering is part of the wire contract). Same no-auth-header +
        https-only semantics as :py:meth:`external_put`.

        Raises:
            ValueError: the URL is not https (scheme guard).
            S3UploadError: any non-2xx response from the upstream service.
        """
        _require_safe_external_url(url)
        if not isinstance(file_content, bytes):
            # The storage POST policy needs a length-bearing multipart body;
            # materialise streaming bodies (mirrors the presigned-PUT
            # length-bearing body requirement).
            chunks = [chunk async for chunk in file_content]
            file_content = b"".join(chunks)
        response = await self._client.post(
            url,
            data=dict(fields),
            files={"file": (filename, file_content, content_type)},
        )
        if response.status_code >= 400:
            raise S3UploadError(
                response.status_code,
                f"S3_HTTP_{response.status_code}",
                response.reason_phrase or "External upload failed",
                {"url": url},
            )
        return response

    async def external_get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET an external absolute URL (e.g. storage download URL).

        Counterpart to :py:meth:`external_put` — same no-auth-header
        semantics, raises :class:`APIError` (not ``S3UploadError``, since
        a failed *download* is not strictly an upload error) on non-2xx.

        Raises:
            ValueError: the URL is not https (scheme guard).
        """
        _require_safe_external_url(url)
        response = await self._client.get(url, headers=headers or {})
        if response.status_code >= 400:
            raise APIError(
                response.status_code,
                f"DOWNLOAD_HTTP_{response.status_code}",
                response.reason_phrase or "External download failed",
                {"url": url},
            )
        return response

    async def external_get_to_file(
        self,
        url: str,
        dest: os.PathLike[str] | str,
        *,
        max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        chunk_size: int = 1024 * 1024,
    ) -> int:
        """Stream an external URL to *dest*, capped at ``max_bytes``.

        Streams in chunks instead of buffering the whole body, and aborts
        (raising ``ValueError``, partial file removed) as soon as the total
        exceeds ``max_bytes`` — so a very large or hostile response cannot
        exhaust memory or disk. Returns the number of bytes written.

        Raises:
            ValueError: the URL is unsafe (scheme/host guard) or the body
                exceeds ``max_bytes``.
            APIError: the server returned a non-2xx status.
        """
        _require_safe_external_url(url)
        written = 0
        dest_path = os.fspath(dest)
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise APIError(
                        response.status_code,
                        f"DOWNLOAD_HTTP_{response.status_code}",
                        response.reason_phrase or "External download failed",
                        {"url": url},
                    )
                with open(dest_path, "wb") as fh:
                    async for chunk in response.aiter_bytes(chunk_size):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(f"Download exceeds the {max_bytes}-byte cap; aborted.")
                        fh.write(chunk)
        except (ValueError, APIError):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(dest_path)
            raise
        return written

    async def aclose(self) -> None:
        await self._client.aclose()


def _maybe_emit_soft_limit(response: httpx.Response) -> None:
    """Surface a soft-limit warning when the response carries the flag header.

    Independent of :class:`AutoThrottleConfig` — soft-limit is purely
    informational; the SDK warns regardless of whether the caller opted
    into auto-throttle. Callers that want silence can use
    :func:`warnings.filterwarnings` or set the
    ``convilyn.throttle`` logger level to :data:`logging.ERROR`.
    """
    if response_has_soft_limit_header(response.headers):
        emit_soft_limit_warning(source=str(response.request.url))


def _decode_error(response: httpx.Response) -> APIError:
    """Translate an httpx response into the appropriate :class:`APIError` subclass.

    Normalises three envelope shapes seen in the wild — the flat
    ``{code, message, ...}``, FastAPI's default
    ``{"detail": {code, message, ...}}``, and an older
    ``{"error": {code, message, ...}}`` used by a few legacy endpoints.
    Whichever shape arrives, the dispatch logic below sees the same
    flat dict.

    Status-aware dispatch:
        * 429 → :class:`RateLimitError`
        * 402 + code in :data:`_PLAN_REQUIRED_CODES` → :class:`PlanRequiredError`
        * 402 + code = ``QUOTA_EXCEEDED`` → :class:`QuotaExceededError`
        * Otherwise → base :class:`APIError`

    Unknown 402 codes fall through to ``APIError`` — forward-compat for
    new tier signals the SDK doesn't know about yet.
    """
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    inner = _flatten_error_envelope(payload)
    code = inner.get("code") or f"HTTP_{status}"
    message = inner.get("message") or response.reason_phrase or "Request failed"
    details = inner.get("details")

    if status == 429:
        return RateLimitError(status, code, message, details)

    if status == 402:
        upgrade_url = inner.get("upgrade_url") or inner.get("upgradeUrl")
        if code in _PLAN_REQUIRED_CODES:
            return PlanRequiredError(
                status,
                code,
                message,
                details,
                upgrade_url=upgrade_url,
            )
        if code == "QUOTA_EXCEEDED":
            return QuotaExceededError(
                status,
                code,
                message,
                details,
                estimated_micro_u=_coerce_int(
                    inner.get("estimatedMicroU") or inner.get("estimated_micro_u")
                ),
                threshold_micro_u=_coerce_int(
                    inner.get("thresholdMicroU") or inner.get("threshold_micro_u")
                ),
                upgrade_url=upgrade_url,
            )

    return APIError(status, code, message, details)


def _flatten_error_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the inner error dict regardless of envelope shape.

    The API returns tier-gate errors as an HTTP error envelope that
    serialises to ``{"detail": {...}}``. Older endpoints emit
    ``{"error": {...}}``. The fallback is the legacy flat shape.
    """
    for key in ("detail", "error"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def _coerce_int(value: Any) -> int | None:
    """Best-effort ``int`` coercion for wire fields that may arrive as str."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_retry_exhausted(response: httpx.Response, attempt_count: int) -> RetryExhaustedError:
    """Convert the final attempt's response into a :class:`RetryExhaustedError`."""
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    code = payload.get("code") or f"HTTP_{status}"
    message = payload.get("message") or response.reason_phrase or "Request failed"
    details = payload.get("details")
    return RetryExhaustedError(status, code, message, details, attempt_count=attempt_count)

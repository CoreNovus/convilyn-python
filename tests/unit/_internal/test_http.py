"""HTTPClient.raw_request — logic / boundary / error / object-state.

Sister to :mod:`test_resilience`: the retry policy is shared with
``request()``, so we don't re-prove the cadence here. We only assert
the headline difference: ``raw_request`` returns the response on any
status (including 4xx / 5xx) instead of raising.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn
from convilyn._internal.http import DEFAULT_BASE_URL, resolve_base_url

API_BASE = "https://api.convilyn.corenovus.com"


# ── resolve_base_url — precedence: explicit arg > env > default ──────


class TestResolveBaseUrl:
    def test_explicit_arg_wins_over_env(self) -> None:
        result = resolve_base_url(
            "https://explicit.example",
            env={"CONVILYN_BASE_URL": "https://env.example"},
        )
        assert result == "https://explicit.example"

    def test_env_used_when_no_explicit_arg(self) -> None:
        result = resolve_base_url(None, env={"CONVILYN_BASE_URL": "https://env.example"})
        assert result == "https://env.example"

    def test_default_when_neither_supplied(self) -> None:
        assert resolve_base_url(None, env={}) == DEFAULT_BASE_URL

    def test_default_is_corenovus_host_only(self) -> None:
        # Host root only — resource paths carry their own /api/v1 prefix.
        assert DEFAULT_BASE_URL == "https://api.convilyn.corenovus.com"


# ── 1. Logic — happy path returns the response ──────────────────────


class TestRawRequestLogic:
    @pytest.mark.asyncio
    async def test_200_returns_response(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/test").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                response = await client._http.raw_request("GET", "/api/v1/test")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


# ── 2. Boundary — non-success statuses still return ────────────────


class TestRawRequestBoundary:
    @pytest.mark.asyncio
    async def test_404_returns_response_does_not_raise(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/missing").mock(
                return_value=httpx.Response(404, json={"code": "NOT_FOUND", "message": "missing"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                response = await client._http.raw_request("GET", "/api/v1/missing")
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_500_after_retries_returns_response(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.get(f"{API_BASE}/api/v1/down").mock(
                return_value=httpx.Response(500, json={"code": "DOWN", "message": "x"})
            )
            async with AsyncConvilyn(
                api_key="ck_test",  # pragma: allowlist secret
                max_retries=2,
            ) as client:
                with patch("convilyn._internal.http.asyncio.sleep", return_value=None):
                    response = await client._http.raw_request("GET", "/api/v1/down")
        assert response.status_code == 500
        # Retried twice (3 total attempts: initial + 2 retries) before returning.
        assert route.call_count == 3


# ── 3. Error — transport failures still bubble up ───────────────────


class TestRawRequestErrors:
    @pytest.mark.asyncio
    async def test_transport_error_propagates(self) -> None:
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/network").mock(side_effect=httpx.ConnectError("boom"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(httpx.ConnectError):
                    await client._http.raw_request("GET", "/api/v1/network")


# ── 4. Object-state — auth header + Idempotency-Key still applied ──


class TestRawRequestObjectState:
    @pytest.mark.asyncio
    async def test_auth_header_present(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={})

        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/check").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.raw_request("GET", "/api/v1/check")
        assert captured == ["Bearer ck_test"]  # pragma: allowlist secret

    @pytest.mark.asyncio
    async def test_idempotency_key_on_mutating_verb(self) -> None:
        captured: list[str | None] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json={})

        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/check").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.raw_request("POST", "/api/v1/check", json={})
        assert captured[0] is not None  # auto-stamped


# ── 5. Scheme guard — external_* only follows https ─────────────────


class TestExternalUrlSchemeGuard:
    """external_post_form / external_get must refuse non-https (backend) URLs."""

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://example-bucket.s3.amazonaws.com/key",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "ftp://example.com/x",
        ],
    )
    @pytest.mark.asyncio
    async def test_external_get_rejects_non_https(self, bad_url: str) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="non-https"):
                await client._http.external_get(bad_url)

    @pytest.mark.parametrize(
        "bad_url",
        ["http://example-bucket.s3.amazonaws.com/key", "file:///tmp/x"],
    )
    @pytest.mark.asyncio
    async def test_external_post_form_rejects_non_https(self, bad_url: str) -> None:
        """The upload path's scheme guard.

        This asserted against ``external_put`` until that method was deleted as
        dead (the contract makes the presign's ``fields`` required, so no
        deployed backend issues the PUT-shaped grant it served). The assertion
        moved here rather than being deleted with it: ``external_post_form`` is
        the method every upload actually goes through, and it had **no** test of
        its own — the guard on the live path was uncovered while the guard on
        the dead one was covered twice.
        """
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="non-https"):
                await client._http.external_post_form(
                    bad_url,
                    fields={"key": "k"},
                    file_content=b"x",
                    filename="x.txt",
                    content_type="text/plain",
                )

    @pytest.mark.asyncio
    async def test_external_get_allows_https(self, monkeypatch) -> None:
        # Stub the host check (its real logic is covered offline in
        # test_urlpolicy.py) so this stays a no-network flow assertion.
        monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _u: True)
        url = "https://example-bucket.s3.amazonaws.com/output.bin?sig=abc"
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(url).mock(return_value=httpx.Response(200, content=b"ok"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                response = await client._http.external_get(url)
        assert response.status_code == 200
        assert response.content == b"ok"


class TestExternalUrlHostGuard:
    """external_* must refuse an https URL whose host resolves to a
    private/internal address (SSRF / upload exfiltration)."""

    @pytest.mark.asyncio
    async def test_external_get_rejects_private_host(self, monkeypatch) -> None:
        monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _u: False)
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="private/internal"):
                await client._http.external_get("https://internal.example/x")

    @pytest.mark.asyncio
    async def test_external_post_form_rejects_private_host(self, monkeypatch) -> None:
        """Upload exfiltration guard, on the method uploads actually use.

        Moved off ``external_put`` when that dead method was deleted — see the
        scheme-guard counterpart above.
        """
        monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _u: False)
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="private/internal"):
                await client._http.external_post_form(
                    "https://internal.example/x",
                    fields={"key": "k"},
                    file_content=b"x",
                    filename="x.txt",
                    content_type="text/plain",
                )


class TestBaseUrlHttpsGuard:
    """A non-loopback http base_url leaks the API key in cleartext."""

    @pytest.mark.parametrize("url", ["http://api.example.com", "http://staging.example.com"])
    def test_http_non_loopback_rejected(self, url: str) -> None:
        with pytest.raises(ValueError, match="insecure base_url"):
            resolve_base_url(url, env={})

    @pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:9000"])
    def test_http_loopback_allowed(self, url: str) -> None:
        assert resolve_base_url(url, env={}) == url

    def test_https_allowed(self) -> None:
        assert resolve_base_url("https://api.example.com", env={}) == "https://api.example.com"


class TestExternalDownloadCap:
    """external_get_to_file streams with a byte cap; oversize aborts."""

    @pytest.mark.asyncio
    async def test_body_over_cap_raises_and_removes_partial(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _u: True)
        url = "https://cdn.example.com/big.bin"
        dest = tmp_path / "out.bin"
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(url).mock(return_value=httpx.Response(200, content=b"x" * 100))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="cap"):
                    await client._http.external_get_to_file(url, dest, max_bytes=10)
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_body_within_cap_writes_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _u: True)
        url = "https://cdn.example.com/small.bin"
        dest = tmp_path / "out.bin"
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(url).mock(return_value=httpx.Response(200, content=b"hello"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                written = await client._http.external_get_to_file(url, dest, max_bytes=1024)
        assert (written, dest.read_bytes()) == (5, b"hello")


# ── 9. Error envelope — the THIRD wire shape (#4204) ───────────────────


class TestStringDetailEnvelope:
    """``HTTPException(status_code=..., detail=<str>)`` is a shape the API really
    emits — the goal lane raises it on four paths — and ``_flatten_error_envelope``
    recognised only the flat and dict-wrapped forms. A string ``detail`` fell
    through unchanged, so ``message`` missed and became the reason phrase: the
    server's explanation was replaced with ``"Bad Request"``.
    """

    @pytest.mark.asyncio
    async def test_a_string_detail_becomes_the_message(self) -> None:
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400, json={"detail": "Structured understanding currently accepts one file"}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as exc_info:
                    await client._http.request("POST", "/api/v1/jobs/goal", json={})

        assert exc_info.value.message == "Structured understanding currently accepts one file"

    @pytest.mark.asyncio
    async def test_a_string_detail_still_reports_the_status_derived_code(self) -> None:
        """A string ``detail`` carries no code, so inventing one would be worse
        than the status-derived fallback. Only ``message`` is recovered."""
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(400, json={"detail": "nope"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as exc_info:
                    await client._http.request("POST", "/api/v1/jobs/goal", json={})

        assert exc_info.value.code == "HTTP_400"

    @pytest.mark.asyncio
    async def test_the_dict_wrapped_shape_is_unchanged(self) -> None:
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400, json={"detail": {"code": "WIDGET_BAD", "message": "inner"}}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as exc_info:
                    await client._http.request("POST", "/api/v1/jobs/goal", json={})

        assert exc_info.value.code == "WIDGET_BAD"

    @pytest.mark.asyncio
    async def test_an_unrecognised_shape_is_still_not_unwrapped_on_a_guess(self) -> None:
        """The caution this change kept: a ``detail`` that is neither string nor
        dict reports the status rather than inventing a code from a sub-value."""
        async with respx.mock() as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(400, json={"detail": ["a", "list"]})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as exc_info:
                    await client._http.request("POST", "/api/v1/jobs/goal", json={})

        assert exc_info.value.code == "HTTP_400"

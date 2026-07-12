"""Files.upload — logic / boundary / error / object-state coverage.

The Convilyn API + S3 are mocked via respx so no network traffic
happens. The tests assert on observable wire behaviour (which routes
were called, in which order, with which payloads) rather than on
internal implementation, so the orchestration can evolve without test
churn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn, Convilyn, File, S3UploadError

API_BASE = "https://api.convilyn.corenovus.com"
PRESIGN_URL = "https://example-bucket.s3.amazonaws.com/key?signature=abc"
S3_KEY = "input/job_test/file_xyz/example.txt"


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """Create a small text file on disk for path-style uploads."""
    target = tmp_path / "report.txt"
    target.write_bytes(b"hello world")
    return target


@pytest.fixture
def confirm_response() -> dict:
    """A canonical FileResponse payload — wire format is camelCase."""
    return {
        "fileId": "file_xyz",
        "fileName": "report.txt",
        "fileSize": 11,
        "mimeType": "text/plain",
        "isInput": True,
        "createdAt": "2026-05-20T12:00:00Z",
        "jobId": None,
    }


def _stub_happy_path(
    mock: respx.MockRouter,
    *,
    expected_filename: str,
    expected_size: int,
    expected_content_type: str,
    confirm_payload: dict,
) -> dict[str, respx.Route]:
    """Wire up the three happy-path routes; return them for assertion."""
    presign = mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
        return_value=httpx.Response(
            200,
            json={
                "uploadUrl": PRESIGN_URL,
                "fileId": "file_xyz",
                "s3Key": S3_KEY,
                "expiresIn": 3600,
            },
        )
    )
    s3 = mock.put(PRESIGN_URL).mock(return_value=httpx.Response(200))
    confirm = mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
        return_value=httpx.Response(200, json=confirm_payload)
    )
    return {
        "presign": presign,
        "s3": s3,
        "confirm": confirm,
        "_expected_filename": expected_filename,
        "_expected_size": expected_size,
        "_expected_content_type": expected_content_type,
    }


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestUploadLogic:
    """Both path-style and content-style uploads return a populated File."""

    @pytest.mark.asyncio
    async def test_path_upload_returns_file(self, sample_file, confirm_response) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            _stub_happy_path(
                mock,
                expected_filename="report.txt",
                expected_size=11,
                expected_content_type="text/plain",
                confirm_payload=confirm_response,
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                result = await client.files.upload(sample_file)

        assert isinstance(result, File)
        assert result.file_id == "file_xyz"
        assert result.filename == "report.txt"
        assert result.size == 11
        assert result.content_type == "text/plain"
        assert result.created_at == datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_content_upload_returns_file(self, confirm_response) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            _stub_happy_path(
                mock,
                expected_filename="hello.txt",
                expected_size=5,
                expected_content_type="text/plain",
                confirm_payload=confirm_response,
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                result = await client.files.upload(
                    content=b"hello",
                    filename="hello.txt",
                )

        assert result.file_id == "file_xyz"


# ── 2. Boundary — input shape edges ──────────────────────────────────


class TestUploadBoundary:
    """Edge cases around filename / content_type inference."""

    @pytest.mark.asyncio
    async def test_unknown_extension_falls_back_to_octet_stream(self) -> None:
        confirm_payload = {
            "fileId": "f1",
            "fileName": "blob.unknownext",
            "fileSize": 4,
            "mimeType": "application/octet-stream",
            "isInput": True,
            "createdAt": "2026-05-20T12:00:00Z",
        }
        async with respx.mock(assert_all_called=True) as mock:
            presign = mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "uploadUrl": PRESIGN_URL,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.put(PRESIGN_URL).mock(return_value=httpx.Response(200))
            mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
                return_value=httpx.Response(200, json=confirm_payload)
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.files.upload(content=b"data", filename="blob.unknownext")

        # Asserting on the wire — presign should have asked for octet-stream
        sent_body = presign.calls.last.request.read().decode()
        assert "application/octet-stream" in sent_body

    @pytest.mark.asyncio
    async def test_no_path_and_no_content_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError):
                await client.files.upload()

    @pytest.mark.asyncio
    async def test_content_without_filename_raises(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError):
                await client.files.upload(content=b"x")


# ── 3. Error — per-step failure surfaces a typed exception ──────────


class TestUploadErrors:
    """Each step's failures are surfaced as the appropriate typed error."""

    @pytest.mark.asyncio
    async def test_presign_unauthorized_raises_api_error(self, sample_file) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
                return_value=httpx.Response(
                    401, json={"code": "AUTH_REQUIRED", "message": "Bad key"}
                )
            )
            async with AsyncConvilyn(api_key="ck_bad") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.upload(sample_file)
        assert info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_s3_put_failure_raises_s3_upload_error(self, sample_file) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "uploadUrl": PRESIGN_URL,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.put(PRESIGN_URL).mock(return_value=httpx.Response(503))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(S3UploadError) as info:
                    await client.files.upload(sample_file)
        assert info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_confirm_failure_raises_api_error(self, sample_file) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "uploadUrl": PRESIGN_URL,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.put(PRESIGN_URL).mock(return_value=httpx.Response(200))
            mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
                return_value=httpx.Response(422, json={"code": "BAD_KEY", "message": "x"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.upload(sample_file)
        assert info.value.status_code == 422


# ── 3b. Presigned-POST grant (backend #2129 upload contract) ─────────

_POST_FIELDS = {
    "Content-Type": "text/plain",
    "key": S3_KEY,
    "policy": "eyJleHBpcmF0aW9uIjogIn0=",
    "x-amz-signature": "sigsigsig",
}


def _stub_post_grant(mock: respx.MockRouter, confirm_payload: dict) -> respx.Route:
    """Presign answers with a POST grant (fields present); S3 expects POST."""
    mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
        return_value=httpx.Response(
            200,
            json={
                "uploadUrl": PRESIGN_URL,
                "fields": _POST_FIELDS,
                "fileId": "file_xyz",
                "s3Key": S3_KEY,
                "expiresIn": 900,
            },
        )
    )
    s3 = mock.post(PRESIGN_URL).mock(return_value=httpx.Response(204))
    mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
        return_value=httpx.Response(200, json=confirm_payload)
    )
    return s3


class TestUploadPostGrant:
    """A presign carrying ``fields`` is an S3 POST-policy grant: the SDK must
    multipart-POST (never PUT), copy every field verbatim, file part LAST.
    Regression for the dev/prod 403s after backend #2129 switched PUT→POST."""

    @pytest.mark.asyncio
    async def test_post_grant_uploads_via_multipart_post(
        self, sample_file, confirm_response
    ) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            s3 = _stub_post_grant(mock, confirm_response)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                result = await client.files.upload(sample_file)

        assert (result.file_id, s3.call_count) == ("file_xyz", 1)

    @pytest.mark.asyncio
    async def test_post_grant_form_carries_fields_verbatim_and_file_last(
        self, sample_file, confirm_response
    ) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            s3 = _stub_post_grant(mock, confirm_response)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.files.upload(sample_file)

        body = s3.calls[0].request.content
        positions = {name: body.find(f'name="{name}"'.encode()) for name in _POST_FIELDS}
        file_pos = body.find(b'name="file"')
        assert (
            all(0 <= pos < file_pos for pos in positions.values())
            and _POST_FIELDS["policy"].encode() in body
        )

    @pytest.mark.asyncio
    async def test_post_grant_s3_403_raises_s3_upload_error(self, sample_file) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "uploadUrl": PRESIGN_URL,
                        "fields": _POST_FIELDS,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 900,
                    },
                )
            )
            mock.post(PRESIGN_URL).mock(return_value=httpx.Response(403))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(S3UploadError) as info:
                    await client.files.upload(sample_file)
        assert info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_content_upload_streaming_body_materialised_for_post(
        self, confirm_response
    ) -> None:
        """bytes-content path through the POST grant still lands one 204."""
        async with respx.mock(assert_all_called=True) as mock:
            s3 = _stub_post_grant(mock, confirm_response)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.files.upload(
                    content=b"hello world", filename="report.txt", content_type="text/plain"
                )

        assert s3.calls[0].request.headers["content-type"].startswith("multipart/form-data")


# ── 4. Object-state — call order + sync wrapper ─────────────────────


class TestUploadObjectState:
    """The 3 steps fire in order; sync wrapper mirrors async behaviour."""

    @pytest.mark.asyncio
    async def test_three_steps_called_in_order(self, sample_file, confirm_response) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            routes = _stub_happy_path(
                mock,
                expected_filename="report.txt",
                expected_size=11,
                expected_content_type="text/plain",
                confirm_payload=confirm_response,
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.files.upload(sample_file)

        assert routes["presign"].called
        assert routes["s3"].called
        assert routes["confirm"].called
        # Each route hit exactly once
        assert routes["presign"].call_count == 1
        assert routes["s3"].call_count == 1
        assert routes["confirm"].call_count == 1

    def test_sync_wrapper_returns_same_file_type(self, sample_file, confirm_response) -> None:
        with respx.mock(assert_all_called=True) as mock:
            _stub_happy_path(
                mock,
                expected_filename="report.txt",
                expected_size=11,
                expected_content_type="text/plain",
                confirm_payload=confirm_response,
            )
            client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
            try:
                result = client.files.upload(sample_file)
            finally:
                client.close()

        assert isinstance(result, File)
        assert result.file_id == "file_xyz"


class TestUploadBodyRegression:
    """Regression guard for the S3-501 upload bug (chunked body rejected)."""

    def test_upload_path_branch_sends_length_bearing_body(self) -> None:
        """The path upload branch must read the file into a bytes body so
        httpx emits Content-Length. An async-generator body makes httpx use
        Transfer-Encoding: chunked, which an S3 presigned PUT rejects with
        HTTP 501 — the reason streaming an upload never worked against real
        storage (respx mocks returned 200 and hid it)."""
        from pathlib import Path

        import convilyn.resources.files as files_mod

        source = Path(files_mod.__file__).read_text(encoding="utf-8")
        assert "read_bytes()" in source, (
            "upload path branch must send a bytes body (Content-Length)"
        )
        assert "_stream_file(handle)" not in source, (
            "async-generator upload body -> httpx chunked -> S3 501"
        )


# ── Upload size cap (self-DoS / OOM defence) ────────────────────────


class TestUploadSizeCap:
    @pytest.mark.asyncio
    async def test_content_over_cap_raises_before_network(self, monkeypatch):
        from convilyn.resources import files as files_mod

        monkeypatch.setattr(files_mod, "MAX_UPLOAD_BYTES", 8)
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="upload cap"):
                await client.files.upload(content=b"x" * 9, filename="big.bin")

    @pytest.mark.asyncio
    async def test_path_over_cap_raises_before_read(self, monkeypatch, tmp_path):
        from convilyn.resources import files as files_mod

        monkeypatch.setattr(files_mod, "MAX_UPLOAD_BYTES", 4)
        big = tmp_path / "big.txt"
        big.write_bytes(b"12345")
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="upload cap"):
                await client.files.upload(big)


# ── delete() — client-controlled retention (A1) ──────────────────────


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_calls_endpoint_and_returns_none(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            route = mock.delete(f"{API_BASE}/api/v1/files/file_xyz").mock(
                return_value=httpx.Response(204)
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                result = await client.files.delete("file_xyz")

        assert result is None
        assert route.called

    @pytest.mark.asyncio
    async def test_delete_unowned_surfaces_404(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.delete(f"{API_BASE}/api/v1/files/file_other").mock(
                return_value=httpx.Response(
                    404, json={"code": "NOT_FOUND", "message": "File not found"}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.delete("file_other")
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_in_use_surfaces_409(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.delete(f"{API_BASE}/api/v1/files/file_busy").mock(
                return_value=httpx.Response(
                    409, json={"code": "FILE_IN_USE", "message": "job still running"}
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.delete("file_busy")
        assert info.value.status_code == 409

    def test_sync_wrapper_present(self) -> None:
        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert callable(sync_client.files.delete)
        finally:
            sync_client.close()

"""Files.upload — logic / boundary / error / object-state coverage.

The Convilyn API + S3 are mocked via respx so no network traffic
happens. The tests assert on observable wire behaviour (which routes
were called, in which order, with which payloads) rather than on
internal implementation, so the orchestration can evolve without test
churn.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn, Convilyn, File, S3UploadError

API_BASE = "https://api.convilyn.com"
PRESIGN_URL = "https://example-bucket.s3.amazonaws.com/key?signature=abc"
S3_KEY = "input/job_test/file_xyz/example.txt"

#: The form fields of a presigned-POST grant. Declared up here, beside the other
#: wire constants, because it is now the ONLY grant shape — not a variant.
#:
#: Every upload stub in this file used to omit ``fields`` and mock an S3 **PUT**,
#: which pinned a wire shape the backend has no producer for: the contract makes
#: ``fields`` required (``UploadPresignResponse: required: [uploadUrl, fields,
#: fileId, s3Key]``) and the only implementation is ``generate_presigned_post``.
#: So the primary upload tests were asserting against the dead branch while the
#: live POST path was exercised only by section 3b below.
_POST_FIELDS = {
    "Content-Type": "text/plain",
    "key": S3_KEY,
    "policy": "eyJleHBpcmF0aW9uIjogIn0=",
    "x-amz-signature": "sigsigsig",
}


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
                "fields": _POST_FIELDS,
                "fileId": "file_xyz",
                "s3Key": S3_KEY,
                "expiresIn": 3600,
            },
        )
    )
    s3 = mock.post(PRESIGN_URL).mock(return_value=httpx.Response(200))
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
        assert result.created_at == datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

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
                        "fields": _POST_FIELDS,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.post(PRESIGN_URL).mock(return_value=httpx.Response(200))
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
                        "fields": _POST_FIELDS,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.post(PRESIGN_URL).mock(return_value=httpx.Response(503))
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
                        "fields": _POST_FIELDS,
                        "fileId": "f1",
                        "s3Key": S3_KEY,
                        "expiresIn": 3600,
                    },
                )
            )
            mock.post(PRESIGN_URL).mock(return_value=httpx.Response(200))
            mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
                return_value=httpx.Response(422, json={"code": "BAD_KEY", "message": "x"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.upload(sample_file)
        assert info.value.status_code == 422


# ── 3b. Presigned-POST grant (upload contract) ───────────────────────


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
    Regression for the dev/prod 403s after the backend switched PUT→POST."""

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
        assert "handle.read(size + 1)" in source, (
            "upload path branch must send a bounded bytes body (Content-Length)"
        )
        assert "_stream_file(handle)" not in source, (
            "async-generator upload body -> httpx chunked -> S3 501"
        )


# ── Upload size: the platform's plan limit, checked before the read ──


#: A size far past anything a client-side cap would have let through.
_HUGE = 50 * 1024**3

_PLAN_LIMIT_MESSAGE = "File size exceeds the upload limit."


def _stub_presign_refusal(mock: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    """Presign answers 413 with the platform's plain-string upsell."""
    presign = mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
        return_value=httpx.Response(413, json={"detail": _PLAN_LIMIT_MESSAGE})
    )
    storage = mock.post(PRESIGN_URL).mock(return_value=httpx.Response(204))
    return presign, storage


def _huge_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[str]]:
    """A path whose reported size is huge, recording every open of it."""
    target = tmp_path / "huge.pdf"
    target.write_bytes(b"%PDF")
    real_stat = Path.stat
    reads: list[str] = []

    def fake_stat(self: Path, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == target:
            return type("Stat", (), {"st_size": _HUGE, "st_mode": result.st_mode})()
        return result

    real_open = Path.open

    def fake_open(self: Path, *args, **kwargs):
        if self == target:
            reads.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "open", fake_open)
    return target, reads


class TestUploadSizeIsThePlatformsCall:
    @pytest.mark.asyncio
    async def test_a_huge_file_is_asked_of_the_platform(self, tmp_path, monkeypatch) -> None:
        target, _ = _huge_file(tmp_path, monkeypatch)
        async with respx.mock(assert_all_called=False) as mock:
            presign, _ = _stub_presign_refusal(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError):
                    await client.files.upload(target)

        assert json.loads(presign.calls.last.request.content)["fileSize"] == _HUGE

    @pytest.mark.asyncio
    async def test_the_platforms_413_reaches_the_caller_with_its_message(
        self, tmp_path, monkeypatch
    ) -> None:
        target, _ = _huge_file(tmp_path, monkeypatch)
        async with respx.mock(assert_all_called=False) as mock:
            _stub_presign_refusal(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.upload(target)

        assert (info.value.status_code, info.value.message) == (413, _PLAN_LIMIT_MESSAGE)

    @pytest.mark.asyncio
    async def test_a_refused_file_is_never_read_or_uploaded(self, tmp_path, monkeypatch) -> None:
        target, reads = _huge_file(tmp_path, monkeypatch)
        async with respx.mock(assert_all_called=False) as mock:
            _, storage = _stub_presign_refusal(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError):
                    await client.files.upload(target)

        assert (reads, storage.called) == ([], False)

    @pytest.mark.asyncio
    async def test_raw_content_over_the_limit_gets_the_same_413(self) -> None:
        async with respx.mock(assert_all_called=False) as mock:
            _stub_presign_refusal(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.files.upload(content=b"x" * 16, filename="big.bin")

        assert info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_a_file_that_grows_after_the_size_check_is_refused(
        self, sample_file, confirm_response, monkeypatch
    ) -> None:
        real_stat = Path.stat

        def stale_stat(self: Path, *args, **kwargs):
            result = real_stat(self, *args, **kwargs)
            if self == sample_file:
                return type("Stat", (), {"st_size": 3, "st_mode": result.st_mode})()
            return result

        monkeypatch.setattr(Path, "stat", stale_stat)
        async with respx.mock(assert_all_called=False) as mock:
            s3 = _stub_post_grant(mock, confirm_response)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="changed size while uploading"):
                    await client.files.upload(sample_file)

        assert s3.called is False


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


# ── files.list() — durable storage + usage (SDK A1b) ────────────────


def _stored_file_wire(**overrides: object) -> dict:
    """The list endpoint returns snake_case (plain BaseModel, no aliases)."""
    base = {
        "file_id": "file_1",
        "file_name": "invoice.pdf",
        "file_size": 2048,
        "mime_type": "application/pdf",
        "file_extension": "pdf",
        "created_at": "2026-07-12T10:00:00Z",
    }
    base.update(overrides)
    return base


class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_typed_files_and_usage(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/files").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "files": [_stored_file_wire()],
                        "usage": {
                            "used_bytes": 2048,
                            "free_bytes": 524288000,
                            "over_quota": False,
                        },
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                listing = await client.files.list()

        assert listing.files[0].filename == "invoice.pdf"
        assert listing.files[0].content_type == "application/pdf"
        assert listing.usage.used_bytes == 2048
        assert listing.usage.over_quota is False

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_files(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/files").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "files": [],
                        "usage": {"used_bytes": 0, "free_bytes": 0, "over_quota": False},
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                listing = await client.files.list()

        assert listing.files == []

    def test_sync_wrapper_present(self) -> None:
        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert callable(sync_client.files.list)
        finally:
            sync_client.close()

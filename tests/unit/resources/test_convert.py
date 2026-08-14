"""Convert.create/wait/download — logic / boundary / error / object-state.

Tests assert on wire behaviour (which routes are called, in what order,
with what payloads) rather than implementation internals, so the 3-step
orchestration can evolve without test churn.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    ConvertJob,
    Convilyn,
    File,
    JobFailedError,
    JobTimeoutError,
)

API_BASE = "https://api.convilyn.corenovus.com"
S3_DOWNLOAD_URL = "https://example-bucket.s3.amazonaws.com/output.pdf?signature=xyz"


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def file_obj() -> File:
    """A canonical File object returned by client.files.upload (commit 2)."""
    return File.model_validate(
        {
            "fileId": "file_xyz",
            "fileName": "report.docx",
            "fileSize": 12345,
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "createdAt": "2026-05-20T12:00:00Z",
        }
    )


def _job_response(status: str, **overrides: Any) -> dict:
    """Build a canonical JobResponse wire payload."""
    base = {
        "jobId": "job_test",
        "status": status,
        "processorType": "document_conversion",
        "progress": 0 if status == "queued" else (50 if status == "processing" else 100),
        "params": {"target_format": "pdf"},
        "resultFiles": None,
        "error": None,
        "retryCount": 0,
        "createdAt": "2026-05-20T12:00:00Z",
        "updatedAt": "2026-05-20T12:00:01Z",
    }
    base.update(overrides)
    return base


def _completed_job() -> dict:
    return _job_response(
        "completed",
        progress=100,
        resultFiles=[
            {
                "filename": "report.pdf",
                "size": 9000,
                "mimetype": "application/pdf",
                "url": S3_DOWNLOAD_URL,
            }
        ],
        completedAt="2026-05-20T12:00:05Z",
    )


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestConvertLogic:
    """create_and_wait sends the right payload, polls, and yields the result."""

    @pytest.mark.asyncio
    async def test_create_and_wait_returns_completed_job(self, file_obj) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    job = await client.convert.create_and_wait(file=file_obj, target_format="pdf")

        assert isinstance(job, ConvertJob)
        assert job.status == "completed"
        assert job.is_terminal
        # Payload sanity — the POST must carry the discriminated-union shape.
        # The request discriminator is snake_case ``processor_type`` per the
        # turbo_lane contract (JobRequest); the camelCase ``processorType`` is
        # only the *response* field name. Sending camelCase makes the backend
        # discriminated union reject the body with 400 union_tag_not_found.
        sent_body = create.calls.last.request.read().decode()
        assert '"processor_type":"document_conversion"' in sent_body.replace(" ", "")
        assert "processorType" not in sent_body
        assert '"source_file_id":"file_xyz"' in sent_body.replace(" ", "")
        assert '"source_format":"docx"' in sent_body.replace(" ", "")
        assert '"target_format":"pdf"' in sent_body.replace(" ", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("filename", "target", "expected_processor", "expected_file_key"),
        [
            ("photo.png", "webp", "image_conversion", "file_id"),
            ("clip.mp4", "mp3", "media_processing", "file_id"),
        ],
    )
    async def test_an_image_or_media_file_reaches_its_own_processor(
        self, filename: str, target: str, expected_processor: str, expected_file_key: str
    ) -> None:
        """The resource wires the family derivation, not just the helper module.

        `image_conversion` and `media_processing` params carry NO source field —
        the backend reads that off the stored object's key — so this also pins
        that the SDK does not invent one.
        """
        uploaded = File.model_validate(
            {
                "fileId": "file_media",
                "fileName": filename,
                "fileSize": 100,
                "mimeType": "application/octet-stream",
                "createdAt": "2026-05-20T12:00:00Z",
            }
        )
        async with respx.mock(assert_all_called=True) as mock:
            create = mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.convert.create(file=uploaded, target_format=target)

        body = json.loads(create.calls.last.request.read().decode())
        assert body["processor_type"] == expected_processor
        assert body["params"][expected_file_key] == "file_media"
        assert body["params"]["output_format"] == target
        assert "source_format" not in body["params"]

    @pytest.mark.asyncio
    async def test_download_to_fetches_first_result_file(self, file_obj, tmp_path) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(S3_DOWNLOAD_URL).mock(
                return_value=httpx.Response(200, content=b"%PDF-1.4 fake pdf content")
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                completed = ConvertJob.model_validate(_completed_job())
                target = tmp_path / "out.pdf"
                returned = await client.convert.download_to(completed, to=target)

        assert returned == target
        assert target.read_bytes().startswith(b"%PDF-1.4")

    @pytest.mark.asyncio
    async def test_download_to_refuses_symlink_target(self, file_obj, tmp_path) -> None:
        # A pre-placed symlink at the destination must NOT be written through.
        link = tmp_path / "out.pdf"
        try:
            link.symlink_to(tmp_path / "elsewhere.pdf")
        except OSError as exc:  # Windows without symlink privilege / Developer Mode
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            completed = ConvertJob.model_validate(_completed_job())
            with pytest.raises(ValueError, match="symlink"):
                await client.convert.download_to(completed, to=link)


# ── 2. Boundary — input shape edges ──────────────────────────────────


class TestConvertBoundary:
    """source_format inference + required-arg validation."""

    @pytest.mark.asyncio
    async def test_file_id_without_source_format_raises(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="source_format is required"):
                await client.convert.create(file_id="file_xyz", target_format="pdf")

    @pytest.mark.asyncio
    async def test_neither_file_nor_file_id_raises_typeerror(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="either `file` or `file_id`"):
                await client.convert.create(target_format="pdf")

    @pytest.mark.asyncio
    async def test_both_file_and_file_id_raises_typeerror(self, file_obj) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="either `file` or `file_id`, not both"):
                await client.convert.create(
                    file=file_obj, file_id="file_other", target_format="pdf"
                )

    @pytest.mark.asyncio
    async def test_unknown_extension_requires_explicit_source_format(self) -> None:
        weird_file = File.model_validate(
            {
                "fileId": "file_x",
                "fileName": "data.weirdext",
                "fileSize": 100,
                "mimeType": "application/octet-stream",
                "createdAt": "2026-05-20T12:00:00Z",
            }
        )
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="pass source_format"):
                await client.convert.create(file=weird_file, target_format="pdf")

    @pytest.mark.asyncio
    async def test_a_file_with_no_extension_at_all_raises(self) -> None:
        nameless = File.model_validate(
            {
                "fileId": "file_x",
                "fileName": "receipt",
                "fileSize": 100,
                "mimeType": "application/octet-stream",
                "createdAt": "2026-05-20T12:00:00Z",
            }
        )
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="Could not infer source_format"):
                await client.convert.create(file=nameless, target_format="pdf")


# ── 3. Error — per-step failure surfaces a typed exception ──────────


class TestConvertErrors:
    """Failed jobs / 404 retrievals / timeouts each get their own exception."""

    @pytest.mark.asyncio
    async def test_failed_status_raises_job_failed_error(self, file_obj) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(
                    200,
                    json=_job_response(
                        "failed",
                        progress=42,
                        error={"code": "CONVERSION_FAILED", "message": "Bad format"},
                    ),
                )
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    with pytest.raises(JobFailedError) as info:
                        await client.convert.create_and_wait(file=file_obj, target_format="pdf")
        assert info.value.code == "CONVERSION_FAILED"
        assert info.value.job_id == "job_test"

    @pytest.mark.asyncio
    async def test_failed_job_with_zero_byte_result_raises_job_failed_error(self, file_obj) -> None:
        # A failed conversion can carry a 0-byte placeholder result file. The
        # SDK must still parse the job and surface JobFailedError, not choke on
        # ResultFile.size validation (a `gt=0` bound raised ValidationError,
        # masking the real failure for callers).
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(
                    200,
                    json=_job_response(
                        "failed",
                        progress=0,
                        resultFiles=[
                            {
                                "filename": "output.pdf",
                                "size": 0,
                                "mimetype": "application/pdf",
                                "url": S3_DOWNLOAD_URL,
                            }
                        ],
                        error={"code": "CONVERSION_FAILED", "message": "Bad format"},
                    ),
                )
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    with pytest.raises(JobFailedError) as info:
                        await client.convert.create_and_wait(file=file_obj, target_format="pdf")
        assert info.value.code == "CONVERSION_FAILED"

    @pytest.mark.asyncio
    async def test_retrieve_404_raises_api_error(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/job_missing").mock(
                return_value=httpx.Response(404, json={"code": "JOB_NOT_FOUND", "message": "..."})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.convert.retrieve("job_missing")
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wait_timeout_raises_job_timeout_error(self) -> None:
        # Stays "processing" forever; wait should time out.
        async with respx.mock() as mock:
            mock.get(f"{API_BASE}/api/v1/jobs/job_slow").mock(
                return_value=httpx.Response(200, json=_job_response("processing"))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    with pytest.raises(JobTimeoutError) as info:
                        # Tiny timeout so the test is fast.
                        await client.convert.wait("job_slow", timeout=0.01, poll_interval=0.5)
        assert info.value.job_id == "job_slow"


# ── 4. Object-state — polling behaviour + sync wrapper ──────────────


class TestConvertObjectState:
    """Polling fires multiple times and the sync wrapper mirrors async."""

    @pytest.mark.asyncio
    async def test_polls_until_terminal(self, file_obj) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            # Side-effect list: queued → processing → completed.
            get_route = mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                side_effect=[
                    httpx.Response(200, json=_job_response("queued")),
                    httpx.Response(200, json=_job_response("processing", progress=50)),
                    httpx.Response(200, json=_completed_job()),
                ]
            )

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    job = await client.convert.create_and_wait(file=file_obj, target_format="pdf")
        assert job.status == "completed"
        assert get_route.call_count == 3

    def test_sync_wrapper_returns_same_convert_job_type(self, file_obj) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(202, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(200, json=_completed_job())
            )

            client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
            try:
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    job = client.convert.create_and_wait(file=file_obj, target_format="pdf")
            finally:
                client.close()

        assert isinstance(job, ConvertJob)
        assert job.is_terminal

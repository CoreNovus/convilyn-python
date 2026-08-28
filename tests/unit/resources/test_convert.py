"""Convert.create/wait/download — logic / boundary / error / object-state.

Tests assert on wire behaviour (which routes are called, in what order,
with what payloads) rather than implementation internals, so the 3-step
orchestration can evolve without test churn.
"""

from __future__ import annotations

import inspect
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
from convilyn.local import UnsupportedRouteError
from convilyn.resources import AsyncConvert

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
    async def test_download_to_refuses_an_existing_file(self, file_obj, tmp_path) -> None:
        """It used to overwrite silently, which `convilyn.local.convert` refuses to
        do and says why. One package cannot hold both positions."""
        target = tmp_path / "out.pdf"
        target.write_bytes(b"the file the user already had")

        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            completed = ConvertJob.model_validate(_completed_job())
            with pytest.raises(FileExistsError):
                await client.convert.download_to(completed, to=target)

    @pytest.mark.asyncio
    async def test_the_refused_file_is_left_untouched(self, file_obj, tmp_path) -> None:
        """Refusing after truncating would be the same data loss with an
        exception on top, so the bytes are read back rather than assumed."""
        target = tmp_path / "out.pdf"
        target.write_bytes(b"the file the user already had")

        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            completed = ConvertJob.model_validate(_completed_job())
            with pytest.raises(FileExistsError):
                await client.convert.download_to(completed, to=target)

        assert target.read_bytes() == b"the file the user already had"

    @pytest.mark.asyncio
    async def test_the_message_names_the_way_out(self, file_obj, tmp_path) -> None:
        """Same wording as the offline half, so the SDK says one thing."""
        target = tmp_path / "out.pdf"
        target.write_bytes(b"x")

        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            completed = ConvertJob.model_validate(_completed_job())
            with pytest.raises(FileExistsError, match="pass overwrite=True to replace it"):
                await client.convert.download_to(completed, to=target)

    @pytest.mark.asyncio
    async def test_overwrite_true_replaces_it(self, file_obj, tmp_path) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            mock.get(S3_DOWNLOAD_URL).mock(
                return_value=httpx.Response(200, content=b"%PDF-1.4 fresh")
            )

            target = tmp_path / "out.pdf"
            target.write_bytes(b"stale")

            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                completed = ConvertJob.model_validate(_completed_job())
                await client.convert.download_to(completed, to=target, overwrite=True)

        assert target.read_bytes() == b"%PDF-1.4 fresh"

    @pytest.mark.asyncio
    async def test_a_symlink_is_still_refused_as_a_symlink(self, file_obj, tmp_path) -> None:
        """Order matters: a symlink is not merely "a file in the way", and
        ``overwrite=True`` must not turn it into one — following it would write
        the bytes wherever it points."""
        link = tmp_path / "out.pdf"
        real = tmp_path / "elsewhere.pdf"
        real.write_bytes(b"somewhere else entirely")
        try:
            link.symlink_to(real)
        except OSError as exc:  # Windows without symlink privilege / Developer Mode
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            completed = ConvertJob.model_validate(_completed_job())
            with pytest.raises(ValueError, match="symlink"):
                await client.convert.download_to(completed, to=link, overwrite=True)

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
    async def test_a_path_string_in_file_raises_typeerror(self) -> None:
        """`file="report.pdf"` used to reach `file.filename` and die with
        `AttributeError: 'str' object has no attribute 'filename'` — an
        implementation detail, from the one resource whose other two refusals
        are careful `TypeError`s (#4108)."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError, match="expects an uploaded File object"):
                await client.convert.create(file="report.pdf", target_format="md")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_the_refusal_names_both_ways_forward(self) -> None:
        """A refusal that does not say what to do instead is half an answer."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError) as exc:
                await client.convert.create(file="report.pdf", target_format="md")  # type: ignore[arg-type]
        assert "file_id=" in str(exc.value) and "files.upload(" in str(exc.value)

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
        # `UnsupportedRouteError`, not `ValueError`: an extension no family speaks
        # reaches the same refusal as any other unroutable pair, so it moved with
        # it. The message is unchanged — only the type, which is now catchable as
        # `ConvilynError` the way the docs have always said it would be.
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(UnsupportedRouteError, match="pass source_format"):
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

    def test_convert_takes_only_the_transport(self) -> None:
        """The constructor is the surface this resource actually needs.

        It also accepted a ``files`` resource for a while. Nothing else pins
        this: ``tests/contract/test_public_surface.py`` pins the *method* set,
        so a second constructor argument can reappear with every gate green.
        """
        assert list(inspect.signature(AsyncConvert.__init__).parameters) == ["self", "http"]

    @pytest.mark.asyncio
    async def test_convert_holds_no_files_resource(self) -> None:
        """The dead dependency was *stored* and never read — pin its absence.

        A signature check alone would pass on an argument that is accepted and
        dropped, which is the same defect one step later.
        """
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            assert not hasattr(client.convert, "_files")


class TestRefusalDetailReachesTheCaller:
    """A refused conversion hands over its operands, not just a canned sentence.

    The third-party harness that prompted this reported the whole failure as

        JobFailedError: ... failed [GENERIC]: Something went wrong during
        processing. Please try again.

    on a six-sheet workbook. The code is now branchable and the retry
    instruction is gone; these pin the half that lets a caller explain the
    refusal in its own words.
    """

    _DETAIL = {
        "reason": "MULTI_SHEET_WORKBOOK",
        "sheetCount": 6,
        "faithfulTargets": ["md", "ods", "html", "pdf"],
    }

    async def _raise(self, file_obj, detail) -> JobFailedError:
        error = {
            "code": "UNSUPPORTED_INPUT",
            "message": "This file type or input isn't supported for this workflow.",
        }
        if detail is not None:
            error["detail"] = detail
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(200, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(
                    200, json=_job_response("failed", progress=90, error=error)
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    with pytest.raises(JobFailedError) as info:
                        await client.convert.create_and_wait(file=file_obj, target_format="csv")
        return info.value

    @pytest.mark.asyncio
    async def test_the_sheet_count_arrives_as_an_integer(self, file_obj) -> None:
        """An `int`, so a caller can localize. Parsing it out of the English
        message is what this exists to make unnecessary."""
        exc = await self._raise(file_obj, self._DETAIL)

        assert exc.detail.sheet_count == 6

    @pytest.mark.asyncio
    async def test_the_alternatives_arrive_as_a_list(self, file_obj) -> None:
        exc = await self._raise(file_obj, self._DETAIL)

        assert exc.detail.faithful_targets == ["md", "ods", "html", "pdf"]

    @pytest.mark.asyncio
    async def test_the_reason_is_branchable(self, file_obj) -> None:
        """`code` deliberately stays `UNSUPPORTED_INPUT` — a new top-level code
        would send older clients down their unknown-code path. The discriminator
        is here instead."""
        exc = await self._raise(file_obj, self._DETAIL)

        assert (exc.code, exc.detail.reason) == ("UNSUPPORTED_INPUT", "MULTI_SHEET_WORKBOOK")

    @pytest.mark.asyncio
    async def test_the_string_form_explains_itself(self, file_obj) -> None:
        """What a caller sees if they only ever `print(exc)` — which is most of
        them. The bare canned sentence is true and useless on its own."""
        exc = await self._raise(file_obj, self._DETAIL)

        assert "6 sheets" in str(exc) and "md, ods, html, pdf" in str(exc)

    @pytest.mark.asyncio
    async def test_the_existing_prefix_is_unchanged(self, file_obj) -> None:
        """Callers match on this prefix. The operands are appended, never
        interpolated into it, so `startswith` keeps working."""
        exc = await self._raise(file_obj, self._DETAIL)

        assert str(exc).startswith("Job job_test (document_conversion) failed [UNSUPPORTED_INPUT]:")

    @pytest.mark.asyncio
    async def test_a_failure_without_a_detail_still_raises_cleanly(self, file_obj) -> None:
        """Boundary, and the direction that matters for forward compatibility:
        every failure predating this field, and almost every one after it, has
        no `detail`. The old sentence must be exactly what it was."""
        exc = await self._raise(file_obj, None)

        assert (exc.detail, str(exc).endswith("supported for this workflow.")) == (None, True)

    @pytest.mark.asyncio
    async def test_an_unknown_reason_does_not_break_the_parse(self, file_obj) -> None:
        """The server may learn refusals this build has never heard of. An
        unrecognised `reason` is data, not a parse error — pinned because a
        `Literal` type here would have turned a future server release into a
        ValidationError on an already-published client."""
        exc = await self._raise(file_obj, {"reason": "SOMETHING_NEW_ENTIRELY"})

        assert (exc.detail.reason, exc.detail.sheet_count) == ("SOMETHING_NEW_ENTIRELY", None)

    @pytest.mark.asyncio
    async def test_an_unknown_reason_carrying_a_count_gets_no_invented_sentence(
        self, file_obj
    ) -> None:
        """The sentence is keyed on the REASON, not on "a count is present".

        Those two conditions select the same failures today, which is exactly
        why this is worth pinning: a future reason that happens to carry a sheet
        count would otherwise inherit the CSV wording and assert something false
        about a conversion it knows nothing about. Silence is the correct output
        for a reason this build has never heard of.
        """
        exc = await self._raise(file_obj, {"reason": "SOMETHING_NEW", "sheetCount": 9})

        assert (exc.detail.sheet_count, "CSV holds one table" in str(exc)) == (9, False)


class TestConversionWarningsReachTheCaller:
    """A successful conversion can still have lost something (#4054).

    The channel was built by #4033 and the LibreOffice route started filling it
    in #4111 — but `ConvertJob` did not model the field, so every warning the
    server sent was dropped at the last hop. `pdf_reverse` has been emitting
    real ones ("Page 3 has minimal or no text") the whole time and no caller has
    ever seen one.

    This matters most where the job **succeeds**: an `.xls` workbook converted
    to CSV returns a file and a green status while holding only its first sheet.
    The warning is the only thing that says so.
    """

    _WARNING = (
        "best_effort: CSV holds one sheet. If this workbook has more than one, "
        "only the first was converted — convert to md, ods, html, pdf to keep every sheet."
    )

    async def _completed_with(self, file_obj, payload: dict) -> Any:
        async with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{API_BASE}/api/v1/jobs").mock(
                return_value=httpx.Response(200, json=_job_response("queued"))
            )
            mock.get(f"{API_BASE}/api/v1/jobs/job_test").mock(
                return_value=httpx.Response(200, json=_job_response("completed", **payload))
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with patch("convilyn.resources.convert.asyncio.sleep", return_value=None):
                    return await client.convert.create_and_wait(file=file_obj, target_format="csv")

    @pytest.mark.asyncio
    async def test_a_warning_on_a_successful_job_is_surfaced(self, file_obj) -> None:
        job = await self._completed_with(file_obj, {"warnings": [self._WARNING]})

        assert job.warnings == [self._WARNING]

    @pytest.mark.asyncio
    async def test_a_clean_conversion_reports_an_empty_list(self, file_obj) -> None:
        """Never ``None``: an empty list unambiguously means "nothing to report",
        so a caller can write ``for w in job.warnings`` without a guard."""
        job = await self._completed_with(file_obj, {"warnings": []})

        assert job.warnings == []

    @pytest.mark.asyncio
    async def test_a_response_without_the_field_still_parses(self, file_obj) -> None:
        """Boundary, and the one that matters for a published client: every job
        row written before #4033, and any older deployment, omits the key
        entirely. Absent must mean empty, not a ValidationError on the user's
        machine."""
        job = await self._completed_with(file_obj, {})

        assert job.warnings == []

    @pytest.mark.asyncio
    async def test_an_unprefixed_warning_is_passed_through_unchanged(self, file_obj) -> None:
        """`pdf_reverse.py` emits warnings with no `kind:` prefix today, and the
        SDK is not the place to normalise that — inventing a prefix here would
        put words in the server's mouth. Pinned because the documented
        vocabulary makes a prefix look guaranteed, and it is not.
        """
        job = await self._completed_with(file_obj, {"warnings": ["Page 3 has minimal or no text"]})

        assert job.warnings == ["Page 3 has minimal or no text"]

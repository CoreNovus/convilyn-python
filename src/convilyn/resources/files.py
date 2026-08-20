"""Files resource — upload local files via Convilyn's presigned-URL flow.

Three steps wrapped behind a single :py:meth:`AsyncFiles.upload`:

1. ``POST /api/v1/upload/presign`` — Convilyn issues a presigned upload URL
2. ``PUT <presigned-url>`` — the client uploads the bytes to the storage URL
3. ``POST /api/v1/upload/confirm`` — Convilyn registers the file record

The steps are exposed as private methods so future extensions (multipart
upload for very large files, progress callbacks, transfer acceleration)
can override one step without rewriting the rest.

Naming follows OpenAI / Stripe — the public class is ``AsyncFiles`` for
the async-primary surface, and ``Files`` is the synchronous facade.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.types import File, FileList

DEFAULT_CONTENT_TYPE = "application/octet-stream"

# Upload cap (500 MiB). The body is read into memory to send a
# length-bearing request; a larger input raises rather than risking OOM.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _require_within_upload_cap(size: int) -> None:
    """Raise ``ValueError`` when *size* exceeds :data:`MAX_UPLOAD_BYTES`."""
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"File is {size} bytes, over the {MAX_UPLOAD_BYTES}-byte upload cap.")


class AsyncFiles:
    """Asynchronous file-management resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.files``. The
    resource owns request shaping and response parsing for the upload
    flow; it does not know how to convert or analyse files — those live
    on sibling resources.
    """

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    # ── Public API ───────────────────────────────────────────────

    async def upload(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        content: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> File:
        """Upload a file and return its :class:`File` record.

        Exactly one of ``path`` or ``content`` must be supplied. The file
        is read into memory to send a length-bearing body (the storage
        upload requires a known Content-Length), so uploads are capped at
        :data:`MAX_UPLOAD_BYTES`; a larger input raises ``ValueError``
        rather than risking an out-of-memory condition.

        Args:
            path: Filesystem path to the file to upload.
            content: Raw bytes to upload (alternative to ``path``).
            filename: Override the filename sent to Convilyn. Required
                when ``content`` is used and inferable from the path
                otherwise.
            content_type: MIME type. Defaults to a best-guess from the
                filename suffix, falling back to
                ``application/octet-stream``.

        Returns:
            The registered :class:`File`.

        Raises:
            TypeError: neither ``path`` nor ``content`` was supplied.
            ValueError: the file exceeds :data:`MAX_UPLOAD_BYTES`.
            convilyn.AuthError / APIError: presign or confirm failed.
            convilyn.S3UploadError: the upload step failed.
        """
        if path is None and content is None:
            raise TypeError("upload() requires either `path` or `content`")

        resolved_filename, resolved_content_type = self._resolve_metadata(
            path=path, filename=filename, content_type=content_type
        )

        if path is not None:
            file_path = Path(os.fspath(path))
            # The storage upload needs a known Content-Length, so the body
            # is sent whole rather than chunked. Check the size before
            # reading it into memory so an oversized file fails fast.
            size = file_path.stat().st_size
            _require_within_upload_cap(size)
            data = file_path.read_bytes()
            return await self._upload_with_body(
                body=data,
                filename=resolved_filename,
                size=len(data),
                content_type=resolved_content_type,
            )

        assert content is not None  # narrow for the type checker
        _require_within_upload_cap(len(content))
        return await self._upload_with_body(
            body=content,
            filename=resolved_filename,
            size=len(content),
            content_type=resolved_content_type,
        )

    async def delete(self, file_id: str) -> None:
        """Delete an uploaded file's cloud copy (storage object + record).

        Only the uploader can delete; a missing or unowned ``file_id``
        surfaces as :class:`~convilyn.APIError` 404 and a file attached
        to a still-running job as 409 (``FILE_IN_USE``). The platform
        deletes input files automatically ~1 hour after upload anyway —
        call this when you want the cloud copy gone deterministically
        the moment your workflow is done (e.g. privacy-sensitive
        documents on an edge device).
        """
        await self._http.request("DELETE", f"/api/v1/files/{file_id}")

    async def list(self) -> FileList:
        """List your durable stored files with a storage-usage summary.

        Only DURABLE files appear here (e.g. emailed-in attachments that you
        chose to keep) — ordinary uploads are ephemeral and are removed by the
        platform's ~1-hour cleanup, so a just-uploaded transient file will not
        be listed. An unauthenticated caller gets an empty list.
        """
        response = await self._http.request("GET", "/api/v1/files")
        return FileList.model_validate(response.json())

    # ── Step orchestration (open for extension) ──────────────────

    async def _upload_with_body(
        self,
        *,
        body: bytes | AsyncIterator[bytes],
        filename: str,
        size: int,
        content_type: str,
    ) -> File:
        """Run the 3-step upload protocol against a resolved body.

        Split out so future subclasses can replace the storage step
        (multipart, progress, accelerated endpoints) without rewriting
        the orchestration or the confirm step.
        """
        presign = await self._presign(filename=filename, size=size, content_type=content_type)
        await self._upload_to_storage(
            upload_url=presign["uploadUrl"],
            fields=presign["fields"],
            body=body,
            filename=filename,
            content_type=content_type,
        )
        confirm = await self._confirm(
            file_id=presign["fileId"],
            filename=filename,
            size=size,
            content_type=content_type,
            s3_key=presign["s3Key"],
        )
        return File.model_validate(confirm)

    async def _presign(
        self,
        *,
        filename: str,
        size: int,
        content_type: str,
    ) -> dict[str, Any]:
        """Step 1 — exchange file metadata for a presigned upload URL."""
        response = await self._http.request(
            "POST",
            "/api/v1/upload/presign",
            json={
                "fileName": filename,
                "fileSize": size,
                "contentType": content_type,
            },
        )
        return cast(dict[str, Any], response.json())

    async def _upload_to_storage(
        self,
        *,
        upload_url: str,
        fields: dict[str, str],
        body: bytes | AsyncIterator[bytes],
        filename: str,
        content_type: str,
    ) -> None:
        """Step 2 — upload the bytes to the storage service via the presigned grant.

        The backend issues a presigned **POST** grant (``fields`` present,
        size-capped via the storage ``content-length-range`` policy): copy the
        fields verbatim into a multipart form, file part LAST.

        ``fields`` is required, not optional. The contract declares it so
        (``UploadPresignResponse: required: [uploadUrl, fields, fileId, s3Key]``)
        and the backend has one producer — ``generate_presigned_post`` — so no
        deployed generation issues the ``fields``-absent presigned-PUT shape
        this used to fall back to.
        """
        await self._http.external_post_form(
            upload_url,
            fields=fields,
            file_content=body,
            filename=filename,
            content_type=content_type,
        )

    async def _confirm(
        self,
        *,
        file_id: str,
        filename: str,
        size: int,
        content_type: str,
        s3_key: str,
    ) -> dict[str, Any]:
        """Step 3 — tell Convilyn the storage write succeeded."""
        response = await self._http.request(
            "POST",
            "/api/v1/upload/confirm",
            json={
                "fileId": file_id,
                "fileName": filename,
                "fileSize": size,
                "contentType": content_type,
                "s3Key": s3_key,
            },
        )
        return cast(dict[str, Any], response.json())

    # ── Metadata resolution ──────────────────────────────────────

    @staticmethod
    def _resolve_metadata(
        *,
        path: str | os.PathLike[str] | None,
        filename: str | None,
        content_type: str | None,
    ) -> tuple[str, str]:
        """Derive ``(filename, content_type)`` from caller input.

        Precedence: explicit kwargs > inferred from ``path`` > defaults.
        Returns a tuple so the caller can stay immutable.
        """
        resolved_name: str | None = filename
        if resolved_name is None and path is not None:
            resolved_name = Path(os.fspath(path)).name
        if not resolved_name:
            raise ValueError("filename is required when uploading raw content")

        resolved_type = content_type
        if resolved_type is None:
            guess, _ = mimetypes.guess_type(resolved_name)
            resolved_type = guess or DEFAULT_CONTENT_TYPE
        return resolved_name, resolved_type


class Files:
    """Synchronous facade around :class:`AsyncFiles`.

    Mirrors the async surface 1:1 so callers can switch between the two
    styles with minimal code change. Each call runs the underlying
    coroutine via the injected runner — the root sync client passes its
    private-loop :class:`~convilyn._internal.loop_runner.LoopRunner` so
    all calls share one loop; standalone construction falls
    back to :func:`asyncio.run`.
    """

    def __init__(self, async_files: AsyncFiles, run: CoroRunner | None = None) -> None:
        self._async = async_files
        self._run: CoroRunner = run if run is not None else asyncio.run

    def upload(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        content: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> File:
        return self._run(
            self._async.upload(
                path=path,
                content=content,
                filename=filename,
                content_type=content_type,
            )
        )

    def delete(self, file_id: str) -> None:
        return self._run(self._async.delete(file_id))

    def list(self) -> FileList:
        return self._run(self._async.list())

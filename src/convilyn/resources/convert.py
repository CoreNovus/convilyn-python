"""Convert resource — document conversion jobs.

Wraps Convilyn's file conversion job API:

* ``POST /api/v1/jobs``           — create a conversion job
* ``GET  /api/v1/jobs/{id}``      — poll status (returns full JobResponse)
* ``GET  <resultFiles[0].url>``   — download the produced artifact

This resource exposes the ``document_conversion`` processor (DOCX / PDF
/ PPTX / TXT / HTML / Markdown interchange). The backend supports other
processor types (image, OCR, media, pdf_operations, image_compression,
document_compression); each can be added as its own resource method or
sibling resource without touching this file's orchestration.

Design follows OpenAI / Stripe convention: data on the
:class:`convilyn.types.ConvertJob` model, behaviour on the resource.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, cast

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.exceptions import JobFailedError, JobTimeoutError
from convilyn.resources.files import AsyncFiles
from convilyn.types import ConvertJob, File

# ── Tunables ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_POLL_TIMEOUT = 300.0
MAX_POLL_INTERVAL = 5.0
STALE_PROGRESS_BACKOFF_AFTER = 3  # consecutive identical progress → grow interval
BACKOFF_FACTOR = 1.5

# Extensions that the SDK can auto-derive into a `source_format`. The
# backend accepts the same lowercase tokens for document conversion;
# keep the mapping explicit so future renames are obvious.
_SOURCE_FORMAT_BY_SUFFIX: dict[str, str] = {
    ".docx": "docx",
    ".doc": "doc",
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".txt": "txt",
    ".rtf": "rtf",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".odt": "odt",
}


class AsyncConvert:
    """Asynchronous conversion resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.convert``.
    """

    def __init__(self, http: HTTPClient, files: AsyncFiles) -> None:
        self._http = http
        self._files = files

    # ── Public API ───────────────────────────────────────────────

    async def create(
        self,
        *,
        file: File | None = None,
        file_id: str | None = None,
        target_format: str,
        source_format: str | None = None,
        quality: str = "standard",
        page_range: str | None = None,
    ) -> ConvertJob:
        """Create a document conversion job and return immediately.

        The returned :class:`ConvertJob` will be in ``queued`` or
        ``processing`` state; call :py:meth:`wait` to block until
        terminal, or :py:meth:`retrieve` for one-shot polling.
        """
        resolved_file_id, resolved_source = self._resolve_source(
            file=file, file_id=file_id, source_format=source_format
        )
        params: dict[str, Any] = {
            "source_file_id": resolved_file_id,
            "source_format": resolved_source,
            "target_format": target_format,
            "quality": quality,
        }
        if page_range is not None:
            params["page_range"] = page_range
        return await self._create_job(
            payload={"processor_type": "document_conversion", "params": params}
        )

    async def retrieve(self, job_id: str) -> ConvertJob:
        """Fetch the current state of a job."""
        return await self._poll_once(job_id)

    async def wait(
        self,
        job_id: str,
        *,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> ConvertJob:
        """Poll until the job reaches a terminal status, the timeout
        expires, or the backend reports failure.

        Raises:
            JobFailedError: terminal status is ``failed``.
            JobTimeoutError: ``timeout`` elapsed before terminal status.
        """
        return await self._wait_loop(
            job_id=job_id,
            timeout=timeout,
            initial_interval=poll_interval,
        )

    async def create_and_wait(
        self,
        *,
        file: File | None = None,
        file_id: str | None = None,
        target_format: str,
        source_format: str | None = None,
        quality: str = "standard",
        page_range: str | None = None,
        timeout: float = DEFAULT_POLL_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> ConvertJob:
        """Shortcut: ``create()`` then ``wait()``."""
        job = await self.create(
            file=file,
            file_id=file_id,
            target_format=target_format,
            source_format=source_format,
            quality=quality,
            page_range=page_range,
        )
        return await self.wait(job.job_id, timeout=timeout, poll_interval=poll_interval)

    async def download_url(self, job: ConvertJob | str) -> str:
        """Return the presigned download URL for the first result file.

        Accepts either a :class:`ConvertJob` (uses its cached state) or a
        ``job_id`` string (retrieves fresh state first).
        """
        resolved = await self._ensure_job(job)
        if not resolved.result_files:
            raise JobFailedError(
                job_id=resolved.job_id,
                processor_type=resolved.processor_type,
                code="NO_RESULT_FILES",
                message="Job is terminal but no result files were produced",
            )
        return resolved.result_files[0].url

    async def download_to(
        self,
        job: ConvertJob | str,
        *,
        to: str | os.PathLike[str],
    ) -> Path:
        """Download the first result file to ``to`` and return the path.

        Uses :py:meth:`HTTPClient.external_put`-style raw GET to bypass
        Convilyn auth headers on the storage URL.
        """
        url = await self.download_url(job)
        target = Path(os.fspath(to))
        # Refuse to write *through* an existing symlink: a pre-placed link
        # could redirect the bytes to an unintended location (e.g. a dotfile
        # or a path outside the intended directory). Writing to a regular
        # path or a brand-new file is unaffected.
        if target.is_symlink():
            raise ValueError(
                f"Refusing to write to {target!r}: target is a symlink. "
                "Remove it or choose a non-symlink destination."
            )
        # Stream to disk with a size cap rather than buffering the whole body
        # in memory — a very large (or hostile) response cannot exhaust RAM.
        await self._http.external_get_to_file(url, target)
        return target

    # ── Private steps (extensible by subclassing) ───────────────

    def _resolve_source(
        self,
        *,
        file: File | None,
        file_id: str | None,
        source_format: str | None,
    ) -> tuple[str, str]:
        """Resolve ``(source_file_id, source_format)`` from caller input.

        Either ``file`` or ``file_id`` must be supplied; ``source_format``
        is required when only ``file_id`` is given.
        """
        if file is not None and file_id is not None:
            raise TypeError("create() accepts either `file` or `file_id`, not both")
        if file is not None:
            inferred = source_format or self._infer_source_format(file.filename)
            if inferred is None:
                raise ValueError(
                    f"Could not infer source_format from filename {file.filename!r}; "
                    "pass source_format explicitly"
                )
            return file.file_id, inferred
        if file_id is not None:
            if source_format is None:
                raise ValueError("source_format is required when only file_id is provided")
            return file_id, source_format
        raise TypeError("create() requires either `file` or `file_id`")

    async def _create_job(self, *, payload: dict[str, Any]) -> ConvertJob:
        response = await self._http.request("POST", "/api/v1/jobs", json=payload)
        return ConvertJob.model_validate(response.json())

    async def _poll_once(self, job_id: str) -> ConvertJob:
        response = await self._http.request("GET", f"/api/v1/jobs/{job_id}")
        return ConvertJob.model_validate(response.json())

    async def _wait_loop(
        self,
        *,
        job_id: str,
        timeout: float,
        initial_interval: float,
    ) -> ConvertJob:
        """Polling with simple backoff on stale progress.

        Kept separate so subclasses can swap in adaptive scheduling when
        the API starts emitting ``suggestedPollIntervalMs`` for
        file conversion jobs.
        """
        start = time.monotonic()
        interval = initial_interval
        stale_count = 0
        last_progress = -1
        while True:
            job = await self._poll_once(job_id)
            if job.is_terminal:
                return self._finalise(job)

            if job.progress == last_progress:
                stale_count += 1
                if stale_count >= STALE_PROGRESS_BACKOFF_AFTER:
                    interval = min(interval * BACKOFF_FACTOR, MAX_POLL_INTERVAL)
                    stale_count = 0
            else:
                last_progress = job.progress
                stale_count = 0

            elapsed = time.monotonic() - start
            if elapsed + interval > timeout:
                raise JobTimeoutError(job_id=job_id, elapsed=elapsed, timeout=timeout)
            await asyncio.sleep(interval)

    @staticmethod
    def _finalise(job: ConvertJob) -> ConvertJob:
        """Translate a failed-status terminal job into ``JobFailedError``."""
        if job.status == "failed":
            err = job.error
            raise JobFailedError(
                job_id=job.job_id,
                processor_type=job.processor_type,
                code=err.code if err else "UNKNOWN",
                message=err.message if err else "Job failed without a structured error",
            )
        return job

    @staticmethod
    def _infer_source_format(filename: str) -> str | None:
        """Map common document suffixes to backend ``source_format`` tokens."""
        suffix = Path(filename).suffix.lower()
        return _SOURCE_FORMAT_BY_SUFFIX.get(suffix)

    async def _ensure_job(self, job: ConvertJob | str) -> ConvertJob:
        """Return a :class:`ConvertJob` whether the caller passed one or a job_id."""
        if isinstance(job, ConvertJob):
            return job
        return await self._poll_once(cast(str, job))


class Convert:
    """Synchronous facade around :class:`AsyncConvert`.

    Mirrors the async surface 1:1 so users can switch styles without
    code change.
    """

    def __init__(self, async_convert: AsyncConvert, run: CoroRunner | None = None) -> None:
        self._async = async_convert
        self._run: CoroRunner = run if run is not None else asyncio.run

    def create(self, **kwargs: Any) -> ConvertJob:
        return self._run(self._async.create(**kwargs))

    def retrieve(self, job_id: str) -> ConvertJob:
        return self._run(self._async.retrieve(job_id))

    def wait(self, job_id: str, **kwargs: Any) -> ConvertJob:
        return self._run(self._async.wait(job_id, **kwargs))

    def create_and_wait(self, **kwargs: Any) -> ConvertJob:
        return self._run(self._async.create_and_wait(**kwargs))

    def download_url(self, job: ConvertJob | str) -> str:
        return self._run(self._async.download_url(job))

    def download_to(self, job: ConvertJob | str, *, to: str | os.PathLike[str]) -> Path:
        return self._run(self._async.download_to(job, to=to))

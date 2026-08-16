"""Convert resource — the free, deterministic conversion lane.

Wraps Convilyn's file conversion job API:

* ``POST /api/v1/jobs``           — create a conversion job
* ``GET  /api/v1/jobs/{id}``      — poll status (returns full JobResponse)
* ``GET  <resultFiles[0].url>``   — download the produced artifact

It reaches all three conversions that lane runs — ``document_conversion``,
``image_conversion`` and ``media_processing`` — and the caller never names one.
The processor is derived from the formats
(:mod:`convilyn._internal.convert_families`), so ``report.docx → pdf``,
``photo.png → webp`` and ``clip.mp4 → mp3`` are one verb rather than three.

**Nothing here spends credits.** The metered processors — OCR, video and audio
transcription — are not reachable from this resource by construction, which is
what makes `convert` and `goals.understand()` distinguishable without reading a
flag. Whether a given pair is *producible* is the backend's answer, published at
``GET /api/v1/{document,image,media}/support``; this SDK carries no copy of it.

Design follows OpenAI / Stripe convention: data on the
:class:`convilyn.types.ConvertJob` model, behaviour on the resource.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, cast

from convilyn._internal.convert_families import build_payload, detect_format
from convilyn._internal.download import download_url_to_path
from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.exceptions import JobFailedError, JobTimeoutError
from convilyn.types import ConvertJob, File

# ── Tunables ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_POLL_TIMEOUT = 300.0
MAX_POLL_INTERVAL = 5.0
#: Floor for any caller-supplied ``poll_interval`` — see the identical guard in
#: :mod:`convilyn.resources.goals`. ``poll_interval=0`` would spin: the backoff is
#: multiplicative so a zero never grows, and ``asyncio.sleep(0)`` does not wait.
MIN_POLL_INTERVAL = 0.2
STALE_PROGRESS_BACKOFF_AFTER = 3  # consecutive identical progress → grow interval
BACKOFF_FACTOR = 1.5


class AsyncConvert:
    """Asynchronous conversion resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.convert``.
    """

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    # ── Public API ───────────────────────────────────────────────

    async def create(
        self,
        *,
        file: File | None = None,
        file_id: str | None = None,
        target_format: str,
        source_format: str | None = None,
        quality: str | int | None = None,
        page_range: str | None = None,
    ) -> ConvertJob:
        """Create a conversion job and return immediately.

        The processor is derived from the source and target formats, so a
        document, an image and a media transcode are all this one call. The
        returned :class:`ConvertJob` will be in ``queued`` or ``processing``
        state; call :py:meth:`wait` to block until terminal, or
        :py:meth:`retrieve` for one-shot polling.

        Args:
            quality: Omitted from the request when ``None``, so each processor
                applies its own default. Document and media conversions take a
                preset name (``"standard"`` / ``"high"``); image conversions
                take a 1-100 integer.
            page_range: Document conversions only — passing it for an image or
                media conversion raises rather than being silently dropped.

        Raises:
            ValueError: the two formats name no single conversion, or a
                parameter does not belong to the one they name.
        """
        resolved_file_id, resolved_source = self._resolve_source(
            file=file, file_id=file_id, source_format=source_format
        )
        return await self._create_job(
            payload=build_payload(
                file_id=resolved_file_id,
                source_format=resolved_source,
                target_format=target_format,
                quality=quality,
                page_range=page_range,
            )
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
        quality: str | int | None = None,
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
        overwrite: bool = False,
    ) -> Path:
        """Download the first result file to ``to`` and return the path.

        Uses :py:meth:`HTTPClient.external_put`-style raw GET to bypass
        Convilyn auth headers on the storage URL.

        Raises ``FileExistsError`` when ``to`` already exists, unless
        ``overwrite=True``. That matches :func:`convilyn.local.convert`,
        whose docstring states the reason as a position rather than a
        default: guessing what somebody wanted is how a converter
        overwrites the wrong file. This method used to overwrite silently,
        so the same package answered that question two ways depending on
        which half of it you reached — which is not two defaults, it is no
        position at all.

        Re-running a download that already landed is the common case, and
        it is one word:

        ```python
        client.convert.download_to(job, to="out/report.pdf", overwrite=True)
        ```
        """
        url = await self.download_url(job)
        return await download_url_to_path(self._http, url, to, overwrite=overwrite)

    # ── Private steps (extensible by subclassing) ───────────────

    def _resolve_source(
        self,
        *,
        file: File | None,
        file_id: str | None,
        source_format: str | None,
    ) -> tuple[str, str]:
        """Resolve ``(file_id, source_format)`` from caller input.

        Either ``file`` or ``file_id`` must be supplied; ``source_format``
        is required when only ``file_id`` is given.

        The resolved source format always decides which processor runs. It is
        additionally *sent* only for document conversions — image and media
        requests carry no source field, because the backend reads that one off
        the stored object's key.
        """
        if file is not None and file_id is not None:
            raise TypeError("create() accepts either `file` or `file_id`, not both")
        if file is not None and not isinstance(file, File):
            # A path string is the natural guess for a parameter called `file`,
            # and the signature is already clear that it is not — but a caller
            # who does not run a type checker used to learn that from
            # `AttributeError: 'str' object has no attribute 'filename'`, two
            # frames down. The two shapes above are already TypeErrors; this is
            # the third one that was missing, not a new kind of refusal.
            raise TypeError(
                "file= expects an uploaded File object; to convert by id use "
                "file_id=, or upload first with files.upload(path) "
                f"(got {type(file).__name__})"
            )
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
        interval = max(initial_interval, MIN_POLL_INTERVAL)  # see MIN_POLL_INTERVAL
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
                detail=err.detail if err else None,
            )
        return job

    @staticmethod
    def _infer_source_format(filename: str) -> str | None:
        """The source format a filename names, or ``None`` when it has none."""
        return detect_format(filename)

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

    def download_to(
        self,
        job: ConvertJob | str,
        *,
        to: str | os.PathLike[str],
        overwrite: bool = False,
    ) -> Path:
        return self._run(self._async.download_to(job, to=to, overwrite=overwrite))

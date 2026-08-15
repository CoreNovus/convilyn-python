"""``goals.to_markdown()`` — extract unstructured content into Markdown.

Lives beside ``goals.py`` rather than inside it because that module is at its
file-size ceiling, and this capability is separable: its own artifact type, its
own selector, its own failure message.

**What this is for.** Documents whose content has to be *extracted* before it can
be written down — scanned pages with no text layer, embedded figures that need
describing. That work calls per-unit billed third-party APIs, so it is charged.

**What it is NOT for.** A plain rendered ``.md``. Deterministic
document-to-Markdown conversion ships free on every plan through the
file-conversion API, and this path will never be the cheaper way to get one. The
error message says so, because a caller who does not know that would otherwise
wait for a pipeline they never needed.

**Not yet served by any platform build.** Every call currently raises
``UnderstandUnavailableError``. The surface ships ahead of the pipeline so code
written against it keeps working unchanged when the capability is enabled
server-side — the same way ``understand()`` shipped ahead of its own rollout. A
differently-shaped result is never returned in its place: answering a request for
a FILE with a JSON object would report success while delivering something else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from convilyn.exceptions import APIError, UnderstandUnavailableError
from convilyn.types import Artifact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from convilyn.resources.goals import AsyncGoals

#: Statuses that mean the platform does not serve this shape. Mirrors
#: ``goals._UNDERSTAND_UNSUPPORTED_STATUSES`` — same question, one axis over.
_UNSERVED_STATUSES = frozenset({400, 404, 422, 501})

_UNSERVED_MESSAGE = (
    "The connected Convilyn platform does not serve Markdown output "
    "(goals.to_markdown) yet. For a deterministic document-to-Markdown render, "
    "use file conversion — it is free on every plan."
)


def select_markdown_artifact(artifacts: list[Artifact]) -> Artifact | None:
    """Pick the Markdown artifact ``to_markdown()`` should return.

    Same rule as ``goals._select_json_artifact`` one mime type over: the method
    promises Markdown, so a job with no Markdown output is an error rather than a
    silent fallback to whatever else the run happened to produce.
    """
    md = [a for a in artifacts if (a.mime_type or "").lower() == "text/markdown"]
    if not md:
        return None
    return next((a for a in md if a.is_primary), md[0])


async def fetch_markdown_artifact(goals: AsyncGoals, job_spec_id: str) -> str:
    """Fetch a completed job's primary Markdown artifact as text.

    Sibling of ``goals._fetch_json_artifact`` and deliberately not a
    generalisation of it: that one parses, this one decodes, and folding both
    into one helper would need a mode flag whose only job is to pick which of two
    return types the caller gets.
    """
    from convilyn.resources.goals import MAX_EXTRACT_JSON_BYTES

    art = select_markdown_artifact(await goals.artifacts(job_spec_id))
    if art is None:
        raise ValueError(f"job {job_spec_id} produced no Markdown artifact")
    if art.size_bytes > MAX_EXTRACT_JSON_BYTES:
        raise ValueError(
            f"artifact {art.artifact_id} is {art.size_bytes} bytes, over the "
            f"{MAX_EXTRACT_JSON_BYTES}-byte in-memory cap; use "
            "download_artifact_to() to stream it to disk instead."
        )
    info = await goals.download_artifact_url(job_spec_id, art.artifact_id)
    response = await goals._http.external_get(info.download_url)
    return response.content.decode("utf-8")


async def run_to_markdown(
    goals: AsyncGoals,
    files: list[str],
    *,
    timeout: float,
    poll_interval: float,
    idle_timeout: float | None,
) -> str:
    """Create the job, drive it to terminal, and return the rendered Markdown.

    Raises:
        ValueError: ``files`` is empty, or the completed job produced no
            Markdown artifact.
        UnderstandUnavailableError: the platform does not serve Markdown output.
            Real conditions (402 quota, 429, 5xx) propagate unchanged.
    """
    if not files:
        raise ValueError("to_markdown() requires at least one file id")
    payload: dict[str, Any] = {"fileIds": files, "outputFormat": "markdown"}
    try:
        job = await goals._create_job(payload=payload)
    except APIError as exc:
        if exc.status_code in _UNSERVED_STATUSES:
            raise UnderstandUnavailableError(_UNSERVED_MESSAGE) from exc
        raise
    # Same FSM reason as understand(): a format-routed create lands READY with no
    # slots and must be confirmed or the job parks forever.
    job = await goals._wait_loop(
        job_spec_id=job.job_spec_id,
        timeout=timeout,
        initial_interval=poll_interval,
        idle_timeout=idle_timeout,
        auto_confirm_ready=True,
    )
    return await fetch_markdown_artifact(goals, job.job_spec_id)

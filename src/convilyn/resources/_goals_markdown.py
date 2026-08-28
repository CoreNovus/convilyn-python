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

**Served, for a single file.** The request is routed by what you uploaded — a
document, an image, an audio file or a video file each has its own pipeline —
and there is no server-side switch: the flags that used to gate this path were
deleted, so a refusal is always about the request and never about an operator
state. More than one file, or a mix of kinds in one request, is refused by the
platform naming the limit; a kind with no pipeline raises
``UnderstandUnavailableError`` rather than returning something of a different
shape, because answering a request for a FILE with a JSON object would report
success while delivering something else.

This paragraph used to read "Not yet served by any platform build. Every call
currently raises ``UnderstandUnavailableError``." That stopped being true when
the markdown routing rows landed, and the equivalent claim in the wire contract
was corrected before this one — a published SDK docstring that tells a caller a
live, metered capability does not exist is the more expensive of the two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from convilyn.exceptions import (
    APIError,
    GoalArtifactUnusableError,
    UnderstandUnavailableError,
)
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

    Same rule as ``_goals_artifacts.select_json_artifact`` one mime type over: the method
    promises Markdown, so a job with no Markdown output is an error rather than a
    silent fallback to whatever else the run happened to produce.
    """
    md = [a for a in artifacts if (a.mime_type or "").lower() == "text/markdown"]
    if not md:
        return None
    return next((a for a in md if a.is_primary), md[0])


async def fetch_markdown_artifact(
    goals: AsyncGoals,
    job_spec_id: str,
    *,
    job_status: str | None = None,
) -> str:
    """Fetch a completed job's primary Markdown artifact as text.

    Sibling of ``_goals_artifacts.fetch_json_artifact`` and deliberately not a
    generalisation of it: that one parses, this one decodes, and folding both
    into one helper would need a mode flag whose only job is to pick which of two
    return types the caller gets.
    """
    from convilyn.resources.goals import MAX_EXTRACT_JSON_BYTES

    art = select_markdown_artifact(await goals.artifacts(job_spec_id))
    if art is None:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="markdown",
            reason="missing",
            job_status=job_status,
        )
    if art.size_bytes > MAX_EXTRACT_JSON_BYTES:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="markdown",
            reason="too_large",
            job_status=job_status,
            artifact_id=art.artifact_id,
            size_bytes=art.size_bytes,
            max_bytes=MAX_EXTRACT_JSON_BYTES,
        )
    info = await goals.download_artifact_url(job_spec_id, art.artifact_id)
    response = await goals._http.external_get(info.download_url)
    try:
        return response.content.decode("utf-8")
    # The JSON sibling has the same hole one decoder over: bytes that are not
    # UTF-8 used to escape as a bare builtin, invisible to `except ConvilynError:`.
    except UnicodeDecodeError as exc:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="markdown",
            reason="unparsable",
            job_status=job_status,
            artifact_id=art.artifact_id,
            detail=str(exc),
        ) from exc


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
        ValueError: ``files`` is empty — an argument mistake.
        GoalArtifactUnusableError: the job SUCCEEDED and its output cannot be
            returned — no Markdown artifact, an undecodable one, or one over the
            in-memory cap. Read ``exc.reason`` to tell them apart.
        UnderstandUnavailableError: this platform serves no pipeline for the
            kind of file you sent. Real conditions (402 quota, 429, 5xx)
            propagate unchanged.
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
    return await fetch_markdown_artifact(goals, job.job_spec_id, job_status=job.status)

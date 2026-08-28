"""Fetching a finished job's JSON artifact — selection, size gate, decode.

Lives beside ``goals.py`` rather than inside it because that module is at its
file-size ceiling, and this is the same separable shape
:mod:`convilyn.resources._goals_markdown` already has: its own artifact type,
its own selector, its own failure vocabulary. Nothing is abstracted out — one
caller, no interface, no new parameter. The body moved; that is all.

Three things can go wrong AFTER the platform reports success, and all three are
:class:`~convilyn.GoalArtifactUnusableError` rather than ``ValueError``: the
caller's arguments were fine and the run was charged.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from convilyn.exceptions import GoalArtifactUnusableError
from convilyn.types import Artifact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from convilyn.resources.goals import AsyncGoals


def select_json_artifact(artifacts: list[Artifact]) -> Artifact | None:
    """Pick the JSON artifact ``extract()`` / ``understand()`` should return.

    Prefers an ``application/json`` artifact (the primary one when flagged),
    else None — both promise JSON, so a job with no JSON output raises
    :class:`~convilyn.GoalArtifactUnusableError` rather than falling back to
    some other artifact type.
    """
    json_arts = [a for a in artifacts if (a.mime_type or "").lower() == "application/json"]
    if not json_arts:
        return None
    return next((a for a in json_arts if a.is_primary), json_arts[0])


async def fetch_json_artifact(
    goals: AsyncGoals,
    job_spec_id: str,
    *,
    job_status: str | None = None,
) -> Any:
    """Fetch a completed job's primary JSON artifact and parse it.

    ``job_status`` is threaded in so a ``"missing"`` failure can say whether the
    run was ``partial`` — the platform admits a partial run on purpose (refusing
    would turn "incomplete" into "nothing", and the money is already spent), so
    "no artifact" is frequently that rather than a defect.
    """
    from convilyn.resources.goals import MAX_EXTRACT_JSON_BYTES

    art = select_json_artifact(await goals.artifacts(job_spec_id))
    if art is None:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="json",
            reason="missing",
            job_status=job_status,
        )
    if art.size_bytes > MAX_EXTRACT_JSON_BYTES:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="json",
            reason="too_large",
            job_status=job_status,
            artifact_id=art.artifact_id,
            size_bytes=art.size_bytes,
            max_bytes=MAX_EXTRACT_JSON_BYTES,
        )
    info = await goals.download_artifact_url(job_spec_id, art.artifact_id)
    response = await goals._http.external_get(info.download_url)
    try:
        return json.loads(response.content)
    # `UnicodeDecodeError` is not a `JSONDecodeError`, and `json.loads` raises it
    # on bytes that are not UTF-8 — so a non-UTF-8 artifact used to escape as a
    # bare builtin that `except ConvilynError:` could not see, without ever
    # producing the "not valid JSON" message this branch exists to give.
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoalArtifactUnusableError(
            job_spec_id=job_spec_id,
            kind="json",
            reason="unparsable",
            job_status=job_status,
            artifact_id=art.artifact_id,
            detail=str(exc),
        ) from exc

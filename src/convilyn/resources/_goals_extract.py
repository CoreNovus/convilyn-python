"""``goals.extract()`` — the deprecated fixed-workflow JSON path.

Lives beside ``goals.py`` for the same reason ``_goals_markdown.py`` does: that
module is at its file-size ceiling, and the ratchet's failure message is an
instruction rather than a number to raise. This is the most separable thing in
it — a deprecated method whose successor (``understand``) shares none of its
body, only the ``run() -> artifacts() -> parse`` machinery both call.

The public method, its signature and its deprecation notice stay in ``goals.py``
where a reader looks for them. What moved is the mechanism.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from convilyn.types import File

if TYPE_CHECKING:  # pragma: no cover - typing only
    from convilyn.resources.goals import AsyncGoals

_DEPRECATION = (
    "goals.extract() is deprecated; use goals.understand(files, schema=...) "
    "for grounded, schema-constrained extraction. extract() runs a fixed "
    "workflow with no caller control over the output shape."
)

#: Raised when the fixed workflow parks for slot input. ``extract()`` promises a
#: single call, so a job that stops to ask a question is the method being wrong
#: for the workflow rather than a state the caller can drive from here.
_NEEDS_INPUT = (
    "extract() expected a single-step workflow, but the job stopped for "
    "slot input; use start()/fill_slot() for interactive workflows."
)


async def run_extract(
    goals: AsyncGoals,
    files: Sequence[str | File],
    *,
    workflow_id: str,
    timeout: float,
    poll_interval: float,
    idle_timeout: float | None,
    stacklevel: int,
) -> Any:
    """Warn, run the fixed workflow, and return its parsed JSON artifact.

    ``stacklevel`` is passed in rather than fixed here because the warning must
    point at the caller's ``extract()`` line, and the number of frames between
    that line and this one is a property of the call chain, not of this module.
    """
    warnings.warn(_DEPRECATION, DeprecationWarning, stacklevel=stacklevel)
    if not files:
        raise ValueError("extract() requires at least one file id")
    job = await goals.run(
        workflow_id=workflow_id,
        files=files,
        timeout=timeout,
        poll_interval=poll_interval,
        idle_timeout=idle_timeout,
    )
    if job.needs_input:
        raise ValueError(_NEEDS_INPUT)
    return await goals._fetch_json_artifact(job.job_spec_id, job_status=job.status)

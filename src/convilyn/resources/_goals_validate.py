"""Client-side admission rules for ``goals.start()``.

Split out of :mod:`convilyn.resources.goals` rather than left as a
``@staticmethod``: it reads none of the resource's state (no ``self._http``, no
instance attributes), so it was already a free function wearing a method, and
``goals.py`` is 150 lines past the 800-line budget with the file-size ratchet's
standing instruction being to extract rather than to raise the number.

Same shape as the three siblings already here — ``_goals_extract``,
``_goals_artifacts``, ``_goals_markdown`` — and, like them, deliberately
private: the public import path stays ``convilyn.resources.goals``, which is a
PUBLISHED surface.
"""

from __future__ import annotations

from collections.abc import Sequence

from convilyn.types import File


def validate_start_inputs(
    *,
    workflow_id: str | None,
    user_workflow_id: str | None,
    goal_text: str | None,
    files: Sequence[str | File] | None,
) -> None:
    """Mirror the backend's XOR + fileIds-required rules client-side.

    Exactly one workflow source (``workflow_id`` / ``user_workflow_id`` /
    ``goal_text``) must be given; ``files`` is required only on the
    ``goal_text``-only (NLP) path. Doing the check here keeps the round-trip
    count honest — a misuse turns into ``ValueError``/``TypeError`` before the
    SDK even opens a socket.
    """
    sources = {
        "workflow_id": workflow_id,
        "user_workflow_id": user_workflow_id,
        "goal_text": goal_text,
    }
    provided = [name for name, value in sources.items() if value is not None]
    if not provided:
        raise TypeError(
            "start() requires exactly one of `workflow_id`, `user_workflow_id`, or `goal_text`"
        )
    if len(provided) > 1:
        raise TypeError(
            "start() accepts exactly one of `workflow_id`, `user_workflow_id`, "
            f"or `goal_text` — not multiple (got {', '.join(sorted(provided))})"
        )
    # Only the NLP path (goal_text alone) requires files; an explicit
    # workflow_id / user_workflow_id run may collect files via checkpoints.
    if goal_text is not None and not files:
        raise ValueError("files is required when only `goal_text` is provided")

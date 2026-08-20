"""``GoalJobFailedError.detail`` / ``.suggested_action`` — which ceiling, and what next.

Its own module rather than growth in ``test_goals.py``, for the reason
``test_goal_artifact_unusable.py`` states in its own docstring: that file is at
its file-size ceiling, and the gate's instruction is *extract something instead
of raising the number*.

Before this, an AI-workflow failure gave a caller two strings::

    GoalJobFailedError: GoalJob job_test failed [PROCESSING_LIMIT]:
    The workflow reached its processing limit. Please try again.

on an iteration cap, an input-token budget, a repeated tool call and a
scratchpad read loop alike — so the caller could not tell which, nor whether
changing the input would help. Worse, the sentence points at re-running, and
re-running ``understand()`` opens a NEW job spec and is charged from scratch.

The pins that matter most are the ones about **not guessing**: an unknown
``reason`` must survive as data, and an unknown ``suggested_action`` must not be
read as permission to retry.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, GoalJobFailedError

API_BASE = "https://api.convilyn.corenovus.com"

_DETAIL = {"reason": "ITERATION_LIMIT", "limit": 15, "reached": 16}


def _job(status: str, **overrides: Any) -> dict:
    base = {
        "jobSpecId": "job_test",
        "status": status,
        "progress": 0 if status == "queued" else 100,
        "fileIds": ["file_abc"],
        "pendingSlots": [],
        "filledSlots": {},
        "pendingInterrupts": [],
        "createdAt": "2026-05-20T12:00:00Z",
        "updatedAt": "2026-05-20T12:00:01Z",
    }
    base.update(overrides)
    return base


async def _raise(**error_fields: Any) -> GoalJobFailedError:
    """Drive one `run()` to a `failed` terminal and hand back the exception."""
    fields: dict[str, Any] = {
        "errorCode": "PROCESSING_LIMIT",
        "errorMessage": "The workflow reached its processing limit.",
    }
    fields.update(error_fields)

    async with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
            return_value=httpx.Response(201, json=_job("queued"))
        )
        mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
            return_value=httpx.Response(200, json=_job("failed", **fields))
        )
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with patch("convilyn.resources.goals.asyncio.sleep", return_value=None):
                with pytest.raises(GoalJobFailedError) as info:
                    await client.goals.run(
                        workflow_id="goal_lane.content_to_multipost", files=["file_abc"]
                    )
    return info.value


class TestTheDetailReachesTheCaller:
    @pytest.mark.asyncio
    async def test_the_ceiling_and_the_count_arrive_as_integers(self) -> None:
        """Ints, so a caller can localize. Parsing them out of the English
        message is what this exists to make unnecessary."""
        exc = await _raise(errorDetail=_DETAIL)

        assert (exc.detail.limit, exc.detail.reached) == (15, 16)

    @pytest.mark.asyncio
    async def test_the_reason_is_branchable_and_the_code_is_not_new(self) -> None:
        """`code` deliberately stays `PROCESSING_LIMIT` — a new top-level code
        would send older clients down their unknown-code path. The
        discriminator rides underneath it instead."""
        exc = await _raise(errorDetail=_DETAIL)

        assert (exc.code, exc.detail.reason) == ("PROCESSING_LIMIT", "ITERATION_LIMIT")

    @pytest.mark.asyncio
    async def test_a_missing_count_stays_none_rather_than_zero(self) -> None:
        """A run resumed from a checkpoint written before the counter existed
        has no count. `0` would be a number a caller renders as fact."""
        exc = await _raise(errorDetail={"reason": "TOKEN_BUDGET", "limit": 200_000})

        assert (exc.detail.limit, exc.detail.reached) == (200_000, None)

    @pytest.mark.asyncio
    async def test_the_existing_string_form_is_unchanged(self) -> None:
        """Callers match on this. The operands are attributes, never
        interpolated into the message, so an existing `startswith` keeps
        working — and the server's promise that it emits no per-instance string
        is not quietly reopened on the client side either."""
        exc = await _raise(errorDetail=_DETAIL, suggestedAction="retry")

        assert str(exc) == (
            "GoalJob job_test failed [PROCESSING_LIMIT]: The workflow reached its processing limit."
        )


class TestTheActionTellsTheCallerWhatToDo:
    @pytest.mark.asyncio
    async def test_retryable_reads_the_servers_action(self) -> None:
        exc = await _raise(errorDetail=_DETAIL, suggestedAction="retry")

        assert (exc.suggested_action, exc.retryable) == ("retry", True)

    @pytest.mark.asyncio
    async def test_an_actionable_failure_that_is_not_retryable(self) -> None:
        """The reason a bare `retryable: bool` was rejected on the wire: a plan
        ceiling is NOT retryable but IS actionable, and a bool loses that."""
        exc = await _raise(errorCode="UPGRADE_REQUIRED", suggestedAction="upgrade")

        assert (exc.suggested_action, exc.retryable) == ("upgrade", False)


class TestWhatThisBuildDoesNotKnow:
    """Both directions of "an older client meets a newer server"."""

    @pytest.mark.asyncio
    async def test_an_unknown_reason_does_not_break_the_parse(self) -> None:
        """An unrecognised `reason` is data, not a ValidationError — pinned
        because a `Literal` here would turn a future server release into a hard
        failure on an already-published client."""
        exc = await _raise(errorDetail={"reason": "SOMETHING_NEW_ENTIRELY"})

        assert (exc.detail.reason, exc.detail.limit) == ("SOMETHING_NEW_ENTIRELY", None)

    @pytest.mark.asyncio
    async def test_an_unknown_action_is_carried_but_is_not_retryable(self) -> None:
        """`retryable` is defined as one specific action, not as "the server
        said something". A future action this build does not know must not be
        guessed into a retry — that is the guess that costs money."""
        exc = await _raise(suggestedAction="something_new")

        assert (exc.suggested_action, exc.retryable) == ("something_new", False)

    @pytest.mark.asyncio
    async def test_a_failure_without_either_field_still_raises_cleanly(self) -> None:
        """Boundary, and the direction that matters for forward compatibility:
        every failure predating these fields, and most after them, carries
        neither. The old behaviour must be exactly what it was."""
        exc = await _raise()

        assert (exc.detail, exc.suggested_action, exc.retryable) == (None, None, False)

"""An unsupported conversion is a `ConvilynError`, because the docs promise it is.

`docs/QUICKSTART.md` §4 says, of the conversion flow:

    Catch the base `ConvilynError` to handle them all uniformly.

Refusing `txt → mp3` raised a bare `ValueError`, which that `except` does not
catch — so a reader who wrote their error handling from the documentation missed
this whole class. The refusal itself was right, and happens before the upload so
it costs nothing; only the type was wrong.

**What deliberately stays a builtin.** Arguments that are simply wrong —
`upload()` with neither `path` nor `content`, `max_attempts` below zero, a
`quality` that will not coerce — keep raising `ValueError` / `TypeError`. That is
what Python means by those, and a caller cannot recover from a typo by catching a
domain error. Converting every one of ~30 such sites would make `ConvilynError`
mean "any mistake at all", which is less useful than what it means now. The line
drawn here is: *the conversion you asked for cannot be produced* is a domain
failure; *the arguments you passed do not make sense* is not.

**The second half of that line, added with the outcome failures.** A run that
finished and produced nothing usable is neither of the two cases above: the
arguments were fine and the platform is satisfied. Stated so a future sweep can
apply it without re-deriving it:

    If the SDK is telling you something about the artifacts a finished job
    produced — that there are none of the kind you asked for, that one is
    unreadable, or that one is too large for this method — it is a
    `ConvilynError`. If it is telling you that your arguments do not make sense,
    or that you called the wrong method for this job, it stays a builtin.

Mechanically: does the raise site's subject include an `Artifact`, or its
absence? If yes, taxonomy. If the subject is an argument or the caller's choice
of method, builtin. That is also why `FileExistsError` from a download stays a
builtin — its subject is the destination.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, ConvilynError, File, GoalArtifactUnusableError
from convilyn.local import UnsupportedRouteError

API_BASE = "https://api.convilyn.com"


def _file(name: str) -> File:
    return File.model_validate(
        {
            "fileId": "file_xyz",
            "fileName": name,
            "fileSize": 10,
            "mimeType": "application/octet-stream",
            "createdAt": "2026-05-20T12:00:00Z",
        }
    )


class TestTheDocumentedPromiseHolds:
    @pytest.mark.asyncio
    async def test_a_cross_family_pair_is_a_convilyn_error(self) -> None:
        """The reported case: `txt → mp3`, refused before any request."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ConvilynError):
                await client.convert.create(file=_file("notes.txt"), target_format="mp3")

    @pytest.mark.asyncio
    async def test_it_is_specifically_an_unsupported_route(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(UnsupportedRouteError):
                await client.convert.create(file=_file("notes.txt"), target_format="mp3")

    @pytest.mark.asyncio
    async def test_converting_a_format_to_itself_is_the_same_class(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(UnsupportedRouteError):
                await client.convert.create(file=_file("notes.txt"), target_format="txt")

    @pytest.mark.asyncio
    async def test_the_message_survives_the_type_change(self) -> None:
        """The refusal text was already good and names the support endpoints —
        the ticket asked for the type to change and the message to stay."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(UnsupportedRouteError, match="different conversion families"):
                await client.convert.create(file=_file("notes.txt"), target_format="mp3")

    @pytest.mark.asyncio
    async def test_the_pair_is_readable_off_the_exception(self) -> None:
        """`UnsupportedRouteError` carries the two formats as attributes, so a
        caller can act on them without parsing English."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(UnsupportedRouteError) as info:
                await client.convert.create(file=_file("notes.txt"), target_format="mp3")

        assert (info.value.source_format, info.value.target_format) == ("txt", "mp3")


class TestOutcomeFailuresAreConvilynErrors:
    """The other side of the same promise, one lane over.

    A workflow run that succeeds and yields nothing usable used to raise a bare
    `ValueError` — the same type as a typo, and invisible to the `except` the
    QUICKSTART calls sufficient. These pin that it no longer does.
    """

    @staticmethod
    def _mock_a_run_with_no_json_artifact(mock: respx.Router) -> None:
        job = {
            "jobSpecId": "job_test",
            "status": "completed",
            "progress": 100,
            "fileIds": [],
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
            return_value=httpx.Response(200, json={**job, "status": "analyzing", "progress": 0})
        )
        mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
            return_value=httpx.Response(200, json=job)
        )
        mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
            return_value=httpx.Response(200, json={"artifacts": [], "total": 0})
        )

    @pytest.mark.asyncio
    async def test_a_run_with_no_json_artifact_is_a_convilyn_error(self) -> None:
        async with respx.mock as mock:
            self._mock_a_run_with_no_json_artifact(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ConvilynError):
                    await client.goals.understand(["file_abc"], schema={"type": "object"})

    @pytest.mark.asyncio
    async def test_it_is_specifically_a_goal_artifact_unusable_error(self) -> None:
        async with respx.mock as mock:
            self._mock_a_run_with_no_json_artifact(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(GoalArtifactUnusableError):
                    await client.goals.understand(["file_abc"], schema={"type": "object"})

    @pytest.mark.asyncio
    async def test_it_is_not_a_value_error(self) -> None:
        """The claim this whole change makes, stated so it can fail: the caller
        did nothing wrong, so the type must not be the one Python uses to say
        they did."""
        async with respx.mock as mock:
            self._mock_a_run_with_no_json_artifact(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(GoalArtifactUnusableError) as info:
                    await client.goals.understand(["file_abc"], schema={"type": "object"})

        assert not isinstance(info.value, ValueError)

    @pytest.mark.asyncio
    async def test_the_reason_is_readable_off_the_exception(self) -> None:
        """Mirror of `test_the_pair_is_readable_off_the_exception`: branching on
        the failure must not mean parsing the message."""
        async with respx.mock as mock:
            self._mock_a_run_with_no_json_artifact(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(GoalArtifactUnusableError) as info:
                    await client.goals.understand(["file_abc"], schema={"type": "object"})

        assert (info.value.reason, info.value.kind) == ("missing", "json")


class TestArgumentMistakesStayBuiltins:
    """Pinned on purpose, so a later sweep does not quietly widen the taxonomy."""

    @pytest.mark.asyncio
    async def test_neither_file_nor_file_id_is_a_type_error(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError):
                await client.convert.create(target_format="pdf")

    @pytest.mark.asyncio
    async def test_page_range_on_an_image_conversion_is_a_value_error(self) -> None:
        """A parameter that does not apply to the family you selected is a
        mistake in the call, not a conversion this platform cannot perform."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError):
                await client.convert.create(
                    file=_file("photo.png"), target_format="webp", page_range="1-2"
                )

    @pytest.mark.asyncio
    async def test_that_value_error_is_not_a_convilyn_error(self) -> None:
        """States the boundary from the other side: widening it later would make
        this test fail rather than pass silently."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError) as info:
                await client.convert.create(
                    file=_file("photo.png"), target_format="webp", page_range="1-2"
                )

        assert not isinstance(info.value, ConvilynError)

    @pytest.mark.asyncio
    async def test_an_empty_file_list_is_still_a_bare_value_error(self) -> None:
        """The workflow lane's own argument mistake. Retyping the OUTCOME
        failures must not sweep the ARGUMENT failures with them."""
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError) as info:
                await client.goals.understand([], schema={"type": "object"})

        assert not isinstance(info.value, ConvilynError)

    @pytest.mark.asyncio
    async def test_a_non_dict_schema_is_still_a_bare_type_error(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(TypeError) as info:
                await client.goals.understand(["file_abc"], schema="not a dict")  # type: ignore[arg-type]

        assert not isinstance(info.value, ConvilynError)

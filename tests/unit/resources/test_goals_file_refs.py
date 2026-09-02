"""A `File` is accepted everywhere a file id is — and by every method, not the
one that got reported.

`files.upload()` returns a `File`; `convert.create(file=...)` takes that object.
Handing the same object to `goals.understand(files=[...])` raised

    TypeError: Object of type File is not JSON serializable

from inside httpx while encoding the request body, naming neither the method nor
the parameter nor what to pass instead.

External testing reported it against `understand()`. It was never confined
there: `start()`, `extract()`, `run_interactive()` and `to_markdown()` all built
`fileIds` from the caller's list the same way. So the boundary tests below
enumerate the methods from their own SIGNATURES rather than naming the ones that
exist today — a new method added with `list[str]` fails here instead of
shipping.

New file rather than an addition to `test_goals.py`, which the file-size ratchet
has grandfathered and whose failure message is an instruction: *"Extract
something instead of raising the number."*
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn
from convilyn._internal.file_refs import file_ids
from convilyn.resources import _goals_markdown as markdown_module
from convilyn.resources import goals as goals_module
from convilyn.types import File

API_BASE = "https://api.convilyn.com"
_SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}


def _file(file_id: str = "file_abc") -> File:
    return File(
        fileId=file_id,
        fileName="report.pdf",
        fileSize=11,
        mimeType="application/pdf",
        createdAt=datetime.now(timezone.utc),
    )


# ── 1. Logic — the normaliser itself ─────────────────────────────────


class TestTheNormaliser:
    def test_it_passes_ids_through(self) -> None:
        assert file_ids(["file_a", "file_b"]) == ["file_a", "file_b"]

    def test_it_unwraps_a_file(self) -> None:
        assert file_ids([_file("file_x")]) == ["file_x"]

    def test_it_takes_a_mix(self) -> None:
        """A caller holding some of each should not have to normalise first —
        that is why this exists rather than a docstring saying "call
        .file_id"."""
        assert file_ids(["file_a", _file("file_b")]) == ["file_a", "file_b"]


# ── 2. Error — anything else names the fix ───────────────────────────


class TestTheRefusal:
    def test_it_names_the_parameter_and_the_fix(self) -> None:
        with pytest.raises(TypeError, match="files= expects file ids"):
            file_ids([object()])

    def test_it_echoes_the_type_that_arrived(self) -> None:
        """House shape, set by `convert.py`'s `_resolve_source`: name what was
        expected, name the way to get it, echo what came."""
        with pytest.raises(TypeError, match="got int"):
            file_ids([3])  # type: ignore[list-item]


# ── 3. Boundary — every method that takes files, derived ─────────────


def _files_parameters() -> list[tuple[str, inspect.Parameter]]:
    """Every public method on either goals facade carrying a `files` parameter.

    Derived from the signatures. A hand-written list is a list somebody has to
    remember to extend, and a list nobody extended is what this defect was.
    """
    found: list[tuple[str, inspect.Parameter]] = []
    for cls in (goals_module.AsyncGoals, goals_module.Goals):
        for name, member in vars(cls).items():
            if name.startswith("_") or not callable(member):
                continue
            parameter = inspect.signature(member).parameters.get("files")
            if parameter is not None:
                found.append((f"{cls.__name__}.{name}", parameter))
    found.append(
        (
            "run_to_markdown",
            inspect.signature(markdown_module.run_to_markdown).parameters["files"],
        )
    )
    return found


_FILES_PARAMETERS = _files_parameters()


class TestEveryFilesParameterAdmitsAFile:
    def test_there_are_parameters_to_check(self) -> None:
        """Vacuity guard. Both parametrized tests below iterate this list, and a
        renamed parameter or a moved class would empty it silently — pytest
        scores an empty parametrize as SKIPPED, which reads as green."""
        assert len(_FILES_PARAMETERS) >= 7

    @pytest.mark.parametrize("name,parameter", _FILES_PARAMETERS, ids=lambda v: str(v))
    def test_the_annotation_admits_a_file(self, name: str, parameter: inspect.Parameter) -> None:
        assert "File" in str(parameter.annotation), f"{name} still takes ids only"

    @pytest.mark.parametrize("name,parameter", _FILES_PARAMETERS, ids=lambda v: str(v))
    def test_the_annotation_is_covariant(self, name: str, parameter: inspect.Parameter) -> None:
        """`Sequence`, not `list`.

        `list` is INVARIANT, so widening it to `list[str | File]` would have
        REJECTED every existing caller passing a plain `list[str]` — pyright
        said so on the first attempt. That would have been a silent break for
        typed callers and a no-op for everyone else, which is the worse of the
        two directions.
        """
        assert "Sequence" in str(parameter.annotation), f"{name} is invariant"


# ── 4. Object state — a File reaches the wire as its id ──────────────


class TestAFileReachesTheWireAsAnId:
    """The annotations above are a promise; this is the payload."""

    @pytest.mark.asyncio
    async def test_understand_sends_the_file_id(self) -> None:
        sent: dict[str, object] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            sent.update(json.loads(request.content))
            return httpx.Response(500, json={"detail": "stop before polling"})

        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(side_effect=_capture)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                #: `APIError` and not a bare `Exception`: the 500 is only a stop
                #: so the test does not have to mock the whole poll loop. Naming
                #: the class is what makes this test FAIL on the old code, where
                #: the same call raised `TypeError` out of the JSON encoder —
                #: `except Exception` would have swallowed exactly the defect.
                with pytest.raises(APIError):
                    await client.goals.understand([_file("file_zzz")], schema=_SCHEMA)

        assert sent["fileIds"] == ["file_zzz"]

    @pytest.mark.asyncio
    async def test_a_wrong_type_is_refused_before_any_request(self) -> None:
        """Why it is raised here and not in the encoder: no round trip, and the
        frame that raises still holds the parameter that was misused."""
        async with respx.mock as mock:
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(TypeError, match="files= expects file ids"):
                    await client.goals.understand([object()], schema=_SCHEMA)  # type: ignore[list-item]
        assert len(mock.calls) == 0

"""``GoalArtifactUnusableError`` — the run succeeded and there is nothing to return.

Its own module rather than growth in ``test_goals.py``: that file is at its
file-size ceiling, and this is one failure mode across three methods
(``extract`` / ``understand`` / ``to_markdown``) and two artifact kinds, which is
a matrix rather than a case.

The pins that matter most are the negative ones. The whole claim of this type is
that a post-success failure is NOT an argument mistake, and that claim is only
falsifiable if something fails when the type drifts back toward ``ValueError``
or toward ``GoalJobFailedError``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, ConvilynError, GoalArtifactUnusableError, GoalJobFailedError
from convilyn.resources.goals import MAX_EXTRACT_JSON_BYTES

API_BASE = "https://api.convilyn.corenovus.com"
_STORAGE_URL = "https://storage.example.com/bucket/out?sig=xyz"


def _job(status: str, **overrides: Any) -> dict:
    base = {
        "jobSpecId": "job_test",
        "status": status,
        "progress": 100 if status in {"completed", "partial"} else 0,
        "fileIds": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _artifact(**overrides: Any) -> dict:
    base = {
        "artifactId": "art_1",
        "fileName": "out.json",
        "mimeType": "application/json",
        "sizeBytes": 64,
        "downloadUrl": _STORAGE_URL,
        "artifactType": "json",
        "metadata": None,
        "isPrimary": True,
        "description": "",
    }
    base.update(overrides)
    return base


def _md_artifact(**overrides: Any) -> dict:
    return _artifact(
        mimeType="text/markdown", artifactType="markdown", fileName="out.md", **overrides
    )


def _wire(
    mock: respx.Router,
    *,
    artifacts: list[dict],
    body: bytes = b'{"ok": true}',
    status: str = "completed",
) -> None:
    """create -> poll -> artifacts -> presign -> storage."""
    mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
        return_value=httpx.Response(200, json=_job("analyzing"))
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
        return_value=httpx.Response(200, json=_job(status))
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": artifacts, "total": len(artifacts)})
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_1/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "downloadUrl": _STORAGE_URL,
                "fileName": "out.json",
                "sizeBytes": 64,
                "mimeType": "application/json",
                "expiresAt": "2026-07-12T13:00:00Z",
            },
        )
    )
    mock.get(_STORAGE_URL).mock(return_value=httpx.Response(200, content=body))


async def _understand() -> GoalArtifactUnusableError:
    async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
        with pytest.raises(GoalArtifactUnusableError) as info:
            await client.goals.understand(["file_abc"], schema={"type": "object"})
    return info.value


async def _to_markdown() -> GoalArtifactUnusableError:
    async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
        with pytest.raises(GoalArtifactUnusableError) as info:
            await client.goals.to_markdown(["file_abc"])
    return info.value


# ── logic ───────────────────────────────────────────────────────────


class TestTheReasonIsReadable:
    @pytest.mark.asyncio
    async def test_no_json_artifact_is_reason_missing(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_md_artifact()])
            exc = await _understand()
        assert (exc.reason, exc.kind, exc.job_spec_id) == ("missing", "json", "job_test")

    @pytest.mark.asyncio
    async def test_missing_carries_no_artifact_id(self) -> None:
        """There is no artifact to name — inventing one would put a fiction in
        the attribute a caller is meant to trust."""
        async with respx.mock as mock:
            _wire(mock, artifacts=[_md_artifact()])
            exc = await _understand()
        assert exc.artifact_id is None

    @pytest.mark.asyncio
    async def test_a_partial_run_says_so(self) -> None:
        """The single most useful operand for ``missing``: the platform admits a
        partial run on purpose, so "no artifact" is often that rather than a
        defect — and these three methods never return the job to ask."""
        async with respx.mock as mock:
            _wire(mock, artifacts=[_md_artifact()], status="partial")
            exc = await _understand()
        assert exc.job_status == "partial"

    @pytest.mark.asyncio
    async def test_oversized_carries_the_operands(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact(sizeBytes=64 * 1024 * 1024)])
            exc = await _understand()
        assert (exc.reason, exc.artifact_id, exc.size_bytes, exc.max_bytes) == (
            "too_large",
            "art_1",
            64 * 1024 * 1024,
            MAX_EXTRACT_JSON_BYTES,
        )

    @pytest.mark.asyncio
    async def test_unparsable_carries_the_decoders_words(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact()], body=b"{not json")
            exc = await _understand()
        assert (exc.reason, exc.artifact_id, bool(exc.detail)) == ("unparsable", "art_1", True)

    @pytest.mark.asyncio
    async def test_markdown_kind_is_markdown(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact()])
            exc = await _to_markdown()
        assert (exc.reason, exc.kind) == ("missing", "markdown")


# ── boundary ────────────────────────────────────────────────────────


class TestTheSizeCapBoundary:
    @pytest.mark.asyncio
    async def test_exactly_at_the_cap_is_allowed(self) -> None:
        """The gate is ``>``, not ``>=``. An artifact of exactly the cap fits."""
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact(sizeBytes=MAX_EXTRACT_JSON_BYTES)])
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                result = await client.goals.understand(["file_abc"], schema={"type": "object"})
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_one_byte_over_the_cap_raises(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact(sizeBytes=MAX_EXTRACT_JSON_BYTES + 1)])
            exc = await _understand()
        assert exc.reason == "too_large"


class TestNonUtf8DoesNotEscapeAsABuiltin:
    """``json.loads(bytes)`` raises ``UnicodeDecodeError`` — NOT a
    ``JSONDecodeError`` — for bytes that are not UTF-8. Before this type, such an
    artifact escaped as a bare builtin that ``except ConvilynError:`` could not
    see, and the "not valid JSON" message never appeared."""

    @pytest.mark.asyncio
    async def test_json_path(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact()], body=b"\x80\x81")
            exc = await _understand()
        assert exc.reason == "unparsable"

    @pytest.mark.asyncio
    async def test_markdown_path(self) -> None:
        async with respx.mock as mock:
            _wire(mock, artifacts=[_md_artifact()], body=b"\x80\x81")
            exc = await _to_markdown()
        assert exc.reason == "unparsable"


# ── error / taxonomy contract ───────────────────────────────────────


class TestTheTaxonomyClaim:
    def test_it_is_a_convilyn_error(self) -> None:
        assert issubclass(GoalArtifactUnusableError, ConvilynError)

    def test_it_is_not_a_value_error(self) -> None:
        """The pin that makes the whole change falsifiable. A dual-inheriting
        ``(ConvilynError, ValueError)`` would keep every ``except ValueError``
        working — and would re-assert the exact claim this type exists to deny."""
        assert not issubclass(GoalArtifactUnusableError, ValueError)

    def test_it_is_not_a_failed_job(self) -> None:
        """The job SUCCEEDED. Parenting this under ``GoalJobFailedError`` would
        make ``except GoalJobFailedError:`` start catching successes."""
        assert not issubclass(GoalArtifactUnusableError, GoalJobFailedError)

    @pytest.mark.parametrize(
        ("kwargs", "needle"),
        [
            ({"reason": "missing"}, "no JSON artifact"),
            (
                {"reason": "too_large", "artifact_id": "a", "size_bytes": 9, "max_bytes": 8},
                "in-memory cap",
            ),
            (
                {"reason": "too_large", "artifact_id": "a", "size_bytes": 9, "max_bytes": 8},
                "download_artifact_to",
            ),
            ({"reason": "unparsable", "artifact_id": "a", "detail": "boom"}, "not valid JSON"),
        ],
    )
    def test_the_message_keeps_the_substring_callers_already_match(
        self, kwargs: dict[str, Any], needle: str
    ) -> None:
        """Four substrings the pre-existing tests ``match=`` on. Retyping the
        exception must not also silently reword it."""
        exc = GoalArtifactUnusableError(job_spec_id="job_test", kind="json", **kwargs)
        assert needle in str(exc)

    def test_the_oversize_message_is_copy_pasteable(self) -> None:
        """The advice names a call the caller can now actually make, because both
        arguments are in the message and on the exception."""
        exc = GoalArtifactUnusableError(
            job_spec_id="job_x",
            kind="json",
            reason="too_large",
            artifact_id="art_y",
            size_bytes=9,
            max_bytes=8,
        )
        assert "'job_x', 'art_y'" in str(exc)


# ── object state ────────────────────────────────────────────────────


class TestConstructionNeverRaises:
    """An exception whose ``__init__`` can fail replaces the real failure with a
    second one, so nothing here validates — these assert that."""

    @pytest.mark.parametrize("reason", ["missing", "unparsable", "too_large"])
    @pytest.mark.parametrize("kind", ["json", "markdown"])
    def test_every_reason_and_kind_constructs_with_no_operands(
        self, reason: Any, kind: Any
    ) -> None:
        exc = GoalArtifactUnusableError(job_spec_id="j", kind=kind, reason=reason)
        assert (exc.reason, exc.kind) == (reason, kind)

    def test_unset_operands_are_none(self) -> None:
        exc = GoalArtifactUnusableError(job_spec_id="j", kind="json", reason="missing")
        assert (exc.artifact_id, exc.size_bytes, exc.max_bytes, exc.detail, exc.job_status) == (
            None,
            None,
            None,
            None,
            None,
        )


# ── the selector, which moved with its function ─────────────────────


class TestSelectJsonArtifact:
    def test_prefers_primary(self) -> None:
        from convilyn.resources._goals_artifacts import select_json_artifact
        from convilyn.types import Artifact

        arts = [
            Artifact.model_validate(_artifact(artifactId="j1", isPrimary=False)),
            Artifact.model_validate(_artifact(artifactId="j2", isPrimary=True)),
        ]
        picked = select_json_artifact(arts)
        assert picked is not None and picked.artifact_id == "j2"

    def test_none_when_no_json(self) -> None:
        from convilyn.resources._goals_artifacts import select_json_artifact
        from convilyn.types import Artifact

        assert select_json_artifact([Artifact.model_validate(_md_artifact())]) is None


# ── recovery: the point of carrying the operands ─────────────────────


class TestTheOversizeRecoveryIsExecutable:
    @pytest.mark.asyncio
    async def test_the_caught_exception_feeds_download_artifact_to(self, tmp_path: Any) -> None:
        """Before this type there was no way to write this: ``understand()``
        returns parsed JSON and never a job handle, so the message's advice named
        a call whose two arguments the caller did not have."""
        dest = tmp_path / "out.json"
        async with respx.mock as mock:
            _wire(mock, artifacts=[_artifact(sizeBytes=64 * 1024 * 1024)])
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                try:
                    await client.goals.understand(["file_abc"], schema={"type": "object"})
                except GoalArtifactUnusableError as exc:
                    assert exc.reason == "too_large"
                    await client.goals.download_artifact_to(
                        exc.job_spec_id, exc.artifact_id or "", to=dest
                    )
        assert json.loads(dest.read_text(encoding="utf-8")) == {"ok": True}

"""``goals.to_markdown()`` — the metered extraction path (#3899).

Split out of ``test_goals.py`` rather than appended to it: that module is at its
file-size ceiling, and this capability has its own artifact type, its own error
message and its own reason to exist.

The error case is the one that runs in production today — no platform build
serves Markdown yet — so it is tested first and hardest. The success path is
tested so the surface is proven correct for the day the pipeline is enabled.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from convilyn import APIError, AsyncConvilyn, Convilyn, UnderstandUnavailableError

API_BASE = "https://api.convilyn.corenovus.com"


def _job_response(status: str) -> dict:
    return {
        "jobSpecId": "job_test",
        "status": status,
        "progress": 0,
        "fileIds": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }


def _artifact(**overrides: Any) -> dict:
    base = {
        "artifactId": "art_json",
        "fileName": "out.md",
        "mimeType": "text/markdown",
        "sizeBytes": 64,
        "downloadUrl": "https://storage.example.com/bucket/doc.json?sig=abc",
        "artifactType": "markdown",
        "platform": None,
        "metadata": None,
        "isPrimary": True,
        "description": "",
    }
    base.update(overrides)
    return base


def _md_artifact_payload(**overrides: Any) -> dict:
    return _artifact(**overrides)


def _json_artifact_payload(**overrides: Any) -> dict:
    return _artifact(mimeType="application/json", artifactType="json", **overrides)


def _mock_extract_chain(mock: respx.Router, *, artifacts: list[dict], storage_json: Any) -> Any:
    """Wire create -> poll -> artifacts -> presign -> storage; return the POST route."""
    post = mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
        return_value=httpx.Response(200, json=_job_response("analyzing"))
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test").mock(
        return_value=httpx.Response(200, json={**_job_response("completed"), "progress": 100})
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts").mock(
        return_value=httpx.Response(200, json={"artifacts": artifacts, "total": len(artifacts)})
    )
    mock.get(f"{API_BASE}/api/v1/jobs/goal/job_test/artifacts/art_json/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "downloadUrl": "https://storage.example.com/bucket/doc.json?sig=xyz",
                "fileName": "out.md",
                "sizeBytes": 64,
                "mimeType": "text/markdown",
                "expiresAt": "2026-07-12T13:00:00Z",
            },
        )
    )
    mock.get("https://storage.example.com/bucket/doc.json?sig=xyz").mock(
        return_value=httpx.Response(200, content=json.dumps(storage_json).encode("utf-8"))
    )
    return post


class TestToMarkdown:
    """The metered extraction path. Not served by any platform build yet, so the
    error case is the one that runs in production today — it is tested first and
    hardest, and the success path is tested so the surface is proven correct for
    the day the pipeline is enabled."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 404, 422, 501])
    async def test_unserved_backend_raises_a_named_error_not_a_bare_400(self, status: int) -> None:
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(status, json={"code": "x", "message": "no"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError):
                    await client.goals.to_markdown(["file_abc"])

    @pytest.mark.asyncio
    async def test_the_error_names_the_free_alternative(self) -> None:
        """A caller who only wants a plain rendered .md must not be left waiting
        on this pipeline — deterministic conversion already does it at no cost."""
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(400, json={"code": "x", "message": "no"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError, match="free on every plan"):
                    await client.goals.to_markdown(["file_abc"])

    @pytest.mark.asyncio
    async def test_real_conditions_propagate_not_swallowed(self) -> None:
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(402, json={"code": "TIER_REQUIRED", "message": "pay"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(APIError) as info:
                    await client.goals.to_markdown(["file_abc"])
        assert not isinstance(info.value, UnderstandUnavailableError)

    @pytest.mark.asyncio
    async def test_empty_files_raises_before_network(self) -> None:
        async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
            with pytest.raises(ValueError, match="at least one file"):
                await client.goals.to_markdown([])

    @pytest.mark.asyncio
    async def test_sends_output_format_and_no_workflow_source(self) -> None:
        async with respx.mock(assert_all_called=True) as mock:
            post = _mock_extract_chain(
                mock, artifacts=[_md_artifact_payload()], storage_json="# Title"
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.goals.to_markdown(["file_abc"])

        body = json.loads(post.calls.last.request.content)
        assert body == {"fileIds": ["file_abc"], "outputFormat": "markdown"}

    @pytest.mark.asyncio
    async def test_no_markdown_artifact_is_an_error_not_a_fallback(self) -> None:
        """The method promises Markdown, so a run with only a JSON artifact is a
        failure — never a silent hand-back of whatever else was produced."""
        async with respx.mock as mock:
            _mock_extract_chain(mock, artifacts=[_json_artifact_payload()], storage_json={})
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="no Markdown artifact"):
                    await client.goals.to_markdown(["file_abc"])

    @pytest.mark.asyncio
    async def test_oversized_artifact_is_refused_rather_than_buffered(self) -> None:
        async with respx.mock as mock:
            _mock_extract_chain(
                mock,
                artifacts=[_md_artifact_payload(sizeBytes=9 * 1024 * 1024)],
                storage_json="x",
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="download_artifact_to"):
                    await client.goals.to_markdown(["file_abc"])

    def test_sync_to_markdown_is_wired(self) -> None:
        client = Convilyn(api_key="ck_x", base_url=API_BASE)  # pragma: allowlist secret
        assert hasattr(client.goals, "to_markdown")

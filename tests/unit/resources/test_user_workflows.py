"""UserWorkflows resource — logic / boundary / error / object-state.

Mirrors :mod:`tests.unit.resources.test_workflows`: respx mocks the HTTP
layer; assertions land on observable wire behaviour (routes hit, params
threaded, typed models returned, typed-exception identity).
"""

from __future__ import annotations

from typing import Any

import httpx
import pydantic
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    UserWorkflowDetail,
    UserWorkflowExport,
    UserWorkflowRun,
    UserWorkflowsPage,
)

API_BASE = "https://api.convilyn.corenovus.com"
UW = "uw_abc123"


def _summary_wire(workflow_id: str = UW) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "specId": f"user_me.{workflow_id}",
        "name": "My Vertical",
        "descriptionPreview": "Explains POS errors.",
        "visibility": "private",
        "tags": ["pos"],
        "stats": {"runCount": 4, "forkCount": 0, "likeCount": 1},
        "sourceType": "builder",
        "sourceSpecId": None,
        "updatedAt": "2026-07-20T10:00:00Z",
        "ownerId": "user_me",
        "ownerDisplayName": "swift falcon",
    }


def _detail_wire(workflow_id: str = UW) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "ownerId": "user_me",
        "ownerDisplayName": "swift falcon",
        "specId": f"user_me.{workflow_id}",
        "sourceSpecId": None,
        "sourceType": "builder",
        "sourceVersion": "1.0.0",
        "name": "My Vertical",
        "description": "Explains POS errors.",
        "systemPrompt": "You are…",
        "specJson": {"phases": []},
        "visibility": "private",
        "tags": ["pos"],
        "foundingIntent": None,
        "stats": {"runCount": 4, "forkCount": 0, "likeCount": 1},
        "createdAt": "2026-07-01T00:00:00Z",
        "updatedAt": "2026-07-20T10:00:00Z",
        "itemVersion": 3,
        # builder-UI wire fields the SDK deliberately does not bind:
        "toolPalette": [],
        "canvasLayout": {"edges": [], "viewport": None},
        "examplePairs": None,
    }


def _run_wire(job: str = "job-1") -> dict[str, Any]:
    return {
        "jobSpecId": job,
        "status": "completed",
        "startedAt": "2026-07-20T10:00:00Z",
        "completedAt": "2026-07-20T10:01:00Z",
        "progress": 100,
        "errorCode": None,
    }


@pytest.fixture
def client() -> AsyncConvilyn:
    return AsyncConvilyn(
        api_key="ck_test_user_workflows",  # pragma: allowlist secret
        base_url=API_BASE,
    )


# ── 1. Logic — happy path per verb ──────────────────────────────────


class TestListLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_returns_typed_page(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(
                200, json={"items": [_summary_wire("uw_a"), _summary_wire("uw_b")], "cursor": None}
            )
        )

        page = await client.user_workflows.list()

        assert [s.workflow_id for s in page.items] == ["uw_a", "uw_b"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_threads_filters(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": "next"}),
        )

        await client.user_workflows.list(visibility="private", limit=5, cursor="prev")

        assert dict(route.calls[0].request.url.params) == {
            "visibility": "private",
            "limit": "5",
            "cursor": "prev",
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_returns_typed_detail(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(200, json=_detail_wire())
        )

        wf = await client.user_workflows.get(UW)

        assert (wf.workflow_id, wf.system_prompt, wf.item_version) == (UW, "You are…", 3)

    @pytest.mark.asyncio
    @respx.mock
    async def test_delete_returns_none_on_204(self, client: AsyncConvilyn) -> None:
        respx.delete(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(204)
        )

        assert await client.user_workflows.delete(UW) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_export_wraps_document_and_header(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/export").mock(
            return_value=httpx.Response(
                200,
                json={"schemaVersion": "3", "name": "My Vertical"},
                headers={"X-Export-Schema-Version": "3"},
            )
        )

        export = await client.user_workflows.export(UW)

        assert export == UserWorkflowExport(
            document={"schemaVersion": "3", "name": "My Vertical"}, schema_version="3"
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_grounded_contract_returns_raw_wire(self, client: AsyncConvilyn) -> None:
        wire = {"contract_id": f"user_me.{UW}", "fields": [], "prompt_template": "p"}
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/grounded-contract").mock(
            return_value=httpx.Response(200, json=wire)
        )

        contract = await client.user_workflows.grounded_contract(UW)

        assert contract == wire

    @pytest.mark.asyncio
    @respx.mock
    async def test_grounded_contract_threads_binding_and_locale(
        self, client: AsyncConvilyn
    ) -> None:
        route = respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/grounded-contract").mock(
            return_value=httpx.Response(200, json={})
        )

        await client.user_workflows.grounded_contract(
            UW, model_binding="local-qwen3-4b", locale="ja"
        )

        assert dict(route.calls[0].request.url.params) == {
            "model_binding": "local-qwen3-4b",
            "locale": "ja",
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_runs_returns_typed_rows(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/runs").mock(
            return_value=httpx.Response(
                200, json={"items": [_run_wire("job-1"), _run_wire("job-2")]}
            )
        )

        runs = await client.user_workflows.runs(UW)

        assert [r.job_spec_id for r in runs] == ["job-1", "job-2"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_runs_threads_limit(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/runs").mock(
            return_value=httpx.Response(200, json={"items": []})
        )

        await client.user_workflows.runs(UW, limit=2)

        assert dict(route.calls[0].request.url.params) == {"limit": "2"}


# ── 2. Boundary ──────────────────────────────────────────────────────


class TestBoundary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_without_filters_sends_no_params(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": None})
        )

        await client.user_workflows.list()

        assert dict(route.calls[0].request.url.params) == {}

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_empty_page_has_no_items(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": None})
        )

        page = await client.user_workflows.list()

        assert (page.items, page.cursor) == ([], None)

    @pytest.mark.asyncio
    @respx.mock
    async def test_export_without_version_header_is_none(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/export").mock(
            return_value=httpx.Response(200, json={})
        )

        export = await client.user_workflows.export(UW)

        assert export.schema_version is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_detail_tolerates_unknown_wire_fields(self, client: AsyncConvilyn) -> None:
        wire = _detail_wire() | {"futureField": {"x": 1}}
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(200, json=wire)
        )

        wf = await client.user_workflows.get(UW)

        assert wf.workflow_id == UW


# ── 3. Error handling ────────────────────────────────────────────────


class TestErrors:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_missing_raises_api_error_404(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(
                404,
                json={"code": "WORKFLOW_NOT_FOUND", "message": "Workflow not found"},
            )
        )

        with pytest.raises(APIError) as info:
            await client.user_workflows.get(UW)

        assert info.value.status_code == 404
        assert info.value.code == "WORKFLOW_NOT_FOUND"

    @pytest.mark.asyncio
    @respx.mock
    async def test_delete_public_raises_api_error_409(self, client: AsyncConvilyn) -> None:
        respx.delete(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(
                409,
                json={
                    "code": "WORKFLOW_IS_PUBLIC_USE_ARCHIVE",
                    "message": "Archive the workflow before deleting.",
                },
            )
        )

        with pytest.raises(APIError) as info:
            await client.user_workflows.delete(UW)

        assert info.value.status_code == 409
        assert info.value.code == "WORKFLOW_IS_PUBLIC_USE_ARCHIVE"


# ── 4. Object state ──────────────────────────────────────────────────


class TestObjectState:
    @pytest.mark.asyncio
    @respx.mock
    async def test_detail_model_is_immutable(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(200, json=_detail_wire())
        )
        wf = await client.user_workflows.get(UW)

        with pytest.raises(pydantic.ValidationError):
            wf.name = "mutated"  # type: ignore[misc]

    @respx.mock
    def test_sync_facade_round_trips_all_five_verbs(self) -> None:
        import convilyn

        respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(200, json={"items": [_summary_wire()], "cursor": None})
        )
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(200, json=_detail_wire())
        )
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/export").mock(
            return_value=httpx.Response(200, json={}, headers={"X-Export-Schema-Version": "3"})
        )
        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/runs").mock(
            return_value=httpx.Response(200, json={"items": [_run_wire()]})
        )
        respx.delete(f"{API_BASE}/api/v1/user_workflows/{UW}").mock(
            return_value=httpx.Response(204)
        )
        sync_client = convilyn.Convilyn(
            api_key="ck_test_sync_verbs",  # pragma: allowlist secret
            base_url=API_BASE,
        )
        try:
            observed = (
                sync_client.user_workflows.list().items[0].workflow_id,
                sync_client.user_workflows.get(UW).workflow_id,
                sync_client.user_workflows.export(UW).schema_version,
                sync_client.user_workflows.runs(UW)[0].job_spec_id,
                sync_client.user_workflows.delete(UW),
            )
        finally:
            sync_client.close()

        assert observed == (UW, UW, "3", "job-1", None)

    @respx.mock
    def test_sync_facade_round_trips_grounded_contract(self) -> None:
        import convilyn

        respx.get(f"{API_BASE}/api/v1/user_workflows/{UW}/grounded-contract").mock(
            return_value=httpx.Response(200, json={"contract_id": f"user_me.{UW}"})
        )
        sync_client = convilyn.Convilyn(
            api_key="ck_test_sync_contract",  # pragma: allowlist secret
            base_url=API_BASE,
        )
        try:
            contract = sync_client.user_workflows.grounded_contract(UW)
        finally:
            sync_client.close()

        assert contract == {"contract_id": f"user_me.{UW}"}

    def test_sync_facade_mirrors_async_surface(self) -> None:
        import convilyn

        sync_client = convilyn.Convilyn(
            api_key="ck_test_sync"  # pragma: allowlist secret
        )
        try:
            async_methods = {
                m for m in dir(sync_client.async_client.user_workflows) if not m.startswith("_")
            }
            sync_methods = {m for m in dir(sync_client.user_workflows) if not m.startswith("_")}

            assert sync_methods == async_methods
        finally:
            sync_client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_page_model_round_trips_wire_aliases(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/user_workflows").mock(
            return_value=httpx.Response(200, json={"items": [_summary_wire()], "cursor": "c1"})
        )
        page = await client.user_workflows.list()

        assert page.model_dump(by_alias=True, exclude_none=False)["items"][0]["workflowId"] == UW


# typed models re-exported at top level (frozen-surface companions)


def test_models_exported_at_top_level() -> None:
    import convilyn

    assert all(
        hasattr(convilyn, name)
        for name in (
            "UserWorkflowSummary",
            "UserWorkflowsPage",
            "UserWorkflowDetail",
            "UserWorkflowRun",
            "UserWorkflowExport",
        )
    )


def test_run_model_parses_wire(client: AsyncConvilyn) -> None:
    run = UserWorkflowRun.model_validate(_run_wire())

    assert (run.job_spec_id, run.status, run.error_code) == ("job-1", "completed", None)


def test_detail_type_is_user_workflow_detail() -> None:
    wf = UserWorkflowDetail.model_validate(_detail_wire())

    assert isinstance(wf, UserWorkflowDetail)


def test_page_type_is_user_workflows_page() -> None:
    page = UserWorkflowsPage.model_validate({"items": [_summary_wire()], "cursor": None})

    assert isinstance(page, UserWorkflowsPage)

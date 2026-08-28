"""Workflows resource — logic / boundary / error / object-state.

Mirrors :mod:`tests.test_goals` and :mod:`tests.test_convert`: respx
mocks the HTTP layer; assertions land on observable wire behaviour
(route call counts, payload shape, typed-exception identity) so the
orchestration can evolve without test churn.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    LikeResponse,
    Workflow,
    WorkflowSearchPage,
)

API_BASE = "https://api.convilyn.corenovus.com"


def _workflow_wire(
    workflow_id: str = "uw_abc123",
    *,
    name: str = "Doc Analyzer",
    visibility: str = "private",
    spec_id: str = "user_xyz.uw_abc123",
    item_version: int = 1,
    like_count: int = 0,
) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "ownerId": "user_owner_1",
        "specId": spec_id,
        "sourceSpecId": "goal_lane.content_to_multipost",
        "sourceType": "curated",
        "sourceVersion": "1.0.0",
        "name": name,
        "description": "Analyse documents end-to-end.",
        "systemPrompt": "...",
        "toolPalette": [],
        "canvasLayout": {"edges": [], "viewport": None},
        "specJson": {},
        "visibility": visibility,
        "tags": ["analysis"],
        "foundingIntent": None,
        "examplePairs": None,
        "stats": {"runCount": 0, "forkCount": 0, "likeCount": like_count},
        "createdAt": "2026-05-20T12:00:00Z",
        "updatedAt": "2026-05-20T12:00:00Z",
        "itemVersion": item_version,
    }


def _summary_wire(workflow_id: str = "uw_one", *, like_count: int = 3) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "specId": f"user_anon.{workflow_id}",
        "name": "Public Doc Analyzer",
        "description": "...",
        "visibility": "public",
        "tags": ["docs"],
        "stats": {"runCount": 10, "forkCount": 2, "likeCount": like_count},
    }


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def client() -> AsyncConvilyn:
    return AsyncConvilyn(
        api_key="ck_test_workflows",  # pragma: allowlist secret
        base_url=API_BASE,
    )


# ── 1. Logic — happy path per verb ──────────────────────────────────


class TestSearchLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_search_default_returns_page(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/workflows/community").mock(
            return_value=httpx.Response(
                200,
                json={"items": [_summary_wire("uw_a"), _summary_wire("uw_b")], "cursor": None},
            )
        )
        page = await client.workflows.search()
        assert route.called
        assert isinstance(page, WorkflowSearchPage)
        assert [s.workflow_id for s in page.items] == ["uw_a", "uw_b"]
        assert page.cursor is None
        # Default sort param threaded through
        assert route.calls[0].request.url.params["sort"] == "recent"

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_threads_filters(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/workflows/community").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": "next_page"}),
        )
        page = await client.workflows.search(
            tag="docs", sort="popular", limit=5, cursor="prev_page"
        )
        assert page.cursor == "next_page"
        params = route.calls[0].request.url.params
        assert params["tag"] == "docs"
        assert params["sort"] == "popular"
        assert params["limit"] == "5"
        assert params["cursor"] == "prev_page"


class TestGetLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_returns_workflow(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/uw_abc123").mock(
            return_value=httpx.Response(200, json=_workflow_wire())
        )
        wf = await client.workflows.get("uw_abc123")
        assert isinstance(wf, Workflow)
        assert wf.workflow_id == "uw_abc123"
        assert wf.visibility == "private"


class TestForkLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_fork_sends_camel_case_body(self, client: AsyncConvilyn) -> None:
        import json

        route = respx.post(f"{API_BASE}/api/v1/workflows/fork").mock(
            return_value=httpx.Response(201, json=_workflow_wire(name="Copy of Doc Analyzer"))
        )
        forked = await client.workflows.fork(
            source_spec_id="goal_lane.content_to_multipost", name="Copy of Doc Analyzer"
        )
        assert isinstance(forked, Workflow)
        body = json.loads(route.calls[0].request.content)
        assert body["sourceSpecId"] == "goal_lane.content_to_multipost"
        assert body["name"] == "Copy of Doc Analyzer"


class TestPublishLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_publish_sends_patch_with_visibility(self, client: AsyncConvilyn) -> None:
        import json

        route = respx.patch(f"{API_BASE}/api/v1/workflows/uw_abc123").mock(
            return_value=httpx.Response(
                200, json=_workflow_wire(visibility="public", item_version=2)
            )
        )
        wf = await client.workflows.publish("uw_abc123", item_version=1)
        assert wf.visibility == "public"
        body = json.loads(route.calls[0].request.content)
        assert body["visibility"] == "public"
        assert body["itemVersion"] == 1


class TestLikeLogic:
    @pytest.mark.asyncio
    @respx.mock
    async def test_like_returns_toggle_state(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/uw_pub/like").mock(
            return_value=httpx.Response(200, json={"liked": True, "likeCount": 42})
        )
        result = await client.workflows.like("uw_pub")
        assert isinstance(result, LikeResponse)
        assert result.liked is True
        assert result.like_count == 42


# ── 2. Boundary — empty pages, sort enum, optional name ─────────────


class TestBoundary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_search_empty_listing(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/community").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": None})
        )
        page = await client.workflows.search()
        assert page.items == []
        assert page.cursor is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_fork_without_name_omits_field(self, client: AsyncConvilyn) -> None:
        import json

        route = respx.post(f"{API_BASE}/api/v1/workflows/fork").mock(
            return_value=httpx.Response(201, json=_workflow_wire())
        )
        await client.workflows.fork(source_spec_id="goal_lane.content_to_multipost")
        body = json.loads(route.calls[0].request.content)
        assert "name" not in body
        assert body["sourceSpecId"] == "goal_lane.content_to_multipost"

    @pytest.mark.asyncio
    @respx.mock
    async def test_patch_only_sends_supplied_fields(self, client: AsyncConvilyn) -> None:
        """PATCH builder must NOT send empty-string defaults for unspecified fields."""
        import json

        route = respx.patch(f"{API_BASE}/api/v1/workflows/uw_abc123").mock(
            return_value=httpx.Response(200, json=_workflow_wire(item_version=2))
        )
        await client.workflows.patch("uw_abc123", item_version=1, tags=["new-tag"])
        body = json.loads(route.calls[0].request.content)
        assert body == {"itemVersion": 1, "tags": ["new-tag"]}


# ── 3. Error — typed exceptions surface backend status codes ────────


class TestErrors:
    @pytest.mark.asyncio
    @respx.mock
    async def test_fork_pro_tier_required_surfaces_api_error(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/fork").mock(
            return_value=httpx.Response(
                402,
                json={
                    "code": "PRO_TIER_REQUIRED",
                    "message": "Upgrade to Pro to fork workflows",
                },
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.workflows.fork(source_spec_id="goal_lane.content_to_multipost")
        assert exc_info.value.status_code == 402
        # The code, not just the status. A `PRO_TIER_REQUIRED` on a 402 is what
        # routes this to `PlanRequiredError` rather than a bare `APIError`, and
        # asserting only the status cannot tell those apart.
        assert exc_info.value.code == "PRO_TIER_REQUIRED"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_not_found_surfaces_api_error_404(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/uw_missing").mock(
            return_value=httpx.Response(
                404,
                json={"code": "WORKFLOW_NOT_FOUND", "message": "Workflow not found"},
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.workflows.get("uw_missing")
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "WORKFLOW_NOT_FOUND"

    @pytest.mark.asyncio
    @respx.mock
    async def test_like_on_private_workflow_surfaces_409(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/workflows/uw_priv/like").mock(
            return_value=httpx.Response(
                409,
                json={
                    "code": "WORKFLOW_NOT_PUBLIC",
                    "message": "Only public workflows can be liked",
                },
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.workflows.like("uw_priv")
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "WORKFLOW_NOT_PUBLIC"

    @pytest.mark.asyncio
    @respx.mock
    async def test_publish_version_mismatch_surfaces_409(self, client: AsyncConvilyn) -> None:
        respx.patch(f"{API_BASE}/api/v1/workflows/uw_abc123").mock(
            return_value=httpx.Response(
                409,
                json={
                    "code": "ITEM_VERSION_MISMATCH",
                    "message": "Workflow was modified by a concurrent request",
                },
            )
        )
        with pytest.raises(APIError) as exc_info:
            await client.workflows.publish("uw_abc123", item_version=1)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ITEM_VERSION_MISMATCH"


# ── 4. Object-state — model parsing, stats wire shape, frozen ───────


class TestObjectState:
    def test_workflow_model_round_trip(self) -> None:
        from pydantic import ValidationError

        wf = Workflow.model_validate(_workflow_wire())
        assert wf.workflow_id == "uw_abc123"
        assert wf.stats.like_count == 0
        # Frozen — Pydantic raises ValidationError on mutation attempts.
        with pytest.raises(ValidationError):
            wf.name = "Tampered"  # type: ignore[misc]

    def test_workflow_summary_round_trip(self) -> None:
        summary = WorkflowSearchPage.model_validate(
            {"items": [_summary_wire(like_count=99)], "cursor": "abc"}
        )
        assert summary.items[0].stats.like_count == 99
        assert summary.cursor == "abc"

    def test_extra_field_tolerated_on_workflow(self) -> None:
        """OCP: backend can add a new field to the wire shape without an SDK bump."""
        data = _workflow_wire()
        data["futureField"] = {"foo": "bar"}
        wf = Workflow.model_validate(data)
        # extra="allow" keeps the field for round-trip
        dumped = wf.model_dump(mode="json", by_alias=True)
        assert dumped["futureField"] == {"foo": "bar"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_default_sort_is_recent(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/workflows/community").mock(
            return_value=httpx.Response(200, json={"items": [], "cursor": None})
        )
        await client.workflows.search()
        assert route.calls[0].request.url.params["sort"] == "recent"


# ── Built-in workflow catalog ────────────────────────────────────────


def _catalog_item_wire(workflow_id: str = "goal_lane.expense_batch_summary") -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "name": "Expense Batch → Vendor-Grouped Summary",
        "description": "Receipts and invoices into one spreadsheet.",
        "icon": "receipt",
        "supportedInputTypes": ["image", "document"],
        "supportedInputFormats": ["jpg", "png", "pdf"],
        "category": "goal_lane",
        "requiredSlotCount": 0,
        "subcategory": None,
        "skuGroup": None,
        "status": "active",
        "supportedLocales": ["en", "ja", "ko", "zh"],
        "maxInputSizeBytes": 52428800,
        "minFileCount": None,
        "tier": "business",
        "freeTierAllowed": None,
    }


class TestCatalog:
    @pytest.mark.asyncio
    @respx.mock
    async def test_catalog_returns_typed_items(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/catalog").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workflows": [
                        _catalog_item_wire(),
                        _catalog_item_wire("goal_lane.reading_to_highlights"),
                    ],
                    "total": 2,
                },
            )
        )
        items = await client.workflows.catalog()

        assert len(items) == 2
        assert items[0].workflow_id == "goal_lane.expense_batch_summary"
        assert items[0].tier == "business"
        assert items[0].free_tier_allowed is None
        assert items[1].workflow_id == "goal_lane.reading_to_highlights"

    @pytest.mark.asyncio
    @respx.mock
    async def test_catalog_empty_returns_empty_list(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/catalog").mock(
            return_value=httpx.Response(200, json={"workflows": [], "total": 0})
        )
        assert await client.workflows.catalog() == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_catalog_error_surfaces_as_api_error(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/workflows/catalog").mock(
            return_value=httpx.Response(500, json={"code": "INTERNAL", "message": "boom"})
        )
        with pytest.raises(APIError) as info:
            await client.workflows.catalog()
        assert info.value.status_code == 500

    def test_catalog_item_tolerates_future_fields(self) -> None:
        from convilyn import CatalogWorkflow

        data = _catalog_item_wire()
        data["futureField"] = True
        item = CatalogWorkflow.model_validate(data)
        assert item.workflow_id == "goal_lane.expense_batch_summary"

    def test_sync_wrapper_present(self) -> None:
        from convilyn import Convilyn

        sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
        try:
            assert callable(sync_client.workflows.catalog)
        finally:
            sync_client.close()

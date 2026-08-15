"""Workflows resource — community marketplace + private workflow management.

Surfaces the five primary verbs for community-workflow management:

* :py:meth:`AsyncWorkflows.search` — browse the public ``/community`` listing
* :py:meth:`AsyncWorkflows.get`    — fetch a single workflow row
* :py:meth:`AsyncWorkflows.fork`   — fork a curated / user source into a private workflow
* :py:meth:`AsyncWorkflows.publish` — flip visibility to ``public`` (PATCH)
* :py:meth:`AsyncWorkflows.like`   — toggle the like on a public workflow

Some endpoints require Pro-tier auth (``fork`` / ``publish``) — the
SDK surfaces the platform's 402/403 via :class:`APIError` so callers
can branch on the status code.

Naming + structure follows :mod:`convilyn.resources.files` and
:mod:`convilyn.resources.goals` for cross-resource consistency.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.types import CatalogWorkflow, LikeResponse, Workflow, WorkflowSearchPage

WorkflowSortMode = Literal["recent", "popular"]


class AsyncWorkflows:
    """Asynchronous community-workflows resource.

    Attached to :class:`convilyn.AsyncConvilyn` as ``client.workflows``.
    The resource owns request shaping and response parsing for the five
    community-workflow verbs; it does not know how to compile or
    execute workflows — those live on sibling resources (`goals`, the
    author SDK).
    """

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    # ── Public API ───────────────────────────────────────────────

    async def catalog(self) -> list[CatalogWorkflow]:
        """List the platform's built-in AI-workflow catalog.

        Distinct from :py:meth:`search`, which browses the user-published
        ``/community`` marketplace — the catalog is the curated set of
        built-in workflows runnable directly via
        ``client.goals.start(workflow_id=...)``. Anonymous callers are
        allowed; deprecated entries are filtered out server-side.
        """
        response = await self._http.request("GET", "/api/v1/workflows/catalog")
        payload = response.json()
        return [CatalogWorkflow.model_validate(item) for item in payload.get("workflows") or []]

    async def search(
        self,
        *,
        tag: str | None = None,
        sort: WorkflowSortMode = "recent",
        limit: int = 20,
        cursor: str | None = None,
    ) -> WorkflowSearchPage:
        """Browse the public ``/community`` workflow listing.

        Unauthenticated callers are allowed by the backend, but the SDK
        still attaches the configured auth header so a single client
        instance can mix anonymous and authenticated calls.

        Args:
            tag: Optional tag filter (case-sensitive, exact match).
            sort: ``"recent"`` (default, newest first) or ``"popular"``
                (descending by like count).
            limit: Page size, 1-50. Backend caps higher values.
            cursor: Opaque cursor from a previous response; pass to
                retrieve the next page.

        Returns:
            A :class:`WorkflowSearchPage` containing one page of summaries
            plus a ``cursor`` for the next call (``None`` when the
            listing is exhausted).
        """
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if tag is not None:
            params["tag"] = tag
        if cursor is not None:
            params["cursor"] = cursor
        response = await self._http.request("GET", "/api/v1/workflows/community", params=params)
        return WorkflowSearchPage.model_validate(response.json())

    async def get(self, workflow_id: str) -> Workflow:
        """Fetch a single workflow by its ``workflow_id``.

        The caller sees their own workflows at any visibility; for
        other users only ``public`` workflows are returned. Both
        ``private`` access and a missing row surface as
        :class:`APIError` with status 404 — the backend collapses both
        to prevent ID-existence probing.
        """
        response = await self._http.request("GET", f"/api/v1/workflows/{workflow_id}")
        return Workflow.model_validate(response.json())

    async def fork(
        self,
        *,
        source_spec_id: str,
        name: str | None = None,
    ) -> Workflow:
        """Fork a curated or user source into a new private workflow.

        Args:
            source_spec_id: The spec id to fork from. For curated
                workflows this is the registry id (e.g. ``doc_analyzer``);
                for user-owned public workflows this is the
                ``user_<prefix>.<workflow_id>`` compiled id available on
                :attr:`Workflow.spec_id`.
            name: Optional friendly name for the new workflow. Defaults
                to ``"Copy of <source name>"`` server-side.

        Raises:
            APIError: 402 if the caller's plan does not include Pro
                tier; 404 if the source does not exist (or is private
                / archived — the backend collapses these to prevent
                probing); 409 if the caller attempts to fork their own
                workflow.
        """
        body: dict[str, Any] = {"sourceSpecId": source_spec_id}
        if name is not None:
            body["name"] = name
        response = await self._http.request("POST", "/api/v1/workflows/fork", json=body)
        return Workflow.model_validate(response.json())

    async def publish(
        self,
        workflow_id: str,
        *,
        item_version: int,
    ) -> Workflow:
        """Flip an owned workflow's visibility to ``public``.

        Requires Pro tier on the caller. Optimistic-lock via
        ``item_version`` — pass the value from a fresh ``get()`` to
        avoid silently overwriting concurrent edits. A mismatch surfaces
        as :class:`APIError` 409.

        Public → archived → public transitions are also supported via
        :meth:`patch`; only the public → private transition is rejected
        by the backend (would silently break forkers' links).
        """
        return await self.patch(workflow_id, item_version=item_version, visibility="public")

    async def patch(
        self,
        workflow_id: str,
        *,
        item_version: int,
        name: str | None = None,
        description: str | None = None,
        visibility: Literal["public", "private", "archived"] | None = None,
        tags: list[str] | None = None,
    ) -> Workflow:
        """Partial update of an owned workflow.

        Generic PATCH escape hatch for the fields the SDK doesn't have
        a dedicated verb for. Only owner-supplied; backend enforces
        ownership via the auth header.
        """
        body: dict[str, Any] = {"itemVersion": item_version}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if visibility is not None:
            body["visibility"] = visibility
        if tags is not None:
            body["tags"] = list(tags)
        response = await self._http.request("PATCH", f"/api/v1/workflows/{workflow_id}", json=body)
        return Workflow.model_validate(response.json())

    async def like(self, workflow_id: str) -> LikeResponse:
        """Toggle the caller's like on a public workflow.

        Idempotent semantics — calling :meth:`like` on a workflow you
        already liked unlikes it; the returned :attr:`LikeResponse.liked`
        flag reflects the post-call state.

        Raises:
            APIError: 404 if the workflow does not exist; 409 if the
                workflow is not public (private workflows are not
                likeable).
        """
        response = await self._http.request("POST", f"/api/v1/workflows/{workflow_id}/like")
        return LikeResponse.model_validate(response.json())


class Workflows:
    """Synchronous facade around :class:`AsyncWorkflows`.

    Mirrors the async surface 1:1; each call runs the underlying
    coroutine via the injected runner (the root sync client's shared
    private loop). Same restriction as
    :class:`convilyn.resources.goals.Goals` — switch to
    :class:`AsyncConvilyn` for high-throughput callers.
    """

    def __init__(self, async_workflows: AsyncWorkflows, run: CoroRunner | None = None) -> None:
        self._async = async_workflows
        self._run: CoroRunner = run if run is not None else asyncio.run

    def catalog(self) -> list[CatalogWorkflow]:
        return self._run(self._async.catalog())

    def search(self, **kwargs: Any) -> WorkflowSearchPage:
        return self._run(self._async.search(**kwargs))

    def get(self, workflow_id: str) -> Workflow:
        return self._run(self._async.get(workflow_id))

    def fork(self, **kwargs: Any) -> Workflow:
        return self._run(self._async.fork(**kwargs))

    def publish(self, workflow_id: str, **kwargs: Any) -> Workflow:
        return self._run(self._async.publish(workflow_id, **kwargs))

    def patch(self, workflow_id: str, **kwargs: Any) -> Workflow:
        return self._run(self._async.patch(workflow_id, **kwargs))

    def like(self, workflow_id: str) -> LikeResponse:
        return self._run(self._async.like(workflow_id))

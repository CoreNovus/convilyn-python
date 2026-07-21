"""User-workflows resource — manage the workflows you author.

The curated management subset of ``/api/v1/user_workflows/*``: list /
get / delete / export / runs for workflows the caller OWNS — typically
authored through the chat Builder (:mod:`convilyn.resources.builder`)
and run via
:py:meth:`convilyn.resources.goals.AsyncGoals.run` (``user_workflow_id=``).

Distinct from :mod:`convilyn.resources.workflows` (``client.workflows``),
which is the community-gallery projection (catalog / search / fork /
like of OTHER users' published workflows). This namespace is "my
authored ``uw_`` workflows"; that one is "the marketplace".

Typical loop::

    page = await client.user_workflows.list()
    wf = await client.user_workflows.get(page.items[0].workflow_id)
    job = await client.goals.run(user_workflow_id=wf.workflow_id, files=[...])
    runs = await client.user_workflows.runs(wf.workflow_id)
    backup = await client.user_workflows.export(wf.workflow_id)
    await client.user_workflows.delete(wf.workflow_id)

Editing / validating / importing / publishing stay in the web Builder —
the SDK is a thin data-plane client, not a second builder UI. Structure
mirrors :mod:`convilyn.resources.workflows`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.types import (
    UserWorkflowDetail,
    UserWorkflowExport,
    UserWorkflowRun,
    UserWorkflowsPage,
    WorkflowVisibility,
)

_BASE = "/api/v1/user_workflows"


class AsyncUserWorkflows:
    """Asynchronous owned-workflow management resource (``client.user_workflows``)."""

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    async def list(
        self,
        *,
        visibility: WorkflowVisibility | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> UserWorkflowsPage:
        """List the workflows you own (most recent first, cursor-paged).

        Args:
            visibility: filter to ``private`` / ``public`` / ``archived``.
                Omitted = all of your workflows.
            limit: page size (server default 20, max 100).
            cursor: opaque continuation token from a previous page's
                :py:attr:`convilyn.types.UserWorkflowsPage.cursor`.
        """
        params: dict[str, Any] = {}
        if visibility is not None:
            params["visibility"] = visibility
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        response = await self._http.request("GET", _BASE, params=params or None)
        return UserWorkflowsPage.model_validate(response.json())

    async def get(self, workflow_id: str) -> UserWorkflowDetail:
        """Fetch the full owner-facing detail of a workflow you own.

        Raises:
            NotFoundError: unknown id, or a workflow you neither own nor
                may view (existence is deliberately not probeable).
        """
        response = await self._http.request("GET", f"{_BASE}/{workflow_id}")
        return UserWorkflowDetail.model_validate(response.json())

    async def delete(self, workflow_id: str) -> None:
        """Hard-delete a workflow you own.

        A public workflow must be archived first — deleting it raises
        :class:`convilyn.APIError` with status 409
        (``WORKFLOW_IS_PUBLIC_USE_ARCHIVE``).
        """
        await self._http.request("DELETE", f"{_BASE}/{workflow_id}")

    async def export(self, workflow_id: str) -> UserWorkflowExport:
        """Download a portable, sanitised export of a workflow you own.

        The document (full ``systemPrompt`` included — owner-only) is
        backend-owned; treat it as opaque JSON for backup / re-import via
        the web Builder. ``schema_version`` mirrors the
        ``X-Export-Schema-Version`` response header.
        """
        response = await self._http.request("GET", f"{_BASE}/{workflow_id}/export")
        return UserWorkflowExport(
            document=response.json(),
            schema_version=response.headers.get("X-Export-Schema-Version"),
        )

    async def runs(self, workflow_id: str, *, limit: int | None = None) -> list[UserWorkflowRun]:
        """List recent AI-workflow runs of a workflow you own (newest first).

        Args:
            limit: max rows (server default 5, max 50).
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        response = await self._http.request(
            "GET", f"{_BASE}/{workflow_id}/runs", params=params or None
        )
        items = response.json().get("items", [])
        return [UserWorkflowRun.model_validate(item) for item in items]


class UserWorkflows:
    """Synchronous facade over :class:`AsyncUserWorkflows` (``convilyn.Convilyn``)."""

    def __init__(
        self, async_user_workflows: AsyncUserWorkflows, run: CoroRunner | None = None
    ) -> None:
        self._async = async_user_workflows
        self._run: CoroRunner = run if run is not None else asyncio.run

    def list(self, **kwargs: Any) -> UserWorkflowsPage:
        return self._run(self._async.list(**kwargs))

    def get(self, workflow_id: str) -> UserWorkflowDetail:
        return self._run(self._async.get(workflow_id))

    def delete(self, workflow_id: str) -> None:
        return self._run(self._async.delete(workflow_id))

    def export(self, workflow_id: str) -> UserWorkflowExport:
        return self._run(self._async.export(workflow_id))

    def runs(self, workflow_id: str, **kwargs: Any) -> list[UserWorkflowRun]:
        return self._run(self._async.runs(workflow_id, **kwargs))

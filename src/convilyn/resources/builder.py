"""Builder resource — chat-driven workflow authoring.

The chat-driven Builder is a shared, client-agnostic capability: create a
Builder session, send message turns, and — when the builder emits a
``register`` verdict — receive the built ``uw_`` workflow id, which you can
hand straight to :py:meth:`convilyn.resources.goals.AsyncGoals.run`
(``workflow_id=...``) to run the new workflow from your own client.

Typical loop::

    session = await client.builder.create_session()
    turn = await client.builder.send_message(session.chat_id, "Build a workflow that …")
    while turn.verdict_action == "request_input":
        # answer turn.pending_slots, then:
        turn = await client.builder.send_message(session.chat_id, my_answer)
    if turn.verdict_action == "register":
        job = await client.goals.run(workflow_id=turn.registered_workflow_id, files=[...])

Builder authoring requires a Pro-tier account (the platform's 402
``TIER_REQUIRED`` is surfaced as :class:`convilyn.APIError`); the ``discover``
sub-mode is exempt. Structure mirrors :mod:`convilyn.resources.workflows`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from convilyn._internal.http import HTTPClient
from convilyn._internal.loop_runner import CoroRunner
from convilyn.types import (
    BuilderMessageList,
    BuilderQuota,
    BuilderSession,
    BuilderTurn,
)

BuilderMode = Literal["edit", "from_scratch", "discover"]


class AsyncBuilder:
    """Asynchronous chat-driven authoring resource (``client.builder``)."""

    def __init__(self, http: HTTPClient) -> None:
        self._http = http

    async def create_session(
        self,
        *,
        builder_mode: BuilderMode | None = None,
        target_workflow_id: str | None = None,
        title: str | None = None,
        initial_message: str | None = None,
        locale: str | None = None,
    ) -> BuilderSession:
        """Create a Builder authoring session (``mode="builder"``).

        Args:
            builder_mode: ``edit`` (default server-side) / ``from_scratch`` /
                ``discover``. ``discover`` is exempt from the Pro-tier gate.
            target_workflow_id: edit an existing ``uw_`` you own (owner-checked;
                400/403/404 otherwise). Only valid with the default builder mode.
            title: optional session title.
            initial_message: optional first message stored on the session.
            locale: BCP-47 locale driving the builder's draft language
                (zh-TW / zh-CN / en / ja / ko). Omitted = ``en``.

        Raises:
            APIError: 402 ``TIER_REQUIRED`` when the account is not Pro-tier
                (except ``discover``); 400/403/404 for a bad ``target_workflow_id``.
        """
        body: dict[str, Any] = {"mode": "builder"}
        if builder_mode is not None:
            body["builder_mode"] = builder_mode
        if target_workflow_id is not None:
            body["target_workflow_id"] = target_workflow_id
        if title is not None:
            body["title"] = title
        if initial_message is not None:
            body["initial_message"] = initial_message
        if locale is not None:
            body["locale"] = locale
        response = await self._http.request("POST", "/api/v1/chat/sessions", json=body)
        return BuilderSession.model_validate(response.json())

    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> BuilderTurn:
        """Drive one Builder turn and return its verdict.

        ``BuilderTurn.verdict_action`` reports the terminal action; on
        ``"register"`` the built workflow's ``uw_`` id is on
        ``BuilderTurn.registered_workflow_id``.
        """
        body: dict[str, Any] = {"content": content}
        if attachments is not None:
            body["attachments"] = attachments
        response = await self._http.request(
            "POST", f"/api/v1/chat/sessions/{chat_id}/messages", json=body
        )
        return BuilderTurn.model_validate(response.json())

    async def get_session(self, chat_id: str) -> BuilderSession:
        """Fetch a Builder session's current state."""
        response = await self._http.request("GET", f"/api/v1/chat/sessions/{chat_id}")
        return BuilderSession.model_validate(response.json())

    async def messages(
        self,
        chat_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> BuilderMessageList:
        """List a Builder session's message transcript (paginated)."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        response = await self._http.request(
            "GET", f"/api/v1/chat/sessions/{chat_id}/messages", params=params or None
        )
        return BuilderMessageList.model_validate(response.json())

    async def quota(self) -> BuilderQuota:
        """The caller's remaining Builder-turn budget for the current window."""
        response = await self._http.request("GET", "/api/v1/chat/builder/quota")
        return BuilderQuota.model_validate(response.json())


class Builder:
    """Synchronous facade over :class:`AsyncBuilder` (``convilyn.Convilyn``)."""

    def __init__(self, async_builder: AsyncBuilder, run: CoroRunner | None = None) -> None:
        self._async = async_builder
        self._run: CoroRunner = run if run is not None else asyncio.run

    def create_session(self, **kwargs: Any) -> BuilderSession:
        return self._run(self._async.create_session(**kwargs))

    def send_message(self, chat_id: str, content: str, **kwargs: Any) -> BuilderTurn:
        return self._run(self._async.send_message(chat_id, content, **kwargs))

    def get_session(self, chat_id: str) -> BuilderSession:
        return self._run(self._async.get_session(chat_id))

    def messages(self, chat_id: str, **kwargs: Any) -> BuilderMessageList:
        return self._run(self._async.messages(chat_id, **kwargs))

    def quota(self) -> BuilderQuota:
        return self._run(self._async.quota())

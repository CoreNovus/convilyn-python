"""Builder resource — logic / boundary / error / object-state (#2668).

respx mocks the HTTP layer; assertions land on observable wire behaviour
(route + payload shape, parsed types, the register `uw_` id, typed errors).
Mirrors :mod:`tests.unit.resources.test_workflows`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from convilyn import (
    APIError,
    AsyncConvilyn,
    BuilderMessageList,
    BuilderQuota,
    BuilderSession,
    BuilderTurn,
)

API_BASE = "https://api.convilyn.corenovus.com"
_UW = "uw_0123456789abcdef01234567"


def _session_wire(**overrides: Any) -> dict[str, Any]:
    return {
        "chat_id": "chat_1",
        "user_id": "user_1",
        "status": "active",
        "mode": "builder",
        "title": None,
        "message_count": 0,
        "agent_state": "builder",
        "builder_mode": "from_scratch",
        "working_memory_summary": None,
        "created_at": "2026-01-01T00:00:00Z",
        "last_message_at": None,
        **overrides,
    }


def _message_wire(role: str = "assistant", content: str = "ok") -> dict[str, Any]:
    return {
        "message_id": "m_1",
        "chat_id": "chat_1",
        "role": role,
        "content": content,
        "message_type": "text",
        "metadata": None,
        "attachments": [],
        "created_at": "2026-01-01T00:00:00Z",
    }


def _turn_wire(**overrides: Any) -> dict[str, Any]:
    return {
        "messages": [_message_wire("user", "build me a thing"), _message_wire()],
        "agent_state": "builder",
        "stop_reason": "completed",
        "verdict_action": None,
        "failure_type": None,
        "pending_slots": [],
        "turn_count": 1,
        "builder_mode": "from_scratch",
        "registered_workflow_id": None,
        "clarification_question": None,
        "clarification_options": [],
        "error": None,
        # Extra wire fields the SDK model ignores:
        "composition_phase": "compose",
        "recommended_workflows": None,
        **overrides,
    }


@pytest.fixture
def client() -> AsyncConvilyn:
    return AsyncConvilyn(
        api_key="ck_test_builder",  # pragma: allowlist secret
        base_url=API_BASE,
    )


class TestCreateSession:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_builder_mode_and_returns_session(self, client: AsyncConvilyn) -> None:
        route = respx.post(f"{API_BASE}/api/v1/chat/sessions").mock(
            return_value=httpx.Response(201, json=_session_wire())
        )
        session = await client.builder.create_session(builder_mode="from_scratch")

        assert route.called
        assert isinstance(session, BuilderSession)
        assert session.chat_id == "chat_1"
        assert session.mode == "builder"
        body = json.loads(route.calls[0].request.content)
        assert body == {"mode": "builder", "builder_mode": "from_scratch"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_threads_edit_target_and_locale(self, client: AsyncConvilyn) -> None:
        route = respx.post(f"{API_BASE}/api/v1/chat/sessions").mock(
            return_value=httpx.Response(201, json=_session_wire(builder_mode="edit"))
        )
        await client.builder.create_session(target_workflow_id=_UW, locale="zh-TW")

        body = json.loads(route.calls[0].request.content)
        assert body == {"mode": "builder", "target_workflow_id": _UW, "locale": "zh-TW"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_surfaces_pro_tier_402(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/chat/sessions").mock(
            return_value=httpx.Response(
                402, json={"code": "TIER_REQUIRED", "message": "Pro required"}
            )
        )
        with pytest.raises(APIError) as exc:
            await client.builder.create_session()
        assert exc.value.status_code == 402


class TestSendMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_register_verdict_returns_workflow_id(self, client: AsyncConvilyn) -> None:
        route = respx.post(f"{API_BASE}/api/v1/chat/sessions/chat_1/messages").mock(
            return_value=httpx.Response(
                201, json=_turn_wire(verdict_action="register", registered_workflow_id=_UW)
            )
        )
        turn = await client.builder.send_message("chat_1", "build me a thing")

        assert route.called
        assert isinstance(turn, BuilderTurn)
        assert turn.verdict_action == "register"
        assert turn.registered_workflow_id == _UW
        assert json.loads(route.calls[0].request.content) == {"content": "build me a thing"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_input_surfaces_pending_slots(self, client: AsyncConvilyn) -> None:
        respx.post(f"{API_BASE}/api/v1/chat/sessions/chat_1/messages").mock(
            return_value=httpx.Response(
                201,
                json=_turn_wire(
                    verdict_action="request_input",
                    pending_slots=[
                        {
                            "slot_id": "s1",
                            "slot_type": "text",
                            "question": "Which format?",
                            "options": None,
                            "required": True,
                            "context": None,
                        }
                    ],
                ),
            )
        )
        turn = await client.builder.send_message("chat_1", "hi")
        assert turn.verdict_action == "request_input"
        assert turn.registered_workflow_id is None
        assert [s.slot_id for s in turn.pending_slots] == ["s1"]


class TestReadEndpoints:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_session(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/chat/sessions/chat_1").mock(
            return_value=httpx.Response(200, json=_session_wire(message_count=4))
        )
        session = await client.builder.get_session("chat_1")
        assert isinstance(session, BuilderSession)
        assert session.message_count == 4

    @pytest.mark.asyncio
    @respx.mock
    async def test_messages_threads_pagination(self, client: AsyncConvilyn) -> None:
        route = respx.get(f"{API_BASE}/api/v1/chat/sessions/chat_1/messages").mock(
            return_value=httpx.Response(
                200, json={"messages": [_message_wire()], "total": 1, "limit": 20, "offset": 0}
            )
        )
        result = await client.builder.messages("chat_1", limit=20, offset=0)
        assert isinstance(result, BuilderMessageList)
        assert result.total == 1
        params = route.calls[0].request.url.params
        assert params["limit"] == "20"
        assert params["offset"] == "0"

    @pytest.mark.asyncio
    @respx.mock
    async def test_quota(self, client: AsyncConvilyn) -> None:
        respx.get(f"{API_BASE}/api/v1/chat/builder/quota").mock(
            return_value=httpx.Response(
                200,
                json={
                    "used": 3,
                    "limit": 10,
                    "remaining": 7,
                    "window_seconds": 3600,
                    "retry_after_seconds": 0,
                },
            )
        )
        quota = await client.builder.quota()
        assert isinstance(quota, BuilderQuota)
        assert (quota.used, quota.remaining) == (3, 7)


class TestSyncFacade:
    def test_sync_builder_is_wired(self) -> None:
        from convilyn import Convilyn

        c = Convilyn(api_key="ck_x", base_url=API_BASE)  # pragma: allowlist secret
        assert hasattr(c.builder, "create_session")
        assert hasattr(c.builder, "send_message")

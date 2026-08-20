"""Builder (chat-driven authoring) response models.

Split out of :mod:`convilyn.types`, which crossed its 800-line budget. The
repo's file-size ratchet says "extract something instead of raising the number",
and this block is the seam that costs least: the Builder wire is snake_case
while every other family in that module is camelCase-with-aliases, so these
models shared no convention with their former neighbours.

**Nothing about the public surface changed.** ``convilyn.types`` re-exports every
name below, so ``from convilyn.types import BuilderSession``,
``from convilyn import BuilderSession`` and ``from convilyn.types import *`` all
resolve exactly as before. This module is an implementation detail of that
package — import from ``convilyn`` or ``convilyn.types``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The chat/Builder wire is snake_case (the backend ChatResponse /
# ProcessMessageResponse models carry no camelCase alias_generator, unlike
# the goals/convert families), so these models need NO field aliases —
# attribute names already match the wire.

BuilderVerdictAction = Literal[
    "request_input",
    "stage_draft",
    "register",
    "infeasible",
    "recommend_existing",
    "missing_tool_call",
    "unknown_tool",
]
"""Terminal action of a Builder turn (``BuilderTurn.verdict_action``)."""


class BuilderAttachment(BaseModel):
    """A file attached to a Builder message."""

    model_config = ConfigDict(frozen=True)

    file_id: str
    file_name: str
    file_size: int = Field(ge=0)
    mime_type: str
    url: str | None = None


class BuilderSession(BaseModel):
    """A chat session in Builder authoring mode.

    Returned by :py:meth:`convilyn.resources.builder.AsyncBuilder.create_session`
    / ``get_session``. Extra wire fields are ignored.
    """

    model_config = ConfigDict(frozen=True)

    chat_id: str
    status: str
    mode: str = "builder"
    message_count: int = Field(ge=0)
    agent_state: str
    created_at: datetime
    user_id: str | None = None
    title: str | None = None
    builder_mode: str | None = None
    working_memory_summary: dict[str, Any] | None = None
    last_message_at: datetime | None = None


class BuilderMessage(BaseModel):
    """One persisted Builder chat message (user echo or assistant reply)."""

    model_config = ConfigDict(frozen=True)

    message_id: str
    chat_id: str
    role: str
    content: str
    message_type: str
    created_at: datetime
    metadata: dict[str, Any] | None = None
    attachments: list[BuilderAttachment] = Field(default_factory=list)


class BuilderPendingSlot(BaseModel):
    """A clarification slot the Builder is waiting on.

    Populated only when ``BuilderTurn.verdict_action == "request_input"``.
    """

    model_config = ConfigDict(frozen=True)

    slot_id: str
    slot_type: str
    question: str
    options: list[Any] | None = None
    required: bool = True
    context: str | None = None


class BuilderTurn(BaseModel):
    """The result of one Builder turn (``send_message``).

    ``verdict_action`` reports the terminal action; on ``"register"``,
    :py:attr:`registered_workflow_id` carries the built ``uw_`` id — hand it to
    :py:meth:`convilyn.resources.goals.AsyncGoals.run` (``workflow_id=...``) to
    run the new workflow. On ``"request_input"``, :py:attr:`pending_slots`
    carries the clarify form. Extra wire fields are ignored.
    """

    model_config = ConfigDict(frozen=True)

    messages: list[BuilderMessage]
    agent_state: str
    stop_reason: str
    verdict_action: str | None = None
    failure_type: str | None = None
    pending_slots: list[BuilderPendingSlot] = Field(default_factory=list)
    turn_count: int | None = None
    builder_mode: str | None = None
    registered_workflow_id: str | None = None
    clarification_question: str | None = None
    clarification_options: list[str] = Field(default_factory=list)
    error: str | None = None


class BuilderMessageList(BaseModel):
    """A page of a Builder session's message transcript."""

    model_config = ConfigDict(frozen=True)

    messages: list[BuilderMessage]
    total: int = Field(ge=0)
    limit: int = Field(ge=0)
    offset: int = Field(ge=0)


class BuilderQuota(BaseModel):
    """The caller's Builder-turn rate-limit snapshot for the current window."""

    model_config = ConfigDict(frozen=True)

    used: int = Field(ge=0)
    limit: int = Field(ge=0)
    remaining: int = Field(ge=0)
    window_seconds: int = Field(ge=0)
    retry_after_seconds: int = Field(default=0, ge=0)

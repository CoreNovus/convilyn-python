"""Public response models exposed by the Convilyn SDK.

These are the canonical Python representations of API responses. Field
names follow Python conventions (``filename``, ``size``, ``content_type``)
even when the wire protocol uses camelCase — the Pydantic ``alias``
config bridges the two so callers never have to think about it.

We intentionally do NOT re-export the API's ``FileResponse`` directly
because the API wire field names (``fileName``, ``mimeType``) are an
implementation detail of the HTTP transport; the SDK's job is to give
Python developers idiomatic types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

JobStatus = Literal["queued", "processing", "completed", "failed"]


class File(BaseModel):
    """A file known to the Convilyn platform.

    Returned by :py:meth:`convilyn.resources.files.AsyncFiles.upload` and,
    in future commits, by ``client.files.get(...)`` / ``client.files.list()``.

    The :py:attr:`file_id` is the only handle other resources need —
    ``client.convert.start(file_id=file.file_id, ...)`` accepts it
    directly. SDK callers should not treat any other field as a stable
    identifier.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    file_id: str = Field(alias="fileId")
    filename: str = Field(alias="fileName")
    size: int = Field(alias="fileSize", gt=0)
    content_type: str = Field(alias="mimeType")
    created_at: datetime = Field(alias="createdAt")
    job_id: str | None = Field(default=None, alias="jobId")
    is_input: bool = Field(default=True, alias="isInput")


class StoredFile(BaseModel):
    """One durable stored file (e.g. an emailed-in attachment).

    Returned inside :class:`FileList` by
    :py:meth:`convilyn.resources.files.AsyncFiles.list`. Durable files
    survive the ~1-hour cleanup that removes ordinary uploads and count
    toward your storage quota. Attribute names mirror :class:`File` for
    consistency; the list wire is snake_case, bridged by ``alias``.

    Ephemeral uploads do NOT appear here — this lists durable storage only.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    file_id: str
    filename: str = Field(alias="file_name")
    size: int = Field(alias="file_size", ge=0)
    content_type: str = Field(alias="mime_type")
    file_extension: str
    created_at: datetime


class StorageUsage(BaseModel):
    """Durable-storage usage against your tier's free quota (bytes)."""

    model_config = ConfigDict(frozen=True)

    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    over_quota: bool


class FileList(BaseModel):
    """Your durable stored files plus a storage-usage summary.

    Returned by :py:meth:`convilyn.resources.files.AsyncFiles.list`.
    """

    model_config = ConfigDict(frozen=True)

    files: list[StoredFile]
    usage: StorageUsage


class ResultFile(BaseModel):
    """A produced artifact attached to a completed job.

    The ``url`` is a presigned storage URL valid for one hour (issued by the
    service at job completion); callers should download promptly rather
    than persist the URL.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    filename: str
    # ``ge=0`` (not ``gt=0``): a failed job can carry a 0-byte placeholder
    # result file, and the SDK must still parse the job so JobFailedError can
    # surface — a stricter bound made pydantic raise ValidationError instead.
    size: int = Field(ge=0)
    mimetype: str  # wire is already lowercase; no alias needed
    url: str


class JobError(BaseModel):
    """Failure detail attached to a ``failed`` job."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str
    message: str


class ConvertJob(BaseModel):
    """A file conversion job — the lifecycle handle returned by
    :py:meth:`convilyn.resources.convert.AsyncConvert.create`.

    OpenAI / Stripe convention: this model carries *data* only. All
    actions (poll / wait / download) live on the resource class so the
    same model can be safely serialised, logged, and round-tripped.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    job_id: str = Field(alias="jobId")
    status: JobStatus
    processor_type: str = Field(alias="processorType")
    progress: int = Field(ge=0, le=100)
    progress_message: str | None = Field(default=None, alias="progressMessage")
    result_files: list[ResultFile] | None = Field(default=None, alias="resultFiles")
    error: JobError | None = None
    retry_count: int = Field(default=0, alias="retryCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    estimated_duration: int | None = Field(default=None, alias="estimatedDuration")

    @property
    def is_terminal(self) -> bool:
        """True when ``status`` is ``completed`` or ``failed``."""
        return self.status in ("completed", "failed")


# ── AI workflow (agentic workflow) types ──────────────────────────────


GoalJobStatus = Literal[
    # Non-terminal — job is still progressing through resolution / setup.
    "draft",
    "created",
    "analyzing",
    "slots_pending",  # waiting on user input via HITL — see PendingSlot / pending_interrupts
    "ready",
    "ready_with_preview",
    "confirmed",
    "queued",
    "executing",
    # Terminal — wait() stops polling here.
    "completed",  # all tasks succeeded
    "partial",  # some tasks succeeded, some failed (terminal but not "failed")
    "failed",  # job failed; details in `error`
    "cancelled",  # user-initiated cancel
]


# AI workflow terminal status set — once a job reports one of these, the
# SDK's ``wait()`` loop stops polling. Kept as a module-level tuple so
# tests can import the canonical set instead of hard-coding the strings.
GOAL_JOB_TERMINAL_STATUSES: tuple[GoalJobStatus, ...] = (
    "completed",
    "partial",
    "failed",
    "cancelled",
)


class PendingSlot(BaseModel):
    """A piece of information the agent is asking the user to supply.

    Surfaced inside :py:attr:`GoalJob.pending_slots` whenever the job's
    status is ``slots_pending``. R2 commit 1 exposes this as data only;
    a future commit adds ``client.goals.fill_slot(...)`` to answer back.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    slot_id: str = Field(alias="slotId")
    slot_type: str = Field(alias="slotType")
    question: str
    options: list[str | dict[str, str]] | None = None
    required: bool = True
    is_disambiguation: bool = Field(default=False, alias="isDisambiguation")
    suggested_value: object | None = Field(default=None, alias="suggestedValue")
    suggested_confidence: float | None = Field(default=None, alias="suggestedConfidence")


class GoalJob(BaseModel):
    """An AI workflow (agentic) job — handle returned by
    :py:meth:`convilyn.resources.goals.AsyncGoals.start`.

    Mirrors :class:`ConvertJob` in the OpenAI / Stripe "data on model,
    behaviour on resource" convention. ``status`` covers the AI workflow
    full lifecycle (13 literals); ``pending_slots`` and
    ``pending_interrupts`` surface HITL requests so callers can decide
    whether to keep polling or to answer back.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    job_spec_id: str = Field(alias="jobSpecId")
    status: GoalJobStatus
    progress: int = Field(default=0, ge=0, le=100)
    item_version: int | None = Field(default=None, alias="itemVersion")
    attempt_id: str | None = Field(default=None, alias="attemptId")
    goal_text: str | None = Field(default=None, alias="goalText")
    file_ids: list[str] = Field(default_factory=list, alias="fileIds")
    pending_slots: list[PendingSlot] = Field(default_factory=list, alias="pendingSlots")
    filled_slots: dict[str, object] = Field(default_factory=dict, alias="filledSlots")
    # ``pending_interrupts`` carries a polymorphic union (slot_fill /
    # batch_review / human_escalation / approval_review). For R2 commit 1
    # we expose it as opaque dicts; a future commit can model each
    # variant once the use cases stabilise.
    pending_interrupts: list[dict[str, object]] = Field(
        default_factory=list, alias="pendingInterrupts"
    )
    agent_message: str | None = Field(default=None, alias="agentMessage")
    error_message: str | None = Field(default=None, alias="errorMessage")
    error_code: str | None = Field(default=None, alias="errorCode")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")

    @field_validator("file_ids", "pending_slots", "pending_interrupts", mode="before")
    @classmethod
    def _null_list_to_empty(cls, value: object) -> object:
        """Coerce a wire ``null`` to an empty list. The API sends ``null``
        for empty collections (e.g. ``pendingInterrupts: null``), which would
        otherwise fail validation against the non-optional ``list`` type."""
        return [] if value is None else value

    @field_validator("filled_slots", mode="before")
    @classmethod
    def _null_dict_to_empty(cls, value: object) -> object:
        """Coerce a wire ``null`` to an empty dict (same rationale as above)."""
        return {} if value is None else value

    @property
    def is_terminal(self) -> bool:
        """True when the job has reached a stable end state.

        Includes ``partial`` (some tasks failed but the workflow as a
        whole reached the finish line) — callers that want strict
        "everything succeeded" should additionally check
        ``status == "completed"``.
        """
        return self.status in GOAL_JOB_TERMINAL_STATUSES

    @property
    def needs_input(self) -> bool:
        """True when the agent is blocked waiting on the user.

        Treat as a non-terminal stopping condition for polling — the
        SDK's ``wait()`` returns here so the caller can answer the
        slots (via the upcoming ``fill_slot`` API) instead of spinning.
        """
        return self.status == "slots_pending"


class Artifact(BaseModel):
    """One output artifact produced by an AI workflow job.

    Returned by :py:meth:`convilyn.resources.goals.AsyncGoals.artifacts`.
    The :py:attr:`download_url` is a presigned storage URL valid for one
    hour from issuance — re-call ``artifacts()`` (or
    ``download_artifact_url()``) for a fresh one instead of persisting it.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    artifact_id: str = Field(alias="artifactId")
    file_name: str = Field(alias="fileName")
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    download_url: str | None = Field(default=None, alias="downloadUrl")
    artifact_type: str | None = Field(default=None, alias="artifactType")
    platform: str | None = None
    metadata: dict[str, Any] | None = None
    is_primary: bool = Field(default=False, alias="isPrimary")
    description: str = ""


class ArtifactDownload(BaseModel):
    """A freshly minted presigned download URL for a single artifact.

    Returned by
    :py:meth:`convilyn.resources.goals.AsyncGoals.download_artifact_url`.
    ``expires_at`` marks the end of the URL's 1-hour validity window.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    download_url: str = Field(alias="downloadUrl")
    file_name: str = Field(alias="fileName")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    mime_type: str = Field(alias="mimeType")
    expires_at: datetime = Field(alias="expiresAt")


# ── AI workflow WebSocket event stream types ──────────────────────────


GoalEventType = Literal[
    # Tool lifecycle
    "tool_started",
    "tool_finished",
    # Agent-step lifecycle
    "agent_step_started",
    "agent_step_finished",
    # Orchestration transitions between agent steps
    "orchestration_transition",
    # Run control / heartbeats
    "status",
    "progress",
    "completed",
    "failed",
    # Human-in-the-loop
    "slot_needed",
    # Reserved by the API but not currently emitted; included so an
    # SDK release does not need to be re-cut the moment the server
    # starts emitting one.
    "keepalive",
    # Agent text streaming
    "agent_text",
    "agent_text_done",
    # ``cancelled`` is a terminal type the SDK self-closes on but the
    # server may emit only as ``failed`` with code=cancelled today; we
    # keep it in the terminal set below so callers and tests can rely on
    # a single source of truth.
]


# Event ``type`` values that cause :meth:`AsyncGoals.events` to close the
# WebSocket and stop iterating. ``cancelled`` is included for
# forward-compat with a future API version that emits it directly; today it
# arrives as a ``failed`` event with ``data.code == "CANCELLED"``.
GOAL_EVENT_TERMINAL_TYPES: tuple[str, ...] = (
    "completed",
    "failed",
    "cancelled",
)


# ── Community workflows (marketplace) ───────────────────────────────


WorkflowVisibility = Literal["private", "public", "archived"]


class WorkflowStats(BaseModel):
    """Aggregate counters surfaced on every workflow row."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    run_count: int = Field(default=0, alias="runCount", ge=0)
    fork_count: int = Field(default=0, alias="forkCount", ge=0)
    like_count: int = Field(default=0, alias="likeCount", ge=0)


class Workflow(BaseModel):
    """A community / private workflow row.

    Returned by :py:meth:`AsyncWorkflows.get` / :py:meth:`AsyncWorkflows.fork` /
    :py:meth:`AsyncWorkflows.publish`. Fields mirror the API's workflow
    shape — anything the API may add later is tolerated via
    ``extra="allow"`` so an older SDK release does not break when the
    wire grows.

    The :py:attr:`workflow_id` is the primary handle for follow-up calls;
    :py:attr:`spec_id` is the compiled spec identifier (used by
    :meth:`fork` as ``source_spec_id`` when forking from another user's
    public workflow).
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    workflow_id: str = Field(alias="workflowId")
    owner_id: str | None = Field(default=None, alias="ownerId")
    spec_id: str = Field(alias="specId")
    source_spec_id: str | None = Field(default=None, alias="sourceSpecId")
    source_type: str | None = Field(default=None, alias="sourceType")
    name: str
    description: str | None = None
    visibility: WorkflowVisibility
    tags: list[str] = Field(default_factory=list)
    stats: WorkflowStats = Field(default_factory=WorkflowStats)
    item_version: int = Field(default=0, alias="itemVersion", ge=0)


class WorkflowSummary(BaseModel):
    """Light-weight community-list row (no ``system_prompt`` / ``spec_json``).

    Returned by :py:meth:`AsyncWorkflows.search`. The community endpoint
    intentionally strips the heavy fields so the listing payload stays
    small.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    workflow_id: str = Field(alias="workflowId")
    spec_id: str = Field(alias="specId")
    owner_id: str | None = Field(default=None, alias="ownerId")
    name: str
    description: str | None = None
    visibility: WorkflowVisibility
    tags: list[str] = Field(default_factory=list)
    stats: WorkflowStats = Field(default_factory=WorkflowStats)


class CatalogWorkflow(BaseModel):
    """One built-in AI workflow from the platform catalog.

    Returned by :py:meth:`convilyn.resources.workflows.AsyncWorkflows.catalog`.
    Pass :py:attr:`workflow_id` as ``workflow_id`` to
    :py:meth:`convilyn.resources.goals.AsyncGoals.start` to run it directly
    (bypassing goal-text destination resolution).

    ``tier`` names the subscription tier the workflow is designed for
    (``None`` = free); ``free_tier_allowed`` is a tri-state gate hint —
    only an explicit ``False`` means a Free-tier run is blocked.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    workflow_id: str = Field(alias="workflowId")
    name: str
    description: str
    icon: str | None = None
    supported_input_types: list[str] = Field(default_factory=list, alias="supportedInputTypes")
    supported_input_formats: list[str] | None = Field(default=None, alias="supportedInputFormats")
    category: str = "goal_lane"
    required_slot_count: int = Field(default=0, alias="requiredSlotCount", ge=0)
    subcategory: str | None = None
    sku_group: str | None = Field(default=None, alias="skuGroup")
    status: str = "active"
    supported_locales: list[str] | None = Field(default=None, alias="supportedLocales")
    max_input_size_bytes: int | None = Field(default=None, alias="maxInputSizeBytes")
    min_file_count: int | None = Field(default=None, alias="minFileCount")
    tier: str | None = None
    free_tier_allowed: bool | None = Field(default=None, alias="freeTierAllowed")


class WorkflowSearchPage(BaseModel):
    """One page of community workflow summaries + cursor for pagination."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    items: list[WorkflowSummary] = Field(default_factory=list)
    cursor: str | None = None


class LikeResponse(BaseModel):
    """Result of :py:meth:`AsyncWorkflows.like` — toggle state + fresh count."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    liked: bool
    like_count: int = Field(alias="likeCount", ge=0)


# ── User-workflow management types ──────────────────────────────────
# The wire shapes of the curated `/user_workflows/*` management subset
# (contract: sdk_public_openapi.yaml UserWorkflowSummary / Detail / Run).
# Distinct from the community-gallery `Workflow` / `WorkflowSummary`
# above: these are the OWNER-facing rows for workflows you author.


class UserWorkflowSummary(BaseModel):
    """One row of :py:meth:`AsyncUserWorkflows.list` — a workflow you own.

    Light-weight (no ``system_prompt`` / ``spec_json``); fetch the full
    detail with :py:meth:`AsyncUserWorkflows.get`.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    workflow_id: str = Field(alias="workflowId")
    spec_id: str = Field(alias="specId")
    name: str
    description_preview: str | None = Field(default=None, alias="descriptionPreview")
    visibility: WorkflowVisibility
    tags: list[str] = Field(default_factory=list)
    stats: WorkflowStats = Field(default_factory=WorkflowStats)
    source_type: str | None = Field(default=None, alias="sourceType")
    source_spec_id: str | None = Field(default=None, alias="sourceSpecId")
    updated_at: str = Field(alias="updatedAt")
    owner_id: str | None = Field(default=None, alias="ownerId")
    owner_display_name: str | None = Field(default=None, alias="ownerDisplayName")


class UserWorkflowsPage(BaseModel):
    """One page of owned-workflow summaries + cursor for pagination."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    items: list[UserWorkflowSummary] = Field(default_factory=list)
    cursor: str | None = None


class UserWorkflowDetail(BaseModel):
    """Full owner-facing detail of a workflow you own.

    Returned by :py:meth:`AsyncUserWorkflows.get`. Carries the heavy
    authoring fields (``system_prompt``, ``spec_json``) the summary row
    omits; builder-UI-only wire fields (tool palette, canvas layout,
    example pairs) are tolerated via ``extra="allow"`` but deliberately
    not bound — they belong to the web Builder surface.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    workflow_id: str = Field(alias="workflowId")
    owner_id: str = Field(alias="ownerId")
    owner_display_name: str | None = Field(default=None, alias="ownerDisplayName")
    spec_id: str = Field(alias="specId")
    source_spec_id: str | None = Field(default=None, alias="sourceSpecId")
    source_type: str | None = Field(default=None, alias="sourceType")
    source_version: str | None = Field(default=None, alias="sourceVersion")
    name: str
    description: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    spec_json: dict[str, Any] | None = Field(default=None, alias="specJson")
    visibility: WorkflowVisibility
    tags: list[str] = Field(default_factory=list)
    founding_intent: str | None = Field(default=None, alias="foundingIntent")
    stats: WorkflowStats = Field(default_factory=WorkflowStats)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    item_version: int = Field(default=0, alias="itemVersion", ge=0)


class UserWorkflowRun(BaseModel):
    """One recent AI-workflow run of an owned workflow.

    Returned by :py:meth:`AsyncUserWorkflows.runs`. ``job_spec_id`` is
    the handle for :py:meth:`convilyn.resources.goals.AsyncGoals.retrieve`
    / ``artifacts`` follow-ups.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    job_spec_id: str = Field(alias="jobSpecId")
    status: str
    started_at: str | None = Field(default=None, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    progress: float | None = None
    error_code: str | None = Field(default=None, alias="errorCode")


class UserWorkflowExport(BaseModel):
    """A portable workflow export document + its schema version.

    Returned by :py:meth:`AsyncUserWorkflows.export`. ``document`` is the
    backend-owned sanitised JSON document (treat as opaque — for backup /
    re-import); ``schema_version`` mirrors the ``X-Export-Schema-Version``
    response header.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    document: dict[str, Any]
    schema_version: str | None = None


# ── Billing / quota types ───────────────────────────────────────────


QuotaState = Literal["ok", "soft_limit", "quota_exceeded"]
# Mirrors the backend plan tiers (plan_catalog.py). MUST include every tier the
# backend can return, or a business-tier caller's quota/plan response fails
# validation. Keep in lockstep with the backend enabled_plan_tiers.
PlanTier = Literal["free", "pro", "business"]


class QuotaCheck(BaseModel):
    """Caller-tier vs cost-estimate verdict.

    Mirrors the API's quota-check response.
    Returned inside :class:`CostEstimate.quota_check`. ``state="ok"``
    means the estimate fits within tier thresholds; ``"soft_limit"``
    means a pro caller is over the soft cap (proceed with caution);
    ``"quota_exceeded"`` is a hard stop on free tier — the equivalent
    API call would raise :class:`convilyn.QuotaExceededError`.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    state: QuotaState
    tier: PlanTier
    estimated_micro_u: int = Field(alias="estimatedMicroU", ge=0)
    threshold_micro_u: int = Field(alias="thresholdMicroU", ge=0)
    upgrade_url: str | None = Field(default=None, alias="upgradeUrl")


class ToolCostEstimate(BaseModel):
    """Per-tool cost breakdown row inside :class:`CostEstimate`."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    tool_name: str = Field(alias="toolName")
    per_invocation_micro_u: int = Field(alias="perInvocationMicroU", ge=0)


class CostEstimate(BaseModel):
    """Result of :meth:`convilyn.resources.account.AsyncAccount.get_quota`.

    Wraps the ``POST /api/v1/workflows/cost-preview`` response. Provides
    a cost-range projection (min / total / max) plus the quota verdict
    in one round-trip so callers can render confirm-modal CTAs without
    a second call.

    ``estimated_micro_u`` is the legacy single-number summary kept for
    back-compat; new callers should prefer the explicit
    ``estimated_min_micro_u`` / ``estimated_total_micro_u`` /
    ``estimated_max_micro_u`` triple.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    estimated_micro_u: int = Field(alias="estimatedMicroU", ge=0)
    estimated_usd: float = Field(alias="estimatedUsd", ge=0)
    estimated_total_micro_u: int = Field(alias="estimatedTotalMicroU", ge=0)
    estimated_min_micro_u: int = Field(alias="estimatedMinMicroU", ge=0)
    estimated_max_micro_u: int = Field(alias="estimatedMaxMicroU", ge=0)
    tools: list[ToolCostEstimate] = Field(default_factory=list)
    quota_check: QuotaCheck = Field(alias="quotaCheck")


class Plan(BaseModel):
    """Caller's current billing tier.

    Derived from ``POST /api/v1/workflows/cost-preview`` — the ``ck_``-accepting
    endpoint that returns the tier in ``quotaCheck.tier`` (see
    :meth:`convilyn.resources.account.AsyncAccount.get_plan`). The tier is one of
    ``free`` / ``pro`` / ``business``. :class:`Plan` is ``extra="allow"``, so any
    future fields the backend adds surface without breaking existing callers.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    tier: PlanTier


class UsageHistoryEntry(BaseModel):
    """One past usage period for a specific metric.

    Mirrors the row shape returned by
    ``GET /api/v1/payment/usage/history`` — one entry per
    ``(metric, period)`` cell. Free-tier callers get monthly periods;
    paid callers get periods aligned to their subscription cycle.

    ``limit`` may be ``None`` for unlimited metrics on paid tiers.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    metric: str
    period_start: datetime
    period_end: datetime
    used: int = Field(ge=0)
    limit: int | None = None


# ── AI workflow event stream (existing) ───────────────────────────────


class GoalEvent(BaseModel):
    """One server-sent event in an AI workflow execution stream.

    Yielded by :meth:`convilyn.resources.goals.AsyncGoals.events`. The
    wire envelope is defined by the event-stream contract;
    new envelope fields are tolerated (``extra="allow"``) so an older
    SDK release does not break when the server starts emitting
    additional metadata.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    type: GoalEventType
    schema_version: int = Field(alias="schemaVersion")
    job_spec_id: str = Field(alias="jobSpecId")
    emitted_at: datetime = Field(alias="emittedAt")
    seq: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """True when this event signals the end of the stream."""
        return self.type in GOAL_EVENT_TERMINAL_TYPES


# ── Builder (chat-driven authoring) ──────────────────────────────────
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

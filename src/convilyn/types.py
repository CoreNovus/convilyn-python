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

# The Builder (chat-driven authoring) models live in `_types_builder` and are
# re-exported here, because adding `warnings` took this module past its
# 800-line budget and the ratchet's answer to that is to extract, not to
# record a new ceiling. Public surface is unchanged: `convilyn.types.X`,
# `convilyn.X` and `import *` all still resolve. That block was the cheap
# seam because the Builder wire is snake_case while every other family here
# is camelCase-with-aliases, so it shared no convention with its neighbours.
#
# A grouped import with one `noqa` rather than an `__all__`: this module has
# never declared one, and adding a partial list would silently shrink what
# `import *` exports.
from convilyn._types_builder import (  # noqa: F401  (re-export; see above)
    BuilderAttachment,
    BuilderMessage,
    BuilderMessageList,
    BuilderPendingSlot,
    BuilderQuota,
    BuilderSession,
    BuilderTurn,
    BuilderVerdictAction,
)

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


class JobErrorDetail(BaseModel):
    """Why a job was refused, as operands rather than a sentence.

    ``JobError.message`` is one canned sentence per ``code``, so this is what
    lets you phrase your own. A workbook refused for CSV reports
    ``reason="MULTI_SHEET_WORKBOOK"`` with the sheet count and the targets that
    keep every sheet. Branch on the reasons you handle — an unknown one only
    means the server knows a case this build does not.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    reason: str
    sheet_count: int | None = Field(default=None, alias="sheetCount")
    faithful_targets: list[str] | None = Field(default=None, alias="faithfulTargets")


class JobError(BaseModel):
    """Failure detail attached to a ``failed`` job."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str
    message: str
    #: Absent on almost every failure: an unsupported extension is fully
    #: described by ``code``.
    detail: JobErrorDetail | None = None


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
    #: What the conversion could not preserve. Empty on a faithful conversion,
    #: and worth reading on a successful one — a `.xls` workbook converted to
    #: CSV succeeds and keeps only its first sheet, and this is where it says
    #: so. Each entry is prefixed by kind (`best_effort:`, `truncated:`,
    #: `bundled:`, `layout_degraded:`, …); split on the first `:` to group them,
    #: but treat an unprefixed entry as a plain note rather than an error.
    warnings: list[str] = Field(default_factory=list)
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


class GoalErrorDetail(BaseModel):
    """Which ceiling an AI-workflow run hit, and how far it got.

    ``GoalJob.error_message`` is one canned sentence per ``error_code``, and
    ``PROCESSING_LIMIT`` alone covers four unrelated ceilings — an iteration
    cap, an input-token budget, a repeated tool call, a scratchpad read loop.
    Without this you cannot tell them apart, nor whether changing the input
    would help.

    ``limit`` and ``reached`` are in the unit ``reason`` implies. Either can be
    ``None``: a run resumed from a checkpoint written before the counter existed
    has no count, and the server sends null rather than a fabricated zero.

    Branch on the reasons you handle and treat an unknown one as absent — the
    server may know a ceiling this build does not.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    reason: str
    limit: int | None = None
    reached: int | None = None


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
    #: Absent on most failures: the code says everything there is to say.
    error_detail: GoalErrorDetail | None = Field(default=None, alias="errorDetail")
    #: The next step this failure implies — ``"retry"`` | ``"upgrade"`` |
    #: ``"login"`` | ``"contact_support"`` | ``"none"``. ``None`` on a job that
    #: has not failed, which is a different fact from a failure with no action.
    suggested_action: str | None = Field(default=None, alias="suggestedAction")
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
    #: Icon hint the server derives from :py:attr:`mime_type` — one of
    #: ``video | image | audio | document | csv | json | zip | text``, or
    #: ``None`` when the artifact carries no MIME. Read
    #: :py:attr:`mime_type` for the real format; this is a display aid.
    artifact_type: str | None = Field(default=None, alias="artifactType")
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

    .. warning::

       **This is not the price of a workflow, and it is not comparable to a
       credit balance.** Three separate reasons, each sufficient on its own:

       1. ``cost-preview`` estimates a **tool palette** for the chat Builder —
          ``(sum of per-tool costs + a flat per-iteration LLM cost) ×
          max_iterations``. It takes no workflow id and knows nothing about
          which workflow you intend to run.
       2. Every field here is **µU (micro-USD)**, while a balance is in
          **credits**. 1 credit = 10,000 µU = $0.01 of insured processing cost
          — the unit definition, which is stable and safe to rely on.
       3. These µU are **insured (pre-margin) cost**, not what you are charged.
          Dividing by 10,000 therefore UNDERSTATES the charge, which is worse
          than having no number at all: it would tell a caller they can afford
          a run they cannot.

       **To ask "what will this workflow cost me, and can I afford it", call
       ``POST /credits/workflow-quote``.** It answers in credits, returns your
       balance in the same unit, and does the comparison server-side
       (``costCredits`` / ``balanceCredits`` / ``sufficient``). It accepts a
       ``ck_`` key. The SDK does not wrap it yet.
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


class CreditBalance(BaseModel):
    """What the caller has left to spend, in credits.

    Returned by :py:meth:`convilyn.resources.account.AsyncAccount.get_balance`,
    which wraps ``GET /api/v1/credits/balance``.

    Two buckets, because they behave differently at renewal:

    * :py:attr:`period_credits` — subscription / trial grants for the active
      billing period. **Zeroed at renewal.**
    * :py:attr:`topup_credits` — the persistent wallet. Never expires.

    :py:attr:`balance_credits` is their TOTAL and is the authoritative number —
    read this one rather than either bucket.

    **Do NOT compare it against :class:`CostEstimate`.** That sentence used to
    say "compare a quote against this one", and there is no quote in this SDK
    that it is comparable to: :meth:`get_quota` returns insured µU for a Builder
    tool palette, and this is charged credits. See :class:`CostEstimate` for the
    three reasons and for the endpoint that does answer the question.

    Both buckets are ``None`` when the server did not send them; read that as
    *unknown*, never as zero, exactly as
    :py:attr:`convilyn.InsufficientCreditsError.shortfall_credits` is read.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")

    balance_credits: int = Field(alias="balanceCredits")
    period_credits: int | None = Field(default=None, alias="periodCredits")
    topup_credits: int | None = Field(default=None, alias="topupCredits")
    period_end: datetime = Field(alias="periodEnd")
    last_grant_at: datetime | None = Field(default=None, alias="lastGrantAt")
    #: Free tier only — credits spent this period against the monthly Free cost
    #: cap. FRACTIONAL by design: it accumulates charges, and rounding
    #: it to whole credits would make the cap unauditable.
    free_tier_month_spent_credits: float | None = Field(
        default=None, alias="freeTierMonthSpentCredits"
    )


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

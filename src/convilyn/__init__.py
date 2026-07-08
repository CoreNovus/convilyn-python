"""Convilyn — official Python client SDK.

Quick start::

    from convilyn import Convilyn

    client = Convilyn(api_key="ck_...")           # or CONVILYN_API_KEY env

    # Convert a file and wait for the result.
    job = client.convert.create_and_wait(file="report.docx", target_format="pdf")
    client.convert.download_to(job, to="report.pdf")

    # Run an agentic AI workflow.
    result = client.goals.run(goal_text="Summarise this", files=[file_id])

Async usage::

    from convilyn import AsyncConvilyn

    async with AsyncConvilyn(api_key="ck_...") as client:  # pragma: allowlist secret
        job = await client.convert.create_and_wait(
            file="report.docx", target_format="pdf"
        )

The public surface — everything reachable as ``from convilyn import X`` —
follows Semantic Versioning. See ``docs/STABILITY.md`` for the stability
contract; ``convilyn._internal`` is implementation detail and exempt.
"""

from convilyn._version import __version__
from convilyn.client import AsyncConvilyn
from convilyn.config import (
    AutoThrottleConfig,
    ExponentialBackoffRetry,
    NoRetry,
    RetryPolicy,
)
from convilyn.exceptions import (
    APIError,
    AuthError,
    ConvilynError,
    GoalJobFailedError,
    GoalJobTimeoutError,
    JobFailedError,
    JobTimeoutError,
    PlanRequiredError,
    QuotaExceededError,
    RateLimitError,
    RetryExhaustedError,
    S3UploadError,
    WebSocketError,
)
from convilyn.sync_client import Convilyn
from convilyn.types import (
    ConvertJob,
    CostEstimate,
    File,
    GoalEvent,
    GoalJob,
    JobError,
    LikeResponse,
    PendingSlot,
    Plan,
    PlanTier,
    QuotaCheck,
    QuotaState,
    ResultFile,
    ToolCostEstimate,
    UsageHistoryEntry,
    Workflow,
    WorkflowSearchPage,
    WorkflowStats,
    WorkflowSummary,
)

__all__ = [
    "APIError",
    "AsyncConvilyn",
    "AuthError",
    "AutoThrottleConfig",
    "ConvertJob",
    "Convilyn",
    "ConvilynError",
    "CostEstimate",
    "ExponentialBackoffRetry",
    "File",
    "GoalEvent",
    "GoalJob",
    "GoalJobFailedError",
    "GoalJobTimeoutError",
    "JobError",
    "JobFailedError",
    "JobTimeoutError",
    "LikeResponse",
    "NoRetry",
    "PendingSlot",
    "Plan",
    "PlanRequiredError",
    "PlanTier",
    "QuotaCheck",
    "QuotaExceededError",
    "QuotaState",
    "RateLimitError",
    "ResultFile",
    "RetryExhaustedError",
    "RetryPolicy",
    "S3UploadError",
    "ToolCostEstimate",
    "UsageHistoryEntry",
    "WebSocketError",
    "Workflow",
    "WorkflowSearchPage",
    "WorkflowStats",
    "WorkflowSummary",
    "__version__",
]

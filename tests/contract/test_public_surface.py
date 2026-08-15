"""Public-API contract — the published surface must not drift silently.

This is the keystone guard for the SDK's stability promise (see
``docs/STABILITY.md``). It freezes the public surface so any change to it
is a *deliberate, reviewed* act rather than an accident:

* ``convilyn.__all__`` — the exact set of top-level exports.
* The resource method sets reached via the client.
* The exception taxonomy (every public error roots at ``ConvilynError``).
* The immutability of response models.

…and it asserts that nothing from ``convilyn._internal`` leaks into the
public ``convilyn`` namespace.

Unlike ``tests/integration/test_examples_syntax.py`` — which only checks
that *documented* imports still resolve — this test also fails when the
surface GROWS unexpectedly (a new implicit export) or when an internal
implementation symbol becomes reachable from the top level. To change the
public API on purpose, update the frozen sets below **and** add a
``CHANGELOG.md`` entry in the same commit.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import convilyn
from convilyn.resources import (
    Account,
    AsyncAccount,
    AsyncBuilder,
    AsyncConvert,
    AsyncFiles,
    AsyncGoals,
    AsyncUserWorkflows,
    AsyncWorkflows,
    Builder,
    Convert,
    Files,
    Goals,
    UserWorkflows,
    Workflows,
)

_SDK_ROOT = Path(__file__).resolve().parents[2]

# ── The frozen public export set ─────────────────────────────────────
# Changing this set IS the act of changing the public API. Pair any edit
# with a CHANGELOG.md entry and a SemVer bump (see docs/STABILITY.md).
FROZEN_ALL = {
    "APIError",
    "Artifact",
    "ArtifactDownload",
    "AsyncConvilyn",
    "AuthError",
    "AutoThrottleConfig",
    "BuilderAttachment",
    "BuilderMessage",
    "BuilderMessageList",
    "BuilderPendingSlot",
    "BuilderQuota",
    "BuilderSession",
    "BuilderTurn",
    "CatalogWorkflow",
    "ConvertJob",
    "Convilyn",
    "ConvilynError",
    "CostEstimate",
    "ExponentialBackoffRetry",
    "File",
    "FileList",
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
    "StorageUsage",
    "StoredFile",
    "ToolCostEstimate",
    "UnderstandUnavailableError",
    "UsageHistoryEntry",
    "UserWorkflowDetail",
    "UserWorkflowExport",
    "UserWorkflowRun",
    "UserWorkflowSummary",
    "UserWorkflowsPage",
    "WebSocketError",
    "Workflow",
    "WorkflowSearchPage",
    "WorkflowStats",
    "WorkflowSummary",
    "__version__",
}

# Implementation symbols that must NEVER be reachable as ``convilyn.X``.
# (The resilience *config* types — RetryPolicy / ExponentialBackoffRetry /
# NoRetry / AutoThrottleConfig — ARE public, re-exported via convilyn.config;
# the transport / auth / wiring below is not.)
INTERNAL_DENYLIST = (
    "HTTPClient",
    "WebsocketsTransport",
    "WSTransport",
    "APIKey",
    "AuthStrategy",
    "AutoThrottleArg",
    "resolve_auto_throttle",
    "resolve_auth",
    "generate_idempotency_key",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
)

EXPECTED_ERRORS = {
    "ConvilynError",
    "AuthError",
    "APIError",
    "RateLimitError",
    "PlanRequiredError",
    "QuotaExceededError",
    "S3UploadError",
    "JobFailedError",
    "JobTimeoutError",
    "GoalJobFailedError",
    "GoalJobTimeoutError",
    "WebSocketError",
    "RetryExhaustedError",
}

ASYNC_RESOURCE_METHODS = {
    AsyncFiles: {"upload", "delete", "list"},
    AsyncConvert: {
        "create",
        "retrieve",
        "wait",
        "create_and_wait",
        "download_url",
        "download_to",
    },
    AsyncGoals: {
        "start",
        "retrieve",
        "wait",
        "run",
        "run_interactive",
        "extract",
        "to_markdown",
        "understand",
        "fill_slot",
        "fill_slots",
        "confirm",
        "cancel",
        "retry",
        "events",
        "artifacts",
        "download_artifact_url",
        "download_artifact_to",
    },
    AsyncWorkflows: {"catalog", "search", "get", "fork", "publish", "patch", "like"},
    AsyncUserWorkflows: {"list", "get", "delete", "export", "grounded_contract", "runs"},
    AsyncBuilder: {"create_session", "send_message", "get_session", "messages", "quota"},
    AsyncAccount: {"get_quota", "get_plan", "usage_history"},
}

# The sync surface mirrors async exactly EXCEPT goals.events() — WebSocket
# streaming is async-only by design (a sync iterator would need a thread +
# queue bridge). This asymmetry is itself part of the frozen contract.
SYNC_RESOURCE_METHODS = {
    Files: {"upload", "delete", "list"},
    Convert: {
        "create",
        "retrieve",
        "wait",
        "create_and_wait",
        "download_url",
        "download_to",
    },
    Goals: {
        "start",
        "retrieve",
        "wait",
        "run",
        "run_interactive",
        "extract",
        "to_markdown",
        "understand",
        "fill_slot",
        "fill_slots",
        "confirm",
        "cancel",
        "retry",
        "artifacts",
        "download_artifact_url",
        "download_artifact_to",
    },
    Workflows: {"catalog", "search", "get", "fork", "publish", "patch", "like"},
    UserWorkflows: {"list", "get", "delete", "export", "grounded_contract", "runs"},
    Builder: {"create_session", "send_message", "get_session", "messages", "quota"},
    Account: {"get_quota", "get_plan", "usage_history"},
}


def _public_methods(cls: type) -> set[str]:
    """Public (non-underscore) callable members defined on ``cls``."""
    return {name for name, _member in inspect.getmembers(cls, callable) if not name.startswith("_")}


# ── __all__ is frozen ────────────────────────────────────────────────


def test_all_matches_frozen_set() -> None:
    """``convilyn.__all__`` must equal the frozen contract set exactly."""
    actual = set(convilyn.__all__)
    assert actual == FROZEN_ALL, (
        "public __all__ drifted — added "
        f"{sorted(actual - FROZEN_ALL)}, removed {sorted(FROZEN_ALL - actual)}. "
        "If intentional, update FROZEN_ALL here AND add a CHANGELOG entry."
    )


def test_every_export_is_importable() -> None:
    """Every name in ``__all__`` must actually resolve (no dangling export)."""
    missing = [name for name in convilyn.__all__ if not hasattr(convilyn, name)]
    assert not missing, f"declared in __all__ but missing: {missing}"


def test_no_implicit_public_exports() -> None:
    """No non-underscore, non-module attribute may exist outside ``__all__``.

    Catches an accidental ``from convilyn._internal.x import Y`` at the top
    of ``__init__`` that forgets to (or shouldn't) join ``__all__``.
    """
    public_attrs = {
        name
        for name, value in vars(convilyn).items()
        if not name.startswith("_") and not inspect.ismodule(value)
    }
    extra = public_attrs - set(convilyn.__all__)
    assert not extra, f"implicit public exports not in __all__: {sorted(extra)}"


# ── _internal stays internal ─────────────────────────────────────────


def test_internal_symbols_not_reachable_from_top_level() -> None:
    """Transport / auth / wiring internals must not be ``convilyn.X``."""
    leaked = [name for name in INTERNAL_DENYLIST if hasattr(convilyn, name)]
    assert not leaked, f"internal implementation symbols leaked publicly: {leaked}"


# ── Exception taxonomy ───────────────────────────────────────────────


def test_error_taxonomy_roots_at_convilyn_error() -> None:
    """Every public error subclasses ``ConvilynError`` (one catch-all)."""
    for name in EXPECTED_ERRORS:
        cls = getattr(convilyn, name)
        assert issubclass(cls, convilyn.ConvilynError), name


def test_api_error_exposes_wire_fields() -> None:
    """``APIError`` carries the ``{status_code, code, message, details}`` envelope."""
    err = convilyn.APIError(404, "NOT_FOUND", "missing")
    assert err.status_code == 404
    assert err.code == "NOT_FOUND"
    assert err.message == "missing"
    assert isinstance(err.details, dict)


# ── Resource surface ─────────────────────────────────────────────────


def test_client_exposes_all_resources() -> None:
    """A constructed client wires up the seven documented resource accessors."""
    client = convilyn.Convilyn(api_key="ck_contract")  # pragma: allowlist secret
    try:
        for attr in (
            "files",
            "convert",
            "goals",
            "workflows",
            "user_workflows",
            "builder",
            "account",
        ):
            assert hasattr(client, attr), attr
    finally:
        client.close()


def test_async_resource_methods_frozen() -> None:
    """Each async resource exposes exactly its documented method set."""
    for cls, expected in ASYNC_RESOURCE_METHODS.items():
        assert _public_methods(cls) == expected, cls.__name__


def test_sync_resource_methods_frozen() -> None:
    """Each sync resource mirrors async, minus the async-only ``events``."""
    for cls, expected in SYNC_RESOURCE_METHODS.items():
        assert _public_methods(cls) == expected, cls.__name__


# ── Response-model immutability ──────────────────────────────────────


def test_response_models_are_frozen() -> None:
    """Key response models are immutable so callers can't mutate server state."""
    for model in (
        convilyn.File,
        convilyn.ConvertJob,
        convilyn.GoalJob,
        convilyn.Workflow,
        convilyn.UserWorkflowDetail,
    ):
        assert model.model_config.get("frozen") is True, model.__name__


# ── Stability doc anchor ─────────────────────────────────────────────


def test_stability_doc_exists_and_names_the_contract() -> None:
    """``docs/STABILITY.md`` must exist and name the public-surface anchor."""
    doc = _SDK_ROOT / "docs" / "STABILITY.md"
    assert doc.exists(), f"missing stability policy doc at {doc}"
    text = doc.read_text(encoding="utf-8")
    assert "convilyn.__all__" in text
    assert "convilyn._internal" in text

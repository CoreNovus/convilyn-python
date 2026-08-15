"""Path A — a Builder-authored ``uw_*`` workflow as an edge ``ModelOperator``.

The chat-built workflow you author in the Builder is a **cloud** ``uw_*`` spec. The
edge SPI (``convilyn_edge``) runs a workflow as a composition of typed operators,
where the model step is a :class:`~convilyn_edge.ModelOperator` — "run typed,
schema-constrained inference at a chosen placement", the keystone that returns a
validated :class:`~convilyn_edge.ModelResult`, **never a bare string**.

This example bridges the two with **nothing but the already-published consumer
SDK**: a ``ModelOperator`` whose ``placement="cloud"`` implementation wraps
``client.goals.run(user_workflow_id="uw_…")``. That is the SPI's own contract —

    One Protocol, two placements.
      • "cloud" — wrap the consumer SDK's cloud workflow (``client.goals``); the
        server runs all deterministic safety logic and re-grounds every value.  ← THIS FILE
      • "edge"  — run inference on-device (a planned option); a follow-on path.

So Path A needs **zero platform change**: the workflow already runs; this adapter
just submits a job and reads the typed result.

What stays server-side:

* **No safety gate is delegated to this adapter.** Redaction, budget, retry, cycle
  detection, and tool permission all stay server-side — this adapter only *submits*
  a job and *reads* the result; it re-implements none of them.
* **Offline-first.** Any failure / timeout / non-terminal outcome maps to
  ``status="unavailable"`` (or ``"uncertain"``) — never raises past this
  adapter's boundary — so the caller can take a fixed fallback path rather than
  crash.
* **Generic operator, injected scenario mapping.** ``build_slots`` (typed input →
  the ``uw_*`` workflow's slot answers) is the only scenario-aware piece and is a
  constructor argument; the operator names no scenario itself.

A vertical Solution Pack wires this exact pattern into its own scenario
workflows; this file is the generic, scenario-free version consumer-SDK users
start from.

Usage::

    uv add --prerelease=allow convilyn convilyn-edge   # or: pip install --pre …
    export CONVILYN_API_KEY=ck_...            # a ck_ consumer key
    python examples/10_uw_as_edge_operator.py   # runs OFFLINE against a fake client
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from convilyn_edge import Evidence, ModelResult

from convilyn import APIError, GoalJob, GoalJobFailedError, GoalJobTimeoutError

#: Terminal statuses that carry a usable result (``partial`` = the workflow reached
#: its end though a sub-task failed; still worth reading its message).
_TERMINAL_OK = frozenset({"completed", "partial"})


class _GoalsClient(Protocol):
    """The single consumer-SDK capability this adapter needs.

    Structural, so a real ``convilyn.AsyncConvilyn`` and an offline fake are equally
    substitutable — only ``goals.run`` is required.
    """

    goals: Any  # exposes: async run(*, user_workflow_id, slots, timeout, idle_timeout)


def _default_build_slots(model_input: Mapping[str, Any]) -> dict[str, Any]:
    """Map the typed operator input onto the ``uw_*`` workflow's slot answers.

    A Builder ``uw_*`` has no *required* slots (the agent never pauses to ask —
    the compile-time UserWorkflow contract), so pre-seeding values just steers it.
    Only non-empty values are sent, so an absent field is omitted, not sent as
    ``None``.
    """
    return {key: value for key, value in model_input.items() if value not in (None, "")}


class UserWorkflowModelOperator:
    """A ``ModelOperator`` (``placement="cloud"``) backed by a Builder ``uw_*`` workflow.

    Wraps ``client.goals.run(user_workflow_id=…)`` and maps the cloud job outcome
    onto the four model statuses the edge SPI defines:

    * terminal + message  → ``success``   (output present; evidence cites the job)
    * terminal + no text  → ``uncertain`` (ran, but produced nothing usable)
    * failed / timed out / API error → ``unavailable`` (offline-first fallback)
    * (``invalid`` is reserved for a schema/re-grounding failure a future *edge*
      placement produces; the cloud server already validates before returning.)

    Substitutable for any other ``ModelOperator`` behind the SPI Protocol.
    """

    def __init__(
        self,
        client: _GoalsClient,
        user_workflow_id: str,
        *,
        build_slots: Callable[[Mapping[str, Any]], dict[str, Any]] = _default_build_slots,
        default_deadline_ms: int = 120_000,
        idle_timeout_s: float | None = 60.0,
    ) -> None:
        if not user_workflow_id.startswith("uw_"):
            raise ValueError(
                f"user_workflow_id must be a Builder-authored 'uw_…' id, got {user_workflow_id!r}"
            )
        self._client = client
        self._user_workflow_id = user_workflow_id
        self._build_slots = build_slots
        self._default_deadline_ms = default_deadline_ms
        self._idle_timeout_s = idle_timeout_s

    async def infer(
        self,
        model_input: Mapping[str, Any],
        *,
        schema: Mapping[str, Any],
        deadline_ms: int | None = None,
        placement: str = "cloud",
    ) -> ModelResult[str]:
        """Run the cloud workflow and return a typed result.

        Never raises for the model being unreachable — that is
        ``status="unavailable"`` (offline-first). ``schema`` is the caller's declared
        output contract; the cloud workflow enforces its own output shape
        server-side, so it is advisory here. ``deadline_ms`` bounds the poll.
        """
        timeout_s = (deadline_ms or self._default_deadline_ms) / 1000.0
        started = time.monotonic()
        try:
            job = await self._client.goals.run(
                user_workflow_id=self._user_workflow_id,
                slots=self._build_slots(model_input) or None,
                timeout=timeout_s,
                idle_timeout=self._idle_timeout_s,
            )
        except (GoalJobFailedError, GoalJobTimeoutError, APIError):
            # Unreachable / failed / timed out → offline-first fallback. We
            # deliberately swallow the error and return a fallback.
            return self._result("unavailable", time.monotonic() - started)

        elapsed_s = time.monotonic() - started
        if job.status not in _TERMINAL_OK:
            return self._result("uncertain", elapsed_s, model_version=job.attempt_id)

        message = (job.agent_message or "").strip()
        if not message:
            return self._result("uncertain", elapsed_s, model_version=job.attempt_id)

        return ModelResult(
            status="success",
            model_id=self._user_workflow_id,
            model_version=job.attempt_id or "cloud",
            latency_ms=elapsed_s * 1000.0,
            output=message,
            # The finished cloud job IS the grounding provenance: the server already
            # re-grounded every value it returned. Cite the job so an auditor can
            # pull its full trace — the device is never a second source of truth.
            evidence=(Evidence(source=f"convilyn://jobs/{job.job_spec_id}"),),
        )

    def _result(
        self, status: str, elapsed_s: float, *, model_version: str | None = None
    ) -> ModelResult[str]:
        return ModelResult(
            status=status,  # type: ignore[arg-type]  # a ModelStatus literal, narrowed above
            model_id=self._user_workflow_id,
            model_version=model_version or "cloud",
            latency_ms=elapsed_s * 1000.0,
        )


# ── Offline self-verifying demo — no network / cloud ─────────────────────────
# Injects a fake consumer client that returns real GoalJob objects (and raises real
# SDK exceptions), proving the wiring AND the offline-first fallback contract.


class _FakeGoals:
    def __init__(self, outcome: Callable[[dict[str, Any] | None], GoalJob]) -> None:
        self._outcome = outcome

    async def run(
        self, *, user_workflow_id: str, slots: dict[str, Any] | None, **_: Any
    ) -> GoalJob:
        return self._outcome(slots)


class _FakeClient:
    def __init__(self, goals: _FakeGoals) -> None:
        self.goals = goals


def _completed_job(message: str) -> GoalJob:
    now = "2026-07-20T00:00:00Z"
    return GoalJob.model_validate(
        {
            "jobSpecId": "job-demo-0001",
            "status": "completed",
            "progress": 100,
            "agentMessage": message,
            "createdAt": now,
            "updatedAt": now,
        }
    )


async def main() -> int:
    schema = {"type": "string", "description": "one short instruction"}

    # ── Case 1 — the cloud workflow returns text (success) ────────────────────
    cloud_up = UserWorkflowModelOperator(
        _FakeClient(_FakeGoals(lambda _slots: _completed_job("Reboot the scanner, then rescan."))),
        user_workflow_id="uw_acme.pos_error_explainer",
    )
    ok = await cloud_up.infer({"error_code": "E104"}, schema=schema)
    print(f"[cloud up]   status={ok.status!r} output={ok.output!r}")
    print(f"             evidence={ok.evidence[0].source!r}")
    assert ok.status == "success" and ok.evidence[0].source.startswith("convilyn://jobs/")

    # ── Case 2 — the cloud is unreachable (timeout) → offline-first fallback ──
    def _timeout(_slots: dict[str, Any] | None) -> GoalJob:
        raise GoalJobTimeoutError(job_spec_id="job-demo-0002", elapsed=120.0, timeout=120.0)

    cloud_down = UserWorkflowModelOperator(
        _FakeClient(_FakeGoals(_timeout)),
        user_workflow_id="uw_acme.pos_error_explainer",
    )
    down = await cloud_down.infer({"error_code": "E104"}, schema=schema)
    print(f"[cloud down] status={down.status!r} output={down.output!r}   [caller takes fallback]")
    assert down.status == "unavailable" and down.output is None

    print("\nOK: uw_* wrapped as a cloud ModelOperator; offline-first fallback holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

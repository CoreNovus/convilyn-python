"""`wait(stop_on=...)` — reaching the approval gate and stopping there.

Before this, every public path either hung or spent: `wait()` stopped only on a
terminal status or `slots_pending`, so a job parked at `ready` polled until the
timeout (observed live on dev — >10 minutes at `status=ready`, `updatedAt ==
createdAt`, nothing charged). `_wait_loop` could already stop there via the
private `extra_stop_statuses`, but the only public callers that passed it —
`run()` and `run_interactive()` — AUTO-CONFIRM, which is the opposite of what a
caller who wants to decide before spending needs.

New file rather than an addition to `test_goals.py`: that one is 1550 lines
against a recorded 1543 ceiling, i.e. 24 lines of headroom, and there is no
reason to spend it here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, Convilyn

API_BASE = "https://api.convilyn.com"
JOB = "gj_1"


def _job(status: str) -> dict:
    return {
        "jobSpecId": JOB,
        "status": status,
        "progress": 50,
        "fileIds": ["file_a"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _poll(mock: respx.Router, *statuses: str) -> respx.Route:
    """One response per call, in order — so a test can prove the loop STOPPED
    by leaving later responses unconsumed."""
    return mock.get(f"{API_BASE}/api/v1/jobs/goal/{JOB}").mock(
        side_effect=[httpx.Response(200, json=_job(s)) for s in statuses]
    )


# ── 1. Logic — it stops where it was told to ─────────────────────────


class TestItStopsOnTheRequestedStatus:
    @pytest.mark.asyncio
    async def test_a_ready_job_is_returned_rather_than_polled(self) -> None:
        async with respx.mock as mock:
            _poll(mock, "ready")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, stop_on={"ready"}, timeout=5)

        assert job.status == "ready"

    @pytest.mark.asyncio
    async def test_it_stops_at_the_gate_without_confirming(self) -> None:
        """The whole point: `run()` reaches `ready` and spends. This must not.

        A `confirm` route is registered and required NOT to be called — the
        distinction from every other public path, and the reason a status
        assertion alone would not be enough.
        """
        async with respx.mock as mock:
            _poll(mock, "analyzing", "ready")
            confirm = mock.post(f"{API_BASE}/api/v1/jobs/goal/{JOB}/confirm")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, stop_on={"ready"}, timeout=5, poll_interval=0.2)

        assert job.status == "ready"
        assert not confirm.called

    @pytest.mark.asyncio
    async def test_it_accepts_several_statuses(self) -> None:
        async with respx.mock as mock:
            _poll(mock, "ready_with_preview")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(
                    JOB, stop_on={"ready", "ready_with_preview"}, timeout=5
                )

        assert job.status == "ready_with_preview"

    @pytest.mark.asyncio
    async def test_a_frozenset_works_too(self) -> None:
        """The signature accepts either; `_wait_loop` wants a frozenset and the
        conversion happens at the boundary."""
        async with respx.mock as mock:
            _poll(mock, "ready")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, stop_on=frozenset({"ready"}), timeout=5)

        assert job.status == "ready"


# ── 2. Boundary — the default is unchanged ───────────────────────────


class TestTheDefaultStillDoesNotStopOnReady:
    """The discriminating half. Without this the tests above would pass against
    an implementation that stopped on `ready` unconditionally — which would be
    a silent behaviour change for every existing caller of `wait()`.
    """

    @pytest.mark.asyncio
    async def test_without_stop_on_a_ready_job_keeps_polling(self) -> None:
        async with respx.mock as mock:
            _poll(mock, "ready", "ready", "completed")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, timeout=5, poll_interval=0.2)

        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_an_empty_stop_on_is_the_default(self) -> None:
        async with respx.mock as mock:
            _poll(mock, "ready", "completed")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, stop_on=set(), timeout=5, poll_interval=0.2)

        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_a_terminal_status_still_wins(self) -> None:
        """`stop_on` ADDS stops; it does not replace the terminal one."""
        async with respx.mock as mock:
            _poll(mock, "completed")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = await client.goals.wait(JOB, stop_on={"ready"}, timeout=5)

        assert job.status == "completed"


# ── 3. Object state — the sync façade forwards it ────────────────────


class TestTheSyncFacadeForwardsIt:
    def test_sync_wait_reaches_the_gate_too(self) -> None:
        """`Goals.wait` forwards `**kwargs`, so this needed no change — which is
        exactly the claim worth pinning, because nothing else would notice if
        the façade grew an explicit signature that dropped `stop_on`.
        """
        with respx.mock as mock:
            _poll(mock, "ready")
            with Convilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                job = client.goals.wait(JOB, stop_on={"ready"}, timeout=5)

        assert job.status == "ready"

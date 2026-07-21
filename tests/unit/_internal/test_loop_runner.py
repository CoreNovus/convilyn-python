"""LoopRunner — the #2112 regression guard.

The sync surface used to give every call its own ``asyncio.run`` loop,
orphaning httpx pooled connections and crashing at interpreter teardown
on Windows. The contract under test: every coroutine a runner executes
— including the final close — runs on ONE private loop.
"""

from __future__ import annotations

import asyncio

import pytest

from convilyn._internal.loop_runner import LoopRunner


async def _current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


class TestLoopRunnerLogic:
    def test_run_returns_coroutine_result(self):
        runner = LoopRunner()

        result = runner.run(_current_loop())

        assert isinstance(result, asyncio.AbstractEventLoop)
        runner.close()

    def test_consecutive_runs_share_one_loop(self):
        """The #2112 fix itself: call N runs on the same loop object."""
        runner = LoopRunner()

        loops = [runner.run(_current_loop()) for _ in range(3)]

        assert loops[0] is loops[1] is loops[2]
        runner.close()

    def test_two_runners_do_not_share_a_loop(self):
        first, second = LoopRunner(), LoopRunner()

        loops = (first.run(_current_loop()), second.run(_current_loop()))

        assert loops[0] is not loops[1]
        first.close()
        second.close()


class TestLoopRunnerState:
    def test_new_runner_reports_not_closed(self):
        runner = LoopRunner()

        assert runner.closed is False

    def test_close_before_any_run_marks_closed(self):
        runner = LoopRunner()

        runner.close()

        assert runner.closed is True

    def test_double_close_stays_closed(self):
        runner = LoopRunner()
        runner.run(_current_loop())

        runner.close()
        runner.close()

        assert runner.closed is True

    def test_close_closes_the_private_loop(self):
        runner = LoopRunner()
        loop = runner.run(_current_loop())

        runner.close()

        assert loop.is_closed() is True


class TestLoopRunnerErrors:
    def test_run_after_close_raises_runtime_error(self):
        runner = LoopRunner()
        runner.close()

        with pytest.raises(RuntimeError, match="client is closed"):
            runner.run(_current_loop())

    @pytest.mark.asyncio
    async def test_run_inside_running_loop_raises_runtime_error(self):
        runner = LoopRunner()

        with pytest.raises(RuntimeError, match="use AsyncConvilyn"):
            runner.run(_current_loop())

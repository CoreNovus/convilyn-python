"""``graphs.plan_execute``: the shared contract, then what is particular to it."""

from __future__ import annotations

import collections
import re
import types
from typing import Any

import pytest

from convilyn import graphs
from tests.unit.graphs.graph_contract import (
    THREAD,
    GraphContract,
    host_around,
    pausing_function,
    pending,
    resume,
)

LIMITS = "at most 256 parts, 64 levels and 16384 bytes as JSON"
PLAN_RETURNS = "plan must return a list of steps, each as text"
REPLAN_RETURNS = re.escape('replan must return {"answer": text} or {"steps": [text, ...]}')


def two_steps(task: str) -> list[str]:
    return ["find", "write"]


def did(task: str, step: str, done: list[dict[str, str]]) -> str:
    return f"{step}: {task}"


def keep_plan(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
    """Keeps the plan while steps remain, then answers from every result."""
    if remaining:
        return {"steps": remaining}
    return {"answer": " / ".join(each["result"] for each in done)}


def built(**overrides: Any) -> Any:
    arguments = {"plan": two_steps, "execute": did, "replan": keep_plan, "max_steps": 4}
    return graphs.plan_execute(**{**arguments, **overrides})


def never_answers(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
    return {"steps": [f"step {len(done) + 1}"]}


class TestPlanExecuteContract(GraphContract):
    NAME = "plan_execute"

    @pytest.fixture
    def graph(self) -> Any:
        return built()

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"task": "refund"}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        return {
            "answer": "find: refund / write: refund",
            "done": [
                {"step": "find", "result": "find: refund"},
                {"step": "write", "result": "write: refund"},
            ],
            "answered": True,
        }


class TestWhatRuns:
    def test_plan_is_handed_the_task(self) -> None:
        seen: list[str] = []

        def plan(task: str) -> list[str]:
            seen.append(task)
            return ["find"]

        built(plan=plan).invoke({"task": "refund"})

        assert seen == ["refund"]

    def test_each_step_is_executed_in_order_with_what_was_done_before_it(self) -> None:
        calls: list[tuple[str, str, list[dict[str, str]]]] = []

        def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            calls.append((task, step, list(done)))
            return f"{step}!"

        built(execute=execute).invoke({"task": "t"})

        assert calls == [("t", "find", []), ("t", "write", [{"step": "find", "result": "find!"}])]

    def test_replan_runs_after_every_step_with_what_is_done_and_what_remains(self) -> None:
        calls: list[tuple[list[str], list[str]]] = []

        def replan(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
            calls.append(([each["step"] for each in done], list(remaining)))
            return keep_plan(task, done, remaining)

        built(replan=replan).invoke({"task": "t"})

        assert calls == [(["find"], ["write"]), (["find", "write"], [])]

    def test_the_steps_replan_returns_replace_the_rest_of_the_plan(self) -> None:
        def replan(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
            if len(done) == 1:
                return {"steps": ["check", "send"]}
            return keep_plan(task, done, remaining)

        result = built(replan=replan).invoke({"task": "t"})

        assert [each["step"] for each in result["done"]] == ["find", "check", "send"]

    def test_an_answer_from_replan_ends_the_run_before_the_remaining_steps(self) -> None:
        result = built(replan=lambda task, done, remaining: {"answer": "early"}).invoke(
            {"task": "t"}
        )

        assert result == {
            "answer": "early",
            "done": [{"step": "find", "result": "find: t"}],
            "answered": True,
        }

    def test_an_empty_plan_goes_straight_to_replan_with_nothing_done(self) -> None:
        calls: list[tuple[list[Any], list[str]]] = []

        def replan(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
            calls.append((list(done), list(remaining)))
            return {"answer": "nothing to do"}

        built(plan=lambda task: [], replan=replan).invoke({"task": "t"})

        assert calls == [([], [])]

    def test_an_empty_plan_answered_by_replan_runs_no_step(self) -> None:
        graph = built(plan=lambda task: [], replan=lambda task, done, remaining: {"answer": "no"})

        result = graph.invoke({"task": "t"})

        assert result == {"answer": "no", "done": [], "answered": True}

    def test_text_in_any_script_passes_through_unchanged(self) -> None:
        result = built().invoke({"task": "退款 返金 환불"})

        assert result["answer"] == "find: 退款 返金 환불 / write: 退款 返金 환불"


class TestTheStepLimit:
    def test_the_run_stops_unanswered_after_max_steps_steps(self) -> None:
        result = built(replan=never_answers, max_steps=3).invoke({"task": "t"})

        assert result == {
            "answer": None,
            "done": [
                {"step": "find", "result": "find: t"},
                {"step": "step 2", "result": "step 2: t"},
                {"step": "step 3", "result": "step 3: t"},
            ],
            "answered": False,
        }

    def test_replan_may_still_answer_after_the_last_step_allowed(self) -> None:
        result = built(max_steps=2).invoke({"task": "t"})

        assert result["answered"] is True

    def test_a_plan_longer_than_the_limit_runs_only_as_many_steps_as_allowed(self) -> None:
        graph = built(plan=lambda task: ["a", "b", "c"], max_steps=1)

        result = graph.invoke({"task": "t"})

        assert [each["step"] for each in result["done"]] == ["a"]

    @pytest.mark.parametrize("most", [1, 32])
    def test_the_smallest_and_largest_limits_run_exactly_that_many_steps(self, most: int) -> None:
        result = built(replan=never_answers, max_steps=most).invoke({"task": "t"})

        assert len(result["done"]) == most


class TestAStepThatPauses:
    def paused_host(self, ran: list[str], finished: list[str]) -> Any:
        ask = pausing_function({"approve": "write"}, ran)

        def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            if step == "write":
                return ask(step)
            finished.append(step)
            return f"{step} ok"

        graph = built(execute=execute)
        return host_around(graph, ("task", "answer", "done", "answered"), saved=True)

    def test_the_run_pauses_with_what_the_step_asked(self) -> None:
        host = self.paused_host([], [])

        host.invoke({"task": "t"}, THREAD)

        assert pending(host) == [{"approve": "write"}]

    def test_resumed_it_finishes_with_the_step_s_answer(self) -> None:
        host = self.paused_host([], [])
        host.invoke({"task": "t"}, THREAD)

        result = host.invoke(resume("yes"), THREAD)

        assert result["answer"] == "find ok / answer: yes"

    def test_a_step_that_finished_before_the_pause_does_not_run_again(self) -> None:
        finished: list[str] = []
        host = self.paused_host([], finished)
        host.invoke({"task": "t"}, THREAD)

        host.invoke(resume("yes"), THREAD)

        assert finished == ["find"]

    async def test_under_ainvoke_a_finished_step_does_not_run_again_either(self) -> None:
        finished: list[str] = []
        host = self.paused_host([], finished)
        await host.ainvoke({"task": "t"}, THREAD)

        await host.ainvoke(resume("yes"), THREAD)

        assert finished == ["find"]

    def test_the_paused_step_runs_again_from_its_start(self) -> None:
        ran: list[str] = []
        host = self.paused_host(ran, [])
        host.invoke({"task": "t"}, THREAD)

        host.invoke(resume("yes"), THREAD)

        assert ran == ["write", "write"]


class TestAsyncFunctions:
    async def test_async_functions_run_under_the_async_entry_point(self) -> None:
        async def plan(task: str) -> list[str]:
            return ["one"]

        async def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            return step.upper()

        async def replan(
            task: str, done: list[dict[str, str]], remaining: list[str]
        ) -> dict[str, Any]:
            return {"answer": done[-1]["result"]}

        graph = graphs.plan_execute(plan=plan, execute=execute, replan=replan, max_steps=2)

        result = await graph.ainvoke({"task": "t"})

        assert result == {
            "answer": "ONE",
            "done": [{"step": "one", "result": "ONE"}],
            "answered": True,
        }

    def test_an_async_function_under_the_sync_entry_point_says_which_to_use(self) -> None:
        async def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            return step

        with pytest.raises(TypeError, match="run it with ainvoke or astream"):
            built(execute=execute).invoke({"task": "t"})


class TestWhatItLeavesAlone:
    def test_execute_changing_the_done_list_it_was_handed_does_not_change_the_output(
        self,
    ) -> None:
        def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            for each in done:
                each["result"] = "changed"
            done.clear()
            return step

        result = built(execute=execute).invoke({"task": "t"})

        assert result["done"] == [
            {"step": "find", "result": "find"},
            {"step": "write", "result": "write"},
        ]

    def test_replan_changing_the_remaining_list_it_was_handed_does_not_change_the_plan(
        self,
    ) -> None:
        def replan(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict[str, Any]:
            kept = list(remaining)
            remaining.clear()
            return {"steps": kept} if kept else {"answer": "done"}

        result = built(replan=replan).invoke({"task": "t"})

        assert [each["step"] for each in result["done"]] == ["find", "write"]

    def test_a_plan_list_changed_after_plan_returned_it_does_not_change_the_run(self) -> None:
        handed: list[str] = ["find", "write"]

        def plan(task: str) -> list[str]:
            return handed

        def execute(task: str, step: str, done: list[dict[str, str]]) -> str:
            handed.append("extra")
            return step

        result = built(plan=plan, execute=execute).invoke({"task": "t"})

        assert [each["step"] for each in result["done"]] == ["find", "write"]


class TestBadArguments:
    @pytest.mark.parametrize("most", [0, -1, 33, True, 2.0, "3", None])
    def test_a_step_limit_outside_one_to_thirty_two_is_refused(self, most: Any) -> None:
        with pytest.raises(ValueError, match="max_steps must be a whole number from 1 to 32"):
            built(max_steps=most)

    def test_the_step_limit_has_no_default(self) -> None:
        with pytest.raises(TypeError, match="max_steps"):
            graphs.plan_execute(plan=two_steps, execute=did, replan=keep_plan)  # type: ignore[call-arg]


class TestBadReturns:
    @pytest.mark.parametrize("returned", ["find", None, {"find": "x"}, 7])
    def test_plan_returning_no_list_is_reported(self, returned: Any) -> None:
        graph = built(plan=lambda task: returned)

        with pytest.raises(graphs.GraphError, match=PLAN_RETURNS):
            graph.invoke({"task": "t"})

    @pytest.mark.parametrize(
        "returned", [["find", 7], [None], [["nested"]], [float("nan")], [{"find"}]]
    )
    def test_plan_returning_a_step_that_is_not_text_is_reported(self, returned: Any) -> None:
        graph = built(plan=lambda task: returned)

        with pytest.raises(graphs.GraphError, match=PLAN_RETURNS):
            graph.invoke({"task": "t"})

    def test_a_tuple_of_steps_is_a_plan_too(self) -> None:
        result = built(plan=lambda task: ("find",)).invoke({"task": "t"})

        assert [each["step"] for each in result["done"]] == ["find"]

    def test_a_plan_of_255_steps_is_within_the_limits(self) -> None:
        graph = built(plan=lambda task: ["s"] * 255, max_steps=1)

        result = graph.invoke({"task": "t"})

        assert len(result["done"]) == 1

    def test_a_plan_of_256_steps_is_over_the_limits(self) -> None:
        graph = built(plan=lambda task: ["s"] * 256)

        with pytest.raises(graphs.GraphError, match=f"plan must return {LIMITS}"):
            graph.invoke({"task": "t"})

    def test_a_plan_over_16_kb_as_json_is_over_the_limits(self) -> None:
        graph = built(plan=lambda task: ["x" * 16_383])

        with pytest.raises(graphs.GraphError, match=f"plan must return {LIMITS}"):
            graph.invoke({"task": "t"})

    def test_execute_returning_no_text_is_reported(self) -> None:
        graph = built(execute=lambda task, step, done: 7)

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"task": "t"})

    def test_a_result_of_exactly_16_kb_as_json_is_kept(self) -> None:
        graph = built(execute=lambda task, step, done: "x" * 16_382, max_steps=1)

        result = graph.invoke({"task": "t"})

        assert len(result["done"][0]["result"]) == 16_382

    def test_a_result_one_byte_over_16_kb_as_json_is_reported(self) -> None:
        graph = built(execute=lambda task, step, done: "x" * 16_383)

        with pytest.raises(graphs.GraphError, match=f"execute must return {LIMITS}"):
            graph.invoke({"task": "t"})

    @pytest.mark.parametrize(
        "returned",
        [
            "an answer",
            None,
            ["write"],
            {},
            {"answer": "a", "steps": ["b"]},
            {"answer": "a", "note": "b"},
            {"steps": []},
            {"steps": "write"},
            {"steps": ["write", 7]},
            {"answer": 7},
            {"answer": None},
            {"note": "b"},
        ],
    )
    def test_replan_returning_anything_but_an_answer_or_steps_is_reported(
        self, returned: Any
    ) -> None:
        graph = built(replan=lambda task, done, remaining: returned)

        with pytest.raises(graphs.GraphError, match=REPLAN_RETURNS):
            graph.invoke({"task": "t"})

    def test_replan_returning_an_answer_over_16_kb_as_json_is_reported(self) -> None:
        graph = built(replan=lambda task, done, remaining: {"answer": "x" * 16_384})

        with pytest.raises(graphs.GraphError, match=f"replan must return {LIMITS}"):
            graph.invoke({"task": "t"})

    def test_replan_returning_256_steps_is_over_the_limits(self) -> None:
        graph = built(replan=lambda task, done, remaining: {"steps": ["s"] * 256})

        with pytest.raises(graphs.GraphError, match=f"replan must return {LIMITS}"):
            graph.invoke({"task": "t"})

    @pytest.mark.parametrize(
        "wrap", [types.MappingProxyType, collections.ChainMap], ids=["mappingproxy", "chainmap"]
    )
    @pytest.mark.parametrize(
        "decision",
        [{"answer": "x" * 20_000}, {"steps": ["x" * 20_000]}],
        ids=["long-answer", "long-step"],
    )
    def test_a_mapping_that_is_not_a_dict_cannot_carry_a_value_past_the_limits(
        self, wrap: Any, decision: dict[str, Any]
    ) -> None:
        graph = built(replan=lambda task, done, remaining: wrap(decision))

        with pytest.raises(graphs.GraphError, match=REPLAN_RETURNS):
            graph.invoke({"task": "t"})

    def test_a_result_that_is_a_str_subclass_is_kept_as_plain_text(self) -> None:
        class Marked(str):
            pass

        graph = built(execute=lambda task, step, done: Marked(step), max_steps=1)

        result = graph.invoke({"task": "t"})

        assert type(result["done"][0]["result"]) is str

    def test_a_step_that_raises_fails_the_run_with_its_error(self) -> None:
        def broken(task: str, step: str, done: list[dict[str, str]]) -> str:
            raise LookupError("source down")

        with pytest.raises(LookupError, match="source down"):
            built(execute=broken).invoke({"task": "t"})

    def test_the_error_does_not_repeat_the_task_or_the_value(self) -> None:
        graph = built(plan=lambda task: ["private-step-text", 7])

        with pytest.raises(graphs.GraphError) as raised:
            graph.invoke({"task": "private-task-text"})

        assert "private" not in str(raised.value)

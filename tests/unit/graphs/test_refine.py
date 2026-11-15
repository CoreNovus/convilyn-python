"""``graphs.refine``: the shared contract, then what is particular to it."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from convilyn import graphs
from tests.unit.graphs.graph_contract import GraphContract


def add_what_is_asked(task: str, previous: str | None, feedback: str | None) -> str:
    return task if previous is None else f"{previous}{feedback}"


def wants_three_marks(task: str, draft: str) -> str | None:
    return None if draft.endswith("!!!") else "!"


class TestRefineContract(GraphContract):
    NAME = "refine"

    @pytest.fixture
    def graph(self) -> Any:
        return graphs.refine(generate=add_what_is_asked, evaluate=wants_three_marks, max_rounds=5)

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"task": "ship"}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        return {"result": "ship!!!", "accepted": True, "feedback": None, "rounds": 4}


class TestRounds:
    def test_a_draft_accepted_first_time_takes_one_round(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=lambda task, draft: None)

        result = graph.invoke({"task": "ship"})

        assert result == {"result": "ship", "accepted": True, "feedback": None, "rounds": 1}

    def test_it_stops_at_the_limit_and_reports_the_last_feedback(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=wants_three_marks, max_rounds=2)

        result = graph.invoke({"task": "ship"})

        assert result == {"result": "ship!", "accepted": False, "feedback": "!", "rounds": 2}

    def test_a_draft_accepted_on_the_last_round_is_accepted(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=wants_three_marks, max_rounds=4)

        result = graph.invoke({"task": "ship"})

        assert result["accepted"] is True

    def test_a_limit_of_one_evaluates_the_only_draft(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=wants_three_marks, max_rounds=1)

        result = graph.invoke({"task": "ship"})

        assert result == {"result": "ship", "accepted": False, "feedback": "!", "rounds": 1}

    def test_the_generator_is_handed_the_previous_draft_and_its_feedback(self) -> None:
        seen: list[tuple[str, str | None, str | None]] = []

        def record(task: str, previous: str | None, feedback: str | None) -> str:
            seen.append((task, previous, feedback))
            return f"{task}{len(seen)}"

        graph = graphs.refine(
            generate=record, evaluate=lambda task, draft: "again" if draft == "t1" else None
        )

        graph.invoke({"task": "t"})

        assert seen == [("t", None, None), ("t", "t1", "again")]

    def test_text_in_any_script_passes_through_unchanged(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=lambda task, draft: None)

        result = graph.invoke({"task": "退款政策 환불 返金"})

        assert result["result"] == "退款政策 환불 返金"


class TestAsyncFunctions:
    async def test_async_functions_run_under_the_async_entry_point(self) -> None:
        async def generate(task: str, previous: str | None, feedback: str | None) -> str:
            await asyncio.sleep(0)
            return task

        async def evaluate(task: str, draft: str) -> str | None:
            await asyncio.sleep(0)
            return None

        graph = graphs.refine(generate=generate, evaluate=evaluate)

        result = await graph.ainvoke({"task": "ship"})

        assert result == {"result": "ship", "accepted": True, "feedback": None, "rounds": 1}

    def test_an_async_function_under_the_sync_entry_point_says_which_to_use(self) -> None:
        async def generate(task: str, previous: str | None, feedback: str | None) -> str:
            await asyncio.sleep(0)
            return task

        graph = graphs.refine(generate=generate, evaluate=lambda task, draft: None)

        with pytest.raises(TypeError, match="run it with ainvoke or astream"):
            graph.invoke({"task": "ship"})


class TestBadArguments:
    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_limit_below_one_is_refused(self, limit: int) -> None:
        with pytest.raises(ValueError, match="max_rounds must be at least 1"):
            graphs.refine(generate=add_what_is_asked, evaluate=wants_three_marks, max_rounds=limit)

    def test_a_generator_that_returns_no_text_is_reported(self) -> None:
        graph = graphs.refine(
            generate=lambda task, previous, feedback: 7, evaluate=wants_three_marks
        )  # type: ignore[arg-type,return-value]

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"task": "ship"})

    def test_an_evaluator_that_returns_neither_text_nor_none_is_reported(self) -> None:
        graph = graphs.refine(generate=add_what_is_asked, evaluate=lambda task, draft: False)  # type: ignore[arg-type,return-value]

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"task": "ship"})

    def test_the_error_does_not_repeat_the_task(self) -> None:
        graph = graphs.refine(
            generate=lambda task, previous, feedback: 7, evaluate=wants_three_marks
        )  # type: ignore[arg-type,return-value]

        with pytest.raises(graphs.GraphError) as raised:
            graph.invoke({"task": "private-task-text"})

        assert "private-task-text" not in str(raised.value)


class TestAsyncFunctionsAreRefusedBeforeTheyRun:
    def test_a_function_that_really_waits_gets_the_same_refusal(self) -> None:
        async def generate(task: str, previous: str | None, feedback: str | None) -> str:
            await asyncio.sleep(0.01)
            return task

        graph = graphs.refine(generate=generate, evaluate=lambda task, draft: None)

        with pytest.raises(TypeError, match="run it with ainvoke or astream"):
            graph.invoke({"task": "ship"})

    def test_the_refused_function_has_not_run_a_single_line(self) -> None:
        started: list[str] = []

        async def generate(task: str, previous: str | None, feedback: str | None) -> str:
            started.append(task)
            return task

        graph = graphs.refine(generate=generate, evaluate=lambda task, draft: None)
        with pytest.raises(TypeError):
            graph.invoke({"task": "ship"})

        assert started == []

    async def test_a_refusal_does_not_stop_the_same_graph_running_async_afterwards(self) -> None:
        async def generate(task: str, previous: str | None, feedback: str | None) -> str:
            return task

        graph = graphs.refine(generate=generate, evaluate=lambda task, draft: None)
        with pytest.raises(TypeError):
            graph.invoke({"task": "ship"})

        result = await graph.ainvoke({"task": "ship"})

        assert result["result"] == "ship"


class TestACallerFunctionRunsAsItWouldOutsideTheGraph:
    def test_a_plain_function_may_run_another_graph_asynchronously(self) -> None:
        inner = graphs.refine(generate=add_what_is_asked, evaluate=lambda task, draft: None)

        def generate(task: str, previous: str | None, feedback: str | None) -> str:
            return asyncio.run(inner.ainvoke({"task": task}))["result"]

        outer = graphs.refine(generate=generate, evaluate=lambda task, draft: None)

        result = outer.invoke({"task": "ship"})

        assert result["result"] == "ship"

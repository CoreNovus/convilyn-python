"""``graphs.fanout``: the shared contract, then what is particular to it."""

from __future__ import annotations

import asyncio
import threading
import time
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

NAMES = ["docs", "tickets", "billing"]


def docs(text: str) -> str:
    return f"docs on {text}"


def tickets(text: str) -> str:
    return f"tickets on {text}"


def billing(text: str) -> str:
    return f"billing on {text}"


BRANCHES = {"docs": docs, "tickets": tickets, "billing": billing}


def tickets_then_docs(question: str, names: list[str]) -> dict[str, str]:
    """Names two of the three, the later-declared one first, each with its own text."""
    return {"tickets": f"{question} (open)", "docs": question}


def joined(question: str, results: dict[str, str]) -> str:
    return f"{question}: " + " | ".join(f"{name}={output}" for name, output in results.items())


def built(**overrides: Any) -> Any:
    arguments = {"branches": BRANCHES, "classify": tickets_then_docs, "synthesize": joined}
    return graphs.fanout(**{**arguments, **overrides})


class TestFanoutContract(GraphContract):
    NAME = "fanout"

    @pytest.fixture
    def graph(self) -> Any:
        return built()

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"question": "login fails"}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        answer = "login fails: tickets=tickets on login fails (open) | docs=docs on login fails"
        return {
            "answer": answer,
            "results": {
                "tickets": "tickets on login fails (open)",
                "docs": "docs on login fails",
            },
        }


class TestWhatRuns:
    def test_classify_is_handed_the_question_and_every_branch_name_in_declared_order(
        self,
    ) -> None:
        seen: list[tuple[str, list[str]]] = []

        def record(question: str, names: list[str]) -> dict[str, str]:
            seen.append((question, names))
            return {}

        built(classify=record).invoke({"question": "q"})

        assert seen == [("q", NAMES)]

    def test_each_chosen_branch_runs_once_with_the_text_classify_gave_it(self) -> None:
        calls: list[tuple[str, str]] = []
        recording = {
            name: (lambda text, name=name: calls.append((name, text)) or name) for name in NAMES
        }

        built(branches=recording).invoke({"question": "q"})

        assert sorted(calls) == [("docs", "q"), ("tickets", "q (open)")]

    def test_a_branch_classify_did_not_name_never_runs(self) -> None:
        calls: list[str] = []

        def watched(text: str) -> str:
            calls.append(text)
            return text

        built(branches={**BRANCHES, "billing": watched}).invoke({"question": "q"})

        assert calls == []

    def test_every_branch_can_be_chosen(self) -> None:
        graph = built(classify=lambda question, names: {name: question for name in names})

        result = graph.invoke({"question": "q"})

        assert result["results"] == {name: f"{name} on q" for name in NAMES}

    def test_a_single_branch_graph_runs_its_branch(self) -> None:
        graph = built(branches={"docs": docs}, classify=lambda question, names: {"docs": question})

        result = graph.invoke({"question": "q"})

        assert result == {"answer": "q: docs=docs on q", "results": {"docs": "docs on q"}}

    def test_when_classify_chooses_nothing_synthesize_gets_no_results(self) -> None:
        seen: list[dict[str, str]] = []

        def record(question: str, results: dict[str, str]) -> str:
            seen.append(dict(results))
            return "nothing applied"

        built(classify=lambda question, names: {}, synthesize=record).invoke({"question": "q"})

        assert seen == [{}]

    def test_when_classify_chooses_nothing_the_answer_is_synthesize_s(self) -> None:
        graph = built(
            classify=lambda question, names: {}, synthesize=lambda question, results: "none"
        )

        result = graph.invoke({"question": "q"})

        assert result == {"answer": "none", "results": {}}

    def test_text_in_any_script_passes_through_unchanged(self) -> None:
        graph = built(
            branches={"docs": lambda text: text},
            classify=lambda question, names: {"docs": question},
        )

        result = graph.invoke({"question": "退款政策 환불 返金"})

        assert result["results"] == {"docs": "退款政策 환불 返金"}


class TestOrder:
    async def test_results_follow_classify_s_order_not_the_order_branches_finish(self) -> None:
        async def slow(text: str) -> str:
            await asyncio.sleep(0.05)
            return "slow"

        async def quick(text: str) -> str:
            return "quick"

        graph = graphs.fanout(
            branches={"quick": quick, "slow": slow},
            classify=lambda question, names: {"slow": question, "quick": question},
            synthesize=joined,
        )

        result = await graph.ainvoke({"question": "q"})

        assert list(result["results"]) == ["slow", "quick"]

    def test_synthesize_sees_the_results_in_classify_s_order(self) -> None:
        seen: list[list[str]] = []

        def record(question: str, results: dict[str, str]) -> str:
            seen.append(list(results))
            return "done"

        built(synthesize=record).invoke({"question": "q"})

        assert seen == [["tickets", "docs"]]


class TestTheyRunAtTheSameTime:
    def test_branches_run_at_the_same_time_under_invoke(self) -> None:
        # Each branch waits for the other: run one after the other, the wait times out.
        both = threading.Barrier(2, timeout=5)

        def meet(text: str) -> str:
            both.wait()
            return text

        graph = built(branches={"docs": meet, "tickets": meet})

        result = graph.invoke({"question": "q"})

        assert result["results"] == {"tickets": "q (open)", "docs": "q"}

    async def test_branches_run_at_the_same_time_under_ainvoke(self) -> None:
        both = asyncio.Barrier(2)

        async def meet(text: str) -> str:
            await asyncio.wait_for(both.wait(), timeout=5)
            return text

        graph = built(branches={"docs": meet, "tickets": meet})

        result = await graph.ainvoke({"question": "q"})

        assert result["results"] == {"tickets": "q (open)", "docs": "q"}

    def test_the_host_s_concurrency_limit_holds_under_invoke(self) -> None:
        running: list[int] = [0]
        most: list[int] = [0]
        lock = threading.Lock()

        def counted(text: str) -> str:
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.02)
            with lock:
                running[0] -= 1
            return text

        graph = built(
            branches={name: counted for name in NAMES},
            classify=lambda question, names: {name: question for name in names},
        )

        graph.invoke({"question": "q"}, {"max_concurrency": 1})

        assert most == [1]

    async def test_the_host_s_concurrency_limit_holds_under_ainvoke(self) -> None:
        running: list[int] = [0]
        most: list[int] = [0]

        async def counted(text: str) -> str:
            running[0] += 1
            most[0] = max(most[0], running[0])
            await asyncio.sleep(0.02)
            running[0] -= 1
            return text

        graph = built(
            branches={name: counted for name in NAMES},
            classify=lambda question, names: {name: question for name in names},
        )

        await graph.ainvoke({"question": "q"}, {"max_concurrency": 1})

        assert most == [1]


class TestABranchThatPauses:
    def paused_host(self, ran: list[str], finished: list[str]) -> Any:
        def tickets_branch(text: str) -> str:
            finished.append(text)
            return "found"

        graph = built(
            branches={"docs": pausing_function({"approve": "docs"}, ran), "tickets": tickets_branch}
        )
        return host_around(graph, ("question", "answer", "results"), saved=True)

    def test_the_run_pauses_with_what_the_branch_asked(self) -> None:
        host = self.paused_host([], [])

        host.invoke({"question": "q"}, THREAD)

        assert pending(host) == [{"approve": "docs"}]

    def test_resumed_it_finishes_with_the_branch_s_answer(self) -> None:
        host = self.paused_host([], [])
        host.invoke({"question": "q"}, THREAD)

        result = host.invoke(resume("yes"), THREAD)

        assert result["results"] == {"tickets": "found", "docs": "answer: yes"}

    def test_a_branch_that_finished_before_the_pause_does_not_run_again(self) -> None:
        finished: list[str] = []
        host = self.paused_host([], finished)
        host.invoke({"question": "q"}, THREAD)

        host.invoke(resume("yes"), THREAD)

        assert finished == ["q (open)"]

    async def test_under_ainvoke_a_finished_branch_does_not_run_again_either(self) -> None:
        finished: list[str] = []
        host = self.paused_host([], finished)
        await host.ainvoke({"question": "q"}, THREAD)

        await host.ainvoke(resume("yes"), THREAD)

        assert finished == ["q (open)"]

    def test_the_paused_branch_runs_again_from_its_start(self) -> None:
        ran: list[str] = []
        host = self.paused_host(ran, [])
        host.invoke({"question": "q"}, THREAD)

        host.invoke(resume("yes"), THREAD)

        assert ran == ["q", "q"]


class TestAsyncFunctions:
    async def test_async_functions_run_under_the_async_entry_point(self) -> None:
        async def classify(question: str, names: list[str]) -> dict[str, str]:
            return {"docs": question}

        async def branch(text: str) -> str:
            return text.upper()

        async def synthesize(question: str, results: dict[str, str]) -> str:
            return results["docs"]

        graph = graphs.fanout(branches={"docs": branch}, classify=classify, synthesize=synthesize)

        result = await graph.ainvoke({"question": "q"})

        assert result == {"answer": "Q", "results": {"docs": "Q"}}

    def test_an_async_branch_under_the_sync_entry_point_says_which_to_use(self) -> None:
        async def branch(text: str) -> str:
            return text

        graph = built(branches={**BRANCHES, "docs": branch})

        with pytest.raises(TypeError, match="run it with ainvoke or astream"):
            graph.invoke({"question": "q"})


class TestWhatItLeavesAlone:
    def test_synthesize_changing_the_results_it_was_handed_does_not_change_the_output(
        self,
    ) -> None:
        def clearing(question: str, results: dict[str, str]) -> str:
            results.clear()
            return "done"

        result = built(synthesize=clearing).invoke({"question": "q"})

        assert result["results"] == {"tickets": "tickets on q (open)", "docs": "docs on q"}

    def test_changing_the_branches_mapping_after_building_does_not_change_the_graph(
        self,
    ) -> None:
        branches = dict(BRANCHES)
        graph = built(branches=branches)
        branches["docs"] = lambda text: "swapped"

        result = graph.invoke({"question": "q"})

        assert result["results"]["docs"] == "docs on q"


class TestBadArguments:
    def test_no_branches_is_refused(self) -> None:
        with pytest.raises(ValueError, match="branches must name at least one branch"):
            built(branches={})

    @pytest.mark.parametrize("name", ["", 7])
    def test_a_branch_name_that_is_not_text_is_refused(self, name: Any) -> None:
        with pytest.raises(ValueError, match="every branch name must be a non-empty string"):
            built(branches={name: docs})

    def test_a_branch_that_is_not_a_function_is_refused(self) -> None:
        with pytest.raises(ValueError, match="every branch must be a function"):
            built(branches={"docs": "not a function"})


class TestBadReturns:
    @pytest.mark.parametrize("returned", [["docs"], "docs", None])
    def test_classify_returning_no_mapping_is_reported(self, returned: Any) -> None:
        graph = built(classify=lambda question, names: returned)

        with pytest.raises(graphs.GraphError, match="classify must return a mapping"):
            graph.invoke({"question": "q"})

    def test_classify_naming_an_unknown_branch_is_reported(self) -> None:
        graph = built(classify=lambda question, names: {"weather": question})

        with pytest.raises(graphs.GraphError, match="classify named a branch this graph"):
            graph.invoke({"question": "q"})

    def test_the_error_does_not_repeat_the_unknown_name(self) -> None:
        graph = built(classify=lambda question, names: {"private-branch-name": question})

        with pytest.raises(graphs.GraphError) as raised:
            graph.invoke({"question": "q"})

        assert "private-branch-name" not in str(raised.value)

    @pytest.mark.parametrize("text", [7, None, ["q"]])
    def test_classify_giving_a_branch_no_text_is_reported(self, text: Any) -> None:
        graph = built(classify=lambda question, names: {"docs": text})

        with pytest.raises(graphs.GraphError, match="classify must give each branch text"):
            graph.invoke({"question": "q"})

    def test_a_branch_that_returns_no_text_is_reported(self) -> None:
        graph = built(branches={**BRANCHES, "docs": lambda text: 7})

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"question": "q"})

    def test_synthesize_returning_no_text_is_reported(self) -> None:
        graph = built(synthesize=lambda question, results: None)

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"question": "q"})

    def test_a_branch_that_raises_fails_the_run_with_its_error(self) -> None:
        def broken(text: str) -> str:
            raise LookupError("source down")

        graph = built(branches={**BRANCHES, "docs": broken})

        with pytest.raises(LookupError, match="source down"):
            graph.invoke({"question": "q"})

    def test_the_error_does_not_repeat_the_question(self) -> None:
        graph = built(classify=lambda question, names: {"docs": 7})

        with pytest.raises(graphs.GraphError) as raised:
            graph.invoke({"question": "private-question-text"})

        assert "private-question-text" not in str(raised.value)

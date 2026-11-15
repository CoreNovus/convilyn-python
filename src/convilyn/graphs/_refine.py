"""Draft, evaluate, redo with the feedback — until accepted or out of rounds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from convilyn.graphs._nodes import Call, node, text
from convilyn.graphs.types import GraphInfo, RefineInput, RefineOutput, RefineState

#: ``(task, previous draft, feedback on it)`` to a new draft. Both are ``None``
#: for the first draft.
Generate = Callable[[str, str | None, str | None], str | Awaitable[str]]
#: ``(task, draft)`` to what is wrong with the draft, or ``None`` to accept it.
Evaluate = Callable[[str, str], str | None | Awaitable[str | None]]

INFO = GraphInfo(
    name="refine",
    summary="Draft, evaluate, and redo with the feedback until accepted.",
    use_when="a draft can be checked, by tests, a schema, a linter or a reviewer, and fixed "
    "from what the check says: it drafts, checks and redrafts with the feedback until the "
    "check passes or the rounds run out.",
    inputs=tuple(RefineInput.__annotations__),
    outputs=tuple(RefineOutput.__annotations__),
    requires=("generate", "evaluate"),
)


def refine(
    *, generate: Generate, evaluate: Evaluate, max_rounds: int = 3
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a graph that improves a draft until ``evaluate`` accepts it.

    Use it when a draft can be checked, by tests, a schema, a linter or a
    reviewer, and fixed from what the check says: it drafts, checks and
    redrafts with the feedback until the check passes or the rounds run out.

    ``evaluate`` returns ``None`` to accept, or text saying what to fix. It does
    not have to be a model: a schema check, a test run or a linter gives
    feedback a model cannot argue with. The graph stops after ``max_rounds``
    drafts either way and reports whether the last one was accepted.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")

    async def draft(state: RefineState, call: Call) -> dict[str, Any]:
        produced = await call(generate, state["task"], state.get("result"), state.get("feedback"))
        return {"result": text(produced), "rounds": state.get("rounds", 0) + 1}

    async def review(state: RefineState, call: Call) -> dict[str, Any]:
        feedback = await call(evaluate, state["task"], state["result"])
        return {
            "accepted": feedback is None,
            "feedback": None if feedback is None else text(feedback),
        }

    def after_review(state: RefineState) -> Literal["draft", "done"]:
        return "done" if state["accepted"] or state["rounds"] >= max_rounds else "draft"

    builder = StateGraph(RefineState, input_schema=RefineInput, output_schema=RefineOutput)
    builder.add_node("draft", node(draft))
    builder.add_node("review", node(review))
    builder.add_edge(START, "draft")
    builder.add_edge("draft", "review")
    builder.add_conditional_edges("review", after_review, {"draft": "draft", "done": END})
    return builder.compile(name=INFO.name)

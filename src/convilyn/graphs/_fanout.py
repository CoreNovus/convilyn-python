"""Choose the branches a question needs, run them at the same time, combine what they found."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from convilyn.graphs._nodes import Call, node, text
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import FanoutInput, FanoutOutput, FanoutState, GraphInfo

#: The text ``classify`` sent a branch to what the branch found.
Branch = Callable[[str], str | Awaitable[str]]
Plan = Mapping[str, str]
#: ``(question, every branch name)`` to the branches to run, each mapped to the
#: text it is given: the question itself, or the part of it that branch answers.
Classify = Callable[[str, list[str]], Plan | Awaitable[Plan]]
#: ``(question, what each branch that ran returned)`` to the answer. The mapping
#: is empty when ``classify`` chose no branch.
Synthesize = Callable[[str, dict[str, str]], str | Awaitable[str]]

INFO = GraphInfo(
    name="fanout",
    summary="Choose the branches a question needs, run them at the same time, combine the results.",
    use_when="a question may need several independent sources at once: it runs the ones "
    "your classifier picks at the same time and combines what they return into one answer.",
    inputs=tuple(FanoutInput.__annotations__),
    outputs=tuple(FanoutOutput.__annotations__),
    requires=("branches", "classify", "synthesize"),
)

_PLAN = "cv_fanout_plan"
_DONE = "cv_fanout_done"
_BRANCH = "cv_fanout_branch"
_TEXT = "cv_fanout_text"


def _functions(branches: Mapping[str, Branch]) -> dict[str, Branch]:
    functions = dict(branches)
    if not functions:
        raise ValueError("branches must name at least one branch")
    if not all(isinstance(name, str) and name for name in functions):
        raise ValueError("every branch name must be a non-empty string")
    if not all(callable(function) for function in functions.values()):
        raise ValueError("every branch must be a function")
    return functions


def fanout(
    *, branches: Mapping[str, Branch], classify: Classify, synthesize: Synthesize
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a graph that answers a question from the branches it applies to.

    Use it when a question may need several independent sources at once: it
    runs the ones your classifier picks at the same time and combines what they
    return into one answer.

    ``classify`` is given the question and every branch name, and returns the
    branches to run, each mapped to the text that branch is given. Every branch
    it names runs once, all of them at the same time; ``synthesize`` then
    receives the question and what each one returned, in the order ``classify``
    named them, whatever order they finished in. When ``classify`` names none,
    ``synthesize`` is called with nothing, so the caller decides what that
    answer says.

    Branches run at the same time under ``invoke`` too, each on a thread of its
    own, so a plain function shared between branches must be safe to call that
    way. ``max_concurrency`` in the run's config caps how many run at once. A
    branch that raises fails the run with its error. A branch may pause with
    ``interrupt``: in a host with a saved-state backend, resuming runs that
    branch again from its start, and the branches that had already finished
    are not run again.
    """
    functions = _functions(branches)
    names = tuple(functions)

    async def choose(state: FanoutState, call: Call) -> dict[str, Any]:
        chosen = await call(classify, state["question"], list(names))
        if not isinstance(chosen, Mapping):
            raise GraphError("classify must return a mapping of branch names to text")
        plan: dict[str, str] = {}
        for name, given in chosen.items():
            if name not in functions:
                # Never the name itself: it may be anything a model wrote.
                raise GraphError("classify named a branch this graph was not built with")
            if not isinstance(given, str):
                raise GraphError("classify must give each branch text")
            plan[names[names.index(name)]] = given
        return {_PLAN: plan}

    def dispatch(state: FanoutState) -> list[Send] | Literal["combine"]:
        plan = state[_PLAN]
        return [Send("branch", {_BRANCH: name, _TEXT: given}) for name, given in plan.items()] or (
            "combine"
        )

    async def branch(sent: dict[str, str], call: Call) -> dict[str, Any]:
        name = sent[_BRANCH]
        return {_DONE: {name: text(await call(functions[name], sent[_TEXT]))}}

    async def combine(state: FanoutState, call: Call) -> dict[str, Any]:
        done = state.get(_DONE) or {}
        results = {name: done[name] for name in state[_PLAN]}
        answer = text(await call(synthesize, state["question"], dict(results)))
        return {"answer": answer, "results": results}

    builder = StateGraph(FanoutState, input_schema=FanoutInput, output_schema=FanoutOutput)
    builder.add_node("classify", node(choose))
    builder.add_node("branch", node(branch))
    builder.add_node("combine", node(combine))
    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", dispatch, ["branch", "combine"])
    builder.add_edge("branch", "combine")
    builder.add_edge("combine", END)
    return builder.compile(name=INFO.name)

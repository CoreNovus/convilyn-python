"""Plan, carry the plan out a step at a time, and replan after each step."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from convilyn.graphs import _schema_limits as limits
from convilyn.graphs._nodes import Call, node, text
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import (
    ExecutedStep,
    GraphInfo,
    PlanExecuteInput,
    PlanExecuteOutput,
    PlanExecuteState,
)

Steps = Sequence[str]
#: ``task`` to the steps that carry it out, in order.
Plan = Callable[[str], Steps | Awaitable[Steps]]
#: ``(task, step, done)`` to what carrying out ``step`` produced. ``done`` lists
#: every earlier step as ``{"step": ..., "result": ...}``.
Execute = Callable[[str, str, list[ExecutedStep]], str | Awaitable[str]]
Decision = dict[str, Any]
#: ``(task, done, remaining)`` to ``{"answer": text}`` once the task is done, or
#: to ``{"steps": [...]}``, the steps to carry out next: ``remaining`` to keep
#: the plan, or new ones.
Replan = Callable[[str, list[ExecutedStep], list[str]], Decision | Awaitable[Decision]]

INFO = GraphInfo(
    name="plan_execute",
    summary="Plan, carry out the plan a step at a time, and replan after each step.",
    use_when="a task takes several steps whose results shape the next ones: it plans, "
    "carries out one step at a time and revises the rest of the plan after each step, "
    "within a step limit.",
    inputs=tuple(PlanExecuteInput.__annotations__),
    outputs=tuple(PlanExecuteOutput.__annotations__),
    requires=("plan", "execute", "replan"),
)

#: The most steps one run may carry out. Every result stays in the run's state
#: and is handed to ``replan`` again after each step, so the bound is small.
MOST_STEPS = 32

_REMAINING = "cv_plan_execute_remaining"
_PLAN_RETURNS = "plan must return a list of steps, each as text"
_REPLAN_RETURNS = 'replan must return {"answer": text} or {"steps": [text, ...]}'


def _plain(returned: object, function: str, refusal: str) -> Any:
    """``returned`` as the plain JSON it is written as, within the value limits.

    Only that copy is looked at and kept, so what reaches the state is what was
    measured: a mapping JSON cannot write, or one that is not a ``dict``, is
    refused rather than let past the byte limit. Errors name the function,
    never the value.
    """
    try:
        written = limits.written(returned)
    except (TypeError, ValueError):
        raise GraphError(refusal) from None
    if written is None:
        raise GraphError(f"{function} must return {limits.WITHIN}")
    return json.loads(written)


def _steps(plain: object, refusal: str) -> list[str]:
    if not isinstance(plain, list) or not all(isinstance(step, str) for step in plain):
        raise GraphError(refusal)
    return plain


def _decision(plain: object) -> dict[str, Any]:
    if not isinstance(plain, dict) or len(plain) != 1:
        raise GraphError(_REPLAN_RETURNS)
    returned: dict[str, Any] = plain
    if "answer" in returned:
        if not isinstance(returned["answer"], str):
            raise GraphError(_REPLAN_RETURNS)
        return {"answer": returned["answer"], "answered": True}
    if "steps" in returned:
        steps = _steps(returned["steps"], _REPLAN_RETURNS)
        if not steps:
            raise GraphError(_REPLAN_RETURNS)
        return {_REMAINING: steps}
    raise GraphError(_REPLAN_RETURNS)


def _copied(done: list[ExecutedStep]) -> list[ExecutedStep]:
    return [ExecutedStep(step=each["step"], result=each["result"]) for each in done]


def plan_execute(
    *, plan: Plan, execute: Execute, replan: Replan, max_steps: int
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a graph that carries out a task by a plan it revises as it goes.

    Use it when a task takes several steps whose results shape the next ones:
    it plans, carries out one step at a time and revises the rest of the plan
    after each step, within a step limit.

    ``plan`` turns the task into steps. ``execute`` carries out the first step
    still to do and returns its result; it is handed the task, the step, and
    every step done so far with its result. After every step, ``replan`` is
    handed the task, what is done and what remains, and returns either
    ``{"answer": text}``, which ends the run, or ``{"steps": [...]}``, the steps
    to carry out next. An empty plan goes straight to ``replan``.

    The three are separate functions so that each can be what suits it: a
    strong model to plan, a cheap one, a tool or another graph to carry out a
    step, and plain code to replan when nothing needs rethinking (returning
    ``remaining`` keeps the plan).

    ``max_steps`` (1 to 32, required) is the most steps one run carries out. A
    run that reaches it without an answer ends with ``answered`` false and no
    ``answer``; ``replan`` still runs after the last step, so it may answer
    there. What each function returns is bounded as :func:`collect` bounds a
    value: at most 256 parts, 64 levels and 16 KB as JSON, or the run fails
    with a ``GraphError``. A longer result is kept outside the graph and
    referred to by a short text.

    Each step is a step of the graph, so a host with a saved-state backend
    saves the run after each one. A step may pause with ``interrupt``: resuming
    runs that step again from its start, and the steps already done are not
    run again.
    """
    if type(max_steps) is not int or not 1 <= max_steps <= MOST_STEPS:
        raise ValueError(f"max_steps must be a whole number from 1 to {MOST_STEPS}")

    async def first(state: PlanExecuteState, call: Call) -> dict[str, Any]:
        returned = await call(plan, state["task"])
        # Measured first: the measure stops at the limits, a look at every step does not.
        steps = _steps(_plain(returned, "plan", _PLAN_RETURNS), _PLAN_RETURNS)
        return {_REMAINING: steps, "done": [], "answer": None, "answered": False}

    async def carry_out(state: PlanExecuteState, call: Call) -> dict[str, Any]:
        step, *rest = state[_REMAINING]
        result = text(await call(execute, state["task"], step, _copied(state["done"])))
        kept = text(_plain(result, "execute", "execute must return text"))
        done = [*state["done"], ExecutedStep(step=step, result=kept)]
        return {"done": done, _REMAINING: rest}

    async def revise(state: PlanExecuteState, call: Call) -> dict[str, Any]:
        returned = await call(
            replan, state["task"], _copied(state["done"]), list(state[_REMAINING])
        )
        return _decision(_plain(returned, "replan", _REPLAN_RETURNS))

    def after_plan(state: PlanExecuteState) -> Literal["execute", "replan"]:
        return "execute" if state[_REMAINING] else "replan"

    def after_replan(state: PlanExecuteState) -> Literal["execute", "end"]:
        if state["answered"] or len(state["done"]) >= max_steps:
            return "end"
        return "execute"

    builder = StateGraph(
        PlanExecuteState, input_schema=PlanExecuteInput, output_schema=PlanExecuteOutput
    )
    builder.add_node("plan", node(first))
    builder.add_node("execute", node(carry_out))
    builder.add_node("replan", node(revise))
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", after_plan, {"execute": "execute", "replan": "replan"})
    builder.add_edge("execute", "replan")
    builder.add_conditional_edges("replan", after_replan, {"execute": "execute", "end": END})
    return builder.compile(name=INFO.name)

"""Route a message to one of your use cases, collect its fields, and answer with only its tools."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from convilyn.graphs._collect import Check, Extract, collect
from convilyn.graphs._nodes import Call, node
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import GraphInfo, UseCase, UseCaseInput, UseCaseOutput, UseCaseState

Graph = CompiledStateGraph[Any, Any, Any, Any]
Choice = Mapping[str, Any]
#: ``(message, choices, active)`` to ``{"use_case_id": ..., "candidates": [...]}``.
#: ``choices`` lists every use case as ``{"id", "description"}``; ``active`` is
#: the use case in progress, or ``None``.
Route = Callable[[str, list[dict[str, str]], str | None], Choice | Awaitable[Choice]]

INFO = GraphInfo(
    name="use_case",
    summary="Route a message to one of your use cases, collect its fields, and answer "
    "with only its tools.",
    use_when="your application handles several distinct tasks, each with its own "
    "instructions, required details and allowed tools: it sends each message to one task, "
    "gathers that task's details and lets an agent answer with only that task's tools.",
    inputs=tuple(UseCaseInput.__annotations__),
    outputs=tuple(UseCaseOutput.__annotations__),
    requires=("model",),
)

#: At most this many use cases are offered when the user is asked to choose.
_MAX_CANDIDATES = 3
#: Asking the user to choose needs at least this many use cases to choose from.
_MIN_CANDIDATES = 2
_ROUTE_RETURNED = (
    "route must return a mapping of use_case_id to text or None and candidates to a list of text"
)


def _clarify_threshold(count: int) -> int:
    """How many candidates make a message ambiguous among ``count`` use cases.

    With a single use case it is two, which one use case can never reach, so a
    graph of one use case never asks the user to choose.
    """
    return max(_MIN_CANDIDATES, min(_MAX_CANDIDATES, count))


def _classified(found: object, known: Mapping[str, UseCase]) -> tuple[str | None, list[str]]:
    """The use case ``route`` chose, if it is one of ``known``, and every one it named.

    The chosen one comes first among the named; a name that is not a use case
    is dropped, so is a repeat, and at most :data:`_MAX_CANDIDATES` are kept.
    """
    if not isinstance(found, Mapping):
        raise GraphError(_ROUTE_RETURNED)
    chosen = found.get("use_case_id")
    named = found.get("candidates") or []
    if not (chosen is None or isinstance(chosen, str)):
        raise GraphError(_ROUTE_RETURNED)
    if not isinstance(named, list | tuple) or not all(isinstance(n, str) for n in named):
        raise GraphError(_ROUTE_RETURNED)
    chosen = chosen if chosen in known else None
    ordered = ([chosen] if chosen is not None else []) + list(named)
    return chosen, list(dict.fromkeys(n for n in ordered if n in known))[:_MAX_CANDIDATES]


def _catalog(tools: Sequence[BaseTool], cases: Sequence[UseCase]) -> dict[str, BaseTool]:
    by_name: dict[str, BaseTool] = {}
    for tool in tools:
        if tool.name in by_name:
            raise ValueError(f"two tools are named {tool.name!r}")
        by_name[tool.name] = tool
    for case in cases:
        unknown = sorted(set(case.tools) - set(by_name))
        if unknown:
            raise ValueError(f"use case {case.id!r} names tools that were not passed: {unknown}")
    return by_name


def _cases(cases: Sequence[UseCase], extract: Extract | None) -> dict[str, UseCase]:
    if not cases:
        raise ValueError("cases must hold at least one use case")
    by_id: dict[str, UseCase] = {}
    for case in cases:
        if not isinstance(case, UseCase):
            raise TypeError("every use case must be a graphs.UseCase")
        if case.id in by_id:
            raise ValueError(f"two use cases have the id {case.id!r}")
        by_id[case.id] = case
    if extract is None and any(case.fields for case in cases):
        raise ValueError("extract is needed when a use case has fields")
    return by_id


def _middleware(middleware: Sequence[AgentMiddleware[Any, Any, Any]]) -> None:
    if any(getattr(each, "tools", None) for each in middleware):
        # The agent registers a middleware's tools, so every use case would hold them.
        raise ValueError(
            "middleware that brings tools of its own cannot be added to every use case"
        )


def _reply(result: Mapping[str, Any]) -> str:
    return str(result["messages"][-1].text)


def use_case(
    *,
    cases: Sequence[UseCase],
    model: str | BaseChatModel,
    tools: Sequence[BaseTool] = (),
    extract: Extract | None = None,
    check: Check | None = None,
    route: Route | None = None,
    middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    max_clarify_turns: int = 1,
    interrupt: bool = False,
) -> Graph:
    """Build a graph that serves a message with one of ``cases``.

    Use it when your application handles several distinct tasks, each with its
    own instructions, required details and allowed tools: it sends each message
    to one task, gathers that task's details and lets an agent answer with only
    that task's tools.

    Each run reads ``message`` and chooses a use case. With ``route``, your
    function names one (or none) and the others it considered; one of the
    ``cases`` that it does not name is never run. Without ``route``, the run
    takes the ``use_case_id`` it was given, which is how an agent of yours that
    has already chosen calls this graph.

    When the message could mean several use cases (as many candidates as there
    are use cases, two at least and three at most), the run ends with
    ``outcome="clarify"`` and the ``candidates`` for your reply to offer, at
    most ``max_clarify_turns`` turns in a row; after that, or when nothing
    fits, it ends with ``"no_match"`` and your fallback answers.

    The chosen use case's ``fields`` are collected by :func:`collect` with
    ``extract`` and ``check``, so they take the same closed set of shapes and
    a field outside them is checked by your ``check``; values are kept in
    ``collected`` under the use case's id; while any is
    missing the run ends with ``"missing"``, or, with ``interrupt=True``, pauses
    as :func:`collect` does. Then an agent built by ``create_agent`` with
    ``model``, the use case's ``instructions`` and only the ``tools`` it names
    answers within the use case's model-call and tool-call limits: ``"answered"``
    with its ``reply``, or ``"budget_exhausted"``. It receives the message and
    the collected values, as JSON, in the user's turn. ``middleware`` is added
    after the limits to every use case's agent, and may not bring tools.

    The limits count every call made while answering one turn, including the
    calls made after a pause inside the agent is resumed.
    """
    by_id = _cases(cases, extract)
    catalog = _catalog(tools, cases)
    _middleware(middleware)
    if max_clarify_turns < 0:
        raise ValueError("max_clarify_turns must not be negative")
    threshold = _clarify_threshold(len(by_id))
    collectors = {
        case.id: collect(fields=case.fields, extract=extract, check=check, interrupt=interrupt)
        for case in cases
        if case.fields and extract is not None
    }
    agents = {
        case.id: create_agent(
            model,
            tools=[catalog[name] for name in case.tools],
            system_prompt=case.instructions,
            # Thread limits, not run limits: a run's count is not saved, so it
            # would start again at zero when a pause inside the agent is resumed.
            # Each turn's agent starts on a thread of its own, so the count is
            # per turn.
            middleware=[
                ModelCallLimitMiddleware(thread_limit=case.max_model_calls, exit_behavior="error"),
                ToolCallLimitMiddleware(thread_limit=case.max_tool_calls, exit_behavior="continue"),
                *middleware,
            ],
            name=case.id,
        )
        for case in cases
    }

    async def choose(state: UseCaseState, call: Call) -> dict[str, Any]:
        active = state.get("use_case_id")
        # An agent calling this graph as a tool writes the count too.
        turns = max(0, state.get("clarify_turns") or 0)
        settled: dict[str, Any] = {
            "reply": None,
            "missing": [],
            "candidates": [],
            "collected": dict(state.get("collected") or {}),
            "clarify_turns": 0,
        }
        if route is None:
            if active is not None and active not in by_id:
                raise GraphError("use_case_id names no use case this graph was built with")
            ended = {} if active is not None else {"outcome": "no_match"}
            return {**settled, "use_case_id": active, **ended}
        choices = [{"id": case.id, "description": case.description} for case in cases]
        found = await call(route, state["message"], choices, active if active in by_id else None)
        chosen, named = _classified(found, by_id)
        if len(named) >= threshold:
            if turns < max_clarify_turns:
                return {
                    **settled,
                    "use_case_id": None,
                    "candidates": named,
                    "clarify_turns": turns + 1,
                    "outcome": "clarify",
                }
            chosen = None
        ended = {} if chosen is not None else {"outcome": "no_match"}
        return {**settled, "use_case_id": chosen, "candidates": named, **ended}

    def case_of(state: UseCaseState) -> UseCase:
        # Reached only after `route` chose a use case.
        return by_id[cast(str, state["use_case_id"])]

    def collect_input(state: UseCaseState) -> dict[str, Any]:
        held = state["collected"].get(case_of(state).id)
        return {"message": state["message"], "collected": held if isinstance(held, Mapping) else {}}

    def collect_output(state: UseCaseState, result: Mapping[str, Any]) -> dict[str, Any]:
        collected = {**state["collected"], case_of(state).id: result["collected"]}
        ended = {"outcome": "missing"} if result["missing"] else {}
        return {"collected": collected, "missing": result["missing"], **ended}

    def fill(state: UseCaseState, config: RunnableConfig) -> dict[str, Any]:
        collector = collectors.get(case_of(state).id)
        if collector is None:
            return {}
        return collect_output(state, collector.invoke(collect_input(state), config))

    async def afill(state: UseCaseState, config: RunnableConfig) -> dict[str, Any]:
        collector = collectors.get(case_of(state).id)
        if collector is None:
            return {}
        return collect_output(state, await collector.ainvoke(collect_input(state), config))

    def agent_input(state: UseCaseState) -> Any:
        case = case_of(state)
        content: list[str | dict[str, Any]] = [{"type": "text", "text": state["message"]}]
        if case.fields:
            held = state["collected"][case.id]
            values = {name: held[name] for name in case.fields if name in held}
            # The values came from the user, so they travel in the user's turn.
            content.append(
                {"type": "text", "text": json.dumps({"collected": values}, ensure_ascii=False)}
            )
        return {"messages": [HumanMessage(content=content)]}

    def answer(state: UseCaseState, config: RunnableConfig) -> dict[str, Any]:
        agent = agents[case_of(state).id]
        try:
            result = agent.invoke(agent_input(state), config)
        except ModelCallLimitExceededError:
            return {"outcome": "budget_exhausted", "reply": None}
        return {"outcome": "answered", "reply": _reply(result)}

    async def aanswer(state: UseCaseState, config: RunnableConfig) -> dict[str, Any]:
        agent = agents[case_of(state).id]
        try:
            result = await agent.ainvoke(agent_input(state), config)
        except ModelCallLimitExceededError:
            return {"outcome": "budget_exhausted", "reply": None}
        return {"outcome": "answered", "reply": _reply(result)}

    def after_route(state: UseCaseState) -> Literal["collect", "end"]:
        return "collect" if state["use_case_id"] is not None else "end"

    def after_collect(state: UseCaseState) -> Literal["answer", "end"]:
        return "end" if state["missing"] else "answer"

    builder = StateGraph(UseCaseState, input_schema=UseCaseInput, output_schema=UseCaseOutput)
    builder.add_node("route", node(choose))
    builder.add_node("collect", RunnableLambda(fill, afunc=afill, name="collect"))
    builder.add_node("answer", RunnableLambda(answer, afunc=aanswer, name="answer"))
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", after_route, {"collect": "collect", "end": END})
    builder.add_conditional_edges("collect", after_collect, {"answer": "answer", "end": END})
    builder.add_edge("answer", END)
    return builder.compile(name=INFO.name)

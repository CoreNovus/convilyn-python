"""The state each graph reads and writes, and the description of a graph.

State is plain data on purpose: strings, numbers, booleans, lists and
dictionaries. Whatever the host graph uses to save its progress can save these
without being taught a new type.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, NotRequired

from pydantic import BaseModel, ConfigDict, Field

# Not `typing.TypedDict`: below Python 3.12 pydantic refuses to build a schema
# from one, and every graph's input and output schema is built from these.
from typing_extensions import TypedDict


class GraphInfo(BaseModel):
    """What one graph is, as data."""

    model_config = ConfigDict(frozen=True)

    #: The name of the function that builds it.
    name: str
    summary: str
    #: The situation it is for and what it does about it, in one sentence that
    #: reads after "Use it when". Its builder's docstring and the table of
    #: graphs in the docs carry the same sentence.
    use_when: str = Field(min_length=1)
    #: State keys the graph reads from the host.
    inputs: tuple[str, ...]
    #: State keys the graph writes back.
    outputs: tuple[str, ...]
    #: The functions the caller supplies when building it.
    requires: tuple[str, ...]


class Document(TypedDict):
    """One retrieved passage."""

    text: str
    #: Where it came from, when the retriever said.
    source: str | None


class RefineInput(TypedDict):
    task: str


class RefineOutput(TypedDict):
    #: The last draft produced.
    result: str
    #: Whether the evaluator accepted ``result``.
    accepted: bool
    #: What the evaluator said about ``result``; ``None`` when it was accepted.
    feedback: str | None
    #: How many drafts were produced.
    rounds: int


class RefineState(RefineInput, RefineOutput):
    """Everything :func:`convilyn.graphs.refine` keeps while it runs."""


class CorrectiveRagInput(TypedDict):
    question: str


class CorrectiveRagOutput(TypedDict):
    answer: str
    #: The passages judged relevant, which the answer was written from.
    documents: list[Document]
    #: Whether any relevant passage was found.
    grounded: bool
    #: How many searches were run.
    attempts: int


class CorrectiveRagState(CorrectiveRagInput, CorrectiveRagOutput):
    """Everything :func:`convilyn.graphs.corrective_rag` keeps while it runs."""

    cv_corrective_rag_query: str
    cv_corrective_rag_found: list[Document]


class CollectInput(TypedDict):
    #: The user's latest message, as text.
    message: str
    #: What earlier turns collected, by field name. Absent on the first turn.
    collected: NotRequired[dict[str, Any]]


class CollectOutput(TypedDict):
    #: Everything collected so far: what came in, plus what this turn found.
    collected: dict[str, Any]
    #: The fields still without a value, in the order they were declared.
    missing: list[str]


class CollectState(CollectOutput):
    """Everything :func:`convilyn.graphs.collect` keeps while it runs.

    Not built on :class:`CollectInput` too: ``collected`` may be absent from the
    input but is always present in the output, and one type cannot say both.
    """

    message: str
    #: The answer a paused run was resumed with, until it has been read.
    cv_collect_message: str | None


class FanoutInput(TypedDict):
    question: str


class FanoutOutput(TypedDict):
    #: What ``synthesize`` wrote from the results.
    answer: str
    #: Each branch that ran, mapped to what it returned, in the order ``classify`` named them.
    results: dict[str, str]


class FanoutState(FanoutInput, FanoutOutput):
    """Everything :func:`convilyn.graphs.fanout` keeps while it runs."""

    #: The branches ``classify`` chose, each mapped to the text it is given.
    cv_fanout_plan: dict[str, str]
    #: What each branch returned, merged as they finish.
    cv_fanout_done: Annotated[dict[str, str], operator.or_]


class ExecutedStep(TypedDict):
    """One step of a plan that was carried out."""

    step: str
    #: What ``execute`` returned for it.
    result: str


class PlanExecuteInput(TypedDict):
    task: str


class PlanExecuteOutput(TypedDict):
    #: What ``replan`` answered; ``None`` when the steps ran out first.
    answer: str | None
    #: Every step carried out, in order, with its result.
    done: list[ExecutedStep]
    #: Whether ``replan`` answered before the steps ran out.
    answered: bool


class PlanExecuteState(PlanExecuteInput, PlanExecuteOutput):
    """Everything :func:`convilyn.graphs.plan_execute` keeps while it runs."""

    #: The steps still to carry out, in order.
    cv_plan_execute_remaining: list[str]


class UseCase(BaseModel):
    """One task your application handles, as data: load it with ``model_validate``.

    The tools it may use are named here and passed as objects when the graph is
    built, so a file of use cases holds no code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    #: What a router reads to tell this use case from the others.
    description: str
    #: The agent's instructions while it works on this use case.
    instructions: str
    #: Each required field's name mapped to its JSON Schema, in the order to ask for
    #: them; the schema is one of the shapes :func:`convilyn.graphs.collect` admits.
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: The names of the tools the agent may call for this use case.
    tools: tuple[str, ...] = ()
    #: How many times the agent may call the model in one run.
    max_model_calls: int = Field(ge=1, le=8)
    #: How many tool calls the agent may make in one run.
    max_tool_calls: int = Field(ge=0, le=8)


class UseCaseInput(TypedDict):
    #: The user's latest message, as text.
    message: str
    #: The use case in progress: what a router is told, or, with no router, the one to run.
    use_case_id: NotRequired[str | None]
    #: What earlier turns collected, by use case and then by field name.
    collected: NotRequired[dict[str, dict[str, Any]]]
    #: How many turns in a row ended asking the user to choose.
    clarify_turns: NotRequired[int]


class UseCaseOutput(TypedDict):
    #: How the run ended: ``answered``; ``missing`` (fields to ask for); ``no_match``
    #: (no use case fits, so your fallback answers); ``clarify`` (ask the user to
    #: choose among ``candidates``); ``budget_exhausted`` (the agent ran out of model calls).
    outcome: Literal["answered", "missing", "no_match", "clarify", "budget_exhausted"]
    #: The agent's answer when ``outcome`` is ``answered``; ``None`` otherwise.
    reply: str | None
    #: The use case this turn ran, or ``None`` when none was chosen.
    use_case_id: str | None
    #: With ``clarify``, the use cases to offer the user; otherwise what the router also named.
    candidates: list[str]
    #: Everything collected so far, by use case and then by field name.
    collected: dict[str, dict[str, Any]]
    #: The chosen use case's fields still without a value, in the order they were declared.
    missing: list[str]
    clarify_turns: int


class UseCaseState(UseCaseOutput):
    """Everything :func:`convilyn.graphs.use_case` keeps while it runs.

    Not built on :class:`UseCaseInput` too: ``collected`` may be absent from the
    input but is always present in the output, and one type cannot say both.
    """

    message: str


__all__ = [
    "CollectInput",
    "CollectOutput",
    "CollectState",
    "CorrectiveRagInput",
    "CorrectiveRagOutput",
    "CorrectiveRagState",
    "Document",
    "ExecutedStep",
    "FanoutInput",
    "FanoutOutput",
    "FanoutState",
    "GraphInfo",
    "PlanExecuteInput",
    "PlanExecuteOutput",
    "PlanExecuteState",
    "RefineInput",
    "RefineOutput",
    "RefineState",
    "UseCase",
    "UseCaseInput",
    "UseCaseOutput",
    "UseCaseState",
]

"""Place a ready-made graph inside your own LangGraph application, three ways.

Needs ``pip install "convilyn[graphs]"`` and nothing else: no key, no account,
no network. The "model" here is plain functions and a scripted stand-in, so the
example runs as it is; replace them with calls to the model you use.

``graphs.refine`` drafts, checks the draft, and redoes it with the feedback
until the check passes. ``graphs.use_case`` chooses which of your tasks a
message is about, gathers its fields and lets an agent act with only that
task's tools. ``graphs.fanout`` asks the sources a question needs at the same
time and combines what they say. ``graphs.plan_execute`` (last) plans a task,
carries it out a step at a time and answers when the plan is done.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from convilyn import graphs


def write_draft(task: str, previous: str | None, feedback: str | None) -> str:
    """Stand-in for a model call. ``previous`` and ``feedback`` are None at first."""
    return f"{task}." if previous is None else f"{previous} Thank you."


def check_draft(task: str, draft: str) -> str | None:
    """Return what to fix, or None to accept. This need not be a model."""
    return None if "Thank you" in draft else "End by thanking the reader."


improve = graphs.refine(generate=write_draft, evaluate=check_draft)


def as_a_node_with_matching_keys() -> dict:
    """Your state already has ``task`` and ``result``: add the graph directly."""

    class State(TypedDict, total=False):
        task: str
        result: str
        customer: str  # yours; the graph never touches it

    builder = StateGraph(State)
    builder.add_node("improve", improve)
    builder.add_edge(START, "improve")
    builder.add_edge("improve", END)
    # The saved-state backend is yours. The graph brought none and uses this one.
    app = builder.compile(checkpointer=InMemorySaver())

    return app.invoke(
        {"task": "Confirm the refund", "customer": "A-17"},
        {"configurable": {"thread_id": "example"}},
    )


def as_a_node_with_your_own_keys() -> dict:
    """Your state calls them ``request`` and ``reply``: rename with ``as_node``."""

    class State(TypedDict, total=False):
        request: str
        reply: str

    builder = StateGraph(State)
    builder.add_node(
        "improve",
        graphs.as_node(improve, inputs={"task": "request"}, outputs={"result": "reply"}),
    )
    builder.add_edge(START, "improve")
    builder.add_edge("improve", END)

    return builder.compile().invoke({"request": "Confirm the refund"})


def as_a_tool_for_an_agent() -> str:
    """An agent decides when to call it. Here the tool call is written by hand."""
    tool = graphs.as_tool(improve, name="write_reply", description="Write and check a reply.")

    class Chat(TypedDict):
        messages: list

    builder = StateGraph(Chat)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    call = AIMessage(
        content="",
        tool_calls=[{"name": "write_reply", "args": {"task": "Confirm the refund"}, "id": "1"}],
    )

    return builder.compile().invoke({"messages": [call]})["messages"][-1].content


# Plain data: in your application, load these from a file with model_validate.
CASES = [
    {
        "id": "refund",
        "description": "Refund an order.",
        "instructions": "Start the refund for the order the user gives, then confirm it.",
        "fields": {"order_id": {"type": "string", "maxLength": 16, "pattern": "^A-[0-9]+$"}},
        "tools": ["refund"],
        "max_model_calls": 3,
        "max_tool_calls": 1,
    },
    {
        "id": "address",
        "description": "Change the delivery address.",
        "instructions": "Say that address changes are made on the account page.",
        "max_model_calls": 1,
        "max_tool_calls": 0,
    },
]


@tool
def refund(order_id: str) -> str:
    """Start the refund of an order."""
    return f"Refund of {order_id} started."


def choose_case(message: str, choices: list[dict[str, str]], active: str | None) -> dict:
    """Stand-in for your router. With a model, ask it in your own words which of
    ``choices`` the message is about, through its structured output."""
    named = [choice["id"] for choice in choices if choice["id"] in message.lower()]
    return {"use_case_id": named[0] if len(named) == 1 else active, "candidates": named}


def read_fields(message: str, schema: dict) -> dict:
    """Stand-in for your structured-output call, as in ``graphs.collect``."""
    return {"order_id": next((word for word in message.split() if word.startswith("A-")), None)}


class ScriptedModel(GenericFakeChatModel):
    """Stand-in for your chat model: replays the answers it was given."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


def a_use_case_turn() -> dict:
    """One turn of a conversation about one of your tasks."""
    model = ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "refund", "args": {"order_id": "A-17"}, "id": "1"}],
                ),
                AIMessage(content="Your refund for A-17 has started."),
            ]
        )
    )
    serve = graphs.use_case(
        cases=[graphs.UseCase.model_validate(case) for case in CASES],
        model=model,
        tools=[refund],
        extract=read_fields,
        route=choose_case,
    )
    return serve.invoke({"message": "Please refund order A-17"})


def search_docs(text: str) -> str:
    """Stand-in for a search of your help pages."""
    return "Refunds take five working days."


def search_orders(text: str) -> str:
    """Stand-in for a lookup in your order system."""
    return "A-17 was refunded yesterday."


def choose_sources(question: str, names: list[str]) -> dict[str, str]:
    """Stand-in for your classifier. With a model, ask it in your own words which
    of ``names`` the question needs, through its structured output."""
    return {name: question for name in names if name != "orders" or "A-" in question}


def write_answer(question: str, results: dict[str, str]) -> str:
    """Stand-in for the model call that writes one answer from every result."""
    return " ".join(results.values())


def a_fanout_question() -> dict:
    """Ask the sources a question needs, all at once, and combine what they say."""
    ask_around = graphs.fanout(
        branches={"docs": search_docs, "orders": search_orders},
        classify=choose_sources,
        synthesize=write_answer,
    )
    return ask_around.invoke({"question": "Where is the refund for A-17?"})


def write_plan(task: str) -> list[str]:
    """Stand-in for the model call that plans: ask it in your own words for the steps."""
    return ["look up the order", "check the refund policy"]


def carry_out_step(task: str, step: str, done: list[dict[str, str]]) -> str:
    """Stand-in for whatever carries out one step: a cheaper model, a tool or a graph."""
    found = {"look up the order": search_orders, "check the refund policy": search_docs}
    return found[step](task)


def review_progress(task: str, done: list[dict[str, str]], remaining: list[str]) -> dict:
    """Keep the plan while steps remain; then write the answer from every result."""
    if remaining:
        return {"steps": remaining}
    return {"answer": " ".join(each["result"] for each in done)}


def a_planned_task() -> dict:
    """Plan a task, carry it out a step at a time, and answer when the plan is done."""
    solve = graphs.plan_execute(
        plan=write_plan, execute=carry_out_step, replan=review_progress, max_steps=4
    )
    return solve.invoke({"task": "Has the refund for A-17 been paid, and when will it arrive?"})


def main() -> None:
    for info in graphs.available():
        keys = f"Reads {info.inputs}, writes {info.outputs}."
        print(f"{info.name}: use it when {info.use_when} {keys}")
    print(as_a_node_with_matching_keys())
    print(as_a_node_with_your_own_keys())
    print(as_a_tool_for_an_agent())
    print(a_use_case_turn())
    print(a_fanout_question())
    print(a_planned_task())


if __name__ == "__main__":
    main()

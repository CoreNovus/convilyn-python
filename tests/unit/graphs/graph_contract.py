"""What every graph must do, whichever one it is.

Subclass :class:`GraphContract` in a graph's own test module, set ``NAME`` and
provide three fixtures: ``graph`` (built from plain functions), ``payload`` (an
input) and ``expected`` (what that input produces). A graph that can pause for
an answer also provides ``pause``. Every test here then runs against that
graph. Passing is what "does not disturb the host" means: the graph runs inside
a host with or without saved state, leaves the host's other keys alone, and
works as a node, as a renamed node, as a tool and when a host's own step calls
it; one that pauses resumes in each of those places.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from convilyn import graphs

Graph = Any
THREAD = {"configurable": {"thread_id": "contract"}}


class Pause(NamedTuple):
    """An input on which the graph pauses, and how the run ends once answered."""

    #: An input that makes the graph pause.
    payload: dict[str, Any]
    #: The value it pauses with.
    asked: Any
    #: The value the host resumes it with.
    answer: Any
    #: The outputs once resumed with ``answer``.
    expected: dict[str, Any]


def resume(answer: Any) -> Command[Any]:
    """What a host passes to continue a paused run."""
    return Command(resume=answer)


def pending(host: Graph, config: Any = THREAD) -> list[Any]:
    """The values a host's paused run is waiting on."""
    return [waiting.value for waiting in host.get_state(config).interrupts]


def host_around(node: Any, keys: tuple[str, ...], *, saved: bool) -> Graph:
    """A host graph whose only step is ``node``, plus one key of its own."""
    state = TypedDict("Host", {**{key: Any for key in keys}, "untouched": str}, total=False)  # type: ignore[misc]
    builder = StateGraph(state)
    builder.add_node("step", node)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    return builder.compile(checkpointer=InMemorySaver() if saved else None)


def prefixed(mapping: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in mapping.items()}


class GraphContract:
    """Tests every graph in :mod:`convilyn.graphs` passes."""

    NAME: str

    @property
    def info(self) -> graphs.GraphInfo:
        return next(info for info in graphs.available() if info.name == self.NAME)

    @pytest.fixture
    def pause(self) -> Pause | None:
        """``None``: this graph never pauses. Override for one that does."""
        return None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.info.inputs + self.info.outputs))

    def direct_host(self, graph: Graph, *, saved: bool) -> Graph:
        return host_around(graph, self.keys, saved=saved)

    def stepping_host(self, graph: Graph) -> Graph:
        """A host whose own step calls the graph, as a host written by hand would."""
        inputs = self.info.inputs

        def step(state: dict[str, Any]) -> dict[str, Any]:
            return graph.invoke({key: state[key] for key in inputs if key in state})

        return host_around(step, self.keys, saved=True)

    # ── what it is ───────────────────────────────────────────────────────

    def test_it_carries_no_saved_state_backend_of_its_own(self, graph: Graph) -> None:
        assert graph.checkpointer is None

    def test_it_carries_no_store_of_its_own(self, graph: Graph) -> None:
        assert graph.store is None

    # ── what it is for ───────────────────────────────────────────────────

    def test_its_builder_docstring_says_when_to_use_it_in_the_same_words(self) -> None:
        """What IDE hover shows is the sentence ``available()`` returns, word for word."""
        docstring = " ".join((getattr(graphs, self.NAME).__doc__ or "").split())

        assert f"Use it when {self.info.use_when}" in docstring

    def test_its_description_lists_the_inputs_it_really_reads(self, graph: Graph) -> None:
        declared = tuple(graph.get_input_jsonschema()["properties"])

        assert self.info.inputs == declared

    def test_its_description_lists_the_outputs_it_really_writes(self, graph: Graph) -> None:
        declared = tuple(graph.get_output_jsonschema()["properties"])

        assert self.info.outputs == declared

    # ── on its own ───────────────────────────────────────────────────────

    def test_it_runs_on_its_own(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        result = graph.invoke(payload)

        assert result == expected

    async def test_it_runs_the_same_under_the_async_entry_point(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        result = await graph.ainvoke(payload)

        assert result == expected

    def test_its_output_is_plain_data(self, graph: Graph, payload: dict[str, Any]) -> None:
        result = graph.invoke(payload)

        assert json.loads(json.dumps(result)) == result

    # ── as a node ────────────────────────────────────────────────────────

    def test_as_a_node_it_runs_in_a_host_that_saves_state(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = self.direct_host(graph, saved=True)

        result = host.invoke({**payload, "untouched": "mine"}, THREAD)

        assert result == {**payload, **expected, "untouched": "mine"}

    def test_as_a_node_it_runs_in_a_host_that_saves_nothing(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = self.direct_host(graph, saved=False)

        result = host.invoke({**payload, "untouched": "mine"})

        assert result == {**payload, **expected, "untouched": "mine"}

    def test_the_host_s_saved_state_holds_what_the_run_returned(
        self, graph: Graph, payload: dict[str, Any]
    ) -> None:
        host = self.direct_host(graph, saved=True)
        result = host.invoke({**payload, "untouched": "mine"}, THREAD)

        saved = host.get_state(THREAD).values

        assert saved == result

    def test_a_second_run_on_the_same_thread_starts_fresh(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = self.direct_host(graph, saved=True)
        host.invoke({**payload, "untouched": "mine"}, THREAD)

        result = host.invoke(payload, THREAD)

        assert result == {**payload, **expected, "untouched": "mine"}

    def test_the_host_can_stream_what_happens_inside(
        self, graph: Graph, payload: dict[str, Any]
    ) -> None:
        host = self.direct_host(graph, saved=True)

        namespaces = {
            ns[0].split(":")[0] for ns, _ in host.stream(payload, THREAD, subgraphs=True) if ns
        }

        assert namespaces == {"step"}

    # ── called by a host's own step ──────────────────────────────────────

    def test_it_runs_when_a_host_step_calls_it(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = self.stepping_host(graph)

        result = host.invoke({**payload, "untouched": "mine"}, THREAD)

        assert result == {**payload, **expected, "untouched": "mine"}

    # ── pausing for an answer ────────────────────────────────────────────

    def test_it_pauses_only_where_it_says_it_will(
        self, graph: Graph, payload: dict[str, Any], pause: Pause | None
    ) -> None:
        host = self.direct_host(graph, saved=True)

        host.invoke(pause.payload if pause else payload, THREAD)

        assert pending(host) == ([pause.asked] if pause else [])

    def test_a_paused_run_resumes_in_a_host_that_saves_state(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        host = self.direct_host(graph, saved=True)
        host.invoke({**paused.payload, "untouched": "mine"}, THREAD)

        result = host.invoke(resume(paused.answer), THREAD)

        assert result == {**paused.payload, **paused.expected, "untouched": "mine"}

    async def test_a_paused_run_resumes_under_the_async_entry_point(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        host = self.direct_host(graph, saved=True)
        await host.ainvoke(paused.payload, THREAD)

        result = await host.ainvoke(resume(paused.answer), THREAD)

        assert result == {**paused.payload, **paused.expected}

    def test_a_paused_run_resumes_when_a_host_step_calls_it(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        host = self.stepping_host(graph)
        host.invoke({**paused.payload, "untouched": "mine"}, THREAD)

        result = host.invoke(resume(paused.answer), THREAD)

        assert result == {**paused.payload, **paused.expected, "untouched": "mine"}

    def test_a_paused_run_resumes_under_the_host_s_own_key_names(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        renamed = {key: f"my_{key}" for key in self.keys}
        node = graphs.as_node(
            graph,
            inputs=renamed_only(renamed, self.info.inputs),
            outputs=renamed_only(renamed, self.info.outputs),
        )
        host = host_around(node, tuple(renamed.values()), saved=True)
        host.invoke(prefixed(paused.payload, "my_"), THREAD)

        result = host.invoke(resume(paused.answer), THREAD)

        assert result == prefixed({**paused.payload, **paused.expected}, "my_")

    def test_two_paused_copies_resume_without_mixing(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        host = self.pair_host(graph)
        host.invoke({**prefixed(paused.payload, "a_"), **prefixed(paused.payload, "b_")}, THREAD)
        waiting = host.get_state(THREAD).interrupts

        result = host.invoke(Command(resume={w.id: paused.answer for w in waiting}), THREAD)

        both = {**paused.payload, **paused.expected}
        assert result == {**prefixed(both, "a_"), **prefixed(both, "b_")}

    def test_a_paused_tool_call_resumes_and_returns_the_outputs(
        self, graph: Graph, pause: Pause | None
    ) -> None:
        paused = needed(pause)
        host = tool_host(graphs.as_tool(graph, name="helper", description="Does the thing."))
        call = AIMessage(
            content="", tool_calls=[{"name": "helper", "args": paused.payload, "id": "call-1"}]
        )
        host.invoke({"messages": [call]}, THREAD)

        reply = host.invoke(resume(paused.answer), THREAD)["messages"][-1]

        assert json.loads(reply.content) == paused.expected

    # ── as a renamed node ────────────────────────────────────────────────

    def test_as_node_reads_and_writes_the_host_s_own_key_names(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        renamed = {key: f"my_{key}" for key in self.info.inputs + self.info.outputs}
        node = graphs.as_node(
            graph,
            inputs=renamed_only(renamed, self.info.inputs),
            outputs=renamed_only(renamed, self.info.outputs),
        )
        host = host_around(node, tuple(renamed.values()), saved=True)

        result = host.invoke({**prefixed(payload, "my_"), "untouched": "mine"}, THREAD)

        assert result == {**prefixed({**payload, **expected}, "my_"), "untouched": "mine"}

    def pair_host(self, graph: Graph) -> Graph:
        """A host running two renamed copies of the graph in the same step."""
        state = TypedDict(
            "Pair", {f"{side}_{key}": Any for side in "ab" for key in self.keys}, total=False
        )  # type: ignore[misc]
        builder = StateGraph(state)
        for side in "ab":
            renamed = {key: f"{side}_{key}" for key in self.keys}
            builder.add_node(
                side,
                graphs.as_node(
                    graph,
                    inputs=renamed_only(renamed, self.info.inputs),
                    outputs=renamed_only(renamed, self.info.outputs),
                ),
            )
            builder.add_edge(START, side)
            builder.add_edge(side, END)
        return builder.compile(checkpointer=InMemorySaver())

    def test_two_copies_run_side_by_side_without_mixing(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = self.pair_host(graph)
        both = {**payload, **expected}

        result = host.invoke({**prefixed(payload, "a_"), **prefixed(payload, "b_")}, THREAD)

        assert result == {**prefixed(both, "a_"), **prefixed(both, "b_")}

    # ── as a tool ────────────────────────────────────────────────────────

    def test_as_tool_takes_the_graph_s_inputs_as_its_arguments(self, graph: Graph) -> None:
        tool = graphs.as_tool(graph, name="helper", description="Does the thing.")

        assert tuple(tool.args) == self.info.inputs

    def test_an_agent_s_tool_call_gets_the_outputs_back_as_json(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        host = tool_host(graphs.as_tool(graph, name="helper", description="Does the thing."))
        call = AIMessage(
            content="", tool_calls=[{"name": "helper", "args": payload, "id": "call-1"}]
        )

        reply = host.invoke({"messages": [call]}, THREAD)["messages"][-1]

        assert json.loads(reply.content) == expected

    async def test_the_tool_runs_the_same_under_the_async_entry_point(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        tool = graphs.as_tool(graph, name="helper", description="Does the thing.")

        reply = await tool.ainvoke(payload)

        assert json.loads(reply) == expected

    # ── what an agent, whose arguments nobody wrote by hand, can pass ─────

    def test_an_argument_of_the_wrong_type_never_reaches_the_graph(
        self, graph: Graph, payload: dict[str, Any]
    ) -> None:
        tool = graphs.as_tool(graph, name="helper", description="Does the thing.")
        wrong = {key: {"nested": [1, 2]} for key in payload}

        with pytest.raises(ValueError, match="the tool's arguments do not match its inputs"):
            tool.invoke(wrong)

    def test_an_argument_the_graph_does_not_declare_is_ignored(
        self, graph: Graph, payload: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        tool = graphs.as_tool(graph, name="helper", description="Does the thing.")
        smuggled = {key: "injected" for key in expected if key not in self.info.inputs}

        reply = tool.invoke({**payload, **smuggled, "config": {"recursion_limit": 1}})

        assert json.loads(reply) == expected


def saving(graph: Graph) -> Graph:
    """The same graph given a saved-state backend of its own, as a caller could."""
    return graph.copy(update={"checkpointer": InMemorySaver()})


def needed(pause: Pause | None) -> Pause:
    if pause is None:
        pytest.skip("this graph never pauses")
    return pause


def tool_host(tool: Any) -> Graph:
    """A host that saves state and runs the tool calls in its last message."""
    builder = StateGraph(TypedDict("Chat", {"messages": list}))  # type: ignore[misc]
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=InMemorySaver())


def graph_of(state: Any, step: Any) -> Graph:
    """A one-step graph of the caller's own, for testing the wrappers."""
    builder = StateGraph(state)
    builder.add_node("step", step)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    return builder.compile()


def echo_graph(state: Any) -> Graph:
    """A graph of the caller's own that returns its input."""
    return graph_of(state, lambda values: values)


def renamed_only(renamed: dict[str, str], keys: tuple[str, ...]) -> dict[str, str]:
    return {key: renamed[key] for key in keys}


# ── for a graph that runs an agent: a model, tools and middleware as plain data ──

_ROLES = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}


def _turn(message: BaseMessage) -> dict[str, Any]:
    content = message.content
    blocks = [content] if isinstance(content, str) else content
    texts = [block if isinstance(block, str) else block["text"] for block in blocks]
    return {"role": _ROLES[message.type], "texts": texts}


class _ScriptedModel(BaseChatModel):
    respond: Any
    #: ``Any``, not a dictionary type: a dictionary field would be copied on the way in.
    seen: Any
    bound: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **_: Any) -> Any:
        return self.model_copy(update={"bound": tuple(tool.name for tool in tools)})

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **_: Any
    ) -> ChatResult:
        turns = [_turn(message) for message in messages]
        self.seen["tools"] = list(self.bound)
        self.seen.setdefault("calls", []).append(turns)
        reply = self.respond(turns, list(self.bound))
        if isinstance(reply, str):
            message = AIMessage(content=reply)
        else:
            call = {"name": reply["name"], "args": reply["args"], "id": f"call-{len(messages)}"}
            message = AIMessage(content="", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=message)])


def scripted_model(respond: Any, seen: dict[str, Any] | None = None) -> Any:
    """A chat model whose every answer comes from ``respond(turns, tools)``.

    ``turns`` is the conversation so far as ``{"role", "texts"}`` dictionaries
    (``texts`` holds each text block of a message); ``tools`` names the tools
    bound to the call. ``respond`` returns the answer as text, or
    ``{"name": ..., "args": {...}}`` to call a tool. ``seen`` records the bound
    tools of the last call and every call's turns.
    """
    return _ScriptedModel(respond=respond, seen={} if seen is None else seen)


def tool_of(function: Any) -> BaseTool:
    """``function`` as a tool named after it, with its docstring as the description."""
    return StructuredTool.from_function(function)


def pausing_tool(name: str) -> BaseTool:
    """A tool that pauses with ``{"confirm": <its argument>}`` and returns the answer."""

    def confirm(value: str) -> str:
        return f"answer: {interrupt({'confirm': value})}"

    return StructuredTool.from_function(confirm, name=name, description="Ask the user to confirm.")


def pausing_function(asked: Any, ran: list[str]) -> Any:
    """A caller's function that pauses with ``asked`` and returns the answer.

    Appends its argument to ``ran`` each time it starts, before pausing.
    """

    def ask(value: str) -> str:
        ran.append(value)
        return f"answer: {interrupt(asked)}"

    return ask


class _Recording(AgentMiddleware[Any, Any, Any]):
    def __init__(self, seen: list[int]) -> None:
        super().__init__()
        self._seen = seen

    def before_model(self, state: Any, runtime: Any) -> None:
        self._seen.append(len(state["messages"]))


def recording_middleware(seen: list[int]) -> Any:
    """Middleware that appends the message count to ``seen`` before every model call."""
    return _Recording(seen)


class _Equipped(AgentMiddleware[Any, Any, Any]):
    def __init__(self, tool: BaseTool) -> None:
        super().__init__()
        self.tools = [tool]


def tool_bringing_middleware(tool: BaseTool) -> Any:
    """Middleware that hands the agent ``tool``, as some middleware does."""
    return _Equipped(tool)

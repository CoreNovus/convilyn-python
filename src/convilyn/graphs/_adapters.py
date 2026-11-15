"""Two ways to place a graph in a host besides adding it as a node directly.

Both work on any compiled graph, not only the ones this package builds.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, RootModel, TypeAdapter, ValidationError

Graph = CompiledStateGraph[Any, Any, Any, Any]

_ANY = TypeAdapter(Any)


def _jsonable(value: Any) -> Any:
    """``value`` in JSON form; raises for a type that has none."""
    return _ANY.dump_python(value, mode="json")


def _sent(valid: BaseModel, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The checked values of the keys that were sent, as the objects they were checked into.

    Not a dump: that would flatten a nested model into a dictionary and write a
    default the agent never asked for over the graph's own. A state declared as
    a dictionary type comes back as one; a model or a dataclass yields only the
    fields that were sent, read by the names the state declares, never by a
    name the agent wrote.
    """
    if not isinstance(valid, RootModel):
        return {key: getattr(valid, key) for key in valid.model_fields_set}
    root = valid.root
    if isinstance(root, Mapping):
        return dict(root)
    return {
        field.name: getattr(root, field.name)
        for field in dataclasses.fields(root)
        if field.name in arguments
    }


def _keys(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema.get("properties", {}))


def _renames(graph_keys: tuple[str, ...], renames: Mapping[str, str], side: str) -> dict[str, str]:
    unknown = sorted(set(renames) - set(graph_keys))
    if unknown:
        known = ", ".join(sorted(graph_keys))
        raise ValueError(f"{side} names keys this graph does not have: {unknown}; it has: {known}")
    return {key: renames.get(key, key) for key in graph_keys}


def as_node(
    graph: Graph,
    *,
    inputs: Mapping[str, str] | None = None,
    outputs: Mapping[str, str] | None = None,
) -> RunnableLambda[Any, dict[str, Any]]:
    """Wrap ``graph`` as a node for a host whose state keys are named differently.

    ``inputs`` and ``outputs`` map the graph's key to the host's:
    ``as_node(g, inputs={"task": "request"}, outputs={"result": "reply"})``
    reads the host's ``request`` and writes the host's ``reply``. Keys left out
    keep their name. Only the graph's declared outputs are written back, so
    nothing else in the host's state is touched.
    """
    reads = _renames(_keys(graph.get_input_jsonschema()), inputs or {}, "inputs")
    writes = _renames(_keys(graph.get_output_jsonschema()), outputs or {}, "outputs")

    def given(state: Mapping[str, Any]) -> dict[str, Any]:
        return {key: state[host] for key, host in reads.items() if host in state}

    def returned(result: Mapping[str, Any]) -> dict[str, Any]:
        return {host: result[key] for key, host in writes.items() if key in result}

    def run(state: Mapping[str, Any], config: RunnableConfig) -> dict[str, Any]:
        return returned(graph.invoke(given(state), config))

    async def arun(state: Mapping[str, Any], config: RunnableConfig) -> dict[str, Any]:
        return returned(await graph.ainvoke(given(state), config))

    return RunnableLambda(run, afunc=arun, name=graph.name)


def as_tool(graph: Graph, *, name: str, description: str) -> BaseTool:
    """Wrap ``graph`` as a tool an agent can call.

    The tool's arguments are the graph's inputs, checked against their types
    before the graph runs, and it returns the graph's outputs as JSON text.
    Nothing the graph kept while running is visible to the agent afterwards;
    what should persist belongs in the agent's own state.

    The agent can set every key in the graph's input schema. The graphs built
    here declare one; for a graph of your own, compile it with ``input_schema=``
    or every state key is an argument the agent may fill in.
    """

    inputs = graph.get_input_schema()

    def report(result: Mapping[str, Any]) -> str:
        # Dates, enums, identifiers and models are written as JSON; a type with
        # no JSON form raises rather than being written as whatever its text
        # happens to be, which could be anything the object holds.
        return json.dumps(_jsonable(result), ensure_ascii=False)

    def checked(arguments: dict[str, Any]) -> dict[str, Any]:
        # The framework does not check arguments against a schema given as a
        # dictionary, and an agent's arguments were written by a model. Checked
        # here, strictly and as JSON, which is the form a model writes them in:
        # a date arrives as text and is accepted as a date, while the text
        # "false" is not accepted as a boolean. The graph receives the checked
        # values, without keys its inputs do not declare.
        try:
            written = json.dumps(_jsonable(arguments))
            valid = inputs.model_validate_json(written, strict=True)
        except ValidationError as invalid:
            # The field and what was wrong, never a value or a key the model
            # wrote. Raised below, outside this block, so the error carries no
            # trace of the model's text either.
            wrong = sorted({f"{error['loc'][0]} ({error['type']})" for error in invalid.errors()})
        else:
            return _sent(valid, arguments)
        raise ValueError(f"the tool's arguments do not match its inputs: {', '.join(wrong)}")

    def run(config: RunnableConfig, **arguments: Any) -> str:
        return report(graph.invoke(checked(arguments), config))

    async def arun(config: RunnableConfig, **arguments: Any) -> str:
        return report(await graph.ainvoke(checked(arguments), config))

    return StructuredTool.from_function(
        func=run,
        coroutine=arun,
        name=name,
        description=description,
        args_schema=graph.get_input_jsonschema(),
        infer_schema=False,
    )

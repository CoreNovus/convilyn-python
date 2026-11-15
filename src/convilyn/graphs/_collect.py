"""Collect a set of fields from what the user says, across as many turns as it takes."""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt as _pause

from convilyn.graphs import _schema_limits as limits
from convilyn.graphs._nodes import Call, node
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import CollectInput, CollectOutput, CollectState, GraphInfo

Found = Mapping[str, Any]
#: ``(message, schema)`` to the values the message gives. ``schema`` is a JSON
#: Schema object whose properties are the fields still missing; a field the
#: message does not give is left out or ``None``.
Extract = Callable[[str, dict[str, Any]], Found | Awaitable[Found]]
#: ``(field, value)`` to whether to keep a value its field's schema accepted.
Check = Callable[[str, Any], bool | Awaitable[bool]]

INFO = GraphInfo(
    name="collect",
    summary="Collect required fields from the user's messages and report what is still missing.",
    use_when="you need certain details from the user before acting, and they may arrive over "
    "several messages: it keeps each valid value found so far and reports which details are "
    "still missing.",
    inputs=tuple(CollectInput.__annotations__),
    outputs=tuple(CollectOutput.__annotations__),
    requires=("extract",),
)

_MESSAGE = "cv_collect_message"


def _validators(fields: Mapping[str, Mapping[str, Any]]) -> dict[str, Draft202012Validator]:
    if not fields:
        raise ValueError("fields must name at least one field")
    if not all(isinstance(name, str) for name in fields):
        raise ValueError("every field name must be a string")
    validators: dict[str, Draft202012Validator] = {}
    for name, schema in fields.items():
        try:
            validators[name] = limits.admit(schema)
        except limits.RefusedError as refused:
            raise ValueError(
                f"the schema of field {name!r} is refused: {refused.reason}; {limits.WAY_OUT}"
            ) from None
    return validators


def collect(
    *,
    fields: Mapping[str, Mapping[str, Any]],
    extract: Extract,
    check: Check | None = None,
    interrupt: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a graph that fills ``fields`` from the user's messages.

    Use it when you need certain details from the user before acting, and they
    may arrive over several messages: it keeps each valid value found so far
    and reports which details are still missing.

    ``fields`` maps each field's name to its JSON Schema, in the order to ask
    for them; a ``description`` in the schema is the place to say what it means.
    Each run reads ``message`` and what is already ``collected``, asks
    ``extract`` for the fields still missing, and writes back ``collected`` with
    whatever valid values it found added, and ``missing``. A valid value already
    collected is never replaced. A value that does not match its field's schema
    is not kept, whether ``extract`` found it or it came in ``collected``, so
    that field is missing; an empty ``missing`` means every field holds a valid
    value. Keys in ``collected`` that are not fields are your state, and are
    carried through untouched.

    **A field's schema is one of a closed set of shapes**, so that how long a
    check takes is decided by the schema and not by the value a model or an
    agent wrote: a scalar ``type`` with its own keywords, ``enum`` or
    ``const``, an ``array`` of one scalar shape with ``maxItems``, an
    ``object`` of scalar ``properties``, or ``true``; a ``pattern`` needs a
    ``maxLength`` of at most 256. Any other keyword is
    refused when the graph is built, with a ``ValueError`` naming the field and
    the keyword. A value is checked only within 256 parts, 64 levels and
    16 KB as JSON; checking it is one pass over its parts.

    A field the shapes cannot express takes the schema ``true`` and is checked
    by ``check(field, value)``, your own code, which returns ``True`` to keep
    the value and ``False`` to drop it. It runs on every value its field's
    schema has accepted, whether ``extract`` found it or it came in
    ``collected``: ``value`` is plain JSON within the limits above. It runs
    every time the graph runs, so a value kept in an earlier turn, or before a
    pause, is checked again; an exception it raises ends the run. A ``true``
    field with no ``check`` is unchecked, by your choice.

    By default the run then ends, and your reply asks for what is missing; the
    next turn's message continues from ``collected``. With ``interrupt=True``
    the graph instead pauses with ``{"missing": [...]}`` until it is resumed
    with the user's answer as text, and repeats until nothing is missing. That
    needs a host compiled with a saved-state backend.
    """
    validators = _validators(fields)
    names = tuple(validators)

    def schema_of(wanted: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {name: copy.deepcopy(validators[name].schema) for name in wanted},
            "additionalProperties": False,
        }

    async def kept(name: str, plain: Any, call: Call) -> bool:
        """Whether a value, in JSON form, passes its field's shape and then ``check``."""
        if not validators[name].is_valid(plain):
            return False
        if check is None:
            return True
        verdict = await call(check, name, plain)
        if not isinstance(verdict, bool):
            raise GraphError("check must return True or False")
        return verdict

    async def holding(given: Mapping[str, Any], call: Call) -> dict[str, Any]:
        """What came in ``collected`` that still counts: each field's value as the
        JSON that was checked, and the keys that are not fields as they came."""
        held: dict[str, Any] = {}
        for key, value in given.items():
            if key not in validators:
                held[key] = value
                continue
            try:
                text = limits.written(value)
            except (TypeError, ValueError):
                continue
            if text is not None:
                plain = json.loads(text)
                if await kept(key, plain, call):
                    held[key] = plain
        return held

    async def accepted(found: object, wanted: list[str], call: Call) -> dict[str, Any]:
        if not isinstance(found, Mapping):
            raise GraphError("extract must return a mapping of field names to values")
        values: dict[str, Any] = {}
        for name in wanted:
            if found.get(name) is None:
                continue
            try:
                text = limits.written(found[name])
            except (TypeError, ValueError):
                raise GraphError("extract must return values that can be written as JSON") from None
            if text is None:
                raise GraphError(f"extract must return values of {limits.WITHIN}")
            plain = json.loads(text)
            if await kept(name, plain, call):
                values[name] = plain
        return values

    async def read(state: CollectState, call: Call) -> dict[str, Any]:
        # Whoever wrote `collected` (an agent filling a tool's arguments, say),
        # a field counts only while its value passes the field's shape and check.
        collected = await holding(state.get("collected") or {}, call)
        wanted = [name for name in names if name not in collected]
        if wanted:
            answer = state.get(_MESSAGE)
            message = state["message"] if answer is None else answer
            found = await call(extract, message, schema_of(wanted))
            collected = {**collected, **await accepted(found, wanted, call)}
        return {
            "collected": collected,
            "missing": [name for name in names if name not in collected],
            # An answer is read once; a later run reads its own message.
            _MESSAGE: None,
        }

    def ask(state: CollectState) -> dict[str, Any]:
        # Runs again from here when resumed, so nothing comes before the pause.
        answer = _pause({"missing": list(state["missing"])})
        if not isinstance(answer, str):
            raise GraphError("resume this graph with the user's answer as text")
        return {_MESSAGE: answer}

    def after_read(state: CollectState) -> Literal["ask", "done"]:
        return "ask" if state["missing"] else "done"

    builder = StateGraph(CollectState, input_schema=CollectInput, output_schema=CollectOutput)
    builder.add_node("read", node(read))
    builder.add_edge(START, "read")
    if interrupt:
        builder.add_node("ask", ask)
        builder.add_conditional_edges("read", after_read, {"ask": "ask", "done": END})
        builder.add_edge("ask", "read")
    else:
        builder.add_edge("read", END)
    return builder.compile(name=INFO.name)

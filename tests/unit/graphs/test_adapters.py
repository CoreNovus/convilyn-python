"""``as_node`` beyond what the shared contract covers: its argument checks."""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticSerializationError
from typing_extensions import TypedDict

from convilyn import graphs
from convilyn.graphs import _registry
from tests.unit.graphs.graph_contract import echo_graph, graph_of


@pytest.fixture
def graph() -> Any:
    return graphs.refine(
        generate=lambda task, previous, feedback: task, evaluate=lambda task, draft: None
    )


class TestAsNodeArguments:
    def test_renaming_an_input_the_graph_does_not_have_is_refused(self, graph: Any) -> None:
        with pytest.raises(
            ValueError, match=r"inputs names keys this graph does not have: \['job'\]"
        ):
            graphs.as_node(graph, inputs={"job": "request"})

    def test_renaming_an_output_the_graph_does_not_have_is_refused(self, graph: Any) -> None:
        with pytest.raises(
            ValueError, match=r"outputs names keys this graph does not have: \['reply'\]"
        ):
            graphs.as_node(graph, outputs={"reply": "text"})

    def test_the_refusal_lists_the_keys_that_exist(self, graph: Any) -> None:
        with pytest.raises(ValueError, match="it has: accepted, feedback, result, rounds"):
            graphs.as_node(graph, outputs={"reply": "text"})

    def test_with_no_renames_it_keeps_the_graph_s_own_key_names(self, graph: Any) -> None:
        node = graphs.as_node(graph)

        result = node.invoke({"task": "ship", "unrelated": 1})

        assert result == {"result": "ship", "accepted": True, "feedback": None, "rounds": 1}

    async def test_it_runs_the_same_under_the_async_entry_point(self, graph: Any) -> None:
        node = graphs.as_node(graph, outputs={"result": "reply"})

        result = await node.ainvoke({"task": "ship"})

        assert result == {"reply": "ship", "accepted": True, "feedback": None, "rounds": 1}


class TestAvailable:
    def test_it_lists_every_graph_in_name_order(self) -> None:
        names = [info.name for info in graphs.available()]

        assert names == [
            "collect",
            "corrective_rag",
            "fanout",
            "plan_execute",
            "refine",
            "use_case",
        ]

    def test_every_listed_graph_is_a_function_of_this_namespace(self) -> None:
        missing = [
            info.name
            for info in graphs.available()
            if not callable(getattr(graphs, info.name, None))
        ]

        assert missing == []

    def test_every_listed_requirement_is_a_parameter_of_its_function(self) -> None:
        import inspect

        unknown = {
            info.name: sorted(
                set(info.requires) - set(inspect.signature(getattr(graphs, info.name)).parameters)
            )
            for info in graphs.available()
        }

        assert unknown == {info.name: [] for info in _registry._GRAPHS}


class Switch(TypedDict, total=False):
    flag: bool
    count: int
    when: date
    scores: dict[str, int]


def flip_switch() -> Any:
    return echo_graph(Switch)


class TestAsToolChecksTypesStrictly:
    """A graph of the caller's own, with inputs that are not text."""

    @pytest.mark.parametrize(
        "arguments",
        [{"flag": "false", "count": 1}, {"flag": False, "count": "5"}],
        ids=["text-for-a-boolean", "text-for-a-number"],
    )
    def test_text_written_for_another_type_is_refused(self, arguments: dict[str, Any]) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        with pytest.raises(ValueError, match="do not match its inputs"):
            tool.invoke(arguments)

    def test_the_refusal_names_the_field_and_not_the_value(self) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        with pytest.raises(ValueError) as refused:
            tool.invoke({"flag": "model-written-text", "count": 1})

        assert str(refused.value) == (
            "the tool's arguments do not match its inputs: flag (bool_type)"
        )

    def test_values_of_the_right_type_pass_through_unchanged(self) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        reply = tool.invoke({"flag": False, "count": 5})

        assert json.loads(reply) == {"flag": False, "count": 5}

    def test_a_date_written_as_text_is_accepted_as_a_date(self) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        reply = tool.invoke({"when": "2026-11-14"})

        assert json.loads(reply) == {"when": "2026-11-14"}

    def test_a_key_the_model_wrote_inside_a_field_is_not_repeated(self) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        with pytest.raises(ValueError) as refused:
            tool.invoke({"scores": {"model-written-key": "x"}})

        assert str(refused.value) == (
            "the tool's arguments do not match its inputs: scores (int_type)"
        )

    def test_the_refusal_carries_no_earlier_error_holding_the_value(self) -> None:
        tool = graphs.as_tool(flip_switch(), name="switch", description="Flips it.")

        with pytest.raises(ValueError) as refused:
            tool.invoke({"flag": "model-written-text"})

        assert refused.value.__context__ is None


class Profile(BaseModel):
    name: str


class Holder(TypedDict, total=False):
    profile: Profile
    connection: str


class Handle:
    """An object with no JSON form whose text is something private."""

    def __str__(self) -> str:
        return "private-connection-text"


class TestAsToolHandsOverWhatItChecked:
    def test_a_field_declared_as_a_model_reaches_the_graph_as_that_model(self) -> None:
        seen: list[object] = []

        def record(values: Holder) -> Holder:
            seen.append(values["profile"])
            return values

        tool = graphs.as_tool(graph_of(Holder, record), name="hold", description="Holds it.")

        tool.invoke({"profile": {"name": "Ada"}})

        assert seen == [Profile(name="Ada")]

    def test_a_model_in_the_output_is_written_as_json(self) -> None:
        tool = graphs.as_tool(echo_graph(Holder), name="hold", description="Holds it.")

        reply = tool.invoke({"profile": {"name": "Ada"}})

        assert json.loads(reply) == {"profile": {"name": "Ada"}}


class TestAsToolNeverWritesAnObjectAsItsText:
    def test_an_output_with_no_json_form_is_refused(self) -> None:
        graph = graph_of(Holder, lambda values: {"connection": Handle()})
        tool = graphs.as_tool(graph, name="hold", description="Holds it.")

        with pytest.raises(PydanticSerializationError):
            tool.invoke({})

    def test_the_refusal_does_not_carry_the_object_s_text(self) -> None:
        graph = graph_of(Holder, lambda values: {"connection": Handle()})
        tool = graphs.as_tool(graph, name="hold", description="Holds it.")

        with pytest.raises(PydanticSerializationError) as refused:
            tool.invoke({})

        assert "private-connection-text" not in str(refused.value)

    def test_an_argument_with_no_json_form_is_refused_rather_than_read_as_text(self) -> None:
        tool = graphs.as_tool(echo_graph(Holder), name="hold", description="Holds it.")

        with pytest.raises(PydanticSerializationError):
            tool.invoke({"connection": Handle()})


@dataclasses.dataclass
class Order:
    item: str
    quantity: int = 3


class Draft(BaseModel):
    item: str
    quantity: int = 3


class TestAsToolOnEveryKindOfState:
    @pytest.mark.parametrize("state", [Order, Draft], ids=["dataclass", "model"])
    def test_only_the_fields_that_were_sent_are_handed_to_the_graph(self, state: Any) -> None:
        handed: list[dict[str, Any]] = []
        graph = echo_graph(state)
        invoke = graph.invoke

        def spy(values: Any, *rest: Any, **options: Any) -> Any:
            handed.append(dict(values))
            return invoke(values, *rest, **options)

        graph.invoke = spy  # type: ignore[method-assign]
        tool = graphs.as_tool(graph, name="order", description="Orders it.")

        tool.invoke({"item": "tea"})

        assert handed == [{"item": "tea"}]

    @pytest.mark.parametrize("state", [Order, Draft], ids=["dataclass", "model"])
    def test_the_graph_applies_its_own_default_for_a_field_not_sent(self, state: Any) -> None:
        tool = graphs.as_tool(echo_graph(state), name="order", description="Orders it.")

        reply = tool.invoke({"item": "tea"})

        assert json.loads(reply) == {"item": "tea", "quantity": 3}

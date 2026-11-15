"""``graphs.use_case``: the shared contract in both modes, then what is particular to it."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from convilyn import graphs
from tests.unit.graphs.graph_contract import (
    THREAD,
    GraphContract,
    Pause,
    host_around,
    pausing_tool,
    pending,
    recording_middleware,
    resume,
    scripted_model,
    tool_bringing_middleware,
    tool_of,
)

REFUND = graphs.UseCase(
    id="refund",
    description="Refund an order.",
    instructions="Refund the order the user names.",
    fields={"order_id": {"type": "string", "maxLength": 16, "pattern": "^A-[0-9]+$"}},
    tools=("refund",),
    max_model_calls=3,
    max_tool_calls=1,
)
ADDRESS = graphs.UseCase(
    id="address",
    description="Change the delivery address.",
    instructions="Change the address.",
    tools=("change_address",),
    max_model_calls=2,
    max_tool_calls=1,
)
KEYS = ("message", "use_case_id", "collected", "clarify_turns")
OUTPUTS = ("outcome", "reply", "candidates", "missing")


def refund(order_id: str) -> str:
    """Refund an order."""
    return f"refunded {order_id}"


def change_address(address: str) -> str:
    """Change the delivery address."""
    return f"moved to {address}"


def pairs(message: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Reads ``name=value`` words, the way a model reads the fields it was asked for."""
    found = dict(word.split("=", 1) for word in message.split() if "=" in word)
    return {name: found.get(name) for name in schema["properties"]}


def by_keyword(message: str, choices: list[dict[str, str]], active: str | None) -> dict[str, Any]:
    """Names every use case whose id appears in the message, or keeps the active one."""
    named = [choice["id"] for choice in choices if choice["id"] in message]
    if len(named) == 1:
        return {"use_case_id": named[0], "candidates": []}
    return {"use_case_id": None if named else active, "candidates": named}


def acting(turns: list[dict[str, Any]], tools: list[str]) -> Any:
    """Calls the first bound tool with the collected values, then reports what it returned."""
    last = turns[-1]
    if last["role"] == "tool":
        return f"done: {last['texts'][0]}"
    collected = json.loads(last["texts"][1])["collected"] if len(last["texts"]) > 1 else {}
    arguments = collected or {"address": "Taipei"}
    return {"name": tools[0], "args": arguments} if tools else "nothing to do"


def confirm_then_report(turns: list[dict[str, Any]], tools: list[str]) -> Any:
    last = turns[-1]
    if last["role"] == "tool":
        return last["texts"][0]
    return {"name": "confirm", "args": {"value": "move"}}


def build(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "cases": [REFUND, ADDRESS],
        "model": scripted_model(acting),
        "tools": [tool_of(refund), tool_of(change_address), pausing_tool("confirm")],
        "extract": pairs,
        "route": by_keyword,
        **overrides,
    }
    return graphs.use_case(**arguments)


ANSWERED = {
    "outcome": "answered",
    "reply": "done: refunded A-1",
    "use_case_id": "refund",
    "candidates": ["refund"],
    "collected": {"refund": {"order_id": "A-1"}},
    "missing": [],
    "clarify_turns": 0,
}


class TestUseCaseContract(GraphContract):
    NAME = "use_case"

    @pytest.fixture
    def graph(self) -> Any:
        return build()

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"message": "refund order_id=A-1"}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        return ANSWERED


class TestUseCaseWhenItWaitsForAnswersContract(TestUseCaseContract):
    @pytest.fixture
    def graph(self) -> Any:
        return build(interrupt=True)

    @pytest.fixture
    def pause(self) -> Pause:
        return Pause(
            payload={"message": "refund please"},
            asked={"missing": ["order_id"]},
            answer="order_id=A-1",
            expected=ANSWERED,
        )


class TestRouting:
    def test_the_use_case_the_router_names_is_run(self) -> None:
        result = build().invoke({"message": "address please"})

        assert result["reply"] == "done: moved to Taipei"

    def test_the_router_is_given_every_use_case_and_the_one_in_progress(self) -> None:
        given: list[Any] = []

        def remember(message: str, choices: list[dict[str, str]], active: str | None) -> Any:
            given.append((message, choices, active))
            return {"use_case_id": None, "candidates": []}

        build(route=remember).invoke({"message": "hi", "use_case_id": "refund"})

        assert given == [
            (
                "hi",
                [
                    {"id": "refund", "description": "Refund an order."},
                    {"id": "address", "description": "Change the delivery address."},
                ],
                "refund",
            )
        ]

    def test_an_active_id_that_is_not_a_use_case_reaches_the_router_as_none(self) -> None:
        given: list[str | None] = []

        def remember(message: str, choices: list[dict[str, str]], active: str | None) -> Any:
            given.append(active)
            return {"use_case_id": None, "candidates": []}

        build(route=remember).invoke({"message": "hi", "use_case_id": "gone"})

        assert given == [None]

    def test_a_message_no_use_case_fits_ends_without_an_answer(self) -> None:
        result = build().invoke({"message": "hello"})

        assert result == {
            "outcome": "no_match",
            "reply": None,
            "use_case_id": None,
            "candidates": [],
            "collected": {},
            "missing": [],
            "clarify_turns": 0,
        }

    def test_an_id_the_router_made_up_is_not_run(self) -> None:
        graph = build(route=lambda *_: {"use_case_id": "delete_all", "candidates": ["delete_all"]})

        result = graph.invoke({"message": "x"})

        assert (result["outcome"], result["candidates"]) == ("no_match", [])

    def test_a_message_that_could_mean_several_asks_the_user_to_choose(self) -> None:
        result = build().invoke({"message": "refund or address?"})

        assert (result["outcome"], result["candidates"], result["clarify_turns"]) == (
            "clarify",
            ["refund", "address"],
            1,
        )

    def test_the_chosen_use_case_comes_first_among_the_candidates(self) -> None:
        graph = build(
            route=lambda *_: {"use_case_id": "address", "candidates": ["refund", "address"]}
        )

        result = graph.invoke({"message": "x"})

        assert result["candidates"] == ["address", "refund"]

    def test_after_the_clarify_limit_an_ambiguous_message_finds_no_match(self) -> None:
        result = build().invoke({"message": "refund or address?", "clarify_turns": 1})

        assert (result["outcome"], result["clarify_turns"]) == ("no_match", 0)

    def test_with_no_clarify_turns_allowed_the_user_is_never_asked_to_choose(self) -> None:
        result = build(max_clarify_turns=0).invoke({"message": "refund or address?"})

        assert result["outcome"] == "no_match"

    def test_a_graph_of_one_use_case_never_asks_the_user_to_choose(self) -> None:
        graph = build(
            cases=[ADDRESS],
            route=lambda *_: {"use_case_id": "address", "candidates": ["address", "x", "y"]},
        )

        result = graph.invoke({"message": "x"})

        assert result["outcome"] == "answered"

    def test_at_most_three_candidates_are_offered(self) -> None:
        cases = [ADDRESS.model_copy(update={"id": f"c{n}"}) for n in range(5)]
        named = [case.id for case in cases]
        graph = build(cases=cases, route=lambda *_: {"use_case_id": None, "candidates": named})

        result = graph.invoke({"message": "x"})

        assert result["candidates"] == ["c0", "c1", "c2"]

    def test_a_chosen_use_case_resets_the_clarify_count(self) -> None:
        result = build().invoke({"message": "address", "clarify_turns": 1})

        assert result["clarify_turns"] == 0

    def test_a_negative_clarify_count_does_not_lift_the_clarify_limit(self) -> None:
        result = build().invoke({"message": "refund or address?", "clarify_turns": -1000})

        assert result["outcome"] == "clarify"

    def test_a_negative_clarify_count_counts_from_zero(self) -> None:
        result = build().invoke({"message": "refund or address?", "clarify_turns": -1000})

        assert result["clarify_turns"] == 1

    async def test_an_async_router_runs_under_ainvoke(self) -> None:
        async def route(message: str, choices: Any, active: str | None) -> dict[str, Any]:
            return {"use_case_id": "address", "candidates": []}

        result = await build(route=route).ainvoke({"message": "x"})

        assert result["outcome"] == "answered"

    def test_an_async_router_under_invoke_says_to_use_ainvoke(self) -> None:
        async def route(message: str, choices: Any, active: str | None) -> dict[str, Any]:
            return {"use_case_id": "address", "candidates": []}

        with pytest.raises(TypeError, match="ainvoke"):
            build(route=route).invoke({"message": "x"})

    @pytest.mark.parametrize(
        "returned",
        [
            "address",
            {"use_case_id": 7, "candidates": []},
            {"use_case_id": None, "candidates": "address"},
            {"use_case_id": None, "candidates": [1]},
        ],
        ids=["not-a-mapping", "id-not-text", "candidates-not-a-list", "candidate-not-text"],
    )
    def test_a_router_answer_of_the_wrong_shape_raises(self, returned: Any) -> None:
        graph = build(route=lambda *_: returned)

        with pytest.raises(graphs.GraphError, match="route must return"):
            graph.invoke({"message": "x"})


class TestWithoutARouter:
    def test_the_use_case_the_host_names_is_run(self) -> None:
        result = build(route=None).invoke({"message": "x", "use_case_id": "address"})

        assert result["reply"] == "done: moved to Taipei"

    def test_with_no_use_case_named_there_is_no_match(self) -> None:
        result = build(route=None).invoke({"message": "x"})

        assert result["outcome"] == "no_match"

    def test_a_use_case_that_does_not_exist_raises_without_repeating_it(self) -> None:
        with pytest.raises(graphs.GraphError) as raised:
            build(route=None).invoke({"message": "x", "use_case_id": "secret-name"})

        assert "secret-name" not in str(raised.value)


class TestCollecting:
    def test_a_missing_field_ends_the_turn_without_running_the_agent(self) -> None:
        seen: dict[str, Any] = {}

        result = build(model=scripted_model(acting, seen)).invoke({"message": "refund please"})

        assert (result["outcome"], result["missing"], seen) == ("missing", ["order_id"], {})

    def test_the_next_turn_continues_from_what_was_collected(self) -> None:
        graph = build()
        first = graph.invoke({"message": "refund please"})

        result = graph.invoke({"message": "order_id=A-1", **first})

        assert result == ANSWERED

    def test_fields_are_kept_under_their_use_case(self) -> None:
        theirs = {"address": {"note": "kept"}, "mine": "kept"}

        result = build().invoke({"message": "refund order_id=A-1", "collected": theirs})

        assert result["collected"] == {**theirs, "refund": {"order_id": "A-1"}}

    def test_what_another_use_case_collected_is_not_used(self) -> None:
        collected = {"address": {"order_id": "A-1"}}

        result = build().invoke({"message": "refund please", "collected": collected})

        assert result["missing"] == ["order_id"]

    def test_a_use_case_value_that_is_not_a_mapping_counts_as_nothing_collected(self) -> None:
        result = build().invoke({"message": "refund please", "collected": {"refund": "A-1"}})

        assert result["missing"] == ["order_id"]

    def test_the_collected_dictionary_passed_in_is_not_changed(self) -> None:
        given: dict[str, Any] = {"refund": {}}

        build().invoke({"message": "refund order_id=A-1", "collected": given})

        assert given == {"refund": {}}


#: A use case whose field the shapes cannot express, checked by the developer's own code.
FREE_REFUND = REFUND.model_copy(update={"fields": {"order_id": True}})


def known_order(field: str, value: Any) -> bool:
    return value == "A-1"


class TestYourOwnCheck:
    def test_a_value_your_check_accepts_is_answered_with(self) -> None:
        graph = build(cases=[FREE_REFUND, ADDRESS], check=known_order)

        result = graph.invoke({"message": "refund order_id=A-1"})

        assert result == ANSWERED

    def test_a_value_your_check_refuses_is_still_missing(self) -> None:
        graph = build(cases=[FREE_REFUND, ADDRESS], check=known_order)

        result = graph.invoke({"message": "refund order_id=B-9"})

        assert (result["outcome"], result["missing"]) == ("missing", ["order_id"])

    def test_a_value_that_came_in_collected_is_checked_too(self) -> None:
        graph = build(cases=[FREE_REFUND, ADDRESS], check=known_order)

        result = graph.invoke(
            {"message": "refund please", "collected": {"refund": {"order_id": "B-9"}}}
        )

        assert result["missing"] == ["order_id"]

    def test_a_field_outside_the_shapes_is_refused_naming_the_use_case_field(self) -> None:
        nested = REFUND.model_copy(update={"fields": {"order_id": {"anyOf": [True]}}})

        with pytest.raises(ValueError, match="field 'order_id' is refused: 'anyOf'"):
            build(cases=[nested, ADDRESS])


class TestTheAgent:
    def test_it_is_bound_only_to_its_use_case_s_tools(self) -> None:
        seen: dict[str, Any] = {}

        build(model=scripted_model(acting, seen)).invoke({"message": "refund order_id=A-1"})

        assert seen["tools"] == ["refund"]

    def test_it_is_instructed_with_its_use_case_s_instructions(self) -> None:
        seen: dict[str, Any] = {}

        build(model=scripted_model(acting, seen)).invoke({"message": "refund order_id=A-1"})

        assert seen["calls"][0][0] == {"role": "system", "texts": [REFUND.instructions]}

    def test_the_collected_values_come_as_json_in_the_user_s_turn(self) -> None:
        seen: dict[str, Any] = {}

        build(model=scripted_model(acting, seen)).invoke({"message": "refund order_id=A-1"})

        assert seen["calls"][0][1] == {
            "role": "user",
            "texts": ["refund order_id=A-1", '{"collected": {"order_id": "A-1"}}'],
        }

    def test_a_use_case_without_fields_sends_the_message_alone(self) -> None:
        seen: dict[str, Any] = {}

        build(model=scripted_model(acting, seen)).invoke({"message": "address"})

        assert seen["calls"][0][1] == {"role": "user", "texts": ["address"]}

    def test_a_call_to_a_tool_of_another_use_case_is_refused_without_running_it(self) -> None:
        ran: list[str] = []

        def change_address(address: str) -> str:
            """Change the delivery address."""
            ran.append(address)
            return "moved"

        def stray(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            if turns[-1]["role"] == "tool":
                return "stopped"
            return {"name": "change_address", "args": {"address": "x"}}

        build(model=scripted_model(stray), tools=[tool_of(refund), tool_of(change_address)]).invoke(
            {"message": "refund order_id=A-1"}
        )

        assert ran == []

    def test_running_out_of_model_calls_ends_without_a_reply(self) -> None:
        def keep_calling(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            return {"name": "refund", "args": {"order_id": "A-1"}}

        result = build(model=scripted_model(keep_calling)).invoke(
            {"message": "refund order_id=A-1"}
        )

        assert (result["outcome"], result["reply"]) == ("budget_exhausted", None)

    def test_a_tool_call_past_the_limit_is_not_run(self) -> None:
        ran: list[str] = []

        def counted(order_id: str) -> str:
            """Refund an order."""
            ran.append(order_id)
            return "refunded"

        def twice(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            calls = sum(turn["role"] == "tool" for turn in turns)
            return {"name": "counted", "args": {"order_id": "A-1"}} if calls < 2 else "stopped"

        one_call = REFUND.model_copy(update={"max_tool_calls": 1, "tools": ("counted",)})
        build(cases=[one_call], model=scripted_model(twice), tools=[tool_of(counted)]).invoke(
            {"message": "refund order_id=A-1"}
        )

        assert ran == ["A-1"]

    def test_your_middleware_runs_before_each_model_call(self) -> None:
        seen: list[int] = []

        build(middleware=[recording_middleware(seen)]).invoke({"message": "refund order_id=A-1"})

        assert seen == [1, 3]

    def test_a_tool_that_pauses_resumes_inside_the_host(self) -> None:
        confirming = ADDRESS.model_copy(update={"tools": ("confirm",)})

        graph = build(cases=[confirming], model=scripted_model(confirm_then_report))
        host = host_around(graph, KEYS + OUTPUTS, saved=True)
        host.invoke({"message": "address"}, THREAD)

        result = host.invoke(resume("yes"), THREAD)

        assert result["reply"] == "answer: yes"

    def test_a_tool_that_pauses_leaves_the_host_waiting_on_it(self) -> None:
        confirming = ADDRESS.model_copy(update={"tools": ("confirm",)})
        graph = build(cases=[confirming], model=scripted_model(confirm_then_report))
        host = host_around(graph, KEYS + OUTPUTS, saved=True)

        host.invoke({"message": "address"}, THREAD)

        assert pending(host) == [{"confirm": "move"}]


class TestLimitsAcrossAPause:
    """A pause inside the agent does not give it a fresh budget; the next turn does."""

    def test_a_tool_call_past_the_limit_is_not_run_after_a_resume(self) -> None:
        ran: list[str] = []

        def counted(order_id: str) -> str:
            """Refund an order."""
            ran.append(order_id)
            return "refunded"

        def refund_confirm_refund(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            plan = [
                {"name": "counted", "args": {"order_id": "A-1"}},
                {"name": "confirm", "args": {"value": "again?"}},
                {"name": "counted", "args": {"order_id": "A-1"}},
            ]
            made = sum(turn["role"] == "tool" for turn in turns)
            return plan[made] if made < len(plan) else "stopped"

        case = ADDRESS.model_copy(
            update={"tools": ("counted", "confirm"), "max_tool_calls": 2, "max_model_calls": 8}
        )
        graph = build(
            cases=[case],
            model=scripted_model(refund_confirm_refund),
            tools=[tool_of(counted), pausing_tool("confirm")],
        )
        host = host_around(graph, KEYS + OUTPUTS, saved=True)
        host.invoke({"message": "address"}, THREAD)

        host.invoke(resume("yes"), THREAD)

        assert ran == ["A-1"]

    def test_model_calls_after_resumes_count_toward_the_limit(self) -> None:
        def always_confirm(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            return {"name": "confirm", "args": {"value": "again?"}}

        case = ADDRESS.model_copy(
            update={"tools": ("confirm",), "max_tool_calls": 8, "max_model_calls": 2}
        )
        graph = build(cases=[case], model=scripted_model(always_confirm))
        host = host_around(graph, KEYS + OUTPUTS, saved=True)
        host.invoke({"message": "address"}, THREAD)
        host.invoke(resume("yes"), THREAD)

        result = host.invoke(resume("yes"), THREAD)

        assert result["outcome"] == "budget_exhausted"

    def test_the_next_turn_starts_with_a_full_budget(self) -> None:
        ran: list[str] = []

        def counted(order_id: str) -> str:
            """Refund an order."""
            ran.append(order_id)
            return "refunded"

        def once(turns: list[dict[str, Any]], tools: list[str]) -> Any:
            if turns[-1]["role"] == "tool":
                return "stopped"
            return {"name": "counted", "args": {"order_id": "A-1"}}

        case = REFUND.model_copy(update={"tools": ("counted",), "max_tool_calls": 1})
        graph = build(cases=[case], model=scripted_model(once), tools=[tool_of(counted)])
        host = host_around(graph, KEYS + OUTPUTS, saved=True)
        host.invoke({"message": "refund order_id=A-1"}, THREAD)

        host.invoke({"message": "refund order_id=A-1"}, THREAD)

        assert ran == ["A-1", "A-1"]


class TestBuilding:
    def test_middleware_that_brings_tools_is_refused(self) -> None:
        with pytest.raises(ValueError, match="brings tools of its own"):
            build(middleware=[tool_bringing_middleware(tool_of(refund))])

    def test_no_use_cases_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one use case"):
            build(cases=[])

    def test_two_use_cases_with_the_same_id_are_refused(self) -> None:
        with pytest.raises(ValueError, match="two use cases have the id"):
            build(cases=[ADDRESS, ADDRESS])

    def test_a_use_case_given_as_a_dictionary_is_refused(self) -> None:
        with pytest.raises(TypeError, match="graphs.UseCase"):
            build(cases=[ADDRESS.model_dump()])

    def test_a_tool_name_that_was_not_passed_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names tools that were not passed"):
            build(tools=[tool_of(refund)])

    def test_two_tools_with_the_same_name_are_refused(self) -> None:
        with pytest.raises(ValueError, match="two tools are named"):
            build(tools=[tool_of(refund), tool_of(refund), tool_of(change_address)])

    def test_fields_without_extract_are_refused(self) -> None:
        with pytest.raises(ValueError, match="extract is needed"):
            build(extract=None)

    def test_use_cases_without_fields_need_no_extract(self) -> None:
        graph = build(cases=[ADDRESS], extract=None)

        assert graph.invoke({"message": "address"})["outcome"] == "answered"

    def test_a_negative_clarify_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_clarify_turns"):
            build(max_clarify_turns=-1)

    def test_a_field_schema_that_is_not_json_schema_is_refused(self) -> None:
        broken = REFUND.model_copy(
            update={"fields": {"order_id": {"type": "string", "minLength": -1}}}
        )

        with pytest.raises(ValueError, match="not a valid JSON Schema"):
            build(cases=[broken])


class TestTheSpec:
    def test_it_loads_from_json(self) -> None:
        text = REFUND.model_dump_json()

        assert graphs.UseCase.model_validate_json(text) == REFUND

    def test_it_cannot_be_changed(self) -> None:
        with pytest.raises(ValidationError):
            REFUND.id = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "change",
        [
            {"max_model_calls": 0},
            {"max_model_calls": 9},
            {"max_tool_calls": -1},
            {"max_tool_calls": 9},
            {"id": ""},
            {"unexpected": 1},
        ],
        ids=[
            "no-model-call",
            "too-many-model-calls",
            "negative-tool-calls",
            "too-many-tool-calls",
            "empty-id",
            "unknown-key",
        ],
    )
    def test_out_of_range_or_unknown_values_are_refused(self, change: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            graphs.UseCase.model_validate({**REFUND.model_dump(), **change})

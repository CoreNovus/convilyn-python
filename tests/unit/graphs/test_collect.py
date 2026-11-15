"""``graphs.collect``: the shared contract in both modes, then what is particular to it."""

from __future__ import annotations

import asyncio
import copy
import datetime
import json
import re
from typing import Any

import pytest

from convilyn import graphs
from tests.unit.graphs.graph_contract import (
    THREAD,
    GraphContract,
    Pause,
    host_around,
    pending,
    resume,
    saving,
)

FIELDS: dict[str, dict[str, Any]] = {
    "order_id": {
        "type": "string",
        "maxLength": 16,
        "pattern": "^A-[0-9]+$",
        "description": "The order number.",
    },
    "email": {"type": "string", "description": "Where to send the reply."},
}
BOTH = {"order_id": "A-1", "email": "a@b.c"}
KEYS = ("message", "collected", "missing")


def pairs(message: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Reads ``name=value`` words, the way a model reads the fields it was asked for."""
    found = dict(word.split("=", 1) for word in message.split() if "=" in word)
    return {name: found.get(name) for name in schema["properties"]}


def returning(value: Any) -> Any:
    return lambda message, schema: value


def build(**overrides: Any) -> Any:
    return graphs.collect(**{"fields": FIELDS, "extract": pairs, **overrides})


class TestCollectContract(GraphContract):
    NAME = "collect"

    @pytest.fixture
    def graph(self) -> Any:
        return build()

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"message": "email=a@b.c", "collected": {"order_id": "A-1"}}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        return {"collected": BOTH, "missing": []}


class TestCollectWhenItWaitsForAnswersContract(TestCollectContract):
    @pytest.fixture
    def graph(self) -> Any:
        return build(interrupt=True)

    @pytest.fixture
    def pause(self) -> Pause:
        return Pause(
            payload={"message": "order_id=A-1"},
            asked={"missing": ["email"]},
            answer="email=a@b.c",
            expected={"collected": BOTH, "missing": []},
        )


class TestCollecting:
    def test_a_message_giving_some_fields_leaves_the_rest_missing(self) -> None:
        result = build().invoke({"message": "order_id=A-1"})

        assert result == {"collected": {"order_id": "A-1"}, "missing": ["email"]}

    def test_a_message_giving_nothing_leaves_every_field_missing(self) -> None:
        result = build().invoke({"message": "hello"})

        assert result == {"collected": {}, "missing": ["order_id", "email"]}

    def test_missing_fields_are_listed_in_the_order_they_were_declared(self) -> None:
        graph = build(fields=dict(reversed(FIELDS.items())))

        result = graph.invoke({"message": "hello"})

        assert result["missing"] == ["email", "order_id"]

    def test_the_next_turn_adds_to_what_was_collected(self) -> None:
        graph = build()
        first = graph.invoke({"message": "order_id=A-1"})

        result = graph.invoke({"message": "email=a@b.c", "collected": first["collected"]})

        assert result == {"collected": BOTH, "missing": []}

    def test_a_value_already_collected_is_never_replaced(self) -> None:
        result = build().invoke({"message": "order_id=A-2", "collected": {"order_id": "A-1"}})

        assert result["collected"]["order_id"] == "A-1"

    def test_keys_of_the_host_s_own_in_collected_are_kept(self) -> None:
        result = build().invoke({"message": "", "collected": {**BOTH, "note": "vip"}})

        assert result["collected"] == {**BOTH, "note": "vip"}

    def test_a_host_key_is_carried_through_unchecked_however_deep(self) -> None:
        deep = nested(100)

        result = build().invoke({"message": "", "collected": {**BOTH, "note": deep}})

        assert result["collected"]["note"] is deep

    def test_the_collected_dictionary_passed_in_is_not_changed(self) -> None:
        given = {"order_id": "A-1"}
        before = copy.deepcopy(given)

        build().invoke({"message": "email=a@b.c", "collected": given})

        assert given == before

    def test_text_in_any_script_passes_through_unchanged(self) -> None:
        result = build().invoke({"message": "order_id=A-1 email=退款@例子.台灣"})

        assert result["collected"]["email"] == "退款@例子.台灣"

    def test_a_value_written_as_a_tuple_is_kept_as_a_list(self) -> None:
        graph = graphs.collect(
            fields={"tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
            extract=returning({"tags": ("a", "b")}),
        )

        result = graph.invoke({"message": ""})

        assert result["collected"] == {"tags": ["a", "b"]}


class TestWhatIsNotKept:
    @pytest.mark.parametrize(
        "found",
        [
            {"order_id": "B-1", "email": "a@b.c"},
            {"order_id": None, "email": "a@b.c"},
            {"order_id": 7, "email": "a@b.c"},
            {"email": "a@b.c"},
        ],
        ids=["fails-its-pattern", "none", "wrong-type", "left-out"],
    )
    def test_a_field_without_a_valid_value_stays_missing(self, found: dict[str, Any]) -> None:
        result = build(extract=returning(found)).invoke({"message": ""})

        assert result == {"collected": {"email": "a@b.c"}, "missing": ["order_id"]}

    def test_a_value_for_a_field_that_was_not_asked_for_is_dropped(self) -> None:
        graph = build(extract=returning({**BOTH, "admin": True}))

        result = graph.invoke({"message": ""})

        assert result["collected"] == BOTH


class TestWhatComesInCollected:
    @pytest.mark.parametrize(
        "given",
        [{"order_id": 12345}, {"order_id": "DROP TABLE"}, {"order_id": object()}],
        ids=["wrong-type", "fails-its-pattern", "no-json-form"],
    )
    def test_a_value_that_does_not_match_its_field_is_removed_and_asked_for(
        self, given: dict[str, Any]
    ) -> None:
        result = build(extract=returning({})).invoke({"message": "", "collected": given})

        assert result == {"collected": {}, "missing": ["order_id", "email"]}

    def test_a_removed_value_can_be_collected_again_in_the_same_run(self) -> None:
        result = build().invoke({"message": "order_id=A-1", "collected": {"order_id": 7}})

        assert result["collected"] == {"order_id": "A-1"}

    def test_an_agent_cannot_mark_a_field_collected_with_an_invalid_value(self) -> None:
        tool = graphs.as_tool(build(extract=returning({})), name="collect", description="d")

        reply = tool.invoke({"message": "", "collected": {"order_id": "x", "email": "a@b.c"}})

        assert json.loads(reply) == {"collected": {"email": "a@b.c"}, "missing": ["order_id"]}


#: One of each admitted shape, with a value it keeps.
ADMITTED = [
    ({"type": "string", "minLength": 2, "maxLength": 9, "pattern": "^A-", "format": "x"}, "A-1"),
    ({"type": "integer", "minimum": 1, "maximum": 9, "multipleOf": 2}, 4),
    ({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}, 0.5),
    ({"type": "boolean"}, False),
    ({"type": ["string", "null"], "maxLength": 3}, "abc"),
    ({"enum": ["a", 1, True, None]}, 1),
    ({"const": "yes", "title": "Agree", "description": "d", "examples": [{"any": 1}]}, "yes"),
    ({"type": "array", "items": {"type": "string"}, "maxItems": 3, "uniqueItems": True}, ["a"]),
    (
        {
            "type": "object",
            "properties": {"street": {"type": "string"}, "$ref": {"type": "string"}},
            "required": ["street"],
            "additionalProperties": False,
        },
        {"street": "Main", "$ref": "a property, not a reference"},
    ),
    ({"type": "array", "maxItems": 2}, ["x", 1]),
    (True, {"any": ["thing"]}),
    ({"description": "anything at all"}, [1, {"a": None}]),
]


class TestTheShapesAdmitted:
    @pytest.mark.parametrize(("schema", "value"), ADMITTED, ids=lambda case: str(case)[:24])
    def test_each_shape_keeps_a_value_it_accepts(self, schema: Any, value: Any) -> None:
        graph = build(fields={"order_id": schema}, extract=returning({"order_id": value}))

        result = graph.invoke({"message": ""})

        assert result["collected"] == {"order_id": value}

    def test_a_value_outside_its_shape_is_not_kept(self) -> None:
        schema = {"type": "integer", "maximum": 9}
        graph = build(fields={"order_id": schema}, extract=returning({"order_id": 10}))

        result = graph.invoke({"message": ""})

        assert result["missing"] == ["order_id"]


#: Keywords and shapes outside the admitted set, each with the keyword it is refused for.
REFUSED = [
    ({"$ref": "#"}, "'$ref'"),
    ({"$defs": {"a": {"type": "string"}}, "type": "string"}, "'$defs'"),
    ({"$id": "https://example.com/s", "type": "string"}, "'$id'"),
    ({"$schema": "https://json-schema.org/draft/2020-12/schema"}, "'$schema'"),
    ({"allOf": [{"type": "string"}]}, "'allOf'"),
    ({"anyOf": [{"type": "string"}]}, "'anyOf'"),
    ({"oneOf": [{"type": "string"}]}, "'oneOf'"),
    ({"not": {"type": "string"}}, "'not'"),
    ({"if": {"type": "string"}}, "'if'"),
    ({"type": "string", "x-note": 1}, "'x-note'"),
    ({"type": "string", "minimum": 1}, "'minimum'"),
    ({"type": "array", "maxItems": 2, "unevaluatedItems": False}, "'unevaluatedItems'"),
    ({"type": "array", "maxItems": 2, "items": {"type": "object"}}, "'type'"),
    ({"type": "array", "maxItems": 2, "items": {"type": "array"}}, "'type'"),
    ({"type": "array", "items": {"type": "string"}}, "needs 'maxItems'"),
    ({"type": "array", "maxItems": 257}, "needs 'maxItems', at most 256"),
    ({"type": "array", "maxItems": 2, "contains": {"type": "string"}}, "'contains'"),
    ({"type": "object", "properties": {"a": {"type": "object"}}}, "'type'"),
    ({"type": "object", "additionalProperties": {"type": "string"}}, "may only be false"),
    ({"type": "object", "patternProperties": {"^a": True}}, "'patternProperties'"),
    ({"type": "integer", "multipleOf": 0.1}, "must be a whole number"),
    ({"enum": [{"a": 1}]}, "only strings, numbers, booleans and null"),
    ({"enum": list(range(257))}, "at most 256 values"),
    ({"const": [1]}, "'const' may be only"),
    ({"type": ["string", {"a": 1}]}, "'type'"),
    ({"type": "array", "maxItems": 2, "items": {"type": [["string"]]}}, "'type'"),
    ({"type": "string", "pattern": "^A"}, "a 'pattern' needs 'maxLength', at most 256"),
    ({"type": "string", "maxLength": 257, "pattern": "^A"}, "needs 'maxLength', at most 256"),
    ({"type": "integer", "multipleOf": 2**53 + 1}, "a whole number up to"),
    ({"type": "string", "description": "x" * 1_100_000}, "larger or deeper than any"),
]


class TestWhatIsRefused:
    @pytest.mark.parametrize(("schema", "named"), REFUSED, ids=lambda case: str(case)[:30])
    def test_a_schema_outside_the_shapes_is_refused_naming_what(
        self, schema: Any, named: str
    ) -> None:
        with pytest.raises(ValueError, match=re.escape(named)):
            build(fields={"order_id": schema})

    def test_the_refusal_names_the_field_and_the_way_out(self) -> None:
        with pytest.raises(ValueError) as refused:
            build(fields={"order_id": {"anyOf": [{"type": "string"}]}})

        assert str(refused.value) == (
            "the schema of field 'order_id' is refused: 'anyOf' is not admitted here; a field "
            "these shapes cannot express takes the schema true and is checked by your own "
            "check function"
        )

    def test_the_refusal_does_not_quote_the_schema(self) -> None:
        with pytest.raises(ValueError) as refused:
            build(fields={"order_id": {"type": "string", "pattern": "private-pattern", "q": 1}})

        assert "private-pattern" not in str(refused.value)

    def test_an_object_with_more_than_64_properties_is_refused(self) -> None:
        # 600 such properties, each with its own pattern, took 5.1 s per check:
        # past 512 patterns the regular-expression cache compiles each again.
        properties = {f"p{n}": {"type": "string", "pattern": f"^{n}"} for n in range(65)}

        with pytest.raises(ValueError, match="at most 64 properties"):
            build(fields={"order_id": {"type": "object", "properties": properties}})

    def test_a_schema_too_large_for_any_shape_is_refused_before_it_is_read(self) -> None:
        with pytest.raises(ValueError, match="larger or deeper than any admitted shape"):
            build(fields={"order_id": {"description": "x", "examples": [nested(3000)]}})


#: Cases found by reviewing earlier versions that admitted any JSON Schema. Each
#: took seconds to check one in-limit value, or followed past the checks; each is
#: now refused when the graph is built.
SLOW_BEFORE = [
    # 19.3 s: uniqueItems compared pairwise once per application.
    ({"allOf": [{"uniqueItems": True}] * 998}, "'allOf'"),
    # 1.05 s: 2,000 parts applied to 255 items.
    ({"items": {"allOf": [{"type": "string"}] * 998}}, "'items'"),
    # 3.85 s: anyOf collects every error of the failing branch.
    ({"anyOf": [{"items": {"allOf": [{"type": "integer"}] * 996}}, {"type": "null"}]}, "'anyOf'"),
    # 4.7 s: a dialect named below the top brings keywords no check read.
    (
        {
            "anyOf": [
                {
                    "$schema": "https://json-schema.org/draft/2019-09/schema",
                    "items": {"$recursiveRef": "#"},
                }
            ]
        },
        "'anyOf'",
    ),
    # Followed into the recursive metaschema: the validator knows its URI.
    (
        {
            "$id": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"m": {"$anchor": "meta", "type": "integer"}},
            "$ref": "#meta",
        },
        "'$id'",
    ),
    # 29.5 s: shared references doubling at each of 18 levels.
    (
        {
            "$defs": {f"d{i}": {"anyOf": [{"$ref": f"#/$defs/d{i + 1}"}] * 2} for i in range(18)},
            "$ref": "#/$defs/d0",
        },
        "'$defs'",
    ),
]


class TestTheSlowCasesFoundBeforeAreRefused:
    @pytest.mark.parametrize(("schema", "named"), SLOW_BEFORE, ids=lambda case: str(case)[:30])
    def test_it_is_refused_while_building(self, schema: Any, named: str) -> None:
        with pytest.raises(ValueError, match=re.escape(named)):
            build(fields={"order_id": schema})


def nested(levels: int) -> list[Any]:
    """An empty list inside ``levels - 1`` more lists: ``levels`` lists deep."""
    value: list[Any] = []
    for _ in range(levels - 1):
        value = [value]
    return value


class TestTheLimitsAValueIsCheckedWithin:
    def test_a_value_of_256_parts_is_kept(self) -> None:
        graph = build(fields={"tree": True}, extract=returning({}))

        result = graph.invoke({"message": "", "collected": {"tree": [0] * 255}})

        assert result["missing"] == []

    def test_a_collected_value_of_more_parts_is_dropped(self) -> None:
        # Compared pair by pair for uniqueItems, 4,000 small objects took 24 s.
        graph = build(fields={"tree": True}, extract=returning({}))

        result = graph.invoke(
            {"message": "", "collected": {"tree": [{"n": n} for n in range(4000)]}}
        )

        assert result["missing"] == ["tree"]

    def test_a_value_sixty_four_levels_deep_is_kept(self) -> None:
        graph = build(fields={"tree": True}, extract=returning({"tree": nested(64)}))

        result = graph.invoke({"message": ""})

        assert result["missing"] == []

    @pytest.mark.parametrize("levels", [65, 3000])
    def test_a_collected_value_nested_deeper_is_dropped(self, levels: int) -> None:
        graph = build(fields={"tree": True}, extract=returning({}))

        result = graph.invoke({"message": "", "collected": {"tree": nested(levels)}})

        assert result["missing"] == ["tree"]

    def test_a_collected_value_of_16_kb_as_json_is_kept(self) -> None:
        graph = build(fields={"note": {"type": "string"}}, extract=returning({}))
        text = "x" * (16_384 - 2)

        result = graph.invoke({"message": "", "collected": {"note": text}})

        assert result["missing"] == []

    def test_a_collected_value_longer_than_16_kb_is_dropped(self) -> None:
        # Every error a check records holds the value's text: 255 strings of
        # 20 KB took 15.6 s.
        graph = build(fields={"note": {"type": "string"}}, extract=returning({}))
        text = "x" * (16_384 - 1)

        result = graph.invoke({"message": "", "collected": {"note": text}})

        assert result["missing"] == ["note"]

    @pytest.mark.parametrize(
        "value",
        [nested(65), [{"n": n} for n in range(300)], "x" * 20_000],
        ids=["deep", "many-parts", "long"],
    )
    def test_the_same_from_extract_is_reported(self, value: Any) -> None:
        graph = build(fields={"tree": True}, extract=returning({"tree": value}))

        with pytest.raises(graphs.GraphError, match="at most 256 parts, 64 levels and 16384"):
            graph.invoke({"message": ""})

    def test_an_extracted_value_that_holds_itself_is_reported(self) -> None:
        looped: list[Any] = []
        looped.append(looped)
        graph = build(fields={"tree": True}, extract=returning({"tree": looped}))

        with pytest.raises(graphs.GraphError, match="at most 256 parts"):
            graph.invoke({"message": ""})

    @pytest.mark.parametrize("pattern", [r"\s+$", "^a*a*a*$"], ids=["trailing", "cubic"])
    def test_text_longer_than_max_length_never_reaches_the_pattern(self, pattern: str) -> None:
        # Unbounded, these took 1.2 s on 16,000 spaces and 5.2 s on 1,600 characters.
        schema = {"type": "string", "pattern": pattern, "maxLength": 256}
        graph = build(fields={"note": schema}, extract=returning({}))
        text = " " * 15_999 + "x" if pattern.startswith("\\") else "a" * 16_000 + "b"

        result = graph.invoke({"message": "", "collected": {"note": text}})

        assert result["missing"] == ["note"]

    def test_a_collected_value_is_kept_as_the_json_that_was_checked(self) -> None:
        schema = {"type": "array", "items": {"type": "string"}, "maxItems": 3}
        graph = build(fields={"tags": schema}, extract=returning({}))

        result = graph.invoke({"message": "", "collected": {"tags": ("a",)}})

        assert result["collected"] == {"tags": ["a"]}


class TestYourOwnCheck:
    def test_a_value_your_check_accepts_is_kept(self) -> None:
        graph = graphs.collect(
            fields={"tree": True},
            extract=returning({"tree": {"kind": "leaf"}}),
            check=lambda field, value: value.get("kind") == "leaf",
        )

        result = graph.invoke({"message": ""})

        assert result["collected"] == {"tree": {"kind": "leaf"}}

    def test_a_value_your_check_refuses_is_dropped(self) -> None:
        graph = graphs.collect(
            fields={"tree": True},
            extract=returning({"tree": {"kind": "branch"}}),
            check=lambda field, value: value.get("kind") == "leaf",
        )

        result = graph.invoke({"message": ""})

        assert result["missing"] == ["tree"]

    def test_a_value_that_came_in_collected_is_checked_too(self) -> None:
        graph = graphs.collect(
            fields={"tree": True},
            extract=returning({}),
            check=lambda field, value: value.get("kind") == "leaf",
        )

        result = graph.invoke({"message": "", "collected": {"tree": {"kind": "branch"}}})

        assert result["missing"] == ["tree"]

    def test_it_is_given_the_field_and_the_plain_value(self) -> None:
        seen: list[tuple[str, Any]] = []
        graph = graphs.collect(
            fields={"tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
            extract=returning({"tags": ("a",)}),
            check=lambda field, value: seen.append((field, value)) is None,
        )

        graph.invoke({"message": ""})

        assert seen == [("tags", ["a"])]

    def test_it_is_not_asked_about_a_value_the_shape_refused(self) -> None:
        seen: list[Any] = []
        graph = graphs.collect(
            fields={"n": {"type": "integer"}},
            extract=returning({"n": "seven"}),
            check=lambda field, value: seen.append(value) is None,
        )

        graph.invoke({"message": ""})

        assert seen == []

    def test_without_a_check_a_true_field_keeps_whatever_it_is_given(self) -> None:
        graph = graphs.collect(fields={"tree": True}, extract=returning({"tree": "anything"}))

        result = graph.invoke({"message": ""})

        assert result["collected"] == {"tree": "anything"}

    async def test_an_async_check_runs_under_the_async_entry_point(self) -> None:
        async def leaf(field: str, value: Any) -> bool:
            return value == "leaf"

        graph = graphs.collect(
            fields={"tree": True}, extract=returning({"tree": "leaf"}), check=leaf
        )

        result = await graph.ainvoke({"message": ""})

        assert result["collected"] == {"tree": "leaf"}

    def test_a_kept_value_is_checked_again_when_a_paused_run_resumes(self) -> None:
        seen: list[tuple[str, Any]] = []
        graph = graphs.collect(
            fields={"a": True, "b": True},
            extract=pairs,
            check=lambda field, value: seen.append((field, value)) is None,
            interrupt=True,
        )
        host = host_around(graph, KEYS, saved=True)
        host.invoke({"message": "a=1"}, THREAD)

        host.invoke(resume("b=2"), THREAD)

        assert seen == [("a", "1"), ("a", "1"), ("b", "2")]

    def test_a_check_that_returns_no_bool_is_reported(self) -> None:
        graph = graphs.collect(
            fields={"tree": True}, extract=returning({"tree": 1}), check=lambda field, value: 1
        )

        with pytest.raises(graphs.GraphError, match="check must return True or False"):
            graph.invoke({"message": ""})

    def test_an_error_in_your_check_ends_the_run(self) -> None:
        def broken(field: str, value: Any) -> bool:
            raise LookupError("service down")

        graph = graphs.collect(fields={"tree": True}, extract=returning({"tree": 1}), check=broken)

        with pytest.raises(LookupError, match="service down"):
            graph.invoke({"message": ""})


class TestWhatExtractIsGiven:
    def test_it_is_asked_only_for_the_fields_still_missing(self) -> None:
        seen: list[dict[str, Any]] = []

        def record(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            seen.append(schema)
            return {}

        build(extract=record).invoke({"message": "", "collected": {"order_id": "A-1"}})

        assert seen == [
            {
                "type": "object",
                "properties": {"email": FIELDS["email"]},
                "additionalProperties": False,
            }
        ]

    def test_it_is_not_called_when_nothing_is_missing(self) -> None:
        calls: list[str] = []

        def record(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            calls.append(message)
            return {}

        build(extract=record).invoke({"message": "hi", "collected": BOTH})

        assert calls == []

    def test_changing_the_schema_it_was_given_does_not_change_the_graph(self) -> None:
        def loosen(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            schema["properties"]["order_id"]["pattern"] = ".*"
            return {}

        graph = build(
            extract=lambda message, schema: loosen(message, schema) or pairs(message, schema)
        )
        graph.invoke({"message": ""})

        result = graph.invoke({"message": "order_id=B-1"})

        assert result["missing"] == ["order_id", "email"]

    def test_changing_the_fields_after_building_does_not_change_the_graph(self) -> None:
        fields = copy.deepcopy(FIELDS)
        graph = build(fields=fields)
        fields["order_id"]["pattern"] = ".*"

        result = graph.invoke({"message": "order_id=B-1"})

        assert result["missing"] == ["order_id", "email"]


class TestAsyncExtract:
    async def test_an_async_extract_runs_under_the_async_entry_point(self) -> None:
        async def extract(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(0)
            return pairs(message, schema)

        result = await build(extract=extract).ainvoke({"message": "order_id=A-1 email=a@b.c"})

        assert result == {"collected": BOTH, "missing": []}

    def test_an_async_extract_under_the_sync_entry_point_says_which_to_use(self) -> None:
        async def extract(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {}

        with pytest.raises(TypeError, match="run it with ainvoke or astream"):
            build(extract=extract).invoke({"message": ""})


class TestPausing:
    def test_without_interrupt_it_ends_the_run_with_fields_missing(self) -> None:
        host = host_around(build(), KEYS, saved=True)

        host.invoke({"message": "order_id=A-1"}, THREAD)

        assert pending(host) == []

    def test_it_asks_again_until_every_field_is_given(self) -> None:
        host = host_around(build(interrupt=True), KEYS, saved=True)
        host.invoke({"message": "hello"}, THREAD)
        host.invoke(resume("order_id=A-1"), THREAD)

        result = host.invoke(resume("email=a@b.c"), THREAD)

        assert result == {"message": "hello", "collected": BOTH, "missing": []}

    def test_a_second_pause_asks_only_for_what_is_still_missing(self) -> None:
        host = host_around(build(interrupt=True), KEYS, saved=True)
        host.invoke({"message": "hello"}, THREAD)

        host.invoke(resume("order_id=A-1"), THREAD)

        assert pending(host) == [{"missing": ["email"]}]

    def test_a_later_run_on_the_same_thread_reads_its_own_message(self) -> None:
        seen: list[str] = []

        def record(message: str, schema: dict[str, Any]) -> dict[str, Any]:
            seen.append(message)
            return pairs(message, schema)

        graph = saving(build(extract=record, interrupt=True))
        graph.invoke({"message": "order_id=A-1"}, THREAD)
        graph.invoke(resume("email=a@b.c"), THREAD)

        graph.invoke({"message": "new turn", "collected": {}}, THREAD)

        assert seen == ["order_id=A-1", "email=a@b.c", "new turn"]

    def test_run_on_its_own_it_hands_back_what_it_waits_for(self) -> None:
        result = build(interrupt=True).invoke({"message": "order_id=A-1"})

        assert [waiting.value for waiting in result["__interrupt__"]] == [{"missing": ["email"]}]

    def test_an_answer_that_is_not_text_is_refused(self) -> None:
        host = host_around(build(interrupt=True), KEYS, saved=True)
        host.invoke({"message": "hello"}, THREAD)

        with pytest.raises(
            graphs.GraphError, match="resume this graph with the user's answer as text"
        ):
            host.invoke(resume({"order_id": "A-1"}), THREAD)


class TestBadArguments:
    def test_no_fields_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fields must name at least one field"):
            build(fields={})

    def test_a_field_name_that_is_not_text_is_refused(self) -> None:
        with pytest.raises(ValueError, match="every field name must be a string"):
            build(fields={1: {"type": "string"}})

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "string", "minLength": -1},
            {"type": "string", "pattern": object()},
            {"minimum": float("nan")},
        ],
        ids=["negative-length", "not-json", "nan"],
    )
    def test_a_schema_that_is_not_valid_json_schema_is_refused(self, schema: Any) -> None:
        with pytest.raises(
            ValueError,
            match="the schema of field 'order_id' is refused: it is not a valid JSON Schema",
        ):
            build(fields={"order_id": schema})

    @pytest.mark.parametrize("found", [None, ["order_id", "A-1"], "order_id=A-1"])
    def test_an_extract_that_returns_no_mapping_is_reported(self, found: Any) -> None:
        with pytest.raises(graphs.GraphError, match="must return a mapping"):
            build(extract=returning(found)).invoke({"message": ""})

    @pytest.mark.parametrize(
        "value",
        [datetime.date(2026, 11, 14), float("nan"), object()],
        ids=["date", "nan", "object"],
    )
    def test_an_extracted_value_with_no_json_form_is_reported(self, value: Any) -> None:
        graph = build(extract=returning({"order_id": value}))

        with pytest.raises(graphs.GraphError, match="values that can be written as JSON"):
            graph.invoke({"message": ""})

    def test_the_error_does_not_repeat_the_message(self) -> None:
        with pytest.raises(graphs.GraphError) as raised:
            build(extract=returning("private-message-text")).invoke(
                {"message": "private-message-text"}
            )

        assert "private-message-text" not in str(raised.value)

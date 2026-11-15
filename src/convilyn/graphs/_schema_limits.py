"""The field schemas ``collect`` admits, and the limits on values a model writes.

Every graph that keeps or passes on a value a caller's function returned
measures it here, with :func:`written`, so there is one set of value limits.

A field's value is written by a model or by an agent, and the time a JSON
Schema check takes depends on the value as much as on the schema. Admitting
any JSON Schema and bounding that time did not converge: references, cycles,
other dialects, ``unevaluated*``, choices that collect every error, pairwise
``uniqueItems``, the size of the text in each error and the regular-expression
cache each opened a new way to make one check take seconds. So a field's
schema is one of a closed set of shapes, checked by an allowlist when the graph
is built:

* a scalar: ``type`` ``string``, ``integer``, ``number``, ``boolean`` or
  ``null`` (or a list of them), with only the keywords that type reads;
* ``enum`` or ``const`` of scalars;
* an ``array`` of one scalar shape, with ``maxItems``;
* an ``object`` whose ``properties`` are scalar shapes, with ``required`` and,
  to refuse other keys, ``additionalProperties: false``;
* ``true`` or a schema of annotations only, which takes any value.

``title``, ``description`` and ``examples`` are allowed everywhere. Anything
else is refused, naming the keyword. A value is measured before it is checked:
at most ``VALUE_PARTS`` parts, ``VALUE_DEPTH`` levels and ``VALUE_BYTES`` bytes
as JSON.

What is proven: checking a value is one pass over its parts. Each part is
checked against one scalar shape: a fixed set of keywords, an ``enum`` of at
most ``ENUM_VALUES`` values, one ``pattern``. ``uniqueItems`` compares at most
``VALUE_PARTS`` items pairwise. Nothing is followed, and nothing is checked twice.
A ``pattern`` is your regular expression and its cost is yours, but it only
ever reads text of at most ``PATTERN_TEXT`` characters: a shape with a
``pattern`` needs ``maxLength``, and every shape is rebuilt so its length and
range keywords are checked first, and a check stops at the first that fails.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

#: How many parts (lists, objects and the values in them) a value may have.
#: ``uniqueItems`` compares items pairwise: 4,000 small objects took 24 s.
VALUE_PARTS = 256
#: How deep a value may go, in lists and objects. It is written as JSON before
#: it is checked, and that goes one call deeper per level.
VALUE_DEPTH = 64
#: How long a value may be, written as JSON. Every error the check records
#: holds the value's text: 255 strings of 20 KB took 15.6 s.
VALUE_BYTES = 16_384
#: How many properties an object shape may declare. Each may carry a
#: ``pattern``; past 512 distinct patterns in one schema every match compiles
#: its pattern again (600 took 5.1 s).
PROPERTIES = 64
#: The longest text a ``pattern`` may read, as the ``maxLength`` it needs.
#: Patterns with no nested repetition still slow down with length: ``\s+$``
#: took 1.2 s on 16,000 spaces, ``^a*a*a*$`` 5.2 s on 1,600 characters.
PATTERN_TEXT = 256
#: The largest whole number ``multipleOf`` may be; beyond it, checking a
#: fractional value against it overflows.
MULTIPLE_OF = 2**53
#: How many values an ``enum`` may list.
ENUM_VALUES = 256

#: The value limits, as an error message says them.
WITHIN = f"at most {VALUE_PARTS} parts, {VALUE_DEPTH} levels and {VALUE_BYTES} bytes as JSON"

#: A sentence every refusal ends with: how to keep a field the shapes cannot express.
WAY_OUT = (
    "a field these shapes cannot express takes the schema true and is checked by your "
    "own check function"
)

_ANNOTATIONS = frozenset({"title", "description", "examples"})
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})
_BY_TYPE: dict[str, frozenset[str]] = {
    "string": frozenset({"minLength", "maxLength", "pattern", "format"}),
    "integer": frozenset(
        {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    ),
    "number": frozenset(
        {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    ),
    "boolean": frozenset(),
    "null": frozenset(),
}
_CHOICES = frozenset({"enum", "const"})
_ARRAY = frozenset({"type", "items", "maxItems", "minItems", "uniqueItems"})
_OBJECT = frozenset({"type", "properties", "required", "additionalProperties"})
#: How deep a schema may be written: an object's property is three levels down.
_SCHEMA_DEPTH = 8
#: Room for the largest admitted shape: every property listing a full ``enum``.
_SCHEMA_PARTS = PROPERTIES * (ENUM_VALUES + 32)
#: How long a schema may be, written as JSON.
_SCHEMA_BYTES = 1_048_576
#: Keywords checked first, in this order: they are cheap, and a value that
#: fails one is not read any further. ``pattern`` is checked last.
_FIRST = (
    "type",
    "enum",
    "const",
    "maxLength",
    "minLength",
    "maxItems",
    "minItems",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "multipleOf",
)
_LAST = ("pattern",)


class RefusedError(Exception):
    """A schema is outside the admitted shapes; ``reason`` names what, never its text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _within(value: object, depth_limit: int, parts_limit: int) -> bool:
    """Whether ``value`` has at most ``parts_limit`` parts, none deeper than ``depth_limit``.

    Counted without recursing, and stopped at the first limit, so a value of
    any size, or one that holds itself, costs at most ``parts_limit`` steps.
    """
    waiting: list[tuple[object, int]] = [(value, 0)]
    counted = 1
    while waiting:
        item, depth = waiting.pop()
        if isinstance(item, Mapping | list | tuple):
            if depth == depth_limit or counted + len(item) > parts_limit:
                return False
            inside = item.values() if isinstance(item, Mapping) else item
            counted += len(item)
            waiting += [(part, depth + 1) for part in inside]
    return True


def written(value: object) -> str | None:
    """``value`` written as JSON when it is within the value limits; ``None`` otherwise.

    The parts and levels are counted before anything is written, so a value of
    any size costs at most ``VALUE_PARTS`` steps to refuse. Raises
    ``TypeError`` or ``ValueError`` for a value JSON cannot hold.
    """
    if not _within(value, VALUE_DEPTH, VALUE_PARTS):
        return None
    text = json.dumps(value, allow_nan=False)
    return text if len(text.encode("utf-8")) <= VALUE_BYTES else None


def _not_admitted(keyword: str) -> RefusedError:
    return RefusedError(f"{keyword!r} is not admitted here")


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _types(schema: Mapping[str, Any]) -> list[str]:
    declared = schema.get("type", [])
    named = [declared] if isinstance(declared, str) else declared
    if not isinstance(named, list) or not all(
        isinstance(name, str) and name in _SCALAR_TYPES for name in named
    ):
        raise _not_admitted("type")
    return named


def _check_choices(schema: Mapping[str, Any]) -> None:
    if "enum" in schema:
        listed = schema["enum"]
        if not isinstance(listed, list) or len(listed) > ENUM_VALUES:
            raise RefusedError(f"'enum' may list at most {ENUM_VALUES} values")
        if not all(_scalar(item) for item in listed):
            raise RefusedError("'enum' may list only strings, numbers, booleans and null")
    if "const" in schema and not _scalar(schema["const"]):
        raise RefusedError("'const' may be only a string, number, boolean or null")


def _check_scalar(schema: object) -> None:
    """A scalar shape: ``true``, annotations, choices, or a scalar ``type`` with its keywords."""
    if not isinstance(schema, Mapping):
        # true or false, the only other schemas the schema check lets through: no keywords.
        return
    allowed = _ANNOTATIONS | _CHOICES | {"type"}
    for name in _types(schema):
        allowed |= _BY_TYPE[name]
    for keyword in schema:
        if keyword not in allowed:
            raise _not_admitted(keyword)
    divisor = schema.get("multipleOf")
    if isinstance(divisor, float) or (isinstance(divisor, int) and divisor > MULTIPLE_OF):
        # Dividing by a fraction, or into one by a huge whole number, overflows inside the check.
        raise RefusedError(f"'multipleOf' must be a whole number up to {MULTIPLE_OF}")
    if "pattern" in schema and not (
        isinstance(schema.get("maxLength"), int) and schema["maxLength"] <= PATTERN_TEXT
    ):
        raise RefusedError(f"a 'pattern' needs 'maxLength', at most {PATTERN_TEXT}")
    _check_choices(schema)


def _check_array(schema: Mapping[str, Any]) -> None:
    for keyword in schema:
        if keyword not in _ARRAY | _ANNOTATIONS:
            raise _not_admitted(keyword)
    if not isinstance(schema.get("maxItems"), int) or schema["maxItems"] > VALUE_PARTS:
        raise RefusedError(f"an array needs 'maxItems', at most {VALUE_PARTS}")
    if "items" in schema:
        _check_scalar(schema["items"])


def _check_object(schema: Mapping[str, Any]) -> None:
    for keyword in schema:
        if keyword not in _OBJECT | _ANNOTATIONS:
            raise _not_admitted(keyword)
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping) or len(properties) > PROPERTIES:
        raise RefusedError(f"an object may declare at most {PROPERTIES} properties")
    for inner in properties.values():
        _check_scalar(inner)
    if schema.get("additionalProperties", False) is not False:
        raise RefusedError("'additionalProperties' may only be false")


def _ordered(schema: Any) -> Any:
    """``schema`` with the cheap keywords first and ``pattern`` last, at every level."""
    if not isinstance(schema, dict):
        return schema
    keys = [key for key in _FIRST if key in schema]
    keys += [key for key in schema if key not in _FIRST and key not in _LAST]
    keys += [key for key in _LAST if key in schema]
    ordered = {key: schema[key] for key in keys}
    if isinstance(ordered.get("items"), dict):
        ordered["items"] = _ordered(ordered["items"])
    if isinstance(ordered.get("properties"), dict):
        ordered["properties"] = {
            name: _ordered(inner) for name, inner in ordered["properties"].items()
        }
    return ordered


def admit(schema: object) -> Draft202012Validator:
    """A validator for ``schema``, or :class:`RefusedError` naming what is not admitted.

    The cheap checks come first, so a schema that is not admitted costs little
    to refuse: its size, then the allowlist, then the full schema check.
    """
    if not _within(schema, _SCHEMA_DEPTH, _SCHEMA_PARTS):
        raise RefusedError("it is larger or deeper than any admitted shape")
    try:
        text = json.dumps(schema, allow_nan=False)
    except (TypeError, ValueError):
        raise RefusedError("it is not a valid JSON Schema") from None
    if len(text.encode("utf-8")) > _SCHEMA_BYTES:
        raise RefusedError("it is larger or deeper than any admitted shape")
    plain = json.loads(text)
    kind = plain.get("type") if isinstance(plain, dict) else None
    if kind == "array":
        _check_array(plain)
    elif kind == "object":
        _check_object(plain)
    else:
        _check_scalar(plain)
    try:
        Draft202012Validator.check_schema(plain)
    except SchemaError:
        raise RefusedError("it is not a valid JSON Schema") from None
    # Nothing to resolve: no admitted shape refers to anything.
    return Draft202012Validator(_ordered(plain), registry=Registry())

"""An advertised `outputSchema` must describe what the tool actually sends.

This exists because it didn't, and the gap was invisible from inside the server.
`quota` published a schema derived from `CostEstimate`, whose camelCase aliases
made the library advertise `estimatedMicroU` while `_plain()`'s
`model_dump(mode="json")` sent `estimated_micro_u`. Every client that validates
output schemas — including MCP's own — raised
`'estimatedMicroU' is a required property`, on every call, with no input that
passed.

Nothing in the suite could see it, and that was structural rather than an
oversight:

* the other MCP tests drive `build_server()` in-process, which exercises only
  the server-side branch — and that branch validates *without* the alias flags
  and with `populate_by_name=True`, so it passes;
* the one assertion about schema content was parametrised on `convert` alone,
  and would have passed for `quota` anyway, because the mismatch is a level
  down inside `$defs`.

So this test deliberately does the one thing those cannot: it takes the schema
the server PUBLISHES and the payload the server SENDS and checks them against
each other with a real JSON Schema validator, the way a client does.
"""

from __future__ import annotations

import pytest

jsonschema = pytest.importorskip("jsonschema")

from convilyn.mcp.server import build_server  # noqa: E402


async def _tools():
    return await build_server().list_tools()


class TestEveryPublishedSchemaMatchesItsPayload:
    async def test_convert_validates_against_its_own_schema(self, tmp_path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("hello", encoding="utf-8")
        server = build_server()

        tool = next(t for t in await server.list_tools() if t.name == "convert")
        result = await server.call_tool("convert", {"paths": [str(source)], "to": "md"})

        jsonschema.validate(result.structured_content, tool.output_schema)

    async def test_a_partial_batch_validates_too(self, tmp_path) -> None:
        """The payload most likely to drift, and the one `isError` used to
        exempt from validation entirely."""
        good = tmp_path / "a.txt"
        good.write_text("hello", encoding="utf-8")
        unroutable = tmp_path / "b.nope"
        unroutable.write_text("x", encoding="utf-8")
        server = build_server()

        tool = next(t for t in await server.list_tools() if t.name == "convert")
        result = await server.call_tool(
            "convert", {"paths": [str(good), str(unroutable)], "to": "md"}
        )

        jsonschema.validate(result.structured_content, tool.output_schema)

    async def test_no_tool_publishes_a_schema_it_cannot_satisfy(self, tmp_path) -> None:
        """The general form. A tool that advertises a schema and sends something
        else is broken for every validating client, so the pairing is checked
        for whichever tools publish one — not a hardcoded list that a new tool
        would quietly escape."""
        source = tmp_path / "a.txt"
        source.write_text("hello", encoding="utf-8")
        calls = {
            "convert": {"paths": [str(source)], "to": "md"},
            "capabilities": {},
            "pdf": {"operation": "info", "source": str(source)},
        }
        server = build_server()

        for tool in await server.list_tools():
            if tool.output_schema is None or tool.name not in calls:
                continue
            result = await server.call_tool(tool.name, calls[tool.name])
            if result.is_error:
                continue
            jsonschema.validate(result.structured_content, tool.output_schema)


class TestANetworkToolIsCoveredToo:
    """The general check above can only call tools that work offline, so a
    hosted tool would escape it — which is exactly what `quota` did.

    This closes that hole without a network call: it builds the payload the way
    `tools.quota` builds it, from a real `CostEstimate` parsed from a real
    camelCase cost-preview body, and validates it against whatever schema the
    tool publishes. Vacuously true while `quota` publishes none, and the moment
    anyone reinstates one derived from an alias-bearing model, red.
    """

    async def test_quotas_real_payload_would_satisfy_any_schema_it_published(self) -> None:
        from convilyn.mcp import tools as tool_impl
        from convilyn.types import CostEstimate

        wire = {
            "estimatedMicroU": 1_000_000,
            "estimatedUsd": 1.0,
            "estimatedTotalMicroU": 1_000_000,
            "estimatedMinMicroU": 1_000_000,
            "estimatedMaxMicroU": 1_000_000,
            "tools": [],
            "quotaCheck": {
                "state": "ok",
                "tier": "pro",
                "estimatedMicroU": 1_000_000,
                "thresholdMicroU": 5_000_000,
                "upgradeUrl": None,
            },
        }
        payload = {"ok": True, "estimate": tool_impl._plain(CostEstimate.model_validate(wire))}

        tool = next(t for t in await _tools() if t.name == "quota")
        if tool.output_schema is not None:
            jsonschema.validate(payload, tool.output_schema)


class TestTheScanIsNotVacuous:
    async def test_at_least_one_tool_publishes_a_schema(self) -> None:
        """Without this, every assertion above passes on a catalogue that
        advertises nothing — which is exactly the state the broken version
        would be reduced to by "fixing" it with a blanket removal."""
        assert any(t.output_schema is not None for t in await _tools())

    async def test_the_schema_is_not_the_vacuous_one(self) -> None:
        """`{"type": "object", "additionalProperties": true}` validates every
        object and describes none. It is what these schemas replaced, so a
        regression to it must not read as success."""
        tool = next(t for t in await _tools() if t.name == "convert")
        assert set(tool.output_schema["properties"]) >= {"ok", "converted", "results"}


class TestQuotaPublishesNoSchemaOnPurpose:
    async def test_quota_has_no_output_schema(self) -> None:
        """Pinned so it reads as a decision. `CostEstimate` carries camelCase
        aliases; publishing a schema derived from it crashed every validating
        client. See `_QUOTA_HAS_NO_OUTPUT_SCHEMA` for why neither available fix
        was worth the contract."""
        tool = next(t for t in await _tools() if t.name == "quota")
        assert tool.output_schema is None

    async def test_understand_has_no_output_schema(self) -> None:
        tool = next(t for t in await _tools() if t.name == "understand")
        assert tool.output_schema is None

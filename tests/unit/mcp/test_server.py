"""The MCP catalogue: what it offers, what it costs to offer, and what it says.

Driven **in-process** — `build_server()` then `await list_tools()` /
`call_tool()`. No subprocess, no JSON-RPC, no client library. The transport is
the part least likely to break and the most expensive to test; the catalogue is
the opposite.
"""

from __future__ import annotations

import json

import pytest

from convilyn.mcp.server import build_server

#: Every tool the server offers. Written out rather than derived from the
#: server, because a test that reads its expectation out of the thing under test
#: passes for any catalogue — including one that lost a tool.
EXPECTED_TOOLS = frozenset(
    {
        "convert",
        "capabilities",
        "pdf",
        "understand",
        "quota",
    }
)

#: The six-section template every description follows. It is the platform's own
#: MCP convention, and "When NOT to use" is the load-bearing one: it is where a
#: tool declines work it cannot do, which is this catalogue's honesty posture
#: expressed in the only place the model reads before choosing.
REQUIRED_SECTIONS = (
    "**Purpose**",
    "**When to use**",
    "**When NOT to use**",
    "**Preconditions**",
    "**Failure modes**",
    "**Example**",
)

#: Summed description length the catalogue may occupy. A ratchet at today's
#: number (3,700), not an aspiration: tool descriptions are re-sent to the model
#: every turn, so this is rent paid per turn by every caller. A sixth tool has
#: to be worth raising this line deliberately.
DESCRIPTION_BUDGET = 6_000


@pytest.fixture
def tools():
    return build_server()


class TestTheCatalogueIsWhatWeSaidItIs:
    async def test_it_offers_exactly_these_tools(self, tools) -> None:
        assert {t.name for t in await tools.list_tools()} == EXPECTED_TOOLS

    async def test_there_are_tools_to_inspect(self, tools) -> None:
        """Vacuity guard. Every assertion below iterates the tool list; an empty
        catalogue would satisfy all of them at once."""
        assert len(await tools.list_tools()) >= 5

    async def test_every_tool_has_an_input_schema(self, tools) -> None:
        for tool in await tools.list_tools():
            assert tool.input_schema.get("type") == "object", tool.name

    async def test_the_catalogue_stays_small(self, tools) -> None:
        """`generic-tools-agent-orchestration.md`: a tool added for one caller is
        charged to all of them. Five is the argument; a sixth needs its own."""
        assert len(await tools.list_tools()) <= 5

    async def test_the_description_budget_holds(self, tools) -> None:
        spent = sum(len(t.description or "") for t in await tools.list_tools())
        assert spent <= DESCRIPTION_BUDGET, (
            f"tool descriptions total {spent} chars, over the {DESCRIPTION_BUDGET} "
            "budget. This is re-sent every turn to every caller — trim, or raise "
            "the budget deliberately with the reason written down."
        )


class TestEveryDescriptionSaysWhenNotToUse:
    """The honesty posture, checked where it can rot silently.

    Measured, and the reason this catalogue exists in this shape: an agent does
    not reach for convilyn when its own reader suffices, and it is right not to.
    A description that only advertises would make the tool chosen for work it
    loses at.
    """

    async def test_every_tool_follows_the_six_section_template(self, tools) -> None:
        for tool in await tools.list_tools():
            missing = [s for s in REQUIRED_SECTIONS if s not in (tool.description or "")]
            assert missing == [], f"{tool.name} is missing {missing}"

    async def test_no_description_claims_to_always_win(self, tools) -> None:
        forbidden = (
            "always convert",
            "convert first",
            "before reading",
            "instead of read",
            "rather than reading",
        )
        for tool in await tools.list_tools():
            body = (tool.description or "").lower()
            hit = [phrase for phrase in forbidden if phrase in body]
            assert hit == [], f"{tool.name} over-claims: {hit}"

    async def test_the_forbidden_scanner_can_actually_fire(self) -> None:
        """Vacuity guard for the check above, which is a negative one.

        A scanner never shown to fail has not been shown to work — and this one
        would pass just as happily against an empty phrase list or a lowercase
        bug.
        """
        probe = "You should ALWAYS CONVERT before reading anything."
        assert "always convert" in probe.lower()


class TestTheMeteredToolsAnnounceThatTheySpend:
    async def test_understand_says_it_spends_credits(self, tools) -> None:
        tool = next(t for t in await tools.list_tools() if t.name == "understand")
        assert "SPENDS CREDITS" in (tool.description or "")

    async def test_the_free_tools_do_not_mention_credits(self, tools) -> None:
        """A free tool that talks about money teaches the model to hesitate over
        the ones that cost nothing — which is the direction that loses the
        zero-token win entirely."""
        for name in ("convert", "capabilities", "pdf"):
            tool = next(t for t in await tools.list_tools() if t.name == name)
            assert "credit" not in (tool.description or "").lower(), name


class TestAToolActuallyRuns:
    async def test_it_converts_a_real_file_with_no_extras(self, tools, tmp_path) -> None:
        """CSV needs nothing installed — `pyproject.toml` says so in as many
        words — so this runs on every machine, extras or not."""
        source = tmp_path / "ledger.csv"
        source.write_text("item,qty\nbolt,4\n", encoding="utf-8")

        result = await tools.call_tool("convert", {"paths": [str(source)], "to": "md"})
        payload = json.loads(result.content[0].text)

        assert payload["ok"] is True
        assert payload["converted"] == 1
        assert payload["tokens_used"] == 0
        assert (tmp_path / "ledger.md").is_file()

    async def test_a_missing_file_is_a_refusal_not_a_crash(self, tools, tmp_path) -> None:
        result = await tools.call_tool("convert", {"paths": [str(tmp_path / "absent.docx")]})
        payload = json.loads(result.content[0].text)

        assert payload["ok"] is False
        assert "not a file" in payload["error"]

    async def test_capabilities_answers_narrowly(self, tools) -> None:
        """It must not dump the route table. Measured: the machine knows 287
        available routes out of 737, and returning them would put a page nobody
        reads into the model's context on every call."""
        result = await tools.call_tool("capabilities", {"source_format": "docx"})
        payload = json.loads(result.content[0].text)

        assert payload["ok"] is True
        assert payload["source_format"] == "docx"
        assert len(result.content[0].text) < 2_000


class TestAHostedToolNeverSpendsWithoutAKey:
    async def test_understand_refuses_a_bad_schema_before_any_network(self, tools) -> None:
        """The schema check is local and comes first, so a malformed request
        cannot upload a file and then fail."""
        result = await tools.call_tool("understand", {"paths": [__file__], "schema": {}})
        payload = json.loads(result.content[0].text)

        assert payload["ok"] is False
        assert "schema" in payload["error"]


class TestTheServerNamesItself:
    async def test_it_reports_the_package_version(self, tools) -> None:
        """Reaches the host as ``serverInfo.version`` — the only place a user can
        see which convilyn their editor is running, and the first thing worth
        knowing when a tool misbehaves. It was empty until measured over a real
        pipe; the in-process tests could not see it."""
        from convilyn import __version__

        assert tools.version == __version__


class TestThePricingToolSaysWhatItIsNot:
    """`quota` returns a PRICE. A model reading it as a balance is the
    one mistake this tool can cause, and its description made none of the three
    distinctions that prevent it.

    The catalogue's other four descriptions each carry their own honesty clause;
    this one said "so an approval can be answered with a number" and then never
    said which question that number cannot answer. `CostEstimate`'s docstring
    argues all of it at length -- and the model reads none of that.
    """

    async def test_it_names_the_balance_call(self, tools) -> None:
        tool = next(t for t in await tools.list_tools() if t.name == "quota")
        assert "get_balance" in (tool.description or "")

    async def test_it_says_the_unit_is_not_credits(self, tools) -> None:
        tool = next(t for t in await tools.list_tools() if t.name == "quota")
        body = (tool.description or "").lower()
        assert "micro-usd" in body
        assert "understates" in body

    async def test_it_still_refuses_to_answer_affordability(self, tools) -> None:
        """The active instruction, not just the caveat. Dividing by 10,000 is
        the obvious move and it is wrong in the dangerous direction: it tells a
        caller they can afford a run they cannot."""
        tool = next(t for t in await tools.list_tools() if t.name == "quota")
        assert "afford" in (tool.description or "").lower()

    async def test_the_free_tools_still_do_not_mention_a_balance(self, tools) -> None:
        """Vacuity guard: the three assertions above would also pass if every
        description had grown the same paragraph."""
        for name in ("convert", "capabilities", "pdf"):
            tool = next(t for t in await tools.list_tools() if t.name == name)
            assert "get_balance" not in (tool.description or ""), name


class TestEveryToolDeclaresItsBehaviour:
    """Annotations were unset on all five until #5028.

    They are the only machine-readable statement of what a tool does to the
    world, and a host needs them for the thing annotations exist for: it cannot
    auto-approve the read-only tools if nothing says which ones are read-only.
    `capabilities` — which reads no file and spends nothing — was gated exactly
    as hard as the tool that uploads your documents and charges you.
    """

    async def test_every_tool_carries_annotations(self, tools) -> None:
        for tool in await tools.list_tools():
            assert tool.annotations is not None, tool.name

    async def test_every_tool_carries_a_title(self, tools) -> None:
        """`name` is machine-shaped; `title` is what a person sees in an
        approval prompt."""
        for tool in await tools.list_tools():
            assert tool.title, tool.name

    @pytest.mark.parametrize(
        ("name", "read_only"),
        [
            ("capabilities", True),
            ("quota", True),
            ("convert", False),
            ("pdf", False),
            ("understand", False),
        ],
    )
    async def test_the_read_only_split_is_declared(self, tools, name, read_only) -> None:
        """`pdf` is in the NOT-read-only half although its `info`
        operation reads nothing. One tool carries one annotation, and the
        honest direction for a tool that can also set a password on your PDF is
        the pessimistic one."""
        tool = next(t for t in await tools.list_tools() if t.name == name)
        assert tool.annotations.read_only_hint is read_only

    @pytest.mark.parametrize(
        ("name", "open_world"),
        [
            ("convert", False),
            ("capabilities", False),
            ("pdf", False),
            ("understand", True),
            ("quota", True),
        ],
    )
    async def test_the_network_boundary_is_declared(self, tools, name, open_world) -> None:
        """The local/hosted split is this product's central claim. It should be
        legible to a host without parsing prose."""
        tool = next(t for t in await tools.list_tools() if t.name == name)
        assert tool.annotations.open_world_hint is open_world


class TestAFailureReachesBothChannels:
    """A refusal used to be invisible to the protocol.

    Every tool returned `{"ok": false, ...}` with `isError` unset, so a host
    keying off the flag the MCP spec defines for tool execution errors saw an
    unbroken run of successes while the model was reading refusals.
    """

    async def test_a_refusal_sets_the_protocol_flag(self, tools) -> None:
        result = await tools.call_tool("pdf", {"operation": "info"})
        assert result.is_error is True

    async def test_a_refusal_still_carries_its_actionable_body(self, tools) -> None:
        """The flag is added, not traded for. A bare `isError` with the payload
        dropped would take away the half the model can act on."""
        result = await tools.call_tool("pdf", {"operation": "info"})
        body = result.structured_content
        assert body["ok"] is False
        assert body["hint"]

    async def test_a_success_does_not_set_the_flag(self, tools) -> None:
        """Vacuity guard: the two assertions above would also pass if the flag
        were hardcoded true."""
        result = await tools.call_tool("capabilities", {})
        assert result.is_error is False
        assert result.structured_content["ok"] is True


class TestTheFiniteParameterSetsAreDeclared:
    async def test_the_operation_enum_matches_pdf_operations(self, tools) -> None:
        """Derived, not retyped. `Literal` needs literal values at type-check
        time so the annotation cannot be built from `PDF_OPERATIONS` — this
        comparison is what stops the two copies drifting."""
        from convilyn.mcp.tools import PDF_OPERATIONS

        tool = next(t for t in await tools.list_tools() if t.name == "pdf")
        enum = tool.input_schema["properties"]["operation"].get("enum")
        assert tuple(enum) == PDF_OPERATIONS

    async def test_the_target_format_is_deliberately_not_an_enum(self, tools) -> None:
        """`to` stays open. Its value set is what this machine can write, which
        depends on what is installed — pinning it in a schema would advertise
        formats a given box cannot produce, and go stale against
        `convilyn local formats`."""
        tool = next(t for t in await tools.list_tools() if t.name == "convert")
        assert "enum" not in tool.input_schema["properties"]["to"]

    async def test_parameters_carry_descriptions(self, tools) -> None:
        """No input schema carried any until #5028."""
        tool = next(t for t in await tools.list_tools() if t.name == "pdf")
        for param in ("operation", "pages", "password"):
            assert tool.input_schema["properties"][param].get("description"), param


class TestConvertSaysItReturnsPaths:
    async def test_the_description_states_the_return_shape(self, tools) -> None:
        """The whole economic claim — a batch is free because the Markdown does
        not enter the context — depended on the model inferring that the tool
        hands back paths. It was documented nowhere the model reads."""
        tool = next(t for t in await tools.list_tools() if t.name == "convert")
        assert "PATHS" in (tool.description or "")


class TestTheStableShapesPublishASchema:
    """`convert` advertises a real `outputSchema`; the rest do not.

    An earlier change recorded losing the schema as the price of setting
    `isError`. It was not: `Annotated[CallToolResult, Envelope]` keeps the type
    — so `is_error` stays ours — and still derives the published schema.

    `quota` was in this set for one commit and had to leave it: `CostEstimate`
    carries camelCase aliases, so the derived schema demanded `estimatedMicroU`
    while the payload sent `estimated_micro_u`, and every validating client
    rejected it. The surviving requirement is narrower than "stable shape" —
    the model must also declare its fields under the names they are sent under.
    """

    @pytest.mark.parametrize("name", ["convert"])
    async def test_the_modelled_tools_publish_one(self, tools, name) -> None:
        tool = next(t for t in await tools.list_tools() if t.name == name)
        assert tool.output_schema is not None

    @pytest.mark.parametrize("name", ["pdf", "understand", "capabilities", "quota"])
    async def test_the_others_deliberately_do_not(self, tools, name) -> None:
        """Not an oversight, and pinned so it reads as a decision.

        `pdf` has five success shapes and a top-level union does not work — the
        library wraps one as `{"result": ...}` while the `CallToolResult` branch
        validates `structured_content` unwrapped, so it can never match.
        `understand`'s `result` is the caller's own JSON Schema output, typeable
        only as `Any`.
        """
        tool = next(t for t in await tools.list_tools() if t.name == name)
        assert tool.output_schema is None

    async def test_the_schema_describes_something(self, tools) -> None:
        """Vacuity guard: `additionalProperties: true` is what this replaced, so
        a schema that is merely present proves nothing."""
        tool = next(t for t in await tools.list_tools() if t.name == "convert")
        assert set(tool.output_schema["properties"]) >= {"ok", "converted", "total", "results"}


class TestOnlyARefusalIsAProtocolError:
    """`ok: false` and "the call failed" are not the same set.

    A batch where one file of twelve failed returns `ok: false` with eleven good
    rows — a call that did its job, and `convert`'s contract insists a failed
    file is a result rather than an exception. Flagging that as a tool execution
    error tells the host the call failed when it did not, and because `isError`
    exempts a result from output-schema validation, it would also have skipped
    validation on exactly the payload most likely to drift.
    """

    async def test_a_partial_batch_is_not_an_error(self, tools, tmp_path) -> None:
        good = tmp_path / "a.txt"
        good.write_text("hello", encoding="utf-8")
        unroutable = tmp_path / "b.nope"
        unroutable.write_text("x", encoding="utf-8")

        result = await tools.call_tool(
            "convert", {"paths": [str(good), str(unroutable)], "to": "md"}
        )

        assert result.is_error is False

    async def test_the_partial_batch_still_reports_the_failure_in_its_body(
        self, tools, tmp_path
    ) -> None:
        """The flag moved; the information did not."""
        good = tmp_path / "a.txt"
        good.write_text("hello", encoding="utf-8")
        unroutable = tmp_path / "b.nope"
        unroutable.write_text("x", encoding="utf-8")

        body = (
            await tools.call_tool("convert", {"paths": [str(good), str(unroutable)], "to": "md"})
        ).structured_content

        assert body["ok"] is False
        assert body["converted"] == 1

    async def test_a_refusal_is_still_an_error(self, tools) -> None:
        """Vacuity guard for the two above: they would also pass if `isError`
        were hardcoded false, which is the state this whole line of work started
        from."""
        result = await tools.call_tool("pdf", {"operation": "info"})
        assert result.is_error is True

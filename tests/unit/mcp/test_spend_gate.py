"""Nothing is uploaded or charged until a HUMAN says yes.

`understand` shipped with this gate written as prose in its own tool
description — *"Call `quota` first if the user has not already agreed
to spend"* — and nothing enforcing it. A tool description is input to the same
model the description is trying to constrain, so it is advice, not a gate.

**Why elicitation rather than a confirmation handshake.** The platform's own MCP
framework uses a two-call handshake (first call returns `needs_confirmation` plus
a token, second call with the token executes), and that is right for the agent
lane it protects. It is the wrong shape here: a model can simply make both calls
itself. `elicitation/create` is a protocol request the CLIENT must put in front
of a person, and the model cannot forge the reply.

Driven through fake sessions rather than a live client, for the same reason
`tools.py` avoids importing `mcp`: a test that has to stand up a real MCP
session to learn whether money was spent is a test most contributors will not
run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from convilyn.mcp import server


def _refusal(result: Any) -> dict[str, Any]:
    """Unwrap a refusal, asserting BOTH channels carry it.

    Tools return a ``CallToolResult`` now: a refusal keeps ``ok: false`` in the
    body and also sets ``isError`` on the wire. Reading only the body would let
    the protocol flag regress unnoticed, which is the defect the change fixed —
    so this helper checks both and hands back the body.
    """
    assert result.is_error is True
    body = result.structured_content
    assert body["ok"] is False
    return body


class _FakeSession:
    def __init__(self, *, capabilities: set[str], roots: list[str] | None = None) -> None:
        self._capabilities = capabilities
        self._roots = roots or []

    def check_client_capability(self, capability: Any) -> bool:
        if capability.elicitation is not None:
            return "elicitation" in self._capabilities
        if capability.roots is not None:
            return "roots" in self._capabilities
        return False  # pragma: no cover - only two are probed

    async def list_roots(self) -> Any:
        class _Root:
            def __init__(self, uri: str) -> None:
                self.uri = uri

        class _Result:
            def __init__(self, roots: list[_Root]) -> None:
                self.roots = roots

        return _Result([_Root(uri) for uri in self._roots])


class _FakeContext:
    """Only the two surfaces the gate touches: `session` and `elicit`."""

    def __init__(self, session: _FakeSession, *, answer: Any = None) -> None:
        self.session = session
        self._answer = answer
        self.elicit_calls: list[str] = []

    async def elicit(self, message: str, schema: type) -> Any:
        self.elicit_calls.append(message)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _Answer:
    def __init__(self, action: str, approve: bool | None = None) -> None:
        self.action = action
        self.data = None if approve is None else type("D", (), {"approve": approve})()


# ── the gate itself ──────────────────────────────────────────────────


class TestItAsksWhenItCan:
    """Vacuity guard: a gate that always refuses passes every refusal test."""

    async def test_an_approving_human_lets_it_through(self) -> None:
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation"}), answer=_Answer("accept", approve=True)
        )
        approved, denial = await server._approved_to_spend(ctx, "spend $1?")
        assert approved is True
        assert denial == ""

    async def test_the_person_is_actually_asked(self) -> None:
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation"}), answer=_Answer("accept", approve=True)
        )
        await server._approved_to_spend(ctx, "spend $1?")
        assert ctx.elicit_calls == ["spend $1?"]


class TestNoAnswerIsNotAYes:
    @pytest.mark.parametrize(
        "answer",
        [
            _Answer("decline"),
            _Answer("cancel"),
            _Answer("accept", approve=False),
        ],
        ids=["declined", "cancelled", "accepted-but-unticked"],
    )
    async def test_a_refusal_does_not_approve(self, answer: _Answer) -> None:
        ctx = _FakeContext(_FakeSession(capabilities={"elicitation"}), answer=answer)
        approved, denial = await server._approved_to_spend(ctx, "spend $1?")
        assert approved is False
        assert denial

    async def test_a_client_that_cannot_ask_is_refused_and_says_so(self) -> None:
        """Fail-closed. A client with no elicitation capability has no way to put
        the question to a person, and "nobody was asked" is not consent."""
        ctx = _FakeContext(_FakeSession(capabilities=set()))
        approved, denial = await server._approved_to_spend(ctx, "spend $1?")
        assert approved is False
        assert "cannot ask you to approve" in denial
        assert ctx.elicit_calls == []

    async def test_the_refusal_message_does_not_promise_a_cli_that_cannot_do_this(self) -> None:
        """`convilyn goals understand` takes already-uploaded file IDs and there
        is no upload command, so there is NO shell path for a local file (#4841).
        Naming it here would be advice that does not work — worse than none."""
        ctx = _FakeContext(_FakeSession(capabilities=set()))
        _, denial = await server._approved_to_spend(ctx, "spend $1?")
        assert "goals understand" not in denial

    async def test_an_elicitation_that_raises_is_a_refusal_not_a_crash(self) -> None:
        """`elicit_with_validation` RAISES when a client accepts with content that
        does not match the schema. Propagating it would break the package's
        "a tool returns, it does not raise" contract and would read as a crash
        rather than as the refusal it is."""
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation"}), answer=ValueError("bad content")
        )
        approved, denial = await server._approved_to_spend(ctx, "spend $1?")
        assert approved is False
        assert "could not obtain approval" in denial


# ── the prompt names no price, on purpose ────────────────────────────


class TestThePromptIsHonest:
    """These assertions replace one that pinned the defect in place.

    The old test fed `_spend_prompt` a hand-built `{"estimated_micro_u":
    1_250_000}` and asserted `"$1.25" in prompt`. That value cannot arise at the
    real call site: `tools.quota()` there is called with no arguments, prices an
    EMPTY Builder tool palette, and returns the same 1,000,000 µU every time —
    so the prompt read "about $1.00" on every call, for every account. The test
    verified a formatter against an input production never produces.
    """

    def test_it_shows_no_currency_figure(self) -> None:
        """The load-bearing one. A price here was fabricated three ways over:
        wrong operation, wrong unit, and no correction afterwards."""
        assert not re.search(r"\$\s*\d", server._spend_prompt(["/tmp/invoice.pdf"]))

    def test_it_says_the_amount_is_unknown(self) -> None:
        """An approval screen with no cost line reads as free, so the unknown is
        stated rather than omitted — the one thing the old prompt got right."""
        assert "not known" in server._spend_prompt(["/tmp/invoice.pdf"])

    def test_it_says_credits_are_spent(self) -> None:
        assert "SPEND CREDITS" in server._spend_prompt(["/tmp/invoice.pdf"])

    def test_it_says_the_files_leave_the_machine(self) -> None:
        prompt = server._spend_prompt(["/tmp/invoice.pdf"])
        assert "UPLOAD" in prompt
        assert "invoice.pdf" in prompt


class TestAllowedRoots:
    async def test_it_uses_the_roots_the_client_declared(self, tmp_path: Path) -> None:
        uri = tmp_path.as_uri()
        ctx = _FakeContext(_FakeSession(capabilities={"roots"}, roots=[uri]))
        assert await server._allowed_roots(ctx) == (tmp_path,)

    async def test_a_client_declaring_no_roots_falls_back_to_cwd(self) -> None:
        """Narrower than "anywhere", never wider: a client that cannot answer
        must not thereby unlock the whole filesystem."""
        ctx = _FakeContext(_FakeSession(capabilities=set()))
        assert await server._allowed_roots(ctx) == (Path.cwd(),)

    async def test_a_failing_list_roots_falls_back_rather_than_raising(self) -> None:
        session = _FakeSession(capabilities={"roots"})

        async def _boom() -> Any:
            raise RuntimeError("transport gone")

        session.list_roots = _boom  # type: ignore[method-assign]
        ctx = _FakeContext(session)
        assert await server._allowed_roots(ctx) == (Path.cwd(),)

    async def test_a_non_file_root_is_dropped_not_coerced(self, tmp_path: Path) -> None:
        ctx = _FakeContext(
            _FakeSession(capabilities={"roots"}, roots=["https://example.com/x", tmp_path.as_uri()])
        )
        assert await server._allowed_roots(ctx) == (tmp_path,)


# ── end to end through the real tool ─────────────────────────────────


class TestTheToolItselfDoesNotSpendWithoutApproval:
    """The tests above check the gate; these check that the TOOL is behind it.

    A correct gate wired past the call site protects nothing, and that is not a
    hypothetical failure mode — it is what the tool description alone amounted
    to before this change.
    """

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "invoice.pdf").write_bytes(b"%PDF-1.4\n")
        return tmp_path

    @pytest.fixture
    def never_spends(self, monkeypatch):
        """Makes any attempt to reach the platform an immediate failure."""
        from convilyn.mcp import tools

        def _explode(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the tool reached the network without approval")

        monkeypatch.setattr(tools, "_client", _explode)
        monkeypatch.setattr(tools, "quota", lambda *a, **k: {"ok": True, "estimate": {}})

    async def _call(self, ctx: _FakeContext, workspace: Path) -> dict:
        handler = _understand_handler()
        return await handler(
            ctx=ctx,
            paths=[str(workspace / "invoice.pdf")],
            schema={"type": "object", "properties": {"total": {"type": "number"}}},
        )

    async def test_a_declining_human_stops_the_upload(self, workspace, never_spends) -> None:
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation", "roots"}, roots=[workspace.as_uri()]),
            answer=_Answer("decline"),
        )
        result = await self._call(ctx, workspace)
        _refusal(result)

    async def test_a_client_without_elicitation_stops_the_upload(
        self, workspace, never_spends
    ) -> None:
        ctx = _FakeContext(
            _FakeSession(capabilities={"roots"}, roots=[workspace.as_uri()]),
            answer=_Answer("accept", approve=True),
        )
        result = await self._call(ctx, workspace)
        assert "cannot ask you to approve" in _refusal(result)["error"]

    async def test_a_bad_request_is_refused_before_anyone_is_asked(
        self, workspace, never_spends
    ) -> None:
        """Ordering, and the reason the precheck is split out: an approval prompt
        for a request that cannot succeed wastes the one thing this gate spends,
        which is the user's attention."""
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation", "roots"}, roots=[workspace.as_uri()]),
            answer=_Answer("accept", approve=True),
        )
        handler = _understand_handler()
        result = await handler(ctx=ctx, paths=[str(workspace / "invoice.pdf")], schema={})
        assert "schema" in _refusal(result)["error"]
        assert ctx.elicit_calls == []

    async def test_a_fenced_path_is_refused_before_anyone_is_asked(
        self, workspace, never_spends
    ) -> None:
        (workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation", "roots"}, roots=[workspace.as_uri()]),
            answer=_Answer("accept", approve=True),
        )
        handler = _understand_handler()
        result = await handler(ctx=ctx, paths=[str(workspace / ".env")], schema={"type": "object"})
        _refusal(result)
        assert ctx.elicit_calls == []

    async def test_understand_never_calls_quota(self, workspace, never_spends, monkeypatch) -> None:
        """`tools.quota()` was a blocking HTTP round-trip on the event loop to
        re-derive a constant that described a different operation. It is gone;
        this fails if anyone reinstates it."""

        from convilyn.mcp import tools

        def _explode(*args, **kwargs):
            raise AssertionError("understand must not price itself with a palette estimate")

        monkeypatch.setattr(tools, "quota", _explode)
        ctx = _FakeContext(
            _FakeSession(capabilities={"elicitation", "roots"}, roots=[workspace.as_uri()]),
            answer=_Answer("decline"),
        )
        handler = _understand_handler()

        result = await handler(
            ctx=ctx,
            paths=[str(workspace / "invoice.pdf")],
            schema={"type": "object", "properties": {"total": {"type": "number"}}},
        )

        _refusal(result)


def _understand_handler():
    """The registered `understand` function, pulled off a built server.

    Reached through the server rather than re-implemented here, so this exercises
    the same wiring the model gets — including that `ctx` is injected rather than
    accepted from the caller.
    """
    built = server.build_server()
    for tool in built._tool_manager.list_tools():  # noqa: SLF001 - test reaches in deliberately
        if tool.name == "understand":
            return tool.fn
    raise AssertionError("understand is not registered")  # pragma: no cover


# ── roots: the fence's input, and its fallback ───────────────────────

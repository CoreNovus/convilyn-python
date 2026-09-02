"""The stdio MCP server. The only module in this package that imports ``mcp``.

Nothing imports this at package scope — :mod:`convilyn.cli.mcp` imports it
inside the command body — so the ``mcp`` extra stays out of the base install and
out of the release pipeline's no-extras wheel smoke test.

**The package is named ``convilyn.mcp`` and that does not shadow the top-level
``mcp``.** Verified rather than assumed: from inside this package a bare
``import mcp`` raises ``ModuleNotFoundError`` when the extra is absent, which is
only possible if it resolved to the top-level name. Python 3 has no implicit
relative imports; the shadowing worry is a Python 2 reflex.

**The API is ``mcp.server.mcpserver.MCPServer``, not ``FastMCP``.** ``mcp`` 2.x
renamed it, and its own shim raises with the migration link. The one example of
this in the wider repo still uses the 1.x path — measured against the installed
2.1.1, that path does not exist. Pinned ``>=2.0`` for that reason: supporting
both would mean a compatibility branch, which P1 forbids, for an SDK nobody has
a 1.x lock on yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ClientCapabilities, ElicitationCapability, RootsCapability
from pydantic import BaseModel, Field

from convilyn import __version__
from convilyn.mcp import tools

#: Every tool description follows the six-section template the platform's own
#: MCP servers use. It is not decoration: a description is the only thing the
#: model reads before choosing, and "when NOT to use" is the half that stops a
#: tool being called for work it cannot do — which is this catalogue's whole
#: honesty posture, expressed where the model will actually see it.
_CONVERT_DESCRIPTION = """\
**Purpose** Convert documents to Markdown (or another format) on this machine.

**When to use** The file is a container rather than text — `.docx`, `.pptx`,
`.xlsx`, `.odt`, `.doc` are zip or OLE archives. Or several files at once: one
call converts twenty for the same zero tokens as one. Or the output has to be
byte-identical every run, because no model is in the path.

**When NOT to use** The file is already text you can read directly (`.md`,
`.txt`, `.csv`, source code) — reading it yourself is faster and costs the same
nothing. A single PDF page you only need to glance at is also faster read
directly.

**Preconditions** `pip install "convilyn[all]"`. Some formats need more —
`convilyn_capabilities` says which, and every failure names its own install
command.

**Failure modes** A missing library returns `ok: false` with a `hint` naming the
exact install command. One unreadable file in twenty does not fail the other
nineteen — each file gets its own result row.

**Example** `{"paths": ["report.docx", "q3.xlsx"], "to": "md"}`
"""

_CAPABILITIES_DESCRIPTION = """\
**Purpose** Say what this machine can convert, and what is missing.

**When to use** Before a batch, to check the format is supported. After a
failure, to learn what to install. When someone asks what conversions are
possible here.

**When NOT to use** As a warm-up before every single conversion — the converter
already reports its own missing requirements when it fails.

**Preconditions** None. Reads no file, spends nothing, never fails.

**Failure modes** An unrecognised extension returns `ok: false` and asks for
`source_format` explicitly.

**Example** `{"source_format": "epub"}` -> whether Calibre is installed
"""

_PDF_DESCRIPTION = """\
**Purpose** Rearrange PDF pages: merge, select, split, rotate, compress, add or
remove a password, or report page count and text.

**When to use** The task is about the PDF's pages rather than its content —
there is no other route to it. `operation: "info"` is also the cheapest way to
learn whether a PDF has a text layer at all.

**When NOT to use** To read what a PDF says — use `convilyn_convert` for that.

**Preconditions** `pip install "convilyn[pdf]"`.

**Failure modes** A wrong password, an encrypted source, or a page range outside
the document each return `ok: false` with the reason.

**Example** `{"operation": "select", "source": "in.pdf", "out": "out.pdf",
"pages": "1-3,7"}`
"""

_UNDERSTAND_DESCRIPTION = """\
**Purpose** Extract structured data from documents, conforming to a JSON Schema
you supply, grounded against the source by the platform.

**When to use** The user asked for specific fields out of a document and the
answer has to be shaped and checked — an invoice total, a contract's parties, a
spec sheet's ratings.

**When NOT to use** For anything the local converter already gives you. Reading
the Markdown yourself is free; this is not.

**Preconditions** A Convilyn account (`convilyn setup`) and a credit balance.
**THIS SPENDS CREDITS AND UPLOADS THE FILES.** The user is asked to approve, with
the price, before anything leaves the machine — you cannot answer that prompt on
their behalf, so do not promise the extraction before they have.

**Failure modes** A declined prompt, or a client that cannot show one, returns
`ok: false` and spends nothing. Files outside the workspace your editor opened,
and anything credential-shaped, are refused. No key returns `ok: false` naming
`convilyn setup`. An empty balance returns the shortfall. All are refusals, not
crashes.

**Example** `{"paths": ["invoice.pdf"], "schema": {"type": "object",
"properties": {"total": {"type": "number"}}}}`
"""

_QUOTA_DESCRIPTION = """\
**Purpose** Price a hosted run before making it, and report this account's tier.

**When to use** Before `convilyn_understand`, so an approval can be answered
with a number instead of a guess.

**When NOT to use** As a balance check. This is a PRICE, not what the account
has left, and the two are not in the same unit — the balance is credits, read
with `client.account.get_balance()`. Also not before any local tool: those are
free, and this call is not free of latency.

**Preconditions** A Convilyn account (`convilyn setup`). Read-only: spends
nothing. The figure is insured pre-margin cost in micro-USD, so scaling it into
credits UNDERSTATES what is charged. Report it as a price to approve; never
answer "you can afford this" from it.

**Failure modes** No key returns `ok: false` naming `convilyn setup`.

**Example** `{}`
"""


#: Capability probes for `session.check_client_capability`, which takes a
#: partially-filled `ClientCapabilities` and answers whether the client declared
#: at least that much at `initialize`. Asking the SESSION rather than assuming is
#: what makes the fail-closed branch reachable instead of theoretical.
_ELICITATION_CAPABILITY = ClientCapabilities(elicitation=ElicitationCapability())
_ROOTS_CAPABILITY = ClientCapabilities(roots=RootsCapability())


class _SpendApproval(BaseModel):
    """What the human is asked before any credit is spent.

    One boolean, because the elicitation ACTION already carries decline and
    cancel — this field exists so the client renders something the person reads
    and ticks, rather than an empty form whose only content is the message.
    """

    approve: bool = Field(
        default=False,
        description="Upload these files to Convilyn and spend credits?",
    )


async def _allowed_roots(ctx: Context) -> tuple[Path, ...]:
    """The directories this session may upload from.

    The MCP client declares its workspace via `roots/list` — editors send the
    open project. That is the containment boundary a user already understands,
    and it needs no configuration of ours.

    **Falls back to the process CWD when the client declares nothing** — a
    narrower boundary than "anywhere", never a wider one. A client that cannot
    answer must not thereby unlock the whole filesystem.
    """
    fallback = (Path.cwd(),)
    try:
        if not ctx.session.check_client_capability(_ROOTS_CAPABILITY):
            return fallback
        result = await ctx.session.list_roots()
    except Exception:  # noqa: BLE001 - any failure means "we were told nothing"
        return fallback

    roots: list[Path] = []
    for root in result.roots:
        path = _path_of(root.uri)
        if path is not None:
            roots.append(path)
    return tuple(roots) or fallback


def _path_of(uri: Any) -> Path | None:
    """`file:///c:/x` → a Path, and anything else → None.

    Roots are `file://` URIs. A non-file scheme is not a directory we can fence
    against, so it contributes nothing rather than being coerced into one.
    """
    parsed = urlparse(str(uri))
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path)
    # `file:///C:/x` parses to `/C:/x` on Windows; strip the leading slash that
    # only exists because the drive letter follows it.
    if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw) if raw else None


def _spend_prompt(paths: list[str], quota: dict[str, Any]) -> str:
    """The sentence the person is asked to approve.

    Carries a price WE fetched, not one the model asserted — the model is the
    party being gated, so a number it supplies is not evidence. When the quote
    cannot be fetched the prompt says so rather than omitting the cost line:
    an approval screen with no price reads as "free".
    """
    names = ", ".join(Path(p).name for p in paths) or "no files"
    estimate = quota.get("estimate") or {}
    micro = estimate.get("estimated_micro_u")
    if isinstance(micro, int):
        cost = f"about ${micro / 1_000_000:.2f}"
    else:
        cost = "an amount that could not be quoted right now"
    return (
        f"Convilyn will UPLOAD {len(paths)} file(s) ({names}) and spend credits "
        f"— {cost}. This leaves your machine. Approve?"
    )


async def _approved_to_spend(ctx: Context, summary: str) -> tuple[bool, str]:
    """Ask the HUMAN before spending. Returns ``(approved, refusal_reason)``.

    This is the gate `convilyn_understand` had only in prose: its description
    told the model to ask first, and nothing made it. A model that skips the
    asking is not misbehaving in a way the description can prevent — the
    description is input to the same model.

    **Elicitation is used rather than a two-call handshake because a handshake
    is self-approvable.** `needs_confirmation` + a token the model then echoes
    back is two tool calls a model can make in a row by itself; `elicitation` is
    a protocol-level request the CLIENT must put in front of a person, and the
    model cannot forge the answer.

    **Fail-closed when the client cannot ask.** No elicitation capability, a
    transport failure, a malformed response — every one of them means nobody was
    asked, and "nobody was asked" is not "yes".
    """
    try:
        if not ctx.session.check_client_capability(_ELICITATION_CAPABILITY):
            return False, (
                "this MCP client cannot ask you to approve spending, so nothing "
                "was uploaded and nothing was charged"
            )
        result = await ctx.elicit(summary, _SpendApproval)
    except Exception as exc:  # noqa: BLE001 - see the docstring: no answer is a no
        # `elicit_with_validation` RAISES when a client accepts with content that
        # does not match the schema. Letting that propagate would break this
        # package's "a tool returns, it does not raise" contract AND would look
        # like a crash rather than a refusal, so it is caught here where the
        # answer is "we did not get consent".
        return False, f"could not obtain approval ({type(exc).__name__}: {exc})"

    if result.action != "accept" or not getattr(result.data, "approve", False):
        return False, "you declined — nothing was uploaded and nothing was charged"
    return True, ""


def build_server() -> Any:
    """The configured server, without running it.

    Separate from :func:`run_stdio` so tests can ``await`` ``list_tools()`` and
    ``call_tool()`` in-process. A test that has to spawn a subprocess and speak
    JSON-RPC to learn what a tool returns is a test most contributors will not
    run — and the transport is not the part that breaks.
    """
    # ``version`` reaches the host as ``serverInfo.version`` — the only place a
    # user can see which convilyn their editor is actually running, and the
    # first thing worth knowing when a tool behaves unexpectedly.
    server = MCPServer(name="convilyn", version=__version__)

    @server.tool(name="convilyn_convert", description=_CONVERT_DESCRIPTION)
    def convilyn_convert(
        paths: list[str],
        to: str = "md",
        out_dir: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return tools.convert(paths, to=to, out_dir=out_dir, overwrite=overwrite)

    @server.tool(name="convilyn_capabilities", description=_CAPABILITIES_DESCRIPTION)
    def convilyn_capabilities(
        path: str | None = None, source_format: str | None = None
    ) -> dict[str, Any]:
        return tools.capabilities(path, source_format=source_format)

    @server.tool(name="convilyn_pdf", description=_PDF_DESCRIPTION)
    def convilyn_pdf(
        operation: str,
        source: str | None = None,
        sources: list[str] | None = None,
        out: str | None = None,
        out_dir: str | None = None,
        pages: str | None = None,
        degrees: int = 90,
        password: str | None = None,
    ) -> dict[str, Any]:
        kwargs = {
            "source": source,
            "sources": sources,
            "out": out,
            "out_dir": out_dir,
            "pages": pages,
            "degrees": degrees,
            "password": password,
        }
        return tools.pdf(operation, **{k: v for k, v in kwargs.items() if v is not None})

    @server.tool(name="convilyn_understand", description=_UNDERSTAND_DESCRIPTION)
    async def convilyn_understand(
        ctx: Context,
        paths: list[str],
        schema: dict[str, Any],
        instructions: str | None = None,
    ) -> dict[str, Any]:
        # Order is the whole design, and each step earns its place:
        #   free local checks -> price -> ASK the human -> spend.
        # Prechecking first means a malformed request never puts an approval
        # prompt in front of a person (nor pays a round-trip to price a call
        # that cannot happen). Pricing before asking means the prompt carries a
        # number. Asking before spending is the gate this tool did not have.
        #
        # `ctx` is injected by annotation and never appears in the tool's input
        # schema, so the model cannot supply one — verified, not assumed.
        roots = await _allowed_roots(ctx)
        _, refusal = tools.precheck_understand(paths, schema=schema, allowed_roots=roots)
        if refusal is not None:
            return refusal

        approved, denial = await _approved_to_spend(ctx, _spend_prompt(paths, tools.quota()))
        if not approved:
            return {"ok": False, "error": denial}
        return tools.understand(
            paths, schema=schema, allowed_roots=roots, instructions=instructions
        )

    @server.tool(name="convilyn_quota", description=_QUOTA_DESCRIPTION)
    def convilyn_quota(
        tool_ids: list[str] | None = None, max_iterations: int | None = None
    ) -> dict[str, Any]:
        return tools.quota(tool_ids, max_iterations=max_iterations)

    return server


def run_stdio() -> None:
    """Serve on stdin/stdout until the client closes it."""
    build_server().run(transport="stdio")

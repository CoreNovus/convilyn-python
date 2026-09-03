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

import json
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import (
    CallToolResult,
    ClientCapabilities,
    ElicitationCapability,
    RootsCapability,
    TextContent,
    ToolAnnotations,
)
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
`capabilities` says which, and every failure names its own install
command.

**Failure modes** A missing library returns `ok: false` with a `hint` naming the
exact install command. One unreadable file in twenty does not fail the other
nineteen — each file gets its own result row.

**Returns** The output PATHS, never the converted text: `{"ok": true,
"converted": 2, "results": [{"output": "report.md", "bytes": 24310}, ...]}`.
That is what makes a batch free — the Markdown does not enter your context.
Read only the files the task actually needs.

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
there is no other route to it. `operation: "info"` reports the page count and a
BOUNDED sample of the text layer (4,000 chars from the first 20 pages by
default), which is enough to tell whether the PDF has a text layer at all.

**When NOT to use** To read what a PDF says — use `convert` for that. `info` is
a sample, not the document: it returns `text_truncated` when it clipped, and
raising `max_chars` to swallow a long PDF spends the tokens `convert` exists to
save. Narrow with `pages` instead.

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
**THIS SPENDS CREDITS AND UPLOADS THE FILES.** The user is asked to approve
before anything leaves the machine — you cannot answer that prompt on their
behalf, so do not promise the extraction before they have. The prompt states
that the amount is not known in advance, because this surface has no per-run
quote; do not offer the user a figure of your own to fill that gap.

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

**When to use** To report the account's tier, and to price a Builder TOOL
PALETTE — a set of tool ids you pass explicitly with an iteration cap.

**When NOT to use** To price `understand`, or any other run. It knows no
workflow id and cannot see what you are about to do; called with no tool ids it
returns a CONSTANT (the per-iteration cost × the default cap), which is not the
price of anything you ran. Also not as a balance check — this is a price, not
what the account has left, and the units differ: the balance is credits, read
with `client.account.get_balance()`. And not before a local tool: those are
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


def _spend_prompt(paths: list[str]) -> str:
    """The sentence the person is asked to approve.

    **It names no price, and that is the fix rather than a gap.** This prompt
    used to carry one, and it was fabricated: it called ``tools.quota()`` with
    no arguments, which prices an EMPTY chat-Builder tool palette —
    ``(0 tools × 20 iterations) + (50,000 µU × 20)`` — so it rendered
    "about $1.00" on every call, for every file, for every account, having
    never asked about the operation being approved. Three things were wrong at
    once: the wrong operation, the wrong unit (insured pre-margin µU, which
    ``CostEstimate``'s own docstring says UNDERSTATES the charge), and no
    correction afterwards, because the charge is not reported back on a
    finished run either.

    **The amount is stated as unknown, not omitted.** The previous docstring
    was right about one thing and it still governs: *an approval screen with no
    cost line reads as "free"*. So the unknown is said out loud. A person
    approving this should feel the uncertainty, because it is real — this
    surface has no per-run quote, and inventing one to fill the space is what
    produced a number 3.4x under the actual charge on a measured run.

    Do not "restore" a figure from ``quota``/``cost-preview``, including its
    ``quota_check`` verdict: that verdict grades the same palette estimate, so
    it repeats this defect more quietly. A real price needs the per-run
    charge to be reported on the wire first; it is not today.
    """
    names = ", ".join(Path(p).name for p in paths) or "no files"
    return (
        f"Convilyn will UPLOAD {len(paths)} file(s) ({names}) and SPEND CREDITS. "
        "The amount is not known before the run — this surface has no per-run "
        "quote, so no figure is shown rather than a guessed one. "
        "This leaves your machine. Approve?"
    )


async def _approved_to_spend(ctx: Context, summary: str) -> tuple[bool, str]:
    """Ask the HUMAN before spending. Returns ``(approved, refusal_reason)``.

    This is the gate `understand` had only in prose: its description
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


def _result(payload: dict[str, Any]) -> CallToolResult:
    """Carry a failure on the PROTOCOL's error channel as well as in the body.

    ``tools.*`` returns ``{"ok": false, "error", "hint"}`` and raises nothing —
    that contract is deliberate and stays exactly as it is. What it did not do
    is set ``isError`` on the wire, so a host keying off the protocol flag (the
    field the MCP spec defines for *tool execution errors*) saw an unbroken run
    of successes while the model was reading refusals. Both audiences now get an
    answer in the channel they actually read.

    **Every tool returns this, success included, and that is forced rather than
    chosen.** ``mcp`` rejects ``dict | CallToolResult`` as a return annotation
    outright — *"CallToolResult cannot be used in Union or Optional types"* — so
    a tool either always builds one or never does.

    **A REFUSAL is what sets the flag, not merely ``ok: false``.** Those are not
    the same set, and the first version of this function conflated them. A batch
    where one file of twelve failed returns ``ok: false`` with eleven good rows —
    a call that did its job, which `convert`'s own contract insists on ("a failed
    file is a result, not an exception"). Flagging that as a tool execution error
    tells the host the call failed when it did not, and — because `is_error`
    exempts a result from output-schema validation — it would also have skipped
    validation on exactly the payload most likely to drift. The discriminator is
    a top-level ``error`` key, which is what ``_error()`` produces and no partial
    success carries.

    **The ``outputSchema`` is not the price of this.** An earlier version of this
    docstring said it was: that ``server.tool()`` takes no ``output_schema``
    argument, so returning ``CallToolResult`` meant giving the schema up. The
    first half is true and the conclusion does not follow —
    ``-> Annotated[CallToolResult, Envelope]`` keeps the type (so ``is_error``
    stays ours) *and* derives the published schema from ``Envelope``
    (``func_metadata.py:416-433``). Where a shape is stable enough to model, both
    are available where the shape has no aliases; see ``ConvertEnvelope``
    below, and ``_QUOTA_HAS_NO_OUTPUT_SCHEMA`` for where it is not.

    ``structuredContent`` is preserved either way — measured on the installed
    ``mcp`` 2.1.1, not assumed.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error="error" in payload,
    )


class _ConvertRow(BaseModel):
    """One file's outcome. Two shapes in one model, deliberately.

    A row is either converted (``output``/``bytes``) or refused
    (``error``/``hint``), never both — but a nested *union* is not worth the
    schema's complexity here, and unlike a top-level one it would at least
    work. See ``ConvertEnvelope`` for why the top level cannot be a union.
    """

    ok: bool
    source: str
    output: str | None = None
    bytes: int | None = None
    error: str | None = None
    hint: str | None = None


class ConvertEnvelope(BaseModel):
    """``convert``'s success payload, published as its ``outputSchema``.

    **The only tool that publishes one**, and the reason is narrow: every field
    here is declared with the name it is sent under. No aliases, so the schema
    the library derives and the payload ``_result`` sends cannot disagree.

    The other four do not, each for its own reason:

    * ``quota`` — see ``_QUOTA_HAS_NO_OUTPUT_SCHEMA`` below. It had one for
      exactly one commit and it crashed every client that validated it.
    * ``pdf`` — five success shapes, and a top-level union does not work:
      ``_create_output_model`` wraps one as ``{"result": ...}`` and sets
      ``wrap_output``, but the ``CallToolResult`` branch validates
      ``structured_content`` *without* applying that wrap, so it can never match.
    * ``understand`` — ``result`` is the caller's own JSON Schema output,
      typeable only as ``Any``.
    * ``capabilities`` — two shapes, same union problem as ``pdf``.

    Note the cost this buys: a success payload that drifts from this model
    becomes a loud failure instead of a silent one. That is the point, and it
    is a new failure mode on this tool.
    """

    ok: bool
    converted: int
    total: int
    tokens_used: int
    results: list[_ConvertRow]


#: Why ``quota`` publishes no ``outputSchema``, recorded because the obvious
#: change — ``estimate: CostEstimate``, which is right there on the public
#: surface — is a crash, and someone will try it again.
#:
#: ``CostEstimate`` (and its nested ``QuotaCheck`` / ``ToolCostEstimate``)
#: declare camelCase aliases. The library derives the published schema through
#: a ``TypeAdapter``, whose ``json_schema()`` defaults to ``by_alias=True``, so
#: the schema demanded ``estimatedMicroU``. The payload comes from ``_plain()``
#: → ``model_dump(mode="json")`` with ``by_alias`` unset, so it sent
#: ``estimated_micro_u``. Server-side nothing complained: ``populate_by_name``
#: accepts either spelling, and ``func_metadata``'s ``CallToolResult`` branch
#: validates *without* the alias flags the other branch uses. The CLIENT
#: compiles the advertised schema and raises
#: ``'estimatedMicroU' is a required property`` — on every call, with no input
#: that passes.
#:
#: The two fixes that look available both cost more than the schema is worth:
#: dumping ``by_alias=True`` makes ``quota`` the only tool emitting camelCase
#: and drags ``_spend_prompt``'s reader with it, and mirroring ``CostEstimate``
#: as an alias-free model duplicates a public type that will drift.
#: ``TestEveryPublishedSchemaMatchesItsPayload`` is what makes this a decision
#: rather than a thing that quietly regresses.
_QUOTA_HAS_NO_OUTPUT_SCHEMA = True


#: Behaviour hints, per tool. Unset until now, which cost hosts the one thing
#: annotations are for: a client cannot auto-approve the two read-only tools if
#: nothing says which two they are, so `capabilities` — which reads no file,
#: spends nothing and "never fails" — was gated exactly as hard as the tool that
#: uploads your documents and charges you.
#:
#: `pdf` is annotated NOT read-only although one of its eight
#: operations (`info`) is. One tool carries one annotation, and the honest
#: direction for a tool that can also rewrite a PDF and set a password is the
#: pessimistic one. That is a real cost of consolidating eight operations into
#: one tool, and it is the right trade anyway — see `PDF_OPERATIONS`.
_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "convert": ToolAnnotations(
        read_only_hint=False,  # writes converted files, and `overwrite` can replace one
        idempotent_hint=False,
        open_world_hint=False,  # entirely local
    ),
    "capabilities": ToolAnnotations(
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "pdf": ToolAnnotations(
        read_only_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    "understand": ToolAnnotations(
        read_only_hint=False,  # uploads, and spends credits
        idempotent_hint=False,
        open_world_hint=True,  # reaches the hosted platform
    ),
    "quota": ToolAnnotations(
        read_only_hint=True,  # a price lookup; spends nothing
        idempotent_hint=True,
        open_world_hint=True,  # but it does go to the network
    ),
}

#: Human-readable names. `name` stays machine-shaped; `title` is what a host
#: shows a person in an approval prompt.
_TITLES: dict[str, str] = {
    "convert": "Convert documents locally",
    "capabilities": "List local conversion capabilities",
    "pdf": "Rearrange PDF pages",
    "understand": "Extract structured data (uploads, costs credits)",
    "quota": "Price a hosted run",
}


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

    # Tool names carry NO `convilyn_` prefix, because the host already supplies
    # one. A plugin install exposes `mcp__plugin_convilyn_convilyn__convert`;
    # prefixing the tool as well spelled `convilyn` three times in one
    # identifier and told the model nothing the namespace had not already said.
    #
    # This reverses the comment that stood here until 4.0.0, which argued for
    # keeping the prefix on the grounds that renaming is breaking. It IS
    # breaking — the names appear in users' permission rules, `allowed-tools`
    # lists, subagent `tools` fields and hook matchers, none of which this
    # package can migrate — and that argument settles the *mechanism* (a major
    # version), not the question. Deferring a rename indefinitely because it is
    # breaking is how a public surface stays wrong permanently.
    #
    # There is no dual-registration deprecation window, and that is the one
    # place this departs from `docs/STABILITY.md`'s general policy. A deprecated
    # MCP tool has to stay REGISTERED to keep working, so the window would ship
    # ten tools: double the catalogue whose small size is the property worth
    # protecting, and over the description budget this module is held to.
    # STABILITY.md carries the carve-out and the reasoning; the release note is
    # the warning this surface gets instead.

    @server.tool(
        name="convert",
        title=_TITLES["convert"],
        description=_CONVERT_DESCRIPTION,
        annotations=_ANNOTATIONS["convert"],
    )
    def convert(
        paths: Annotated[
            list[str],
            Field(description="Files to convert. Pass the whole batch in ONE call."),
        ],
        to: Annotated[
            str,
            Field(
                description=(
                    "Target format extension without the dot, e.g. 'md', 'pdf', 'png'. "
                    "Deliberately not an enum: the available set depends on what is "
                    "installed here — ask capabilities."
                )
            ),
        ] = "md",
        out_dir: Annotated[
            str | None,
            Field(description="Directory to write into. Default: beside each input file."),
        ] = None,
        overwrite: Annotated[
            bool,
            Field(description="Replace an existing output file instead of refusing."),
        ] = False,
    ) -> Annotated[CallToolResult, ConvertEnvelope]:
        return _result(tools.convert(paths, to=to, out_dir=out_dir, overwrite=overwrite))

    @server.tool(
        name="capabilities",
        title=_TITLES["capabilities"],
        description=_CAPABILITIES_DESCRIPTION,
        annotations=_ANNOTATIONS["capabilities"],
    )
    def capabilities(
        path: Annotated[
            str | None,
            Field(description="A file whose format should be inspected."),
        ] = None,
        source_format: Annotated[
            str | None,
            Field(description="A format extension without the dot, e.g. 'epub'."),
        ] = None,
    ) -> CallToolResult:
        return _result(tools.capabilities(path, source_format=source_format))

    @server.tool(
        name="pdf",
        title=_TITLES["pdf"],
        description=_PDF_DESCRIPTION,
        annotations=_ANNOTATIONS["pdf"],
    )
    def pdf(
        operation: Annotated[
            # Spelled out because `Literal` needs literal values at type-check
            # time, so it cannot be built from `PDF_OPERATIONS` at runtime.
            # `test_the_operation_enum_matches_pdf_operations` compares the two
            # and fails if they drift — the CHECK is derived even though the
            # value cannot be.
            Literal["merge", "select", "split", "rotate", "compress", "protect", "unlock", "info"],
            Field(description="Which page operation to run."),
        ],
        source: Annotated[
            str | None, Field(description="The input PDF. Every operation except 'merge'.")
        ] = None,
        sources: Annotated[
            list[str] | None, Field(description="The input PDFs, in order. 'merge' only.")
        ] = None,
        out: Annotated[str | None, Field(description="Output file path.")] = None,
        out_dir: Annotated[
            str | None, Field(description="Output directory. 'split' only, one file per page.")
        ] = None,
        pages: Annotated[
            str | None,
            Field(description="1-based pages, as printed: '3', '1-5', or '1-3,7,10-12'."),
        ] = None,
        degrees: Annotated[
            int, Field(description="Clockwise rotation: 90, 180 or 270. 'rotate' only.")
        ] = 90,
        password: Annotated[
            str | None, Field(description="Password for 'protect' or 'unlock'.")
        ] = None,
        max_chars: Annotated[
            int | None,
            Field(
                description=(
                    "'info' only: cap on returned text (default 4000 ≈ 2 pages). "
                    "Raise it deliberately; the whole layer can be very large."
                )
            ),
        ] = None,
        max_pages: Annotated[
            int | None,
            Field(
                description=(
                    "'info' only: how many leading pages to read when 'pages' is "
                    "omitted (default 20). Ignored once you pass 'pages'."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        kwargs = {
            "source": source,
            "sources": sources,
            "out": out,
            "out_dir": out_dir,
            "pages": pages,
            "degrees": degrees,
            "password": password,
            "max_chars": max_chars,
            "max_pages": max_pages,
        }
        return _result(tools.pdf(operation, **{k: v for k, v in kwargs.items() if v is not None}))

    @server.tool(
        name="understand",
        title=_TITLES["understand"],
        description=_UNDERSTAND_DESCRIPTION,
        annotations=_ANNOTATIONS["understand"],
    )
    async def understand(
        ctx: Context,
        paths: Annotated[
            list[str],
            Field(description="Files to extract from. Must sit inside the session's workspace."),
        ],
        schema: Annotated[
            dict[str, Any],
            Field(description="A JSON Schema describing the shape to return."),
        ],
        instructions: Annotated[
            str | None,
            Field(description="Extra guidance, e.g. 'the total is the figure after tax'."),
        ] = None,
    ) -> CallToolResult:
        # Order is the whole design, and each step earns its place:
        #   free local checks -> ASK the human -> spend.
        # Prechecking first means a malformed request never puts an approval
        # prompt in front of a person. Asking before spending is the gate this
        # tool did not have.
        #
        # There used to be a `price` step between them, and it is gone rather
        # than moved: it called `tools.quota()` — a blocking HTTP round-trip on
        # this event loop — to obtain a number that was the same constant every
        # time and described a different operation. Paying latency to fabricate
        # a price is worse than showing none, and `_spend_prompt` now says the
        # amount is unknown instead of guessing it.
        #
        # `ctx` is injected by annotation and never appears in the tool's input
        # schema, so the model cannot supply one — verified, not assumed.
        roots = await _allowed_roots(ctx)
        _, refusal = tools.precheck_understand(paths, schema=schema, allowed_roots=roots)
        if refusal is not None:
            return _result(refusal)

        approved, denial = await _approved_to_spend(ctx, _spend_prompt(paths))
        if not approved:
            return _result({"ok": False, "error": denial})
        return _result(
            tools.understand(paths, schema=schema, allowed_roots=roots, instructions=instructions)
        )

    @server.tool(
        name="quota",
        title=_TITLES["quota"],
        description=_QUOTA_DESCRIPTION,
        annotations=_ANNOTATIONS["quota"],
    )
    def quota(
        tool_ids: Annotated[
            list[str] | None,
            Field(description="Tool ids the run would use, e.g. ['pdf-mcp:extract_text']."),
        ] = None,
        max_iterations: Annotated[
            int | None,
            Field(
                description=(
                    "Iteration cap to price against. The per-iteration model cost is "
                    "unconditional, so an empty tool list is NOT a zero estimate."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        return _result(tools.quota(tool_ids, max_iterations=max_iterations))

    return server


def run_stdio() -> None:
    """Serve on stdin/stdout until the client closes it."""
    build_server().run(transport="stdio")

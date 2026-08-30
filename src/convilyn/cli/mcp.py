"""``convilyn mcp`` — speak MCP over stdin/stdout, for an agent rather than a person.

Module scope imports no ``mcp``: the SDK's install footprint is exactly
``httpx`` + ``pydantic`` + ``click``, and the release pipeline installs the
wheel with **no extras** and runs ``convilyn --version``. Anything importing the
protocol here would break that, which is the same rule ``cli/local.py`` records
for the conversion extras.
"""

from __future__ import annotations

import click

from convilyn.cli._exit_codes import EXIT_USAGE
from convilyn.cli._extras import install_command
from convilyn.cli._output import make_renderer


@click.group(name="mcp", help="Expose convilyn's tools over MCP, for an AI coding agent.")
def mcp_command() -> None:
    """Grouped rather than a bare command so ``mcp status`` can join it later
    without moving ``serve`` and breaking every config that names it."""


@mcp_command.command(
    "serve",
    help=(
        "Speak MCP on stdin/stdout. Started by your agent, not by you — see "
        "`convilyn agent install`."
    ),
)
def serve_command() -> None:
    try:
        from convilyn.mcp.server import run_stdio
    except ImportError as exc:
        # Only OUR missing extra is turned into advice. A typo inside
        # convilyn.mcp.server is also an ImportError, and reporting that as
        # "install the mcp extra" would send someone to install a package they
        # already have while the real defect stays hidden.
        if (exc.name or "").split(".")[0] != "mcp":
            raise
        # Through the renderer, not `click.echo`. The renderer owns the `✗`
        # glyph AND the codepage degradation behind it — a hand-written `✗`
        # raises `UnicodeEncodeError` on a cp950 console, which turns a helpful
        # refusal into a crash on exactly the machines least able to read it.
        # Observed here, not imagined: the first version of this line did that.
        make_renderer(json_output=False).event(
            "error",
            message=(
                "Cannot start the MCP server here: convilyn mcp needs mcp, which "
                f"is not installed. Add it with {install_command('mcp')}."
            ),
        )
        raise SystemExit(EXIT_USAGE) from exc

    run_stdio()

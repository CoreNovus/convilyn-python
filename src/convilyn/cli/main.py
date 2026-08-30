"""Top-level ``convilyn`` Click group + entry point.

Adding a new sub-command is two lines (``cli.add_command(...)``) — the
sub-command lives in its own module so each command's options, help
text, and exit-code mapping stay isolated. This is the OCP seam for
the CLI: the group never changes, only the registry grows.
"""

from __future__ import annotations

import click

from convilyn._version import __version__
from convilyn.cli.account import account_command
from convilyn.cli.agent import agent_command
from convilyn.cli.api import api_command
from convilyn.cli.convert import convert_command
from convilyn.cli.doctor import doctor_command
from convilyn.cli.goals import goals_command
from convilyn.cli.local import local_command
from convilyn.cli.mcp import mcp_command
from convilyn.cli.setup import setup_command


@click.group(
    context_settings={
        "help_option_names": ["-h", "--help"],
    },
)
@click.version_option(__version__, prog_name="convilyn", message="%(prog)s %(version)s")
def cli() -> None:
    """Convilyn — convert files and run AI workflows from the command line.

    Every sub-command is scriptable (``--json`` for machine-readable
    output, ``--dry-run`` for safe previews, exit codes you can branch
    on). Pair with the Python SDK by importing ``convilyn``.
    """


cli.add_command(setup_command, name="setup")
cli.add_command(convert_command, name="convert")
cli.add_command(doctor_command, name="doctor")
cli.add_command(api_command, name="api")
cli.add_command(goals_command, name="goals")
cli.add_command(account_command, name="account")
cli.add_command(local_command, name="local")
cli.add_command(mcp_command, name="mcp")
cli.add_command(agent_command, name="agent")


if __name__ == "__main__":
    cli()

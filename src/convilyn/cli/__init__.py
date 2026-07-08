"""Convilyn command-line interface.

The CLI is the AI-coding-era entry point for the SDK: every sub-command
is scriptable (``--json`` machine-readable output, no interactive
prompts, exit codes that distinguish usage vs API vs job failures).

Sub-commands compose the Python SDK exactly as a third-party caller
would — the CLI never reaches into ``_internal``. This keeps the CLI
honest about what the SDK exposes, and means every behaviour available
on the command line is also available programmatically.
"""

from convilyn.cli.main import cli

__all__ = ["cli"]

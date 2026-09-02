"""``convilyn agent install`` -- register convilyn with the coding agents on this machine.

One command for both destinations, because the alternative is a README asking
the user to hand-edit a TOML file, and a hand-edited TOML file is how a working
config becomes a broken one.

What it writes is deliberately small: a skill file, and a four-line MCP table
with no credential in it. See :mod:`convilyn.agent.install` for why the TOML
half edits text rather than parsing.
"""

from __future__ import annotations

from pathlib import Path

import click

from convilyn.agent.install import PAST_TENSE_ACTIONS, install, mcp_extra_installed
from convilyn.cli._exit_codes import EXIT_OK, EXIT_USAGE
from convilyn.cli._extras import install_command as extra_install_hint
from convilyn.cli._output import make_renderer

#: Past tense -> infinitive, for the conditional a dry run must speak in.
#:
#: `Step.action` is past tense because it describes what a real run DID, and the
#: JSON keeps that single vocabulary so a caller can diff a dry run against a
#: real one word-for-word. Only the human line is rewritten — and it needs the
#: infinitive, because "would created" is not English. A bare `f"would {action}"`
#: produced exactly that, and the first test written for it passed anyway:
#: `"would create" in "would created"` is True. Substring assertions on rendered
#: text do not check what they look like they check.
#:
#: Keyed on `install.PAST_TENSE_ACTIONS`, and required to cover it exactly. This
#: map used to be a hand-kept pair that omitted `appended`, so a `--dry-run` on
#: any machine that already had a `~/.codex/config.toml` announced a change it
#: had not made.
_CONDITIONAL = {
    "created": "would create",
    "updated": "would update",
    "appended": "would append",
}


def _phrase(action: str, *, dry_run: bool) -> str:
    """What a real run DID, or what a dry run WOULD do.

    Total over `install.ACTIONS` by construction: `Step` refuses any action
    outside it, every past-tense one has an entry above, and the tense-neutral
    ones are returned as they are. Deliberately no `.get(action, action)`
    fallback — a default cannot tell "needs no conditional form" from "nobody
    wrote one", and answering the second as though it were the first is exactly
    how a dry run came to speak in the past tense.
    """
    if not dry_run:
        return action
    return _CONDITIONAL[action] if action in PAST_TENSE_ACTIONS else action


@click.group(name="agent", help="Register convilyn with the AI coding agents on this machine.")
def agent_command() -> None:
    """Grouped so ``agent status`` / ``agent uninstall`` can join later without
    moving ``install`` and breaking the line every README will print."""


@agent_command.command(
    "install",
    help="Install the convilyn skill and register its MCP server. Safe to re-run.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would be written, and write nothing.",
)
@click.option(
    "--home",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Install under this directory instead of your home directory.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit a single JSON object on stdout.")
def install_command(dry_run: bool, home: Path | None, json_output: bool) -> None:
    renderer = make_renderer(json_output=json_output)
    root = home or Path.home()

    steps = install(root, dry_run=dry_run)
    refused = [step for step in steps if step.action == "refused"]
    mcp_ready = mcp_extra_installed()

    for step in steps:
        action = _phrase(step.action, dry_run=dry_run and step.changed)
        renderer.event(
            "warn" if step.action == "refused" else "info",
            message=f"{action}: {step.target}" + (f" -- {step.detail}" if step.detail else ""),
        )

    if not mcp_ready:
        renderer.event(
            "warn",
            message=(
                "The MCP server is registered but cannot start yet: the mcp extra "
                f"is not installed. Add it with {extra_install_hint('mcp')}. The skill "
                "works without it."
            ),
        )

    renderer.final(
        {
            "ok": not refused,
            "dry_run": dry_run,
            "mcp_extra_installed": mcp_ready,
            "steps": [
                {
                    "target": str(step.target),
                    "action": step.action,
                    "changed": step.changed,
                    "detail": step.detail,
                }
                for step in steps
            ],
            # The marketplace is the TEAM-distribution route, not the one this
            # command performs. It used to be printed as the Claude Code
            # instruction because nothing here wrote anything Claude Code reads;
            # the skills-directory plugin above now does, so this is an
            # alternative rather than the answer. Reported, not run: installing
            # from a marketplace is a decision made inside the agent.
            "marketplace_alternative": [
                "/plugin marketplace add CoreNovus/convilyn-python",
                "/plugin install convilyn@convilyn",
            ],
        }
    )

    if not json_output:
        renderer.event(
            "info",
            message="Claude Code picks this up on the next session (or run /reload-plugins now).",
        )
        renderer.event(
            "info",
            message="Codex picks it up on the next run.",
        )
        renderer.event(
            "info",
            message=(
                "To distribute it to a team from the marketplace instead: "
                "/plugin marketplace add CoreNovus/convilyn-python "
                "then /plugin install convilyn@convilyn"
            ),
        )

    raise SystemExit(EXIT_USAGE if refused else EXIT_OK)

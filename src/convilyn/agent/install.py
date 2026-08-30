"""Put convilyn where an AI coding agent will find it.

Three destinations, because the two hosts look in different places and neither
reads the other's:

* ``~/.agents/skills/convilyn/SKILL.md`` -- **Codex's** user-scope skill
  directory (its docs list ``$HOME/.agents/skills``, ``$CWD/.agents/skills`` and
  ``/etc/codex/skills``). The file is the one shipped inside the wheel, copied
  verbatim.
* ``~/.codex/config.toml`` -- an ``[mcp_servers.convilyn]`` table, **merged into
  whatever is already there**.
* ``~/.claude/skills/convilyn/`` -- a **skills-directory plugin** for Claude
  Code: a ``.claude-plugin/plugin.json`` manifest, the skill, and ``.mcp.json``.
  Any folder under a skills directory carrying that manifest loads as
  ``convilyn@skills-dir`` on the next session, with no marketplace and no
  install step. This is the shape ``claude plugin init`` scaffolds.

**Why the third one is not the marketplace route.** Claude Code does not read
``~/.agents/skills`` -- it scans ``~/.claude/skills``, the project's
``.claude/skills``, the enterprise directory, and installed plugins. So the two
Codex destinations do nothing for it, and for a while the only Claude Code path
this package offered was ``/plugin marketplace add``, which resolves from the
public GitHub mirror rather than from anything ``pip`` put on disk. That route
still works and is the right one for team distribution; it is a poor fit for
"one command on your own machine", because it makes a local install depend on a
push having happened.

The Codex TOML destination edits a file the user owns and that other tools also
write, so the rule there is **refuse rather than guess**. A config that cannot be
extended safely is reported and left exactly as it was; nothing here rewrites a
line it did not add. The Claude Code destination is a directory this package
owns end to end, so it is written outright.

**Why the TOML edit is textual.** ``tomllib`` reads TOML from 3.11 and this
package supports 3.10; nothing in the standard library *writes* TOML at any
version. The alternatives were a new runtime dependency for one command, or
appending one well-formed table and refusing the shapes where appending is not
safe. The second is smaller and its failure mode is a message rather than a
mangled config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

#: The table this command owns. It never touches any other part of the file.
SECTION = "[mcp_servers.convilyn]"

#: Appended verbatim. No ``env`` block: ``convilyn setup`` already writes the
#: credential where the CLI finds it, so putting a key here would move a secret
#: into a file that gets copied between machines and pasted into issues for no
#: gain at all.
BLOCK = f"""
{SECTION}
command = "convilyn"
args = ["mcp", "serve"]
"""

#: ``mcp_servers = {{ ... }}`` at top level. Appending ``[mcp_servers.convilyn]``
#: after an inline table is a TOML error, so this is the shape that gets refused.
_INLINE_TABLE = re.compile(r"^\s*mcp_servers\s*=", re.MULTILINE)


@dataclass(frozen=True)
class Step:
    """One destination, and what happened to it.

    ``changed`` is False for an already-correct destination -- re-running is
    expected, so "already there" is a success, not a warning.
    """

    target: Path
    action: str
    changed: bool
    detail: str = ""


def payload(name: str) -> Path:
    """One canonical payload file, as shipped inside the wheel.

    The canonical names carry no leading dot (``mcp.json``, not ``.mcp.json``):
    a dotfile under ``src/`` is one packaging tool away from being silently
    skipped, and a payload the wheel does not carry breaks this module. The dot
    is added at the destination, where the host requires it.
    """
    return Path(str(resources.files("convilyn.agent") / name))


def skill_source() -> Path:
    """The canonical ``SKILL.md``, as shipped inside the wheel."""
    return payload("SKILL.md")


def skill_destination(home: Path) -> Path:
    return home / ".agents" / "skills" / "convilyn" / "SKILL.md"


def codex_config(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def claude_plugin_root(home: Path) -> Path:
    """The skills-directory plugin's own root.

    Claude Code treats any folder under a skills directory that contains
    ``.claude-plugin/plugin.json`` as a plugin discovered in place, rather than
    as a plain skill -- which is what lets this one carry an MCP server too.
    """
    return home / ".claude" / "skills" / "convilyn"


#: ``(canonical payload name, path under the plugin root)``.
#:
#: ``SKILL.md`` sits at the plugin ROOT rather than under ``skills/convilyn/``.
#: A plugin shipping exactly one skill is allowed to do that, it is what
#: ``claude plugin init`` scaffolds, and it avoids the doubled command name
#: ``/convilyn:convilyn`` that the nested layout would produce.
_CLAUDE_PLUGIN_FILES: tuple[tuple[str, str], ...] = (
    ("plugin.json", ".claude-plugin/plugin.json"),
    ("mcp.json", ".mcp.json"),
    ("SKILL.md", "SKILL.md"),
)


def install_skill(home: Path, *, dry_run: bool = False) -> Step:
    destination = skill_destination(home)
    #: Bytes, not text. ``read_text``/``write_text`` round-trips newlines through
    #: the platform default, so on Windows the copy would differ from the source
    #: it is supposed to be identical to.
    payload = skill_source().read_bytes()

    if destination.is_file() and destination.read_bytes() == payload:
        return Step(destination, "unchanged", False, "already up to date")

    action = "updated" if destination.is_file() else "created"
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return Step(destination, action, True)


def install_claude_code_plugin(home: Path, *, dry_run: bool = False) -> Step:
    """Write the skills-directory plugin Claude Code loads with no install step.

    Reported as ONE step against the plugin root, not three against its files:
    the three are a single unit to the host -- a manifest without the skill, or a
    skill without ``.mcp.json``, is a half-installed plugin rather than partial
    progress -- and three lines about one thing reads as three things.

    ``"unchanged"`` requires every file to match byte-for-byte, so a partially
    written or hand-edited tree is reported as ``"updated"`` and repaired, which
    is also what makes re-running safe.
    """
    root = claude_plugin_root(home)
    wanted = {
        root / relative: payload(name).read_bytes() for name, relative in _CLAUDE_PLUGIN_FILES
    }

    if all(path.is_file() and path.read_bytes() == body for path, body in wanted.items()):
        return Step(root, "unchanged", False, "already up to date")

    action = "updated" if root.is_dir() else "created"
    if not dry_run:
        for path, body in wanted.items():
            #: Bytes, not text -- see ``install_skill``. The manifests are
            #: compared byte-for-byte above, so a newline translated on write
            #: would report drift on every subsequent run.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    return Step(root, action, True, "loads as convilyn@skills-dir on the next session")


def install_codex_mcp(home: Path, *, dry_run: bool = False) -> Step:
    destination = codex_config(home)

    if not destination.is_file():
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(BLOCK.lstrip("\n"), encoding="utf-8")
        return Step(destination, "created", True)

    existing = destination.read_text(encoding="utf-8")

    if SECTION in existing:
        return Step(destination, "unchanged", False, "already declares convilyn")

    if _INLINE_TABLE.search(existing):
        return Step(
            destination,
            "refused",
            False,
            "mcp_servers is written as an inline table here; adding a "
            f"{SECTION} section after one is invalid TOML. Add\n"
            '  convilyn = { command = "convilyn", args = ["mcp", "serve"] }\n'
            "inside your existing mcp_servers table instead.",
        )

    if not dry_run:
        #: Append. A new ``[table]`` header terminates whatever preceded it, so
        #: this is safe wherever the file happens to end -- with one exception,
        #: refused above.
        separator = "" if existing.endswith("\n") else "\n"
        destination.write_text(existing + separator + BLOCK, encoding="utf-8")
    return Step(destination, "appended", True)


def mcp_extra_installed() -> bool:
    """Is the ``mcp`` extra present, so the server this registers can start?

    Asked because the two halves come apart: the config is written by the base
    package, and the server it points at needs an extra the base package does
    not pull in. Without this the command reports success and the user meets the
    failure later, inside their editor, as a server that will not start -- which
    is both the least informative place to learn it and the hardest to trace
    back to here.

    ``find_spec`` rather than ``import``: this runs on the base install, and
    importing the protocol here would put it in the base package's import graph,
    which is the exact thing the extra exists to avoid.
    """
    from importlib.util import find_spec

    try:
        return find_spec("mcp") is not None
    except (ImportError, ValueError):  # pragma: no cover - a broken partial install
        return False


def install(home: Path, *, dry_run: bool = False) -> list[Step]:
    """Every destination, in host order: Claude Code first, then Codex.

    Both hosts are always written. Detecting which one is "installed" would mean
    guessing from directories that exist for other reasons, and guessing wrong in
    the quiet direction -- writing nothing for a host the user does have -- is
    the failure this command exists to prevent. Writing a config for a host that
    is absent costs a few unread files.
    """
    return [
        install_claude_code_plugin(home, dry_run=dry_run),
        install_skill(home, dry_run=dry_run),
        install_codex_mcp(home, dry_run=dry_run),
    ]

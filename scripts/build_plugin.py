#!/usr/bin/env python
"""Generate the marketplace plugin tree from the canonical payload.

    python scripts/build_plugin.py            # write it
    python scripts/build_plugin.py --check    # fail if anything has drifted

The plugin payload ships to **three** places, and only one of them can be the
file people edit:

* ``src/convilyn/agent/`` -- the canonical copy. It lives under ``src/`` because
  that is what the wheel ships, and the wheel is what ``convilyn agent install``
  reads when it writes a skills-directory plugin into ``~/.claude/skills/``.
* ``plugins/convilyn/`` -- the marketplace source, generated here. It is what
  ``/plugin marketplace add`` resolves from the public mirror.
* ``~/.claude/skills/convilyn/`` -- written at install time by
  ``convilyn.agent.install`` from the wheel copy. Not this script's business.

Before this script owned them, the two manifests under ``plugins/convilyn/``
were hand-maintained, so adding the third consumer would have made three
hand-maintained copies of the same JSON. The copy that drifts is always the one
nobody is looking at.

**Bytes, not text.** ``write_bytes``, never ``write_text``: on Windows the
latter translates ``\\n`` to ``\\r\\n``, so the file written differs from the
file compared and ``--check`` reports drift on a machine that just ran the
generator.

**Why the JSON copies carry no "generated" marker.** JSON has no comments, and
an extra key is not free here: ``claude plugin validate --strict`` reports an
unrecognized field as an error, so a ``_generated`` marker would fail the
official validator. The marker lives in ``plugins/convilyn/README.md`` instead,
and drift is caught by ``--check`` rather than by a reader noticing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO / "src" / "convilyn" / "agent"
PLUGIN_DIR = REPO / "plugins" / "convilyn"

#: Placed after the YAML frontmatter, never before it -- a comment above the
#: opening ``---`` stops the frontmatter being frontmatter, and the skill then
#: has no name and no description.
BANNER = (
    "<!-- Generated from src/convilyn/agent/SKILL.md. Edit that file and run\n"
    "     python scripts/build_plugin.py -- this copy is checked in CI. -->\n"
)

#: The canonical files carry no leading dot. The plugin layout needs
#: ``.mcp.json`` and ``.claude-plugin/plugin.json``, but a dotfile under
#: ``src/`` is one packaging tool away from being silently skipped, and a
#: payload the wheel does not carry breaks the install path that reads it.
SKILL = "SKILL.md"
PLUGIN_JSON = "plugin.json"
MCP_JSON = "mcp.json"


class DriftError(RuntimeError):
    """The generated copy is not what the generator would write."""


def _split_frontmatter(text: str, source: Path) -> tuple[str, str]:
    """Return ``(frontmatter_including_delimiters, body)``.

    Refuses rather than guesses: a skill with no frontmatter has no ``name`` and
    no ``description``, so it is not a skill, and silently banner-ing the top of
    it would produce a file that looks fine and is never loaded.
    """
    if not text.startswith("---\n"):
        raise DriftError(f"{source} does not open with YAML frontmatter.")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise DriftError(f"{source} has an unterminated frontmatter block.")
    cut = end + len("\n---\n")
    return text[:cut], text[cut:]


def _render_skill(source: Path) -> bytes:
    frontmatter, body = _split_frontmatter(source.read_text(encoding="utf-8"), source)
    return (frontmatter + BANNER + body).encode("utf-8")


def _render_verbatim(source: Path) -> bytes:
    return source.read_bytes()


#: ``(canonical name, path under plugins/convilyn/, renderer)``.
#:
#: The marketplace copy keeps the ``skills/<name>/SKILL.md`` layout. The
#: install-time copy uses the single-skill-at-plugin-root form instead, which
#: the plugin docs allow and ``claude plugin init`` itself produces -- that is
#: ``install.py``'s concern, not this script's.
OUTPUTS: tuple[tuple[str, str, object], ...] = (
    (SKILL, "skills/convilyn/SKILL.md", _render_skill),
    (PLUGIN_JSON, ".claude-plugin/plugin.json", _render_verbatim),
    (MCP_JSON, ".mcp.json", _render_verbatim),
)


def _plan() -> list[tuple[Path, Path, bytes]]:
    plan: list[tuple[Path, Path, bytes]] = []
    for name, relative, render in OUTPUTS:
        source = SOURCE_DIR / name
        if not source.is_file():
            raise DriftError(f"canonical payload missing: {source.relative_to(REPO)}")
        plan.append((source, PLUGIN_DIR / relative, render(source)))  # type: ignore[operator]
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Report drift; write nothing.")
    args = parser.parse_args(argv)

    plan = _plan()

    if args.check:
        drifted = False
        for source, target, expected in plan:
            if not target.is_file():
                print(f"MISSING: {target.relative_to(REPO)}", file=sys.stderr)
                drifted = True
            elif target.read_bytes() != expected:
                print(
                    f"DRIFT: {target.relative_to(REPO)} is not what "
                    f"{source.relative_to(REPO)} would generate.",
                    file=sys.stderr,
                )
                drifted = True
        if drifted:
            print("Run: python scripts/build_plugin.py", file=sys.stderr)
            return 1
        print(f"in step: {len(plan)} generated file(s) under {PLUGIN_DIR.relative_to(REPO)}")
        return 0

    for _source, target, expected in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
        print(f"wrote {target.relative_to(REPO)} ({len(expected)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

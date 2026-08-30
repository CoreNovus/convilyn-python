"""The agent-facing surface: the plugin payload, and the command that installs it.

``SKILL.md``, ``plugin.json`` and ``mcp.json`` beside this module are the
canonical copies, and they live here rather than beside the plugin because this
is what the wheel ships -- ``install`` reads them out of the INSTALLED package,
on a machine where there is no repository to fall back to. The marketplace copy
under ``plugins/convilyn/`` is generated from them by
``scripts/build_plugin.py``, and a gate check keeps the two in step.

The canonical names carry no leading dot. The plugin layout needs ``.mcp.json``
and ``.claude-plugin/plugin.json``; a dotfile under ``src/`` is one packaging
tool away from being silently skipped, and a payload the wheel does not carry
breaks the install path outright. The dot is added at the destination.

The import below is eager, and that is deliberate. It was lazy first, on the
reasoning that keeps optional dependencies out of ``convilyn.cli.local`` -- but
that reasoning does not transfer: nothing in ``convilyn/__init__.py`` imports
this package, so a plain ``import convilyn`` never reaches it, and the module it
guards pulls in nothing beyond the standard library. A lazy accessor with no
cost to avoid is the indirection with one caller that the house rules reject.
"""

from __future__ import annotations

from convilyn.agent.install import install

__all__ = ["install"]

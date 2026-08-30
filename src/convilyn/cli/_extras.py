"""The one sentence that tells a user how to install an extra.

Authored once, in :mod:`convilyn.local._routes`, and re-exported here. A second
copy is a sentence that drifts — one gets ``uv add`` and the other ``pip
install``, and the user meets whichever code path they happened to reach.

**The direction is deliberate and was corrected after measuring.** The obvious
move is to put the sentence here and have ``local`` import it, since ``cli`` is
where user-facing prose lives. That is a circular import: ``convilyn.cli``
imports the client, so ``convilyn.local`` importing ``convilyn.cli`` fails on
``convilyn/__init__.py`` mid-initialisation — observed, not predicted. It is
also backwards as layering: ``AGENT.md`` places ``convilyn.local`` below the CLI
precisely so it stays usable without one. ``cli`` may depend on ``local``; the
reverse may not.

So the canonical string stays in the lower layer beside the requirement objects
that carry it, and this module is the name the CLI half reaches for.
"""

from __future__ import annotations

from convilyn.local._routes import _install_command


def install_command(extra: str) -> str:
    """Both spellings, because a user has one of the two tools and not the other."""
    return _install_command(extra)

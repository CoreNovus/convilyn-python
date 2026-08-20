"""Answer "can this machine do X" without paying for X.

Every optional dependency in this namespace is heavy, and the questions callers
ask most often — what can I convert, what am I missing — must not import any of
them. ``convilyn local formats`` on a machine with everything installed would
otherwise pull pdfplumber, python-pptx and Pillow just to print a table.

So availability is decided by ``importlib.util.find_spec``, which consults the
import system's finders and stops before executing the module. "Installed" is
deliberately weaker than "imports successfully": a package that is present but
broken reports available here and fails at conversion time with a real error,
which is the more useful split. The alternative — importing to be sure — makes
the cheap question as expensive as the expensive one.

Nothing here raises. An absent dependency is a fact about the host, not an
error, and the caller asking the question is precisely the caller who has not
decided what to do about it yet.

The probes are injectable so both arms are testable in an environment that will
only ever have one of them. That is not a hypothetical convenience: CI runs with
the extras installed, so the "missing" arm would never execute otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

#: Signature of a package probe — takes an import name, says whether it resolves.
PackageProbe = Callable[[str], bool]

#: Signature of an executable probe — takes a command name, returns its path.
ExecutableProbe = Callable[[str], str | None]


def package_available(
    import_name: str,
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> bool:
    """True when ``import_name`` resolves on this interpreter.

    Takes the **import** name, not the distribution name: ``python-docx`` is
    installed but imported as ``docx``, and confusing the two is the standard
    way this check silently reports the wrong answer.
    """
    if import_name in sys.modules:
        return True
    try:
        return find_spec(import_name) is not None
    except (ImportError, ValueError):
        # A namespace package with a broken parent raises rather than returning
        # None. Unimportable is unavailable; it is not this function's job to
        # decide whether that is surprising.
        return False


def executable_path(
    command: str,
    *,
    extra_locations: Iterable[Path] = (),
    which: ExecutableProbe = shutil.which,
) -> Path | None:
    """Locate ``command`` on PATH, then in ``extra_locations``.

    PATH first, always. A tool the user has deliberately put on their PATH is
    the one they mean, even when a bundled copy exists somewhere this SDK knows
    about.

    ``extra_locations`` covers the desktop installers that do not modify PATH —
    which is the normal case for office suites on Windows and macOS, and the
    reason a bare ``shutil.which`` reports "not installed" on a machine where
    the application is sitting in the Start menu.
    """
    found = which(command)
    if found:
        return Path(found)

    for candidate in extra_locations:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


__all__ = [
    "ExecutableProbe",
    "PackageProbe",
    "executable_path",
    "package_available",
]

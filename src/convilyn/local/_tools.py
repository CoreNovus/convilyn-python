"""Where the external programs live on a desktop machine.

Two conversions need a program that cannot be installed from PyPI: legacy and
OpenDocument office files go through LibreOffice, and ebooks go through Calibre.
Both are ordinary desktop applications, and neither installer reliably puts its
command-line entry point on ``PATH`` — LibreOffice on Windows and macOS notably
does not.

So this carries a per-platform candidate list beside the ``PATH`` lookup. The
lists are short, and deliberately name only the locations the official installers
use. Guessing more broadly would find a copy the user did not intend to run.

The paths here are a *desktop* fact, and are not shared with the server-side
conversion pipeline: what a container image puts under ``/opt`` has nothing to do
with where a person's laptop keeps an application, and a list trying to serve
both would be wrong for each.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from convilyn.local._probe import ExecutableProbe, executable_path


@dataclass(frozen=True)
class ExternalTool:
    """An external program a conversion route may require."""

    #: Stable identifier used in `Requirement.name` and in CLI output.
    key: str
    #: Human name, as the user would search for it.
    display_name: str
    #: The command this SDK actually runs.
    command: str
    #: Where to get it, shown verbatim when it is missing.
    download_url: str


LIBREOFFICE = ExternalTool(
    key="libreoffice",
    display_name="LibreOffice",
    command="soffice",
    download_url="https://www.libreoffice.org/download/",
)

CALIBRE = ExternalTool(
    key="calibre",
    display_name="Calibre",
    command="ebook-convert",
    download_url="https://calibre-ebook.com/download",
)

#: Every tool this namespace knows about, keyed as it appears in a requirement.
TOOLS: dict[str, ExternalTool] = {tool.key: tool for tool in (LIBREOFFICE, CALIBRE)}

_WINDOWS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "libreoffice": (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ),
    "calibre": (
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
    ),
}

_MACOS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "libreoffice": ("/Applications/LibreOffice.app/Contents/MacOS/soffice",),
    "calibre": ("/Applications/calibre.app/Contents/MacOS/ebook-convert",),
}


def _candidates(tool: ExternalTool) -> tuple[Path, ...]:
    """Install locations to try after PATH, for the running platform only."""
    if sys.platform == "win32":
        table = _WINDOWS_CANDIDATES
    elif sys.platform == "darwin":
        table = _MACOS_CANDIDATES
    else:
        # Linux packagers put the command on PATH. A distribution that does not
        # is better served by the user's own symlink than by a guess here.
        return ()
    return tuple(Path(p) for p in table.get(tool.key, ()))


def find_tool(tool: ExternalTool, *, which: ExecutableProbe | None = None) -> Path | None:
    """Locate ``tool``, or ``None`` when this machine does not have it."""
    return executable_path(
        tool.command,
        extra_locations=_candidates(tool),
        which=which if which is not None else shutil.which,
    )


#: "Where is this tool", as one question.
#:
#: This is the seam callers inject, rather than the lower-level ``which``.
#: Injecting ``which`` alone does not isolate anything: the lookup falls through
#: to the platform's install locations, and on a machine that genuinely has
#: LibreOffice it finds the real one. A test that meant to describe a bare
#: machine would then quietly describe this one.
ToolProbe = Callable[[ExternalTool], Path | None]


def install_hint(tool: ExternalTool) -> str:
    """One sentence telling the user how to get ``tool``.

    Has to stand alone — it is what ``convilyn local doctor`` prints beside a
    missing tool — so it names the command as well as the download. Callers that
    have already said which command they need should not repeat it.
    """
    return f"Install {tool.display_name} from {tool.download_url} (provides `{tool.command}`)."


__all__ = [
    "CALIBRE",
    "LIBREOFFICE",
    "TOOLS",
    "ExternalTool",
    "ToolProbe",
    "find_tool",
    "install_hint",
]

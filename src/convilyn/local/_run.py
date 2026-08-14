"""Invoke the external programs. Internal.

Separate from :mod:`convilyn.local._tools`, which is the catalogue of what
those programs *are* and where they live, because the two answer different
questions at different times. "Do I have LibreOffice" is asked whenever
somebody lists what this machine can convert — cheaply, and often. "Run
LibreOffice" happens only when a conversion actually starts.

Keeping them apart is also what lets the generated bridge depend on the runner
without dragging in the discovery tables, and lets the route builder depend on
discovery without being able to start a subprocess.
"""

from __future__ import annotations

import os
import subprocess
from functools import cache
from pathlib import Path

from convilyn.local._tools import CALIBRE, LIBREOFFICE, find_tool, install_hint

#: How long one office-suite or ebook conversion may take before it is killed.
#:
#: Generous on purpose. A large presentation on a laptop is genuinely slow, and
#: a conversion killed halfway is a worse outcome than one that took a minute.
CONVERSION_TIMEOUT_SECONDS = 300


class ToolNotInstalledError(RuntimeError):
    """The external program a conversion needs is not on this machine.

    Its message is the tool's own install hint, so the sentence a caller sees
    here is the same one ``capabilities()`` reports.
    """


def _run(argv: list[str], *, timeout: int) -> tuple[int, str, str]:
    """Run ``argv`` and return ``(returncode, stdout, stderr)``.

    A list, never a shell string. Every element carries a path the user chose,
    and a shell would give those paths a second meaning.

    Output is decoded with ``errors="replace"``: these programs write localised
    messages in the console's own encoding, and a conversion must not fail on
    the encoding of its own error message.
    """
    completed = subprocess.run(  # noqa: S603 — list argv, no shell, fixed executables
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", errors="replace"),
        completed.stderr.decode("utf-8", errors="replace"),
    )


def run_libreoffice(
    input_path: str,
    output_dir: str,
    target_format: str,
    filter_name: str | None = None,
    timeout: int = CONVERSION_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Convert a file with LibreOffice, headless.

    LibreOffice names its output after the *input's* stem and cannot be told
    otherwise, which is why the caller searches ``output_dir`` afterwards rather
    than being handed a path.

    Raises:
        ToolNotInstalledError: LibreOffice is not on this machine.
        subprocess.TimeoutExpired: the conversion exceeded ``timeout``.
    """
    soffice = find_tool(LIBREOFFICE)
    if soffice is None:
        raise ToolNotInstalledError(install_hint(LIBREOFFICE))

    convert_arg = f"{target_format}:{filter_name}" if filter_name else target_format
    return _run(
        [
            str(soffice),
            f"-env:UserInstallation={_private_profile()}",
            "--headless",
            "--convert-to",
            convert_arg,
            "--outdir",
            output_dir,
            input_path,
        ],
        timeout=timeout,
    )


@cache
def _private_profile() -> str:
    """A ``file://`` URL for this package's own LibreOffice profile.

    LibreOffice serialises on its user profile: a second ``soffice`` started
    against a profile another instance holds does not queue, it exits non-zero
    having converted nothing, and the failure arrives as "conversion failed"
    with nothing in stderr to act on. The common trigger is not exotic — a user
    with LibreOffice open on their desktop converts one file and it fails.

    So conversions run against a profile of our own. It is **persistent, not
    temporary**, and that is the whole design rather than an implementation
    detail: LibreOffice pays a first-run initialisation the first time it sees
    a profile, measured here at ~19 s. A temp profile per process pays that on
    every run — which is a poor trade against the desktop-collision it fixes,
    and would be charged to somebody converting a single file. A directory
    under the user's cache pays it once per machine.

    Deleting it is always safe; it is rebuilt on the next conversion.
    """
    path = _cache_root() / "libreoffice-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path.as_uri()


def _cache_root() -> Path:
    """This package's cache directory, per the platform's convention.

    Hand-rolled rather than taking a dependency on ``platformdirs``: this is the
    only cache this package keeps, and one directory is not worth a package in
    everybody's install (see ``docs/QUICKSTART.md`` on what each extra pulls in).
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base or Path.home() / "AppData" / "Local") / "convilyn" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "convilyn"


def run_calibre(
    input_path: str,
    output_path: str,
    timeout: int = CONVERSION_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Convert an ebook with Calibre's ``ebook-convert``.

    Unlike LibreOffice this takes an explicit output path and derives the format
    from its extension, so there is nothing to search for afterwards.

    Raises:
        ToolNotInstalledError: Calibre is not on this machine.
        subprocess.TimeoutExpired: the conversion exceeded ``timeout``.
    """
    ebook_convert = find_tool(CALIBRE)
    if ebook_convert is None:
        raise ToolNotInstalledError(install_hint(CALIBRE))

    return _run([str(ebook_convert), input_path, output_path], timeout=timeout)


__all__ = [
    "CONVERSION_TIMEOUT_SECONDS",
    "ToolNotInstalledError",
    "run_calibre",
    "run_libreoffice",
]

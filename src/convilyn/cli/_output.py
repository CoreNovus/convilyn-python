"""Output rendering for the CLI — JSON vs human-friendly.

Two renderers implement the :class:`OutputRenderer` Protocol so each
sub-command can stay format-agnostic. The Protocol exposes exactly two
methods:

* ``event(kind, **fields)`` — narrate progress (rendered as a status
  line by :class:`HumanRenderer`, swallowed by :class:`JsonRenderer` so
  the JSON-mode stdout is a single valid object).
* ``final(payload)`` — flush the terminal summary. The human renderer
  prints a ✓ line; the JSON renderer emits ``json.dumps(payload)``.

Separating render from sub-command logic is the SRP / OCP seam — adding
a future YAML / table format is a new renderer class, no change to
``convert.py`` / ``doctor.py``.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any, Protocol, runtime_checkable


@runtime_checkable
class OutputRenderer(Protocol):
    """A formatter for CLI output, decoupled from the originating command."""

    def event(self, kind: str, **fields: Any) -> None: ...

    def final(self, payload: dict[str, Any]) -> None: ...


class HumanRenderer:
    """Pretty, line-oriented output for interactive shells.

    Status lines go to ``stderr`` so callers piping ``stdout`` to ``jq``
    or similar still get clean machine-readable data when they pair
    ``--json`` with a redirected pipe.
    """

    def __init__(
        self,
        *,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
    ) -> None:
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr

    def event(self, kind: str, **fields: Any) -> None:
        # Map event kinds to a leading glyph for at-a-glance scanning.
        glyph = _GLYPHS.get(kind, "•")
        message = fields.get("message") or _format_event(kind, fields)
        _write(f"{glyph} {message}", self._stderr)

    def final(self, payload: dict[str, Any]) -> None:
        # The summary line goes to stdout so a caller redirecting
        # ``> result.txt`` still captures a sensible one-liner.
        summary = payload.get("summary") or _format_summary(payload)
        _write(summary, self._stdout)


def _write(line: str, stream: IO[str]) -> None:
    """Print a line, degrading rather than crashing on a narrow codepage.

    The glyphs and typographic punctuation this CLI prints are not
    representable everywhere: a Windows console left on ``cp437`` cannot encode
    ``✓`` or an em dash, and Python raises ``UnicodeEncodeError`` from inside
    ``print`` rather than dropping the character. That turns a successful
    conversion into a traceback **after the file was already written**, which
    is the worst of both — the work happened and the user is told it failed.

    Retried with ``backslashreplace`` rather than dropping the line, so an
    unprintable character degrades to a visible escape and the message survives.
    """
    try:
        print(line, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(line.encode(encoding, errors="backslashreplace").decode(encoding), file=stream)


class JsonRenderer:
    """Emits a single JSON object on stdout — events are swallowed.

    AI agents and shell pipelines (e.g. ``convilyn convert ... --json |
    jq``) require a single deterministic JSON document; we therefore
    drop progress events on the floor in JSON mode.
    """

    def __init__(
        self,
        *,
        stdout: IO[str] | None = None,
    ) -> None:
        self._stdout = stdout or sys.stdout

    def event(self, kind: str, **fields: Any) -> None:
        # Deliberately silent — see class docstring.
        return None

    def final(self, payload: dict[str, Any]) -> None:
        # ``ensure_ascii=True`` (the default) escapes non-ASCII to ``\uXXXX`` so
        # machine-readable JSON is portable across console codepages — writing a
        # raw glyph (e.g. ``✓`` from a job payload) crashes on a non-UTF-8
        # stdout such as a Windows ``cp950`` console. The escapes parse
        # identically for any JSON consumer.
        json.dump(payload, self._stdout, ensure_ascii=True, sort_keys=True)
        self._stdout.write("\n")


_GLYPHS: dict[str, str] = {
    "upload": "↑",
    "create": "▶",
    "wait": "…",
    "download": "↓",
    "ok": "✓",
    "warn": "!",
    "error": "✗",
}


def _format_event(kind: str, fields: dict[str, Any]) -> str:
    """Render a status event into a single line of human-readable text."""
    if kind == "upload" and "filename" in fields:
        size = fields.get("size_bytes")
        size_str = f" ({_humanise_size(size)})" if size else ""
        return f"Uploading {fields['filename']}{size_str}"
    if kind == "create" and "job_id" in fields:
        return f"Created job {fields['job_id']}"
    if kind == "wait":
        progress = fields.get("progress")
        if progress is not None:
            return f"Converting… {progress}%"
        return "Waiting for job to finish"
    if kind == "download" and "path" in fields:
        return f"Downloaded {fields['path']}"
    # Fallback: dump the fields readably.
    return f"{kind}: " + ", ".join(f"{k}={v}" for k, v in fields.items())


def _format_summary(payload: dict[str, Any]) -> str:
    """Best-effort one-liner summary for non-JSON mode."""
    output_path = payload.get("output_path")
    output_size = payload.get("output_size_bytes")
    if output_path and output_size is not None:
        return f"✓ {output_path} ({_humanise_size(output_size)})"
    if output_path:
        return f"✓ {output_path}"
    status = payload.get("status")
    if status:
        return f"✓ status={status}"
    return "✓ done"


def _humanise_size(size_bytes: int | None) -> str:
    """Render a byte count as KiB / MiB; matches `du -h` shape."""
    # Explicit None / zero check — `if not size_bytes` is ambiguous to
    # type-checkers (None and 0 are both falsy; pyright can't narrow
    # `int | None` to `int` through the branch).
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    remaining: float = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if remaining < 1024:
            return f"{remaining:.0f} {unit}" if unit == "B" else f"{remaining:.1f} {unit}"
        remaining /= 1024
    return f"{remaining:.1f} TiB"


def make_renderer(*, json_output: bool) -> OutputRenderer:
    """Pick the renderer based on the ``--json`` flag.

    Kept as a free function so sub-commands stay format-agnostic — they
    request `make_renderer(json_output=ctx.params['json_output'])` and
    pass the result down.
    """
    return JsonRenderer() if json_output else HumanRenderer()

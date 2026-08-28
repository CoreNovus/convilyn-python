"""The terminal banner ``convilyn setup`` prints before opening a browser.

A large block-letter "CONVILYN" wordmark, colored with the platform's own
multi-stop brand spectrum rather than a single accent hue.

**Wordmark provenance.** The block letters are the `pyfiglet
<https://github.com/pwaller/pyfiglet>`_ "ansi_shadow" font's output for
"CONVILYN" (``uvx pyfiglet -f ansi_shadow CONVILYN``), generated once as a
build-time tool and baked in as a literal, so the shipped package carries no
figlet dependency at runtime (this SDK's install footprint stays exactly
``httpx`` + ``pydantic`` + ``click``).

**Colour.** Earlier drafts paired a mascot character with a flat one- or
two-colour treatment; per operator direction this drops the character
entirely and instead applies ``--bp-spectrum-h`` — the platform's own
defined brand gradient (``frontend-web/src/styles/builder.css:112``,
documented in ``frontend-web/docs/design/asset-specs.md`` §"品牌光譜"):
``#7c3aed`` (violet, 0%) → ``#a855f7`` (light purple, 25%) → ``#e11d48``
(rose/red, 55%) → ``#f59e0b`` (amber, 100%). This is the same four-stop
sequence the web app already uses for its route line and OG-card top edge —
reused here rather than inventing a new gradient, so "add red, warm-orange,
purple, neon" resolves to an existing brand asset rather than an ad-hoc
guess. The gradient is applied per COLUMN (interpolated by each character's
horizontal position across the full wordmark width), not per row, so it
reads as one smooth horizontal sweep rather than six independently-colored
lines.

Truecolor ANSI (24-bit) throughout, so the rendered hues match the web brand
exactly on any terminal that supports it; on one that does not, the escapes
are typically either ignored or degrade to a nearby colour — a purely
cosmetic loss, never a functional one, since :func:`should_show_banner`
already limits this to an interactive TTY.

Printed ONLY from ``convilyn setup`` — no other command's output changes.
"""

from __future__ import annotations

import os
import sys

from convilyn.cli._output import write_line

_RESET = "\x1b[0m"

#: ``--bp-spectrum-h`` (frontend-web/src/styles/builder.css:112), as
#: (position 0..1, RGB) stops.
_SPECTRUM: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (124, 58, 237)),  # #7c3aed violet
    (0.25, (168, 85, 247)),  # #a855f7 light purple
    (0.55, (225, 29, 72)),  # #e11d48 rose/red
    (1.0, (245, 158, 11)),  # #f59e0b amber
)

#: `pyfiglet -f ansi_shadow CONVILYN`, verbatim — every row is 63 columns,
#: verified by the pin in ``test_banner.py``.
_WORDMARK_LINES: tuple[str, ...] = (
    " ██████╗ ██████╗ ███╗   ██╗██╗   ██╗██╗██╗  ██╗   ██╗███╗   ██╗",
    "██╔════╝██╔═══██╗████╗  ██║██║   ██║██║██║  ╚██╗ ██╔╝████╗  ██║",
    "██║     ██║   ██║██╔██╗ ██║██║   ██║██║██║   ╚████╔╝ ██╔██╗ ██║",
    "██║     ██║   ██║██║╚██╗██║╚██╗ ██╔╝██║██║    ╚██╔╝  ██║╚██╗██║",
    "╚██████╗╚██████╔╝██║ ╚████║ ╚████╔╝ ██║███████╗██║   ██║ ╚████║",
    " ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝  ╚═══╝  ╚═╝╚══════╝╚═╝   ╚═╝  ╚═══╝",
)
_WORDMARK_WIDTH = len(_WORDMARK_LINES[0])


def _spectrum_color(t: float) -> tuple[int, int, int]:
    """Interpolate ``_SPECTRUM`` at position ``t`` (0..1)."""
    for (t0, c0), (t1, c1) in zip(_SPECTRUM, _SPECTRUM[1:], strict=False):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(round(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))  # type: ignore[return-value]
    return _SPECTRUM[-1][1]


def _gradient_row(row: str) -> str:
    out: list[str] = []
    current: tuple[int, int, int] | None = None
    for col, ch in enumerate(row):
        if ch == " ":
            out.append(ch)
            continue
        color = _spectrum_color(col / (_WORDMARK_WIDTH - 1))
        if color != current:
            r, g, b = color
            out.append(f"\x1b[38;2;{r};{g};{b}m")
            current = color
        out.append(ch)
    out.append(_RESET)
    return "".join(out)


_ART: tuple[str, ...] = tuple(_gradient_row(row) for row in _WORDMARK_LINES)


def should_show_banner(*, json_output: bool) -> bool:
    """Whether the banner should print at all.

    Suppressed entirely (not merely de-colored) in ``--json`` mode, when
    stdout is not a TTY (piped/CI output), or when ``NO_COLOR`` is set — a
    static-art banner in captured output is noise, not a partial win.
    """
    return not json_output and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def print_banner() -> None:
    """Print the banner to stdout. Caller is responsible for gating via
    :func:`should_show_banner` first."""
    for line in _ART:
        write_line(line, sys.stdout)

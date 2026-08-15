"""What Pillow can actually read and write on THIS machine. Internal.

The platform publishes a matrix of image conversions it offers. That matrix is
the right answer for a server, whose codec set is fixed by its image. It is the
wrong answer here: a desktop's Pillow may or may not have the HEIC plugin, the
AVIF plugin, or the JPEG XL plugin, and a route table copied from the server
would claim conversions that fail the moment they are tried.

So capability is measured, not declared.

**Writability is measured by attempting a save**, not by reading Pillow's
``SAVE`` registry. The registry lists what a plugin *offers*; whether the offer
holds for a given build is a different question, and several formats answer it
differently on Windows than on Linux. A one-pixel save into memory settles it
in microseconds and cannot be wrong.

Readability is taken from the registered extensions, because opening requires a
file that is genuinely in that format — synthesising one per format would be a
codec implementation, which is the thing being probed.

Both answers are cached for the process. Plugins register at import time and do
not appear later, so re-probing would spend time to learn the same thing.
"""

from __future__ import annotations

import io
from functools import cache

#: Modes a format is offered in order of preference when probing a save. Some
#: encoders accept only one; trying a couple avoids reporting a format
#: unwritable when it is merely particular.
_PROBE_MODES = ("RGB", "RGBA", "L", "1")


@cache
def readable_extensions() -> frozenset[str]:
    """Lowercase extensions (no dot) Pillow can open here."""
    from PIL import Image

    Image.init()
    return frozenset(
        extension.lstrip(".").lower()
        for extension, name in Image.registered_extensions().items()
        if name in Image.OPEN
    )


@cache
def writable_formats() -> frozenset[str]:
    """Pillow format names that a one-pixel save actually succeeds for.

    Measured rather than read from ``Image.SAVE``: the registry says what an
    encoder claims, and a claim is not a build. A format whose plugin is
    present but whose backing library is missing appears in the registry and
    raises on use.
    """
    from PIL import Image

    Image.init()
    confirmed: set[str] = set()

    for name in Image.SAVE:
        for mode in _PROBE_MODES:
            try:
                Image.new(mode, (1, 1)).save(io.BytesIO(), format=name)
            except Exception:  # noqa: BLE001 — any failure means "not here"
                continue
            confirmed.add(name)
            break

    return frozenset(confirmed)


def can_read(extension: str) -> bool:
    """True when Pillow on this machine can open that extension."""
    return extension.lstrip(".").lower() in readable_extensions()


def can_write(pillow_format: str) -> bool:
    """True when a save in that Pillow format succeeds on this machine."""
    return pillow_format in writable_formats()


__all__ = ["can_read", "can_write", "readable_extensions", "writable_formats"]

"""Shared download-to-disk step for presigned storage URLs.

One implementation of the "write server bytes to a caller-chosen path"
contract, used by every resource that persists an artifact
(``convert.download_to``, ``goals.download_artifact_to``): the symlink
refusal and the size-capped streaming must stay byte-identical across
resources, so they live here instead of being copied per resource.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from convilyn._internal.http import HTTPClient


async def download_url_to_path(
    http: HTTPClient,
    url: str,
    to: str | os.PathLike[str],
) -> Path:
    """Stream ``url`` to ``to`` and return the resolved path.

    Refuses to write *through* an existing symlink: a pre-placed link could
    redirect the bytes to an unintended location (e.g. a dotfile or a path
    outside the intended directory). Writing to a regular path or a
    brand-new file is unaffected. The body is streamed to disk with a size
    cap rather than buffered whole — a very large (or hostile) response
    cannot exhaust RAM.
    """
    target = Path(os.fspath(to))
    if target.is_symlink():
        raise ValueError(
            f"Refusing to write to {target!r}: target is a symlink. "
            "Remove it or choose a non-symlink destination."
        )
    await http.external_get_to_file(url, target)
    return target

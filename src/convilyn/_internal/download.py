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
    *,
    overwrite: bool,
) -> Path:
    """Stream ``url`` to ``to`` and return the resolved path.

    Refuses to write *through* an existing symlink: a pre-placed link could
    redirect the bytes to an unintended location (e.g. a dotfile or a path
    outside the intended directory). Writing to a regular path or a
    brand-new file is unaffected. The body is streamed to disk with a size
    cap rather than buffered whole — a very large (or hostile) response
    cannot exhaust RAM.

    Refuses an existing destination unless ``overwrite`` is set, raising
    ``FileExistsError`` with the same wording ``convilyn.local.convert``
    uses — because it is the same decision, and a package that answers it
    one way offline and the other way online has not answered it.

    ``overwrite`` is **required**, not defaulted. Every caller here writes
    bytes to a path a user chose, so "what happens to the file already
    there" is a position each entry point has to take rather than inherit;
    a default is how one of them quietly ends up without one, which is the
    defect this parameter was added to remove.
    """
    target = Path(os.fspath(to))
    if target.is_symlink():
        raise ValueError(
            f"Refusing to write to {target!r}: target is a symlink. "
            "Remove it or choose a non-symlink destination."
        )
    # After the symlink refusal, so the more specific hazard keeps its own
    # message: a symlink is not merely "a file that is in the way".
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; pass overwrite=True to replace it")
    await http.external_get_to_file(url, target)
    return target

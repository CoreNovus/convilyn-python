"""Write a file by replacing it, never by truncating it where it stands.

``Path.write_text`` / ``Path.write_bytes`` open with ``O_TRUNC`` and then write,
so anything that stops the process in between — a Ctrl-C, a full disk, a step
that failed after this point — leaves a truncated or empty file behind, having
already destroyed what was there.

That is not theoretical here. It cost a credentials file first: external testing
reached a zero-byte ``credentials.json`` and could not leave it, because
:func:`~convilyn._internal.credentials.read_credentials` answered "no
credential" while the server still held an active key minted under the same
machine name and refused to mint a second. The local file said one thing, the
server said the opposite, and the user was between them with nothing on the
machine to fix it.

The installer (:mod:`convilyn.agent.install`) has the same shape on a worse
file. ``~/.codex/config.toml`` is **the user's**, other tools write it, and what
a truncation loses is their whole Codex configuration — including the content
that command exists to preserve. Its own docstring makes the promise this
module is what keeps: *"A config that cannot be extended safely is reported and
left exactly as it was; nothing here rewrites a line it did not add."*

Extracted from ``credentials.write_credentials``, which had the whole pattern
and was its only site. Four more call sites in the installer made it a
relocation rather than a new abstraction — no new variation point, nothing
reserved for a future that has not arrived.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

#: Mode for a file only its owner should read. Enforced by
#: :func:`tempfile.mkstemp`, which opens ``0o600`` in ONE step rather than
#: ``open()`` followed by a separate ``chmod`` — the latter leaves a window
#: where the file is briefly world-readable before its permissions narrow.
OWNER_ONLY = 0o600

#: Mode for a file that carries no secret — the agent-host manifests and
#: skills the installer writes.
#:
#: An explicit literal rather than the ``0o666 & ~umask`` that ``open()`` would
#: have applied, and that is a deliberate choice in both directions:
#: :func:`tempfile.mkstemp` always creates ``0o600``, so doing nothing would
#: SILENTLY NARROW files whose mode this package never used to set, while a
#: umask-derived mode differs per machine. This package already argues against
#: the second shape where it declined to build a check on :mod:`mimetypes`
#: (``resources/convert.py``): a result keyed on the machine rather than on the
#: data answers differently in different places for no stated reason. ``0o644``
#: is what the default umask of ``022`` produces, so nothing changes for
#: almost everyone, and what does change is written down.
WORLD_READABLE = 0o644


def write_atomically(path: Path, payload: bytes, *, mode: int) -> None:
    """Write ``payload`` to ``path``, replacing it in one indivisible step.

    The staged file is created in the SAME directory as ``path``, because
    :func:`os.replace` is only atomic within one filesystem. It is removed if
    anything goes wrong before the rename, so a failure leaves neither a
    half-written target nor a stray temp file.

    :func:`os.replace` is atomic on POSIX and on Windows, so a concurrent
    reader sees the whole old file or the whole new one, never a partial one.

    Callers pass ``mode`` explicitly — :data:`OWNER_ONLY` for a credential,
    :data:`WORLD_READABLE` for a manifest — so the permission is a decision at
    the call site rather than a side effect of how the temp file happened to be
    created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            #: Rename orders the metadata, not the DATA. Without this a crash
            #: just after the rename can leave the new name pointing at an
            #: empty file — the same zero-byte state, reached the long way.
            os.fsync(handle.fileno())
        if mode != OWNER_ONLY:
            os.chmod(staged, mode)
        os.replace(staged, path)
    except BaseException:
        #: BaseException, not Exception: the interruption this exists to
        #: survive is Ctrl-C, and `except Exception` does not catch it — it
        #: would leave the staged file behind on the one path that matters
        #: most. Cleanup failure is swallowed because the caller's problem is
        #: the original exception, not the stray file.
        with suppress(OSError):
            os.unlink(staged)
        raise

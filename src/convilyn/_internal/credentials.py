"""Local credentials file — the persisted output of ``convilyn setup``.

This is the third and lowest-priority source in
:func:`convilyn._internal.auth.resolve_auth`'s precedence chain (explicit
``api_key`` arg → ``CONVILYN_API_KEY`` env var → this file). It exists so a
browser login only ever has to run once per machine; it never competes with
either of the two higher-priority sources — see that module's docstring for
the precedence guarantee.

Path follows the platform's config-directory convention, hand-rolled the same
way :func:`convilyn.local._run._cache_root` is (no ``platformdirs``
dependency — one directory is not worth a package in everybody's install):
``$XDG_CONFIG_HOME/convilyn`` (falling back to ``~/.config/convilyn``) on
POSIX, ``%APPDATA%\\convilyn`` on Windows. Deliberately the **config**, not
cache, directory — a credential must survive a cache-clearing sweep and
should not share a directory with disposable temp/profile data.

**Windows limitation, stated rather than papered over**: the ``0o600``
permission mode passed to :func:`os.open` has no effect on NTFS ACLs. This
module does not attempt Windows ACL hardening; the POSIX permission
narrowing below is real, the Windows one is a no-op.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path


def config_root() -> Path:
    """This package's config directory, per the platform's convention."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base or Path.home() / "AppData" / "Roaming") / "convilyn"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "convilyn"


def credentials_path() -> Path:
    """Full path to the credentials file."""
    return config_root() / "credentials.json"


def write_credentials(key: str, *, source: str = "setup") -> Path:
    """Persist ``key`` as the file-based auth source. Returns the path written.

    Creates the config directory (mode ``0700`` on POSIX) and writes the file
    via ``os.open(..., 0o600)`` rather than ``open()`` followed by a separate
    ``chmod`` — the latter leaves a window where the file is briefly
    world-readable before its permissions are narrowed.
    """
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    payload = json.dumps(
        {
            "api_key": key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    return path


def read_credentials() -> str | None:
    """Return the stored API key, or ``None`` on any missing/corrupt file.

    Never raises — this is a fallback auth source, not a required one, so a
    missing or malformed file just means "no credential from this source",
    exactly like an unset environment variable.
    """
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = data.get("api_key")
    return key if isinstance(key, str) and key else None


def credentials_file_mode() -> int | None:
    """POSIX permission bits of the credentials file, or ``None``.

    ``None`` on Windows (no equivalent check), on a missing file, or on any
    stat failure — used by ``convilyn doctor`` to warn when the file is more
    permissive than the ``0600`` it was created with.
    """
    if os.name == "nt":
        return None
    try:
        return stat.S_IMODE(credentials_path().stat().st_mode)
    except OSError:
        return None

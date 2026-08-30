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

**Windows**: the ``0o600`` mode passed to :func:`os.open` has no effect on NTFS
ACLs. Protection there comes from the ACL the file inherits from ``%APPDATA%``,
which by default grants only the user, ``Administrators`` and ``SYSTEM`` —
measured, and equivalent in practice to POSIX ``0600`` (owner plus root).

What inheritance does NOT give is a guarantee, and that is the gap
:func:`broad_principals_with_access` closes. The ACL is whatever the parent
directory happens to carry, so a redirected ``%APPDATA%`` (a roaming profile, a
share, a restored backup) can hand the file a permissive ACL with nothing
noticing. Verified rather than assumed: writing credentials under a directory
granted to ``BUILTIN\\Users`` produces a credentials file readable by every
local account.

This module still does not *rewrite* ACLs — see that function's docstring for
why detection rather than enforcement.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
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


#: Principals whose presence on the credentials file's ACL means "more than
#: this machine's owner can read your API key".
#:
#: Matched on the name AFTER the domain prefix (`BUILTIN\Users` → `users`), and
#: on the well-known SID too, because `icacls` prints a raw SID whenever it
#: cannot resolve the name — which is exactly the case a name list would miss.
#:
#: This is a **blocklist of things known to be broad, not an allowlist of things
#: known to be safe**, and the direction is deliberate. An allowlist would have
#: to enumerate every legitimate principal on every Windows edition, domain and
#: display language, and each one it did not know about would raise a false
#: alarm — which is how a warning stops being read. The cost is the opposite
#: error: an exotic localised group name could slip past. The SID column is what
#: keeps that narrow, since SIDs do not translate.
_BROAD_PRINCIPALS: frozenset[str] = frozenset(
    {
        "everyone",
        "s-1-1-0",
        "users",
        "s-1-5-32-545",
        "authenticated users",
        "s-1-5-11",
        "guests",
        "s-1-5-32-546",
        "guest",
        "s-1-5-7",  # ANONYMOUS LOGON
    }
)


def parse_icacls_principals(output: str, *, target: str = "") -> frozenset[str]:
    """Principal names from ``icacls`` output, lowercased, domain stripped.

    Split out from the subprocess call so it can be tested on any OS with
    captured text — the parsing is the part that can be wrong, and pinning it to
    a Windows-only test would mean it was exercised on nobody's CI.

    Only ACE lines are read. ``icacls`` ends with a localised summary ("已成功
    處理 1 個檔案"), and on a zh-TW machine the ACE lines came back pure ASCII
    while that trailer did not — so the parser keys on the
    ``PRINCIPAL:(flags)`` shape and ignores everything else, rather than trying
    to skip a line whose wording depends on the display language.

    ``target`` is the path that was passed to ``icacls``; it prefixes the FIRST
    ACE line and is stripped by exact match. Trimming it by splitting on
    whitespace instead is what a first attempt did, and it is wrong in both
    directions: a principal containing a space (``NT AUTHORITY\\Authenticated
    Users``) loses its first word, and a path containing one (``C:\\Program
    Files\\…``) leaks into the principal. The exact prefix is known, so guessing
    at it is unnecessary.
    """
    principals: set[str] = set()
    for raw in output.splitlines():
        line = raw
        if target and line.startswith(target):
            line = line[len(target) :]
        candidate = line.strip()
        if not candidate or "(" not in candidate:
            continue
        head = candidate.split("(", 1)[0]
        if not head.endswith(":"):
            continue
        principals.add(head[:-1].split("\\")[-1].strip().lower())
    return frozenset(principals)


def broad_principals_with_access() -> frozenset[str] | None:
    """Broadly-scoped principals on the credentials file's ACL, or ``None``.

    ``None`` means "not measured" — not Windows, no file, or ``icacls`` could
    not be run. An empty set means measured and clean. `convilyn doctor` renders
    those two differently on purpose: a check that cannot distinguish "I looked
    and it is fine" from "I did not look" is one whose green means nothing.

    **Detection, not enforcement.** Rewriting the ACL (``icacls /inheritance:r
    /grant:r``) was considered and rejected: it can fail halfway, it behaves
    differently on domain-joined machines, and getting it wrong locks a user out
    of their own credentials file to fix a condition that requires an attacker
    who can already run code as them. The realistic exposure is a misconfigured
    ``%APPDATA%``, and telling the user about it is the proportionate response.

    ``icacls`` is located through ``%SystemRoot%`` rather than ``shutil.which``.
    ``which`` returned ``None`` under a Git Bash-inherited ``PATH`` (whose
    entries are POSIX-form and unresolvable to Windows), so relying on it would
    make the check silently skip in exactly the developer environment most
    likely to run it.
    """
    if os.name != "nt":
        return None
    path = credentials_path()
    if not path.exists():
        return None

    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    icacls = Path(system_root) / "System32" / "icacls.exe"
    if not icacls.is_file():
        return None

    try:
        completed = subprocess.run(  # noqa: S603 - fixed absolute path, no shell
            [str(icacls), str(path)],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    # `errors="replace"` because only the trailing summary line is localised and
    # it is discarded anyway; a decode error must not turn into "no finding".
    text = completed.stdout.decode("utf-8", errors="replace")
    return frozenset(parse_icacls_principals(text, target=str(path)) & _BROAD_PRINCIPALS)

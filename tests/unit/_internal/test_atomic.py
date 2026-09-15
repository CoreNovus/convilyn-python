"""`write_atomically` — replace, never truncate.

The failure it exists to survive is an interruption BETWEEN the truncate and
the flush, which `Path.write_text` makes reachable and which cost a zero-byte
`credentials.json` in external testing. The same shape sits one directory over
in `agent/install.py`, on `~/.codex/config.toml` — a file this package does not
own and other tools also write.

**The interruption is `KeyboardInterrupt`, so every test here raises
`BaseException`, not `Exception`.** A test that raised `Exception` would pass
against an `except Exception` implementation and prove the opposite of what it
claims — the cleanup would not run on the one path that matters most.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from convilyn._internal.atomic import OWNER_ONLY, WORLD_READABLE, write_atomically


def _staged_siblings(directory: Path) -> list[Path]:
    """Temp files the helper stages beside its target, if any survived."""
    return sorted(p for p in directory.iterdir() if p.name.endswith(".tmp"))


# ── 1. Logic — it writes, and it replaces ────────────────────────────


class TestItWrites:
    def test_it_creates_a_file_that_did_not_exist(self, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        write_atomically(target, b"hello", mode=WORLD_READABLE)
        assert target.read_bytes() == b"hello"

    def test_it_replaces_existing_content_whole(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_bytes(b"old content that is longer than the new")
        write_atomically(target, b"new", mode=WORLD_READABLE)
        assert target.read_bytes() == b"new"

    def test_it_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "deep.txt"
        write_atomically(target, b"x", mode=WORLD_READABLE)
        assert target.read_bytes() == b"x"

    def test_it_leaves_no_staged_file_behind_on_success(self, tmp_path: Path) -> None:
        write_atomically(tmp_path / "clean.txt", b"x", mode=WORLD_READABLE)
        assert _staged_siblings(tmp_path) == []


# ── 2. Error — the interruption this exists for ──────────────────────


class TestAnInterruptionLeavesTheOriginalIntact:
    """The load-bearing class. Each test interrupts the write and requires the
    file on disk to be byte-identical to what was there before."""

    def test_the_original_survives_a_keyboard_interrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "config.toml"
        target.write_bytes(b"[mcp_servers.other]\ncommand = 'keep-me'\n")
        before = target.read_bytes()

        monkeypatch.setattr("os.fdopen", _raising_fdopen(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            write_atomically(target, b"clobbered", mode=WORLD_READABLE)

        assert target.read_bytes() == before

    def test_no_staged_file_is_left_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "config.toml"
        target.write_bytes(b"keep\n")

        monkeypatch.setattr("os.fdopen", _raising_fdopen(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            write_atomically(target, b"clobbered", mode=WORLD_READABLE)

        assert _staged_siblings(tmp_path) == []

    def test_a_target_that_did_not_exist_is_not_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "never.txt"
        monkeypatch.setattr("os.fdopen", _raising_fdopen(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            write_atomically(target, b"x", mode=WORLD_READABLE)

        assert not target.exists()

    def test_an_ordinary_exception_is_cleaned_up_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`BaseException` is a superset, not a substitute — an `OSError`
        (a full disk) must still leave the original and no temp file."""
        target = tmp_path / "config.toml"
        target.write_bytes(b"keep\n")

        monkeypatch.setattr("os.fdopen", _raising_fdopen(OSError))
        with pytest.raises(OSError):
            write_atomically(target, b"clobbered", mode=WORLD_READABLE)

        assert target.read_bytes() == b"keep\n"
        assert _staged_siblings(tmp_path) == []


def _raising_fdopen(exc: type[BaseException]):
    """Close the real fd, then raise — so the interruption is simulated without
    leaking a descriptor into the rest of the session."""

    def fake(fd: int, *_args: object, **_kwargs: object):
        os.close(fd)
        raise exc("interrupted")

    return fake


# ── 3. Boundary — the mode is the call site's decision ───────────────


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
class TestMode:
    """`tempfile.mkstemp` always creates `0o600`, so a helper that did nothing
    would SILENTLY NARROW the installer's files, and one that always chmod'd
    would widen a credential. Both directions are asserted."""

    def test_owner_only_is_not_readable_by_anyone_else(self, tmp_path: Path) -> None:
        target = tmp_path / "credentials.json"
        write_atomically(target, b"{}", mode=OWNER_ONLY)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_world_readable_keeps_the_mode_the_installer_always_had(self, tmp_path: Path) -> None:
        target = tmp_path / "plugin.json"
        write_atomically(target, b"{}", mode=WORLD_READABLE)
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_the_two_modes_differ(self) -> None:
        """A vacuity guard. If these ever collapse to one value the two tests
        above still pass while asserting nothing about the distinction — the
        shape this was caught by, where `undefined == undefined`."""
        assert OWNER_ONLY != WORLD_READABLE

    def test_replacing_a_file_does_not_inherit_its_old_mode(self, tmp_path: Path) -> None:
        """`os.replace` moves the staged file's metadata, not the target's, so
        a pre-existing permissive file must not keep its bits."""
        target = tmp_path / "credentials.json"
        target.write_bytes(b"{}")
        os.chmod(target, 0o666)
        write_atomically(target, b"{}", mode=OWNER_ONLY)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


# ── 4. Object state — the staged file is a sibling ───────────────────


class TestTheStagedFileIsInTheTargetDirectory:
    def test_it_stages_beside_the_target_not_in_the_system_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`os.replace` is only atomic within one filesystem, so staging in
        `$TMPDIR` would make the rename a cross-device copy — and on some
        systems an outright `OSError`. Asserted by recording where mkstemp was
        asked to put it, which is the decision rather than its consequence.
        """
        seen: list[str] = []
        real = __import__("tempfile").mkstemp

        def spy(*args: object, **kwargs: object):
            seen.append(str(kwargs.get("dir")))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("tempfile.mkstemp", spy)
        target = tmp_path / "sub" / "file.txt"
        write_atomically(target, b"x", mode=WORLD_READABLE)

        assert seen == [str(target.parent)]

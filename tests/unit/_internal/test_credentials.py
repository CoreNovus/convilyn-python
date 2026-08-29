"""Local credentials file — logic / boundary / error / object-state."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from convilyn._internal import credentials

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `config_root()` to a throwaway directory for this test only.

    Not autouse: `TestConfigRoot` below tests the real per-platform
    resolution logic and must NOT have it short-circuited.
    """
    root = tmp_path / "convilyn-config"
    monkeypatch.setattr(credentials, "config_root", lambda: root)
    return root


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestCredentialsLogic:
    def test_write_then_read_round_trips(self, isolated_root: Path) -> None:
        credentials.write_credentials("ck_example_key")  # pragma: allowlist secret
        assert credentials.read_credentials() == "ck_example_key"  # pragma: allowlist secret

    def test_write_returns_the_credentials_path(self, isolated_root: Path) -> None:
        path = credentials.write_credentials("ck_example_key")  # pragma: allowlist secret
        assert path == credentials.credentials_path()

    def test_write_records_source_and_timestamp(self, isolated_root: Path) -> None:
        credentials.write_credentials("ck_example_key", source="setup")  # pragma: allowlist secret
        data = json.loads(credentials.credentials_path().read_text(encoding="utf-8"))
        assert data["source"] == "setup"
        assert "created_at" in data

    def test_second_write_overwrites_the_first(self, isolated_root: Path) -> None:
        credentials.write_credentials("ck_old")  # pragma: allowlist secret
        credentials.write_credentials("ck_new")  # pragma: allowlist secret
        assert credentials.read_credentials() == "ck_new"  # pragma: allowlist secret


# ── 2. Boundary — POSIX permission narrowing ─────────────────────────


class TestCredentialsPermissions:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission model")
    def test_file_is_created_with_0600(self, isolated_root: Path) -> None:
        credentials.write_credentials("ck_example_key")  # pragma: allowlist secret
        assert credentials.credentials_file_mode() == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission model")
    def test_config_directory_is_created_with_0700(self, isolated_root: Path) -> None:
        credentials.write_credentials("ck_example_key")  # pragma: allowlist secret
        dir_mode = stat.S_IMODE(isolated_root.stat().st_mode)
        assert dir_mode == 0o700

    def test_file_mode_is_none_on_windows(
        self, isolated_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials.write_credentials("ck_example_key")  # pragma: allowlist secret
        monkeypatch.setattr(os, "name", "nt")
        assert credentials.credentials_file_mode() is None


# ── 3. Error — missing / corrupt file never raises ───────────────────


class TestCredentialsErrors:
    def test_missing_file_returns_none(self, isolated_root: Path) -> None:
        assert credentials.read_credentials() is None

    def test_missing_file_mode_returns_none(self, isolated_root: Path) -> None:
        assert credentials.credentials_file_mode() is None

    def test_malformed_json_returns_none(self, isolated_root: Path) -> None:
        isolated_root.mkdir(parents=True, exist_ok=True)
        (isolated_root / "credentials.json").write_text("not json", encoding="utf-8")
        assert credentials.read_credentials() is None

    def test_missing_api_key_field_returns_none(self, isolated_root: Path) -> None:
        isolated_root.mkdir(parents=True, exist_ok=True)
        (isolated_root / "credentials.json").write_text(
            json.dumps({"source": "setup"}), encoding="utf-8"
        )
        assert credentials.read_credentials() is None

    def test_empty_api_key_field_returns_none(self, isolated_root: Path) -> None:
        isolated_root.mkdir(parents=True, exist_ok=True)
        (isolated_root / "credentials.json").write_text(
            json.dumps({"api_key": ""}), encoding="utf-8"
        )
        assert credentials.read_credentials() is None


# ── 4. Object-state — per-platform path resolution ───────────────────


@pytest.mark.uses_real_config_root
class TestConfigRoot:
    """The REAL `config_root()` — deliberately not using `isolated_root`.

    Each case is guarded to the platform it describes, and the guard is the
    same one in both directions. ``monkeypatch.setattr(os, "name", ...)`` steers
    the branch under test, but ``pathlib`` reads ``os.name`` too and picks its
    flavour from it — so on Windows the POSIX cases construct a ``PosixPath``,
    which Python 3.13 refuses outright (``UnsupportedOperation: cannot
    instantiate 'PosixPath' on your system``). The Windows case already carried
    this guard; the POSIX ones did not, so the full ``sdk_local_ci.py`` run was
    red on every Windows machine from the commit that added this file.
    """

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Python 3.13's pathlib refuses to instantiate PosixPath on Windows",
    )
    def test_posix_uses_xdg_config_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-test-home")
        assert credentials.config_root() == Path("/tmp/xdg-test-home") / "convilyn"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Python 3.13's pathlib refuses to instantiate PosixPath on Windows",
    )
    def test_posix_falls_back_to_dot_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert credentials.config_root() == tmp_path / ".config" / "convilyn"

    @pytest.mark.skipif(
        os.name != "nt",
        reason="Python 3.13's pathlib refuses to instantiate WindowsPath on POSIX",
    )
    def test_windows_uses_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        assert credentials.config_root() == Path("C:\\Users\\test\\AppData\\Roaming") / "convilyn"

    def test_credentials_path_is_config_root_slash_credentials_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(credentials, "config_root", lambda: tmp_path)
        assert credentials.credentials_path() == tmp_path / "credentials.json"

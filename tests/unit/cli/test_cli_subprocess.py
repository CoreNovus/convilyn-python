"""Entrypoint subprocess smoke — #2112 teardown guard.

Runs ``python -m convilyn.cli.main`` in a real child interpreter so the
full startup → command → interpreter-teardown cycle is exercised the
way end users invoke it. The real-network Windows repro rides the turbo
E2E ``scenario_cli_convert``; the same-loop invariant that fixes #2112
is asserted in ``tests/unit/_internal/test_loop_runner.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    target = tmp_path / "note.txt"
    target.write_text("hello")
    return target


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "convilyn.cli.main", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCliSubprocessSmoke:
    def test_convert_dry_run_exits_zero(self, sample_file: Path) -> None:
        result = _run_cli("convert", str(sample_file), "--to", "pdf", "--json", "--dry-run")

        assert result.returncode == 0

    def test_convert_dry_run_teardown_is_clean(self, sample_file: Path) -> None:
        result = _run_cli("convert", str(sample_file), "--to", "pdf", "--json", "--dry-run")

        assert "Event loop is closed" not in result.stderr

    def test_convert_dry_run_emits_single_json_object(self, sample_file: Path) -> None:
        result = _run_cli("convert", str(sample_file), "--to", "pdf", "--json", "--dry-run")

        assert json.loads(result.stdout)["dry_run"] is True

    def test_version_exits_zero(self) -> None:
        result = _run_cli("--version")

        assert result.returncode == 0

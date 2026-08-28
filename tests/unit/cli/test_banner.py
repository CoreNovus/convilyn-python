"""``convilyn.cli._banner`` — logic / boundary / object-state."""

from __future__ import annotations

import re

import pytest

from convilyn.cli import _banner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _FakeStream:
    def __init__(self, isatty: bool) -> None:
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty

    def write(self, _text: str) -> int:
        return 0


# ── 1. Logic — the three gates ────────────────────────────────────────


class TestShouldShowBannerLogic:
    def test_true_when_interactive_and_human_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_banner.sys, "stdout", _FakeStream(True))
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _banner.should_show_banner(json_output=False) is True

    def test_false_in_json_mode_even_if_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_banner.sys, "stdout", _FakeStream(True))
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _banner.should_show_banner(json_output=True) is False


# ── 2. Boundary — non-TTY and NO_COLOR ────────────────────────────────


class TestShouldShowBannerBoundary:
    def test_false_when_stdout_is_not_a_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_banner.sys, "stdout", _FakeStream(False))
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _banner.should_show_banner(json_output=False) is False

    def test_false_when_no_color_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_banner.sys, "stdout", _FakeStream(True))
        monkeypatch.setenv("NO_COLOR", "1")
        assert _banner.should_show_banner(json_output=False) is False


# ── 3. Object-state — the rendered art ────────────────────────────────


class TestPrintBanner:
    def test_prints_multiple_lines_without_raising(self, capsys: pytest.CaptureFixture) -> None:
        _banner.print_banner()
        out = capsys.readouterr().out
        assert len(out.splitlines()) > 1

    def test_wordmark_survives_ansi_stripping(self, capsys: pytest.CaptureFixture) -> None:
        # The wordmark is block-letter art (`pyfiglet -f ansi_shadow`), not
        # the literal string "CONVILYN" — check its first row instead.
        _banner.print_banner()
        out = _ANSI_RE.sub("", capsys.readouterr().out)
        assert _banner._WORDMARK_LINES[0] in out

    def test_all_wordmark_rows_share_one_width(self) -> None:
        widths = {len(row) for row in _banner._WORDMARK_LINES}
        assert widths == {_banner._WORDMARK_WIDTH}

    def test_gradient_sweeps_from_violet_to_amber(self, capsys: pytest.CaptureFixture) -> None:
        _banner.print_banner()
        out = capsys.readouterr().out
        # First non-space glyph on the top row should be near-violet; the
        # last non-space glyph should be near-amber — the sweep runs the
        # right direction across the wordmark.
        assert "38;2;124;58;237" in out or "38;2;127;60;238" in out
        assert "38;2;245;158;11" in out

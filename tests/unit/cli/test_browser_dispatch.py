"""``open_url_with_fallback`` may only hand the OS a real web URL.

Two of its three call sites pass ``exc.upgrade_url`` — a string taken from the
SERVER's 402 response body. It went straight to ``webbrowser.open``, and that is
not a "render a page" function: on Windows it falls through to ``os.startfile``,
which opens a local path, a UNC share, or an executable as readily as a URL;
``xdg-open`` is similarly obliging.

The refusal path deliberately still PRINTS the URL. Losing a legitimate link
because it had an unexpected shape would be a worse failure than the one being
fixed, and the printed URL is what a headless session relies on anyway.
"""

from __future__ import annotations

import pytest

from convilyn.cli import _browser as browser_cli


@pytest.fixture
def launched(monkeypatch) -> list[str]:
    """Records what actually reached the OS launcher."""
    calls: list[str] = []
    monkeypatch.setattr(browser_cli.webbrowser, "open", lambda url: calls.append(url) or True)
    return calls


class TestItStillOpensWhatItShould:
    """Vacuity guard: a function that opens nothing passes every refusal test."""

    def test_an_https_url_is_dispatched(self, launched: list[str]) -> None:
        browser_cli.open_url_with_fallback("https://convilyn.com/billing", intro="x")
        assert launched == ["https://convilyn.com/billing"]

    def test_a_loopback_http_url_is_dispatched(self, launched: list[str]) -> None:
        """The local dev API is `http://localhost:*`; refusing it would break
        `convilyn setup` against a dev backend, which is not the hazard here."""
        browser_cli.open_url_with_fallback("http://localhost:8000/callback", intro="x")
        assert launched == ["http://localhost:8000/callback"]


class TestItRefusesWhatTheOsWouldExecute:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///C:/Windows/System32/calc.exe",
            r"C:\Windows\System32\calc.exe",
            "//attacker/share/payload.exe",
            "javascript:alert(1)",
            "vbscript:msgbox(1)",
            "data:text/html,<script>alert(1)</script>",
            "ms-msdt:/id",
            "http://evil.example.com/",  # http is loopback-only
            "https://user:pw@evil.example.com/",  # userinfo phishing shape
            "https:///no-host",
            "",
        ],
    )
    def test_it_is_not_dispatched(self, launched: list[str], url: str) -> None:
        browser_cli.open_url_with_fallback(url, intro="x")
        assert launched == []

    def test_the_url_is_still_printed_when_refused(self, launched, capsys) -> None:
        """A refused link is withheld from the launcher, never from the user."""
        browser_cli.open_url_with_fallback("file:///etc/passwd", intro="x")
        assert "file:///etc/passwd" in capsys.readouterr().err
        assert launched == []


class TestTheExistingContractIsUnchanged:
    def test_no_browser_still_skips_the_launch(self, launched: list[str]) -> None:
        browser_cli.open_url_with_fallback("https://convilyn.com/x", intro="x", attempt_open=False)
        assert launched == []

    def test_a_launcher_failure_still_does_not_raise(self, monkeypatch) -> None:
        """`webbrowser.open` failures were already swallowed — a billing refusal
        must not become a crash because no display was available."""

        def _boom(_url: str) -> bool:
            raise RuntimeError("no display")

        monkeypatch.setattr(browser_cli.webbrowser, "open", _boom)
        browser_cli.open_url_with_fallback("https://convilyn.com/x", intro="x")

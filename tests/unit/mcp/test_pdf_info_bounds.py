"""`pdf info` returns a bounded sample, not the whole text layer.

It used to return everything: a 19-page spec measured 36,660 characters —
roughly 9,200 tokens from one call, doubled on the wire because the payload
rides in both the text block and ``structuredContent``. The description called
it "the cheapest way to learn whether a PDF has a text layer", which it was not.

The host's own overflow message tells a model to "use pagination or filtering
tools" when a result is too big. This server ships one — ``pages`` — and never
mentioned it for ``info``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.mcp import tools
from convilyn.mcp.tools import INFO_MAX_CHARS, INFO_MAX_PAGES

reportlab = pytest.importorskip("reportlab", reason="fixture builder")


def _pdf(path: Path, pages: int) -> Path:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    style = getSampleStyleSheet()["BodyText"]
    flow: list = []
    for index in range(1, pages + 1):
        if index > 1:
            flow.append(PageBreak())
        flow.append(Paragraph(f"Page {index}. " + ("lorem ipsum dolor sit amet " * 40), style))
    SimpleDocTemplate(str(path), pagesize=LETTER).build(flow)
    return path


@pytest.fixture(scope="module")
def long_pdf(tmp_path_factory) -> Path:
    """More pages than `INFO_MAX_PAGES`, so both bounds are exercised."""
    return _pdf(tmp_path_factory.mktemp("pdf") / "long.pdf", INFO_MAX_PAGES + 20)


@pytest.fixture(scope="module")
def short_pdf(tmp_path_factory) -> Path:
    return _pdf(tmp_path_factory.mktemp("pdf") / "short.pdf", 2)


class TestInfoIsBounded:
    def test_it_clips_the_text(self, long_pdf) -> None:
        assert len(tools.pdf("info", source=str(long_pdf))["text"]) == INFO_MAX_CHARS

    def test_it_says_it_clipped(self, long_pdf) -> None:
        """Silent truncation is the failure mode: a model cannot tell a short
        document from a clipped one, and would answer about the part it got."""
        assert tools.pdf("info", source=str(long_pdf))["text_truncated"] is True

    def test_it_reports_which_pages_it_read(self, long_pdf) -> None:
        result = tools.pdf("info", source=str(long_pdf))
        assert result["pages_read"] == f"1-{INFO_MAX_PAGES}"
        assert result["pages"] == INFO_MAX_PAGES + 20

    def test_the_hint_names_both_narrower_calls(self, long_pdf) -> None:
        """Two right answers, not one: a page range for part of it, and
        `convert` for all of it at zero tokens."""
        hint = tools.pdf("info", source=str(long_pdf))["hint"]
        assert "pages" in hint
        assert "convert(" in hint

    def test_a_page_bound_applies_before_extraction(self, long_pdf) -> None:
        """The page cap is not redundant with the character cap. pypdf extracts
        per page, so clipping the string afterwards would still have paid to
        read every page of a long document."""
        bounded = tools.pdf("info", source=str(long_pdf))
        unbounded = tools.pdf("info", source=str(long_pdf), max_chars=10**9, max_pages=10**6)
        assert unbounded["pages_read"] == f"1-{unbounded['pages']}"
        assert len(unbounded["text"]) > len(bounded["text"]) * 4


class TestInfoStaysUsableWhenItFits:
    def test_a_short_document_is_not_marked_truncated(self, short_pdf) -> None:
        """Vacuity guard: every assertion above would also pass if the flag were
        hardcoded true and the text always clipped."""
        assert tools.pdf("info", source=str(short_pdf))["text_truncated"] is False

    def test_a_short_document_carries_no_hint(self, short_pdf) -> None:
        assert "hint" not in tools.pdf("info", source=str(short_pdf))

    def test_an_explicit_range_is_honoured_over_the_page_default(self, long_pdf) -> None:
        assert tools.pdf("info", source=str(long_pdf), pages="2")["pages_read"] == "2"


class TestAPdfFailureDoesNotBlameTheEnvironment:
    def test_a_corrupt_file_is_not_told_to_install_pypdf(self, tmp_path) -> None:
        """`PdfOperationError` also subclasses `LocalError`, so one
        `except LocalError` attached the install hint to every PDF failure —
        telling the reader to fix an environment that was never the problem."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4\ngarbage")

        result = tools.pdf("info", source=str(broken))

        assert result["ok"] is False
        assert "convilyn[pdf]" not in (result.get("hint") or "")

"""PDF extraction, against real PDFs generated with reportlab.

Fixtures are built rather than committed: a binary fixture is opaque in review,
and the thing under test is "does a bigger font become a heading", which is
only meaningful if the test controls the font size.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.local._engine.markdown.pdf import extract


def _pdf(path: Path, lines: list[tuple[str, str, int]]) -> Path:
    """Write a PDF. Each line is ``(text, font, size)``."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=LETTER)
    y = 720
    for text, font, size in lines:
        c.setFont(font, size)
        c.drawString(72, y, text)
        y -= size + 12
    c.save()
    return path


@pytest.fixture
def simple_pdf(tmp_path: Path) -> Path:
    """One large title over many body lines, so the mode is unambiguous."""
    body = [(f"Body sentence number {i} with ordinary prose.", "Helvetica", 10) for i in range(12)]
    return _pdf(tmp_path / "s.pdf", [("QUARTERLY REPORT", "Helvetica-Bold", 24), *body])


class TestHeadingDetection:
    def test_large_font_line_becomes_a_heading(self, simple_pdf):
        kinds = {b.kind for b in extract(simple_pdf).blocks}

        assert "heading" in kinds

    def test_body_text_stays_a_paragraph(self, simple_pdf):
        paragraphs = [b for b in extract(simple_pdf).blocks if b.kind == "paragraph"]

        assert len(paragraphs) >= 10

    def test_the_large_line_is_the_one_promoted(self, simple_pdf):
        headings = [b.text for b in extract(simple_pdf).blocks if b.kind == "heading"]

        assert "QUARTERLY REPORT" in headings

    def test_uniform_font_produces_no_font_based_heading(self, tmp_path):
        """With one font size there is no ratio, so nothing may be promoted by size."""
        lines = [(f"Uniform line {i} of plain running text.", "Helvetica", 11) for i in range(8)]
        doc = extract(_pdf(tmp_path / "u.pdf", lines))

        assert all(b.kind != "heading" for b in doc.blocks)


class TestPageHandling:
    def test_multi_page_pdf_emits_a_page_break(self, tmp_path):
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas

        path = tmp_path / "m.pdf"
        c = canvas.Canvas(str(path), pagesize=LETTER)
        c.setFont("Helvetica", 11)
        c.drawString(72, 720, "First page content.")
        c.showPage()
        c.setFont("Helvetica", 11)
        c.drawString(72, 720, "Second page content.")
        c.save()

        assert any(b.kind == "page_break" for b in extract(path).blocks)

    def test_empty_pdf_reports_a_probable_scan(self, tmp_path):
        """No text layer is the scanned-document signal the OCR phase keys on."""
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas

        path = tmp_path / "e.pdf"
        c = canvas.Canvas(str(path), pagesize=LETTER)
        c.showPage()
        c.save()

        assert any("scan" in w for w in extract(path).warnings)


class TestRobustness:
    def test_source_format_is_reported(self, simple_pdf):
        assert extract(simple_pdf).source_format == "pdf"

    def test_best_effort_warning_is_always_present(self, simple_pdf):
        """PDF has no structure; the output must never claim to be faithful."""
        assert any("best_effort" in w for w in extract(simple_pdf).warnings)

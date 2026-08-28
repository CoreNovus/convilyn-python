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


class TestRunningFurnitureIsNotBody:
    """A page number or a `CONFIDENTIAL` banner is printed on the page without
    being part of what the page says.

    The exhaustive cases — each condition of the rule removed on its own, and
    the reference textbook it was measured against — live with the editable
    source in `backend-api`. What is pinned here is that the SHIPPED package
    behaves the way the offline engine is documented to, since this is the code
    a `convilyn.local.convert()` caller actually runs.
    """

    HEADER = "ACME TRADING - CONFIDENTIAL"
    FOOTER = "Page 1 of 1 - invoice INV-2041"
    TITLE = "Invoice INV-2041"
    BODY = "Issued to Northwind Retail and payable within thirty days."

    def _letterhead(self, path: Path, margin: float, header: bool = True) -> Path:
        from reportlab.pdfgen import canvas

        width, height = 595.27, 841.89
        c = canvas.Canvas(str(path), pagesize=(width, height))
        c.setFont("Helvetica-Oblique", 8)
        if header:
            c.drawString(margin, height - margin + 10, self.HEADER)
        c.drawString(margin, margin - 18, self.FOOTER)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(margin, height - margin - 24.0, self.TITLE)
        c.setFont("Helvetica", 10)
        y = height - margin - 60.0
        for _ in range(6):
            c.drawString(margin, y, self.BODY)
            y -= 14.0
        c.save()
        return path

    def _text(self, path: Path) -> str:
        return "\n".join(b.text for b in extract(path).blocks if b.text)

    def test_the_source_really_carries_the_furniture(self, tmp_path):
        """Guard: every assertion below is an absence, and an empty document
        would satisfy all of them."""
        import pdfplumber

        with pdfplumber.open(self._letterhead(tmp_path / "a.pdf", 56.0)) as pdf:
            raw = pdf.pages[0].extract_text() or ""

        assert self.HEADER in raw
        assert self.FOOTER in raw

    def test_the_header_and_footer_are_dropped(self, tmp_path):
        body = self._text(self._letterhead(tmp_path / "b.pdf", 56.0))

        assert self.HEADER not in body
        assert self.FOOTER not in body

    def test_the_title_and_prose_survive(self, tmp_path):
        body = self._text(self._letterhead(tmp_path / "c.pdf", 56.0))

        assert self.TITLE in body
        assert self.BODY in body

    def test_a_title_inside_the_band_is_not_taken_for_a_header(self, tmp_path):
        """A tight top margin puts the H1 where a running header would be. It is
        set larger than everything around it, which is what tells them apart."""
        body = self._text(self._letterhead(tmp_path / "d.pdf", 18.0, header=False))

        assert self.TITLE in body
        assert self.FOOTER not in body

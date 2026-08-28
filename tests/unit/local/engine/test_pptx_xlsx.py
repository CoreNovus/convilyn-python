"""PPTX and XLSX extraction — the two biggest holes in the old format matrix.

Neither format could reach Markdown at all before this: ``SUPPORTED_CONVERSIONS``
never listed ``md`` as a target for either.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest

from convilyn.local._engine.markdown.pptx import extract as extract_pptx
from convilyn.local._engine.markdown.xlsx import extract as extract_xlsx


def _png(size: tuple[int, int] = (256, 256)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size)
    image.putdata(
        [
            ((x * 7) % 256, (y * 11) % 256, (x * y) % 256)
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ── PPTX ─────────────────────────────────────────────────────────────


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Results"
    slide.placeholders[1].text_frame.text = "First point"
    slide.notes_slide.notes_text_frame.text = "Say the number out loud."

    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "Appendix"
    image_path = tmp_path / "f.png"
    image_path.write_bytes(_png())
    second.shapes.add_picture(str(image_path), Inches(1), Inches(1))

    path = tmp_path / "d.pptx"
    presentation.save(str(path))
    return path


class TestPptx:
    def test_slide_title_becomes_a_heading(self, deck):
        headings = [b.text for b in extract_pptx(deck).blocks if b.kind == "heading"]

        assert "Results" in headings

    def test_body_placeholder_becomes_a_list_item(self, deck):
        items = [b.text for b in extract_pptx(deck).blocks if b.kind == "list_item"]

        assert "First point" in items

    def test_speaker_notes_are_kept_as_a_quote(self, deck):
        """Notes are usually the densest text in a deck; dropping them loses it."""
        quotes = [b.text for b in extract_pptx(deck).blocks if b.kind == "quote"]

        assert "Say the number out loud." in quotes

    def test_slides_are_separated_by_a_page_break(self, deck):
        assert any(b.kind == "page_break" for b in extract_pptx(deck).blocks)

    def test_picture_is_extracted_with_bytes(self, deck):
        image = next(b.image for b in extract_pptx(deck).blocks if b.kind == "image")

        assert image is not None and image.data.startswith(b"\x89PNG")

    def test_untitled_slide_still_gets_a_heading(self, tmp_path):
        """A blank layout has no title; the section must still be navigable."""
        from pptx import Presentation

        presentation = Presentation()
        presentation.slides.add_slide(presentation.slide_layouts[6])
        path = tmp_path / "b.pptx"
        presentation.save(str(path))

        assert extract_pptx(path).blocks[0].kind == "heading"

    def test_source_format_is_reported(self, deck):
        assert extract_pptx(deck).source_format == "pptx"


# ── XLSX ─────────────────────────────────────────────────────────────


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Orders"
    sheet.append(["item", "qty", "when"])
    sheet.append(["bolt", 4, datetime(2026, 8, 9)])
    sheet.append(["nut", 12.0, datetime(2026, 8, 10, 14, 30)])

    second = book.create_sheet("Notes")
    second.append(["free text"])

    path = tmp_path / "w.xlsx"
    book.save(str(path))
    return path


class TestXlsx:
    def test_each_sheet_becomes_a_heading(self, workbook):
        headings = [b.text for b in extract_xlsx(workbook).blocks if b.kind == "heading"]

        assert headings == ["Orders", "Notes"]

    def test_rows_become_a_table(self, workbook):
        table = next(b for b in extract_xlsx(workbook).blocks if b.kind == "table")

        assert table.rows[0] == ("item", "qty", "when")

    def test_whole_number_float_loses_its_decimal(self, workbook):
        """openpyxl returns 12.0 for an integer cell; "12.0" reads as wrong."""
        table = next(b for b in extract_xlsx(workbook).blocks if b.kind == "table")

        assert table.rows[2][1] == "12"

    def test_midnight_datetime_renders_as_a_plain_date(self, workbook):
        table = next(b for b in extract_xlsx(workbook).blocks if b.kind == "table")

        assert table.rows[1][2] == "2026-08-09"

    def test_datetime_with_a_time_keeps_it(self, workbook):
        table = next(b for b in extract_xlsx(workbook).blocks if b.kind == "table")

        assert table.rows[2][2] == "2026-08-10 14:30:00"

    def test_empty_sheet_is_reported_not_dropped(self, tmp_path):
        import openpyxl

        book = openpyxl.Workbook()
        book.active.title = "Blank"
        path = tmp_path / "e.xlsx"
        book.save(str(path))

        texts = [b.text for b in extract_xlsx(path).blocks if b.kind == "paragraph"]

        assert "(empty sheet)" in texts

    def test_source_format_is_reported(self, workbook):
        assert extract_xlsx(workbook).source_format == "xlsx"


# ── number formats, in the tree that ships ───────────────────────────
#
# The rule: a number format is applied when doing so is LOSSLESS and ADDS
# meaning, and refused when it only reshapes appearance. Pinned here as well as
# upstream because this module's docstring — hand-written, installed by the
# projection — is what a user reads, and it now states this rule. Nothing else
# checks that a projected doc tells the truth.


def _one_cell(tmp_path, value, number_format: str) -> str:
    """The rendered text of a single formatted cell."""
    import openpyxl

    book = openpyxl.Workbook()
    cell = book.active.cell(row=1, column=1, value=value)
    cell.number_format = number_format
    path = tmp_path / "one.xlsx"
    book.save(str(path))

    table = next(b for b in extract_xlsx(path).blocks if b.kind == "table")
    return table.rows[0][0]


class TestNumberFormats:
    def test_a_percentage_is_shifted_and_marked(self, tmp_path):
        assert _one_cell(tmp_path, 0.279613264457963, "0.00%") == "27.9613264457963%"

    def test_the_percentage_format_does_not_round(self, tmp_path):
        """A spreadsheet shows ``-72.0%``; that drops eleven digits the file has."""
        assert _one_cell(tmp_path, -0.720386735542037, "0.0%") == "-72.0386735542037%"

    def test_the_shift_is_exact_not_arithmetic(self, tmp_path):
        """``0.07 * 100`` is ``7.000000000000001``. Binary float multiplication
        is not a decimal shift, and inventing digits is the tampering this rule
        forbids — while passing any test that only looked for a ``%``."""
        assert _one_cell(tmp_path, 0.07, "0%") == "7%"

    def test_a_currency_symbol_survives(self, tmp_path):
        assert _one_cell(tmp_path, -141270, '"NT$"#,##0') == "NT$-141270"

    def test_thousands_separators_are_not_applied(self, tmp_path):
        """Lossless, but zero semantic, and they break downstream parsing."""
        assert _one_cell(tmp_path, 1234567, "#,##0") == "1234567"

    def test_decimal_rounding_is_never_applied(self, tmp_path):
        assert _one_cell(tmp_path, 3.14159265, "0.00") == "3.14159265"

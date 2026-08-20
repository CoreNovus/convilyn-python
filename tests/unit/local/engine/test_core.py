"""The pure core of the document->Markdown engine: render, triage, text, csv.

Offline by construction — no network, no AWS, no Bedrock. That is the property
the whole extract -> enrich -> render split was chosen for, so it is asserted
here rather than assumed.
"""

from __future__ import annotations

import io
import re

import pytest

from convilyn.local._engine.markdown import images as img
from convilyn.local._engine.markdown.csv_ import MAX_ROWS
from convilyn.local._engine.markdown.csv_ import extract as extract_csv
from convilyn.local._engine.markdown.model import (
    Block,
    ExtractedImage,
    MarkdownDoc,
)
from convilyn.local._engine.markdown.render import render
from convilyn.local._engine.markdown.text import extract as extract_text


def _png(colour: tuple[int, int, int], size: tuple[int, int] = (128, 128)) -> bytes:
    """A real PNG — triage decodes its input, so a fake byte string proves nothing."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _noisy_image(size: tuple[int, int] = (128, 128)):
    """A Pillow image with many distinct colours — i.e. one that looks like content."""
    from PIL import Image

    image = Image.new("RGB", size)
    image.putdata(
        [
            ((x * 7) % 256, (y * 11) % 256, (x * y) % 256)
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    return image


def _encode(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _noisy_png(size: tuple[int, int] = (128, 128)) -> bytes:
    return _encode(_noisy_image(size))


# ── render: structure ────────────────────────────────────────────────


class TestRenderStructure:
    def test_heading_level_is_clamped_to_six(self):
        doc = MarkdownDoc(blocks=(Block(kind="heading", text="Deep", level=99),))

        assert render(doc).startswith("###### Deep")

    def test_heading_level_below_one_is_raised_to_one(self):
        doc = MarkdownDoc(blocks=(Block(kind="heading", text="Top", level=0),))

        assert render(doc).startswith("# Top")

    def test_table_gets_a_gfm_separator_row(self):
        doc = MarkdownDoc(blocks=(Block(kind="table", rows=(("a", "b"), ("1", "2"))),))

        assert "| --- | --- |" in render(doc)

    def test_short_table_rows_are_padded_not_dropped(self):
        """A merged cell yields a short row; dropping it would lose data."""
        doc = MarkdownDoc(blocks=(Block(kind="table", rows=(("a", "b"), ("only",))),))

        assert "| only |  |" in render(doc)

    def test_consecutive_list_items_are_tight(self):
        doc = MarkdownDoc(
            blocks=(
                Block(kind="list_item", text="one"),
                Block(kind="list_item", text="two"),
            )
        )

        assert "- one\n- two" in render(doc)

    def test_output_ends_with_exactly_one_newline(self):
        doc = MarkdownDoc(blocks=(Block(kind="paragraph", text="body"),))

        assert render(doc).endswith("body\n")


# ── render: escaping ─────────────────────────────────────────────────


class TestRenderEscaping:
    def test_underscores_are_not_escaped(self):
        """CommonMark ignores intra-word underscores; escaping mangles code."""
        doc = MarkdownDoc(blocks=(Block(kind="paragraph", text="call __init__ now"),))

        assert "__init__" in render(doc)

    def test_angle_bracket_is_escaped(self):
        """Text out of an uploaded file must not emit live markup.

        Asserted as "the escaped form is present" rather than "the raw form is
        absent": ``\\<script>`` CONTAINS ``<script>`` as a substring, so the
        absence form passes only by accident and fails when the escaping works.
        """
        doc = MarkdownDoc(blocks=(Block(kind="paragraph", text="<script>x</script>"),))

        assert r"\<script>" in render(doc)

    def test_pipe_inside_a_cell_does_not_break_the_row(self):
        doc = MarkdownDoc(blocks=(Block(kind="table", rows=(("a|b", "c"),)),))

        assert r"a\|b" in render(doc)

    def test_newline_inside_a_cell_becomes_a_break(self):
        doc = MarkdownDoc(blocks=(Block(kind="table", rows=(("a\nb", "c"),)),))

        assert "a<br>b" in render(doc)

    def test_code_block_content_is_not_escaped(self):
        doc = MarkdownDoc(blocks=(Block(kind="code", text="x = a[0] * 2", language="python"),))

        assert "x = a[0] * 2" in render(doc)

    def test_line_start_hash_is_escaped_in_a_paragraph(self):
        doc = MarkdownDoc(blocks=(Block(kind="paragraph", text="# not a heading"),))

        assert render(doc).startswith("\\#")


# ── render: images ───────────────────────────────────────────────────


def _image(**overrides) -> ExtractedImage:
    base = {
        "data": b"x",
        "media_type": "image/png",
        "asset_name": "img-0001.png",
        "dedup_key": "k",
    }
    return ExtractedImage(**{**base, **overrides})


class TestRenderImages:
    def test_image_links_into_the_asset_dir(self):
        doc = MarkdownDoc(blocks=(Block(kind="image", image=_image()),))

        assert "(assets/img-0001.png)" in render(doc)

    def test_description_is_rendered_as_a_quote_not_as_alt_text(self):
        doc = MarkdownDoc(blocks=(Block(kind="image", image=_image(description="A bar chart.")),))

        assert "> A bar chart." in render(doc)

    def test_undescribed_image_still_renders(self):
        """Budget exhaustion degrades the enrichment, never the document."""
        doc = MarkdownDoc(blocks=(Block(kind="image", image=_image(alt_text="logo")),))

        assert "![logo](assets/img-0001.png)" in render(doc)


# ── model ────────────────────────────────────────────────────────────


class TestUniqueImages:
    def test_repeated_image_is_counted_once(self):
        """The enrichment budget is sized off this, so a repeat must collapse."""
        repeated = _image()
        doc = MarkdownDoc(
            blocks=(
                Block(kind="image", image=repeated),
                Block(kind="image", image=repeated),
            )
        )

        assert len(doc.unique_images) == 1

    def test_distinct_images_are_both_kept(self):
        doc = MarkdownDoc(
            blocks=(
                Block(kind="image", image=_image(dedup_key="a")),
                Block(kind="image", image=_image(dedup_key="b")),
            )
        )

        assert len(doc.unique_images) == 2


# ── image triage ─────────────────────────────────────────────────────


class TestDedupKey:
    def test_reencoded_image_shares_a_key(self):
        """The case a byte hash misses: same picture, different compression."""
        image = _noisy_image((256, 256))
        default = _encode(image)
        recompressed = io.BytesIO()
        image.save(recompressed, format="PNG", compress_level=1)

        assert img.inspect(default).dedup_key == img.inspect(recompressed.getvalue()).dedup_key

    def test_rescaled_image_is_a_known_miss(self):
        """A rescale does NOT converge, and that is pinned on purpose.

        Hashing for equality cannot tolerate scale — any perturbation crossing
        a quantisation boundary changes the digest entirely. Tolerating it
        needs a distance comparison over a perceptual hash, which would also
        merge crops and recolours (describing figure 3 with figure 2's caption).

        This test exists so the gap is visible in the suite instead of being
        assumed away by the module docstring. If a future change DOES make
        rescales converge, this test fails and should be replaced with the
        positive assertion — a red test here is good news, not a regression.
        """
        from PIL import Image

        original = _noisy_image((256, 256))
        rescaled = original.resize((128, 128), Image.Resampling.LANCZOS)

        assert img.inspect(_encode(original)).dedup_key != img.inspect(_encode(rescaled)).dedup_key

    def test_different_images_do_not_share_a_key(self):
        assert img.inspect(_png((255, 0, 0))).dedup_key != img.inspect(_noisy_png()).dedup_key

    def test_undecodable_bytes_fall_back_to_a_byte_hash(self):
        facts = img.inspect(b"not an image at all")

        assert facts.decoded is False

    def test_undecodable_bytes_still_produce_a_key(self):
        assert img.inspect(b"not an image at all").dedup_key


class TestDecorativeFilter:
    def test_flat_colour_block_is_decorative(self):
        data = _png((10, 20, 30), (256, 256))

        assert img.is_decorative(img.inspect(data), len(data)) is True

    def test_tiny_image_is_decorative(self):
        data = _noisy_png((8, 8))

        assert img.is_decorative(img.inspect(data), len(data)) is True

    def test_wide_thin_rule_is_decorative(self):
        data = _noisy_png((1000, 4))

        assert img.is_decorative(img.inspect(data), len(data)) is True

    def test_content_bearing_image_is_kept(self):
        data = _noisy_png((256, 256))

        assert img.is_decorative(img.inspect(data), len(data)) is False

    def test_undecodable_image_is_never_decorative(self):
        """No evidence either way, so keep it — a dropped figure is worse."""
        assert img.is_decorative(img.inspect(b"junk"), 999_999) is False


class TestAssetNaming:
    def test_name_is_zero_padded_and_sortable(self):
        assert img.asset_name(7, "image/png") == "img-0007.png"

    def test_unknown_media_type_gets_a_bin_extension(self):
        assert img.asset_name(1, "image/x-nonsense").endswith(".bin")


# ── text + csv extractors ────────────────────────────────────────────


class TestTextExtractor:
    def test_all_caps_line_becomes_a_heading(self, tmp_path):
        source = tmp_path / "a.txt"
        source.write_text("INTRODUCTION\n\nSome body text here.", encoding="utf-8")

        assert extract_text(source).blocks[0].kind == "heading"

    def test_prose_becomes_a_paragraph(self, tmp_path):
        source = tmp_path / "a.txt"
        source.write_text("Just an ordinary sentence that runs on.", encoding="utf-8")

        assert extract_text(source).blocks[0].kind == "paragraph"

    def test_non_utf8_bytes_do_not_raise(self, tmp_path):
        """A legacy codepage must degrade, not fail the job."""
        source = tmp_path / "a.txt"
        source.write_bytes(b"caf\xe9 society")

        assert extract_text(source).blocks


class TestCsvExtractor:
    def test_first_row_becomes_the_header(self, tmp_path):
        source = tmp_path / "a.csv"
        source.write_text("name,qty\nbolt,4\n", encoding="utf-8")

        assert extract_csv(source).blocks[0].rows[0] == ("name", "qty")

    def test_semicolon_delimiter_is_sniffed(self, tmp_path):
        source = tmp_path / "a.csv"
        source.write_text("name;qty\nbolt;4\n", encoding="utf-8")

        assert extract_csv(source).blocks[0].rows[0] == ("name", "qty")

    def test_single_column_file_does_not_raise(self, tmp_path):
        """Sniffer raises on input with no delimiter; that is not an error."""
        source = tmp_path / "a.csv"
        source.write_text("solo\nvalue\n", encoding="utf-8")

        assert extract_csv(source).blocks[0].rows[0] == ("solo",)

    def test_empty_file_reports_rather_than_raises(self, tmp_path):
        source = tmp_path / "a.csv"
        source.write_text("", encoding="utf-8")

        assert extract_csv(source).warnings == ("empty CSV",)

    def test_oversized_file_is_truncated_with_a_warning(self, tmp_path):
        source = tmp_path / "a.csv"
        source.write_text("h\n" + "".join(f"{i}\n" for i in range(MAX_ROWS + 50)), encoding="utf-8")

        assert any("truncated" in w for w in extract_csv(source).warnings)

    def test_the_default_cap_converts_that_many_data_rows(self, tmp_path):
        """#3997, as the reporter met it: the DEFAULT cap, with no argument.

        The published engine converted 4,999 rows under a warning naming 5,000,
        because the header was charged to the cap. Pinned here as well as at the
        API level because this is the path a caller who never passes ``max_rows``
        takes — which is nearly all of them.
        """
        source = tmp_path / "a.csv"
        source.write_text("h\n" + "".join(f"{i}\n" for i in range(MAX_ROWS + 50)), encoding="utf-8")

        doc = extract_csv(source)

        assert len(doc.blocks[0].rows) - 1 == MAX_ROWS
        assert any(f"first {MAX_ROWS} data rows" in w for w in doc.warnings)

    @pytest.mark.parametrize("cell", ["a|b", "a\nb"])
    def test_hostile_cells_survive_a_full_round_trip(self, tmp_path, cell):
        """Extract -> render must not emit a table that breaks its own grammar.

        Counts UNESCAPED pipes. A naive ``split("|")`` counts the escaped one
        too — ``a\\|b`` still contains the character — so the naive form reports
        a broken row precisely when the escaping worked.
        """
        source = tmp_path / "a.csv"
        source.write_text(f'h1,h2\n"{cell}",z\n', encoding="utf-8")

        last_row = render(extract_csv(source)).splitlines()[-1]

        assert len(re.findall(r"(?<!\\)\|", last_row)) == 3

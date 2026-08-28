"""Where a batch puts the images it extracts, and why each document gets its own.

The engine names assets per document — ``img-0001.png``, ``img-0002.png`` — because
within one document that is unambiguous. A batch converts many documents into one
directory, so those names collide, and the loser is overwritten **silently**: the
Markdown still links to a file that exists, so nothing errors and nothing warns.
The reader gets another document's picture.

That is the failure these tests exist to prevent, and it is worth being precise
about why it is worse than a crash: a broken link is visible on the first look, and
a link to the wrong image is not visible at all until somebody who knows what the
figure should say reads it. Downstream — an LLM summarising the batch, a static site
build — nothing is available to notice.

The single-file case is deliberately asserted here too. It was already correct, and
the fix must not move its paths: a change to where a shipped artifact lands is a
public behaviour change, and there is no reason to spend one on the case that had no
bug.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from convilyn import local


def _png(seed: int, size: tuple[int, int] = (256, 256)) -> bytes:
    """A content-bearing PNG. ``seed`` makes two of them distinguishable.

    Flat images are filtered out as decorative, so the pixels have to carry
    something — and the two documents' images must differ, or an overwrite would
    be undetectable by comparing bytes.
    """
    from PIL import Image

    image = Image.new("RGB", size)
    image.putdata(
        [
            ((x * 7 + seed) % 256, (y * 11 + seed) % 256, (x * y + seed) % 256)
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _docx_with_image(tmp_path: Path, name: str, seed: int) -> Path:
    docx = pytest.importorskip("docx", reason="the docx extractor needs python-docx")

    document = docx.Document()
    document.add_paragraph(f"Document {name}.")
    picture = tmp_path / f"{name}-source.png"
    picture.write_bytes(_png(seed))
    document.add_picture(str(picture))

    path = tmp_path / f"{name}.docx"
    document.save(str(path))
    return path


@pytest.fixture
def two_documents(tmp_path: Path) -> tuple[Path, Path]:
    """Two documents with one image each, and the images are not the same picture."""
    return (
        _docx_with_image(tmp_path, "alpha", seed=0),
        _docx_with_image(tmp_path, "beta", seed=128),
    )


class TestTheFixtureIsCapableOfShowingTheBug:
    """Guard-the-guard: with identical images, an overwrite is invisible."""

    def test_the_two_source_images_differ(self, two_documents) -> None:
        assert _png(0) != _png(128)


class TestBatchGivesEachDocumentItsOwnAssetDirectory:
    def test_each_document_keeps_its_own_image(self, two_documents, tmp_path: Path) -> None:
        """The regression: both documents' assets were written as ``img-0001.png``.

        Asserted on **bytes**, not on the number of files. A count would pass on a
        run that wrote two files and pointed both Markdown documents at one of them.
        """
        out = tmp_path / "md"

        local.convert_many(two_documents, to="md", out_dir=out)

        alpha = (out / "assets" / "alpha" / "img-0001.png").read_bytes()
        beta = (out / "assets" / "beta" / "img-0001.png").read_bytes()

        assert alpha != beta

    def test_each_markdown_links_into_its_own_namespace(
        self, two_documents, tmp_path: Path
    ) -> None:
        out = tmp_path / "md"

        local.convert_many(two_documents, to="md", out_dir=out)

        assert "](assets/alpha/img-0001.png)" in (out / "alpha.md").read_text(encoding="utf-8")
        assert "](assets/beta/img-0001.png)" in (out / "beta.md").read_text(encoding="utf-8")

    def test_every_link_resolves_to_a_file_that_exists(self, two_documents, tmp_path: Path) -> None:
        """A namespaced link that points nowhere trades one silent defect for a
        louder one. The link and the write have to agree, so both are read back."""
        import re

        out = tmp_path / "md"

        local.convert_many(two_documents, to="md", out_dir=out)

        targets = [
            target
            for name in ("alpha.md", "beta.md")
            for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", (out / name).read_text("utf-8"))
        ]
        assert targets, "no image links rendered — the fixture stopped exercising images"
        assert [t for t in targets if not (out / t).is_file()] == []


class TestSingleFileLayoutIsUnchanged:
    """It had no bug, so it gets no new path."""

    def test_the_asset_sits_directly_under_assets(self, tmp_path: Path) -> None:
        source = _docx_with_image(tmp_path, "solo", seed=7)

        local.convert(source, to="md")

        assert (tmp_path / "assets" / "img-0001.png").is_file()

    def test_no_per_document_directory_is_created(self, tmp_path: Path) -> None:
        source = _docx_with_image(tmp_path, "solo", seed=7)

        local.convert(source, to="md")

        assert not (tmp_path / "assets" / "solo").exists()

    def test_the_link_is_unqualified(self, tmp_path: Path) -> None:
        source = _docx_with_image(tmp_path, "solo", seed=7)

        local.convert(source, to="md")

        assert "](assets/img-0001.png)" in (tmp_path / "solo.md").read_text(encoding="utf-8")

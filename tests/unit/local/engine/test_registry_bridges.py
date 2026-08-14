"""The bridge mapping: which formats are read through a sibling, and with what.

The mapping is pure data and lives beside the extractor dispatch it complements.
Running the hop is somebody else's job — this only asserts that the table says
the right thing, which is what makes the hop possible to write correctly.
"""

from __future__ import annotations

import pytest

from convilyn.local._engine.formats import DocumentFormat
from convilyn.local._engine.markdown.registry import (
    bridge_for,
    can_read,
    extractor_for,
)

#: Every bridged format, and what it is bridged to and with.
BRIDGED = [
    (DocumentFormat.DOC, DocumentFormat.DOCX, "libreoffice"),
    (DocumentFormat.ODT, DocumentFormat.DOCX, "libreoffice"),
    (DocumentFormat.RTF, DocumentFormat.DOCX, "libreoffice"),
    (DocumentFormat.XLS, DocumentFormat.XLSX, "libreoffice"),
    (DocumentFormat.ODS, DocumentFormat.XLSX, "libreoffice"),
    (DocumentFormat.PPT, DocumentFormat.PPTX, "libreoffice"),
    (DocumentFormat.ODP, DocumentFormat.PPTX, "libreoffice"),
    (DocumentFormat.EPUB, DocumentFormat.DOCX, "calibre"),
    (DocumentFormat.MOBI, DocumentFormat.DOCX, "calibre"),
    (DocumentFormat.AZW3, DocumentFormat.DOCX, "calibre"),
]
BRIDGED_SOURCES = [row[0] for row in BRIDGED]


class TestBridgeMapping:
    @pytest.mark.parametrize(("source", "bridge", "_engine"), BRIDGED)
    def test_each_bridged_format_names_its_sibling(self, source, bridge, _engine):
        assert bridge_for(source).bridge is bridge

    @pytest.mark.parametrize(("source", "_bridge", "engine"), BRIDGED)
    def test_each_bridged_format_names_its_engine(self, source, _bridge, engine):
        """An office suite cannot open an EPUB; an ebook tool has no use for ODS."""
        assert bridge_for(source).engine == engine

    @pytest.mark.parametrize(
        "source",
        [DocumentFormat.DOCX, DocumentFormat.XLSX, DocumentFormat.PPTX],
    )
    def test_a_format_that_is_its_own_sibling_has_no_mapping(self, source):
        """OOXML reads natively; a self-referential row would loop the bridge."""
        assert bridge_for(source) is None


class TestTheTwoTablesAgree:
    @pytest.mark.parametrize("source", BRIDGED_SOURCES)
    def test_a_bridged_format_has_no_native_extractor(self, source):
        """Both tables answering would make "native or bridge?" ambiguous."""
        assert extractor_for(source) is None

    @pytest.mark.parametrize("source", BRIDGED_SOURCES)
    def test_every_sibling_can_actually_be_extracted(self, source):
        """A sibling with no extractor would send the bridge round a dead end."""
        assert extractor_for(bridge_for(source).bridge) is not None


class TestCanRead:
    @pytest.mark.parametrize(
        "source",
        [DocumentFormat.PDF, DocumentFormat.DOCX, DocumentFormat.TXT, DocumentFormat.CSV],
    )
    def test_natively_read_formats(self, source):
        assert can_read(source) is True

    @pytest.mark.parametrize("source", BRIDGED_SOURCES)
    def test_bridged_formats(self, source):
        assert can_read(source) is True

    @pytest.mark.parametrize("source", [DocumentFormat.HTML, DocumentFormat.MD])
    def test_formats_the_engine_does_not_own(self, source):
        """Markdown as a source is not a conversion this engine performs."""
        assert can_read(source) is False


class TestEveryBridgedFormatStaysReachable:
    """A bridged row is the only thing keeping these three readable at all.

    They have no native extractor, so if a row were dropped the format would
    silently stop being convertible while still looking like a supported one.
    This asserts the property the rows exist for, rather than re-stating them.
    """

    @pytest.mark.parametrize(
        "source", [DocumentFormat.EPUB, DocumentFormat.MOBI, DocumentFormat.AZW3]
    )
    def test_reachable_through_a_sibling(self, source):
        assert extractor_for(source) is None
        assert can_read(source) is True
        assert bridge_for(source) is not None

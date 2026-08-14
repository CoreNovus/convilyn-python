"""XML extraction, including the entity attacks defusedxml is here to stop."""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.local._engine.markdown.registry import extractor_for
from convilyn.local._engine.markdown.xml_ import extract

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<lolz>&lol4;</lolz>"""

EXTERNAL_ENTITY = """<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<foo>&xxe;</foo>"""


@pytest.fixture
def catalogue(tmp_path: Path) -> Path:
    path = tmp_path / "c.xml"
    path.write_text(
        '<catalogue name="spring"><item sku="A1">Bolt</item><item sku="B2">Nut</item></catalogue>',
        encoding="utf-8",
    )
    return path


class TestStructure:
    def test_root_becomes_the_title(self, catalogue):
        assert extract(catalogue).title == "catalogue"

    def test_child_elements_become_headings(self, catalogue):
        headings = [b.text for b in extract(catalogue).blocks if b.kind == "heading"]

        assert headings.count("item") == 2

    def test_element_text_becomes_a_paragraph(self, catalogue):
        texts = [b.text for b in extract(catalogue).blocks if b.kind == "paragraph"]

        assert "Bolt" in texts

    def test_attributes_become_a_table(self, catalogue):
        tables = [b for b in extract(catalogue).blocks if b.kind == "table"]

        assert ("sku", "A1") in tables[1].rows

    def test_namespaced_tag_loses_its_namespace(self, tmp_path):
        path = tmp_path / "ns.xml"
        path.write_text('<r xmlns="http://example.com/v1"><child/></r>', encoding="utf-8")

        assert extract(path).title == "r"


class TestSafety:
    def test_billion_laughs_is_refused_not_expanded(self, tmp_path):
        """Stdlib ElementTree would expand this into gigabytes of memory."""
        path = tmp_path / "b.xml"
        path.write_text(BILLION_LAUGHS, encoding="utf-8")

        assert extract(path).blocks == ()

    def test_billion_laughs_reports_a_reason(self, tmp_path):
        path = tmp_path / "b.xml"
        path.write_text(BILLION_LAUGHS, encoding="utf-8")

        assert extract(path).warnings

    def test_external_entity_is_refused(self, tmp_path):
        """An upload must not become a file read from inside the VPC."""
        path = tmp_path / "x.xml"
        path.write_text(EXTERNAL_ENTITY, encoding="utf-8")

        assert extract(path).blocks == ()

    def test_malformed_xml_reports_rather_than_raises(self, tmp_path):
        path = tmp_path / "m.xml"
        path.write_text("<open>no close", encoding="utf-8")

        assert any("malformed" in w for w in extract(path).warnings)


class TestRegistry:
    def test_xml_resolves_to_an_extractor(self):
        from convilyn.local._engine.formats import DocumentFormat

        assert extractor_for(DocumentFormat.XML) is not None

    def test_html_is_left_to_the_existing_html2text_route(self):
        """Rebuilding a mature HTML->MD converter would be reimplementation."""
        from convilyn.local._engine.formats import DocumentFormat

        assert extractor_for(DocumentFormat.HTML) is None

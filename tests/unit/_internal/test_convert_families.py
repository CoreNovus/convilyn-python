"""The free lane's family derivation — `convilyn._internal.convert_families`.

`convert` reaches three processors and the caller names none of them, so the
derivation is the feature. These tests are written against the *rule* ("the
family that speaks both formats") rather than against a table of pairs: a table
here would be the fourth hand-typed copy #3923 exists to delete.
"""

from __future__ import annotations

import pytest

from convilyn._internal import convert_families as families

# ── 1. Logic — the three families are reachable, and by the pair ─────


class TestFamilyDerivation:
    @pytest.mark.parametrize(
        ("source", "target", "expected"),
        [
            ("docx", "pdf", "document_conversion"),
            ("png", "webp", "image_conversion"),
            ("mp4", "mp3", "media_processing"),
        ],
    )
    def test_each_family_is_reachable(self, source: str, target: str, expected: str) -> None:
        assert families.resolve_family(source, target).processor_type == expected

    def test_a_format_in_two_families_is_decided_by_the_target(self) -> None:
        # `gif` is an image and a video container. Reading the family off the
        # SOURCE alone cannot answer this; reading it off the pair can.
        assert families.resolve_family("gif", "png").name == "image"
        assert families.resolve_family("gif", "mp4").name == "media"

    def test_pdf_is_decided_by_the_target_too(self) -> None:
        assert families.resolve_family("pdf", "docx").name == "document"
        assert families.resolve_family("png", "pdf").name == "image"

    def test_no_family_is_a_metered_processor(self) -> None:
        # The whole point of splitting the verbs: `convert` cannot route to
        # something that charges credits.
        metered = {"ocr_processing", "video_transcription", "audio_transcription"}
        assert not {family.processor_type for family in families.FAMILIES} & metered


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PDF", "pdf"),
            (".pdf", "pdf"),
            ("  .PDF  ", "pdf"),
            ("jpeg", "jpg"),  # projected from the backend's alias table
            ("htm", "html"),  # the SDK's one hand-written spelling
        ],
    )
    def test_a_spelling_resolves_to_what_the_worker_reads(self, raw: str, expected: str) -> None:
        assert families.normalise_format(raw) == expected

    def test_detect_format_reads_the_extension(self) -> None:
        assert families.detect_format("holiday.JPEG") == "jpg"

    def test_detect_format_returns_none_without_an_extension(self) -> None:
        assert families.detect_format("receipt") is None


# ── 2. Boundary — the precondition the rule rests on ─────────────────


class TestTheRuleStaysUnambiguous:
    def test_no_two_families_share_more_than_one_format(self) -> None:
        """A two-format overlap would make some pair belong to both families.

        The projection refuses to emit such a table; this is the same assertion
        held against the committed artifact, so a hand-edited or half-updated
        file is caught here even if the generator never ran.
        """
        overlaps = [
            left.formats & right.formats
            for index, left in enumerate(families.FAMILIES)
            for right in families.FAMILIES[index + 1 :]
        ]
        assert all(len(shared) <= 1 for shared in overlaps), overlaps

    def test_the_overlaps_are_not_all_empty(self) -> None:
        # Guard-the-guard: the assertion above passes trivially if the families
        # stopped overlapping, and the two known overlaps are exactly what makes
        # "decide by the pair" necessary rather than decorative.
        assert any(
            left.formats & right.formats
            for index, left in enumerate(families.FAMILIES)
            for right in families.FAMILIES[index + 1 :]
        )

    def test_every_family_has_a_vocabulary(self) -> None:
        assert all(family.formats for family in families.FAMILIES)


# ── 3. Error — every refusal says what to do next ────────────────────


class TestRefusals:
    def test_formats_from_different_families_name_both(self) -> None:
        with pytest.raises(ValueError, match="different conversion families"):
            families.resolve_family("docx", "mp4")

    def test_an_unknown_format_is_named(self) -> None:
        with pytest.raises(ValueError, match="'weirdext' does not name a format"):
            families.resolve_family("weirdext", "pdf")

    def test_an_unknown_source_suggests_source_format(self) -> None:
        with pytest.raises(ValueError, match="pass source_format"):
            families.resolve_family("weirdext", "pdf")

    def test_an_unknown_target_does_not_suggest_source_format(self) -> None:
        # The hint would be noise: the source resolved fine.
        with pytest.raises(ValueError, match="does not name a format") as caught:
            families.resolve_family("pdf", "weirdext")
        assert "pass source_format" not in str(caught.value)

    def test_converting_a_format_to_itself_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a conversion"):
            families.resolve_family("pdf", "pdf")

    def test_page_range_outside_document_conversion_is_refused(self) -> None:
        with pytest.raises(ValueError, match="document-conversion parameter"):
            families.build_payload(
                file_id="f_1", source_format="png", target_format="webp", page_range="1-5"
            )

    def test_a_preset_quality_on_an_image_is_refused(self) -> None:
        with pytest.raises(ValueError, match="numeric quality"):
            families.build_payload(
                file_id="f_1", source_format="png", target_format="webp", quality="standard"
            )


# ── 4. Object state — the wire shape each family declares ────────────


class TestPayloadShape:
    def test_document_declares_its_source_and_names_the_file_source_file_id(self) -> None:
        payload = families.build_payload(
            file_id="f_1", source_format="docx", target_format="pdf", page_range="1-5"
        )
        assert payload == {
            "processor_type": "document_conversion",
            "params": {
                "source_file_id": "f_1",
                "target_format": "pdf",
                "source_format": "docx",
                "page_range": "1-5",
            },
        }

    def test_image_uses_file_id_and_output_format_and_sends_no_source(self) -> None:
        payload = families.build_payload(
            file_id="f_1", source_format="png", target_format="webp", quality="80"
        )
        assert payload == {
            "processor_type": "image_conversion",
            "params": {"file_id": "f_1", "output_format": "webp", "quality": 80},
        }

    def test_media_uses_file_id_and_output_format_and_a_preset_quality(self) -> None:
        payload = families.build_payload(
            file_id="f_1", source_format="mp4", target_format="mp3", quality="high"
        )
        assert payload == {
            "processor_type": "media_processing",
            "params": {"file_id": "f_1", "output_format": "mp3", "quality": "high"},
        }

    def test_quality_is_absent_when_the_caller_did_not_ask_for_one(self) -> None:
        # Each processor's own default then applies — and they disagree
        # ("standard" vs 80), so no single client-side default could be right.
        payload = families.build_payload(file_id="f_1", source_format="docx", target_format="pdf")
        assert "quality" not in payload["params"]

    def test_a_numeric_quality_reaches_a_media_conversion_as_a_string(self) -> None:
        payload = families.build_payload(
            file_id="f_1", source_format="mp4", target_format="mp3", quality=1
        )
        assert payload["params"]["quality"] == "1"

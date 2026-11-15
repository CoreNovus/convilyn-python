"""`rag.split` — Markdown in, passages out.

Sizes are measured with `len` in most cases so the expected cuts can be read
straight off the fixture text.
"""

from __future__ import annotations

import hashlib

import pytest

from convilyn import rag

SMALL = rag.SplitOptions(target=40, max=60, min=10)


def _texts(markdown: str, **kwargs: object) -> list[str]:
    return [p.text for p in rag.split(markdown, source_id="s", measure=len, **kwargs)]  # type: ignore[arg-type]


def _trails(markdown: str, **kwargs: object) -> list[tuple[str, ...]]:
    return [p.section for p in rag.split(markdown, source_id="s", **kwargs)]  # type: ignore[arg-type]


class TestSections:
    def test_each_heading_opens_a_section_with_its_trail(self) -> None:
        markdown = "# Guide\n\nintro\n\n## Setup\n\nsteps\n\n### Win\n\nnotes\n\n## Usage\n\nrun"

        result = _trails(markdown)

        assert result == [
            ("Guide",),
            ("Guide", "Setup"),
            ("Guide", "Setup", "Win"),
            ("Guide", "Usage"),
        ]

    def test_a_passage_never_crosses_a_section(self) -> None:
        markdown = "## A\n\none\n\n## B\n\ntwo"

        result = _texts(markdown)

        assert result == ["one", "two"]

    def test_headings_below_section_depth_stay_in_the_text(self) -> None:
        markdown = "# Top\n\nbody\n\n#### Detail\n\nmore"

        result = _texts(markdown)

        assert result == ["body\n\n#### Detail\n\nmore"]

    def test_section_depth_zero_keeps_the_document_as_one_section(self) -> None:
        markdown = "# A\n\none\n\n## B\n\ntwo"

        result = _trails(markdown, options=rag.SplitOptions(section_depth=0))

        assert result == [()]

    def test_setext_headings_open_sections(self) -> None:
        markdown = "Title\n=====\n\nbody\n\nSub\n---\n\nmore"

        result = _trails(markdown)

        assert result == [("Title",), ("Title", "Sub")]

    def test_a_heading_without_content_yields_no_passage(self) -> None:
        markdown = "# Empty\n\n## Filled\n\nbody"

        result = _trails(markdown)

        assert result == [("Empty", "Filled")]

    def test_a_hash_inside_a_fenced_block_is_not_a_heading(self) -> None:
        markdown = "# Real\n\n```sh\n# comment\n```"

        result = _trails(markdown)

        assert result == [("Real",)]

    def test_text_before_the_first_heading_has_an_empty_trail(self) -> None:
        result = _trails("preface\n\n# One\n\nbody")

        assert result == [(), ("One",)]


LONG = "a" * 30_000


class TestLongHeadings:
    """Every passage carries its trail, so a name in it has a bound."""

    def test_a_heading_of_ordinary_length_is_carried_as_written(self) -> None:
        heading = "h" * 256

        result = _trails(f"# {heading}\n\nbody")

        assert result == [(heading,)]

    def test_a_long_heading_is_cut_and_marked_in_the_trail(self) -> None:
        result = _trails(f"# {LONG}\n\nbody")

        assert set(result) == {("a" * 255 + "…",)}

    def test_a_long_heading_is_kept_whole_as_text_of_its_section(self) -> None:
        passages = rag.split(f"# {LONG}\n\nbody", source_id="s", measure=len)

        assert "".join(passage.text for passage in passages).count("a") == 30_000

    def test_a_long_title_is_cut_like_a_heading(self) -> None:
        result = _trails("body", title=LONG)

        assert result == [("a" * 255 + "…",)]

    def test_a_cut_title_and_the_same_first_heading_are_not_repeated(self) -> None:
        result = _trails(f"# {LONG}\n\nbody", title=LONG)

        assert set(result) == {("a" * 255 + "…",)}

    def test_passages_under_one_long_heading_stay_in_proportion_to_the_document(
        self,
    ) -> None:
        """Before the bound, this document wrote 278 times its length in trails alone."""
        markdown = f"# {LONG}\n\n" + "word. " * 5000

        passages = rag.split(
            markdown, source_id="s", options=rag.SplitOptions(target=16, max=16, min=0)
        )

        written = sum(len(passage.model_dump_json()) for passage in passages)
        assert written <= 12 * len(markdown)

    def test_a_trail_costs_each_passage_a_bounded_number_of_characters(self) -> None:
        markdown = "\n\n".join(f"{'#' * level} {LONG}" for level in (1, 2, 3)) + "\n\nbody"

        passages = rag.split(markdown, source_id="s", title=LONG)

        assert max(sum(map(len, passage.section)) for passage in passages) <= 512

    def test_outer_names_give_way_first_when_a_trail_is_long(self) -> None:
        outer, inner = "o" * 200, "i" * 200
        markdown = f"# {outer}\n\n## {'m' * 200}\n\n### {inner}\n\nbody"

        result = _trails(markdown)

        assert result[-1] == ("o" * 111 + "…", "m" * 200, inner)

    def test_a_title_and_a_first_heading_that_differ_past_the_cut_are_both_kept(self) -> None:
        title, heading = "a" * 255 + "YY", "a" * 255 + "ZZ"

        result = _trails(f"# {heading}\n\nbody", title=title)

        assert len(set(result)) == 1 and len(result[0]) == 2

    def test_control_characters_are_read_as_spaces_in_a_trail(self) -> None:
        result = _trails("# Refund\x01policy\x7f\x85 terms\n\nbody")

        assert result == [("Refund policy terms",)]

    def test_a_tab_in_a_title_still_separates_words(self) -> None:
        result = _trails("body", title="Annual\treport")

        assert result == [("Annual report",)]

    def test_a_name_of_control_characters_alone_is_left_out(self) -> None:
        result = _trails("# Intro\n\n## \x01\x02\n\nbody", title="\x01" * 5)

        assert result == [("Intro",)]

    def test_a_title_and_a_heading_that_differ_only_in_control_characters_are_one(
        self,
    ) -> None:
        result = _trails("# Intro\n\nbody", title="Intro\x01")

        assert result == [("Intro",)]

    def test_a_long_heading_above_many_sections_is_read_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cleaning and cutting a name once is what keeps this linear."""
        from convilyn.rag import _split

        read: list[int] = []
        original = _split._readable

        def counted(name: str) -> str:
            read.append(len(name))
            return original(name)

        monkeypatch.setattr(_split, "_readable", counted)
        markdown = "# " + "\x01" * 50_000 + "\n\n" + "##\ny\n\n" * 2_000

        rag.split(markdown, source_id="s", title="\x01" * 25_000)

        assert sum(read) <= 2 * len(markdown)

    def test_many_short_sections_under_long_headings_stay_in_proportion(self) -> None:
        """Before the whole-trail bound and with controls kept, this wrote 890 times its size."""
        names = "\n\n".join(f"{'#' * level} {chr(1) * 300}" for level in range(1, 6))
        markdown = names + "\n\n" + "######\ny\n\n" * 2000

        passages = rag.split(
            markdown, source_id="s", title=chr(1) * 300, options=rag.SplitOptions(section_depth=6)
        )

        written = sum(len(passage.model_dump_json()) for passage in passages)
        assert written <= 100 * len(markdown)


class TestTitle:
    def test_title_leads_every_trail(self) -> None:
        result = _trails("intro\n\n## Setup\n\nsteps", title="Handbook")

        assert result == [("Handbook",), ("Handbook", "Setup")]

    def test_title_equal_to_the_first_heading_is_not_repeated(self) -> None:
        result = _trails("# Handbook\n\nintro\n\n## Setup\n\nsteps", title="Handbook")

        assert result == [("Handbook",), ("Handbook", "Setup")]


class TestGathering:
    def test_small_blocks_are_gathered_until_target(self) -> None:
        markdown = "\n\n".join(["a" * 15, "b" * 15, "c" * 15, "d" * 15])

        result = _texts(markdown, options=SMALL)

        assert result == [f"{'a' * 15}\n\n{'b' * 15}\n\n{'c' * 15}", "d" * 15]

    def test_a_block_that_would_exceed_max_starts_a_new_passage(self) -> None:
        markdown = "\n\n".join(["a" * 35, "b" * 35])

        result = _texts(markdown, options=SMALL)

        assert result == ["a" * 35, "b" * 35]

    def test_a_small_trailing_piece_joins_the_previous_passage(self) -> None:
        markdown = "\n\n".join(["a" * 45, "b" * 5])

        result = _texts(markdown, options=SMALL)

        assert result == [f"{'a' * 45}\n\n{'b' * 5}"]

    def test_a_small_trailing_piece_stays_alone_when_joining_would_exceed_max(self) -> None:
        markdown = "\n\n".join(["a" * 58, "b" * 5])

        result = _texts(markdown, options=SMALL)

        assert result == ["a" * 58, "b" * 5]

    def test_a_small_only_piece_is_kept(self) -> None:
        result = _texts("tiny", options=SMALL)

        assert result == ["tiny"]

    def test_no_passage_exceeds_max(self) -> None:
        markdown = "# T\n\n" + "\n\n".join(f"Sentence number {n} ends here." * 3 for n in range(30))

        sizes = [len(text) for text in _texts(markdown, options=SMALL)]

        assert max(sizes) <= SMALL.max


class TestOversizeBlocks:
    def test_a_paragraph_is_divided_at_sentence_ends(self) -> None:
        markdown = "第一句話在這裡結束。第二句話也在這裡結束。第三句話同樣結束。"

        result = _texts(markdown, options=rag.SplitOptions(target=12, max=12, min=0))

        assert result == ["第一句話在這裡結束。", "第二句話也在這裡結束。", "第三句話同樣結束。"]

    def test_a_decimal_point_is_not_a_sentence_end(self) -> None:
        markdown = "Version 3.5 is out. Upgrade today."

        result = _texts(markdown, options=rag.SplitOptions(target=20, max=20, min=0))

        assert result == ["Version 3.5 is out.", "Upgrade today."]

    def test_a_sentence_longer_than_max_is_cut_to_fit(self) -> None:
        result = _texts("x" * 25, options=rag.SplitOptions(target=10, max=10, min=0))

        assert result == ["x" * 10, "x" * 10, "x" * 5]

    def test_a_list_is_divided_between_items(self) -> None:
        markdown = "- first item text\n- second item text\n- third item text"

        result = _texts(markdown, options=rag.SplitOptions(target=36, max=36, min=0))

        assert result == ["- first item text\n- second item text", "- third item text"]

    def test_a_nested_item_stays_with_its_parent(self) -> None:
        markdown = "- parent one\n  - child one\n- parent two\n  - child two"

        result = _texts(markdown, options=rag.SplitOptions(target=26, max=26, min=0))

        assert result == ["- parent one\n  - child one", "- parent two\n  - child two"]

    def test_a_table_repeats_its_header_in_every_piece(self) -> None:
        markdown = "| k | v |\n|---|---|\n| a | 1 |\n| b | 2 |\n| c | 3 |"

        result = _texts(markdown, options=rag.SplitOptions(target=40, max=40, min=0))

        assert result == [
            "| k | v |\n|---|---|\n| a | 1 |\n| b | 2 |",
            "| k | v |\n|---|---|\n| c | 3 |",
        ]

    def test_a_divided_table_has_no_piece_of_the_header_alone(self) -> None:
        header = "| k | v |\n|---|---|\n"
        markdown = f"{header}| a |{' ' * 600}| b |"

        passages = rag.split(
            markdown,
            source_id="doc",
            options=rag.SplitOptions(target=50, max=50, min=0),
            measure=len,
        )

        assert [passage.text.removeprefix(header).strip() != "" for passage in passages] == [
            True
        ] * len(passages)

    def test_a_divided_table_keeps_every_cell_of_a_row_cut_through_spaces(self) -> None:
        header = "| k | v |\n|---|---|\n"
        markdown = f"{header}| a |{' ' * 600}| b |"

        passages = rag.split(
            markdown,
            source_id="doc",
            options=rag.SplitOptions(target=50, max=50, min=0),
            measure=len,
        )

        assert [passage.text.removeprefix(header).strip() for passage in passages] == [
            "| a |",
            "| b |",
        ]

    def test_whitespace_heavy_input_is_cut_into_passages_of_bounded_length(self) -> None:
        """Deep indentation and blank lines cost tokens, so they cannot hide in a passage."""
        line = " " * 59 + "x"
        markdown = "```\n" + "\n\n".join([line] * 1000) + "\n```"  # about 62,000 characters

        passages = rag.split(markdown, source_id="doc")

        # 512 tokens at no less than 2 per line of 61 characters.
        assert max(len(passage.text) for passage in passages) <= 512 * 61 // 2

    def test_a_divided_fence_stays_within_the_limit_when_lines_have_spaces_at_their_edges(
        self,
    ) -> None:
        """A space beside a line break costs a token that the space alone does not."""
        markdown = "```\n" + "\n".join([" word "] * 40) + "\n```"
        options = rag.SplitOptions(target=23, max=23, min=0)

        passages = rag.split(markdown, source_id="doc", options=options)

        assert max(rag.estimate_tokens(passage.text) for passage in passages) <= 23

    def test_a_measure_that_charges_far_more_inside_a_fence_still_ends_with_all_the_content(
        self,
    ) -> None:
        """Your own measure need not add up either; the worst it gets is small pieces."""

        def surcharged(text: str) -> int:
            return len(text) + (10 if "x" in text and "`" in text else 0)

        markdown = "```\n" + "x" * 30 + "\n```"

        passages = rag.split(
            markdown,
            source_id="doc",
            options=rag.SplitOptions(target=16, max=16, min=0),
            measure=surcharged,
        )

        assert "".join(passage.text.strip("`\n") for passage in passages) == "x" * 30

    def test_a_fence_long_in_characters_does_not_multiply_the_work(self) -> None:
        """The fence is measured once per passage, not once per line of content.

        Measuring it with every line read about 870 times the document's
        length here; once per passage reads about 20 times.
        """
        measured = 0

        def counted(text: str) -> int:
            nonlocal measured
            measured += len(text)
            return rag.estimate_tokens(text)

        opening = "```py" + " " * 4_000  # a long frame that is still repeated
        markdown = opening + "\n" + "\n".join(["xy"] * 2000) + "\n```"

        rag.split(markdown, source_id="doc", measure=counted)

        assert measured < 100 * len(markdown)

    def test_a_fence_padded_with_spaces_is_not_copied_into_every_piece(self) -> None:
        """A measure that sees a long run of spaces as nothing must not make the output balloon."""
        markdown = "```" + " " * 30_000 + "\n" + "\n".join(["xy"] * 300) + "\n```"
        options = rag.SplitOptions(target=4, max=4, min=0, tokenizer="xlm_r")

        passages = rag.split(markdown, source_id="doc", options=options)

        assert sum(len(passage.text) for passage in passages) <= 2 * len(markdown)

    def test_a_table_header_padded_with_spaces_is_not_copied_into_every_piece(self) -> None:
        header = "| k" + " " * 30_000 + "| v |\n|---|---|\n"
        markdown = header + "\n".join(["| a | 1 |"] * 300)
        options = rag.SplitOptions(target=8, max=8, min=0, tokenizer="xlm_r")

        passages = rag.split(markdown, source_id="doc", options=options)

        assert sum(len(passage.text) for passage in passages) <= 2 * len(markdown)

    def test_a_measure_that_keeps_one_line_over_the_limit_does_not_regroup_without_end(
        self,
    ) -> None:
        """One line your measure prices above any room is tried a few times, not once per unit."""
        rounds = 0

        def priced(text: str) -> int:
            nonlocal rounds
            rounds += text.startswith("```") and "Z" in text
            return len(text) + 509 * text.count("Z")

        markdown = "```\n" + "\n".join(["ab"] * 200 + ["Z"]) + "\n```"

        rag.split(
            markdown,
            source_id="doc",
            options=rag.SplitOptions(target=512, max=512, min=0),
            measure=priced,
        )

        assert rounds < 40

    def test_a_measure_that_answers_with_something_that_is_not_a_number_still_ends(self) -> None:
        def broken(text: str) -> int:
            if "`" in text and len(text) > 8:
                return float("nan")  # type: ignore[return-value]
            return len(text)

        markdown = "```\n" + "\n".join(["ab cd"] * 50) + "\n```"

        passages = rag.split(
            markdown,
            source_id="doc",
            options=rag.SplitOptions(target=32, max=32, min=0),
            measure=broken,
        )

        assert len(passages) > 0

    def test_a_divided_table_stays_within_the_limit_when_cells_are_padded(self) -> None:
        header = "| k | v |\n|---|---|\n"
        markdown = header + "\n".join(["|  a  |" + " " * 30 + "b  |"] * 20)
        options = rag.SplitOptions(target=30, max=30, min=0)

        passages = rag.split(markdown, source_id="doc", options=options)

        assert max(rag.estimate_tokens(passage.text) for passage in passages) <= 30

    def test_a_fenced_block_repeats_its_fence_in_every_piece(self) -> None:
        markdown = "```py\nline_one = 1\nline_two = 2\nline_three = 3\n```"

        result = _texts(markdown, options=rag.SplitOptions(target=36, max=36, min=0))

        assert result == [
            "```py\nline_one = 1\nline_two = 2\n```",
            "```py\nline_three = 3\n```",
        ]

    def test_an_oversize_deep_heading_is_cut_like_prose(self) -> None:
        markdown = "#### " + "h" * 30

        result = _texts(markdown, options=rag.SplitOptions(target=20, max=20, min=0))

        assert [len(text) <= 20 for text in result] == [True, True]


class TestInputBoundary:
    @pytest.mark.parametrize("markdown", ["", "   \n\n\t\n", "# Only a heading"])
    def test_a_document_without_content_yields_nothing(self, markdown: str) -> None:
        result = rag.split(markdown, source_id="s")

        assert result == ()

    def test_front_matter_is_not_content(self) -> None:
        result = _texts("---\ntitle: x\n---\n\nbody")

        assert result == ["body"]

    def test_an_unclosed_front_matter_fence_is_treated_as_content(self) -> None:
        result = _texts("---\n\nbody")

        assert result == ["body"]

    def test_windows_line_endings_are_read_as_newlines(self) -> None:
        result = _texts("# T\r\n\r\nline one\r\nline two")

        assert result == ["line one\nline two"]

    def test_a_thematic_break_separates_blocks_and_is_dropped(self) -> None:
        result = _texts("above\n\n***\n\nbelow")

        assert result == ["above\n\nbelow"]

    def test_an_unclosed_fence_runs_to_the_end(self) -> None:
        result = _texts("```\ncode\n# not a heading")

        assert result == ["```\ncode\n# not a heading"]

    def test_a_list_continues_across_a_blank_line_before_an_indented_line(self) -> None:
        result = _texts("- one\n\n  still one\n\nafter")

        assert result == ["- one\n\n  still one\n\nafter"]


class TestErrors:
    def test_an_empty_source_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source_id must be a non-empty string"):
            rag.split("body", source_id="")


class TestPassageIdentity:
    def test_passage_fields_describe_the_piece(self) -> None:
        passage = rag.split("# T\n\nbody", source_id="doc")[0]

        assert passage.model_dump(exclude={"passage_id"}) == {
            "source_id": "doc",
            "ordinal": 0,
            "section": ("T",),
            "text": "body",
            "digest": hashlib.sha256(b"body").hexdigest(),
        }

    def test_ordinals_count_from_zero_across_sections(self) -> None:
        passages = rag.split("## A\n\none\n\n## B\n\ntwo\n\n## C\n\nthree", source_id="doc")

        assert [p.ordinal for p in passages] == [0, 1, 2]

    def test_splitting_again_yields_the_same_identifiers(self) -> None:
        markdown = "## A\n\none\n\n## B\n\ntwo"

        first = [p.passage_id for p in rag.split(markdown, source_id="doc")]

        assert first == [p.passage_id for p in rag.split(markdown, source_id="doc")]

    def test_identifiers_differ_between_sources(self) -> None:
        one = rag.split("body", source_id="a")[0].passage_id

        assert one != rag.split("body", source_id="b")[0].passage_id

    def test_identifiers_are_unique_within_a_source_with_repeated_text(self) -> None:
        passages = rag.split("## A\n\nsame\n\n## B\n\nsame", source_id="doc")

        assert len({p.passage_id for p in passages}) == 2


class TestMeasure:
    def test_the_default_measure_is_the_cautious_token_estimate(self) -> None:
        markdown = "退款流程需要三個工作天。" * 40
        options = rag.SplitOptions(target=100, max=100, min=0)

        sizes = [
            rag.estimate_tokens(p.text) for p in rag.split(markdown, source_id="s", options=options)
        ]

        assert max(sizes) <= options.max

    def test_options_naming_a_tokenizer_are_measured_with_its_estimate(self) -> None:
        markdown = "退款流程需要三個工作天。" * 40
        options = rag.SplitOptions(target=100, max=100, min=0, tokenizer="qwen3")

        result = rag.split(markdown, source_id="s", options=options)

        assert result == rag.split(
            markdown,
            source_id="s",
            options=rag.SplitOptions(target=100, max=100, min=0),
            measure=rag.token_estimator("qwen3"),
        )

    def test_options_naming_a_language_are_measured_with_its_estimate(self) -> None:
        markdown = "Refunds are issued within three working days. " * 60
        options = rag.SplitOptions(target=100, max=100, min=0, language="en")

        result = rag.split(markdown, source_id="s", options=options)

        assert result == rag.split(
            markdown,
            source_id="s",
            options=rag.SplitOptions(target=100, max=100, min=0),
            measure=rag.token_estimator(language="en"),
        )

    def test_a_measure_argument_outranks_the_estimate_the_options_name(self) -> None:
        markdown = "\n\n".join(["one two", "three four", "five six"])
        options = rag.SplitOptions(target=4, max=4, min=0, tokenizer="cl100k", language="en")

        result = [
            p.text
            for p in rag.split(
                markdown, source_id="s", options=options, measure=lambda t: len(t.split())
            )
        ]

        assert result == ["one two\n\nthree four", "five six"]

    def test_a_japanese_half_width_full_stop_ends_a_sentence(self) -> None:
        markdown = "ｺﾝﾆﾁﾊｾｶｲﾃﾞｽ｡ﾂｷﾞﾉﾌﾞﾝﾃﾞｽ｡"
        options = rag.SplitOptions(target=12, max=12, min=0)

        result = [p.text for p in rag.split(markdown, source_id="s", options=options, measure=len)]

        assert result == ["ｺﾝﾆﾁﾊｾｶｲﾃﾞｽ｡", "ﾂｷﾞﾉﾌﾞﾝﾃﾞｽ｡"]

    def test_a_custom_measure_decides_the_cuts(self) -> None:
        markdown = "\n\n".join(["one two", "three four", "five six"])
        options = rag.SplitOptions(target=4, max=4, min=0)

        result = [
            p.text
            for p in rag.split(
                markdown, source_id="s", options=options, measure=lambda t: len(t.split())
            )
        ]

        assert result == ["one two\n\nthree four", "five six"]

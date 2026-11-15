"""`rag.split` on input written to be expensive or to break its promises.

Markdown reaches this function from wherever the caller got it, so its cost has
to stay proportional to the input and its size promise has to hold regardless.
Cost is asserted by counting calls to the length measure rather than by timing,
which keeps these deterministic.
"""

from __future__ import annotations

import pytest

from convilyn import rag

#: Long enough that a quadratic path would be unmistakable in the counts below.
_LONG = 20_000


class _CountingMeasure:
    """A measure, remembering how many characters it was asked to measure."""

    def __init__(self, inner: rag.Measure = len) -> None:
        self.characters = 0
        self._inner = inner

    def __call__(self, text: str) -> int:
        self.characters += len(text)
        return self._inner(text)


def _texts(markdown: str, options: rag.SplitOptions) -> list[str]:
    return [p.text for p in rag.split(markdown, source_id="s", options=options, measure=len)]


class TestHeadingMarkers:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("# Title", "Title"),
            ("#   Title   ", "Title"),
            ("## Title ##", "Title"),
            ("## Title ####   ", "Title"),
            ("# C#", "C#"),
            ("# Issue #5", "Issue #5"),
            ("# ###", ""),
            ("#\tTabbed", "Tabbed"),
        ],
        ids=[
            "plain",
            "padded",
            "closed",
            "closed-and-padded",
            "hash-in-word",
            "hash-mid-title",
            "only-closing-hashes",
            "tab-after-marker",
        ],
    )
    def test_heading_title_is_read_without_its_markers(self, line: str, expected: str) -> None:
        passages = rag.split(f"{line}\n\nbody", source_id="s")

        assert passages[0].section == ((expected,) if expected else ())

    @pytest.mark.parametrize("line", ["#Title", "####### seven"], ids=["no-space", "seven-hashes"])
    def test_a_line_that_only_resembles_a_heading_is_text(self, line: str) -> None:
        passages = rag.split(f"{line}\n\nbody", source_id="s")

        assert passages[0].section == ()

    def test_a_heading_with_a_very_long_run_of_spaces_is_still_read(self) -> None:
        markdown = "# a" + " " * _LONG + "b\n\nbody"

        passages = rag.split(markdown, source_id="s")

        # Read as a heading: it opens the trail, cut to the trail's bound.
        assert passages[0].section == (("a" + " " * _LONG)[:255] + "…",)


class TestTableDelimiter:
    @pytest.mark.parametrize(
        "delimiter",
        ["|---|---|", "---|---", "| :--- | ---: |", "|:-:|:-:|", "  | - | - |  "],
        ids=["piped", "bare", "aligned", "centred", "padded"],
    )
    def test_a_delimiter_row_makes_the_block_a_table(self, delimiter: str) -> None:
        rows = "\n".join(f"| a | {n} |" for n in range(12))
        markdown = f"| k | v |\n{delimiter}\n{rows}"

        result = _texts(markdown, rag.SplitOptions(target=80, max=80, min=0))

        # Only a table repeats its first two lines in every piece.
        repeated = [text.startswith(f"| k | v |\n{delimiter}\n") for text in result]
        assert (len(result) >= 2, all(repeated)) == (True, True)

    @pytest.mark.parametrize(
        "second_line",
        ["| a | b |", "|---|x|", "| |", "|| ---"],
        ids=["ordinary-row", "non-dash-cell", "empty-cell", "empty-leading-cell"],
    )
    def test_without_a_delimiter_row_the_lines_are_a_paragraph(self, second_line: str) -> None:
        rows = "\n".join(f"more | text {n}" for n in range(8))
        markdown = f"k | v\n{second_line}\n{rows}"

        result = _texts(markdown, rag.SplitOptions(target=60, max=60, min=0))

        # A paragraph is cut wherever it fits; only a table would repeat line one.
        assert [text.startswith("k | v") for text in result][1:].count(True) == 0

    def test_a_delimiter_candidate_with_a_very_long_run_of_spaces_is_handled(self) -> None:
        markdown = "k | v\n" + " " * _LONG + "x|\nrow"

        passages = rag.split(markdown, source_id="s", measure=lambda _: 1)

        assert len(passages) == 1


class TestCostStaysProportional:
    @pytest.mark.parametrize(
        "text",
        ["a" * _LONG, "字" * _LONG, "word " * (_LONG // 5)],
        ids=["latin-no-sentence-end", "cjk-no-sentence-end", "spaced-no-sentence-end"],
    )
    def test_text_without_a_sentence_end_is_cut_in_near_linear_work(self, text: str) -> None:
        measure = _CountingMeasure()

        rag.split(text, source_id="s", measure=measure)

        # A quadratic cut measures on the order of len(text)**2 / max characters
        # (about 780,000 here); the bound is forty passes over the input.
        assert measure.characters <= 40 * len(text)

    @pytest.mark.parametrize("inner", [len, rag.estimate_tokens], ids=["len", "estimate"])
    @pytest.mark.parametrize(
        "markdown",
        [
            "a\n\n" * (_LONG // 3),
            "- a\n" * (_LONG // 4),
            "a. " * (_LONG // 3),
            "字。" * (_LONG // 2),
            "```\n" + "a\n" * (_LONG // 2) + "```",
            "| k |\n|---|\n" + "| a |\n" * (_LONG // 6),
        ],
        ids=["blocks", "list-items", "sentences", "cjk-sentences", "fence-lines", "table-rows"],
    )
    def test_very_many_small_pieces_are_joined_in_near_linear_work(
        self, markdown: str, inner: rag.Measure
    ) -> None:
        measure = _CountingMeasure(inner)

        rag.split(markdown, source_id="s", measure=measure)

        # Measuring the growing passage again for every piece added to it costs
        # hundreds of passes over the input; the bound is forty.
        assert measure.characters <= 40 * len(markdown)

    def test_a_size_limit_larger_than_the_input_does_not_make_joining_quadratic(self) -> None:
        markdown = "a\n\n" * (_LONG // 3)
        measure = _CountingMeasure()
        roomy = rag.SplitOptions(target=10 * _LONG, max=10 * _LONG, min=0)

        rag.split(markdown, source_id="s", options=roomy, measure=measure)

        assert measure.characters <= 40 * len(markdown)

    def test_no_text_is_lost_when_joining_very_many_small_blocks(self) -> None:
        words = [f"w{n}" for n in range(2_000)]

        result = _texts("\n\n".join(words), rag.SplitOptions(target=100, max=120, min=0))

        assert "\n\n".join(result).split("\n\n") == words

    def test_no_text_is_lost_when_cutting_without_sentence_ends(self) -> None:
        text = "字" * 5_000

        result = _texts(text, rag.SplitOptions(target=100, max=100, min=0))

        assert "".join(result) == text


class TestNoPassageIsBlank:
    """Under a measure that counts whitespace, a run of it can fill a piece."""

    @pytest.mark.parametrize("gap", [512, 1024, 5_000], ids=["one-piece", "two-pieces", "many"])
    def test_a_long_run_of_spaces_inside_a_paragraph_makes_no_passage(self, gap: int) -> None:
        markdown = "x" * 512 + " " * gap + "y" * 512

        result = _texts(markdown, rag.SplitOptions())

        kept = "".join(result)
        assert (all(text.strip() for text in result), kept) == (True, "x" * 512 + "y" * 512)

    def test_blank_lines_filling_a_fence_piece_make_no_passage(self) -> None:
        markdown = "```\naaaaaaaaaaaa\n\nbbbbbbbbbbbb\n```"

        result = _texts(markdown, rag.SplitOptions(target=20, max=20, min=0))

        assert result == ["```\naaaaaaaaaaaa\n```", "```\nbbbbbbbbbbbb\n```"]

    def test_a_fence_of_only_blank_lines_makes_no_empty_piece(self) -> None:
        markdown = "```\n" + "\n" * 200 + "```\n\nafter"

        result = _texts(markdown, rag.SplitOptions(target=40, max=40, min=0))

        assert result == ["after"]


class TestRepeatedFramesStayBounded:
    def test_an_oversize_table_header_is_not_repeated(self) -> None:
        header = "| " + "h" * 400 + " |\n|---|"
        markdown = header + "\n" + "\n".join(f"| row {n} |" for n in range(200))

        result = _texts(markdown, rag.SplitOptions(target=100, max=100, min=0))

        assert sum(len(text) for text in result) <= 2 * len(markdown)

    def test_an_oversize_fence_opening_is_not_repeated(self) -> None:
        markdown = "```" + "i" * 400 + "\n" + "\n".join(f"line {n}" for n in range(200)) + "\n```"

        result = _texts(markdown, rag.SplitOptions(target=100, max=100, min=0))

        assert sum(len(text) for text in result) <= 2 * len(markdown)

    @pytest.mark.parametrize(
        "markdown",
        [
            "| " + "h" * 400 + " |\n|---|\n" + "\n".join(f"| {n} |" for n in range(50)),
            "```" + "i" * 400 + "\n" + "\n".join(f"line {n}" for n in range(50)),
            "| k | v |\n|---|---|\n" + "\n".join(f"| {'x' * 300} | {n} |" for n in range(5)),
        ],
        ids=["oversize-header", "oversize-fence-opening", "oversize-rows"],
    )
    def test_no_passage_exceeds_max_whatever_the_block(self, markdown: str) -> None:
        options = rag.SplitOptions(target=100, max=100, min=0)

        sizes = [len(text) for text in _texts(markdown, options)]

        assert max(sizes) <= options.max

    def test_a_header_at_exactly_half_the_limit_is_still_repeated(self) -> None:
        header = "| abcdefg |\n|------|"  # 21 characters with its newline
        markdown = header + "\n| one |\n| two |\n| three |"

        result = _texts(markdown, rag.SplitOptions(target=42, max=42, min=0))

        assert [text.startswith(header) for text in result] == [True, True]

    def test_a_header_just_over_half_the_limit_is_not_repeated(self) -> None:
        header = "| abcdefg |\n|------|"
        markdown = header + "\n| one |\n| two |\n| three |"

        result = _texts(markdown, rag.SplitOptions(target=41, max=41, min=0))

        assert [text.startswith(header) for text in result] == [True, False]


class TestDividedFences:
    def test_an_unclosed_fence_keeps_its_last_line(self) -> None:
        markdown = "```\n" + "\n".join(f"line {n}" for n in range(10))

        result = _texts(markdown, rag.SplitOptions(target=40, max=40, min=0))

        assert "line 9" in result[-1]

    def test_every_piece_of_an_unclosed_fence_is_closed(self) -> None:
        markdown = "```\n" + "\n".join(f"line {n}" for n in range(10))

        result = _texts(markdown, rag.SplitOptions(target=40, max=40, min=0))

        assert all(text.endswith("\n```") for text in result)

    def test_a_long_fence_is_closed_with_a_fence_of_the_same_length(self) -> None:
        markdown = "`````\n" + "\n".join(f"line {n}" for n in range(10)) + "\n`````"

        result = _texts(markdown, rag.SplitOptions(target=40, max=40, min=0))

        assert all(text.startswith("`````\n") and text.endswith("\n`````") for text in result)

    def test_a_shorter_fence_inside_the_content_does_not_end_the_block(self) -> None:
        markdown = "````\n```\ninner\n```\n````\n\nafter"

        result = _texts(markdown, rag.SplitOptions(target=4096, max=4096, min=0))

        assert result == [markdown]

    def test_a_tilde_fence_is_divided_with_tildes(self) -> None:
        markdown = "~~~\n" + "\n".join(f"line {n}" for n in range(10)) + "\n~~~"

        result = _texts(markdown, rag.SplitOptions(target=40, max=40, min=0))

        assert all(text.startswith("~~~\n") and text.endswith("\n~~~") for text in result)

    def test_a_blank_line_inside_a_divided_fence_is_kept(self) -> None:
        markdown = "```\nfirst = 1\n\nsecond = 2\nthird = 3\nfourth = 4\n```"

        result = _texts(markdown, rag.SplitOptions(target=36, max=36, min=0))

        assert result[0] == "```\nfirst = 1\n\nsecond = 2\n```"

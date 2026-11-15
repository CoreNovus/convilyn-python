"""Reading a question file: errors name the line, never quote it."""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.rag import bench

GOOD = (
    '{"id": "q1", "question": "how long do refunds take", '
    '"support": [{"source_id": "handbook", "excerpt": "five days"}], "group": "billing"}\n'
    "\n"
    '{"id": "q2", "question": "who founded the moon", "status": "draft"}\n'
)


def write(directory: Path, text: str) -> Path:
    path = directory / "questions.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


class TestReading:
    def test_every_question_is_read_in_order_and_blank_lines_skipped(self, tmp_path: Path) -> None:
        loaded = bench.questions(write(tmp_path, GOOD))

        assert [(q.id, q.status) for q in loaded] == [("q1", "ready"), ("q2", "draft")]

    def test_the_support_is_read_as_given(self, tmp_path: Path) -> None:
        loaded = bench.questions(write(tmp_path, GOOD))

        assert loaded[0].support == (bench.Support(source_id="handbook", excerpt="five days"),)

    def test_a_byte_order_mark_is_not_part_of_the_first_line(self, tmp_path: Path) -> None:
        path = tmp_path / "questions.jsonl"
        path.write_bytes(b"\xef\xbb\xbf" + GOOD.encode("utf-8"))

        assert len(bench.questions(path)) == 2


class TestRefusals:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ('{"id": "q1", "question": "PLANTED-VALUE"\n', r"line 1: not valid JSON \(column"),
            ('["PLANTED-VALUE"]\n', "line 1: must be a JSON object"),
            ('{"id": "q1"}\n', "line 1: question: Field required"),
            ('{"id": "q1", "question": "x", "PLANTED-VALUE": 1}\n', "line 1: unknown field"),
            ('{"id": "q1", "question": "x", "status": "PLANTED-VALUE"}\n', "line 1: status: "),
            (
                '{"id": "q1", "question": "x", "support": [{"excerpt": "y"}]}\n',
                "support.0.source_id",
            ),
            ("\n\n", "no questions"),
        ],
        ids=[
            "not-json",
            "not-an-object",
            "missing",
            "unknown-field",
            "bad-status",
            "bad-support",
            "empty",
        ],
    )
    def test_a_line_that_is_not_a_question_is_refused_without_quoting_it(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message) as caught:
            bench.questions(write(tmp_path, text))

        assert ("PLANTED-VALUE" in str(caught.value), caught.value.__context__) == (False, None)

    def test_a_repeated_id_names_both_lines(self, tmp_path: Path) -> None:
        text = '{"id": "q1", "question": "a"}\n{"id": "q1", "question": "b"}\n'

        with pytest.raises(ValueError, match="line 2 repeats the id of 1"):
            bench.questions(write(tmp_path, text))

    def test_a_file_that_is_not_utf8_is_refused_without_its_content(self, tmp_path: Path) -> None:
        path = tmp_path / "questions.jsonl"
        path.write_bytes(b'{"id": "q1", "question": "PLANTED-VALUE caf\xe9"}\n')

        with pytest.raises(ValueError, match=r"not UTF-8 text \(byte") as caught:
            bench.questions(path)

        shown = repr(caught.value) + repr(caught.value.args)
        assert ("PLANTED-VALUE" in shown, caught.value.__context__) == (False, None)

    @pytest.mark.parametrize(
        "line",
        [
            '{"id": "q1", "question": "x", "support": [{"source_id": "a", "excerpt": "5"}]}',
            '{"id": "q1", "question": "x", "support": [{"source_id": "a", "excerpt": "   "}]}',
        ],
        ids=["one-character", "nothing-once-folded"],
    )
    def test_an_excerpt_that_could_never_match_is_refused(self, tmp_path: Path, line: str) -> None:
        with pytest.raises(ValueError, match="at least two characters once folded"):
            bench.questions(write(tmp_path, line + "\n"))

    @pytest.mark.parametrize(
        "line",
        ["[" * 100_000, '{"id": "q1", "question": ' + "9" * 5000 + "}"],
        ids=["deep", "digits"],
    )
    def test_json_the_parser_gives_up_on_is_refused_like_any_other(
        self, tmp_path: Path, line: str
    ) -> None:
        with pytest.raises(ValueError, match="line 1: not valid JSON$"):
            bench.questions(write(tmp_path, line + "\n"))

    def test_a_line_separator_inside_a_string_does_not_split_the_line(self, tmp_path: Path) -> None:
        line = '{"id": "q1", "question": "one two"}\n'

        assert bench.questions(write(tmp_path, line))[0].question == "one two"

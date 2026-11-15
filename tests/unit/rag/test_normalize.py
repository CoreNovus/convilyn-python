"""`rag.normalize` and `rag.normalizer_stamp` — the one text fold."""

from __future__ import annotations

import pytest

from convilyn import rag


class TestNormalizeLogic:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("ＡＢＣ１２３", "abc123"),
            ("Hello World", "hello world"),
            ("退​款‍流程", "退款流程"),
            ("ｶﾀｶﾅ", "カタカナ"),
            ("line one\n\tline two", "line one\n\tline two"),
        ],
        ids=["full-width", "case", "zero-width", "half-width-kana", "layout-kept"],
    )
    def test_default_options_fold_text(self, text: str, expected: str) -> None:
        result = rag.normalize(text)

        assert result == expected

    @pytest.mark.parametrize(
        ("options", "expected"),
        [
            (rag.NormalizeOptions(fold_width=False), "ａb​".lower().replace("​", "")),
            (rag.NormalizeOptions(fold_case=False), "aB"),
            (rag.NormalizeOptions(strip_invisible=False), "ab​"),
            (
                rag.NormalizeOptions(fold_width=False, fold_case=False, strip_invisible=False),
                "ａB​",
            ),
        ],
        ids=["no-width", "no-case", "no-strip", "nothing"],
    )
    def test_each_switch_disables_only_its_fold(
        self, options: rag.NormalizeOptions, expected: str
    ) -> None:
        result = rag.normalize("ａB​", options)

        assert result == expected


class TestNormalizeBoundary:
    def test_empty_text_stays_empty(self) -> None:
        result = rag.normalize("")

        assert result == ""

    def test_text_of_only_invisible_characters_becomes_empty(self) -> None:
        result = rag.normalize("​‌\u0000")

        assert result == ""

    def test_normalizing_twice_changes_nothing_more(self) -> None:
        once = rag.normalize("Ｆｕｌｌ Width​ 한국어 日本語")

        assert rag.normalize(once) == once


class TestNormalizerStamp:
    @pytest.mark.parametrize(
        ("options", "expected"),
        [
            (None, "nfkc+lower+visible"),
            (rag.NormalizeOptions(fold_case=False), "nfkc+visible"),
            (rag.NormalizeOptions(fold_width=False, strip_invisible=False), "lower"),
            (
                rag.NormalizeOptions(fold_width=False, fold_case=False, strip_invisible=False),
                "none",
            ),
        ],
        ids=["default", "no-case", "case-only", "nothing"],
    )
    def test_stamp_names_the_enabled_folds(
        self, options: rag.NormalizeOptions | None, expected: str
    ) -> None:
        result = rag.normalizer_stamp(options)

        assert result == expected

    def test_equal_options_share_a_stamp(self) -> None:
        first = rag.normalizer_stamp(rag.NormalizeOptions())

        assert first == rag.normalizer_stamp(rag.NormalizeOptions(fold_case=True))

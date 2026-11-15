"""`rag.token_estimator` and `rag.estimate_tokens` — tokens without a tokenizer.

The expected values are the measured figures applied by hand: a count of
characters divided by the characters per token of the densest measured language
written in that script (or of the named language), rounded up. The figures are
those of the measured document's text, its line breaks and indentation left
out; whitespace is counted by the run, on top.
"""

from __future__ import annotations

import pytest

from convilyn import rag

FAMILIES = ("qwen3", "xlm_r", "cl100k")


class TestNamedTokenizer:
    @pytest.mark.parametrize(
        ("tokenizer", "text", "expected"),
        [
            ("qwen3", "退款流程退款流程退款流程", 9),  # 12 / 1.38
            ("xlm_r", "退款流程退款流程退款流程", 9),  # 12 / 1.39
            ("cl100k", "退款流程退款流程退款流程", 17),  # 12 / 0.706
            ("qwen3", "かなカナかなカナ", 6),  # 8 / 1.42
            ("xlm_r", "한국어한국어", 5),  # 6 / 1.49
            ("cl100k", "привет", 4),  # 6 / 1.99
            ("xlm_r", "retrieval", 3),  # 9 / 3.53
            ("qwen3", "नमस्ते", 7),  # 6 code points / 0.86
        ],
        ids=[
            "han-qwen3",
            "han-xlm-r",
            "han-cl100k",
            "kana",
            "hangul",
            "cyrillic",
            "latin",
            "unlisted-script",
        ],
    )
    def test_estimate_follows_the_named_tokenizer(
        self, tokenizer: rag.TokenizerFamily, text: str, expected: int
    ) -> None:
        result = rag.token_estimator(tokenizer)(text)

        assert result == expected

    def test_scripts_in_one_text_are_counted_separately(self) -> None:
        measure = rag.token_estimator("xlm_r")

        result = measure("退款流程 refund")  # 4 / 1.39 + 6 / 3.53 = 4.58

        assert result == 5

    def test_cjk_punctuation_and_full_width_forms_count_as_han(self) -> None:
        measure = rag.token_estimator("qwen3")

        result = measure("。！？ＡＢ")  # 5 / 1.38

        assert result == 4


class TestCautiousDefault:
    @pytest.mark.parametrize(
        "text",
        [
            "退款流程需要三個工作天。",
            "かなとカナと漢字",
            "한국어 문장입니다",
            "Refund policy.",
            "Привет",
        ],
        ids=["chinese", "japanese", "korean", "latin", "cyrillic"],
    )
    def test_default_is_never_below_any_named_tokenizer(self, text: str) -> None:
        named = max(rag.token_estimator(family)(text) for family in FAMILIES)

        assert rag.token_estimator()(text) >= named

    def test_estimate_tokens_is_the_cautious_estimator(self) -> None:
        text = "退款 refund 환불 возврат"

        assert rag.estimate_tokens(text) == rag.token_estimator(None)(text)


class TestNamedLanguage:
    @pytest.mark.parametrize(
        ("tokenizer", "language", "text", "expected"),
        [
            ("qwen3", "en", "a" * 8891, 1996),
            ("xlm_r", "en", "a" * 8891, 2205),
            ("cl100k", "en", "a" * 8891, 1975),
            ("qwen3", "ru", "я" * 10204, 3500),
            ("xlm_r", "ko", "한" * 3531, 2376),
            ("cl100k", "zh-Hans", "款" * 2892, 3414),
            ("qwen3", "ja", "漢" * 2000 + "か" * 2091, 2875),
        ],
        ids=["en-qwen3", "en-xlm-r", "en-cl100k", "ru", "ko", "zh-Hans", "ja-both-scripts"],
    )
    def test_the_measured_document_s_size_is_reproduced(
        self, tokenizer: rag.TokenizerFamily, language: rag.Language, text: str, expected: int
    ) -> None:
        """As many characters as the measured document has give its text's tokens."""
        result = rag.token_estimator(tokenizer, language=language)(text)

        assert result == expected

    @pytest.mark.parametrize("tokenizer", FAMILIES)
    def test_english_is_no_longer_counted_as_the_densest_latin_language(
        self, tokenizer: rag.TokenizerFamily
    ) -> None:
        text = "Refunds are issued within three working days." * 20

        named = rag.token_estimator(tokenizer, language="en")(text)

        assert named < rag.token_estimator(tokenizer)(text)

    def test_a_script_outside_the_language_keeps_the_tokenizer_s_figure(self) -> None:
        text = "退款流程退款流程退款流程"  # Han inside a document declared English

        result = rag.token_estimator("qwen3", language="en")(text)

        assert result == rag.token_estimator("qwen3")(text)

    @pytest.mark.parametrize("info", rag.languages(), ids=lambda info: info.code)
    def test_with_no_tokenizer_a_language_still_takes_the_highest_count(
        self, info: rag.LanguageInfo
    ) -> None:
        text = {"han": "款", "kana": "か", "hangul": "한", "latin": "a", "cyrillic": "я"}[
            info.scripts[0]
        ] * 500

        named = max(rag.token_estimator(family, language=info.code)(text) for family in FAMILIES)

        assert rag.token_estimator(language=info.code)(text) == named

    @pytest.mark.parametrize("info", rag.languages(), ids=lambda info: info.code)
    def test_naming_a_language_never_counts_more_than_leaving_it_out(
        self, info: rag.LanguageInfo
    ) -> None:
        text = "款かカ한aяz" * 100

        assert rag.token_estimator(language=info.code)(text) <= rag.estimate_tokens(text)


class TestBoundary:
    def test_an_empty_text_is_zero(self) -> None:
        result = rag.estimate_tokens("")

        assert result == 0

    def test_a_single_character_is_at_least_one_token(self) -> None:
        result = rag.token_estimator("xlm_r")("a")

        assert result == 1

    def test_an_exact_multiple_is_not_rounded_up(self) -> None:
        measure = rag.token_estimator("xlm_r")

        result = measure("a" * 9953)  # the densest Latin document: 9953 characters, 2820 tokens

        assert result == 2820

    def test_a_supplementary_plane_ideograph_counts_as_han(self) -> None:
        measure = rag.token_estimator("cl100k")

        result = measure("\U00020000" * 2)  # 2 / 0.706

        assert result == 3


SPENDING = ("qwen3", "cl100k")


class TestWhitespace:
    """Two tokenizers spend tokens on whitespace; the third folds it away."""

    @pytest.mark.parametrize("tokenizer", SPENDING)
    def test_a_single_space_between_words_is_free(self, tokenizer: rag.TokenizerFamily) -> None:
        measure = rag.token_estimator(tokenizer)

        assert measure("alpha beta") == measure("alphabeta")

    @pytest.mark.parametrize(
        ("whitespace", "expected"),
        [
            ("  ", 1),
            (" " * 80, 1),
            (" " * 81, 2),
            ("\n", 1),
            ("\n" * 12, 1),
            ("\n" * 13, 2),
            ("\r\n", 1),
            ("\r\n" * 4, 1),
            ("\r\n" * 5, 2),
            ("\t", 1),
            ("\t\t\t", 3),
            ("\r", 1),
            ("　", 1),
            (" ", 1),
            (" ", 3),
            (" ", 3),
        ],
        ids=[
            "two-spaces",
            "eighty-spaces",
            "eighty-one-spaces",
            "one-line-break",
            "twelve-line-breaks",
            "thirteen-line-breaks",
            "one-windows-line-ending",
            "four-windows-line-endings",
            "five-windows-line-endings",
            "one-tab",
            "three-tabs",
            "lone-carriage-return",
            "ideographic-space",
            "no-break-space",
            "em-space",
            "line-separator",
        ],
    )
    def test_a_run_costs_what_was_measured_for_its_kind(
        self, whitespace: str, expected: int
    ) -> None:
        measure = rag.token_estimator("cl100k")

        result = measure(f"a{whitespace}b") - measure("ab")

        assert result == expected

    def test_each_kind_in_a_mixed_run_is_counted_on_its_own(self) -> None:
        measure = rag.token_estimator("qwen3")

        result = measure("a\n    b") - measure("ab")  # the line break, then the indentation

        assert result == 2

    def test_a_space_beside_other_whitespace_is_not_the_free_one(self) -> None:
        measure = rag.token_estimator("qwen3")

        result = measure("a \nb") - measure("ab")

        assert result == 2

    def test_whitespace_at_the_edges_of_a_text_is_counted(self) -> None:
        measure = rag.token_estimator("cl100k")

        result = measure("\n\nab    ") - measure("ab")

        assert result == 2

    @pytest.mark.parametrize("text", ["   ", "\n\t 　"], ids=["spaces", "mixed"])
    def test_text_of_whitespace_alone_is_not_free(self, text: str) -> None:
        assert rag.estimate_tokens(text) > 0

    def test_the_tokenizer_that_folds_whitespace_away_is_not_charged_for_it(self) -> None:
        measure = rag.token_estimator("xlm_r")

        assert measure("a\n\n\n        b\t\tc") == measure("abc")

    def test_the_cautious_estimate_charges_for_whitespace(self) -> None:
        text = "value = 1\n" + " " * 59

        assert rag.estimate_tokens(text) == rag.estimate_tokens("value=1") + 2

    def test_naming_a_language_does_not_change_what_whitespace_costs(self) -> None:
        plain = rag.token_estimator("qwen3")
        named = rag.token_estimator("qwen3", language="en")

        assert named("\n" * 100) == plain("\n" * 100)

    def test_a_text_that_is_mostly_whitespace_is_no_longer_estimated_near_zero(self) -> None:
        indented = ("\n" + " " * 59 + "x") * 1000  # 61,000 characters, 1,000 visible

        assert rag.estimate_tokens(indented) >= 2000

    def test_a_very_long_run_is_counted_by_its_length(self) -> None:
        measure = rag.token_estimator("cl100k")

        result = measure("a" + " " * 1_000_000 + "b") - measure("ab")

        assert result == 12_500


class TestErrors:
    def test_an_unknown_tokenizer_is_refused_with_the_choices(self) -> None:
        with pytest.raises(ValueError, match=r"choose one of: cl100k, qwen3, xlm_r"):
            rag.token_estimator("bert")  # type: ignore[arg-type]

    def test_an_uncalibrated_language_is_refused_with_the_choices(self) -> None:
        with pytest.raises(ValueError, match=r"language 'th'; choose one of: de, en, es"):
            rag.token_estimator("qwen3", language="th")  # type: ignore[arg-type]

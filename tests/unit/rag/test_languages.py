"""`rag.preset` and `rag.languages` — sizes that travel with their ruler."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from convilyn import rag

FAMILIES = ("qwen3", "xlm_r", "cl100k")
CODES = ("zh-Hant", "zh-Hans", "en", "ja", "ko", "es", "fr", "de", "pt", "ru", "id")

#: Long enough to yield a dozen passages at the measured sizes.
_TRADITIONAL_CHINESE = "\n\n".join(
    f"第{n}節：退款流程需要三個工作天，請保留收據以便查核；逾期申請將不予受理。" * 6
    for n in range(40)
)


def _mean_length(passages: tuple[rag.Passage, ...]) -> float:
    return sum(len(p.text) for p in passages) / len(passages)


class TestMeasuredSetting:
    def test_traditional_chinese_reproduces_the_measured_configuration(self) -> None:
        recipe = rag.preset("zh-Hant")

        result = rag.split(_TRADITIONAL_CHINESE, source_id="s", options=recipe.split)

        assert result == rag.split(
            _TRADITIONAL_CHINESE,
            source_id="s",
            options=rag.SplitOptions(target=384, max=512, min=128),
            measure=rag.token_estimator("qwen3"),
        )

    def test_traditional_chinese_keeps_the_measured_sizes(self) -> None:
        split = rag.preset("zh-Hant").split

        assert (split.target, split.max, split.min, split.tokenizer) == (384, 512, 128, "qwen3")

    def test_the_preset_yields_longer_passages_than_the_cautious_default(self) -> None:
        """The same three numbers under the cautious estimate hold far less text."""
        preset = rag.split(_TRADITIONAL_CHINESE, source_id="s", options=rag.preset("zh-Hant").split)
        default = rag.split(_TRADITIONAL_CHINESE, source_id="s")

        assert _mean_length(preset) >= 1.6 * _mean_length(default)


class TestEqualContent:
    @pytest.mark.parametrize(
        ("language", "tokenizer", "expected"),
        [
            ("zh-Hant", "xlm_r", (382, 509, 127)),
            ("zh-Hant", "cl100k", (752, 1003, 251)),
            ("en", None, (393, 524, 131)),
            ("en", "cl100k", (389, 518, 130)),
            ("ja", "qwen3", (566, 754, 189)),
            ("ru", "cl100k", (1011, 1349, 337)),
        ],
        ids=["zh-xlm-r", "zh-cl100k", "en-unnamed", "en-cl100k", "ja-qwen3", "ru-cl100k"],
    )
    def test_sizes_are_scaled_by_the_tokens_the_same_content_takes(
        self,
        language: rag.Language,
        tokenizer: rag.TokenizerFamily | None,
        expected: tuple[int, int, int],
    ) -> None:
        split = rag.preset(language, tokenizer=tokenizer).split

        assert (split.target, split.max, split.min) == expected

    @pytest.mark.parametrize("tokenizer", FAMILIES)
    def test_traditional_chinese_holds_the_same_text_under_every_tokenizer(
        self, tokenizer: rag.TokenizerFamily
    ) -> None:
        """Another tokenizer changes the numbers, not how much text a passage holds."""
        measured = rag.split(
            _TRADITIONAL_CHINESE, source_id="s", options=rag.preset("zh-Hant").split
        )

        result = rag.split(
            _TRADITIONAL_CHINESE,
            source_id="s",
            options=rag.preset("zh-Hant", tokenizer=tokenizer).split,
        )

        assert _mean_length(result) == pytest.approx(_mean_length(measured), rel=0.05)

    def test_with_no_tokenizer_named_sizes_are_in_the_measured_tokenizer(self) -> None:
        assert rag.preset("fr") == rag.preset("fr", tokenizer="qwen3")


class TestEveryPreset:
    @pytest.mark.parametrize("tokenizer", [None, *FAMILIES], ids=str)
    @pytest.mark.parametrize("language", CODES)
    def test_recipe_names_its_language(
        self, language: rag.Language, tokenizer: rag.TokenizerFamily | None
    ) -> None:
        recipe = rag.preset(language, tokenizer=tokenizer)

        assert recipe.split.language == language

    @pytest.mark.parametrize("tokenizer", [None, *FAMILIES], ids=str)
    @pytest.mark.parametrize("language", CODES)
    def test_recipe_survives_a_json_round_trip(
        self, language: rag.Language, tokenizer: rag.TokenizerFamily | None
    ) -> None:
        recipe = rag.preset(language, tokenizer=tokenizer)

        assert rag.Recipe.model_validate_json(recipe.model_dump_json()) == recipe


class TestLanguages:
    def test_the_listed_languages(self) -> None:
        codes = tuple(info.code for info in rag.languages())

        assert codes == CODES

    def test_only_the_measured_language_is_graded_measured(self) -> None:
        graded = {info.code: info.sizes for info in rag.languages()}

        assert graded == {code: "measured" if code == "zh-Hant" else "derived" for code in CODES}

    @pytest.mark.parametrize(
        ("code", "scripts"),
        [("ja", ("han", "kana")), ("ko", ("hangul",)), ("ru", ("cyrillic",)), ("id", ("latin",))],
    )
    def test_a_language_reports_the_scripts_it_is_calibrated_on(
        self, code: str, scripts: tuple[str, ...]
    ) -> None:
        by_code = {info.code: info for info in rag.languages()}

        assert by_code[code].scripts == scripts

    def test_a_language_row_is_frozen(self) -> None:
        info = rag.languages()[0]

        with pytest.raises(ValidationError):
            info.name = "other"  # type: ignore[misc]


class TestErrors:
    def test_a_language_without_a_preset_is_refused_with_the_way_forward(self) -> None:
        with pytest.raises(ValueError, match=r"no preset for language 'th'.*your own measure"):
            rag.preset("th")  # type: ignore[arg-type]

    def test_an_unknown_tokenizer_is_refused_with_the_choices(self) -> None:
        with pytest.raises(ValueError, match=r"choose one of: cl100k, qwen3, xlm_r"):
            rag.preset("en", tokenizer="bert")  # type: ignore[arg-type]

    @pytest.mark.parametrize("tokenizer", ["", 0, False], ids=["empty", "zero", "false"])
    def test_a_falsy_tokenizer_is_refused_rather_than_read_as_unset(
        self, tokenizer: object
    ) -> None:
        with pytest.raises(ValueError, match=r"unknown tokenizer"):
            rag.preset("en", tokenizer=tokenizer)  # type: ignore[arg-type]

    def test_a_very_long_language_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            rag.preset("x" * 100_000)  # type: ignore[arg-type]

        assert len(str(caught.value)) < 300

    def test_a_very_long_tokenizer_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            rag.preset("en", tokenizer="x" * 100_000)  # type: ignore[arg-type]

        assert len(str(caught.value)) < 300

    def test_a_very_long_estimator_language_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            rag.token_estimator(language="x" * 100_000)  # type: ignore[arg-type]

        assert len(str(caught.value)) < 300

    def test_options_refuse_an_uncalibrated_language(self) -> None:
        with pytest.raises(ValidationError):
            rag.SplitOptions(language="th")  # type: ignore[arg-type]

    def test_options_refuse_an_unknown_tokenizer(self) -> None:
        with pytest.raises(ValidationError):
            rag.SplitOptions(tokenizer="bert")  # type: ignore[arg-type]

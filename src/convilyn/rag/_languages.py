"""Passage sizes per language, with the ruler they are read in.

The default passage sizes were settled in retrieval experiments on Traditional
Chinese, counted with one tokenizer. A size is an amount of content, so it does
not carry over as a number: the same three numbers mean less text under a
tokenizer that spends more tokens per character, and a different amount of text
in a language that spends more tokens on the same content.

A preset keeps the amount of content fixed instead. For any other language or
tokenizer the measured sizes are multiplied by how many tokens that language
takes under that tokenizer relative to the measured setting, on the parallel
document in ``_measure``. That makes them *derived*: equal content is a sound
starting point, and nobody has yet measured that it is the best size for
retrieval in that language.
"""

from __future__ import annotations

from typing import Final

from convilyn.rag._measure import (
    MEASURED,
    Language,
    TokenizerFamily,
    shown,
    text_tokens,
    token_estimator,
)
from convilyn.rag.recipe import Recipe, SplitOptions
from convilyn.rag.types import LanguageInfo

#: The setting the default sizes were measured in.
_MEASURED_LANGUAGE: Final[Language] = "zh-Hant"
_MEASURED_TOKENIZER: Final[TokenizerFamily] = "qwen3"

_NAMES: dict[Language, str] = {
    "zh-Hant": "Chinese (Traditional)",
    "zh-Hans": "Chinese (Simplified)",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "id": "Indonesian",
}


def languages() -> tuple[LanguageInfo, ...]:
    """List the languages :func:`preset` has sizes for."""
    return tuple(
        LanguageInfo(
            code=code,
            name=_NAMES[code],
            scripts=scripts,
            sizes="measured" if code == _MEASURED_LANGUAGE else "derived",
        )
        for code, (scripts, _, _) in MEASURED.items()
    )


def preset(language: Language, *, tokenizer: TokenizerFamily | None = None) -> Recipe:
    """Return the recipe for ``language``, sized in ``tokenizer``'s tokens.

    The sizes hold the same amount of content as the measured Traditional
    Chinese setting, and the recipe names the estimate they are read with, so
    ``rag.split(markdown, options=recipe.split)`` needs nothing else. With no
    tokenizer named, sizes are in the tokenizer of the measured setting; name
    the one your embedding model uses to read them in its tokens.

    A language that is not listed has no preset. It can still be split: choose
    the sizes yourself and pass your own ``measure``.
    """
    if language not in MEASURED:
        known = ", ".join(sorted(MEASURED))
        raise ValueError(
            f"no preset for language {shown(language)}; choose one of: {known}, "
            f"or build SplitOptions yourself and pass your own measure"
        )
    family = _MEASURED_TOKENIZER if tokenizer is None else tokenizer
    token_estimator(family)  # refuses an unknown tokenizer with the choices

    # The text alone, without the measured document's layout: the estimate a
    # size is read with counts whitespace separately, the same way.
    scale = text_tokens(language, family) / text_tokens(_MEASURED_LANGUAGE, _MEASURED_TOKENIZER)
    measured = SplitOptions()
    return Recipe(
        split=SplitOptions(
            target=round(measured.target * scale),
            max=round(measured.max * scale),
            min=round(measured.min * scale),
            tokenizer=family,
            language=language,
        )
    )

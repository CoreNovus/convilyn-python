"""Estimate how many model tokens a text takes, without loading a tokenizer.

How many tokens a text costs depends far more on the tokenizer than on the
language: the same document in Hindi took 3.7 times as many tokens under one
widely used tokenizer as under another. So an estimate has to know which
tokenizer it is standing in for, and when it does not, it has to err high — a
passage estimated too small is sent to a model that then rejects or truncates
it.

The figures below are characters per token, by writing system, measured on one
document available in every language (the Universal Declaration of Human
Rights) with each tokenizer's published vocabulary.

Whitespace is counted apart from the text. Two of the three tokenizers spend
tokens on it — one for the indentation of a line, one for a line break, more
for long runs — so text that is mostly whitespace (deeply indented code, blank
lines) costs far more than its visible characters suggest, and counting it as
free let such a passage grow many times past what a model accepts. The text's
own figures are therefore measured on the document with its layout taken out,
and whitespace is added by the run, at figures chosen so that no measured run
was counted short.

How close the estimate is, measured against the real tokenizers on that
document as published: the cautious estimate gave between 1.00 and 1.23 times
the largest true count for Chinese, Japanese, Korean, Spanish, French, German,
Portuguese, Russian and Indonesian. English is over-counted by more than half,
because one figure serves every Latin-script language and that figure is the
one of the language that takes the most tokens; an over-count yields smaller
passages, never oversize ones. Naming the language removes that: the estimate
then uses the figure measured for that language, and with the tokenizer named
as well it gave between 0.97 and 1.09 times the true count. Languages outside
the list are not calibrated — Vietnamese was under-counted by a quarter under
one tokenizer — so pass your own ``measure`` for them, and whenever the exact
count matters. One document is a narrow sample: source code was under-counted
by a quarter under the tokenizer that does not spend tokens on whitespace.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from reprlib import repr as shown  # an echoed argument is cut to a bounded length
from typing import Literal, get_args

#: Returns the size of a piece of text, in the unit ``SplitOptions`` is read in.
Measure = Callable[[str], int]

#: The tokenizer an estimate stands in for.
#:
#: * ``"qwen3"`` — the Qwen3 embedding models
#: * ``"xlm_r"`` — XLM-RoBERTa vocabularies: bge-m3, multilingual-e5, and the
#:   multilingual embedding models that publish the same tokenizer
#: * ``"cl100k"`` — the ``text-embedding-3`` models
TokenizerFamily = Literal["qwen3", "xlm_r", "cl100k"]

#: A language the estimate is calibrated for, as a BCP 47 tag.
Language = Literal["zh-Hant", "zh-Hans", "en", "ja", "ko", "es", "fr", "de", "pt", "ru", "id"]

_Script = Literal["han", "kana", "hangul", "latin", "cyrillic", "other"]

_SCRIPTS: tuple[_Script, ...] = get_args(_Script)

#: The tokenizers that spend tokens on whitespace. The third folds any run of
#: it into the word that follows, at no cost that was measurable.
_SPENDS_ON_WHITESPACE: frozenset[TokenizerFamily] = frozenset({"qwen3", "cl100k"})

#: What whitespace costs those tokenizers, measured on runs of 1 to 6,000
#: characters between words, between punctuation and at the edges of a text.
#: Each figure is the most characters one token may be assumed to hold without
#: any measured run being counted short. The two tokenizers agreed on every run.
#:
#: A single space between two words costs nothing: it is part of the word.
_SPACES_PER_TOKEN = 80
_NEWLINES_PER_TOKEN = 12
_LINE_ENDINGS_PER_TOKEN = 4  # carriage return + line feed, as a pair
#: Every other whitespace character took a token or more of its own. These
#: took one; the rest (the typographic spaces, the line and paragraph
#: separators) took up to three.
_ONE_TOKEN_WHITESPACE = frozenset("\t\r\v\f\x1c\x1d\x1e\x1f\xa0" + chr(0x3000))
_RARE_WHITESPACE_TOKENS = 3

#: A space alone between two characters that are not whitespace, which is not
#: counted, or else one stretch of whitespace of a single kind.
_WHITESPACE = re.compile(r"(?<!\s) (?!\s)|( +)|(\n+)|((?:\r\n)+)|(\s)")

#: The parallel document as measured: per language, the writing systems it is
#: mainly written in, its characters (whitespace excluded), and its tokens under
#: each tokenizer.
MEASURED: dict[Language, tuple[tuple[_Script, ...], int, dict[TokenizerFamily, int]]] = {
    "zh-Hant": (("han",), 2700, {"qwen3": 2218, "xlm_r": 1939, "cl100k": 4059}),
    "zh-Hans": (("han",), 2892, {"qwen3": 2130, "xlm_r": 1963, "cl100k": 3704}),
    "en": (("latin",), 8891, {"qwen3": 2228, "xlm_r": 2205, "cl100k": 2207}),
    "ja": (("han", "kana"), 4091, {"qwen3": 3180, "xlm_r": 2441, "cl100k": 5077}),
    "ko": (("hangul",), 3531, {"qwen3": 3078, "xlm_r": 2376, "cl100k": 4842}),
    "es": (("latin",), 10038, {"qwen3": 3131, "xlm_r": 2502, "cl100k": 3179}),
    "fr": (("latin",), 9953, {"qwen3": 3305, "xlm_r": 2820, "cl100k": 3317}),
    "de": (("latin",), 10295, {"qwen3": 3454, "xlm_r": 2462, "cl100k": 3462}),
    "pt": (("latin",), 9484, {"qwen3": 3152, "xlm_r": 2412, "cl100k": 3187}),
    "ru": (("cyrillic",), 10204, {"qwen3": 3738, "xlm_r": 2541, "cl100k": 5377}),
    "id": (("latin",), 10865, {"qwen3": 4006, "xlm_r": 2325, "cl100k": 3986}),
}

#: The same document with its layout taken out — every run of whitespace
#: reduced to the one space that separates two words, or to nothing in Chinese
#: and Japanese — in tokens, under the tokenizers that spend tokens on
#: whitespace. This is what the text itself costs; the difference from the
#: figures above is what the document's line breaks and indentation cost.
_TEXT_TOKENS: dict[Language, dict[TokenizerFamily, int]] = {
    "zh-Hant": {"qwen3": 1951, "cl100k": 3822},
    "zh-Hans": {"qwen3": 1783, "cl100k": 3414},
    "en": {"qwen3": 1996, "cl100k": 1975},
    "ja": {"qwen3": 2875, "cl100k": 4786},
    "ko": {"qwen3": 2847, "cl100k": 4611},
    "es": {"qwen3": 2897, "cl100k": 2945},
    "fr": {"qwen3": 3074, "cl100k": 3086},
    "de": {"qwen3": 3221, "cl100k": 3229},
    "pt": {"qwen3": 2963, "cl100k": 2998},
    "ru": {"qwen3": 3500, "cl100k": 5139},
    "id": {"qwen3": 3773, "cl100k": 3753},
}

#: Characters per token for a script no listed language is written in: the
#: densest of the other scripts measured on the same document.
_UNLISTED_SCRIPT: dict[TokenizerFamily, float] = {"qwen3": 0.86, "xlm_r": 2.86, "cl100k": 0.81}


def text_tokens(language: Language, family: TokenizerFamily) -> int:
    """Tokens the text of the measured document takes, its layout set aside.

    Where whitespace is counted on its own, the text is weighed without it:
    otherwise every line break of the measured document would be paid for
    twice, once in this figure and once as whitespace.
    """
    return _TEXT_TOKENS[language].get(family, MEASURED[language][2][family])


def _density(language: Language, family: TokenizerFamily) -> float:
    """Characters per token of ``language`` under ``family``."""
    return MEASURED[language][1] / text_tokens(language, family)


def _densest(family: TokenizerFamily, script: _Script) -> float:
    """Characters per token of the densest listed language written in ``script``."""
    quotients = [
        _density(language, family)
        for language, (scripts, _, _) in MEASURED.items()
        if script in scripts
    ]
    return min(quotients, default=_UNLISTED_SCRIPT[family])


#: Characters per token, by tokenizer and script, when no language is named.
_CHARS_PER_TOKEN: dict[TokenizerFamily, dict[_Script, float]] = {
    family: {script: _densest(family, script) for script in _SCRIPTS}
    for family in get_args(TokenizerFamily)
}

#: Inclusive code point ranges, checked in order. CJK punctuation and full-width
#: forms are counted with Han: each is a token of its own in every vocabulary
#: measured.
_RANGES: tuple[tuple[int, int, _Script], ...] = (
    (0x0000, 0x024F, "latin"),
    (0x0400, 0x052F, "cyrillic"),
    (0x1100, 0x11FF, "hangul"),
    (0x3000, 0x303F, "han"),
    (0x3040, 0x30FF, "kana"),
    (0x3130, 0x318F, "hangul"),
    (0x31F0, 0x31FF, "kana"),
    (0x3400, 0x4DBF, "han"),
    (0x4E00, 0x9FFF, "han"),
    (0xAC00, 0xD7AF, "hangul"),
    (0xF900, 0xFAFF, "han"),
    (0xFF00, 0xFF65, "han"),
    (0xFF66, 0xFF9F, "kana"),
    (0x20000, 0x323AF, "han"),
)


def token_estimator(
    tokenizer: TokenizerFamily | None = None, *, language: Language | None = None
) -> Measure:
    """Return a length measure that estimates tokens for ``tokenizer``.

    With no tokenizer named, the measure takes the highest count any measured
    tokenizer would give, so that a passage sized with it also fits a model
    chosen later. With a ``language`` named, the writing systems that language
    is mainly written in are counted at the figure measured for it rather than
    at the densest language sharing the script. See the module notes for the
    languages this was checked on.
    """
    if tokenizer is not None and tokenizer not in _CHARS_PER_TOKEN:
        known = ", ".join(sorted(_CHARS_PER_TOKEN))
        raise ValueError(f"unknown tokenizer {shown(tokenizer)}; choose one of: {known}")
    if language is not None and language not in MEASURED:
        known = ", ".join(sorted(MEASURED))
        raise ValueError(
            f"no estimate is calibrated for language {shown(language)}; choose one of: {known}, "
            f"or leave it unset and pass your own measure"
        )
    families: tuple[TokenizerFamily, ...] = (
        tuple(_CHARS_PER_TOKEN) if tokenizer is None else (tokenizer,)
    )
    table = {
        script: min(_figure(family, script, language) for family in families) for script in _SCRIPTS
    }
    # Counted as soon as one tokenizer the measure stands in for spends tokens
    # on whitespace.
    spaced = any(family in _SPENDS_ON_WHITESPACE for family in families)

    def measure(text: str) -> int:
        counts = dict.fromkeys(_SCRIPTS, 0)
        for ch in text:
            if not ch.isspace():
                counts[_script_of(ch)] += 1
        tokens = sum(count / table[script] for script, count in counts.items())
        whole = int(tokens) + (tokens > int(tokens))
        return whole + _whitespace_tokens(text) if spaced else whole

    return measure


def _whitespace_tokens(text: str) -> int:
    """Tokens the whitespace of ``text`` takes, under a tokenizer that spends them."""
    tokens = 0
    for match in _WHITESPACE.finditer(text):
        spaces, newlines, pairs, other = match.groups()
        if spaces:
            tokens += -(-len(spaces) // _SPACES_PER_TOKEN)
        elif newlines:
            tokens += -(-len(newlines) // _NEWLINES_PER_TOKEN)
        elif pairs:
            tokens += -(-len(pairs) // (len("\r\n") * _LINE_ENDINGS_PER_TOKEN))
        elif other:
            tokens += 1 if other in _ONE_TOKEN_WHITESPACE else _RARE_WHITESPACE_TOKENS
    return tokens


def _figure(family: TokenizerFamily, script: _Script, language: Language | None) -> float:
    """Characters per token for ``script`` under ``family``, for ``language`` if it is named."""
    if language is not None and script in MEASURED[language][0]:
        return _density(language, family)
    return _CHARS_PER_TOKEN[family][script]


def _script_of(ch: str) -> _Script:
    point = ord(ch)
    for low, high, script in _RANGES:
        if low <= point <= high:
            return script
    return "other"


#: The measure used when none is passed: the cautious estimate.
estimate_tokens: Measure = token_estimator()

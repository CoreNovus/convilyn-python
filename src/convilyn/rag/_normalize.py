"""Text folding shared by indexing, searching and evaluation.

One function on purpose. Text is matched correctly only when both sides were
folded the same way, so there is exactly one place that does it and a stamp that
names how it was done.
"""

from __future__ import annotations

import unicodedata

from convilyn.rag.recipe import NormalizeOptions

#: Control characters that carry layout and are therefore kept.
_KEPT_CONTROLS = frozenset("\n\t")

#: Unicode categories removed by ``strip_invisible``: control and format.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf"})

#: Stamp fragment for each fold, in the order the folds are applied.
_STAMP_PARTS = (
    ("fold_width", "nfkc"),
    ("fold_case", "lower"),
    ("strip_invisible", "visible"),
)


def normalize(text: str, options: NormalizeOptions | None = None) -> str:
    """Fold ``text`` for matching.

    Apply this to both the text you index and the query you search with, using
    the same options.
    """
    chosen = options or NormalizeOptions()
    folded = text
    if chosen.fold_width:
        folded = unicodedata.normalize("NFKC", folded)
    if chosen.fold_case:
        folded = folded.lower()
    if chosen.strip_invisible:
        folded = "".join(
            ch
            for ch in folded
            if ch in _KEPT_CONTROLS or unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
        )
    return folded


def normalizer_stamp(options: NormalizeOptions | None = None) -> str:
    """Name the folds ``options`` applies.

    Two option sets that fold text identically share a stamp; a store keeps the
    stamp it was built with and compares it on every open.
    """
    chosen = options or NormalizeOptions()
    parts = [label for field, label in _STAMP_PARTS if getattr(chosen, field)]
    return "+".join(parts) or "none"

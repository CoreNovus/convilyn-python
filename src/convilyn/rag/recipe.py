"""The tunable parameters of a retrieval pipeline, as plain data.

A :class:`Recipe` holds numbers and switches and nothing else, so the same
values can be written to a file, compared between two experiments, and loaded in
production unchanged. Anything that is a *function* — a length measure, a
replacement splitter — is passed as an argument to the stage that uses it, never
stored here.

Every stage takes its own options object, so a stage is usable on its own::

    from convilyn import rag

    rag.split(markdown, source_id="handbook", options=rag.SplitOptions(target=256))
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convilyn.rag._measure import Language, TokenizerFamily


class SplitOptions(BaseModel):
    """How Markdown is cut into passages.

    Sizes are in whatever unit the length measure returns. The built-in measure
    estimates model tokens, and ``tokenizer`` and ``language`` say which
    estimate: a size is only meaningful together with the ruler it was chosen
    with, so the two are kept in the same object. Pass ``measure=`` to
    :func:`convilyn.rag.split` to count with your own tokenizer instead, and
    the three sizes follow that unit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A passage is closed once it reaches this size.
    target: int = Field(default=384, gt=0)
    #: No passage exceeds this size. The one exception is a limit smaller than
    #: a single character measures, which no cut can honour.
    max: int = Field(default=512, gt=0)
    #: A trailing piece smaller than this joins the passage before it, when the
    #: result still fits within ``max``.
    min: int = Field(default=128, ge=0)
    #: Headings down to this level open a new section, and a passage never
    #: crosses a section. Deeper headings stay in the text.
    section_depth: int = Field(default=3, ge=0, le=6)
    #: The tokenizer the built-in estimate stands in for. Unset, the estimate
    #: assumes the least favourable one.
    tokenizer: TokenizerFamily | None = None
    #: The language the built-in estimate is calibrated to. Unset, each script
    #: is counted as its densest measured language.
    language: Language | None = None

    @model_validator(mode="after")
    def _sizes_are_ordered(self) -> SplitOptions:
        if not self.min <= self.target <= self.max:
            # The sizes are not repeated: this message is also how a settings
            # file's error reads, and those never quote the file's values.
            raise ValueError("sizes must satisfy min <= target <= max")
        return self


class NormalizeOptions(BaseModel):
    """Which folds are applied to text before it is matched.

    The same options must be used when indexing and when searching; a store
    records :func:`convilyn.rag.normalizer_stamp` so a mismatch is reported
    instead of silently returning worse results.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Unicode NFKC: full-width and half-width forms become one.
    fold_width: bool = True
    #: Lowercase.
    fold_case: bool = True
    #: Remove zero-width and control characters, keeping newline and tab.
    strip_invisible: bool = True


class SearchOptions(BaseModel):
    """How a search is answered. Changing these never requires rebuilding an index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: How many passages a search returns when the call does not say.
    limit: int = Field(default=10, gt=0)
    #: With an embedder or a reranker: how many passages each kind of search
    #: contributes before they are fused, and how many the reranker reorders.
    candidates: int = Field(default=100, gt=0)


class FuseOptions(BaseModel):
    """How rankings are combined into one. See :func:`convilyn.rag.fuse`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``"min_max"`` scales each ranking's scores to between 0 and 1 and
    #: averages them; ``"rrf"`` (reciprocal rank fusion) reads ranks only.
    method: Literal["min_max", "rrf"] = "min_max"
    #: One weight per ranking, in the order the rankings are passed. Unset,
    #: every ranking counts the same. Only their proportions matter.
    weights: tuple[float, ...] | None = None
    #: How much rank matters under ``"rrf"``: a larger number flattens the
    #: difference between the first places and the later ones.
    rrf_k: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _weights_are_usable(self) -> FuseOptions:
        if self.weights is not None and (
            not self.weights
            or not all(math.isfinite(weight) and weight >= 0 for weight in self.weights)
            or not any(weight > 0 for weight in self.weights)
        ):
            raise ValueError(
                "weights must be finite numbers no smaller than 0, at least one above 0"
            )
        return self


class Recipe(BaseModel):
    """Every tunable parameter of one pipeline, grouped by stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: SplitOptions = Field(default_factory=SplitOptions)
    normalize: NormalizeOptions = Field(default_factory=NormalizeOptions)
    search: SearchOptions = Field(default_factory=SearchOptions)
    fuse: FuseOptions = Field(default_factory=FuseOptions)


__all__ = ["FuseOptions", "NormalizeOptions", "Recipe", "SearchOptions", "SplitOptions"]

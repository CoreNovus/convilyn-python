"""The values the retrieval toolkit hands back.

Frozen, like the rest of this SDK's public models, and without wire aliases:
nothing here is serialized to or from the Convilyn API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from convilyn._internal.vectors import unit
from convilyn.rag._measure import Language

#: A metadata value: what a source can be labelled with and filtered by.
Scalar = str | int | float | bool


class Passage(BaseModel):
    """One retrievable piece of a source document."""

    model_config = ConfigDict(frozen=True)

    #: Derived from the source, the position and the text, so splitting the same
    #: document again yields the same identifiers.
    passage_id: str
    #: The caller's identifier for the document this came from.
    source_id: str
    #: Position within the source, counted from zero.
    ordinal: int
    #: Titles of the headings this passage sits under, outermost first.
    section: tuple[str, ...]
    #: The Markdown of the passage, as written in the source.
    text: str
    #: SHA-256 of ``text``. Unchanged text keeps its digest, which is what lets
    #: an index skip work it has already done.
    digest: str


class LanguageInfo(BaseModel):
    """One language :func:`convilyn.rag.preset` has sizes for."""

    model_config = ConfigDict(frozen=True)

    #: The BCP 47 tag to pass as ``language``.
    code: Language
    #: The language's name in English.
    name: str
    #: The writing systems the token estimate is calibrated on for it.
    scripts: tuple[str, ...]
    #: Where the preset's passage sizes come from: ``measured`` in retrieval
    #: experiments on this language, or ``derived`` by converting the measured
    #: sizes to the same amount of content in this one.
    sizes: Literal["measured", "derived"]


class SourceInfo(BaseModel):
    """One document held by a corpus."""

    model_config = ConfigDict(frozen=True)

    #: The caller's identifier for the document.
    source_id: str
    #: The document's title, when one was given.
    title: str | None = None
    #: Labels the caller attached, which ``where=`` filters on.
    meta: dict[str, Scalar] = {}
    #: How many passages the document was stored as.
    passages: int
    #: SHA-256 of what was added, tagged with how: the Markdown for
    #: ``add_markdown``, the passages' digests for ``add_passages``. For
    #: Markdown it does not depend on how the text was cut, so it says whether
    #: the *documents* changed: a bench pin compares it, and a recipe change
    #: alone leaves it the same. (Passages you cut yourself are the document,
    #: so cutting them again changes it.)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class Hit(BaseModel):
    """One passage a search returned."""

    model_config = ConfigDict(frozen=True)

    passage: Passage
    #: Higher is a better match. Comparable within one search only: the scale
    #: belongs to the store and the kind of search that produced it.
    score: float
    #: Title and labels of the document the passage came from.
    title: str | None = None
    meta: dict[str, Scalar] = {}


class SearchResult(BaseModel):
    """What a search returned, best match first."""

    model_config = ConfigDict(frozen=True)

    hits: tuple[Hit, ...]
    #: Steps of the search that failed and were left out, so the hits come
    #: from what remained: ``"embed"`` (the query could not be embedded, so
    #: only keyword search ran) and ``"rerank"`` (the reranker failed, so the
    #: fused order stands).
    skipped: tuple[Literal["embed", "rerank"], ...] = ()


class Condition(BaseModel):
    """One clause of a ``where=`` filter: the label ``key`` is one of ``values``."""

    model_config = ConfigDict(frozen=True)

    key: str
    values: tuple[Scalar, ...]


class IndexIdentity(BaseModel):
    """How an index was built.

    A store records this when it is first prepared and compares it every time
    after, because an index searched with settings other than the ones it was
    built with returns worse results without saying so.
    """

    model_config = ConfigDict(frozen=True)

    #: Version of the layout a store keeps on disk. A store refuses any other.
    format: int = 3
    #: :func:`convilyn.rag.normalizer_stamp` of the text fold in use.
    normalizer: str
    #: What made the vectors, such as ``"openai/text-embedding-3-small"``.
    #: ``None`` for an index that holds no vectors. Vectors from two models
    #: are not comparable, so an index built by one is refused under another.
    embedding: str | None = None
    #: How many numbers each vector has. Set exactly when ``embedding`` is.
    dimensions: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def _embedding_comes_with_its_size(self) -> IndexIdentity:
        if (self.embedding is None) != (self.dimensions is None):
            raise ValueError("embedding and dimensions are set together, or neither is")
        return self


class IndexEntry(BaseModel):
    """A passage as a store receives it: the passage, its folded text, its vector."""

    model_config = ConfigDict(frozen=True)

    passage: Passage
    #: ``passage.text`` after :func:`convilyn.rag.normalize`. Matching is done
    #: on this; the passage keeps the text as written.
    folded: str
    #: The passage's embedding when the index holds vectors; its size is the
    #: index's ``dimensions``. Scaled to length 1 when the entry is made,
    #: whatever length it was given with, so every store compares like with like.
    vector: tuple[float, ...] | None = None

    @field_validator("vector")
    @classmethod
    def _vector_has_a_direction(cls, vector: tuple[float, ...] | None) -> tuple[float, ...] | None:
        return None if vector is None else unit(vector)


class StoreInfo(BaseModel):
    """What a store is and how it matches text."""

    model_config = ConfigDict(frozen=True)

    #: The key it is created with: ``rag.store(kind, ...)``.
    kind: str
    #: How keyword search treats Chinese, Japanese and Korean, which are
    #: written without spaces between words.
    cjk: str


__all__ = [
    "Condition",
    "Hit",
    "IndexEntry",
    "IndexIdentity",
    "LanguageInfo",
    "Passage",
    "Scalar",
    "SearchResult",
    "SourceInfo",
    "StoreInfo",
]

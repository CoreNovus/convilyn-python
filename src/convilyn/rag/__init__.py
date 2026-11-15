"""Retrieval over your own documents — a toolkit, not a fixed pipeline.

Every stage is a function you can call on its own, and the pieces compose into
a full pipeline when you want one: turning Markdown into passages, the one text
fold that indexing, searching and evaluation share, keyword and vector search
over a store, and fusing and reranking what they find.

    from convilyn import rag

    with rag.open(store=rag.store("sqlite", path="kb.sqlite3")) as corpus:
        corpus.add_markdown(markdown, source_id="handbook", meta={"lang": "en"})
        for hit in corpus.search("refund policy", where={"lang": "en"}).hits:
            print(hit.score, hit.passage.section)

``rag.aopen()`` is the same thing for async code. Or take only the part you
need::

    passages = rag.split(markdown, source_id="handbook", title="Staff handbook")

**Hybrid search.** Pass ``embed=`` (and, if you like, ``rerank=``) to
``rag.open``: an embedder or reranker from :mod:`convilyn.providers`, or an
object of your own of the same shape (``rag.Embedder``, ``rag.Reranker``). A
search then fuses keyword and vector search, reranks, and still answers by
keyword if the query cannot be embedded, saying so in ``result.skipped``.

**One settings file.** ``rag.open("rag.toml")`` builds the corpus from
``[store]``, ``[embed]``, ``[rerank]`` and ``[recipe]`` tables, so an
experiment and production read the same file. The file never holds a key: the
connectors it describes read the environment variable their provider names.

Nothing here opens a network connection or reads a credential of its own; with
``embed=`` or ``rerank=``, the objects you passed are what reach a service, and
with a settings file, the connectors from :mod:`convilyn.providers` it names.
Keyword search needs nothing beyond Python; vector search in the SQLite store
needs numpy, from ``pip install "convilyn[rag]"``.

**Stores.** ``rag.store(kind, ...)`` creates one and ``rag.stores()`` lists the
kinds. A store only keeps passages and answers searches; cutting, folding and
ranking happen here, so the same recipe behaves the same on every store. To use
your own, implement ``rag.Store`` and pass the object: it ranks equal to the
built-in ones.

**Sizes are yours to define.** ``SplitOptions`` is read in whatever unit the
length measure returns, and there are three ways to supply one.

Say nothing, and the built-in estimate assumes the least favourable tokenizer,
so passages fit whichever embedding model you later choose. Name the tokenizer
your model uses, and the estimate follows it::

    rag.split(markdown, source_id="handbook", measure=rag.token_estimator("xlm_r"))

Or count exactly, with your own function::

    rag.split(markdown, source_id="handbook", measure=lambda text: len(encode(text)))

**A preset per language.** ``rag.preset()`` returns a ``Recipe`` whose sizes and
ruler belong together, so nothing has to be matched by hand::

    recipe = rag.preset("zh-Hant")
    rag.split(markdown, source_id="handbook", options=recipe.split)

``rag.languages()`` lists the languages that have one, and how well founded
each is.

The tokenizer matters more than the language: the same content can cost several
times as many tokens under one tokenizer as under another, so a size that is
right for one model is not right for the next.

**Languages.** Sentence boundaries are recognised for Chinese, Japanese, Korean
and languages written in Latin or Cyrillic script. Other languages can still be
split — headings, tables and lists do not depend on language — but an oversize
paragraph is cut by length rather than at sentence ends, and there is no preset:
choose the sizes and pass your own ``measure``.

## How this package is laid out

**A leading underscore means internal**, as everywhere in this SDK.

Public, and covered by the stability policy:

* this module — the only import a user needs
* ``types`` — frozen models
* ``recipe`` — the tunable parameters, as plain data
* ``errors`` — what is raised at run time

Internal, and free to change in any release:

* ``_markdown`` — finds block boundaries
* ``_measure`` — token estimates and the measurements behind them
* ``_languages`` — the per-language presets, as data
* ``_filter`` — the portable ``where=`` filter
* ``_stores`` — the store interface and the stores shipped
* ``_corpus`` — the stages composed over one store
* ``_split`` — the passage rules
* ``_rank`` — combining rankings and joining neighbouring passages
* ``_models`` — calling an embedder or a reranker, yours or a provider's
* ``_normalize`` — the shared text fold
"""

# No `from __future__ import annotations` here: it would bind a public
# attribute named `annotations` on a namespace whose export set is frozen.
from convilyn.rag._corpus import AsyncCorpus, Corpus, aopen, open  # noqa: A004
from convilyn.rag._languages import languages, preset
from convilyn.rag._measure import (
    Language,
    Measure,
    TokenizerFamily,
    estimate_tokens,
    token_estimator,
)
from convilyn.rag._models import Embedder, Reranker
from convilyn.rag._normalize import normalize, normalizer_stamp
from convilyn.rag._rank import fuse, merge_adjacent
from convilyn.rag._split import split
from convilyn.rag._stores import Store, store, stores
from convilyn.rag.errors import RagError, StoreError
from convilyn.rag.recipe import (
    FuseOptions,
    NormalizeOptions,
    Recipe,
    SearchOptions,
    SplitOptions,
)
from convilyn.rag.types import (
    Condition,
    Hit,
    IndexEntry,
    IndexIdentity,
    LanguageInfo,
    Passage,
    Scalar,
    SearchResult,
    SourceInfo,
    StoreInfo,
)

__all__ = [
    "AsyncCorpus",
    "Condition",
    "Corpus",
    "Embedder",
    "FuseOptions",
    "Hit",
    "IndexEntry",
    "IndexIdentity",
    "Language",
    "LanguageInfo",
    "Measure",
    "NormalizeOptions",
    "Passage",
    "RagError",
    "Recipe",
    "Reranker",
    "Scalar",
    "SearchOptions",
    "SearchResult",
    "SourceInfo",
    "SplitOptions",
    "Store",
    "StoreError",
    "StoreInfo",
    "TokenizerFamily",
    "aopen",
    "estimate_tokens",
    "fuse",
    "languages",
    "merge_adjacent",
    "normalize",
    "normalizer_stamp",
    "open",
    "preset",
    "split",
    "store",
    "stores",
    "token_estimator",
]

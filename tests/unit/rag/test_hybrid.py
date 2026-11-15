"""`rag.open(embed=..., rerank=...)` — keyword and vector search, fused and reranked.

The embedders here are tiny models of a real one: each word maps to an axis,
so a query can find a passage that shares none of its words. No test reaches a
network.
"""

from __future__ import annotations

import contextlib
import sys
import warnings
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from convilyn import rag

pytest.importorskip("numpy")

#: Words and the axis each one points along: "money" means what "refund" does.
AXES = {"refund": 0, "money": 0, "shipping": 1, "delivery": 1}

DOCUMENT = "# Refunds\n\nrefund policy\n\n# Shipping\n\nshipping times"


def vector_of(text: str) -> list[float]:
    vector = [0.0, 0.0, 0.1]
    for word in text.lower().split():
        if word in AXES:
            vector[AXES[word]] += 1.0
    return vector


class WordEmbedder:
    """A synchronous embedder of the caller's own, recording what it was asked."""

    model = "test/word-axes"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
        self.calls.append((kind, tuple(texts)))
        return [vector_of(text) for text in texts]


class AsyncWordEmbedder(WordEmbedder):
    """The same, async, answering in the shape the providers' embedders do."""

    class Result:
        def __init__(self, vectors: list[list[float]]) -> None:
            self.vectors = vectors

    async def embed(self, texts: Sequence[str], *, kind: str) -> AsyncWordEmbedder.Result:  # type: ignore[override]
        return self.Result(super().embed(texts, kind=kind))


class FailingOnQueries(WordEmbedder):
    def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
        if kind == "query":
            raise ConnectionError("the service is down")
        return super().embed(texts, kind=kind)


class Reversing:
    """A reranker that puts the given documents in reverse order."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        self.seen.append(len(documents))
        return [(index, float(index)) for index in reversed(range(len(documents)))]


def texts(result: rag.SearchResult) -> list[str]:
    return [hit.passage.text for hit in result.hits]


@pytest.fixture
def embedder() -> WordEmbedder:
    return WordEmbedder()


@pytest.fixture
def corpus(embedder: WordEmbedder) -> Iterator[rag.Corpus]:
    with rag.open(embed=embedder) as opened:
        opened.add_markdown(DOCUMENT, source_id="handbook")
        yield opened


class TestHybridSearch:
    def test_a_passage_only_vectors_find_is_returned(self, corpus: rag.Corpus) -> None:
        result = corpus.search("money back", limit=1)

        assert texts(result) == ["refund policy"]

    def test_a_keyword_match_is_still_found(self, corpus: rag.Corpus) -> None:
        result = corpus.search("shipping", limit=1)

        assert texts(result) == ["shipping times"]

    def test_nothing_was_skipped_when_every_step_ran(self, corpus: rag.Corpus) -> None:
        assert corpus.search("money").skipped == ()

    def test_passages_are_embedded_as_documents_and_the_query_as_a_query(
        self, corpus: rag.Corpus, embedder: WordEmbedder
    ) -> None:
        corpus.search("money")

        assert [kind for kind, _ in embedder.calls] == ["document", "document", "query"]

    def test_where_is_honoured_by_both_searches(self, embedder: WordEmbedder) -> None:
        with rag.open(embed=embedder) as corpus:
            corpus.add_markdown("refund policy", source_id="en", meta={"lang": "en"})
            corpus.add_markdown("money matters", source_id="ja", meta={"lang": "ja"})

            result = corpus.search("money", where={"lang": "en"})

        assert [hit.passage.source_id for hit in result.hits] == ["en"]

    async def test_an_async_embedder_answering_with_vectors_works_the_same(self) -> None:
        async with await rag.aopen(embed=AsyncWordEmbedder()) as corpus:
            await corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = await corpus.search("money back", limit=1)

        assert texts(result) == ["refund policy"]

    def test_the_sync_corpus_refuses_an_async_embedder(self) -> None:
        """Its private loop closes with the corpus, which would strand the embedder's client."""
        with pytest.raises(TypeError, match="use rag.aopen for async ones"):
            rag.open(embed=AsyncWordEmbedder())

    def test_the_sync_corpus_refuses_an_async_reranker(self) -> None:
        class AsyncReranker:
            async def rerank(
                self, query: str, documents: Sequence[str], *, top_n: int | None = None
            ) -> list[tuple[int, float]]:
                return []

        with pytest.raises(TypeError, match="use rag.aopen for async ones"):
            rag.open(rerank=AsyncReranker())

    async def test_the_async_corpus_calls_a_synchronous_embedder_off_the_loop(
        self, embedder: WordEmbedder
    ) -> None:
        async with await rag.aopen(embed=embedder) as corpus:
            await corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = await corpus.search("money back", limit=1)

        assert texts(result) == ["refund policy"]

    def test_the_fusion_settings_of_the_recipe_are_used(self, embedder: WordEmbedder) -> None:
        recipe = rag.Recipe(fuse=rag.FuseOptions(method="rrf", rrf_k=60))
        with rag.open(embed=embedder, recipe=recipe) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = corpus.search("money back", limit=1)

        # Keyword search finds nothing; vector search ranks it first: half of 1/(60+1).
        assert result.hits[0].score == pytest.approx(0.5 / 61)


class TestEmbeddingOnce:
    def test_an_unchanged_passage_is_not_embedded_again(
        self, corpus: rag.Corpus, embedder: WordEmbedder
    ) -> None:
        embedder.calls.clear()

        corpus.add_markdown(DOCUMENT + "\n\n# Returns\n\nmoney back", source_id="handbook")

        assert embedder.calls == [("document", ("money back",))]

    def test_a_document_added_again_unchanged_is_not_embedded_at_all(
        self, corpus: rag.Corpus, embedder: WordEmbedder
    ) -> None:
        embedder.calls.clear()

        corpus.add_markdown(DOCUMENT, source_id="handbook")

        assert embedder.calls == []

    def test_a_reused_vector_still_finds_its_passage(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(DOCUMENT, source_id="handbook")

        assert texts(corpus.search("money back", limit=1)) == ["refund policy"]

    def test_the_same_text_twice_in_a_document_is_embedded_once(
        self, embedder: WordEmbedder
    ) -> None:
        passages = rag.split("refund\n\n# Again\n\nrefund", source_id="d")
        with rag.open(embed=embedder) as corpus:
            embedder.calls.clear()

            corpus.add_passages(passages)

        assert embedder.calls == [("document", ("refund",))]


class TestFailures:
    def test_a_query_that_cannot_be_embedded_is_answered_by_keyword(self) -> None:
        with rag.open(embed=FailingOnQueries()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning, match="embed step failed with ConnectionError"):
                result = corpus.search("shipping")

        assert (texts(result)[:1], result.skipped) == (["shipping times"], ("embed",))

    def test_opening_without_numpy_is_refused_before_any_text_is_embedded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No embedding is paid for that the SQLite store could never search."""
        embedder = WordEmbedder()
        monkeypatch.setitem(sys.modules, "numpy", None)

        with pytest.raises(rag.StoreError, match=r'pip install "convilyn\[rag\]"'):
            rag.open(embed=embedder)

        assert embedder.calls == []

    def test_opening_without_numpy_and_without_an_embedder_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "numpy", None)

        with rag.open() as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            assert texts(corpus.search("shipping"))[:1] == ["shipping times"]

    def test_a_search_without_numpy_is_refused_naming_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not answered by keyword: the vectors were asked for and cannot be compared."""
        with rag.open(embed=WordEmbedder()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")
            monkeypatch.setitem(sys.modules, "numpy", None)

            with pytest.raises(rag.StoreError, match=r'pip install "convilyn\[rag\]"'):
                corpus.search("shipping")

    def test_a_search_without_numpy_does_not_fall_back_with_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with rag.open(embed=WordEmbedder()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")
            monkeypatch.setitem(sys.modules, "numpy", None)

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with contextlib.suppress(rag.StoreError):
                    corpus.search("shipping")

        assert [str(warning.message) for warning in caught] == []

    def test_a_failure_while_adding_a_document_is_raised(self) -> None:
        class Broken(WordEmbedder):
            def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
                if texts == ["dimensions"]:
                    return super().embed(texts, kind=kind)
                raise ConnectionError("the service is down")

        with rag.open(embed=Broken()) as corpus:
            with pytest.raises(ConnectionError):
                corpus.add_markdown(DOCUMENT, source_id="handbook")

    def test_an_embedder_that_returns_too_few_vectors_is_refused(self) -> None:
        class Short(WordEmbedder):
            def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
                return super().embed(texts, kind=kind)[:1]

        with rag.open(embed=Short()) as corpus:
            with pytest.raises(rag.RagError, match="returned 1 vectors for 2 texts"):
                corpus.add_markdown(DOCUMENT, source_id="handbook")

    def test_an_embedder_that_returns_an_empty_vector_is_refused_when_opening(self) -> None:
        class Empty(WordEmbedder):
            def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
                return [[] for _ in texts]

        with pytest.raises(rag.RagError, match="empty vector"):
            rag.open(embed=Empty())

    def test_an_embedder_without_a_model_name_is_refused(self) -> None:
        class Nameless(WordEmbedder):
            model = ""

        with pytest.raises(ValueError, match="model must be a non-empty string"):
            rag.open(embed=Nameless())

    @pytest.mark.parametrize("bad", [object(), "openai", len], ids=["object", "text", "function"])
    def test_something_that_is_not_an_embedder_is_refused(self, bad: object) -> None:
        with pytest.raises(TypeError, match="embed must have a model name"):
            rag.open(embed=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [object(), len], ids=["object", "function"])
    def test_something_that_is_not_a_reranker_is_refused(self, bad: object) -> None:
        with pytest.raises(TypeError, match="rerank must have a rerank"):
            rag.open(rerank=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("limit", [0, -1, True, 2.5], ids=str)
    def test_a_limit_that_is_not_a_count_is_refused(
        self, corpus: rag.Corpus, limit: object
    ) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            corpus.search("money", limit=limit)  # type: ignore[arg-type]


class TestIndexIdentity:
    def test_an_index_reopened_with_the_same_embedder_keeps_its_vectors(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=path), embed=WordEmbedder()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

        with rag.open(store=rag.store("sqlite", path=path), embed=WordEmbedder()) as corpus:
            result = corpus.search("money back", limit=1)

        assert texts(result) == ["refund policy"]

    def test_an_index_reopened_with_another_model_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        rag.open(store=rag.store("sqlite", path=path), embed=WordEmbedder()).close()

        class Other(WordEmbedder):
            model = "test/another-model"

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            rag.open(store=rag.store("sqlite", path=path), embed=Other())

    def test_an_index_with_vectors_reopened_without_an_embedder_is_refused(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "kb.sqlite3"
        rag.open(store=rag.store("sqlite", path=path), embed=WordEmbedder()).close()

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            rag.open(store=rag.store("sqlite", path=path))


class TestReranking:
    def test_the_reranker_s_order_and_scores_are_returned(self, embedder: WordEmbedder) -> None:
        with rag.open(embed=embedder) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")
            fused = corpus.search("money shipping")
        with rag.open(embed=WordEmbedder(), rerank=Reversing()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            reranked = corpus.search("money shipping")

        # The reverser scores the document at position n with n, best first.
        expected = [(text, float(n)) for n, text in enumerate(texts(fused))][::-1]
        assert [(hit.passage.text, hit.score) for hit in reranked.hits] == expected

    def test_the_reranker_sees_at_most_the_candidates(self, embedder: WordEmbedder) -> None:
        reranker = Reversing()
        markdown = "\n\n".join(f"# s{n}\n\nrefund {n}" for n in range(30))
        recipe = rag.Recipe(search=rag.SearchOptions(candidates=5, limit=3))
        with rag.open(embed=embedder, rerank=reranker, recipe=recipe) as corpus:
            corpus.add_markdown(markdown, source_id="d")

            result = corpus.search("refund")

        assert (max(reranker.seen), len(result.hits)) == (5, 3)

    def test_a_reranker_alone_reorders_the_keyword_hits(self) -> None:
        with rag.open(rerank=Reversing()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")
            plain = rag.open()
            plain.add_markdown(DOCUMENT, source_id="handbook")
            keyword = texts(plain.search("refund shipping"))
            plain.close()

            result = corpus.search("refund shipping")

        assert texts(result) == list(reversed(keyword))

    def test_a_failing_reranker_leaves_the_fused_order(self, embedder: WordEmbedder) -> None:
        class Down:
            def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None):  # type: ignore[no-untyped-def]
                raise ConnectionError("the service is down")

        with rag.open(embed=embedder, rerank=Down()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning, match="rerank step failed with ConnectionError"):
                result = corpus.search("money back", limit=1)

        assert (texts(result), result.skipped) == (["refund policy"], ("rerank",))

    @pytest.mark.parametrize(
        "answer",
        [
            [(5, 1.0)],
            [(0, 1.0), (0, 0.5)],
            [(0, float("nan"))],
            [("0", 1.0)],
            [(0, True)],
            [(0, 10**400)],
        ],
        ids=[
            "position-not-given",
            "position-twice",
            "nan",
            "text-position",
            "boolean-score",
            "huge-score",
        ],
    )
    def test_a_reranker_answer_that_does_not_fit_is_skipped(
        self, embedder: WordEmbedder, answer: list[tuple[object, object]]
    ) -> None:
        class Wrong:
            def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None):  # type: ignore[no-untyped-def]
                return answer

        with rag.open(embed=embedder, rerank=Wrong()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning, match="rerank step failed with RagError"):
                assert corpus.search("money").skipped == ("rerank",)

    async def test_a_reranker_answering_like_the_providers_one_works(
        self, embedder: WordEmbedder
    ) -> None:
        class Ranked:
            def __init__(self, index: int, score: float) -> None:
                self.index, self.score = index, score

        class Shaped:
            class Result:
                def __init__(self, ranking: list[Ranked]) -> None:
                    self.ranking = ranking

            async def rerank(
                self, query: str, documents: Sequence[str], *, top_n: int | None = None
            ):  # type: ignore[no-untyped-def]
                return self.Result([Ranked(len(documents) - 1, 9.0)])

        async with await rag.aopen(embed=embedder, rerank=Shaped()) as corpus:
            await corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = await corpus.search("money")

        assert [hit.score for hit in result.hits] == [9.0]


class TestWithTheProvidersConnectors:
    """The connectors in ``convilyn.providers`` are embedders and rerankers as they are."""

    def test_a_providers_embedder_and_reranker_plug_in_unchanged(self) -> None:
        import json

        import httpx
        import respx

        from convilyn import providers

        def embeddings(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            data = [{"index": n, "embedding": vector_of(t)} for n, t in enumerate(texts)]
            return httpx.Response(200, json={"data": data, "model": "m"})

        def ranking(request: httpx.Request) -> httpx.Response:
            documents = json.loads(request.content)["documents"]
            results = [
                {"index": n, "relevance_score": 1.0 / (n + 1)} for n in range(len(documents))
            ]
            return httpx.Response(200, json={"results": results})

        with respx.mock() as mock:
            mock.post("https://embed.example.com/v1/embeddings").mock(side_effect=embeddings)
            mock.post("https://rank.example.com/rerank").mock(side_effect=ranking)
            embedder = providers.embedder(
                "openai_compatible", model="m", base_url="https://embed.example.com/v1"
            )
            reranker = providers.reranker(
                "cohere_compatible", model="r", base_url="https://rank.example.com"
            )
            with rag.open(embed=embedder, rerank=reranker) as corpus:
                corpus.add_markdown(DOCUMENT, source_id="handbook")

                result = corpus.search("money back", limit=1)
            embedder.close()
            reranker.close()

        assert (texts(result), result.skipped) == (["refund policy"], ())


class TestDigestsAndNumbers:
    def test_a_passage_whose_digest_is_not_its_text_s_is_refused(self, corpus: rag.Corpus) -> None:
        """Otherwise the new text would be stored with the old text's vector."""
        passage = rag.split("original words", source_id="handbook")[0]
        edited = passage.model_copy(update={"text": "completely different words"})

        with pytest.raises(ValueError, match="digest is not the SHA-256 of its text"):
            corpus.add_passages([edited])

    def test_a_query_vector_the_store_cannot_take_falls_back_to_keyword(self) -> None:
        class WrongSize(WordEmbedder):
            def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
                vectors = super().embed(texts, kind=kind)
                return [vector[:2] for vector in vectors] if kind == "query" else vectors

        with rag.open(embed=WrongSize()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning, match="embed step failed with ValueError"):
                result = corpus.search("shipping")

        assert (texts(result)[:1], result.skipped) == (["shipping times"], ("embed",))

    def test_a_provider_s_failure_category_is_named_in_the_warning(self) -> None:
        class Refused(WordEmbedder):
            class Error(Exception):
                category = "auth"

            def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
                if kind == "query":
                    raise self.Error("PRIVATE-DETAIL")
                return super().embed(texts, kind=kind)

        with rag.open(embed=Refused()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning) as caught:
                corpus.search("shipping")

        message = str(caught[0].message)
        assert "Error (auth)" in message and "PRIVATE-DETAIL" not in message

    @pytest.mark.parametrize(
        "answer",
        [
            "numpy",
            [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}],
        ],
        ids=["numpy-numbers", "mappings"],
    )
    def test_a_reranker_answering_in_numpy_numbers_or_mappings_works(
        self, embedder: WordEmbedder, answer: object
    ) -> None:
        numpy = pytest.importorskip("numpy")
        given = (
            [(numpy.int64(1), numpy.float32(0.9)), (numpy.int64(0), numpy.float32(0.1))]
            if answer == "numpy"
            else answer
        )

        class Numeric:
            def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None):  # type: ignore[no-untyped-def]
                return given

        with rag.open(embed=embedder, rerank=Numeric()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = corpus.search("money shipping")

        assert (result.skipped, len(result.hits)) == ((), 2)

    def test_a_failure_of_the_store_itself_is_raised_not_skipped(
        self, embedder: WordEmbedder, tmp_path: Path
    ) -> None:
        import sqlite3

        path = tmp_path / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=path), embed=embedder) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")
            with sqlite3.connect(path) as raw:
                raw.execute("UPDATE vectors SET data = zeroblob(4)")
            raw.close()

            with pytest.raises(rag.StoreError, match="damaged"):
                corpus.search("money")


class TestFallbackReporting:
    def test_the_warning_names_the_caller_s_line_for_the_sync_corpus(self) -> None:
        with rag.open(embed=FailingOnQueries()) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning) as caught:
                corpus.search("shipping")

        assert Path(caught[0].filename).name == "test_hybrid.py"

    async def test_the_warning_names_the_caller_s_line_for_the_async_corpus(self) -> None:
        async with await rag.aopen(embed=FailingOnQueries()) as corpus:
            await corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.warns(RuntimeWarning) as caught:
                await corpus.search("shipping")

        assert Path(caught[0].filename).name == "test_hybrid.py"

    def test_a_store_failing_otherwise_than_by_refusing_the_vector_is_raised(
        self, embedder: WordEmbedder
    ) -> None:
        from convilyn.rag._stores.sqlite import SqliteStore

        class Broken(SqliteStore):
            async def dense(self, vector, *, limit, where=()):  # type: ignore[no-untyped-def]
                raise RuntimeError("the store is broken")

        with rag.open(store=Broken(), embed=embedder) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            with pytest.raises(RuntimeError, match="the store is broken"):
                corpus.search("money")

    def test_a_passage_whose_text_utf8_cannot_encode_is_refused(self, corpus: rag.Corpus) -> None:
        passage = rag.split("plain words", source_id="handbook")[0]
        lone = passage.model_copy(update={"text": "half \ud800 pair"})

        with pytest.raises(ValueError, match="digest is not the SHA-256 of its text"):
            corpus.add_passages([lone])

    def test_a_passage_utf8_cannot_encode_is_refused_even_without_a_digest(
        self, corpus: rag.Corpus
    ) -> None:
        passage = rag.split("plain words", source_id="handbook")[0]
        lone = passage.model_copy(update={"text": "half \ud800 pair", "digest": None})

        with pytest.raises(ValueError, match="digest is not the SHA-256 of its text"):
            corpus.add_passages([lone])

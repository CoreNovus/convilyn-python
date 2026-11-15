"""`rag.open` / `rag.aopen` — the stages composed over one store."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from convilyn import rag

HANDBOOK = "# Refunds\n\nRefund policy: thirty days.\n\n# Shipping\n\nShipping takes five days."
手冊 = "# 退款\n\n退款流程需要三個工作天。\n\n# 運費\n\n運費於結帳時計算。"


def found(result: rag.SearchResult) -> list[tuple[str, int]]:
    return [(hit.passage.source_id, hit.passage.ordinal) for hit in result.hits]


@pytest.fixture
def corpus() -> Iterator[rag.Corpus]:
    with rag.open() as opened:
        yield opened


class RecordingStore:
    """A caller's own store: keeps what it is given, matches by substring."""

    def __init__(self) -> None:
        self.identity: rag.IndexIdentity | None = None
        self.written: dict[str, tuple[rag.SourceInfo, list[rag.IndexEntry]]] = {}
        self.queries: list[tuple[str, int, tuple[rag.Condition, ...]]] = []
        self.closed = 0

    def describe(self) -> rag.StoreInfo:
        return rag.StoreInfo(kind="recording", cjk="substring")

    async def prepare(self, identity: rag.IndexIdentity) -> None:
        self.identity = identity

    async def write(self, source: rag.SourceInfo, entries: Sequence[rag.IndexEntry]) -> None:
        self.written[source.source_id] = (source, list(entries))

    async def remove(self, source_id: str) -> bool:
        return self.written.pop(source_id, None) is not None

    async def sources(self) -> tuple[rag.SourceInfo, ...]:
        return tuple(source for source, _ in self.written.values())

    async def lexical(
        self, query: str, *, limit: int, where: Sequence[rag.Condition] = ()
    ) -> tuple[rag.Hit, ...]:
        self.queries.append((query, limit, tuple(where)))
        return tuple(
            rag.Hit(passage=entry.passage, score=1.0)
            for _, entries in self.written.values()
            for entry in entries
            if query in entry.folded
        )[:limit]

    async def vectors(self, source_id: str) -> dict[str, tuple[float, ...]]:
        return {}

    async def dense(
        self, vector: Sequence[float], *, limit: int, where: Sequence[rag.Condition] = ()
    ) -> tuple[rag.Hit, ...]:
        # A keyword-only store of the caller's: it holds no vectors.
        raise ValueError("this index holds no vectors")

    async def close(self) -> None:
        self.closed += 1


class TestAddAndSearch:
    def test_added_markdown_is_found_by_a_word_in_it(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook")

        assert found(corpus.search("shipping")) == [("handbook", 1)]

    def test_a_two_character_chinese_query_finds_its_passage(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(手冊, source_id="手冊")

        assert found(corpus.search("運費")) == [("手冊", 1)]

    def test_adding_reports_what_was_stored(self, corpus: rag.Corpus) -> None:
        result = corpus.add_markdown(
            HANDBOOK, source_id="handbook", title="Staff handbook", meta={"lang": "en"}
        )

        assert result == rag.SourceInfo(
            source_id="handbook",
            title="Staff handbook",
            meta={"lang": "en"},
            passages=2,
            digest=hashlib.sha256(b"markdown\0" + HANDBOOK.encode("utf-8")).hexdigest(),
        )

    def test_the_title_leads_each_passage_s_section(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook", title="Staff handbook")

        hit = corpus.search("shipping").hits[0]

        assert hit.passage.section == ("Staff handbook", "Shipping")

    def test_the_query_is_folded_like_the_passages(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook")

        assert found(corpus.search("ＳＨＩＰＰＩＮＧ")) == [("handbook", 1)]

    def test_a_hit_keeps_the_text_as_written(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook")

        hit = corpus.search("shipping").hits[0]

        assert hit.passage.text == "Shipping takes five days."

    def test_adding_the_same_source_again_leaves_only_the_new_version(
        self, corpus: rag.Corpus
    ) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook")

        corpus.add_markdown("# Returns\n\nReturns are free.", source_id="handbook")

        assert (found(corpus.search("shipping refund")), found(corpus.search("returns"))) == (
            [],
            [("handbook", 0)],
        )

    def test_sources_lists_what_was_added(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="b")
        corpus.add_markdown(手冊, source_id="a")

        assert [(s.source_id, s.passages) for s in corpus.sources()] == [("a", 2), ("b", 2)]

    def test_a_removed_source_is_no_longer_found(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="handbook")

        removed = corpus.remove("handbook")

        assert (removed, found(corpus.search("shipping"))) == (True, [])

    def test_removing_an_unknown_source_reports_false(self, corpus: rag.Corpus) -> None:
        assert corpus.remove("nothing") is False


class TestWhere:
    def test_where_narrows_the_search(self, corpus: rag.Corpus) -> None:
        corpus.add_markdown(HANDBOOK, source_id="en", meta={"lang": "en"})
        corpus.add_markdown(HANDBOOK, source_id="draft", meta={"lang": "en", "draft": True})

        result = corpus.search("shipping", where={"draft": True})

        assert found(result) == [("draft", 1)]

    def test_where_with_a_list_matches_any_of_its_values(self, corpus: rag.Corpus) -> None:
        for lang in ("en", "ja", "ko"):
            corpus.add_markdown(HANDBOOK, source_id=lang, meta={"lang": lang})

        result = corpus.search("shipping", where={"lang": ["en", "ko"]})

        assert sorted(found(result)) == [("en", 1), ("ko", 1)]

    def test_an_operator_the_filter_does_not_have_is_refused(self, corpus: rag.Corpus) -> None:
        with pytest.raises(ValueError, match="not portable across stores"):
            corpus.search("shipping", where={"year": {"$gt": 2020}})

    def test_a_label_a_filter_could_not_match_is_refused_when_adding(
        self, corpus: rag.Corpus
    ) -> None:
        with pytest.raises(ValueError, match="must be a string, number or boolean"):
            corpus.add_markdown(HANDBOOK, source_id="a", meta={"tags": ["x"]})  # type: ignore[dict-item]


class TestLimit:
    def test_limit_defaults_to_the_recipe_s(self) -> None:
        recipe = rag.Recipe(search=rag.SearchOptions(limit=2))
        with rag.open(recipe=recipe) as corpus:
            for n in range(4):
                corpus.add_markdown(HANDBOOK, source_id=f"s{n}")

            assert len(corpus.search("shipping").hits) == 2

    def test_a_limit_in_the_call_outranks_the_recipe_s(self) -> None:
        recipe = rag.Recipe(search=rag.SearchOptions(limit=2))
        with rag.open(recipe=recipe) as corpus:
            for n in range(4):
                corpus.add_markdown(HANDBOOK, source_id=f"s{n}")

            assert len(corpus.search("shipping", limit=3).hits) == 3

    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_limit_that_is_not_positive_is_refused(self, corpus: rag.Corpus, limit: int) -> None:
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            corpus.search("shipping", limit=limit)


class TestRecipe:
    def test_the_recipe_s_sizes_decide_the_passages(self) -> None:
        recipe = rag.Recipe(split=rag.SplitOptions(target=4096, max=4096, min=0, section_depth=0))
        with rag.open(recipe=recipe) as corpus:
            stored = corpus.add_markdown(HANDBOOK, source_id="handbook")

        assert stored.passages == 1

    def test_a_measure_argument_replaces_the_recipe_s_estimate(self) -> None:
        recipe = rag.Recipe(split=rag.SplitOptions(target=3, max=3, min=0, section_depth=0))
        words = "\n\n".join(["one two", "three", "four five six"])
        with rag.open(recipe=recipe, measure=lambda text: len(text.split())) as corpus:
            stored = corpus.add_markdown(words, source_id="words")

        assert stored.passages == 2

    def test_the_corpus_reports_the_recipe_it_was_opened_with(self) -> None:
        recipe = rag.preset("ja")
        with rag.open(recipe=recipe) as corpus:
            assert corpus.recipe is recipe

    def test_a_fold_switched_off_is_off_for_both_sides(self) -> None:
        recipe = rag.Recipe(normalize=rag.NormalizeOptions(fold_width=False))
        with rag.open(recipe=recipe) as corpus:
            corpus.add_markdown(HANDBOOK, source_id="handbook")

            assert found(corpus.search("ＳＨＩＰＰＩＮＧ")) == []


class TestAddPassages:
    def test_passages_cut_by_the_caller_are_searchable(self, corpus: rag.Corpus) -> None:
        passages = rag.split(HANDBOOK, source_id="mine", measure=len)

        corpus.add_passages(passages, title="Mine", meta={"lang": "en"})

        assert found(corpus.search("refund", where={"lang": "en"})) == [("mine", 0)]

    def test_adding_reports_the_source_they_belong_to(self, corpus: rag.Corpus) -> None:
        passages = rag.split(HANDBOOK, source_id="mine")

        result = corpus.add_passages(passages)

        joined = "\n".join(passage.digest for passage in passages)
        assert result == rag.SourceInfo(
            source_id="mine",
            passages=2,
            digest=hashlib.sha256(b"passages\0" + joined.encode("utf-8")).hexdigest(),
        )

    def test_passages_of_two_sources_in_one_call_are_refused(self, corpus: rag.Corpus) -> None:
        mixed = [*rag.split(HANDBOOK, source_id="a"), *rag.split(HANDBOOK, source_id="b")]

        with pytest.raises(ValueError, match="exactly one source_id per call, got 2"):
            corpus.add_passages(mixed)

    def test_no_passages_at_all_are_refused(self, corpus: rag.Corpus) -> None:
        with pytest.raises(ValueError, match="exactly one source_id per call, got 0"):
            corpus.add_passages([])


class TestIdentity:
    def test_an_index_built_with_another_fold_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=path)) as corpus:
            corpus.add_markdown(HANDBOOK, source_id="handbook")
        other = rag.Recipe(normalize=rag.NormalizeOptions(fold_case=False))

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            rag.open(store=rag.store("sqlite", path=path), recipe=other)

    def test_an_index_is_reopened_with_the_settings_it_was_built_with(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=path)) as corpus:
            corpus.add_markdown(HANDBOOK, source_id="handbook")

        with rag.open(store=rag.store("sqlite", path=path)) as corpus:
            assert found(corpus.search("shipping")) == [("handbook", 1)]

    def test_changing_only_search_settings_does_not_require_a_rebuild(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=path)) as corpus:
            corpus.add_markdown(HANDBOOK, source_id="handbook")
        other = rag.Recipe(search=rag.SearchOptions(limit=1))

        with rag.open(store=rag.store("sqlite", path=path), recipe=other) as corpus:
            assert found(corpus.search("shipping refund")) in ([("handbook", 0)], [("handbook", 1)])


class TestACallersOwnStore:
    def test_it_is_prepared_with_the_identity_of_the_recipe(self) -> None:
        store = RecordingStore()
        recipe = rag.Recipe(normalize=rag.NormalizeOptions(fold_case=False))

        with rag.open(store=store, recipe=recipe):
            pass

        assert store.identity == rag.IndexIdentity(
            normalizer=rag.normalizer_stamp(recipe.normalize)
        )

    def test_it_receives_each_passage_with_its_folded_text(self) -> None:
        store = RecordingStore()
        with rag.open(store=store) as corpus:
            corpus.add_markdown("# Title\n\nＢＯＤＹ Text", source_id="a")

        _, entries = store.written["a"]
        assert [(e.passage.text, e.folded) for e in entries] == [("ＢＯＤＹ Text", "body text")]

    def test_it_receives_the_folded_query_the_limit_and_the_conditions(self) -> None:
        store = RecordingStore()
        with rag.open(store=store) as corpus:
            corpus.search("ＢＯＤＹ", limit=7, where={"lang": "en"})

        assert store.queries == [("body", 7, (rag.Condition(key="lang", values=("en",)),))]

    def test_its_hits_are_returned_as_the_result(self) -> None:
        store = RecordingStore()
        with rag.open(store=store) as corpus:
            corpus.add_markdown("# Title\n\nBODY", source_id="a")

            assert found(corpus.search("body")) == [("a", 0)]

    def test_the_corpus_exposes_it(self) -> None:
        store = RecordingStore()
        with rag.open(store=store) as corpus:
            assert corpus.store is store

    def test_it_is_closed_once_with_the_corpus(self) -> None:
        store = RecordingStore()
        corpus = rag.open(store=store)

        corpus.close()
        corpus.close()

        assert store.closed == 1

    def test_a_store_that_refuses_to_prepare_is_reported(self) -> None:
        class Refusing(RecordingStore):
            async def prepare(self, identity: rag.IndexIdentity) -> None:
                raise rag.StoreError("must be rebuilt")

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            rag.open(store=Refusing())

    def test_a_failure_to_close_does_not_replace_the_reason_opening_failed(self) -> None:
        class Refusing(RecordingStore):
            async def prepare(self, identity: rag.IndexIdentity) -> None:
                raise rag.StoreError("must be rebuilt")

            async def close(self) -> None:
                raise OSError("close failed")

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            rag.open(store=Refusing())

    def test_a_closed_corpus_refuses_work(self) -> None:
        corpus = rag.open(store=RecordingStore())
        corpus.close()

        with pytest.raises(rag.StoreError, match="the corpus is closed"):
            corpus.sources()

    def test_a_store_that_refuses_to_prepare_is_closed(self) -> None:
        class Refusing(RecordingStore):
            async def prepare(self, identity: rag.IndexIdentity) -> None:
                raise rag.StoreError("must be rebuilt")

        store = Refusing()
        with pytest.raises(rag.StoreError):
            rag.open(store=store)

        assert store.closed == 1


class TestAsync:
    async def test_the_async_corpus_returns_what_the_sync_one_does(self) -> None:
        def with_the_sync_corpus() -> rag.SearchResult:
            with rag.open() as sync_corpus:
                sync_corpus.add_markdown(HANDBOOK, source_id="handbook", meta={"lang": "en"})
                return sync_corpus.search("shipping refund", where={"lang": "en"})

        # In a worker thread: the sync corpus refuses to run on a running loop.
        expected = await asyncio.to_thread(with_the_sync_corpus)

        async with await rag.aopen() as corpus:
            await corpus.add_markdown(HANDBOOK, source_id="handbook", meta={"lang": "en"})
            result = await corpus.search("shipping refund", where={"lang": "en"})

        assert result == expected

    async def test_async_add_passages_remove_and_sources(self) -> None:
        async with await rag.aopen() as corpus:
            await corpus.add_passages(rag.split(HANDBOOK, source_id="a"))
            listed = [s.source_id for s in await corpus.sources()]
            removed = await corpus.remove("a")

        assert (listed, removed) == (["a"], True)

    async def test_the_async_corpus_exposes_its_store_and_recipe(self) -> None:
        store, recipe = RecordingStore(), rag.Recipe()

        async with await rag.aopen(store=store, recipe=recipe) as corpus:
            assert (corpus.store, corpus.recipe) == (store, recipe)

    async def test_leaving_the_block_closes_the_store(self) -> None:
        store = RecordingStore()

        async with await rag.aopen(store=store):
            pass

        assert store.closed == 1

    async def test_opening_the_sync_corpus_inside_a_running_loop_points_at_aopen(self) -> None:
        with pytest.raises(RuntimeError, match="use rag.aopen and AsyncCorpus"):
            rag.open()

    async def test_using_the_sync_corpus_inside_a_running_loop_points_at_aopen(self) -> None:
        corpus = await asyncio.to_thread(rag.open)

        with pytest.raises(RuntimeError, match="use rag.aopen and AsyncCorpus"):
            corpus.sources()

        await asyncio.to_thread(corpus.close)

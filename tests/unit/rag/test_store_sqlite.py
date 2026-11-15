"""The SQLite store: the shared contract, and what is particular to this engine."""

from __future__ import annotations

import importlib
import sqlite3
import sys
from array import array
from collections.abc import AsyncIterator
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from convilyn import rag
from convilyn.rag._stores import sqlite as sqlite_store

from .store_contract import IDENTITY, VECTORS, XY, StoreContract, X, Y, Z, found, put, put_vectors


class TestInMemory(StoreContract):
    @pytest.fixture
    async def store(self) -> AsyncIterator[rag.Store]:
        store = rag.store("sqlite")
        yield store
        await store.close()


class TestOnDisk(StoreContract):
    @pytest.fixture
    async def store(self, tmp_path: Path) -> AsyncIterator[rag.Store]:
        store = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        yield store
        await store.close()


class TestPersistence:
    async def test_a_file_keeps_its_passages_between_openings(self, tmp_path: Path) -> None:
        first = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        await first.prepare(IDENTITY)
        await put(first, "a", "refund policy")
        await first.close()

        second = rag.store("sqlite", path=str(tmp_path / "kb.sqlite3"))
        await second.prepare(IDENTITY)
        hits = await second.lexical("refund", limit=5)
        await second.close()

        assert found(hits) == [("a", 0)]

    async def test_a_file_built_with_another_fold_is_refused_on_reopening(
        self, tmp_path: Path
    ) -> None:
        first = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        await first.prepare(IDENTITY)
        await first.close()
        second = rag.store("sqlite", path=tmp_path / "kb.sqlite3")

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            await second.prepare(rag.IndexIdentity(normalizer="none"))

        await second.close()

    async def test_two_stores_in_memory_do_not_share_an_index(self) -> None:
        first, second = rag.store("sqlite"), rag.store("sqlite")
        await first.prepare(IDENTITY)
        await second.prepare(IDENTITY)
        await put(first, "a", "refund policy")

        hits = await second.lexical("refund", limit=5)
        await first.close()
        await second.close()

        assert hits == ()


class TestVectors:
    async def test_vectors_are_kept_between_openings(self, tmp_path: Path) -> None:
        first = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        await first.prepare(VECTORS)
        await put_vectors(first, "a", X, Y)
        await first.close()

        second = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        await second.prepare(VECTORS)
        hits = await second.dense(Y, limit=1)
        await second.close()

        assert found(hits) == [("a", 1)]

    async def test_an_index_larger_than_one_read_is_searched_whole(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sqlite_store, "_VECTORS_PER_READ", 2)
        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, X, Z, Y, XY)

        hits = await store.dense(Y, limit=2)
        await store.close()

        assert found(hits) == [("a", 3), ("a", 4)]

    async def test_ties_across_reads_keep_the_order_passages_were_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sqlite_store, "_VECTORS_PER_READ", 2)
        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        await put_vectors(store, "b", X, X, X)
        await put_vectors(store, "a", X, X)

        hits = await store.dense(X, limit=4)
        await store.close()

        assert found(hits) == [("b", 0), ("b", 1), ("b", 2), ("a", 0)]

    async def test_a_store_searched_before_it_is_prepared_holds_no_vectors(self) -> None:
        store = rag.store("sqlite")

        with pytest.raises(ValueError, match="holds no vectors"):
            await store.dense(X, limit=1)

        await store.close()

    async def test_vector_search_without_numpy_names_the_extra_to_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "numpy", None)
        store = rag.store("sqlite")
        await store.prepare(VECTORS)

        with pytest.raises(rag.StoreError, match=r'pip install "convilyn\[rag\]"'):
            await store.dense(X, limit=1)

        await store.close()

    async def test_a_numpy_that_fails_to_load_keeps_its_own_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Installed but broken (a missing library, say): the hint to install would mislead."""
        real_import = importlib.import_module

        def broken(name: str, package: str | None = None) -> object:
            if name == "numpy":
                raise ImportError("DLL load failed while importing _multiarray_umath")
            return real_import(name, package)

        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        monkeypatch.setattr(importlib, "import_module", broken)

        with pytest.raises(ImportError, match="DLL load failed"):
            await store.dense(X, limit=1)

        await store.close()

    async def test_without_numpy_keyword_search_and_indexing_still_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "numpy", None)
        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)

        hits = await store.lexical("passage", limit=1)
        await store.close()

        assert found(hits) == [("a", 0)]

    async def test_more_hits_than_sqlite_takes_arguments_are_returned(self) -> None:
        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        await put_vectors(store, "a", *([X] * 33_000))

        hits = await store.dense(X, limit=33_000)
        await store.close()

        assert len(hits) == 33_000

    @pytest.mark.parametrize(
        "damage",
        ["UPDATE vectors SET data = 'text'", "UPDATE vectors SET data = zeroblob(16)"],
        ids=["not-bytes", "wrong-length"],
    )
    async def test_a_damaged_vector_is_a_store_error(self, tmp_path: Path, damage: str) -> None:
        path = tmp_path / "kb.sqlite3"
        store = rag.store("sqlite", path=path)
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)
        with sqlite3.connect(path) as raw:
            raw.execute(damage)
        raw.close()

        with pytest.raises(rag.StoreError, match="damaged"):
            await store.dense(X, limit=1)
        with pytest.raises(rag.StoreError, match="damaged"):
            await store.vectors("a")

        await store.close()

    async def test_a_stored_vector_that_is_not_numbers_is_a_store_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "kb.sqlite3"
        store = rag.store("sqlite", path=path)
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)
        with sqlite3.connect(path) as raw:
            raw.execute("UPDATE vectors SET data = ?", (bytes.fromhex("0000c07f") * 3,))
        raw.close()

        with pytest.raises(rag.StoreError, match="damaged"):
            await store.dense(X, limit=1)

        await store.close()

    async def test_a_heading_trail_is_stored_as_its_characters(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        store = rag.store("sqlite", path=path)
        await store.prepare(IDENTITY)
        passage = rag.split("# 退款流程\n\nbody", source_id="a")[0]
        entry = rag.IndexEntry(passage=passage, folded=rag.normalize(passage.text))
        await store.write(rag.SourceInfo(source_id="a", passages=1, digest="0" * 64), [entry])
        await store.close()

        with sqlite3.connect(path) as raw:
            (section,) = raw.execute("SELECT section FROM passages").fetchone()
        raw.close()

        assert section == '["退款流程"]'

    async def test_a_heading_trail_with_a_lone_surrogate_is_still_stored(
        self, tmp_path: Path
    ) -> None:
        store = rag.store("sqlite", path=tmp_path / "kb.sqlite3")
        await store.prepare(IDENTITY)
        passage = rag.split("# Head\ud800\n\nbody", source_id="a")[0]
        entry = rag.IndexEntry(passage=passage, folded=rag.normalize(passage.text))
        await store.write(rag.SourceInfo(source_id="a", passages=1, digest="0" * 64), [entry])

        hits = await store.lexical("body", limit=1)
        await store.close()

        assert hits[0].passage.section == ("Head\ud800",)

    async def test_a_passage_that_can_no_longer_be_read_is_left_out(self, tmp_path: Path) -> None:
        path = tmp_path / "kb.sqlite3"
        store = rag.store("sqlite", path=path)
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)
        await put_vectors(store, "b", X)
        with sqlite3.connect(path) as raw:
            raw.execute("DELETE FROM sources WHERE source_id = 'a'")
        raw.close()

        hits = await store.dense(X, limit=5)
        await store.close()

        assert found(hits) == [("b", 0)]

    @pytest.mark.parametrize(
        "text",
        ["10", b"ab", bytearray(b"ab"), memoryview(b"ab"), ["1", "0"]],
        ids=["str", "bytes", "bytearray", "memoryview", "numeric-strings"],
    )
    async def test_text_is_not_read_as_a_vector(self, text: object) -> None:
        store = rag.store("sqlite")
        await store.prepare(VECTORS.model_copy(update={"dimensions": 2}))

        with pytest.raises(TypeError, match="not text or bytes"):
            await store.dense(text, limit=1)  # type: ignore[arg-type]

        await store.close()

    @pytest.mark.parametrize(
        "complex_vector", [[1 + 0j, 0j], [complex(1, 1), 0.0]], ids=["zero-imaginary", "mixed"]
    )
    async def test_a_complex_number_is_not_read_as_its_real_part(
        self, complex_vector: list[complex]
    ) -> None:
        store = rag.store("sqlite")
        await store.prepare(VECTORS.model_copy(update={"dimensions": 2}))

        with pytest.raises(TypeError, match="not complex ones"):
            await store.dense(complex_vector, limit=1)  # type: ignore[arg-type]

        await store.close()

    @pytest.mark.parametrize(
        "make",
        [
            lambda numpy: numpy.array(X, dtype=numpy.float32),
            lambda numpy: numpy.array([True, False, False]),
            lambda numpy: [numpy.array(1.0), numpy.array(0.0), numpy.array(0.0)],
            lambda numpy: [Decimal(1), Decimal(0), Decimal(0)],
            lambda numpy: [Fraction(1), 0, False],
            lambda numpy: memoryview(array("d", X)),
        ],
        ids=["float32-array", "bool-array", "0-d-arrays", "decimals", "mixed", "float-buffer"],
    )
    async def test_numbers_of_any_kind_a_float_can_be_made_from_are_a_vector(
        self, make: object
    ) -> None:
        numpy = pytest.importorskip("numpy")
        store = rag.store("sqlite")
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)

        hits = await store.dense(make(numpy), limit=1)  # type: ignore[operator]
        await store.close()

        assert found(hits) == [("a", 0)]

    def test_vectors_are_stored_the_same_on_every_machine(self) -> None:
        assert sqlite_store._packed((1.0, -2.0)) == bytes.fromhex("0000803f000000c0")


class TestAtomicReplacement:
    async def test_a_failed_write_leaves_the_previous_version(self) -> None:
        store = rag.store("sqlite")
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")
        broken = rag.IndexEntry.model_construct(passage=None, folded="x")

        with pytest.raises(AttributeError):
            await store.write(rag.SourceInfo(source_id="a", passages=1, digest="0" * 64), [broken])

        hits = await store.lexical("refund", limit=5)
        await store.close()
        assert found(hits) == [("a", 0)]


class TestEngineFailures:
    async def test_an_engine_error_is_reported_without_its_details(self, tmp_path: Path) -> None:
        store = rag.store("sqlite", path=tmp_path / "missing-directory" / "kb.sqlite3")

        with pytest.raises(rag.StoreError) as caught:
            await store.prepare(IDENTITY)

        await store.close()
        assert str(caught.value) == "the SQLite store could not complete the operation"

    async def test_an_engine_error_keeps_its_cause_for_debugging(self, tmp_path: Path) -> None:
        store = rag.store("sqlite", path=tmp_path / "missing-directory" / "kb.sqlite3")

        with pytest.raises(rag.StoreError) as caught:
            await store.prepare(IDENTITY)

        await store.close()
        assert isinstance(caught.value.__cause__, sqlite3.Error)

    async def test_text_the_engine_cannot_encode_is_a_store_error(self) -> None:
        store = rag.store("sqlite")
        await store.prepare(IDENTITY)
        passage = rag.split("placeholder", source_id="a")[0].model_copy(update={"text": "\ud800"})
        entry = rag.IndexEntry(passage=passage, folded="x")

        with pytest.raises(rag.StoreError, match="could not complete the operation"):
            await store.write(rag.SourceInfo(source_id="a", passages=1, digest="0" * 64), [entry])

        await store.close()

    async def test_a_number_too_long_to_store_is_a_store_error(self) -> None:
        store = rag.store("sqlite")
        await store.prepare(IDENTITY)
        source = rag.SourceInfo(source_id="a", meta={"n": 10**5000}, passages=0, digest="0" * 64)

        with pytest.raises(rag.StoreError, match="could not complete the operation"):
            await store.write(source, [])

        await store.close()

    async def test_a_limit_the_engine_cannot_hold_is_a_store_error(self) -> None:
        store = rag.store("sqlite")
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        with pytest.raises(rag.StoreError, match="could not complete the operation"):
            await store.lexical("refund", limit=2**63)

        await store.close()

    def test_a_connection_that_cannot_be_set_up_is_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class HalfOpen:
            closed = False

            def execute(self, statement: str) -> None:
                raise sqlite3.OperationalError("cannot set")

            def close(self) -> None:
                self.closed = True

        connection = HalfOpen()
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)

        with pytest.raises(sqlite3.OperationalError):
            sqlite_store._connect(":memory:")

        assert connection.closed is True

    def test_an_empty_path_is_refused(self) -> None:
        with pytest.raises(ValueError, match="path must not be empty"):
            rag.store("sqlite", path="")

    def test_a_build_without_full_text_search_is_named(self) -> None:
        class NoFullText:
            def execute(self, statement: str) -> None:
                if "fts5" in statement:
                    raise sqlite3.OperationalError("no such module: fts5")

        with pytest.raises(rag.StoreError, match="built without FTS5"):
            sqlite_store._prepare(NoFullText(), IDENTITY)  # type: ignore[arg-type]

    def test_another_schema_error_is_not_mistaken_for_missing_full_text_search(self) -> None:
        class Broken:
            def execute(self, statement: str) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            sqlite_store._prepare(Broken(), IDENTITY)  # type: ignore[arg-type]


class TestMatchExpression:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("refund", '"refund"'),
            ("refund policy", '"refund" OR "policy"'),
            ("refund refund", '"refund"'),
            ("退款流程", '"退款" OR "款流" OR "流程"'),
            ("款", '"款"*'),
            ("pdf轉檔", '"pdf" OR "轉檔"'),
            ("轉檔pdf", '"轉檔" OR "pdf"'),
            ("轉pdf檔", '"轉"* OR "pdf" OR "檔"*'),
            ("a_b", '"a_b"'),
            ('say "hi"', '"say" OR "hi"'),
            ("", ""),
            ("?!…", ""),
        ],
        ids=[
            "word",
            "words",
            "repeated-word",
            "cjk-run",
            "one-cjk-character",
            "latin-then-cjk",
            "cjk-then-latin",
            "cjk-around-latin",
            "underscore",
            "quotes",
            "empty",
            "marks-only",
        ],
    )
    def test_query_becomes_quoted_terms(self, query: str, expected: str) -> None:
        assert sqlite_store._match_expression(query) == expected

    def test_a_very_long_unspaced_query_is_cut_to_a_bounded_number_of_terms(self) -> None:
        query = "".join(chr(0x4E00 + n) for n in range(5_000))

        expression = sqlite_store._match_expression(query)

        assert expression.count(" OR ") == 255

    def test_one_word_alternating_scripts_is_cut_to_the_bound(self) -> None:
        query = "".join(f"a{n}一" for n in range(5_000))

        expression = sqlite_store._match_expression(query)

        assert expression.count(" OR ") == 255

    def test_repeated_pieces_do_not_use_up_the_bound(self) -> None:
        query = "一" * 300 + "丁丂 refund"

        expression = sqlite_store._match_expression(query)

        assert expression == '"一一" OR "一丁" OR "丁丂" OR "refund"'

    def test_words_past_the_bound_are_not_read(self) -> None:
        words = [f"w{n}" for n in range(5_000)]

        expression = sqlite_store._match_expression(" ".join(words))

        assert expression.endswith('"w255"')

    def test_a_very_long_query_is_cut_to_a_bounded_number_of_terms(self) -> None:
        query = " ".join(f"w{n}" for n in range(5_000))

        expression = sqlite_store._match_expression(query)

        assert expression.count(" OR ") == 255


class TestIndexedText:
    @pytest.mark.parametrize(
        ("folded", "expected"),
        [
            ("refund policy", "refund policy"),
            ("退款流程", " 退款 款流 流程 "),
            ("款", " 款 "),
            ("pdf轉檔說明", "pdf 轉檔 檔說 說明 "),
            ("カタカナ", " カタ タカ カナ "),
            ("환불", " 환불 "),
        ],
        ids=["latin", "cjk-run", "one-character", "mixed", "kana", "hangul"],
    )
    def test_unspaced_runs_are_indexed_as_overlapping_pairs(
        self, folded: str, expected: str
    ) -> None:
        assert sqlite_store._spaced(folded) == expected


class TestFactory:
    def test_an_unknown_kind_is_refused_with_the_choices(self) -> None:
        with pytest.raises(ValueError, match=r"unknown store 'redis'; choose one of: sqlite"):
            rag.store("redis")

    def test_a_very_long_kind_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            rag.store("x" * 100_000)

        assert len(str(caught.value)) < 200

    def test_an_option_the_store_does_not_take_is_refused(self) -> None:
        with pytest.raises(TypeError):
            rag.store("sqlite", url="http://example.invalid")

    def test_the_kinds_are_listed_as_data(self) -> None:
        assert rag.stores() == (
            rag.StoreInfo(kind="sqlite", cjk="overlapping two-character pieces"),
        )

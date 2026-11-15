"""What every store must do, whichever engine is behind it.

Subclass :class:`StoreContract` in a store's own test module and provide the
``store`` fixture; every test here then runs against that store. A store that
passes can stand in for any other, which is what lets a recipe measured on one
be used on the next.
"""

from __future__ import annotations

import pytest

from convilyn import rag

IDENTITY = rag.IndexIdentity(normalizer=rag.normalizer_stamp())


def entries(source_id: str, *texts: str) -> list[rag.IndexEntry]:
    """Index entries for ``texts``, folded the default way."""
    return [
        rag.IndexEntry(passage=passage, folded=rag.normalize(passage.text))
        for passage in rag.split(
            "\n\n".join(f"# s{n}\n\n{t}" for n, t in enumerate(texts)), source_id=source_id
        )
    ]


async def put(
    store: rag.Store,
    source_id: str,
    *texts: str,
    title: str | None = None,
    meta: dict[str, rag.Scalar] | None = None,
) -> None:
    rows = entries(source_id, *texts)
    source = rag.SourceInfo(
        source_id=source_id, title=title, meta=meta or {}, passages=len(rows), digest="0" * 64
    )
    await store.write(source, rows)


def found(hits: tuple[rag.Hit, ...]) -> list[tuple[str, int]]:
    return [(hit.passage.source_id, hit.passage.ordinal) for hit in hits]


#: An index that holds three-number vectors.
VECTORS = rag.IndexIdentity(
    normalizer=rag.normalizer_stamp(), embedding="test/three-axes", dimensions=3
)

#: Unit vectors, named for what they point at.
X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
XY = (0.7071067811865476, 0.7071067811865476, 0.0)


async def put_vectors(
    store: rag.Store,
    source_id: str,
    *vectors: tuple[float, ...] | None,
    meta: dict[str, rag.Scalar] | None = None,
) -> None:
    """Write one passage per vector; ``None`` writes a passage without one."""
    rows = [
        row.model_copy(update={"vector": vector})
        for row, vector in zip(
            entries(source_id, *(f"passage {n}" for n in range(len(vectors)))),
            vectors,
            strict=True,
        )
    ]
    source = rag.SourceInfo(
        source_id=source_id, meta=meta or {}, passages=len(rows), digest="0" * 64
    )
    await store.write(source, rows)


class StoreContract:
    """Tests every :class:`convilyn.rag.Store` passes. Provide a ``store`` fixture."""

    # ── writing and listing ──────────────────────────────────────────────

    async def test_it_satisfies_the_store_interface(self, store: rag.Store) -> None:
        assert isinstance(store, rag.Store)

    async def test_a_new_store_holds_no_sources(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)

        assert await store.sources() == ()

    async def test_a_written_source_is_listed_with_its_labels(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)

        await put(store, "a", "refund policy", "shipping", title="Handbook", meta={"lang": "en"})

        assert await store.sources() == (
            rag.SourceInfo(
                source_id="a", title="Handbook", meta={"lang": "en"}, passages=2, digest="0" * 64
            ),
        )

    async def test_sources_are_listed_in_identifier_order(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        for source_id in ("b", "c", "a"):
            await put(store, source_id, "text")

        listed = [source.source_id for source in await store.sources()]

        assert listed == ["a", "b", "c"]

    async def test_a_source_with_no_passages_is_still_listed(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)

        await store.write(rag.SourceInfo(source_id="empty", passages=0, digest="0" * 64), [])

        assert [source.source_id for source in await store.sources()] == ["empty"]

    # ── searching ────────────────────────────────────────────────────────

    async def test_a_word_finds_the_passage_that_contains_it(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", "shipping times")

        hits = await store.lexical("shipping", limit=5)

        assert found(hits) == [("a", 1)]

    async def test_a_hit_carries_the_passage_as_it_was_written(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        rows = entries("a", "Refund POLICY")
        await store.write(rag.SourceInfo(source_id="a", passages=1, digest="0" * 64), rows)

        hits = await store.lexical("refund", limit=5)

        assert hits[0].passage == rows[0].passage

    async def test_a_hit_carries_its_source_s_title_and_labels(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", title="Handbook", meta={"lang": "en", "year": 2024})

        hits = await store.lexical("refund", limit=5)

        assert (hits[0].title, hits[0].meta) == ("Handbook", {"lang": "en", "year": 2024})

    async def test_the_better_match_comes_first(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund", "refund refund refund policy for every refund")
        await put(
            store, "b", "a long passage about shipping that mentions refund once among many words"
        )

        hits = await store.lexical("refund", limit=5)

        assert hits[0].score >= hits[-1].score

    async def test_scores_do_not_increase_down_the_list(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        for n in range(6):
            await put(store, f"s{n}", "refund " * (n + 1) + "and other words " * n)

        scores = [hit.score for hit in await store.lexical("refund", limit=10)]

        assert scores == sorted(scores, reverse=True)

    async def test_any_word_of_the_query_can_match(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")
        await put(store, "b", "shipping times")

        hits = await store.lexical("refund shipping", limit=5)

        assert sorted(found(hits)) == [("a", 0), ("b", 0)]

    @pytest.mark.parametrize(
        ("text", "query"),
        [
            ("退款流程需要三個工作天", "退款"),
            ("退款流程需要三個工作天", "工作天"),
            ("退款流程需要三個工作天", "款"),
            ("返金には三営業日かかります", "返金"),
            ("ひらがなとカタカナ", "カタカナ"),
            ("환불은 영업일 기준 삼일이 걸립니다", "환불"),
        ],
        ids=[
            "chinese-two-characters",
            "chinese-three-characters",
            "chinese-one-character",
            "japanese-kanji",
            "japanese-kana",
            "korean",
        ],
    )
    async def test_a_short_query_in_an_unspaced_script_matches(
        self, store: rag.Store, text: str, query: str
    ) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", text)
        await put(store, "b", "unrelated english text")

        hits = await store.lexical(rag.normalize(query), limit=5)

        assert found(hits) == [("a", 0)]

    async def test_a_query_mixing_scripts_matches_on_either(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "pdf 轉檔說明")
        await put(store, "b", "圖片壓縮")

        hits = await store.lexical("pdf壓縮", limit=5)

        assert sorted(found(hits)) == [("a", 0), ("b", 0)]

    async def test_limit_caps_the_number_of_hits(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        for n in range(5):
            await put(store, f"s{n}", "refund policy")

        hits = await store.lexical("refund", limit=3)

        assert len(hits) == 3

    @pytest.mark.parametrize(
        "query", ["", "   ", "?!", "nomatch"], ids=["empty", "blank", "marks", "absent"]
    )
    async def test_a_query_that_matches_nothing_returns_no_hits(
        self, store: rag.Store, query: str
    ) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        assert await store.lexical(query, limit=5) == ()

    @pytest.mark.parametrize(
        "query",
        [
            '"',
            '" OR "',
            "refund AND",
            "NOT refund",
            "a*",
            "(refund",
            "col:refund",
            "refund NEAR policy",
            "^refund",
            "-refund",
            "'; DROP TABLE passages; --",
        ],
    )
    async def test_query_syntax_of_the_engine_is_read_as_plain_words(
        self, store: rag.Store, query: str
    ) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        hits = await store.lexical(query, limit=5)

        assert found(hits) in ([], [("a", 0)])

    # ── filtering ────────────────────────────────────────────────────────

    async def test_a_condition_keeps_only_matching_sources(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", meta={"lang": "en"})
        await put(store, "b", "refund policy", meta={"lang": "ja"})

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="lang", values=("ja",))]
        )

        assert found(hits) == [("b", 0)]

    async def test_a_condition_with_several_values_matches_any_of_them(
        self, store: rag.Store
    ) -> None:
        await store.prepare(IDENTITY)
        for source_id, lang in (("a", "en"), ("b", "ja"), ("c", "ko")):
            await put(store, source_id, "refund policy", meta={"lang": lang})

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="lang", values=("en", "ko"))]
        )

        assert sorted(found(hits)) == [("a", 0), ("c", 0)]

    async def test_every_condition_has_to_hold(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", meta={"lang": "en", "team": "sales"})
        await put(store, "b", "refund policy", meta={"lang": "en", "team": "support"})

        hits = await store.lexical(
            "refund",
            limit=5,
            where=[
                rag.Condition(key="lang", values=("en",)),
                rag.Condition(key="team", values=("support",)),
            ],
        )

        assert found(hits) == [("b", 0)]

    async def test_a_source_without_the_label_does_not_match(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="lang", values=("en",))]
        )

        assert hits == ()

    @pytest.mark.parametrize(
        ("stored", "wanted"),
        [(1, "1"), ("1", 1), (True, 1), (1, True), (1.5, "1.5"), ("true", True)],
        ids=[
            "int-vs-str",
            "str-vs-int",
            "bool-vs-int",
            "int-vs-bool",
            "float-vs-str",
            "str-vs-bool",
        ],
    )
    async def test_a_value_of_another_type_does_not_match(
        self, store: rag.Store, stored: rag.Scalar, wanted: rag.Scalar
    ) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", meta={"label": stored})

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="label", values=(wanted,))]
        )

        assert hits == ()

    @pytest.mark.parametrize("value", ["en", 7, 2.5, True, False, "", "it's"])
    async def test_a_value_matches_itself(self, store: rag.Store, value: rag.Scalar) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", meta={"label": value})

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="label", values=(value,))]
        )

        assert found(hits) == [("a", 0)]

    # ── replacing and removing ───────────────────────────────────────────

    async def test_writing_a_source_again_replaces_its_passages(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", "old shipping text")

        await put(store, "a", "returns")

        assert await store.lexical("refund shipping", limit=5) == ()

    async def test_writing_a_source_again_replaces_its_labels(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy", meta={"lang": "en"})

        await put(store, "a", "refund policy", meta={"lang": "ja"})

        hits = await store.lexical(
            "refund", limit=5, where=[rag.Condition(key="lang", values=("en",))]
        )
        assert hits == ()

    async def test_writing_a_source_again_leaves_one_listing(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "one", "two")

        await put(store, "a", "three")

        assert [(s.source_id, s.passages) for s in await store.sources()] == [("a", 1)]

    async def test_replacing_one_source_leaves_the_others(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")
        await put(store, "b", "refund policy")

        await put(store, "a", "returns")

        assert found(await store.lexical("refund", limit=5)) == [("b", 0)]

    async def test_removing_a_source_reports_that_it_was_there(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        assert await store.remove("a") is True

    async def test_removing_an_unknown_source_reports_that_it_was_not(
        self, store: rag.Store
    ) -> None:
        await store.prepare(IDENTITY)

        assert await store.remove("a") is False

    async def test_a_removed_source_is_no_longer_found(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        await store.remove("a")

        assert await store.lexical("refund", limit=5) == ()

    async def test_a_removed_source_is_no_longer_listed(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        await store.remove("a")

        assert await store.sources() == ()

    # ── identity and lifetime ────────────────────────────────────────────

    async def test_preparing_again_with_the_same_identity_keeps_the_index(
        self, store: rag.Store
    ) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        await store.prepare(IDENTITY)

        assert found(await store.lexical("refund", limit=5)) == [("a", 0)]

    @pytest.mark.parametrize(
        "other",
        [
            rag.IndexIdentity(normalizer="none"),
            rag.IndexIdentity(format=1, normalizer=rag.normalizer_stamp()),
            VECTORS,
        ],
        ids=["another-fold", "another-format", "vectors-added"],
    )
    async def test_preparing_with_another_identity_is_refused(
        self, store: rag.Store, other: rag.IndexIdentity
    ) -> None:
        await store.prepare(IDENTITY)

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            await store.prepare(other)

    async def test_closing_twice_is_harmless(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await store.close()

        await store.close()

    async def test_a_closed_store_refuses_work(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await store.close()

        with pytest.raises(rag.StoreError):
            await store.sources()

    async def test_describe_names_a_kind_the_factory_knows(self, store: rag.Store) -> None:
        assert store.describe() in rag.stores()

    # ── vector search ────────────────────────────────────────────────────

    async def test_a_document_s_vectors_are_read_back_by_passage_digest(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, None)
        digest = entries("a", "passage 0")[0].passage.digest

        stored = await store.vectors("a")

        assert list(stored) == [digest] and stored[digest] == pytest.approx(X)

    async def test_an_unknown_document_has_no_vectors(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)

        assert await store.vectors("missing") == {}

    async def test_an_index_without_vectors_reads_back_none(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)
        await put(store, "a", "refund policy")

        assert await store.vectors("a") == {}

    async def test_the_nearest_passage_comes_first(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, Y, XY)

        hits = await store.dense(Y, limit=3)

        assert found(hits) == [("a", 1), ("a", 2), ("a", 0)]

    async def test_a_hit_s_score_is_the_cosine_similarity(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", XY)

        hits = await store.dense(X, limit=1)

        assert hits[0].score == pytest.approx(0.70710678, abs=1e-6)

    async def test_a_vector_hit_is_the_passage_with_its_document_labels(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, meta={"lang": "en"})

        hit = (await store.dense(X, limit=1))[0]

        assert (hit.passage, hit.meta) == (entries("a", "passage 0")[0].passage, {"lang": "en"})

    async def test_the_limit_caps_the_vector_hits(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, Y, Z, XY)

        hits = await store.dense(X, limit=2)

        assert len(hits) == 2

    async def test_where_is_honoured_by_vector_search(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "en", X, meta={"lang": "en"})
        await put_vectors(store, "ja", X, meta={"lang": "ja"})

        hits = await store.dense(X, limit=5, where=(rag.Condition(key="lang", values=("ja",)),))

        assert found(hits) == [("ja", 0)]

    async def test_a_passage_stored_without_a_vector_is_never_returned(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", None, X)

        hits = await store.dense(X, limit=5)

        assert found(hits) == [("a", 1)]

    async def test_a_passage_without_a_vector_is_still_found_by_keyword(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", None)

        assert found(await store.lexical("passage", limit=5)) == [("a", 0)]

    async def test_equal_scores_come_back_in_the_same_order_every_time(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X, X, X)
        await put_vectors(store, "b", X, X)

        first = await store.dense(X, limit=5)
        second = await store.dense(X, limit=5)

        assert found(first) == found(second)

    async def test_rewriting_a_document_replaces_its_vectors(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)
        await put_vectors(store, "a", Y)

        hits = await store.dense(X, limit=5)

        assert [round(hit.score, 6) for hit in hits] == [0.0]

    async def test_a_removed_document_s_vectors_are_not_found(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)

        await store.remove("a")

        assert await store.dense(X, limit=5) == ()

    async def test_an_empty_index_returns_no_vector_hits(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)

        assert await store.dense(X, limit=5) == ()

    async def test_a_query_of_any_length_is_scored_by_its_direction(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", X)

        hits = await store.dense((7.0, 0.0, 0.0), limit=1)

        assert hits[0].score == pytest.approx(1.0, abs=1e-6)

    async def test_a_stored_vector_of_any_length_is_scored_by_its_direction(
        self, store: rag.Store
    ) -> None:
        await store.prepare(VECTORS)
        await put_vectors(store, "a", (10.0, 0.0, 0.0), XY)

        hits = await store.dense(XY, limit=2)

        assert found(hits) == [("a", 1), ("a", 0)]

    async def test_a_query_with_no_direction_is_refused(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)

        with pytest.raises(ValueError, match="no direction"):
            await store.dense((0.0, 0.0, 0.0), limit=5)

    @pytest.mark.parametrize("limit", [0, -1, True, 2.5], ids=str)
    async def test_a_limit_that_is_not_a_count_is_refused_by_both_searches(
        self, store: rag.Store, limit: object
    ) -> None:
        await store.prepare(VECTORS)

        with pytest.raises(ValueError, match="limit must be a positive integer"):
            await store.dense(X, limit=limit)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            await store.lexical("passage", limit=limit)  # type: ignore[arg-type]

    async def test_a_query_vector_of_another_size_is_refused(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)

        with pytest.raises(ValueError, match="does not fit this index"):
            await store.dense((1.0, 0.0), limit=5)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "infinite"])
    async def test_a_query_vector_that_is_not_numbers_is_refused(
        self, store: rag.Store, bad: float
    ) -> None:
        await store.prepare(VECTORS)

        with pytest.raises(ValueError, match="finite numbers"):
            await store.dense((bad, 0.0, 0.0), limit=5)

    async def test_a_stored_vector_of_another_size_is_refused(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)

        with pytest.raises(ValueError, match="does not fit this index"):
            await put_vectors(store, "a", (1.0, 0.0))

    async def test_an_index_without_vectors_refuses_a_vector_search(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)

        with pytest.raises(ValueError, match="holds no vectors"):
            await store.dense(X, limit=5)

    async def test_an_index_without_vectors_refuses_to_store_one(self, store: rag.Store) -> None:
        await store.prepare(IDENTITY)

        with pytest.raises(ValueError, match="holds no vectors"):
            await put_vectors(store, "a", X)

    async def test_an_index_built_by_another_embedding_is_refused(self, store: rag.Store) -> None:
        await store.prepare(VECTORS)
        other = VECTORS.model_copy(update={"embedding": "test/another-model"})

        with pytest.raises(rag.StoreError, match="must be rebuilt"):
            await store.prepare(other)

"""`rag.IndexIdentity` and `rag.IndexEntry` — what a store is handed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from convilyn import rag


def passage() -> rag.Passage:
    return rag.split("refund policy", source_id="a")[0]


class TestIndexIdentity:
    def test_an_index_without_vectors_names_no_embedding(self) -> None:
        identity = rag.IndexIdentity(normalizer="n")

        assert (identity.embedding, identity.dimensions) == (None, None)

    def test_the_layout_version_is_the_one_with_vectors(self) -> None:
        assert rag.IndexIdentity(normalizer="n").format == 3

    def test_an_embedding_is_recorded_with_its_size(self) -> None:
        identity = rag.IndexIdentity(normalizer="n", embedding="m", dimensions=8)

        assert (identity.embedding, identity.dimensions) == ("m", 8)

    @pytest.mark.parametrize(
        "fields",
        [{"embedding": "m"}, {"dimensions": 8}],
        ids=["embedding-without-size", "size-without-embedding"],
    )
    def test_an_embedding_and_its_size_come_together(self, fields: dict[str, object]) -> None:
        with pytest.raises(ValidationError, match="set together"):
            rag.IndexIdentity(normalizer="n", **fields)  # type: ignore[arg-type]

    @pytest.mark.parametrize("size", [0, -1], ids=str)
    def test_a_size_that_is_not_positive_is_refused(self, size: int) -> None:
        with pytest.raises(ValidationError):
            rag.IndexIdentity(normalizer="n", embedding="m", dimensions=size)


class TestIndexEntry:
    def test_an_entry_has_no_vector_unless_given_one(self) -> None:
        assert rag.IndexEntry(passage=passage(), folded="x").vector is None

    def test_an_entry_keeps_the_vector_it_is_given(self) -> None:
        entry = rag.IndexEntry(passage=passage(), folded="x", vector=(0.6, 0.8))

        assert entry.vector == pytest.approx((0.6, 0.8))

    def test_a_vector_is_scaled_to_length_one(self) -> None:
        entry = rag.IndexEntry(passage=passage(), folded="x", vector=(3.0, 4.0))

        assert entry.vector == pytest.approx((0.6, 0.8))

    def test_a_vector_with_no_direction_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="no direction"):
            rag.IndexEntry(passage=passage(), folded="x", vector=(0.0, 0.0))

    def test_a_size_that_is_a_boolean_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            rag.IndexIdentity(normalizer="n", embedding="m", dimensions=True)

    @pytest.mark.parametrize(
        "vector",
        [(), (float("nan"), 1.0), (float("inf"), 0.0)],
        ids=["empty", "nan", "infinite"],
    )
    def test_a_vector_that_is_not_finite_numbers_is_refused(
        self, vector: tuple[float, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="finite numbers"):
            rag.IndexEntry(passage=passage(), folded="x", vector=vector)

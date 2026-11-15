"""``graphs.corrective_rag``: the shared contract, then what is particular to it."""

from __future__ import annotations

from typing import Any

import pytest

from convilyn import graphs, rag
from tests.unit.graphs.graph_contract import GraphContract

SHELF = {
    "refund": ["Refunds take five days.", "Our office is in Taipei."],
    "money back": ["Refunds take five days."],
}


def look_up(query: str) -> list[str]:
    return SHELF.get(query, [])


def mentions_refunds(question: str, passage: str) -> bool:
    return "Refunds" in passage


def to_plain_words(question: str, query: str) -> str:
    return "money back"


def quote_first(question: str, documents: list[graphs.Document]) -> str:
    return documents[0]["text"] if documents else "I could not find that."


def build(**changes: Any) -> Any:
    functions: dict[str, Any] = {
        "retrieve": look_up,
        "grade": mentions_refunds,
        "rewrite": to_plain_words,
        "generate": quote_first,
    }
    return graphs.corrective_rag(**{**functions, **changes})


class TestCorrectiveRagContract(GraphContract):
    NAME = "corrective_rag"

    @pytest.fixture
    def graph(self) -> Any:
        return build()

    @pytest.fixture
    def payload(self) -> dict[str, Any]:
        return {"question": "refund"}

    @pytest.fixture
    def expected(self) -> dict[str, Any]:
        return {
            "answer": "Refunds take five days.",
            "documents": [{"text": "Refunds take five days.", "source": None}],
            "grounded": True,
            "attempts": 1,
        }


class TestRetrying:
    def test_a_search_that_finds_nothing_relevant_is_reworded_and_run_again(self) -> None:
        result = build().invoke({"question": "how long until I am repaid"})

        assert result == {
            "answer": "Refunds take five days.",
            "documents": [{"text": "Refunds take five days.", "source": None}],
            "grounded": True,
            "attempts": 2,
        }

    def test_the_reworder_is_given_the_query_that_failed(self) -> None:
        seen: list[tuple[str, str]] = []

        def record(question: str, query: str) -> str:
            seen.append((question, query))
            return f"{query}+"

        build(rewrite=record, max_retries=2).invoke({"question": "q"})

        assert seen == [("q", "q"), ("q", "q+")]

    def test_when_every_search_fails_the_answer_is_marked_ungrounded(self) -> None:
        result = build(rewrite=lambda question, query: "nothing", max_retries=2).invoke(
            {"question": "weather"}
        )

        assert result == {
            "answer": "I could not find that.",
            "documents": [],
            "grounded": False,
            "attempts": 3,
        }

    def test_no_retries_means_one_search(self) -> None:
        result = build(max_retries=0).invoke({"question": "weather"})

        assert result["attempts"] == 1

    def test_passages_judged_irrelevant_do_not_reach_the_answer(self) -> None:
        handed: list[list[graphs.Document]] = []

        def record(question: str, documents: list[graphs.Document]) -> str:
            handed.append(documents)
            return "ok"

        build(generate=record).invoke({"question": "refund"})

        assert handed == [[{"text": "Refunds take five days.", "source": None}]]


class TestWithACorpus:
    def test_a_corpus_search_fits_as_the_retriever_and_names_its_source(self) -> None:
        with rag.open(store=rag.store("sqlite")) as corpus:
            corpus.add_markdown("# Refunds\n\nRefunds take five days.", source_id="handbook")
            graph = build(retrieve=corpus.search)

            result = graph.invoke({"question": "refunds"})

        assert [document["source"] for document in result["documents"]] == ["handbook"]

    async def test_an_async_corpus_search_fits_too(self) -> None:
        async with await rag.aopen() as corpus:
            await corpus.add_markdown("# Refunds\n\nRefunds take five days.", source_id="handbook")
            graph = build(retrieve=corpus.search)

            result = await graph.ainvoke({"question": "refunds"})

        assert result["grounded"] is True


class TestBadArguments:
    def test_a_negative_retry_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_retries must not be negative"):
            build(max_retries=-1)

    @pytest.mark.parametrize("found", ["one string", 7, None, [1, 2]])
    def test_a_retriever_that_returns_the_wrong_shape_is_reported(self, found: object) -> None:
        graph = build(retrieve=lambda query: found)

        with pytest.raises(graphs.GraphError, match="must return"):
            graph.invoke({"question": "refund"})

    def test_a_reworder_that_returns_no_text_is_reported(self) -> None:
        graph = build(rewrite=lambda question, query: None)

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"question": "weather"})

    def test_a_generator_that_returns_no_text_is_reported(self) -> None:
        graph = build(generate=lambda question, documents: {"answer": "x"})

        with pytest.raises(graphs.GraphError, match="must return a string"):
            graph.invoke({"question": "refund"})

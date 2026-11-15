"""``bench.run`` and ``bench.arun`` over any retriever, and the report they give."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest

from convilyn import rag
from convilyn.rag import bench

DOCUMENT = "# Refunds\n\nRefunds take five days.\n\n# Shipping\n\nParcels ship in two days.\n"

QUESTIONS = (
    bench.Question(
        id="refund",
        question="refunds",
        group="billing",
        support=(bench.Support(source_id="handbook", excerpt="Refunds take five days"),),
    ),
    bench.Question(
        id="ship",
        question="parcels",
        group="delivery",
        support=(bench.Support(source_id="handbook", excerpt="Parcels ship in two days"),),
    ),
    bench.Question(id="moon", question="who founded the moon"),
    bench.Question(id="later", question="anything", status="draft"),
)


@pytest.fixture
def corpus() -> Iterator[rag.Corpus]:
    with rag.open() as opened:
        opened.add_markdown(DOCUMENT, source_id="handbook")
        yield opened


def mapping_retriever(source_id: str, text: str):  # type: ignore[no-untyped-def]
    return lambda question: [{"source_id": source_id, "text": text}]


class TestRunningACorpus:
    def test_a_corpus_search_finds_both_answers_first(self, corpus: rag.Corpus) -> None:
        report = bench.run(retrieve=corpus.search, questions=QUESTIONS)

        assert (report.metrics["recall@1"], report.metrics["ndcg@10"]) == (1.0, 1.0)

    def test_drafts_are_counted_and_not_run(self, corpus: rag.Corpus) -> None:
        report = bench.run(retrieve=corpus.search, questions=QUESTIONS)

        assert (report.drafts, [r.id for r in report.results]) == (1, ["refund", "ship", "moon"])

    def test_a_question_with_no_answer_is_timed_but_not_scored(self, corpus: rag.Corpus) -> None:
        report = bench.run(retrieve=corpus.search, questions=QUESTIONS)

        moon = report.results[2]
        assert (moon.answerable, moon.scores, moon.latency_ms >= 0) == (False, {}, True)

    def test_groups_average_their_own_questions(self, corpus: rag.Corpus) -> None:
        report = bench.run(retrieve=corpus.search, questions=QUESTIONS)

        assert sorted(report.groups) == ["billing", "delivery"]

    def test_a_question_file_can_be_given_by_path(self, corpus: rag.Corpus, tmp_path: Path) -> None:
        path = tmp_path / "q.jsonl"
        path.write_text(
            "\n".join(question.model_dump_json() for question in QUESTIONS), encoding="utf-8"
        )

        assert len(bench.run(retrieve=corpus.search, questions=path).results) == 3


class TestWhatRetrieveMayReturn:
    def test_mappings_with_source_id_and_text(self) -> None:
        retrieve = mapping_retriever("handbook", "Refunds take five days.")

        report = bench.run(retrieve=retrieve, questions=QUESTIONS[:1], k=(1,))

        assert report.metrics["recall@1"] == 1.0

    def test_a_sequence_of_hits(self, corpus: rag.Corpus) -> None:
        report = bench.run(
            retrieve=lambda question: corpus.search(question).hits, questions=QUESTIONS[:1]
        )

        assert report.metrics["mrr@1"] == 1.0

    def test_only_the_first_max_k_hits_are_read(self) -> None:
        endless = itertools.repeat({"source_id": "elsewhere", "text": "nothing"})

        report = bench.run(retrieve=lambda question: endless, questions=QUESTIONS[:1], k=(3,))

        assert report.metrics["recall@3"] == 0.0

    @pytest.mark.parametrize(
        "answer",
        ["text", {"source_id": "a", "text": "b"}, 5, [{"source_id": "a"}], [object()]],
        ids=["string", "one-mapping", "number", "mapping-without-text", "unknown-item"],
    )
    def test_anything_else_is_refused(self, answer: object) -> None:
        with pytest.raises(TypeError, match="retrieve must return"):
            bench.run(retrieve=lambda question: answer, questions=QUESTIONS[:1])


class TestSyncAndAsync:
    def test_run_refuses_an_async_retrieve(self) -> None:
        async def retrieve(question: str) -> list[object]:
            return []

        with pytest.raises(TypeError, match="use bench.arun"):
            bench.run(retrieve=retrieve, questions=QUESTIONS[:1])

    def test_run_refuses_an_awaitable_answer_and_closes_it(self) -> None:
        async def answer() -> list[object]:
            return []

        with pytest.raises(TypeError, match="returned an awaitable"):
            bench.run(retrieve=lambda question: answer(), questions=QUESTIONS[:1])

    async def test_arun_awaits_an_async_retrieve(self) -> None:
        async def retrieve(question: str) -> list[dict[str, str]]:
            return [{"source_id": "handbook", "text": "Refunds take five days."}]

        report = await bench.arun(retrieve=retrieve, questions=QUESTIONS[:1])

        assert report.metrics["recall@1"] == 1.0

    async def test_arun_calls_a_sync_retrieve_too(self) -> None:
        retrieve = mapping_retriever("handbook", "Refunds take five days.")

        report = await bench.arun(retrieve=retrieve, questions=QUESTIONS[:1])

        assert report.metrics["recall@1"] == 1.0


class TestArguments:
    @pytest.mark.parametrize("k", [(), (0,), (5, 5), (1.0,), (True,), "15"])
    def test_k_must_be_distinct_positive_integers(self, k: object) -> None:
        with pytest.raises(ValueError, match="k must be distinct positive integers"):
            bench.run(retrieve=lambda q: [], questions=QUESTIONS, k=k)  # type: ignore[arg-type]

    def test_k_is_reported_in_ascending_order(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[:1], k=(10, 1))

        assert report.k == (1, 10)

    @pytest.mark.parametrize("cover", [0, 1.5, float("nan"), True, "half"])
    def test_cover_must_be_above_zero_and_at_most_one(self, cover: object) -> None:
        with pytest.raises(ValueError, match="cover must be a number above 0"):
            bench.run(retrieve=lambda q: [], questions=QUESTIONS, cover=cover)  # type: ignore[arg-type]

    @pytest.mark.parametrize("questions", [(), ("not a question",)])
    def test_questions_must_be_questions(self, questions: object) -> None:
        with pytest.raises(ValueError, match="questions must be"):
            bench.run(retrieve=lambda q: [], questions=questions)  # type: ignore[arg-type]

    def test_questions_given_directly_must_have_unique_ids(self) -> None:
        with pytest.raises(ValueError, match="question 1 repeats the id of 0"):
            bench.run(retrieve=lambda q: [], questions=(QUESTIONS[0], QUESTIONS[0]))


class TestReport:
    def report(self) -> bench.Report:
        return bench.run(
            retrieve=mapping_retriever("handbook", "Refunds take five days."),
            questions=QUESTIONS,
        )

    def test_averages_cover_the_answerable_questions(self) -> None:
        assert self.report().metrics["recall@1"] == 0.5

    def test_latency_percentiles_cover_every_question_run(self) -> None:
        metrics = self.report().metrics

        assert metrics["latency_p95_ms"] >= metrics["latency_p50_ms"] >= 0  # type: ignore[operator]

    def test_a_set_with_no_answerable_question_cannot_be_measured(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[2:3])

        assert report.metrics["ndcg@10"] is None

    def test_a_met_bound_passes(self) -> None:
        outcome = self.report().check(min_recall_at_1=0.5, max_latency_p95_ms=60_000)

        assert (outcome.ok, outcome.passed) == (True, ("recall@1", "latency_p95_ms"))

    def test_a_missed_bound_says_by_how_much(self) -> None:
        with pytest.raises(
            AssertionError, match=r"recall@1 0\.5000 < 0\.9000 \(short by 0\.4000\)"
        ):
            self.report().require(min_recall_at_1=0.9)

    def test_an_exceeded_maximum_says_by_how_much(self) -> None:
        outcome = self.report().check(max_recall_at_1=0.25)

        assert outcome.breaches == ("recall@1 0.5000 > 0.2500 (over by 0.2500)",)

    def test_a_bound_that_cannot_be_measured_fails(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[2:3])

        with pytest.raises(AssertionError, match="ndcg@10: not measurable"):
            report.require(min_ndcg_at_10=0.5)

    @pytest.mark.parametrize("name", ["min_ndcg_at_7", "ndcg_at_10", "least_recall_at_1"])
    def test_an_unknown_bound_is_refused_with_the_known_ones(self, name: str) -> None:
        with pytest.raises(ValueError, match="a bound is one of: min_/max_recall_at_1"):
            self.report().check(**{name: 0.5})

    @pytest.mark.parametrize("limit", [float("inf"), True, "0.5"])
    def test_a_bound_must_be_a_finite_number(self, limit: object) -> None:
        with pytest.raises(ValueError, match="must be a finite number"):
            self.report().check(min_recall_at_1=limit)  # type: ignore[arg-type]

    def test_a_report_round_trips_through_json(self) -> None:
        report = self.report()

        assert bench.Report.model_validate_json(report.model_dump_json()) == report


class TestEdges:
    def test_an_awaitable_without_close_is_still_refused(self) -> None:
        class Pending:
            def __await__(self):  # type: ignore[no-untyped-def]
                yield from ()

        with pytest.raises(TypeError, match="returned an awaitable"):
            bench.run(retrieve=lambda question: Pending(), questions=QUESTIONS[:1])

    def test_a_set_of_drafts_only_has_no_latency(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[3:])

        assert (report.results, report.metrics["latency_p95_ms"]) == ((), None)

    def test_require_returns_quietly_when_every_bound_is_met(self) -> None:
        report = bench.run(
            retrieve=mapping_retriever("handbook", "Refunds take five days."),
            questions=QUESTIONS[:1],
        )

        assert report.require(min_recall_at_1=1.0) is None


class TestReviewedEdges:
    """Inputs a security review tried against the first version."""

    def test_check_with_no_bound_is_refused(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[:1])

        with pytest.raises(ValueError, match="give at least one bound"):
            report.check()

    def test_a_bound_too_large_for_a_float_is_refused(self) -> None:
        report = bench.run(retrieve=lambda q: [], questions=QUESTIONS[:1])

        with pytest.raises(ValueError, match="must be a finite number"):
            report.check(min_recall_at_1=10**400)

    def test_a_metric_that_is_not_a_finite_number_is_not_measured(self) -> None:
        from convilyn.rag.bench import _bounds

        assert _bounds.check({"recall@1": float("nan")}, {"min_recall_at_1": 0.5}) == (
            (),
            (),
            ("recall@1",),
        )

    @pytest.mark.parametrize(
        "change",
        [
            {"latency_ms": "NaN"},
            {"latency_ms": -1},
            {"scores": {"recall@1": "NaN", "mrr@1": 0, "ndcg@1": 0}},
            {"scores": {"recall@1": 2, "mrr@1": 0, "ndcg@1": 0}},
            {"scores": {"recall@1": 1}},
        ],
        ids=["nan-latency", "negative-latency", "nan-score", "score-above-one", "missing-metric"],
    )
    def test_a_report_read_back_is_checked_like_one_a_run_made(
        self, change: dict[str, object]
    ) -> None:
        result = {"id": "q", "group": None, "answerable": True, "latency_ms": 1.0}
        result["scores"] = {"recall@1": 1.0, "mrr@1": 1.0, "ndcg@1": 1.0}
        result.update(change)

        with pytest.raises(ValueError):
            bench.Report.model_validate({"k": [1], "cover": 0.5, "results": [result]})

    @pytest.mark.parametrize("k", [[5, 1], [1, 1], [0]], ids=["unsorted", "repeated", "zero"])
    def test_a_report_s_k_must_be_distinct_ascending_and_positive(self, k: list[int]) -> None:
        with pytest.raises(ValueError):
            bench.Report.model_validate({"k": k, "cover": 0.5, "results": []})

    def test_k_may_be_any_iterable_of_integers(self) -> None:
        report = bench.run(
            retrieve=lambda q: [], questions=QUESTIONS[:1], k=(cut for cut in (3, 1))
        )

        assert report.k == (1, 3)

    async def test_an_async_retrieve_answering_with_an_awaitable_is_refused(self) -> None:
        async def inner() -> list[object]:
            return []

        async def retrieve(question: str):  # type: ignore[no-untyped-def]
            return inner()

        with pytest.raises(TypeError, match="retrieve must return"):
            await bench.arun(retrieve=retrieve, questions=QUESTIONS[:1])

    def test_a_future_returned_to_run_is_cancelled(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()

            with pytest.raises(TypeError, match="returned an awaitable"):
                bench.run(retrieve=lambda q: future, questions=QUESTIONS[:1])

            assert future.cancelled()
        finally:
            loop.close()

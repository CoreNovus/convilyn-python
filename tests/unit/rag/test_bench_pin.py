"""``bench.pin`` and ``bench.check``: holding retrieval to a standard."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from convilyn import rag
from convilyn.rag import bench

DEMO = resources.files("convilyn.rag.bench").joinpath("_demo")


def documents() -> list[tuple[str, str]]:
    return sorted(
        (entry.name[:-3], entry.read_text(encoding="utf-8"))
        for entry in DEMO.iterdir()
        if entry.name.endswith(".md")
    )


def questions() -> tuple[bench.Question, ...]:
    with resources.as_file(DEMO.joinpath("questions.jsonl")) as path:
        return bench.questions(path)


def measured(
    recipe: rag.Recipe | None = None, *, skip: str | None = None
) -> tuple[bench.Report, tuple[rag.SourceInfo, ...]]:
    """Index the demo (all but ``skip``) with ``recipe``, run its questions."""
    with rag.open(recipe=recipe) as corpus:
        for source_id, text in documents():
            if source_id != skip:
                corpus.add_markdown(text, source_id=source_id)
        return bench.run(retrieve=corpus.search, questions=questions()), corpus.sources()


@pytest.fixture(scope="module")
def baseline() -> tuple[bench.Report, tuple[rag.SourceInfo, ...]]:
    return measured()


@pytest.fixture
def standard(baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]) -> bench.Pin:
    report, sources = baseline
    return bench.pin(report, questions=questions(), sources=sources)


class TestOutcomes:
    def test_the_same_run_again_holds(
        self, standard: bench.Pin, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline

        assert bench.check(standard, report, questions=questions(), sources=sources).outcome == (
            "held"
        )

    def test_a_setting_that_returns_one_passage_is_worse(self, standard: bench.Pin) -> None:
        narrow = rag.Recipe(search=rag.SearchOptions(limit=1))
        report, sources = measured(narrow)

        outcome = bench.check(standard, report, questions=questions(), sources=sources)

        assert (outcome.outcome, outcome.comparison is not None) == ("worse", True)

    def test_a_changed_question_set_is_not_comparable(
        self, standard: bench.Pin, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline
        edited = (*questions()[:-1], questions()[-1].model_copy(update={"question": "other"}))

        outcome = bench.check(standard, report, questions=edited, sources=sources)

        assert (outcome.outcome, outcome.reasons) == (
            "not_comparable",
            ("the question set changed since the pin",),
        )

    def test_a_missing_document_is_not_comparable_and_counted(self, standard: bench.Pin) -> None:
        report, sources = measured(skip="shipping")

        outcome = bench.check(standard, report, questions=questions(), sources=sources)

        assert "0 added, 1 removed, 0 edited" in outcome.reasons[0]

    def test_a_different_k_or_cover_is_not_comparable(self, standard: bench.Pin) -> None:
        report, sources = measured()
        other = report.model_copy(update={"k": (1, 10), "cover": 0.6})

        outcome = bench.check(standard, other, questions=questions(), sources=sources)

        assert outcome.reasons == ("k differs from the pin's", "cover differs from the pin's")

    def test_too_few_questions_to_compare_is_not_comparable(self) -> None:
        few = questions()[:5]
        with rag.open() as corpus:
            for source_id, text in documents():
                corpus.add_markdown(text, source_id=source_id)
            report = bench.run(retrieve=corpus.search, questions=few)
            sources = corpus.sources()
        standard = bench.pin(report, questions=few, sources=sources)

        outcome = bench.check(standard, report, questions=few, sources=sources)

        assert (outcome.outcome, "a comparison needs at least 10" in outcome.reasons[0]) == (
            "not_comparable",
            True,
        )

    def test_a_reordered_question_file_is_the_same_set(
        self, standard: bench.Pin, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline

        outcome = bench.check(
            standard, report, questions=tuple(reversed(questions())), sources=sources
        )

        assert outcome.outcome == "held"


class TestSettings:
    def test_a_changed_settings_file_is_reported_not_refused(
        self, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]], tmp_path: Path
    ) -> None:
        report, sources = baseline
        before, after = tmp_path / "a.toml", tmp_path / "b.toml"
        before.write_text("[recipe.search]\nlimit = 10\n", encoding="utf-8")
        after.write_text("[recipe.search]\nlimit = 5\n", encoding="utf-8")
        standard = bench.pin(report, questions=questions(), sources=sources, settings=before)

        outcome = bench.check(
            standard, report, questions=questions(), sources=sources, settings=after
        )

        assert (outcome.outcome, outcome.settings_changed) == ("held", True)

    def test_without_settings_on_either_side_nothing_is_said(
        self, standard: bench.Pin, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline

        outcome = bench.check(standard, report, questions=questions(), sources=sources)

        assert outcome.settings_changed is None


class TestPinFile:
    def test_a_pin_round_trips_through_json(self, standard: bench.Pin) -> None:
        assert bench.Pin.model_validate_json(standard.model_dump_json()) == standard

    @pytest.mark.parametrize(
        "change",
        [
            {"format": 2},
            {"metric": "ndcg@3"},
            {"questions": "PLANTED"},
            {"corpus": [["b", "0" * 64], ["a", "0" * 64]]},
            {"corpus": [["a", "0" * 64], ["a", "0" * 64]]},
            {"corpus": [["a", "0" * 64], ["a", "1" * 64]]},
            {"settings": "not-a-digest"},
        ],
        ids=[
            "format",
            "metric-not-in-k",
            "digest",
            "unsorted",
            "repeated",
            "one-id-twice",
            "settings",
        ],
    )
    def test_a_pin_that_does_not_agree_with_itself_is_refused(
        self, standard: bench.Pin, change: dict[str, object]
    ) -> None:
        data = standard.model_dump(mode="json")
        data.update(change)

        with pytest.raises(ValueError):
            bench.Pin.model_validate(data)

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"sources": ["handbook"]}, "sources must be the corpus's sources"),
            ({"questions": ["what?"]}, "questions must be a question file's path"),
        ],
    )
    def test_arguments_of_the_wrong_kind_are_refused(
        self,
        baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]],
        arguments: dict[str, object],
        message: str,
    ) -> None:
        report, sources = baseline
        given: dict[str, object] = {"questions": questions(), "sources": sources, **arguments}

        with pytest.raises(ValueError, match=message):
            bench.pin(report, **given)  # type: ignore[arg-type]

    def test_a_question_file_path_is_read_like_the_questions(
        self, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline
        with resources.as_file(DEMO.joinpath("questions.jsonl")) as path:
            by_path = bench.pin(report, questions=path, sources=sources)

        assert (
            by_path.questions == bench.pin(report, questions=questions(), sources=sources).questions
        )


class TestMarkdown:
    def test_a_held_check_shows_its_comparison(
        self, standard: bench.Pin, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline

        text = bench.check(standard, report, questions=questions(), sources=sources).to_markdown()

        assert text.startswith(
            "# Pin check: held\n\nSettings since the pin: not recorded.\n\n# Comparison: ndcg@10"
        )

    def test_a_check_that_could_not_compare_lists_why(self, standard: bench.Pin) -> None:
        report, sources = measured(skip="shipping")

        text = bench.check(standard, report, questions=questions(), sources=sources).to_markdown()

        assert text.endswith(
            "- the documents changed since the pin: 0 added, 1 removed, 0 edited\n"
        )


class TestReviewedEdges:
    """Inputs a security review tried against the first version."""

    def test_sources_listing_one_document_twice_are_refused(
        self, baseline: tuple[bench.Report, tuple[rag.SourceInfo, ...]]
    ) -> None:
        report, sources = baseline
        twice = (*sources, sources[0].model_copy(update={"digest": "0" * 64}))

        with pytest.raises(ValueError, match="each source_id once"):
            bench.pin(report, questions=questions(), sources=twice)

    def test_markdown_that_reads_like_passage_digests_is_another_document(self) -> None:
        passages = rag.split("Refunds take five days.", source_id="a")
        lookalike = "\n".join(passage.digest for passage in passages)
        with rag.open() as corpus:
            by_passages = corpus.add_passages(passages)
            by_markdown = corpus.add_markdown(lookalike, source_id="a")

        assert by_passages.digest != by_markdown.digest

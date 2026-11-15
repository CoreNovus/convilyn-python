"""``bench.compare`` and the Markdown a person reads."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from convilyn.rag import bench


def report(
    values: Sequence[float],
    *,
    ids: Sequence[str] | None = None,
    groups: Sequence[str | None] | None = None,
    unanswered: Sequence[str] = (),
) -> bench.Report:
    """A report over k = (10,) whose every metric for question i is values[i]."""
    names = ids or [f"q{i}" for i in range(len(values))]
    labels = groups or [None] * len(values)
    results = [
        bench.QuestionResult(
            id=name,
            group=label,
            answerable=True,
            scores={"recall@10": value, "mrr@10": value, "ndcg@10": value},
            latency_ms=float(index + 1),
        )
        for index, (name, label, value) in enumerate(zip(names, labels, values, strict=True))
    ]
    results += [
        bench.QuestionResult(id=name, group=None, answerable=False, scores={}, latency_ms=1.0)
        for name in unanswered
    ]
    return bench.Report(k=(10,), cover=0.5, results=tuple(results))


NOISE = [0.5 + (0.1 if index % 2 else -0.1) for index in range(40)]


class TestVerdicts:
    def test_a_clear_gain_is_better(self) -> None:
        assert bench.compare(report([0.2] * 30), report([0.8] * 30)).verdict == "better"

    def test_a_clear_loss_is_worse(self) -> None:
        assert bench.compare(report([0.8] * 30), report([0.2] * 30)).verdict == "worse"

    def test_noise_is_inconclusive(self) -> None:
        assert bench.compare(report([0.5] * 40), report(NOISE)).verdict == "inconclusive"

    def test_no_change_is_inconclusive(self) -> None:
        assert bench.compare(report([0.5] * 10), report([0.5] * 10)).verdict == "inconclusive"

    def test_a_uniform_gain_has_that_gain_as_its_whole_interval(self) -> None:
        result = bench.compare(report([0.2] * 30), report([0.8] * 30))

        assert (result.low, result.mean_difference, result.high) == pytest.approx((0.6, 0.6, 0.6))


class TestReproducibility:
    def test_the_same_seed_gives_the_same_interval(self) -> None:
        first = bench.compare(report([0.5] * 40), report(NOISE), seed=7)
        second = bench.compare(report([0.5] * 40), report(NOISE), seed=7)

        assert first == second

    def test_the_comparison_records_what_it_rests_on(self) -> None:
        result = bench.compare(report([0.5] * 40), report(NOISE), resamples=500, seed=3)

        assert (result.metric, result.n, result.resamples, result.seed, result.confidence) == (
            "ndcg@10",
            40,
            500,
            3,
            0.95,
        )

    def test_questions_are_paired_by_id_not_by_position(self) -> None:
        names = [f"q{i}" for i in range(10)]
        before = report([i / 10 for i in range(10)], ids=names)
        after = report([i / 10 for i in range(10)][::-1], ids=names[::-1])

        assert bench.compare(before, after).mean_difference == pytest.approx(0.0)

    def test_questions_with_no_answer_are_left_out(self) -> None:
        before = report([0.2] * 10, unanswered=["x"])
        after = report([0.8] * 10, unanswered=["y"])

        assert bench.compare(before, after).n == 10


class TestRefusals:
    def test_reports_over_different_questions_are_refused_with_counts(self) -> None:
        before = report([0.5, 0.5], ids=["a", "b"])
        after = report([0.5, 0.5, 0.5], ids=["a", "c", "d"])

        with pytest.raises(ValueError, match="1 answered only before, 2 only after"):
            bench.compare(before, after)

    def test_reports_with_nothing_to_compare_are_refused(self) -> None:
        empty = report([], unanswered=["x"])

        with pytest.raises(ValueError, match="share 0 answerable questions"):
            bench.compare(empty, empty)

    def test_fewer_than_ten_shared_questions_are_refused(self) -> None:
        with pytest.raises(ValueError, match="share 9 answerable questions; .* at least 10"):
            bench.compare(report([0.2] * 9), report([0.8] * 9))

    @pytest.mark.parametrize("metric", ["ndcg@5", "latency_p95_ms"])
    def test_a_metric_the_reports_do_not_carry_is_refused(self, metric: str) -> None:
        with pytest.raises(ValueError, match="has no metric"):
            bench.compare(report([0.5]), report([0.5]), metric=metric)

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"resamples": 99}, "resamples must be an integer from 100 to 1,000,000"),
            ({"resamples": 1_000_001}, "resamples must be"),
            ({"resamples": 1000.0}, "resamples must be"),
            ({"seed": "0"}, "seed must be an integer"),
            ({"confidence": 1}, "confidence must be a number from 0.5"),
            ({"confidence": 0.4}, "confidence must be"),
            ({"confidence": 0}, "confidence must be"),
            ({"resamples": 100, "confidence": 0.99}, "too few resamples for this confidence"),
            ({"confidence": True}, "confidence must be"),
            ({"confidence": "high"}, "confidence must be"),
        ],
    )
    def test_options_out_of_range_are_refused(
        self, options: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            bench.compare(report([0.5]), report([0.5]), **options)  # type: ignore[arg-type]

    def test_anything_but_a_report_is_refused(self) -> None:
        with pytest.raises(TypeError, match="after must be a bench Report"):
            bench.compare(report([0.5]), {"results": []})  # type: ignore[arg-type]


class TestMarkdown:
    def test_a_report_reads_as_a_table_of_all_and_each_group(self) -> None:
        text = report([1.0, 0.5], groups=["billing", "a|b"], unanswered=["x"]).to_markdown()

        assert text == (
            "# Retrieval report\n"
            "\n"
            "Questions run: 3 (2 with an answer, 1 without); drafts skipped: 0. "
            "k = 10; cover = 0.5.\n"
            "\n"
            "| metric | all | a\\|b | billing |\n"
            "|---|---:|---:|---:|\n"
            "| recall@10 | 0.750 | 0.500 | 1.000 |\n"
            "| mrr@10 | 0.750 | 0.500 | 1.000 |\n"
            "| ndcg@10 | 0.750 | 0.500 | 1.000 |\n"
            "| latency_p50_ms | 1.0 | 2.0 | 1.0 |\n"
            "| latency_p95_ms | 2.0 | 2.0 | 1.0 |\n"
        )

    def test_a_metric_that_cannot_be_measured_reads_n_a(self) -> None:
        assert "| ndcg@10 | n/a |" in report([], unanswered=["x"]).to_markdown()

    def test_a_comparison_reads_as_its_verdict_and_numbers(self) -> None:
        result = bench.compare(report([0.2] * 30), report([0.8] * 30), resamples=1000)

        assert result.to_markdown() == (
            "# Comparison: ndcg@10\n"
            "\n"
            "**better**: mean difference +0.600 (95% interval +0.600 to +0.600) over 30 "
            "questions; paired bootstrap, 1,000 resamples, seed 0.\n"
        )

    def test_a_long_or_marked_up_group_name_is_shown_inert(self) -> None:
        names = ["cr\r# HEADING", r"esc\|pipe", "<b>bold</b> & `code`", "x" * 70]

        text = report([0.5] * 4, groups=names).to_markdown()

        header = text.splitlines()[4]
        assert header == (
            r"| metric | all | &lt;b&gt;bold&lt;/b&gt; &amp; \`code\` | cr \# HEADING "
            r"| esc\\\|pipe | " + "x" * 59 + "\N{HORIZONTAL ELLIPSIS} |"
        )

    def test_inline_markdown_in_a_group_name_is_escaped(self) -> None:
        name = "[a](u) ![p](q) **b** _i_ ~s~"

        header = report([0.5], groups=[name]).to_markdown().splitlines()[4]

        assert header == r"| metric | all | \[a\](u) \!\[p\](q) \*\*b\*\* \_i\_ \~s\~ |"

    def test_a_name_is_cut_by_what_shows_not_by_its_escaping(self) -> None:
        header = report([0.5], groups=["_" * 70]).to_markdown().splitlines()[4]

        assert header == "| metric | all | " + "\\_" * 59 + "\N{HORIZONTAL ELLIPSIS} |"

    def test_joiners_stay_and_reordering_controls_go(self) -> None:
        family = "\N{MAN}\N{ZERO WIDTH JOINER}\N{WOMAN}\N{ZERO WIDTH JOINER}\N{GIRL}"
        name = family + "\N{RIGHT-TO-LEFT OVERRIDE}abc"

        header = report([0.5], groups=[name]).to_markdown().splitlines()[4]

        assert header == f"| metric | all | {family} abc |"

    def test_a_high_confidence_is_shown_exactly(self) -> None:
        result = bench.compare(report([0.2] * 30), report([0.8] * 30), confidence=0.999)

        assert "(99.9% interval" in result.to_markdown()


class TestReviewedEdges:
    """Inputs a security review tried against the first version."""

    def test_rounding_alone_is_inconclusive(self) -> None:
        before = report([0.3] * 10)
        after = report([0.1 + 0.2] * 10)

        assert bench.compare(before, after).verdict == "inconclusive"

    def test_a_vanishing_constant_gain_is_inconclusive(self) -> None:
        assert bench.compare(report([0.5] * 10), report([0.5 + 1e-12] * 10)).verdict == (
            "inconclusive"
        )

    def test_a_report_with_a_repeated_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="result ids must be unique; 1 repeated"):
            report([0.1, 0.2], ids=["a", "a"])

    @pytest.mark.parametrize(
        "change",
        [
            {"verdict": "better", "low": -0.9, "high": -0.1, "mean_difference": -0.5},
            {"low": 0.2, "high": 0.1},
            {"resamples": 1},
            {"n": 9},
            {"confidence": 0.3},
            {"metric": "x\n# HEADING"},
            {"metric": "x ndcg@10"},
            {"metric": "ndcg@10<script>"},
            {"seed": True},
            {"confidence": 0.999, "resamples": 100},
        ],
        ids=[
            "verdict-against-interval",
            "low-above-high",
            "resamples",
            "n",
            "confidence",
            "metric",
            "metric-prefix",
            "metric-suffix",
            "boolean-seed",
            "too-few-resamples",
        ],
    )
    def test_a_comparison_read_back_must_agree_with_itself(self, change: dict[str, object]) -> None:
        fields: dict[str, object] = {
            "metric": "ndcg@10",
            "n": 10,
            "mean_difference": 0.15,
            "low": 0.1,
            "high": 0.2,
            "confidence": 0.95,
            "resamples": 1000,
            "seed": 0,
            "verdict": "better",
        }
        fields.update(change)

        with pytest.raises(ValueError):
            bench.Comparison.model_validate(fields)

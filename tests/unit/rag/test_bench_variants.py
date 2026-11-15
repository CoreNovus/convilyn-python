"""``bench.compare_variants``: every variant against the first, at a family confidence."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from convilyn.rag import bench


def report(values: Sequence[float], *, k: tuple[int, ...] = (10,)) -> bench.Report:
    """A report whose every metric for question i is values[i]."""
    names = [f"{metric}@{cut}" for metric in ("recall", "mrr", "ndcg") for cut in k]
    results = tuple(
        bench.QuestionResult(
            id=f"q{index}",
            group=None,
            answerable=True,
            scores=dict.fromkeys(names, value),
            latency_ms=float(index + 1),
        )
        for index, value in enumerate(values)
    )
    return bench.Report(k=k, cover=0.5, results=results)


BASE = report([0.5] * 30)
WORSE = report([0.2] * 30)
BETTER = report([0.8] * 30)


class TestVerdicts:
    def test_each_variant_after_the_first_gets_its_own_verdict(self) -> None:
        variants = bench.compare_variants({"base": BASE, "worse": WORSE, "better": BETTER})

        verdicts = {name: c.verdict for name, c in variants.comparisons.items()}

        assert verdicts == {"worse": "worse", "better": "better"}

    def test_the_first_report_is_the_baseline(self) -> None:
        variants = bench.compare_variants({"worse": WORSE, "base": BASE})

        assert variants.baseline == "worse"

    def test_each_comparison_matches_compare_at_the_adjusted_confidence(self) -> None:
        variants = bench.compare_variants({"base": BASE, "worse": WORSE, "better": BETTER})

        assert variants.comparisons["worse"] == bench.compare(BASE, WORSE, confidence=0.975)

    def test_the_metric_compared_can_be_chosen(self) -> None:
        variants = bench.compare_variants({"base": BASE, "worse": WORSE}, metric="recall@10")

        assert variants.comparisons["worse"].metric == "recall@10"


class TestFamilyConfidence:
    @pytest.mark.parametrize(
        ("count", "each"),
        [(2, 0.95), (3, 0.975), (5, 0.9875)],
        ids=["one-comparison-unchanged", "two-comparisons", "four-comparisons"],
    )
    def test_each_comparison_takes_a_bonferroni_share(self, count: int, each: float) -> None:
        reports = {f"v{index}": BASE for index in range(count)}

        variants = bench.compare_variants(reports)

        assert [c.confidence for c in variants.comparisons.values()] == pytest.approx(
            [each] * (count - 1)
        )

    def test_the_family_confidence_is_kept_as_asked(self) -> None:
        variants = bench.compare_variants({"a": BASE, "b": WORSE, "c": BETTER}, confidence=0.9)

        assert variants.confidence == 0.9

    def test_the_lowest_confidence_is_accepted(self) -> None:
        variants = bench.compare_variants({"a": BASE, "b": WORSE}, confidence=0.5)

        assert variants.comparisons["b"].confidence == 0.5

    def test_too_many_variants_for_the_resamples_is_refused(self) -> None:
        reports = {f"v{index}": BASE for index in range(12)}

        with pytest.raises(ValueError, match="too few resamples"):
            bench.compare_variants(reports, resamples=100)


class TestPlan:
    def test_too_few_answerable_questions_are_refused_before_comparing(self) -> None:
        with pytest.raises(ValueError, match="share 9 answerable questions"):
            bench.compare_variants({"a": report([0.5] * 9), "b": report([0.2] * 9)})

    def test_exactly_the_fewest_answerable_questions_are_compared(self) -> None:
        variants = bench.compare_variants({"a": report([0.5] * 10), "b": report([0.2] * 10)})

        assert variants.comparisons["b"].n == 10

    @pytest.mark.parametrize("resamples", [99, 1_000_001, 1.5], ids=["below", "above", "float"])
    def test_resamples_outside_their_range_are_refused(self, resamples: object) -> None:
        with pytest.raises(ValueError, match="resamples must be an integer"):
            bench.compare_variants({"a": BASE, "b": WORSE}, resamples=resamples)  # type: ignore[arg-type]


class TestRefusals:
    def test_a_single_report_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least two reports"):
            bench.compare_variants({"base": BASE})

    def test_no_reports_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least two reports"):
            bench.compare_variants({})

    def test_a_sequence_instead_of_a_mapping_is_refused(self) -> None:
        with pytest.raises(TypeError, match="must map each variant"):
            bench.compare_variants([BASE, WORSE])  # type: ignore[arg-type]

    @pytest.mark.parametrize("name", ["", 3], ids=["empty", "not-a-string"])
    def test_a_name_that_is_not_a_non_empty_string_is_refused(self, name: object) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            bench.compare_variants({"base": BASE, name: WORSE})  # type: ignore[dict-item]

    def test_something_that_is_not_a_report_is_refused_by_position(self) -> None:
        with pytest.raises(TypeError, match="report 2 is not a bench Report"):
            bench.compare_variants({"base": BASE, "other": {"k": [10]}})  # type: ignore[dict-item]

    def test_a_report_without_the_metric_is_refused_by_position(self) -> None:
        with pytest.raises(ValueError, match="report 2 has no metric ndcg@10"):
            bench.compare_variants({"base": BASE, "other": report([0.5] * 30, k=(5,))})

    @pytest.mark.parametrize(
        "confidence", [1, 0.49, True, "0.95"], ids=["one", "below-half", "bool", "text"]
    )
    def test_a_confidence_outside_its_range_is_refused(self, confidence: object) -> None:
        with pytest.raises(ValueError, match="confidence must be a number"):
            bench.compare_variants({"a": BASE, "b": WORSE}, confidence=confidence)  # type: ignore[arg-type]

    def test_reports_over_different_questions_are_refused(self) -> None:
        with pytest.raises(ValueError, match="cover different questions"):
            bench.compare_variants({"a": BASE, "b": report([0.5] * 31)})


class TestReadBack:
    def test_variants_survive_a_json_round_trip(self) -> None:
        variants = bench.compare_variants({"base": BASE, "worse": WORSE, "better": BETTER})

        again = bench.Variants.model_validate_json(variants.model_dump_json())

        assert again == variants

    def test_the_order_of_variants_survives_a_json_round_trip(self) -> None:
        variants = bench.compare_variants({"z": BASE, "a": WORSE})

        again = bench.Variants.model_validate_json(variants.model_dump_json())

        assert list(again.reports) == ["z", "a"]

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda data: data["reports"].pop("better"), "each variant after the first"),
            (lambda data: data["comparisons"].pop("better"), "each variant after the first"),
            (
                lambda data: (data["reports"].pop("worse"), data["reports"].pop("better")),
                "at least two variants",
            ),
            (lambda data: data.update(confidence=0.9), "confidence each one takes"),
            (lambda data: data.update(metric="recall@10"), "on the metric compared"),
        ],
        ids=[
            "report-dropped",
            "comparison-dropped",
            "one-variant",
            "family-confidence-edited",
            "metric-edited",
        ],
    )
    def test_an_edited_record_is_refused(self, change: object, message: str) -> None:
        data = bench.compare_variants({"base": BASE, "worse": WORSE, "better": BETTER}).model_dump(
            mode="json"
        )
        change(data)  # type: ignore[operator]

        with pytest.raises(ValidationError, match=message):
            bench.Variants.model_validate(data)


class TestMarkdown:
    def test_one_comparison_states_the_confidence_without_adjustment(self) -> None:
        text = bench.compare_variants({"base": BASE, "worse": WORSE}).to_markdown()

        assert "at 95% confidence." in text

    def test_several_comparisons_state_the_adjusted_confidence(self) -> None:
        text = bench.compare_variants(
            {"base": BASE, "worse": WORSE, "better": BETTER}
        ).to_markdown()

        assert "97.5% confidence each, so that all 2 hold together at 95% (Bonferroni)" in text

    def test_the_baseline_row_has_no_verdict(self) -> None:
        text = bench.compare_variants({"base": BASE, "worse": WORSE}).to_markdown()

        assert "| base | 0.500 | 0.500 | 0.500 | 29.0 | baseline |  |" in text

    def test_a_variant_row_carries_its_verdict_and_interval(self) -> None:
        text = bench.compare_variants({"base": BASE, "worse": WORSE}).to_markdown()

        assert (
            "| worse | 0.200 | 0.200 | 0.200 | 29.0 | **worse** | -0.300 (-0.300 to -0.300) |"
            in (text)
        )

    def test_a_metric_beyond_the_largest_cut_gets_its_own_column(self) -> None:
        reports = {"a": report([0.5] * 30, k=(5, 10)), "b": report([0.2] * 30, k=(5, 10))}

        text = bench.compare_variants(reports, metric="mrr@5").to_markdown()

        assert "| variant | mrr@5 | recall@10 | mrr@10 | ndcg@10 | latency_p95_ms |" in text

    def test_a_variant_name_cannot_add_a_column(self) -> None:
        text = bench.compare_variants({"base": BASE, "a|b": WORSE}).to_markdown()

        assert "| a\\|b |" in text

    def test_the_same_variants_give_the_same_text(self) -> None:
        reports = {"base": BASE, "worse": WORSE, "better": BETTER}

        assert (
            bench.compare_variants(reports).to_markdown()
            == bench.compare_variants(reports).to_markdown()
        )

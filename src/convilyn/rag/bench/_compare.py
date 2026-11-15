"""Whether one report is better than another, beyond the noise of its questions.

A paired bootstrap over the questions both reports answered: resample the
per-question differences with replacement, take the mean each time, and read
the interval the middle ``confidence`` of those means spans. Standard library
only, with a fixed seed, so the same reports give the same verdict every run.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from convilyn.rag.bench.types import (
    MAX_RESAMPLES,
    MIN_PAIRS,
    MIN_RESAMPLES,
    SAME,
    Comparison,
    Report,
    Variants,
    each_confidence,
    metric_names,
    verdict_of,
)


def compare(
    before: Report,
    after: Report,
    *,
    metric: str = "ndcg@10",
    resamples: int = 10_000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Comparison:
    """Compare ``after`` with ``before`` on ``metric``, question by question.

    The reports must cover the same answerable questions (by ``id``), at
    least ``MIN_PAIRS`` (10) of them: fewer cannot carry a verdict, and a
    question one side lacks is not dropped quietly, since that changes what
    is compared. ``metric`` is a retrieval metric both reports carry, such as
    ``"recall@5"``; latency is not compared here, as lower is better there.
    A per-question difference within ``SAME`` (1e-9) of zero counts as zero,
    so arithmetic rounding cannot make a verdict. The verdict is
    ``"better"`` when the whole interval of the mean difference is above 0,
    ``"worse"`` when it is below, and ``"inconclusive"`` when it contains 0.
    """
    _check_options(metric, resamples, seed, confidence, before, after)
    first = {result.id: result for result in before.results if result.answerable}
    second = {result.id: result for result in after.results if result.answerable}
    if first.keys() != second.keys():
        only_before = len(first.keys() - second.keys())
        only_after = len(second.keys() - first.keys())
        raise ValueError(
            "the reports cover different questions: "
            f"{only_before} answered only before, {only_after} only after"
        )
    if len(first) < MIN_PAIRS:
        raise ValueError(
            f"the reports share {len(first)} answerable questions; a comparison needs at "
            f"least {MIN_PAIRS}"
        )
    differences = [
        _settled(second[key].scores[metric] - first[key].scores[metric]) for key in sorted(first)
    ]
    mean = sum(differences) / len(differences)
    low, high = _interval(differences, resamples, random.Random(seed), confidence)
    return Comparison(
        metric=metric,
        n=len(differences),
        mean_difference=mean,
        low=low,
        high=high,
        confidence=confidence,
        resamples=resamples,
        seed=seed,
        verdict=verdict_of(low, high),
    )


def compare_variants(
    reports: Mapping[str, Report],
    *,
    metric: str = "ndcg@10",
    resamples: int = 10_000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Variants:
    """Compare every variant with the first, ``reports``' baseline.

    ``reports`` maps a variant's name to its report, at least two, in the
    order to show them. Each variant after the first is compared with the
    first by :func:`compare`. With more than one comparison, each is made at
    a higher confidence (Bonferroni: ``1 - (1 - confidence) / comparisons``)
    so that ``confidence`` holds for all of them at once: otherwise, the more
    variants tried, the likelier one is called better by chance.
    """
    if not isinstance(reports, Mapping):
        raise TypeError("reports must map each variant's name to its bench Report")
    named = dict(reports)
    if len(named) < 2:
        raise ValueError("compare_variants needs at least two reports: a baseline and a variant")
    for position, (name, report) in enumerate(named.items(), start=1):
        if not isinstance(name, str) or not name:
            raise ValueError("each variant's name must be a non-empty string")
        if not isinstance(report, Report):
            raise TypeError(f"report {position} is not a bench Report")
        if metric not in metric_names(report.k):
            raise ValueError(
                f"report {position} has no metric {metric}; "
                f"it has: {', '.join(metric_names(report.k))}"
            )
    baseline, *others = named
    answerable = sum(result.answerable for result in named[baseline].results)
    check_plan(len(named), answerable, resamples=resamples, confidence=confidence)
    each = each_confidence(confidence, len(named) - 1)
    comparisons = {
        name: compare(
            named[baseline],
            named[name],
            metric=metric,
            resamples=resamples,
            seed=seed,
            confidence=each,
        )
        for name in others
    }
    return Variants(metric=metric, confidence=confidence, reports=named, comparisons=comparisons)


def check_plan(
    variants: int, answerable: int, *, resamples: int = 10_000, confidence: float = 0.95
) -> None:
    """Refuse a comparison of ``variants`` (two or more) that could not be made.

    A run can call paid services, so the command line checks this before
    running anything: enough answerable questions, and ``resamples`` enough
    to carry each comparison's adjusted confidence.
    """
    if answerable < MIN_PAIRS:
        raise ValueError(
            f"the reports share {answerable} answerable questions; a comparison needs at "
            f"least {MIN_PAIRS}"
        )
    _check_resamples(resamples)
    _check_confidence(confidence)
    if math.floor((1 - each_confidence(confidence, variants - 1)) / 2 * resamples) < 1:
        raise ValueError(
            "too few resamples for this confidence across this many variants; "
            "raise resamples or compare fewer variants"
        )


def _settled(difference: float) -> float:
    """``difference``, or 0 when it is within rounding of 0."""
    return 0.0 if abs(difference) <= SAME else difference


def _check_options(
    metric: str, resamples: int, seed: int, confidence: float, before: Report, after: Report
) -> None:
    for name, report in (("before", before), ("after", after)):
        if not isinstance(report, Report):
            raise TypeError(f"{name} must be a bench Report")
        if metric not in metric_names(report.k):
            raise ValueError(
                f"{name} has no metric {metric}; it has: {', '.join(metric_names(report.k))}"
            )
    _check_resamples(resamples)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    _check_confidence(confidence)
    if math.floor((1 - confidence) / 2 * resamples) < 1:
        # Otherwise the interval is just the extremes, short of the confidence asked for.
        raise ValueError("too few resamples for this confidence; raise resamples")


def _check_resamples(resamples: int) -> None:
    if type(resamples) is not int or not MIN_RESAMPLES <= resamples <= MAX_RESAMPLES:
        raise ValueError(
            f"resamples must be an integer from {MIN_RESAMPLES:,} to {MAX_RESAMPLES:,}"
        )


def _check_confidence(confidence: float) -> None:
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not 0.5 <= confidence < 1
    ):
        raise ValueError("confidence must be a number from 0.5 up to, not including, 1")


def _interval(
    differences: list[float], resamples: int, rng: random.Random, confidence: float
) -> tuple[float, float]:
    """The percentile interval of the resampled means."""
    n = len(differences)
    means = sorted(sum(rng.choices(differences, k=n)) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    low = means[math.floor(tail * resamples)]
    high = means[max(0, math.ceil((1 - tail) * resamples) - 1)]
    return low, high

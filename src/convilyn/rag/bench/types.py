"""The data a bench reads and writes: questions, per-question results, a report."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from convilyn.rag._normalize import normalize
from convilyn.rag.bench import _bounds, _text

#: Fewest answerable questions two reports must share to be compared.
MIN_PAIRS = 10
#: Resamples a comparison takes, at least and at most.
MIN_RESAMPLES, MAX_RESAMPLES = 100, 1_000_000
#: A per-question difference this close to 0 counts as 0 (float rounding).
SAME = 1e-9

_METRIC = re.compile(r"^(?:recall|mrr|ndcg)@[1-9][0-9]*$")

Verdict = Literal["better", "worse", "inconclusive"]


def verdict_of(low: float, high: float) -> Verdict:
    """The verdict an interval of the mean difference gives."""
    return "better" if low > 0 else "worse" if high < 0 else "inconclusive"


def each_confidence(confidence: float, comparisons: int) -> float:
    """The confidence each of ``comparisons`` takes so all hold at ``confidence``.

    Bonferroni: the chance that any one interval misses is at most the sum of
    each one's, so splitting ``1 - confidence`` evenly bounds the whole family.
    """
    return 1 - (1 - confidence) / comparisons


def folded(text: str) -> str:
    """``text`` as matching sees it: folded by ``rag.normalize``, spacing removed."""
    return "".join(normalize(text).split())


class Support(BaseModel):
    """Where an answer is: a document, and optionally the words in it.

    With an ``excerpt``, a passage supports the item only when it covers
    enough of those words; without one, any passage of the document does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    excerpt: str | None = Field(default=None, min_length=1)

    @field_validator("excerpt")
    @classmethod
    def _matchable(cls, excerpt: str | None) -> str | None:
        # Matching compares pairs of characters once folded; an excerpt with
        # fewer than two could never be found, and would fail a gate for a
        # reason that has nothing to do with the retriever.
        if excerpt is not None and len(folded(excerpt)) < 2:
            raise ValueError("an excerpt must hold at least two characters once folded")
        return excerpt


class Question(BaseModel):
    """One question of a set, as one line of a question file.

    A question with no ``support`` has no answer in the documents: it is run
    (its latency counts) but left out of the retrieval metrics. A ``"draft"``
    is not run at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    group: str | None = Field(default=None, min_length=1)
    support: tuple[Support, ...] = ()
    status: Literal["ready", "draft"] = "ready"


class QuestionResult(BaseModel):
    """How one question did. ``scores`` is empty for a question with no answer."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: str
    group: str | None
    answerable: bool
    scores: dict[str, Annotated[float, Field(ge=0, le=1)]]
    latency_ms: float = Field(ge=0)


class Check(BaseModel):
    """The outcome of :meth:`Report.check`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: tuple[str, ...] = ()
    breaches: tuple[str, ...] = ()
    not_measurable: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Every bound was measured and met. A bound that could not be
        measured fails: a check must not pass on nothing."""
        return not self.breaches and not self.not_measurable

    def message(self) -> str:
        """The failures, one per line."""
        lines = ["bench bounds not met:"]
        lines += [f"  {breach}" for breach in self.breaches]
        lines += [f"  {name}: not measurable on this question set" for name in self.not_measurable]
        return "\n".join(lines)


class Report(BaseModel):
    """What a bench run found, question by question, and its averages."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    k: tuple[Annotated[int, Field(gt=0)], ...] = Field(min_length=1)
    cover: float = Field(gt=0, le=1)
    results: tuple[QuestionResult, ...]
    drafts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Report:
        # A report read back from JSON is checked like one a run made, so a
        # hand-edited or damaged one cannot pass a bound it would not have.
        if len(set(self.k)) != len(self.k) or list(self.k) != sorted(self.k):
            raise ValueError("k must be distinct and in ascending order")
        names = set(metric_names(self.k))
        for result in self.results:
            if set(result.scores) != (names if result.answerable else set()):
                raise ValueError("every answerable result scores exactly the metrics of k")
        repeated = len(self.results) - len({result.id for result in self.results})
        if repeated:
            # A run never repeats an id (question files refuse it); one read
            # back with a repeat would be compared with a question missing.
            raise ValueError(f"result ids must be unique; {repeated} repeated")
        return self

    @property
    def metrics(self) -> dict[str, float | None]:
        """Averages over the answerable questions, and latency over all.

        A metric is ``None`` when no question could carry it.
        """
        return _summary(self.results, self.k)

    @property
    def groups(self) -> dict[str, dict[str, float | None]]:
        """:attr:`metrics` for each ``group`` the questions name."""
        names = sorted({result.group for result in self.results if result.group is not None})
        return {
            name: _summary([r for r in self.results if r.group == name], self.k) for name in names
        }

    def check(self, **bounds: float) -> Check:
        """Check ``min_<metric>`` / ``max_<metric>`` bounds without raising.

        A metric's name in a bound writes ``@`` as ``_at_``:
        ``min_ndcg_at_10=0.85``, ``max_latency_p95_ms=300``. An unknown name
        raises ``ValueError`` and lists the known ones.
        """
        passed, breaches, unmeasured = _bounds.check(self.metrics, bounds)
        return Check(passed=passed, breaches=breaches, not_measurable=unmeasured)

    def require(self, **bounds: float) -> None:
        """Like :meth:`check`, raising ``AssertionError`` with the gaps."""
        outcome = self.check(**bounds)
        if not outcome.ok:
            raise AssertionError(outcome.message())

    def to_markdown(self) -> str:
        """The report as Markdown, the same text for the same report."""
        return _text.report(
            self.metrics,
            self.groups,
            run=len(self.results),
            answerable=sum(result.answerable for result in self.results),
            drafts=self.drafts,
            k=self.k,
            cover=self.cover,
        )


class Comparison(BaseModel):
    """The outcome of :func:`~convilyn.rag.bench.compare`, with what it rests on.

    ``low`` and ``high`` bound the mean difference (after minus before) at
    ``confidence``; with the same ``seed`` and ``resamples`` the same reports
    give the same numbers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    metric: str = Field(pattern=_METRIC.pattern)
    n: int = Field(ge=MIN_PAIRS)
    mean_difference: float
    low: float
    high: float
    confidence: float = Field(ge=0.5, lt=1)
    resamples: int = Field(ge=MIN_RESAMPLES, le=MAX_RESAMPLES)
    seed: int = Field(strict=True)
    verdict: Verdict

    @model_validator(mode="after")
    def _consistent(self) -> Comparison:
        # Read back from JSON, a comparison must still say what its numbers say.
        if self.low > self.high:
            raise ValueError("low must not be above high")
        if self.verdict != verdict_of(self.low, self.high):
            raise ValueError("the verdict must follow from the interval")
        if math.floor((1 - self.confidence) / 2 * self.resamples) < 1:
            raise ValueError("too few resamples for this confidence")
        return self

    def to_markdown(self) -> str:
        """The comparison as Markdown: the verdict, then the numbers."""
        return _text.comparison(
            metric=self.metric,
            verdict=self.verdict,
            mean=self.mean_difference,
            low=self.low,
            high=self.high,
            n=self.n,
            confidence=self.confidence,
            resamples=self.resamples,
            seed=self.seed,
        )


class Variants(BaseModel):
    """The outcome of :func:`~convilyn.rag.bench.compare_variants`.

    ``reports`` holds each variant's report in the order given; the first is
    the baseline. ``comparisons`` holds, for every other variant in the same
    order, its :class:`Comparison` with the baseline, each at the confidence
    :attr:`each_confidence` so that all of them hold at ``confidence``
    together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    metric: str = Field(pattern=_METRIC.pattern)
    confidence: float = Field(ge=0.5, lt=1)
    reports: dict[Annotated[str, Field(min_length=1)], Report]
    comparisons: dict[str, Comparison]

    @model_validator(mode="after")
    def _consistent(self) -> Variants:
        # Read back from JSON, the comparisons must still be the ones the
        # reports and the family's confidence call for.
        names = list(self.reports)
        if len(names) < 2:
            raise ValueError("there must be at least two variants: a baseline and another")
        if list(self.comparisons) != names[1:]:
            raise ValueError("each variant after the first needs one comparison, in order")
        for comparison in self.comparisons.values():
            if comparison.metric != self.metric:
                raise ValueError("every comparison must be on the metric compared")
            if not math.isclose(comparison.confidence, self.each_confidence):
                raise ValueError("every comparison must be at the confidence each one takes")
        return self

    @property
    def baseline(self) -> str:
        """The name of the variant the others are compared with."""
        return next(iter(self.reports))

    @property
    def each_confidence(self) -> float:
        """The confidence each comparison is made at (Bonferroni)."""
        return each_confidence(self.confidence, len(self.reports) - 1)

    def to_markdown(self) -> str:
        """The variants as Markdown: one row each, with its verdict."""
        baseline = self.reports[self.baseline]
        family = ("recall", "mrr", "ndcg")
        columns = [self.metric]
        columns += [f"{name}@{max(baseline.k)}" for name in family]
        columns = [*dict.fromkeys(columns), "latency_p95_ms"]
        rows = []
        for name, report in self.reports.items():
            metrics = report.metrics
            comparison = self.comparisons.get(name)
            outcome = (
                None
                if comparison is None
                else (
                    comparison.verdict,
                    comparison.mean_difference,
                    comparison.low,
                    comparison.high,
                )
            )
            rows.append((name, [metrics.get(column) for column in columns], outcome))
        first = next(iter(self.comparisons.values()))
        return _text.variants(
            metric=self.metric,
            columns=columns,
            rows=rows,
            n=first.n,
            confidence=self.confidence,
            each=self.each_confidence,
            resamples=first.resamples,
            seed=first.seed,
        )


def _summary(results: Sequence[QuestionResult], k: tuple[int, ...]) -> dict[str, float | None]:
    answered = [result for result in results if result.answerable]
    summary: dict[str, float | None] = {}
    for name in metric_names(k):
        values = [result.scores[name] for result in answered]
        summary[name] = sum(values) / len(values) if values else None
    latencies = sorted(result.latency_ms for result in results)
    summary["latency_p50_ms"] = _percentile(latencies, 50)
    summary["latency_p95_ms"] = _percentile(latencies, 95)
    return summary


def metric_names(k: tuple[int, ...]) -> list[str]:
    """The retrieval metrics a run with cut-offs ``k`` reports, in order."""
    return [f"{metric}@{cut}" for metric in ("recall", "mrr", "ndcg") for cut in k]


def _percentile(ordered: Sequence[float], percent: int) -> float | None:
    """The nearest-rank percentile of ``ordered``; ``None`` for no values."""
    if not ordered:
        return None
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


_DIGEST = r"^[0-9a-f]{64}$"


class Pin(BaseModel):
    """A report held as the standard later runs are checked against.

    Besides the report, it records what makes two runs comparable: a digest
    of the question set, and each document's ``(source_id, digest)``
    (:attr:`convilyn.rag.SourceInfo.digest`, independent of how the text was
    cut). The settings file's digest is recorded too, to say whether the
    settings changed, which a check is there to judge, not to refuse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Version of this layout; a pin of any other is refused.
    format: Literal[1] = 1
    metric: str = Field(pattern=_METRIC.pattern)
    report: Report
    questions: str = Field(pattern=_DIGEST)
    corpus: tuple[tuple[str, Annotated[str, Field(pattern=_DIGEST)]], ...]
    settings: str | None = Field(default=None, pattern=_DIGEST)

    @model_validator(mode="after")
    def _consistent(self) -> Pin:
        if self.metric not in metric_names(self.report.k):
            raise ValueError("the pin's metric must be one its report carries")
        ids = [source_id for source_id, _ in self.corpus]
        if ids != sorted(set(ids)):
            raise ValueError("the corpus must list each document once, in order")
        return self


class PinCheck(BaseModel):
    """The outcome of :func:`~convilyn.rag.bench.check`.

    ``held`` and ``worse`` carry the comparison they rest on;
    ``not_comparable`` says why in ``reasons`` instead, and never passes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["held", "worse", "not_comparable"]
    reasons: tuple[str, ...] = ()
    comparison: Comparison | None = None
    #: Whether the settings file differs from the pinned one; ``None`` when
    #: either side has none to compare.
    settings_changed: bool | None = None

    def to_markdown(self) -> str:
        """The outcome as Markdown: the verdict line, then why."""
        if self.comparison is not None:
            body = self.comparison.to_markdown()
        else:
            body = "".join(f"- {reason}\n" for reason in self.reasons)
        changed = {True: "changed", False: "unchanged", None: "not recorded"}[self.settings_changed]
        return f"# Pin check: {self.outcome}\n\nSettings since the pin: {changed}.\n\n{body}"


__all__ = [
    "Check",
    "Comparison",
    "Pin",
    "PinCheck",
    "Question",
    "QuestionResult",
    "Report",
    "Support",
    "Variants",
]

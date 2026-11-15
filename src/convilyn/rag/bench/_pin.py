"""Holding retrieval to a standard: pin a report, then check later runs against it.

A check compares like with like or says it cannot: when the questions, the
documents, ``k`` or ``cover`` differ from the pin, the outcome is
``not_comparable``, which a CI gate treats as a failure. When they match, the
pinned and the current report are compared question by question
(:func:`~convilyn.rag.bench.compare`), and ``worse`` is a verdict the
bootstrap gives, not a dip in an average.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

from convilyn.rag.bench import _questions
from convilyn.rag.bench._compare import compare
from convilyn.rag.bench.types import Pin, PinCheck, Question, Report
from convilyn.rag.types import SourceInfo

Questions = str | os.PathLike[str] | Sequence[Question]
Settings = str | os.PathLike[str] | None


def pin(
    report: Report,
    *,
    questions: Questions,
    sources: Sequence[SourceInfo],
    settings: Settings = None,
    metric: str = "ndcg@10",
) -> Pin:
    """Hold ``report`` as the standard for the same questions and documents.

    ``questions`` is what the report was run on (a file or the questions);
    ``sources`` is the corpus's :meth:`~convilyn.rag.Corpus.sources`;
    ``settings`` is the settings file the corpus was opened from, if any.
    """
    return Pin(
        metric=metric,
        report=report,
        questions=_question_digest(questions),
        corpus=_fingerprint(sources),
        settings=_settings_digest(settings),
    )


def check(
    standard: Pin,
    report: Report,
    *,
    questions: Questions,
    sources: Sequence[SourceInfo],
    settings: Settings = None,
) -> PinCheck:
    """Check ``report`` against ``standard``: held, worse, or not comparable."""
    reasons = _differences(standard, report, questions, sources)
    current = _settings_digest(settings)
    changed = None if standard.settings is None or current is None else standard.settings != current
    if reasons:
        return PinCheck(outcome="not_comparable", reasons=tuple(reasons), settings_changed=changed)
    try:
        comparison = compare(standard.report, report, metric=standard.metric)
    except ValueError as error:
        # Too few questions for a verdict: say so rather than pass.
        return PinCheck(outcome="not_comparable", reasons=(str(error),), settings_changed=changed)
    outcome = "worse" if comparison.verdict == "worse" else "held"
    return PinCheck(outcome=outcome, comparison=comparison, settings_changed=changed)


def _differences(
    standard: Pin, report: Report, questions: Questions, sources: Sequence[SourceInfo]
) -> list[str]:
    """What differs from the pin, in words that name no question or document."""
    reasons: list[str] = []
    if _question_digest(questions) != standard.questions:
        reasons.append("the question set changed since the pin")
    pinned, now = dict(standard.corpus), dict(_fingerprint(sources))
    added = len(now.keys() - pinned.keys())
    removed = len(pinned.keys() - now.keys())
    edited = sum(1 for key in pinned.keys() & now.keys() if pinned[key] != now[key])
    if added or removed or edited:
        reasons.append(
            f"the documents changed since the pin: {added} added, {removed} removed, "
            f"{edited} edited"
        )
    if report.k != standard.report.k:
        reasons.append("k differs from the pin's")
    if report.cover != standard.report.cover:
        reasons.append("cover differs from the pin's")
    return reasons


def _question_digest(questions: Questions) -> str:
    """A digest of the question set that ignores the order of lines."""
    if isinstance(questions, str | os.PathLike):
        loaded = _questions.questions(Path(questions))
    else:
        loaded = tuple(questions)
        if not all(isinstance(question, Question) for question in loaded):
            raise ValueError("questions must be a question file's path or a sequence of Question")
    lines = sorted(question.model_dump_json() for question in loaded)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _fingerprint(sources: Sequence[SourceInfo]) -> tuple[tuple[str, str], ...]:
    if not all(isinstance(source, SourceInfo) for source in sources):
        raise ValueError("sources must be the corpus's sources()")
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("sources must list each source_id once")
    return tuple(sorted((source.source_id, source.digest) for source in sources))


def _settings_digest(settings: Settings) -> str | None:
    if settings is None:
        return None
    return hashlib.sha256(Path(settings).read_bytes()).hexdigest()

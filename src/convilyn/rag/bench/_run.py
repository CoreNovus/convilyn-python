"""Running a question set through any retriever.

``retrieve`` is whatever answers a question: a corpus's ``search``, or a
function around someone else's retrieval. It is called once per question, in
order, and timed; nothing else is called and nothing is sent anywhere.
"""

from __future__ import annotations

import inspect
import itertools
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from convilyn.rag.bench import _questions, _score
from convilyn.rag.bench.types import Question, QuestionResult, Report
from convilyn.rag.types import Hit, SearchResult

Questions = str | os.PathLike[str] | Sequence[Question]

_SHAPE = (
    "retrieve must return a SearchResult, a sequence of Hits, or a sequence of "
    "mappings with source_id and text"
)


def run(
    retrieve: Callable[[str], Any],
    questions: Questions,
    *,
    k: Sequence[int] = (1, 5, 10),
    cover: float = 0.5,
) -> Report:
    """Run every question through ``retrieve`` and score what it returns.

    ``retrieve(question)`` returns a :class:`~convilyn.rag.SearchResult`, a
    sequence of :class:`~convilyn.rag.Hit`, or mappings with ``source_id``
    and ``text``, best first; only the first ``max(k)`` are read.
    ``questions`` is a question file's path or the questions themselves.
    ``cover`` is the share of an excerpt's character pairs a passage must
    hold to support it. Use :func:`arun` for an ``async`` retrieve.
    """
    cuts, chosen, drafts = _prepared(questions, k, cover)
    if inspect.iscoroutinefunction(retrieve):
        raise TypeError("retrieve is async; use bench.arun")
    results = []
    for question in chosen:
        started = time.perf_counter()
        answer = retrieve(question.question)
        elapsed = time.perf_counter() - started
        if inspect.isawaitable(answer):
            _discard(answer)
            raise TypeError("retrieve returned an awaitable; use bench.arun")
        results.append(_result(question, answer, elapsed, cuts, cover))
    return Report(k=cuts, cover=cover, results=tuple(results), drafts=drafts)


async def arun(
    retrieve: Callable[[str], Any],
    questions: Questions,
    *,
    k: Sequence[int] = (1, 5, 10),
    cover: float = 0.5,
) -> Report:
    """:func:`run` for async code; ``retrieve`` may be sync or ``async``.

    Questions run one at a time, so each latency is the retriever's own.
    """
    cuts, chosen, drafts = _prepared(questions, k, cover)
    results = []
    for question in chosen:
        started = time.perf_counter()
        answer = retrieve(question.question)
        if inspect.isawaitable(answer):
            answer = await answer
        elapsed = time.perf_counter() - started
        results.append(_result(question, answer, elapsed, cuts, cover))
    return Report(k=cuts, cover=cover, results=tuple(results), drafts=drafts)


def _prepared(
    questions: Questions, k: Sequence[int], cover: float
) -> tuple[tuple[int, ...], list[Question], int]:
    """Checked cut-offs, the questions to run, and how many drafts were left out."""
    cuts = () if isinstance(k, str | bytes) or not isinstance(k, Iterable) else tuple(k)
    if (
        not cuts
        or not all(type(cut) is int and cut > 0 for cut in cuts)
        or len(set(cuts)) != len(cuts)
    ):
        raise ValueError("k must be distinct positive integers, such as (1, 5, 10)")
    if isinstance(cover, bool) or not isinstance(cover, int | float) or not 0 < cover <= 1:
        raise ValueError("cover must be a number above 0 and at most 1")
    if isinstance(questions, str | os.PathLike):
        loaded = _questions.questions(Path(questions))
    else:
        loaded = tuple(questions)
        if not loaded or not all(isinstance(question, Question) for question in loaded):
            raise ValueError("questions must be a question file's path or a sequence of Question")
        _questions.unique(loaded, where="question ", offset=0)
    chosen = [question for question in loaded if question.status == "ready"]
    return tuple(sorted(cuts)), chosen, len(loaded) - len(chosen)


def _result(
    question: Question, answer: object, elapsed: float, cuts: tuple[int, ...], cover: float
) -> QuestionResult:
    hits = _hits(answer, max(cuts))
    scores = (
        _score.scores(_score.first_ranks(question.support, hits, cover), cuts)
        if question.support
        else {}
    )
    return QuestionResult(
        id=question.id,
        group=question.group,
        answerable=bool(question.support),
        scores=scores,
        latency_ms=elapsed * 1000,
    )


def _hits(answer: object, limit: int) -> list[tuple[str, str]]:
    """The first ``limit`` hits of ``answer`` as ``(source_id, text)``."""
    if inspect.isawaitable(answer):
        # An `async` retrieve that answered with another awaitable.
        _discard(answer)
        raise TypeError(_SHAPE)
    items = answer.hits if isinstance(answer, SearchResult) else answer
    if isinstance(items, str | bytes | Mapping) or not isinstance(items, Iterable):
        raise TypeError(_SHAPE)
    hits: list[tuple[str, str]] = []
    for item in itertools.islice(items, limit):
        if isinstance(item, Hit):
            hits.append((item.passage.source_id, item.passage.text))
        elif (
            isinstance(item, Mapping)
            and isinstance(source_id := item.get("source_id"), str)
            and isinstance(text := item.get("text"), str)
        ):
            hits.append((source_id, text))
        else:
            raise TypeError(_SHAPE)
    return hits


def _discard(answer: Awaitable[object]) -> None:
    """Stop an awaitable that will not be awaited: close a coroutine so it
    does not warn, cancel a task or future so it does not run on."""
    for name in ("close", "cancel"):
        stop = getattr(answer, name, None)
        if callable(stop):
            stop()
            return

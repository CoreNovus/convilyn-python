"""Measure retrieval: how often the passages that answer a question come back.

Works on any retriever, not only a :mod:`convilyn.rag` corpus, and needs no
key and no service of its own::

    from convilyn import rag
    from convilyn.rag import bench

    with rag.open("rag.toml") as corpus:
        report = bench.run(retrieve=corpus.search, questions="questions.jsonl")
    print(report.metrics["ndcg@10"])
    report.require(min_ndcg_at_10=0.85)

A question file has one JSON object per line: ``id``, ``question``, and the
``support`` that answers it (a ``source_id`` and, optionally, an
``excerpt``). A passage supports an item when it comes from that document
and, with an excerpt, holds at least half of the excerpt's character pairs
once folded by :func:`convilyn.rag.normalize`. Reported per question and on
average: ``recall@k``, ``mrr@k``, ``ndcg@k`` and latency; see
``docs/TOOLS.md`` for the definitions.
"""

from convilyn.rag.bench._compare import compare, compare_variants
from convilyn.rag.bench._pin import check, pin
from convilyn.rag.bench._questions import questions
from convilyn.rag.bench._run import arun, run
from convilyn.rag.bench.types import (
    Check,
    Comparison,
    Pin,
    PinCheck,
    Question,
    QuestionResult,
    Report,
    Support,
    Variants,
)

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
    "arun",
    "check",
    "compare",
    "compare_variants",
    "pin",
    "questions",
    "run",
]

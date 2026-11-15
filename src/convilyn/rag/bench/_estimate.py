"""What a bench run would ask of model services, counted before it runs.

Read from a settings file and the questions alone: no store is opened, no
connector is built, nothing is sent. A search embeds its query once when an
embedder is set, and hands the reranker at most ``max(limit, candidates)``
passages, split into calls of the reranker's ``max_documents``. Retries are
not counted, and prices are the provider's, not counted here.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from convilyn.rag import _config
from convilyn.rag._measure import estimate_tokens
from convilyn.rag.bench.types import Question
from convilyn.rag.recipe import Recipe


@dataclass(frozen=True)
class Calls:
    """The calls one run makes, with what they carry at most."""

    embed_calls: int
    embed_tokens: int
    rerank_calls: int
    rerank_documents: int


def calls(config: str | os.PathLike[str], questions: Sequence[Question]) -> Calls:
    """The calls a run of ``questions`` on the corpus ``config`` describes makes.

    Raises ``ValueError`` for a settings file ``rag.open`` would refuse, or
    one naming a provider that does not offer what its table asks for.
    """
    # Imported here: convilyn.providers imports convilyn.rag.
    from convilyn import providers  # noqa: PLC0415

    file = Path(config)
    settings = _config.read(file)
    asked = [question.question for question in questions if question.status == "ready"]
    rows = {row.name: row for row in providers.available()}
    embed_calls = embed_tokens = rerank_calls = rerank_documents = 0
    if settings.embed is not None:
        offering = tuple(name for name, row in rows.items() if row.embed_wire is not None)
        _config.check_provider(file, "embed", settings.embed.provider, offering)
        embed_calls = len(asked)
        embed_tokens = sum(estimate_tokens(text) for text in asked)
    if settings.rerank is not None:
        offering = tuple(name for name, row in rows.items() if row.rerank_wire is not None)
        _config.check_provider(file, "rerank", settings.rerank.provider, offering)
        search = (settings.recipe or Recipe()).search
        pool = max(search.limit, search.candidates)
        largest = (
            settings.rerank.max_documents
            or rows[settings.rerank.provider].rerank_max_documents
            or pool
        )
        rerank_calls = len(asked) * math.ceil(pool / largest)
        rerank_documents = len(asked) * pool
    return Calls(
        embed_calls=embed_calls,
        embed_tokens=embed_tokens,
        rerank_calls=rerank_calls,
        rerank_documents=rerank_documents,
    )

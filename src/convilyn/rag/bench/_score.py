"""Whether a passage supports an answer, and the metrics that follow.

Matching and scoring are pure functions of text and ranks, so they are tested
on hand-computed cases and shared by every way of running a bench.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from convilyn.rag.bench.types import Support, folded, metric_names


def grams(text: str) -> frozenset[str]:
    """The character pairs of ``text`` once folded, with spacing left out.

    Pairs of characters work the same for scripts written with and without
    spaces. A text of fewer than two characters has none; an excerpt that
    short is refused when it is read (:class:`~convilyn.rag.bench.Support`).
    """
    chars = folded(text)
    return frozenset(chars[at : at + 2] for at in range(len(chars) - 1))


def covers(excerpt: frozenset[str], passage: frozenset[str], cover: float) -> bool:
    """Whether ``passage`` holds at least ``cover`` of ``excerpt``'s pairs."""
    return bool(excerpt) and len(excerpt & passage) / len(excerpt) >= cover


def first_ranks(
    support: Sequence[Support], hits: Sequence[tuple[str, str]], cover: float
) -> list[int | None]:
    """For each support item, the first rank (from 1) of a hit that supports it.

    ``hits`` are ``(source_id, text)`` pairs, best first; ``None`` when no hit
    supports the item. A hit's pairs are computed only when an item with an
    excerpt names its document, and once: a retriever returning very long
    passages costs only for those that could answer.
    """
    hit_grams: dict[int, frozenset[str]] = {}
    ranks: list[int | None] = []
    for item in support:
        wanted = None if item.excerpt is None else grams(item.excerpt)
        found = None
        for rank, (source_id, text) in enumerate(hits, 1):
            if source_id != item.source_id:
                continue
            if wanted is None:
                found = rank
                break
            if rank not in hit_grams:
                hit_grams[rank] = grams(text)
            if covers(wanted, hit_grams[rank], cover):
                found = rank
                break
        ranks.append(found)
    return ranks


def scores(ranks: Sequence[int | None], k: tuple[int, ...]) -> dict[str, float]:
    """``recall@k``, ``mrr@k`` and ``ndcg@k`` for one question's first ranks."""
    found = sorted(rank for rank in ranks if rank is not None)
    effective = _effective(found)
    values: dict[str, float] = {}
    for cut in k:
        values[f"recall@{cut}"] = sum(rank <= cut for rank in found) / len(ranks)
        values[f"mrr@{cut}"] = 1 / found[0] if found and found[0] <= cut else 0.0
        ideal = _dcg(range(1, min(len(ranks), cut) + 1))
        values[f"ndcg@{cut}"] = _dcg(rank for rank in effective if rank <= cut) / ideal
    return {name: values[name] for name in metric_names(k)}


def _effective(found: Sequence[int]) -> list[int]:
    """Ranks with ties spread over the following places.

    Each support item scores once, at the first rank that covers it. When one
    passage covers several items, they take that rank and the ones after it,
    as if each had its own passage there, so a passage covering every item
    at the top is ideal and no score exceeds 1.
    """
    spread: list[int] = []
    for rank in found:
        spread.append(max(rank, spread[-1] + 1) if spread else rank)
    return spread


def _dcg(ranks: Iterable[int]) -> float:
    return sum(1 / math.log2(rank + 1) for rank in ranks)

"""Combining rankings, and joining what a ranking split apart.

Both are plain functions over :class:`~convilyn.rag.types.Hit`: they need no
store and no network, so they work on rankings from anywhere — two of this
SDK's searches, a search of yours, or a vector index elsewhere.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from convilyn.rag._split import BLOCK_JOINER, passage_of
from convilyn.rag.recipe import FuseOptions
from convilyn.rag.types import Hit


@dataclass
class _Seen:
    """One passage as the rankings have shown it so far."""

    hit: Hit
    score: float
    best_rank: int
    ranking: int


def fuse(*rankings: Sequence[Hit], options: FuseOptions | None = None) -> tuple[Hit, ...]:
    """Merge rankings of hits into one, best first.

    Each ranking is a sequence of hits, best first, such as the hits of a
    search. A passage is recognised across rankings by its ``passage_id``; one
    that a ranking does not hold earns nothing from that ranking. The hit kept
    for a passage is the one from the first ranking that holds it, with its
    score replaced by the fused score.

    ``options.method`` chooses how scores are combined:

    * ``"min_max"`` — each ranking's scores are scaled to between 0 and 1,
      then averaged with ``options.weights``. A ranking whose scores are all
      equal gives each of its hits 1.
    * ``"rrf"`` — each ranking gives a hit ``weight / (rrf_k + rank)``, rank
      counted from 1; the scores themselves are not read.

    Passages with the same fused score are ordered by their best rank in any
    ranking, then by the order of the rankings, so the same call always returns
    the same order.
    """
    chosen = options or FuseOptions()
    weights = _weights(chosen, len(rankings))
    seen: dict[str, _Seen] = {}
    for index, ranking in enumerate(rankings):
        hits = _checked(ranking, f"ranking {index}")
        for rank, (hit, share) in enumerate(zip(hits, _shares(hits, chosen), strict=True)):
            earned = weights[index] * share
            known = seen.get(hit.passage.passage_id)
            if known is None:
                seen[hit.passage.passage_id] = _Seen(hit, earned, rank, index)
                continue
            known.score += earned
            if rank < known.best_rank:
                known.best_rank, known.ranking = rank, index
    ordered = sorted(
        seen.values(), key=lambda entry: (-entry.score, entry.best_rank, entry.ranking)
    )
    return tuple(entry.hit.model_copy(update={"score": entry.score}) for entry in ordered)


def _weights(options: FuseOptions, count: int) -> list[float]:
    """Return one weight per ranking, summing to 1."""
    if options.weights is None:
        return [1 / count] * count if count else []
    if len(options.weights) != count:
        raise ValueError(
            f"options.weights has {len(options.weights)} weights for {count} rankings; "
            f"give one per ranking"
        )
    # Checked again here, not only when the options were built: pydantic's
    # copy and construct skip validation. Divided by the largest first, so
    # that weights each near the float limit do not overflow when summed.
    largest = max(options.weights)
    usable = all(math.isfinite(weight) and weight >= 0 for weight in options.weights)
    if not (usable and largest > 0):
        raise ValueError("weights must be finite numbers no smaller than 0, at least one above 0")
    scaled = [weight / largest for weight in options.weights]
    total = math.fsum(scaled)
    return [weight / total for weight in scaled]


def _checked(ranking: Sequence[Hit], label: str) -> tuple[Hit, ...]:
    """Return ``ranking`` as a tuple, refusing what cannot be a ranking.

    ``label`` is what the caller knows it as, for the messages.
    """
    if isinstance(ranking, Hit):
        raise TypeError(f"{label} must be a sequence of hits, not one hit; pass [hit]")
    hits = tuple(ranking)
    passages: set[str] = set()
    for hit in hits:
        if not isinstance(hit, Hit):
            raise TypeError(f"{label} holds a {type(hit).__name__}, not a Hit")
        if hit.passage.passage_id in passages:
            raise ValueError(f"{label} holds one passage twice; list each passage once")
        passages.add(hit.passage.passage_id)
    return hits


def _shares(hits: tuple[Hit, ...], options: FuseOptions) -> list[float]:
    """What each hit of one ranking earns before its ranking's weight."""
    if options.method == "rrf":
        # Checked here as well as when built; see ``_weights``.
        if not options.rrf_k > 0:
            raise ValueError("rrf_k must be above 0")
        return [1 / (options.rrf_k + rank) for rank in range(1, len(hits) + 1)]
    if options.method != "min_max":
        raise ValueError('method must be "min_max" or "rrf"')
    scores = [hit.score for hit in hits]
    if not all(map(math.isfinite, scores)):
        raise ValueError("a score that is not a finite number cannot be scaled; use method rrf")
    low, high = min(scores, default=0.0), max(scores, default=0.0)
    if high == low:
        return [1.0] * len(scores)
    span = high - low
    if math.isinf(span):
        # Two finite scores can be further apart than a float holds. Halved,
        # the difference fits, and the proportions are the same.
        low, span = low / 2, high / 2 - low / 2
        return [(score / 2 - low) / span for score in scores]
    return [(score - low) / span for score in scores]


def merge_adjacent(hits: Sequence[Hit]) -> tuple[Hit, ...]:
    """Join hits that are neighbouring passages of one document.

    Passages of the same source whose ``ordinal`` follow one another with no
    gap become one hit: their texts in the order of the document, under the
    section of the first, with the score, title and labels of the best-ranked
    of them. The joined hit takes the place of that best-ranked one; every
    other hit keeps its place. Only passages already in ``hits`` are joined —
    nothing is fetched to fill a gap.
    """
    ranked = _checked(hits, "hits")
    runs = _runs(ranked)
    best_of_run = {min(run): run for run in runs}
    joined: list[Hit] = []
    for rank, hit in enumerate(ranked):
        run = best_of_run.get(rank)
        if run is None:
            continue
        joined.append(hit if len(run) == 1 else _joined(ranked, run, hit))
    return tuple(joined)


def _runs(ranked: tuple[Hit, ...]) -> list[list[int]]:
    """Group the ranks of ``ranked`` into runs of consecutive passages."""
    by_place = sorted(
        range(len(ranked)),
        key=lambda rank: (ranked[rank].passage.source_id, ranked[rank].passage.ordinal),
    )
    runs: list[list[int]] = []
    previous: Hit | None = None
    for rank in by_place:
        passage = ranked[rank].passage
        follows = (
            previous is not None
            and previous.passage.source_id == passage.source_id
            and previous.passage.ordinal + 1 == passage.ordinal
        )
        if follows:
            runs[-1].append(rank)
        else:
            runs.append([rank])
        previous = ranked[rank]
    return runs


def _joined(ranked: tuple[Hit, ...], run: list[int], best: Hit) -> Hit:
    """One hit holding the passages of ``run``, which is in document order."""
    first = ranked[run[0]].passage
    text = BLOCK_JOINER.join(ranked[rank].passage.text for rank in run)
    return best.model_copy(
        update={"passage": passage_of(first.source_id, first.ordinal, first.section, text)}
    )

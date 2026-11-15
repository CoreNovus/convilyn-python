"""Checking a report's averages against ``min_`` / ``max_`` bounds."""

from __future__ import annotations

import math
from collections.abc import Mapping


def check(
    metrics: Mapping[str, float | None], bounds: Mapping[str, float]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Each bound checked: the metrics that passed, the breaches, the unmeasured.

    An unknown metric raises rather than being skipped: a bound on a name
    that does not exist would pass every run and read like one enforced. No
    bound at all raises for the same reason. A value that is not a finite
    number counts as not measured, since every comparison with NaN is false.
    """
    if not bounds:
        raise ValueError("give at least one bound, such as min_ndcg_at_10=0.85")
    passed: list[str] = []
    breaches: list[str] = []
    unmeasured: list[str] = []
    for name, limit in bounds.items():
        direction, metric = _parsed(name, metrics)
        limit = _finite(name, limit)
        value = metrics[metric]
        if value is None or not math.isfinite(value):
            unmeasured.append(metric)
        elif direction == "min" and value < limit:
            breaches.append(f"{metric} {value:.4f} < {limit:.4f} (short by {limit - value:.4f})")
        elif direction == "max" and value > limit:
            breaches.append(f"{metric} {value:.4f} > {limit:.4f} (over by {value - limit:.4f})")
        else:
            passed.append(metric)
    return tuple(passed), tuple(breaches), tuple(unmeasured)


def _finite(name: str, limit: object) -> float:
    """``limit`` as a float, refusing anything that is not a finite number."""
    if isinstance(limit, int | float) and not isinstance(limit, bool):
        try:
            number = float(limit)
        except OverflowError:
            number = math.inf
        if math.isfinite(number):
            return number
    raise ValueError(f"bound {name} must be a finite number")


def _parsed(name: str, metrics: Mapping[str, float | None]) -> tuple[str, str]:
    """``min_ndcg_at_10`` -> ``("min", "ndcg@10")``."""
    for direction in ("min", "max"):
        prefix = f"{direction}_"
        if name.startswith(prefix):
            metric = name[len(prefix) :].replace("_at_", "@")
            if metric in metrics:
                return direction, metric
    known = ", ".join(f"min_/max_{metric.replace('@', '_at_')}" for metric in metrics)
    raise ValueError(f"unknown bound {name}; a bound is one of: {known}")

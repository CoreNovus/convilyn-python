"""Scaling a vector to length 1, the one way the SDK does it.

A dot product of two vectors of length 1 is their cosine similarity, which is
the score a vector search reports. The providers' embedders, the passages a
store keeps and the query a store is asked about all go through this function,
so the scores mean the same thing wherever they were computed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def unit(values: Iterable[float]) -> tuple[float, ...]:
    """Return ``values`` scaled to length 1.

    Raises ``ValueError`` for what has no direction: no numbers, a component
    that is not a finite number (or not a number at all), or all zeros. The
    message is fixed; nothing of the input is repeated, and the conversion's
    own error is not kept on it.
    """
    numbers: list[float] = []
    try:
        numbers = [float(value) for value in values]
    except (OverflowError, ValueError, TypeError):
        # Left empty, and refused below, outside the handler.
        numbers = []
    # Every component is looked at: a NaN compares false with everything, so
    # taking the largest alone would let one through.
    if not numbers or not all(map(math.isfinite, numbers)):
        raise ValueError("a vector must be a non-empty sequence of finite numbers")
    # Divided by the largest component first, so that squaring neither
    # overflows for large values nor vanishes for small ones.
    largest = max(map(abs, numbers))
    if largest == 0.0:
        raise ValueError("a vector of zeros has no direction")
    scaled = [number / largest for number in numbers]
    length = math.sqrt(math.fsum(number * number for number in scaled))
    return tuple(number / length for number in scaled)

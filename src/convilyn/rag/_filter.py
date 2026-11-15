"""The portable ``where=`` filter.

Every store can express three things over a source's metadata, and nothing
else is accepted, so a filter that works on one store works on the next:

* ``{"lang": "en"}`` — the value equals
* ``{"lang": ["en", "ja"]}`` — the value is one of
* several keys — all of them hold

Anything richer (ranges, negation, nesting) differs between stores; it is
refused here rather than translated differently by each one.
"""

from __future__ import annotations

from collections.abc import Mapping
from reprlib import repr as shown

from convilyn.rag.types import Condition, Scalar

_SCALARS = (str, int, float, bool)
_COLLECTIONS = (list, tuple, set, frozenset)


def check_meta(meta: Mapping[str, Scalar] | None) -> dict[str, Scalar]:
    """Return ``meta`` as a plain dict, refusing anything a filter could not match."""
    if meta is None:
        return {}
    if not isinstance(meta, Mapping):
        raise TypeError("meta must be a mapping of names to values")
    for key, value in meta.items():
        _check_key(key)
        if not isinstance(value, _SCALARS):
            raise ValueError(
                f"meta value for {shown(key)} must be a string, number or boolean, "
                f"got {type(value).__name__}"
            )
    return dict(meta)


def parse_where(where: Mapping[str, object] | None) -> tuple[Condition, ...]:
    """Turn a ``where=`` mapping into conditions, all of which must hold."""
    if where is None:
        return ()
    if not isinstance(where, Mapping):
        raise TypeError("where must be a mapping of names to values")
    return tuple(_condition(key, wanted) for key, wanted in where.items())


def _condition(key: object, wanted: object) -> Condition:
    name = _check_key(key)
    if isinstance(wanted, _SCALARS):
        return Condition(key=name, values=(wanted,))
    if not isinstance(wanted, _COLLECTIONS):
        raise ValueError(
            f"where[{shown(name)}] must be a value or a list of values, "
            f"got {type(wanted).__name__}; ranges, negation and nesting are not portable "
            f"across stores"
        )
    values = tuple(wanted)
    if not values:
        raise ValueError(f"where[{shown(name)}] is an empty list, which nothing can match")
    for value in values:
        if not isinstance(value, _SCALARS):
            raise ValueError(
                f"where[{shown(name)}] may only list strings, numbers and booleans, "
                f"got {type(value).__name__}"
            )
    return Condition(key=name, values=values)


def _check_key(key: object) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("a metadata name must be a non-empty string")
    return key

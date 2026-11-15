"""The graphs this SDK ships.

Adding a graph is adding a row here: a module of its own that exports the
function building it and an ``INFO`` describing it, and that passes the shared
contract tests.
"""

from __future__ import annotations

from convilyn.graphs import _collect, _corrective_rag, _fanout, _plan_execute, _refine, _use_case
from convilyn.graphs.types import GraphInfo

_GRAPHS: tuple[GraphInfo, ...] = (
    _collect.INFO,
    _corrective_rag.INFO,
    _fanout.INFO,
    _plan_execute.INFO,
    _refine.INFO,
    _use_case.INFO,
)


def available() -> tuple[GraphInfo, ...]:
    """Describe every graph this package can build, in name order."""
    return tuple(sorted(_GRAPHS, key=lambda info: info.name))

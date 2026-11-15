"""Ready-made graphs to place inside a LangGraph application.

Each function here builds a compiled graph from functions you supply — the
model call, the search, the check — and never chooses a model for you::

    from convilyn import graphs

    improve = graphs.refine(generate=write_draft, evaluate=run_checks)

**Which one.** Each graph is for one situation, which ``use_when`` in
:func:`available` states and its builder's docstring repeats after its
one-line summary.

**Three ways to use one.** Add it as a node when your state uses the same key
names::

    builder.add_node("improve", improve)

Rename the keys when it does not::

    builder.add_node("improve", graphs.as_node(improve, inputs={"task": "request"}))

Or hand it to an agent as a tool::

    tool = graphs.as_tool(improve, name="improve", description="Write and check a draft.")

**Your setup stays yours.** A graph built here carries no saved-state backend,
store or run-time context of its own; it uses whatever the host graph was
compiled with, and runs the same when the host has none. It reads only its
declared inputs and writes only its declared outputs — :func:`available` lists
both for every graph. Keys it keeps for itself start with ``cv_``.

Two things belong to the framework, not to this package. Added directly as a
node, a graph shares any state key your graph also declares under the same
name; use :func:`as_node` when that is not what you want. And to stream what
happens inside, the host asks for it with ``subgraphs=True``.

**Plain or async functions.** Supply either. With plain functions the graph
runs under ``invoke`` and ``ainvoke``; with any ``async`` one, use ``ainvoke``.

Needs ``pip install "convilyn[graphs]"``.

## How this package is laid out

Public, and covered by the stability policy: this module, ``types`` (state and
descriptions) and ``errors``. Every module with a leading underscore is
internal: one per graph, ``_adapters`` for the node and tool wrappers,
``_nodes`` for how a step calls your functions, and ``_registry`` for the list.
"""

# No `from __future__ import annotations` here: it would bind a public
# attribute named `annotations` on a namespace whose export set is frozen.
try:
    from convilyn.graphs._adapters import as_node, as_tool
    from convilyn.graphs._collect import collect
    from convilyn.graphs._corrective_rag import corrective_rag
    from convilyn.graphs._fanout import fanout
    from convilyn.graphs._plan_execute import plan_execute
    from convilyn.graphs._refine import refine
    from convilyn.graphs._registry import available
    from convilyn.graphs._use_case import use_case
except ImportError as _missing:
    if (_missing.name or "").split(".")[0] in {
        "langgraph",
        "langchain",
        "langchain_core",
        "jsonschema",
        "referencing",
    }:
        raise ImportError(
            'convilyn.graphs needs its optional dependencies: pip install "convilyn[graphs]"',
            name=_missing.name,
        ) from _missing
    raise
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import (
    CollectInput,
    CollectOutput,
    CollectState,
    CorrectiveRagInput,
    CorrectiveRagOutput,
    CorrectiveRagState,
    Document,
    ExecutedStep,
    FanoutInput,
    FanoutOutput,
    FanoutState,
    GraphInfo,
    PlanExecuteInput,
    PlanExecuteOutput,
    PlanExecuteState,
    RefineInput,
    RefineOutput,
    RefineState,
    UseCase,
    UseCaseInput,
    UseCaseOutput,
    UseCaseState,
)

__all__ = [
    "CollectInput",
    "CollectOutput",
    "CollectState",
    "CorrectiveRagInput",
    "CorrectiveRagOutput",
    "CorrectiveRagState",
    "Document",
    "ExecutedStep",
    "FanoutInput",
    "FanoutOutput",
    "FanoutState",
    "GraphError",
    "GraphInfo",
    "PlanExecuteInput",
    "PlanExecuteOutput",
    "PlanExecuteState",
    "RefineInput",
    "RefineOutput",
    "RefineState",
    "UseCase",
    "UseCaseInput",
    "UseCaseOutput",
    "UseCaseState",
    "available",
    "as_node",
    "as_tool",
    "collect",
    "corrective_rag",
    "fanout",
    "plan_execute",
    "refine",
    "use_case",
]

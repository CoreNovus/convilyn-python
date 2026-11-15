"""Search, keep what is relevant, reword the query when nothing is, then answer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from convilyn.graphs._nodes import Call, node, text
from convilyn.graphs.errors import GraphError
from convilyn.graphs.types import (
    CorrectiveRagInput,
    CorrectiveRagOutput,
    CorrectiveRagState,
    Document,
    GraphInfo,
)
from convilyn.rag.types import SearchResult

Retrieved = SearchResult | Sequence[str]
#: A query to what it found. ``corpus.search`` from :mod:`convilyn.rag` fits
#: as it is; so does any function returning a list of strings.
Retrieve = Callable[[str], Retrieved | Awaitable[Retrieved]]
#: ``(question, passage text)`` to whether the passage helps answer it.
Grade = Callable[[str, str], bool | Awaitable[bool]]
#: ``(question, the query that found nothing)`` to a query worth trying next.
Rewrite = Callable[[str, str], str | Awaitable[str]]
#: ``(question, relevant passages)`` to the answer. The list is empty when
#: every search came back with nothing relevant.
Generate = Callable[[str, list[Document]], str | Awaitable[str]]

INFO = GraphInfo(
    name="corrective_rag",
    summary="Search, keep the relevant passages, reword and retry when none are, then answer.",
    use_when="answers must come from your documents and one search can miss: it keeps only "
    "the passages judged relevant, rewords the query and searches again when none are, then "
    "answers and says whether the answer is grounded.",
    inputs=tuple(CorrectiveRagInput.__annotations__),
    outputs=tuple(CorrectiveRagOutput.__annotations__),
    requires=("retrieve", "grade", "rewrite", "generate"),
)

_QUERY = "cv_corrective_rag_query"
_FOUND = "cv_corrective_rag_found"


def _documents(retrieved: object) -> list[Document]:
    if isinstance(retrieved, SearchResult):
        return [
            Document(text=hit.passage.text, source=hit.passage.source_id) for hit in retrieved.hits
        ]
    if isinstance(retrieved, str) or not isinstance(retrieved, Sequence):
        raise GraphError("retrieve must return a search result or a sequence of strings")
    return [Document(text=text(item), source=None) for item in retrieved]


def corrective_rag(
    *,
    retrieve: Retrieve,
    grade: Grade,
    rewrite: Rewrite,
    generate: Generate,
    max_retries: int = 2,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a graph that answers a question from passages it has checked.

    Use it when answers must come from your documents and one search can miss:
    it keeps only the passages judged relevant, rewords the query and searches
    again when none are, then answers and says whether the answer is grounded.

    Each search result is passed to ``grade``, and only what it keeps reaches
    ``generate``. When nothing is kept the query is reworded and the search run
    again, up to ``max_retries`` more times. After that ``generate`` is still
    called, with no passages and ``grounded`` false, so the caller decides what
    an ungrounded answer should say.
    """
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")

    async def search(state: CorrectiveRagState, call: Call) -> dict[str, Any]:
        query = state.get(_QUERY) or state["question"]
        found = _documents(await call(retrieve, query))
        return {_QUERY: query, _FOUND: found, "attempts": state.get("attempts", 0) + 1}

    async def check(state: CorrectiveRagState, call: Call) -> dict[str, Any]:
        kept = [
            document
            for document in state[_FOUND]
            if await call(grade, state["question"], document["text"])
        ]
        return {"documents": kept, "grounded": bool(kept)}

    def after_check(state: CorrectiveRagState) -> Literal["answer", "reword"]:
        return "answer" if state["grounded"] or state["attempts"] > max_retries else "reword"

    async def reword(state: CorrectiveRagState, call: Call) -> dict[str, Any]:
        return {_QUERY: text(await call(rewrite, state["question"], state[_QUERY]))}

    async def answer(state: CorrectiveRagState, call: Call) -> dict[str, Any]:
        return {"answer": text(await call(generate, state["question"], state["documents"]))}

    builder = StateGraph(
        CorrectiveRagState, input_schema=CorrectiveRagInput, output_schema=CorrectiveRagOutput
    )
    builder.add_node("search", node(search))
    builder.add_node("check", node(check))
    builder.add_node("reword", node(reword))
    builder.add_node("answer", node(answer))
    builder.add_edge(START, "search")
    builder.add_edge("search", "check")
    builder.add_conditional_edges("check", after_check)
    builder.add_edge("reword", "search")
    builder.add_edge("answer", END)
    return builder.compile(name=INFO.name)

"""The demo that ships with the package: six short documents, a question set.

Written for this project, in English and Traditional Chinese, about a
bookshop that does not exist, so it carries no third-party text and no
licence question. Running it needs no key, no network and nothing beyond the
base package: keyword search over an in-memory SQLite index.
"""

from __future__ import annotations

from importlib import resources

from convilyn import rag
from convilyn.rag.bench._run import run
from convilyn.rag.bench.types import Report

_FOLDER = "_demo"


def demo() -> Report:
    """Index the demo documents by keyword and run the demo questions."""
    folder = resources.files("convilyn.rag.bench").joinpath(_FOLDER)
    documents = sorted(
        (entry for entry in folder.iterdir() if entry.name.endswith(".md")),
        key=lambda entry: entry.name,
    )
    with rag.open() as corpus:
        for entry in documents:
            corpus.add_markdown(entry.read_text(encoding="utf-8"), source_id=entry.name[:-3])
        with resources.as_file(folder.joinpath("questions.jsonl")) as questions:
            return run(retrieve=corpus.search, questions=questions)

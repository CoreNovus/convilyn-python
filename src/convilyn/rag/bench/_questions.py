"""Reading a question file: one JSON object per line.

Errors name the line and the field and never quote the line: a question set
can hold text its owner would not want in a log.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from convilyn.rag.bench.types import Question


def questions(path: str | os.PathLike[str]) -> tuple[Question, ...]:
    """The questions in the JSONL file at ``path``, in file order.

    Blank lines are skipped. Raises ``ValueError`` for a file that is not
    UTF-8, a line that is not a JSON object of a question, a repeated ``id``,
    or a file with no question.
    """
    file = Path(path)
    loaded: list[Question] = []
    # Split on newlines only: `splitlines` also splits on U+2028 and its kin,
    # which JSON strings may hold unescaped. A trailing `\r` is JSON spacing.
    for number, line in enumerate(_text(file).split("\n"), 1):
        if line.strip():
            loaded.append(_question(file, number, line))
    if not loaded:
        raise ValueError(f"{file}: no questions")
    unique(loaded, where=f"{file}: line ", offset=1)
    return tuple(loaded)


def unique(chosen: Sequence[Question], *, where: str, offset: int) -> None:
    """Refuse a repeated id, naming both places (``where`` + position)."""
    seen: dict[str, int] = {}
    for position, question in enumerate(chosen, offset):
        if question.id in seen:
            raise ValueError(
                f"{where}{position} repeats the id of {seen[question.id]}; ids must be unique"
            )
        seen[question.id] = position


def _text(file: Path) -> str:
    raw = file.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        problem = f"not UTF-8 text (byte {error.start})"
    # Raised outside the handler: the decode error holds the whole file.
    raise ValueError(f"{file}: {problem}")


def _question(file: Path, number: int, line: str) -> Question:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as error:
        problem = f"not valid JSON (column {error.colno})"
    except (ValueError, RecursionError):
        # An integer literal over the digit limit, or nesting too deep to parse.
        problem = "not valid JSON"
    else:
        if not isinstance(data, dict):
            problem = "must be a JSON object"
        else:
            try:
                return Question.model_validate(data)
            except ValidationError as error:
                problem = _described(error)
    # Raised outside the handlers: the parse and validation errors hold the line.
    raise ValueError(f"{file}: line {number}: {problem}")


def _described(error: ValidationError) -> str:
    """The first problem as ``field: message``, naming only fields we define.

    An unknown field's name is the file's own text, so it is not repeated.
    """
    first = error.errors(include_input=False, include_url=False)[0]
    location = [str(part) for part in first["loc"]]
    if first["type"] == "extra_forbidden":
        return ".".join([*location[:-1], "unknown field"])
    return f"{'.'.join(location)}: {first['msg']}"

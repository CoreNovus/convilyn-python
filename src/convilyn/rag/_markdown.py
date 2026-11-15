"""Find the block boundaries of a Markdown document.

This is a scanner, not a renderer. Splitting needs to know where a heading, a
table, a list, a fenced block or a paragraph starts and ends, and nothing about
what is inside one — every block keeps its source lines verbatim. That is why
there is no Markdown library behind this module: a parser would be asked for
only the outermost level of what it produces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BlockKind = Literal["heading", "paragraph", "list", "table", "fence"]

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# Every pattern here runs on arbitrary text, so none may backtrack: each is
# anchored and has at most one unbounded run. Anything needing two (a heading's
# closing hashes, a table's delimiter cells) is finished in plain code below.
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?=[ \t]|$)")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_DELIMITER_CELL = re.compile(r":?-+:?")
_LIST_MARKER = re.compile(r"^([ \t]*)(?:[-*+]|\d{1,9}[.)])[ \t]+")
_FRONT_MATTER_FENCE = "---"


@dataclass(frozen=True, slots=True)
class Block:
    """One top-level block and the source lines it spans."""

    kind: BlockKind
    lines: tuple[str, ...]
    #: Heading level, 1 to 6. Zero for every other kind.
    level: int = 0
    #: Heading text without its markers. Empty for every other kind.
    title: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def scan(markdown: str) -> tuple[Block, ...]:
    """Return the top-level blocks of ``markdown`` in document order."""
    lines = _without_front_matter(markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    blocks: list[Block] = []
    paragraph: list[str] = []
    index = 0

    def close_paragraph() -> None:
        if paragraph:
            blocks.append(Block("paragraph", tuple(paragraph)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]

        if not line.strip():
            close_paragraph()
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            close_paragraph()
            end = _fence_end(lines, index, fence.group(1))
            blocks.append(Block("fence", tuple(lines[index:end])))
            index = end
            continue

        underline = _SETEXT_UNDERLINE.match(line)
        if underline and paragraph:
            title = " ".join(part.strip() for part in paragraph)
            level = 1 if underline.group(1).startswith("=") else 2
            blocks.append(Block("heading", (*paragraph, line), level, title))
            paragraph.clear()
            index += 1
            continue

        if _THEMATIC_BREAK.match(line):
            close_paragraph()
            index += 1
            continue

        heading = _ATX_HEADING.match(line)
        if heading:
            close_paragraph()
            title = _heading_title(line[heading.end() :])
            blocks.append(Block("heading", (line,), len(heading.group(1)), title))
            index += 1
            continue

        if _starts_table(lines, index):
            close_paragraph()
            end = _table_end(lines, index)
            blocks.append(Block("table", tuple(lines[index:end])))
            index = end
            continue

        if _LIST_MARKER.match(line):
            close_paragraph()
            end = _list_end(lines, index)
            blocks.append(Block("list", tuple(lines[index:end])))
            index = end
            continue

        paragraph.append(line)
        index += 1

    close_paragraph()
    return tuple(blocks)


def list_items(block: Block) -> tuple[str, ...]:
    """Split a list block into its outermost items, nested content included."""
    first = _LIST_MARKER.match(block.lines[0])
    base = len(first.group(1)) if first else 0
    items: list[list[str]] = []
    for line in block.lines:
        marker = _LIST_MARKER.match(line)
        if marker and len(marker.group(1)) <= base:
            items.append([line])
        else:
            items[-1].append(line)
    return tuple("\n".join(item).rstrip() for item in items)


def fence_parts(block: Block) -> tuple[str, tuple[str, ...], str]:
    """Return a fenced block's opening line, content lines and closing line.

    The closing line is rebuilt from the opening marker, so it is as long as the
    one that opened the block — a shorter one would let a fence written inside
    the content end it early. An unclosed block has every line after the
    opening one as content.
    """
    opening = block.lines[0]
    marker = _FENCE.match(opening)
    closing = marker.group(1) if marker else "```"
    content = block.lines[1:]
    if content and _closes_fence(content[-1], closing):
        content = content[:-1]
    return opening, content, closing


def _closes_fence(line: str, marker: str) -> bool:
    stripped = line.strip()
    indent = len(line) - len(line.lstrip(" "))
    return indent <= 3 and len(stripped) >= len(marker) and set(stripped) == {marker[0]}


def _heading_title(rest: str) -> str:
    """Return a heading's text, without an optional closing run of hashes."""
    title = rest.strip()
    unclosed = title.rstrip("#")
    if unclosed != title and (not unclosed or unclosed[-1] in " \t"):
        return unclosed.rstrip()
    return title


def _is_table_delimiter(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    cells = stripped.removeprefix("|").removesuffix("|").split("|")
    return all(_TABLE_DELIMITER_CELL.fullmatch(cell.strip()) for cell in cells)


def _without_front_matter(lines: list[str]) -> list[str]:
    if not lines or lines[0].strip() != _FRONT_MATTER_FENCE:
        return lines
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONT_MATTER_FENCE:
            return lines[index + 1 :]
    return lines


def _fence_end(lines: list[str], start: int, opening: str) -> int:
    for index in range(start + 1, len(lines)):
        if _closes_fence(lines[index], opening):
            return index + 1
    return len(lines)


def _starts_table(lines: list[str], index: int) -> bool:
    if "|" not in lines[index] or index + 1 >= len(lines):
        return False
    return _is_table_delimiter(lines[index + 1])


def _table_end(lines: list[str], start: int) -> int:
    index = start + 2
    while index < len(lines) and lines[index].strip() and "|" in lines[index]:
        index += 1
    return index


def _list_end(lines: list[str], start: int) -> int:
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if line.strip():
            index += 1
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        continues = bool(following.strip()) and (
            following[0] in " \t" or bool(_LIST_MARKER.match(following))
        )
        if not continues:
            break
        index += 1
    return index

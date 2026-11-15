"""Cut a Markdown document into passages along its own structure.

The rules, in the order they apply:

1. A heading down to ``section_depth`` opens a section. A passage never crosses
   one, so every passage has a single, truthful heading trail.
2. Inside a section, blocks are gathered until the passage reaches ``target`` or
   the next block would take it past ``max``.
3. A block larger than ``max`` is divided the way its kind allows: a paragraph
   at sentence ends, a list between items, a fenced block between lines with the
   fence repeated, a table between rows with its header repeated.
4. A trailing piece smaller than ``min`` joins the passage before it when the
   result still fits.

Passages do not overlap. A table header is the one thing that is repeated,
because a row without its header no longer says what its cells mean.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence

from convilyn.rag._markdown import Block, fence_parts, list_items, scan
from convilyn.rag._measure import Measure, token_estimator
from convilyn.rag.recipe import SplitOptions
from convilyn.rag.types import Passage

BLOCK_JOINER = "\n\n"
_LINE_JOINER = "\n"
# The CJK marks end a sentence wherever they appear. The Latin ones also sit
# inside abbreviations and numbers, so they count only before whitespace.
_SENTENCE_END = re.compile(r"(?<=[。！？；｡])|(?<=[.!?])(?=\s)")
_TABLE_HEADER_LINES = 2
#: A repeated header or fence may take at most 1/this of the size limit.
_REPEAT_SHARE_DIVISOR = 2
#: ... and at most this many characters for each unit of the limit. Ordinary
#: text runs at one to five characters per token, so a header or fence that
#: fits half the limit stays well inside this; only padding goes past it.
_REPEAT_CHARACTERS_PER_UNIT = 8
#: The most characters a heading or title takes in a passage's ``section``.
#: A heading is a line of a few words; this is a bound, not a tuned value.
_TRAIL_NAME_LIMIT = 256
#: The most characters the whole trail takes, title and headings together.
#: Real trails run to a few dozen; this is a bound, not a tuned value.
_TRAIL_LIMIT = 512
_CUT_MARK = "…"
#: Control characters (Unicode category Cc), read as spaces in trail names.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
#: How many times the lines of a fence or table are grouped again with less
#: room before every candidate is measured in its frame instead. The built-in
#: estimate settles in four at most.
_REGROUP_ROUNDS = 8


def split(
    markdown: str,
    *,
    source_id: str,
    title: str | None = None,
    options: SplitOptions | None = None,
    measure: Measure | None = None,
) -> tuple[Passage, ...]:
    """Split ``markdown`` into passages.

    ``title`` names the document and leads every passage's ``section`` trail,
    which matters for documents whose own headings do not say what they are
    about. ``measure`` replaces the built-in token estimate, which is otherwise
    the one ``options`` names by tokenizer and language.
    """
    if not source_id:
        raise ValueError("source_id must be a non-empty string")
    chosen = options or SplitOptions()
    size_of = measure or token_estimator(chosen.tokenizer, language=chosen.language)

    passages: list[Passage] = []
    for trail, blocks in _sections(scan(markdown), chosen.section_depth, title):
        units = [piece for block in blocks for piece in _fit(block, chosen.max, size_of)]
        for text in _gather(units, chosen, size_of):
            passages.append(passage_of(source_id, len(passages), trail, text))
    return tuple(passages)


def passage_of(source_id: str, ordinal: int, trail: tuple[str, ...], text: str) -> Passage:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = f"{source_id}\n{ordinal}\n{digest}".encode()
    return Passage(
        passage_id=hashlib.sha256(identity).hexdigest()[:32],
        source_id=source_id,
        ordinal=ordinal,
        section=trail,
        text=text,
        digest=digest,
    )


def _sections(
    blocks: Iterable[Block], depth: int, title: str | None
) -> list[tuple[tuple[str, ...], list[Block]]]:
    """Group blocks under the heading trail they belong to.

    Every passage carries its trail, so it is bounded (see :func:`_bounded`).
    A name is cleaned and cut once, when its heading opens, so a section costs
    the same however long the headings above it were. A heading longer than a
    trail takes is kept whole as the first block of its section.
    """
    root = _readable(title or "")
    root_shown = _cut(root, _TRAIL_NAME_LIMIT)
    # Each open heading: its level, its name as a trail shows it, and whether
    # it is the outermost heading and says what the title says.
    open_headings: list[tuple[int, str, bool]] = []
    sections: list[tuple[tuple[str, ...], list[Block]]] = [
        (_bounded((root_shown,) if root else ()), [])
    ]

    for block in blocks:
        if block.kind != "heading" or block.level > depth:
            sections[-1][1].append(block)
            continue
        name = _readable(block.title)
        open_headings = [heading for heading in open_headings if heading[0] < block.level]
        if name:
            # Compared as written, before any cut: two names that differ only
            # past the cut are two names.
            repeats_title = not open_headings and name == root
            open_headings.append((block.level, _cut(name, _TRAIL_NAME_LIMIT), repeats_title))
        names = tuple(shown for _, shown, _ in open_headings)
        repeated = bool(open_headings) and open_headings[0][2]
        trail = names if repeated or not root else (root_shown, *names)
        too_long = len(name) > _TRAIL_NAME_LIMIT
        sections.append((_bounded(trail), [block] if too_long else []))

    return [(trail, members) for trail, members in sections if members]


def _bounded(trail: tuple[str, ...]) -> tuple[str, ...]:
    """Hold ``trail``, whose names are already cut, to ``_TRAIL_LIMIT`` characters.

    The outer names give way first, because the innermost says most about the
    passage. Each name further out keeps at least its cut mark.
    """
    room = _TRAIL_LIMIT
    kept: list[str] = []
    for outer, name in reversed(tuple(enumerate(trail))):
        available = room - outer * len(_CUT_MARK)
        cut = _cut(name, max(available, len(_CUT_MARK)))
        kept.append(cut)
        room -= len(cut)
    return tuple(reversed(kept))


def _readable(name: str) -> str:
    """``name`` as a trail shows it: each control character read as a space.

    Control characters mean nothing in a heading, and a serialiser writes each
    as six. A tab or a line break is still a gap between words, so each becomes
    a space, and the runs of spaces that leaves are closed up.
    """
    if _CONTROL.search(name) is None:
        return name
    return " ".join(_CONTROL.sub(" ", name).split())


def _cut(name: str, limit: int) -> str:
    """``name`` cut to ``limit`` characters, marked when it was cut."""
    if len(name) <= limit:
        return name
    return name[: limit - len(_CUT_MARK)] + _CUT_MARK


def _gather(units: list[str], options: SplitOptions, size_of: Measure) -> list[str]:
    """Join consecutive units into passages within the size limits.

    A passage closes with the unit that brings it to ``target``, or one unit
    earlier when that unit would take it past ``max``.
    """
    passages: list[str] = []
    start = 0
    while start < len(units):
        below = _reach(units, start, BLOCK_JOINER, options.target - 1, size_of)
        if start + below == len(units):
            return _with_remainder(passages, units[start:], options, size_of)
        count = below + 1
        if below and size_of(BLOCK_JOINER.join(units[start : start + count])) > options.max:
            count = below
        passages.append(BLOCK_JOINER.join(units[start : start + count]))
        start += count
    return passages


def _with_remainder(
    passages: list[str], units: list[str], options: SplitOptions, size_of: Measure
) -> list[str]:
    """Add the units left over below ``target``, joined to the last passage if small."""
    remainder = BLOCK_JOINER.join(units)
    if passages and size_of(remainder) < options.min:
        merged = f"{passages[-1]}{BLOCK_JOINER}{remainder}"
        if size_of(merged) <= options.max:
            return [*passages[:-1], merged]
    return [*passages, remainder]


def _fit(block: Block, limit: int, size_of: Measure) -> list[str]:
    """Return ``block`` as pieces that each fit within ``limit``."""
    if size_of(block.text) <= limit:
        return [block.text]
    return _DIVIDERS[block.kind](block, limit, size_of)


def _divide_prose(text: str, limit: int, size_of: Measure) -> list[str]:
    # Sentences keep the whitespace that followed the previous one, so joining
    # them with nothing restores the source text exactly.
    pieces = [
        piece
        for sentence in _SENTENCE_END.split(text)
        if sentence.strip()
        for piece in _hard_cut(sentence, limit, size_of)
    ]
    # A measure that counts whitespace can fill a whole group with it; such a
    # group carries nothing and is not a passage.
    return [group.strip() for group in _pack(pieces, "", limit, size_of) if group.strip()]


def _divide_paragraph(block: Block, limit: int, size_of: Measure) -> list[str]:
    return _divide_prose(block.text, limit, size_of)


def _divide_list(block: Block, limit: int, size_of: Measure) -> list[str]:
    pieces: list[str] = []
    for item in list_items(block):
        pieces.extend([item] if size_of(item) <= limit else _divide_prose(item, limit, size_of))
    return _pack(pieces, _LINE_JOINER, limit, size_of)


def _divide_fence(block: Block, limit: int, size_of: Measure) -> list[str]:
    opening, content, closing = fence_parts(block)
    # Measured with the two joiners a piece will contain, so the room left for
    # content is exact.
    empty = _LINE_JOINER.join((opening, "", closing))
    frame = size_of(empty)
    if not _worth_repeating(empty, frame, limit):
        return _divide_prose(block.text, limit, size_of)

    def framed(group: str) -> str:
        return _LINE_JOINER.join((opening, group, closing))

    # A blank line is content inside a fence, so it is kept rather than cut away.
    def pieces_of(line: str, room: int, measure: Measure) -> list[str]:
        return _hard_cut(line, room, measure) or [line]

    return [
        framed(group)
        for group in _fill_frames(content, limit, frame, size_of, framed, pieces_of)
        # Blank lines are kept beside the lines they separate, never on their own.
        if group.strip()
    ]


def _divide_table(block: Block, limit: int, size_of: Measure) -> list[str]:
    header = _LINE_JOINER.join(block.lines[:_TABLE_HEADER_LINES])
    frame = size_of(f"{header}{_LINE_JOINER}")
    if not _worth_repeating(header, frame, limit):
        return _divide_prose(block.text, limit, size_of)

    def framed(group: str) -> str:
        return f"{header}{_LINE_JOINER}{group}"

    # A row cut inside a run of spaces leaves pieces that hold nothing; one
    # would become a passage of the header alone.
    def pieces_of(row: str, room: int, measure: Measure) -> list[str]:
        return [piece for piece in _hard_cut(row, room, measure) if piece.strip()]

    rows = block.lines[_TABLE_HEADER_LINES:]
    return [framed(group) for group in _fill_frames(rows, limit, frame, size_of, framed, pieces_of)]


def _fill_frames(
    lines: Sequence[str],
    limit: int,
    frame: int,
    size_of: Measure,
    framed: Callable[[str], str],
    pieces_of: Callable[[str, int, Measure], list[str]],
) -> list[str]:
    """Group ``lines`` so that each group, once ``framed``, is within ``limit``.

    A measure need not add up across a join: whitespace beside a line break
    costs differently than on its own. Taking the frame's size off the limit is
    therefore only a first guess at the room for content. Each group is checked
    once as it will be emitted, and when one is over, the room is reduced by
    what it was over by and the lines are grouped again. The frame is measured
    once per group that way, never once per line, so a frame that is long in
    characters does not multiply the work.
    """
    room = limit - frame
    for _ in range(_REGROUP_ROUNDS):
        pieces = [piece for line in lines for piece in pieces_of(line, room, size_of)]
        groups = _pack(pieces, _LINE_JOINER, room, size_of)
        over = max((size_of(framed(group)) - limit for group in groups), default=0)
        if over <= 0:
            return groups
        # Written so that a measure answering with something that does not
        # compare (not a number) ends the rounds instead of repeating them.
        if not room > over:
            break
        room -= over

    # Giving up room did not settle it: there was none left to give, which
    # takes a limit of a few units, or a measure of the caller's keeps a group
    # over whatever the room. Measure every candidate inside its frame.
    def in_frame(group: str) -> int:
        return size_of(framed(group))

    pieces = [piece for line in lines for piece in pieces_of(line, limit, in_frame)]
    return _pack(pieces, _LINE_JOINER, limit, in_frame)


def _worth_repeating(repeated: str, repeated_size: int, limit: int) -> bool:
    """Whether a header or fence is small enough to repeat in every piece.

    Repeating costs its size once per piece. Past half the limit that cost
    outweighs the content each piece carries, and the output would grow without
    bound relative to the input — so a larger one is divided as plain text.

    Its length in characters is held to the limit as well. A measure may count
    a long run of spaces as almost nothing, and a fence line padded with tens
    of thousands of them would then be copied into every piece at no cost the
    measure can see.
    """
    return (
        repeated_size * _REPEAT_SHARE_DIVISOR <= limit
        and len(repeated) <= limit * _REPEAT_CHARACTERS_PER_UNIT
    )


_DIVIDERS: dict[str, Callable[[Block, int, Measure], list[str]]] = {
    "heading": _divide_paragraph,
    "paragraph": _divide_paragraph,
    "list": _divide_list,
    "fence": _divide_fence,
    "table": _divide_table,
}


def _pack(pieces: list[str], joiner: str, limit: int, size_of: Measure) -> list[str]:
    """Join consecutive pieces greedily without exceeding ``limit``."""
    groups: list[str] = []
    start = 0
    while start < len(pieces):
        # A piece that does not fit on its own is still a group of its own.
        count = max(1, _reach(pieces, start, joiner, limit, size_of))
        groups.append(joiner.join(pieces[start : start + count]))
        start += count
    return groups


def _reach(pieces: list[str], start: int, joiner: str, limit: int, size_of: Measure) -> int:
    """Return how many pieces from ``start`` join into a text within ``limit``.

    Found the way :func:`_hard_cut` finds a prefix — double the count until the
    join no longer fits, then search inside that bracket — so that a run of very
    many small pieces is not measured again for every piece added to it.
    """
    available = len(pieces) - start

    def fits(count: int) -> bool:
        return size_of(joiner.join(pieces[start : start + count])) <= limit

    low = 0
    high = 1
    while high < available and fits(high):
        low = high
        high = min(high * 2, available)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _hard_cut(text: str, limit: int, size_of: Measure) -> list[str]:
    """Cut ``text`` into the longest prefixes that fit, as a last resort.

    Each piece is found by doubling a candidate length until it no longer fits
    and then searching inside that bracket, so a piece costs about its own size
    to find however long the remaining text is.
    """
    pieces: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        fits = 1
        reach = min(2, total - start)
        while fits < reach and size_of(text[start : start + reach]) <= limit:
            fits = reach
            reach = min(reach * 2, total - start)
        low, high = fits, reach
        while low < high:
            middle = (low + high + 1) // 2
            if size_of(text[start : start + middle]) <= limit:
                low = middle
            else:
                high = middle - 1
        pieces.append(text[start : start + low])
        start += low
    return pieces

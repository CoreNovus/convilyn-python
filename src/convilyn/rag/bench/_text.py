"""The Markdown a person reads: a report's tables and a comparison's verdict.

Plain data in, text out, with fixed number formats, so the same report gives
the same text on every run and a diff of two summaries shows only real
changes.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence


def report(
    metrics: Mapping[str, float | None],
    groups: Mapping[str, Mapping[str, float | None]],
    *,
    run: int,
    answerable: int,
    drafts: int,
    k: tuple[int, ...],
    cover: float,
) -> str:
    """A report as Markdown: one row per metric, a column for all and each group."""
    columns = ["all", *groups]
    lines = [
        "# Retrieval report",
        "",
        f"Questions run: {run} ({answerable} with an answer, {run - answerable} without); "
        f"drafts skipped: {drafts}. k = {', '.join(str(cut) for cut in k)}; cover = {cover:g}.",
        "",
        "| metric | " + " | ".join(_cell(name) for name in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for name in metrics:
        values = [metrics[name], *(group[name] for group in groups.values())]
        lines.append(f"| {name} | " + " | ".join(_number(name, v) for v in values) + " |")
    return "\n".join(lines) + "\n"


def comparison(
    *,
    metric: str,
    verdict: str,
    mean: float,
    low: float,
    high: float,
    n: int,
    confidence: float,
    resamples: int,
    seed: int,
) -> str:
    """A comparison as Markdown: the verdict, then the numbers it rests on."""
    return (
        f"# Comparison: {_cell(metric)}\n\n"
        f"**{verdict}**: mean difference {mean:+.3f} "
        f"({confidence * 100:.6g}% interval {low:+.3f} to {high:+.3f}) over {n} questions; "
        f"paired bootstrap, {resamples:,} resamples, seed {seed}.\n"
    )


#: A variant's verdict and the mean difference and interval it rests on.
Outcome = tuple[str, float, float, float]


def variants(
    *,
    metric: str,
    columns: Sequence[str],
    rows: Sequence[tuple[str, Sequence[float | None], Outcome | None]],
    n: int,
    confidence: float,
    each: float,
    resamples: int,
    seed: int,
) -> str:
    """Variants as Markdown: one row each, the baseline's with no outcome."""
    count = len(rows) - 1
    level = (
        f"{confidence * 100:.6g}% confidence"
        if count == 1
        else f"{each * 100:.6g}% confidence each, so that all {count} hold together at "
        f"{confidence * 100:.6g}% (Bonferroni)"
    )
    lines = [
        f"# Variants: {_cell(metric)}",
        "",
        f"Each variant is compared with the baseline, {_cell(rows[0][0])}, on {_cell(metric)} "
        f"over {n} questions: paired bootstrap, {resamples:,} resamples, seed {seed}, "
        f"at {level}.",
        "",
        # Metric names are the bench's own (or checked against its pattern).
        "| variant | " + " | ".join(columns) + f" | verdict | {_cell(metric)} difference |",
        "|---|" + "---:|" * len(columns) + "---|---|",
    ]
    for name, values, outcome in rows:
        numbers = " | ".join(
            _number(column, value) for column, value in zip(columns, values, strict=True)
        )
        if outcome is None:
            verdict, difference = "baseline", ""
        else:
            word, mean, low, high = outcome
            verdict, difference = f"**{word}**", f"{mean:+.3f} ({low:+.3f} to {high:+.3f})"
        lines.append(f"| {_cell(name)} | {numbers} | {verdict} | {difference} |")
    return "\n".join(lines) + "\n"


def _number(metric: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}" if metric.startswith("latency") else f"{value:.3f}"


def _cell(text: str) -> str:
    """``text`` (a group name from a question file) safe in a table cell.

    Characters that end a line or reorder text become spaces; HTML special
    characters become entities; the Markdown characters that make a column,
    code, a link, an image, emphasis or a heading are escaped; a long name is
    cut. So a name cannot add a column, start a line, or carry markup into a
    published summary. A bare URL may still be shown as a link by renderers
    that autolink, as it would in any text.
    """
    spaced = "".join(" " if _breaks(char) else char for char in text)
    # Cut the visible text first, so escaping never splits a mark in two.
    visible = (
        spaced if len(spaced) <= _LONGEST else spaced[: _LONGEST - 1] + "\N{HORIZONTAL ELLIPSIS}"
    )
    escaped = visible.replace("\\", "\\\\")
    for char in "|`[]!*_#~":
        escaped = escaped.replace(char, "\\" + char)
    return escaped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


#: Longest a name is shown in a table, in visible characters.
_LONGEST = 60

#: Format characters that reorder how text displays (bidirectional controls).
#: Other format characters, such as the joiners inside emoji and Persian
#: words, are kept: they cannot break a line.
_REORDERING = frozenset(
    chr(point) for point in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)


def _breaks(char: str) -> bool:
    """Whether ``char`` is a control character, ends a line, or reorders text."""
    return unicodedata.category(char) in {"Cc", "Zl", "Zp"} or char in _REORDERING

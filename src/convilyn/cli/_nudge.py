"""A single, once-ever pointer at ``convilyn feedback``.

A survey nobody can find collects nothing, and ``convilyn --help`` is not where
people look while they are busy converting a file. So the offer is made once, at
the end of the third successful local conversion — late enough that the person
has actually used the thing they would be answering about.

## Why the counter lives in the CACHE root

`config_root()` holds `credentials.json` and **only** that: two assertions in
`tests/unit/_internal/test_credentials.py` compare `iterdir()` for exact
equality with `["credentials.json"]`, on the grounds that a stray file beside a
secret is a second copy of a secret nothing would clean up. A counter dropped
there turns both red.

The cache root is the right home on its own merits, not just as somewhere else
to put it: losing this file costs one extra hint, which is exactly the kind of
loss a cache is allowed to have.

## Why it never raises

This runs *after* a conversion has already succeeded and been reported. A
failure to write a hint counter must not turn a completed conversion into an
error, so every path here swallows. The worst outcome is that the hint appears
once more, or never.
"""

from __future__ import annotations

import sys
from pathlib import Path

from convilyn.cli._output import write_line
from convilyn.local._run import _cache_root

#: Shown on the Nth success, once. Third rather than first: the first
#: conversion is someone finding out whether this works at all, and asking them
#: what would make it better is asking before they know.
SHOW_ON_RUN = 3

_COUNTER_NAME = "feedback-nudge.count"

_MESSAGE = "\nEnjoying this? `convilyn feedback` — three questions, about 30 seconds."


def note_successful_conversion(*, json_output: bool, cache_root: Path | None = None) -> None:
    """Count one success and, on exactly the Nth, print the offer to stderr.

    ``json_output`` suppresses it entirely: ``JsonRenderer`` promises a single
    JSON document on stdout, and while this writes to stderr, a caller who asked
    for machine output asked not to be talked to.

    ``cache_root`` is a parameter rather than a module lookup so tests can point
    it at a tmp dir without patching a private function in another package.
    """
    if json_output:
        return
    try:
        counter = (cache_root or _cache_root()) / _COUNTER_NAME
        count = _read(counter) + 1
        _write(counter, count)
        if count == SHOW_ON_RUN:
            write_line(_MESSAGE, sys.stderr)
    except Exception:  # pragma: no cover - a hint must never fail a conversion
        return


def _read(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        # Absent, unreadable, or hand-edited to nonsense. Starting over costs
        # one delayed hint; refusing to count would cost the hint entirely.
        return 0


def _write(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count), encoding="utf-8")

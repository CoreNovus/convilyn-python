"""Every documented `convilyn ...` line must be an invocation the CLI accepts.

An audit found three defects "by running the commands rather than reading them",
and two of them were documentation: `docs/README.md` showed `goals start` with
a positional argument the command does not take, and `docs/QUICKSTART.md`
quoted a glob in a `local batch` example, which suppresses the shell expansion
that `nargs=-1` exists to receive. Both shipped in the sdist
(`pyproject.toml`'s sdist `include` lists `docs`), so both were in the
published artifact.

Neither was reachable by any existing check. `test_examples_syntax.py`'s
`_PYTHON_BLOCK` matches ```python fences only, so nothing in this package has
ever looked at a shell fence — its comment even states the assumption out loud,
that a python block is "the only place in a markdown file where a line is code
a reader would run". A `bash` fence is exactly that too.

**Nothing here executes anything.** The commands are resolved against the real
`click` tree and their arguments parsed statically, so this stays an offline
unit-speed check with no network, no filesystem writes and no credentials. That
also means it cannot assert a command *works* — only that a reader who copies
the line does not get a usage error.

Two checks, because the two defects fail differently:

* `TestEveryDocumentedInvocationParses` — the command path resolves and every
  flag it uses is declared. Catches defect 2 (`Got unexpected extra argument`).
* `TestNoQuotedGlobReachesAPathArgument` — a quoted glob passed to a
  `click.Path` positional. Catches defect 3, which is a *value* defect and
  therefore invisible to the first check.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import pytest

from convilyn.cli.main import cli

_DOCS = Path(__file__).resolve().parents[2] / "docs"

#: Shell fences. `console` is included — QUICKSTART uses it for transcripts, and
#: a transcript's command lines are still lines a reader copies.
_SHELL_FENCE = re.compile(r"^```(?:bash|sh|shell|console)\n(.*?)^```", re.MULTILINE | re.DOTALL)

#: Opt out of one fence by putting this immediately above it. Markers rather
#: than prose-sniffing, following `test_quickstart_exception_list.py`, whose
#: docstring argues the case: a scan that guesses which lines are real is a
#: scan that silently stops covering things.
_SKIP_MARKER = "<!-- cli-check: skip -->"

#: `convilyn-author` is a DIFFERENT package with its own CLI; it is documented
#: here only to point readers at it.
_FOREIGN_PREFIXES = ("convilyn-author",)

_GLOB_CHARS = set("*?[")


def _doc_files() -> list[Path]:
    """Every shipped markdown doc. `docs/internal/` is excluded from the sdist
    (`pyproject.toml`), so a reader never receives it."""
    return sorted(p for p in _DOCS.rglob("*.md") if "internal" not in p.parts)


def _shell_lines(text: str) -> list[str]:
    """Command lines from every shell fence, with continuations joined."""
    out: list[str] = []
    for block in _SHELL_FENCE.findall(_strip_skipped_fences(text)):
        joined = block.replace("\\\n", " ")
        for raw in joined.splitlines():
            line = raw.strip()
            if line.startswith("$ "):
                line = line[2:].strip()
            #: An output line does not start with the command name, which is
            #: what makes this discriminating without a `$ ` prompt convention
            #: — QUICKSTART has command lines with no prompt AND fences that
            #: are almost entirely output.
            if line.startswith("convilyn") and not line.startswith(_FOREIGN_PREFIXES):
                out.append(line)
    return out


def _strip_skipped_fences(text: str) -> str:
    """Drop any fence preceded by the opt-out marker."""
    return re.sub(
        re.escape(_SKIP_MARKER) + r"\n+```(?:bash|sh|shell|console)\n.*?^```",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )


def _argv(line: str) -> list[str]:
    """The `convilyn ...` argv, with shell plumbing removed.

    Truncated at the first pipe or `>` because what follows belongs to `jq` or
    the shell, not to this CLI, and leading `VAR=value` assignments are dropped
    for the same reason.
    """
    head = re.split(r"[|>]", line, maxsplit=1)[0]
    try:
        #: ``comments=True`` so a trailing ``# just the audio`` is not read as
        #: five positional arguments -- while a ``#`` inside quotes survives,
        #: which a regex strip would have eaten.
        tokens = shlex.split(head, comments=True)
    except ValueError:  # pragma: no cover - an unbalanced quote in a doc
        pytest.fail(f"documented line does not lex as shell: {line!r}")
    while tokens and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    return tokens[1:]  # drop "convilyn" itself


def _resolve(argv: list[str]) -> tuple[click.Command, list[str]]:
    """Walk the click group tree; return the command and its remaining args."""
    node: click.Command = cli
    rest = list(argv)
    while rest and isinstance(node, click.Group):
        child = node.get_command(click.Context(node), rest[0])
        if child is None:
            break
        node = child
        rest.pop(0)
    return node, rest


def _quoted_tokens(line: str) -> list[str]:
    """Tokens the doc wrapped in quotes — the shell would NOT expand these."""
    return [q or d for q, d in re.findall(r"'([^']*)'|\"([^\"]*)\"", line)]


_LINES: list[tuple[str, str]] = [
    (path.name, line) for path in _doc_files() for line in _shell_lines(path.read_text("utf-8"))
]


# ── 0. Non-vacuity — the scan must actually be scanning ──────────────


class TestTheScanIsNotVacuous:
    """Run FIRST, deliberately. Both checks below are absence assertions over
    `_LINES`; an empty `_LINES` makes them vacuously true, and every one of
    this repo's recorded observable failures has that shape.
    """

    def test_it_found_the_docs(self) -> None:
        assert len(_doc_files()) >= 4

    def test_it_found_a_substantial_number_of_invocations(self) -> None:
        #: A deliberate floor, not today's count: 40+ were measured while
        #: writing this. If a refactor drops the extraction to a handful, this
        #: fails instead of quietly passing.
        assert len(_LINES) >= 25, f"only {len(_LINES)} documented invocations found"

    def test_it_reaches_both_docs_that_carried_a_defect(self) -> None:
        found = {name for name, _ in _LINES}
        assert {"README.md", "QUICKSTART.md"} <= found

    def test_the_resolver_can_actually_resolve(self) -> None:
        """Guards the walker itself: if `_resolve` ever returned the root group
        for everything, both checks below would pass while testing nothing."""
        node, rest = _resolve(["goals", "start", "--dry-run"])
        assert node.name == "start"
        assert rest == ["--dry-run"]


# ── 1. Logic — every documented invocation parses ─────────────────────


class TestEveryDocumentedInvocationParses:
    @pytest.mark.parametrize(
        ("doc", "line"), _LINES, ids=[f"{d}:{i}" for i, (d, _) in enumerate(_LINES)]
    )
    def test_the_cli_accepts_it(self, doc: str, line: str) -> None:
        argv = _argv(line)
        command, rest = _resolve(argv)

        assert command is not cli or not argv, (
            f"{doc}: `convilyn {' '.join(argv)}` names no command"
        )

        declared = {opt for param in command.params for opt in (*param.opts, *param.secondary_opts)}
        used = {tok.split("=", 1)[0] for tok in rest if tok.startswith("-")}
        unknown = sorted(used - declared)
        assert not unknown, f"{doc}: `{command.name}` has no {', '.join(unknown)} — {line!r}"

        positional_slots = sum(
            1 for p in command.params if isinstance(p, click.Argument) and p.nargs != -1
        )
        takes_variadic = any(
            isinstance(p, click.Argument) and p.nargs == -1 for p in command.params
        )
        #: Values belonging to a preceding option are not positionals. Counted
        #: by walking rather than guessed, because miscounting here is how a
        #: check like this starts reporting false positives and gets deleted.
        given = _positionals_only(command, rest)
        if not takes_variadic:
            assert len(given) <= positional_slots, (
                f"{doc}: `{command.name}` takes {positional_slots} positional "
                f"argument(s), the doc passes {len(given)} ({given}) — {line!r}"
            )


def _positionals_only(command: click.Command, rest: list[str]) -> list[str]:
    """Tokens that are real positionals, skipping each option's own value."""
    wants_value = {
        opt: not param.is_flag if isinstance(param, click.Option) else False
        for param in command.params
        for opt in (*param.opts, *param.secondary_opts)
    }
    out: list[str] = []
    expect_value = False
    for token in rest:
        if expect_value:
            expect_value = False
            continue
        if token.startswith("-"):
            expect_value = wants_value.get(token, False) and "=" not in token
            continue
        out.append(token)
    return out


# ── 2. Boundary — a quoted glob is a different defect ────────────────


class TestNoQuotedGlobReachesAPathArgument:
    """Defect 3's shape, which check 1 cannot see.

    `local batch` declares `nargs=-1` with `click.Path(exists=True)`: it wants
    the SHELL to expand the glob and hand it a file list. Quoting the pattern
    passes the literal string through, `exists=True` refuses it, and the reader
    gets `Path 'docs/*.pdf' does not exist`. The invocation parses perfectly —
    it is the value that is wrong.
    """

    @pytest.mark.parametrize(
        ("doc", "line"), _LINES, ids=[f"{d}:{i}" for i, (d, _) in enumerate(_LINES)]
    )
    def test_globs_are_left_unquoted(self, doc: str, line: str) -> None:
        quoted = [tok for tok in (_quoted_tokens(line) or []) if _GLOB_CHARS & set(tok)]
        if not quoted:
            return
        command, _ = _resolve(_argv(line))
        path_args = [
            p
            for p in command.params
            if isinstance(p, click.Argument) and isinstance(p.type, click.Path)
        ]
        assert not path_args, (
            f"{doc}: `{command.name}` takes a path argument and the doc quotes the glob "
            f"{quoted!r}, so the shell will not expand it — drop the quotes. Line: {line!r}"
        )

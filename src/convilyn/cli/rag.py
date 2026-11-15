"""``convilyn rag`` — retrieval tools that run on this machine.

``convilyn rag bench demo`` evaluates keyword search on the documents and
questions that ship with the package; ``convilyn rag bench run`` evaluates
your own corpus (a ``rag.toml``) on your own questions. Both write
``summary.md`` and ``report.json``. ``convilyn rag bench compare`` runs the
questions on several ``rag.toml`` files and compares each with the first.

**Module scope imports nothing from** ``convilyn.rag``: ``cli/main.py``
imports this module to register the group, and ``convilyn --version`` must
stay as light as it is. The commands import what they use.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

import click

from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_NOT_COMPARABLE,
    EXIT_OK,
    EXIT_USAGE,
)

if TYPE_CHECKING:
    from convilyn.rag.bench import Pin, Question, Report
    from convilyn.rag.types import SourceInfo

_OUT_OPTION_HELP = (
    "Directory to write summary.md and report.json to (created if missing; "
    "files already there are replaced)."
)


@click.group(help="Retrieval tools that run on this machine.")
def rag_command() -> None:
    """Retrieval: evaluate search over documents with `convilyn rag bench`."""


@rag_command.group("bench", help="Measure how often search finds the passages that answer.")
def bench_command() -> None:
    """Evaluate retrieval on a question set."""


@bench_command.command(
    "demo",
    help=(
        "Evaluate keyword search on the documents and questions that ship with "
        "the package. No API key, no network.\n\nExample: convilyn rag bench demo"
    ),
)
@click.option(
    "--out",
    "out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("bench-demo"),
    show_default=True,
    help=_OUT_OPTION_HELP,
)
def demo_command(out: Path) -> None:
    from convilyn.rag.bench._sample import demo  # noqa: PLC0415 — see module docstring

    try:
        report = demo()
    except OSError:
        _fail("could not read the demo files that ship with the package", EXIT_JOB_FAILED)
    _write(report, out)


@bench_command.command(
    "run",
    help=(
        "Evaluate the corpus a rag.toml describes on your question file.\n\n"
        "Example: convilyn rag bench run --config rag.toml --questions questions.jsonl"
    ),
)
@click.option(
    "--config",
    "config",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The rag.toml that opens your corpus (already indexed).",
)
@click.option(
    "--questions",
    "questions",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The question file, one JSON object per line.",
)
@click.option(
    "--out",
    "out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("bench-run"),
    show_default=True,
    help=_OUT_OPTION_HELP,
)
def run_command(config: Path, questions: Path, out: Path) -> None:
    report, _, _ = _measure(config, questions)
    _write(report, out)


_CONFIG_OPTION = click.option(
    "--config",
    "config",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The rag.toml that opens your corpus (already indexed).",
)
_QUESTIONS_OPTION = click.option(
    "--questions",
    "questions",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The question file, one JSON object per line.",
)


@bench_command.command(
    "pin",
    help=(
        "Run your questions on your corpus and keep the result as the standard "
        "`check` holds later runs to.\n\n"
        "Example: convilyn rag bench pin --config rag.toml --questions questions.jsonl"
    ),
)
@_CONFIG_OPTION
@_QUESTIONS_OPTION
@click.option(
    "--out",
    "out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("bench-pin.json"),
    show_default=True,
    help="Where to write the pin (replaced if it exists).",
)
def pin_command(config: Path, questions: Path, out: Path) -> None:
    from convilyn.rag import bench  # noqa: PLC0415 — see module docstring

    report, sources, asked = _measure(config, questions)
    standard = _guarded(
        lambda: bench.pin(report, questions=asked, sources=sources, settings=config)
    )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        _replace(out, standard.model_dump_json(indent=2) + "\n")
    except OSError:
        _fail(f"could not write the pin to {out}", EXIT_JOB_FAILED)
    ndcg = report.metrics.get(standard.metric)
    shown = "n/a" if ndcg is None else f"{ndcg:.3f}"
    click.echo(f"pinned {standard.metric} {shown} over {len(report.results)} questions")
    click.echo(f"wrote {out}")


@bench_command.command(
    "check",
    help=(
        "Run your questions on your corpus and compare with a pin. Exit 0 when "
        "retrieval held, 3 when it is worse, 4 when the questions or documents "
        "changed since the pin.\n\n"
        "Example: convilyn rag bench check --config rag.toml --questions "
        "questions.jsonl --pin bench-pin.json"
    ),
)
@_CONFIG_OPTION
@_QUESTIONS_OPTION
@click.option(
    "--pin",
    "pin_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The pin written by `convilyn rag bench pin`.",
)
def check_command(config: Path, questions: Path, pin_file: Path) -> None:
    from convilyn.rag import bench  # noqa: PLC0415 — see module docstring

    standard = _load_pin(pin_file)
    report, sources, asked = _measure(config, questions)
    outcome = _guarded(
        lambda: bench.check(standard, report, questions=asked, sources=sources, settings=config)
    )
    click.echo(outcome.to_markdown())
    codes = {"held": EXIT_OK, "worse": EXIT_JOB_FAILED, "not_comparable": EXIT_NOT_COMPARABLE}
    sys.exit(codes[outcome.outcome])


@bench_command.command(
    "compare",
    help=(
        "Run your questions on each corpus the rag.toml files describe and compare "
        "every one with the first. Each variant is named by its file's stem.\n\n"
        "Example: convilyn rag bench compare --questions questions.jsonl "
        "rag.toml rag-rerank.toml"
    ),
)
@_QUESTIONS_OPTION
@click.argument(
    "configs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--out",
    "out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("bench-compare"),
    show_default=True,
    help=(
        "Directory to write summary.md and report-1.json, report-2.json, ... to, "
        "numbered in the order given (created if missing; files already there are replaced)."
    ),
)
@click.option(
    "--estimate",
    is_flag=True,
    help=(
        "Only count the calls to model services each variant would make, from the "
        "settings and the question file; open and send nothing."
    ),
)
def compare_command(questions: Path, configs: tuple[Path, ...], out: Path, estimate: bool) -> None:
    from convilyn.rag import bench  # noqa: PLC0415 — see module docstring

    if len(configs) < 2:
        _fail("compare needs at least two rag.toml files: a baseline and a variant", EXIT_USAGE)
    names = [config.stem for config in configs]
    if len(set(names)) != len(names):
        _fail(
            "two rag.toml files have the same name; each variant is named by its file's stem",
            EXIT_USAGE,
        )
    asked = _asked(questions)
    if estimate:
        _print_estimates(dict(zip(names, configs, strict=True)), asked)
        return
    from convilyn.rag.bench._compare import check_plan  # noqa: PLC0415 — see module docstring

    answerable = sum(1 for question in asked if question.status == "ready" and question.support)
    with _handled():
        # Before the first run: a run can call paid services.
        check_plan(len(configs), answerable)
    reports = {
        name: _measured(config, asked)[0] for name, config in zip(names, configs, strict=True)
    }
    with _handled():
        variants = bench.compare_variants(reports)
    files = {"summary.md": variants.to_markdown()}
    for number, report in enumerate(reports.values(), start=1):
        files[f"report-{number}.json"] = report.model_dump_json(indent=2) + "\n"
    _write_files(out, files)
    for name, comparison in variants.comparisons.items():
        click.echo(
            f"{name}: {comparison.verdict} ({comparison.mean_difference:+.3f} {variants.metric})"
        )
    click.echo(f"wrote {out / 'summary.md'}")


def _print_estimates(configs: Mapping[str, Path], asked: tuple[Question, ...]) -> None:
    """Say, for each variant, the calls a run would make to model services."""
    from convilyn.rag.bench._estimate import calls  # noqa: PLC0415 — see module docstring

    for name, config in configs.items():
        with _handled():
            counted = calls(config, asked)
        parts = []
        if counted.embed_calls:
            parts.append(
                f"{counted.embed_calls:,} embedding calls "
                f"(about {counted.embed_tokens:,} input tokens)"
            )
        if counted.rerank_calls:
            parts.append(
                f"{counted.rerank_calls:,} reranking calls "
                f"(at most {counted.rerank_documents:,} documents)"
            )
        click.echo(f"{name}: {', '.join(parts) or 'no calls to a model service'}")
    click.echo("Counted from the settings and the question file; nothing was opened or sent.")
    click.echo("Retries are not counted, and prices are the provider's.")


def _measure(
    config: Path, questions: Path
) -> tuple[Report, tuple[SourceInfo, ...], tuple[Question, ...]]:
    """Open the corpus ``config`` describes, run ``questions``, read its documents.

    The question file is read once, and what was read is returned with the
    report, so a pin describes exactly the questions its report ran.
    """
    asked = _asked(questions)
    report, sources = _measured(config, asked)
    return report, sources, asked


def _asked(questions: Path) -> tuple[Question, ...]:
    """The questions in the file ``questions``, read once."""
    from convilyn.rag import bench  # noqa: PLC0415 — see module docstring

    with _handled():
        return bench.questions(questions)


def _measured(config: Path, asked: tuple[Question, ...]) -> tuple[Report, tuple[SourceInfo, ...]]:
    """Open the corpus ``config`` describes, run ``asked``, read its documents."""
    from convilyn import rag  # noqa: PLC0415 — see module docstring
    from convilyn.rag import bench  # noqa: PLC0415

    with _handled(), rag.open(config) as corpus:
        return bench.run(retrieve=corpus.search, questions=asked), corpus.sources()


@contextmanager
def _handled() -> Iterator[None]:
    """Turn the failures a bench step expects into one line and an exit code."""
    from convilyn.exceptions import ConvilynError  # noqa: PLC0415 — see module docstring
    from convilyn.providers import ProviderError  # noqa: PLC0415

    try:
        yield
    except ProviderError as error:
        # A model service could not be reached or refused: not a usage error.
        _fail(str(error), EXIT_API_ERROR)
    except (ValueError, ConvilynError) as error:
        # Both say what is wrong in fixed words; neither repeats a value or a key.
        _fail(str(error), EXIT_USAGE)
    except OSError:
        _fail("could not read the settings file, the question file or the index", EXIT_USAGE)


_Value = TypeVar("_Value")


def _guarded(step: Callable[[], _Value]) -> _Value:
    """Run a pin or check step, which reads the settings file again.

    Only a failure to read it is expected here; anything else is a defect
    and is raised as one, not dressed up as a file problem.
    """
    try:
        return step()
    except OSError:
        _fail("could not read the settings file", EXIT_USAGE)


def _load_pin(path: Path) -> Pin:
    """The pin at ``path``, or a one-line refusal that quotes nothing from it."""
    from pydantic import ValidationError  # noqa: PLC0415

    from convilyn.rag.bench import Pin  # noqa: PLC0415 — see module docstring

    try:
        with path.open("rb") as stream:
            # Read one byte past the limit, so a larger file is known without
            # reading it all, whatever its size was a moment before.
            data = stream.read(_LARGEST_PIN + 1)
        if len(data) > _LARGEST_PIN:
            _fail(f"{path} is larger than a bench pin can be", EXIT_USAGE)
        return Pin.model_validate_json(data)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        problem = f"{path} is not a bench pin ({_where(first)}: {first['msg']})"
    except OSError:
        problem = f"could not read the pin {path}"
    # Raised outside the handlers: the validation error holds the file's values.
    _fail(problem, EXIT_USAGE)


#: A pin holds one report; far beyond any real one, and small enough to read.
_LARGEST_PIN = 16 * 1024 * 1024


def _where(problem: Mapping[str, Any]) -> str:
    """Where in a pin ``problem`` is, naming only the pin's own fields.

    Keys deeper in the file, and unknown ones, are the file's own text.
    """
    from convilyn.rag.bench import Pin  # noqa: PLC0415 — see module docstring

    location = problem["loc"]
    top = location[0] if location else None
    if problem["type"] == "extra_forbidden" and len(location) == 1:
        return "unknown field"
    return top if isinstance(top, str) and top in Pin.model_fields else "the file"


def _write(report: Report, out: Path) -> None:
    """Write the report's two files under ``out`` and say where they are."""
    _write_files(
        out,
        {
            "summary.md": report.to_markdown(),
            "report.json": report.model_dump_json(indent=2) + "\n",
        },
    )
    ndcg = report.metrics.get("ndcg@10")
    shown = "n/a" if ndcg is None else f"{ndcg:.3f}"
    click.echo(f"ndcg@10 {shown} over {len(report.results)} questions")
    click.echo(f"wrote {out / 'summary.md'} and {out / 'report.json'}")


def _write_files(out: Path, files: Mapping[str, str]) -> None:
    """Put each of ``files`` (name to text) in the directory ``out``."""
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            _replace(out / name, text)
    except OSError:
        _fail(f"could not write the report to {out}", EXIT_JOB_FAILED)


def _replace(target: Path, text: str) -> None:
    """Put ``text`` at ``target`` by renaming a new file over it.

    A rename replaces whatever is at ``target``, a symbolic link included,
    rather than writing through it, so a link planted in a shared output
    directory cannot redirect the write; and a reader never sees half a file.
    """
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".bench-", suffix=target.suffix)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(name, target)
    except BaseException:
        # Writing, closing or renaming failed (a full disk, an interrupt):
        # leave nothing half-written behind in the user's directory.
        Path(name).unlink(missing_ok=True)
        raise


def _fail(message: str, code: int) -> NoReturn:
    """Say what went wrong in one line and exit with ``code``."""
    click.echo(f"error: {message}", err=True)
    sys.exit(code)

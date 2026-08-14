"""Convert files on this machine. No API key, no network, no account.

    from convilyn import local

    local.convert("report.docx", to="md")
    local.convert_many(Path("in").glob("*.xlsx"), to="md", out_dir="out")

## Why these functions are synchronous

The rest of this SDK is async-primary because it is IO-bound over HTTP: the
useful thing an ``await`` does there is let the event loop run something else
while the network answers. Conversion is CPU- and subprocess-bound, which
inverts the argument. An ``async def`` that ran pdfplumber inline would satisfy
the letter of that convention while blocking the loop for seconds — a coroutine
that lies about yielding is worse than a plain function.

The only honest asynchronous form is running the synchronous implementation off
the loop, so the synchronous one has to exist either way. ``aconvert`` and
``aconvert_many`` do exactly that and nothing else.

## What raises and what does not

``capabilities()`` and ``plan()`` never raise. They answer questions about the
machine, and a missing dependency is an answer.

``convert()`` raises, because a one-shot call that quietly returns a failure is
easy to ignore. ``convert_many()`` does not, because a five-hundred-file batch
that stops on file three is not a batch — each failure becomes a result with
``ok=False``, and ``raise_on_error=True`` opts back into stopping.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path

from convilyn.local._engine.formats import (
    IMAGE_FORMAT_ALIASES,
    DocumentFormat,
    ImageFormat,
)
from convilyn.local._routes import MARKDOWN, build_capabilities
from convilyn.local._run import ToolNotInstalledError
from convilyn.local._runners import RUNNERS
from convilyn.local.errors import (
    ConversionFailedError,
    MissingDependencyError,
    UnsupportedRouteError,
)
from convilyn.local.types import (
    Capabilities,
    ConversionResult,
    Engine,
    LocalErrorInfo,
    ProgressEvent,
    Route,
)

#: Called once per file in a batch, three times per file.
ProgressCallback = Callable[[ProgressEvent], None]


# ── Introspection — never raises ─────────────────────────────────────


@cache
def capabilities() -> Capabilities:
    """What this machine can convert right now, and what each route needs.

    Cached for the process, and measurably so: building the table probes every
    Pillow codec with a one-pixel save, which costs ~160 ms the first time.
    ``convert()`` consults it through ``plan()``, so an uncached build would
    charge a hundred-file batch sixteen seconds to learn the same thing a
    hundred times.

    Caching is honest here rather than merely convenient: Pillow's plugins
    register at import, and an external program does not usually appear
    mid-run. A caller who installs something and wants the new answer restarts,
    which is what they would do anyway. Tests that need a different machine
    inject probes into ``_routes.build_capabilities`` and bypass this entirely.
    """
    return build_capabilities()


def detect_format(source: str | Path) -> str | None:
    """The source format of ``source``, from its extension, or ``None``.

    Extension-based. Content sniffing would be more robust against a mislabelled
    file, and is deliberately not done here: every caller of this function is
    about to be told, precisely, that the format is unsupported — which is a
    better outcome than silently converting a file the user believes is
    something else.
    """
    suffix = Path(source).suffix.lstrip(".").lower()
    if not suffix:
        return None

    # Alias first: `jpeg`, `tif` and friends name a format whose canonical
    # spelling is something else, and every table downstream is keyed by the
    # canonical one.
    alias = IMAGE_FORMAT_ALIASES.get(suffix)
    if alias is not None:
        return alias.value

    for enum in (DocumentFormat, ImageFormat):
        try:
            return enum(suffix).value
        except ValueError:
            continue
    return None


def plan(source: str | Path, *, to: str = MARKDOWN, source_format: str | None = None) -> Route:
    """The route ``convert`` would take, without taking it.

    Raises:
        UnsupportedRouteError: this engine has no such route. That is not a
            machine fact — no installation makes it work — so it is the one
            question here that answers with an exception.
    """
    resolved = source_format or detect_format(source)
    caps = capabilities()

    if resolved is None:
        raise UnsupportedRouteError(
            source_format=Path(source).suffix or "(no extension)",
            target_format=to,
        )

    route = caps.can(resolved, to)
    if route is None:
        raise UnsupportedRouteError(
            source_format=resolved,
            target_format=to,
            supported_targets=caps.available_targets(resolved),
        )
    return route


# ── Conversion ───────────────────────────────────────────────────────


def convert(
    source: str | Path,
    *,
    to: str | None = None,
    out: str | Path | None = None,
    source_format: str | None = None,
    overwrite: bool = False,
) -> ConversionResult:
    """Convert one file and return where it landed.

    Exactly one of ``to`` and ``out`` determines the target: ``to`` names the
    format and the output sits beside the input; ``out`` names the path and the
    format comes from its suffix. Passing neither is an error rather than a
    default, because guessing what somebody wanted is how a converter overwrites
    the wrong file.

    Raises:
        UnsupportedRouteError: no such route in this engine.
        MissingDependencyError: the route exists but something it needs is not
            installed. Its message is the route's own ``unavailable_reason``.
        ConversionFailedError: the route ran and could not produce output.
        FileNotFoundError: ``source`` does not exist.
        FileExistsError: the output exists and ``overwrite`` is false.
    """
    source_path = Path(source)
    target_format, output_path = _resolve_target(source_path, to=to, out=out)
    route = plan(source_path, to=target_format, source_format=source_format)

    if not source_path.is_file():
        raise FileNotFoundError(f"no such file: {source_path}")
    # ONE field decides both branches: the route already worked out why it cannot
    # run, and this reads that answer instead of re-deriving it from `missing`.
    # `missing_requirement` is the one kind whose fix is an extra of this package,
    # which is exactly the line the error taxonomy is drawn on — see
    # `errors.UnsupportedRouteError` for why it is "is it one of our extras"
    # rather than "is it fixable".
    #
    # Re-deriving is what shipped a bug. This guarded on `route.missing`, which is
    # empty for a route that is unavailable with nothing missing — Pillow present,
    # this build unable to write PSD — so that pair reached the encoder and
    # surfaced as `KeyError: 'PSD'` wrapped in "conversion failed": a run-time
    # crash standing in for a question the route table had already answered.
    if route.unavailable_kind == "missing_requirement":
        raise MissingDependencyError(route=route, requirement=route.missing[0])
    if route.unavailable_kind is not None:
        raise UnsupportedRouteError(
            source_format=route.source_format,
            target_format=route.target_format,
            reason=route.unavailable_reason,
        )
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} exists; pass overwrite=True to replace it")

    started = time.monotonic()
    try:
        warnings, written = _run(source_path, route, output_path)
    except ToolNotInstalledError as exc:
        # `capabilities()` said the program was there and it was gone by the
        # time we ran it. Rare, but it is a missing dependency rather than a
        # conversion that failed, and the caller acts on those differently.
        raise MissingDependencyError(route=route, requirement=route.requirements[0]) from exc
    except Exception as exc:  # noqa: BLE001 — every extractor failure is one outcome
        raise ConversionFailedError(source=str(source_path), route=route, message=str(exc)) from exc

    return ConversionResult(
        ok=True,
        source=source_path,
        source_format=route.source_format,
        target_format=route.target_format,
        engine=route.engine,
        elapsed_seconds=time.monotonic() - started,
        output=written,
        warnings=warnings,
    )


def convert_many(
    sources: Iterable[str | Path],
    *,
    to: str = MARKDOWN,
    out_dir: str | Path,
    source_format: str | None = None,
    overwrite: bool = False,
    on_progress: ProgressCallback | None = None,
    raise_on_error: bool = False,
) -> tuple[ConversionResult, ...]:
    """Convert many files into ``out_dir``, one result per input.

    Failures are results, not exceptions, unless ``raise_on_error`` is set. The
    caller gets the whole picture in one pass and decides what to do about the
    three files that did not work.
    """
    paths = [Path(s) for s in sources]
    destination = Path(out_dir)
    outputs = {path: destination / f"{path.stem}.{to}" for path in paths}
    _reject_output_collisions(outputs)
    destination.mkdir(parents=True, exist_ok=True)

    results: list[ConversionResult] = []
    for index, path in enumerate(paths):
        _emit(on_progress, index, len(paths), path, "start")
        try:
            result = convert(
                path,
                out=outputs[path],
                source_format=source_format,
                overwrite=overwrite,
            )
        except Exception as exc:  # noqa: BLE001 — recorded, not swallowed
            if raise_on_error:
                raise
            result = _failure(path, to, exc)
        _emit(on_progress, index, len(paths), path, "done" if result.ok else "failed")
        results.append(result)
    return tuple(results)


# ── Async wrappers — thread-offloading, nothing more ─────────────────


async def aconvert(source: str | Path, **kwargs: object) -> ConversionResult:
    """``convert`` on a worker thread, so the event loop keeps running."""
    return await asyncio.to_thread(convert, source, **kwargs)  # type: ignore[arg-type]


async def aconvert_many(
    sources: Iterable[str | Path], **kwargs: object
) -> tuple[ConversionResult, ...]:
    """``convert_many`` on a worker thread."""
    return await asyncio.to_thread(convert_many, sources, **kwargs)  # type: ignore[arg-type]


# ── Helpers (testable in isolation; SRP) ─────────────────────────────


def _resolve_target(source: Path, *, to: str | None, out: str | Path | None) -> tuple[str, Path]:
    """``(target_format, output_path)`` from whichever of ``to``/``out`` was given."""
    if to is None and out is None:
        raise ValueError("pass `to=` to name the target format, or `out=` to name the file")
    if out is not None:
        output = Path(out)
        target = to or output.suffix.lstrip(".").lower()
        if not target:
            raise ValueError(f"cannot infer a target format from {output}; pass `to=`")
        return target, output
    assert to is not None  # narrowed by the guard above
    return to, source.with_suffix(f".{to}")


def _reject_output_collisions(outputs: dict[Path, Path]) -> None:
    """Refuse a batch in which two inputs would write the same file.

    Output names come from the stem, so ``report.docx`` and ``report.pdf`` both
    want ``report.md``. Without ``overwrite`` the second raises ``FileExistsError``
    and the collision is at least visible. **With** it, the second silently
    replaces the first and the run reports converting both — which is how a batch
    loses a file while telling you it succeeded.

    Raised before anything is written, because a half-run batch is harder to
    reason about than one that refused.
    """
    seen: dict[Path, Path] = {}
    clashes: list[str] = []
    for source, output in outputs.items():
        first = seen.setdefault(output, source)
        if first != source:
            clashes.append(f"{first.name} and {source.name} → {output.name}")

    if clashes:
        raise ValueError(
            "these inputs would write the same output file: "
            + "; ".join(clashes)
            + ". Convert them in separate runs, or into separate directories."
        )


def _run(source: Path, route: Route, output: Path) -> tuple[tuple[str, ...], Path]:
    """Hand the conversion to the family that performs it.

    A lookup, not a branch. Each family — and what running one involves, including
    rendering and writing assets — lives in :mod:`convilyn.local._runners`; adding
    one is a row in its table, with nothing to change here.
    """
    return RUNNERS[route.engine](source, route, output)


def _failure(source: Path, target_format: str, exc: Exception) -> ConversionResult:
    """Turn an exception into the batch's per-file result."""
    requirement = getattr(exc, "requirement", None)
    source_format = detect_format(source) or ""
    return ConversionResult(
        ok=False,
        source=source,
        source_format=source_format,
        target_format=target_format,
        engine=_engine_of(source_format, target_format, exc),
        elapsed_seconds=0.0,
        error=LocalErrorInfo(
            code=type(exc).__name__,
            message=str(exc),
            requirement=requirement,
        ),
    )


def _engine_of(source_format: str, target_format: str, exc: Exception) -> Engine | None:
    """Which engine this failure belongs to, or ``None`` when there is no route.

    This used to be the literal ``"structured"``, which meant **every** failure in a
    batch reported that engine — a failed image conversion included. Nothing was
    computing it; the field simply asserted the most common answer, and
    ``convilyn local batch --json`` published it.

    Two honest sources, in order. Most of these exceptions carry the route they
    failed on, which is exact. Otherwise the pair is looked up in the route table,
    which is the same table that would have chosen the runner. ``None`` only when
    the engine genuinely does not exist — an unknown extension has no route, so any
    engine named for it would be invented.
    """
    route = getattr(exc, "route", None)
    if isinstance(route, Route):
        return route.engine
    known = capabilities().can(source_format, target_format)
    return known.engine if known else None


def _emit(
    callback: ProgressCallback | None, index: int, total: int, source: Path, phase: str
) -> None:
    if callback is not None:
        callback(
            ProgressEvent(index=index, total=total, source=source, phase=phase)  # type: ignore[arg-type]
        )


__all__ = [
    "ProgressCallback",
    "aconvert",
    "aconvert_many",
    "capabilities",
    "convert",
    "convert_many",
    "detect_format",
    "plan",
]

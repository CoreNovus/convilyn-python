"""``convilyn local` — convert files offline, with no API key.

Every sub-command here works without credentials, without network, and without
an account. That is the reason this is its own group rather than flags on
``convilyn convert``: the two answer to different halves of the product, and a
user reaching for the offline one should not be told to authenticate.

**Module scope must not reach an optional dependency.** ``cli/main.py`` imports
this module to register the group, and the release pipeline runs ``convilyn
--version`` against a wheel installed with **no extras at all**.

The types below are safe to import here, and that was measured rather than
assumed: importing ``convilyn.local`` pulls none of ``pdfplumber``, ``PIL``,
``pypdf``, ``docx``, ``pptx``, ``openpyxl`` or ``defusedxml``. The engine
imports each extractor lazily inside its registry entry, so the namespace costs
pydantic and nothing else. A packaging test holds that property, because it is
the kind that decays silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import click

from convilyn.cli._exit_codes import EXIT_JOB_FAILED, EXIT_USAGE
from convilyn.cli._output import OutputRenderer, make_renderer
from convilyn.local.types import ConversionResult, ProgressEvent, Requirement, Route

_JSON_OPTION = click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout (silences progress lines).",
)


@click.group(help="Convert files on this machine. No API key required.")
def local_command() -> None:
    """Offline conversion. Run `convilyn local doctor` to see what is available."""


# ── convert ──────────────────────────────────────────────────────────


@click.command(
    "convert",
    help=("Convert one file offline.\n\nExample: convilyn local convert report.docx --to md"),
)
@click.argument(
    "input_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option("--to", "target_format", help="Target format token (e.g. md).")
@click.option(
    "-o",
    "--out",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write here instead of beside the input.",
)
@click.option("--overwrite", is_flag=True, help="Replace an existing output file.")
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Show the route that would run, and write nothing.",
)
@_JSON_OPTION
def convert_command(
    input_file: Path,
    target_format: str | None,
    output_path: Path | None,
    overwrite: bool,
    dry_run: bool,
    json_output: bool,
) -> None:
    from convilyn import local

    renderer = make_renderer(json_output=json_output)

    try:
        route = local.plan(input_file, to=target_format or "md")
    except local.UnsupportedRouteError as exc:
        raise SystemExit(EXIT_USAGE) from _fail(renderer, "Unsupported conversion", exc)

    if dry_run:
        _emit_dry_run(renderer, input_file, route, output_path)
        return

    # `not available`, NOT `route.missing`. The two differ for a route that is
    # unavailable with nothing missing — Pillow present, this build unable to write
    # PSD — and guarding on `missing` let that state fall through to `convert()`,
    # which raises `UnsupportedRouteError`. That is not in the `except` below, so
    # the user got a Python traceback where every other refusal prints one line.
    #
    # This is the CLI half of the same defect `Route.unavailable_kind` exists to
    # remove: a consumer reading availability through one field could not see a
    # state the other field was carrying.
    if not route.available:
        raise SystemExit(EXIT_USAGE) from _fail(
            renderer, "Cannot convert here", RuntimeError(route.unavailable_reason or "")
        )

    renderer.event("create", message=f"Converting {input_file.name} → {route.target_format}")
    try:
        result = local.convert(
            input_file,
            to=None if output_path else route.target_format,
            out=output_path,
            overwrite=overwrite,
        )
    except (local.ConversionFailedError, OSError) as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _fail(renderer, "Conversion failed", exc)

    for warning in result.warnings:
        renderer.event("warn", message=warning)
    renderer.event("ok", message=f"Wrote {result.output}")
    renderer.final(
        {
            "command": "local convert",
            **_result_payload(result),
            # Every command supplies its own summary. Omitting it falls through
            # to the renderer's generic formatter, which emits a check glyph —
            # and that raises UnicodeEncodeError on a cp950 console.
            "summary": f"Converted {input_file.name} → {result.output}",
        }
    )


# ── batch ────────────────────────────────────────────────────────────


@click.command(
    "batch",
    help=(
        "Convert many files into one directory.\n\n"
        "Example: convilyn local batch docs/*.docx --to md --out-dir build/"
    ),
)
@click.argument(
    "inputs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option("--to", "target_format", default="md", show_default=True, help="Target format.")
@click.option(
    "--out-dir",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write into; created if absent.",
)
@click.option("--overwrite", is_flag=True, help="Replace existing output files.")
@click.option(
    "--stop-on-error",
    is_flag=True,
    help="Abort on the first failure instead of converting the rest.",
)
@_JSON_OPTION
def batch_command(
    inputs: tuple[Path, ...],
    target_format: str,
    out_dir: Path,
    overwrite: bool,
    stop_on_error: bool,
    json_output: bool,
) -> None:
    from convilyn import local

    renderer = make_renderer(json_output=json_output)

    def report(event: ProgressEvent) -> None:
        if event.phase == "start":
            renderer.event(
                "create", message=f"[{event.index + 1}/{event.total}] {event.source.name}"
            )

    try:
        results = local.convert_many(
            inputs,
            to=target_format,
            out_dir=out_dir,
            overwrite=overwrite,
            on_progress=report,
            raise_on_error=stop_on_error,
        )
    except ValueError as exc:
        # A bad batch, rejected before anything was written — two inputs whose
        # outputs would collide. That is the caller's arguments, not a failure
        # of any one conversion, so it is a usage error.
        raise SystemExit(EXIT_USAGE) from _fail(renderer, "Cannot run this batch", exc)
    except local.LocalError as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _fail(renderer, "Batch stopped", exc)

    failed = [r for r in results if not r.ok]
    for result in failed:
        renderer.event(
            "error",
            message=f"{result.source.name}: {result.error.message if result.error else 'failed'}",
        )

    converted = len(results) - len(failed)
    renderer.event("ok", message=f"Converted {converted} of {len(results)} files")
    renderer.final(
        {
            "command": "local batch",
            "converted": converted,
            "failed": len(failed),
            "results": [_result_payload(r) for r in results],
            "summary": f"Converted {converted} of {len(results)} files into {out_dir}.",
        }
    )

    if failed:
        raise SystemExit(EXIT_JOB_FAILED)


# ── formats ──────────────────────────────────────────────────────────


class _Line(NamedTuple):
    kind: str
    message: str


def _grouped_lines(routes: Sequence[Route]) -> list[_Line]:
    """One line per source format, not one per route.

    The image engine alone produces several hundred routes, and a flat list of
    them is a wall nobody reads — the answer a person wants from ``formats`` is
    "what can I do with a png", which is a row, not four hundred.

    An unavailable source collapses further, to the reason: every route out of
    it fails for the same cause, so repeating that cause once per target buries
    the one sentence that would fix it. The machine-readable payload stays flat,
    because a program wants the matrix rather than a summary of it.
    """
    by_source: dict[str, list[Route]] = {}
    for route in routes:
        by_source.setdefault(route.source_format, []).append(route)

    lines: list[_Line] = []
    for source, group in sorted(by_source.items()):
        targets = sorted({r.target_format for r in group if r.available})
        if targets:
            lines.append(_Line("ok", f"{source:>5} → {', '.join(targets)}"))
            continue
        reason = next((r.unavailable_reason for r in group if r.unavailable_reason), "")
        lines.append(_Line("warn", f"{source:>5} → unavailable. {reason}"))
    return lines


@click.command("formats", help="List every conversion this engine knows, and its status.")
@click.option("--from", "source_filter", help="Only show routes from this source format.")
@click.option("--available-only", is_flag=True, help="Hide routes that cannot run here.")
@_JSON_OPTION
def formats_command(source_filter: str | None, available_only: bool, json_output: bool) -> None:
    from convilyn import local

    renderer = make_renderer(json_output=json_output)
    routes = local.capabilities().routes

    if source_filter:
        routes = tuple(r for r in routes if r.source_format == source_filter)
    if available_only:
        routes = tuple(r for r in routes if r.available)

    for line in _grouped_lines(routes):
        renderer.event(line.kind, message=line.message)

    renderer.final(
        {
            "command": "local formats",
            "routes": [
                {
                    "source_format": r.source_format,
                    "target_format": r.target_format,
                    "engine": r.engine,
                    "available": r.available,
                    # Carried so a consumer of this payload can tell "install
                    # something" from "stop looking" without knowing what an
                    # engine is. Reading `available` alone cannot answer that,
                    # and reading `engine` to decide how to interpret it is the
                    # coupling this field exists to remove.
                    "unavailable_kind": r.unavailable_kind,
                    "unavailable_reason": r.unavailable_reason,
                }
                for r in routes
            ],
            "summary": (
                f"{sum(1 for r in routes if r.available)} of {len(routes)} routes "
                f"available on this machine."
            ),
        }
    )


# ── doctor ───────────────────────────────────────────────────────────


@click.command("doctor", help="Show what offline conversion can do here, and how to extend it.")
@_JSON_OPTION
def doctor_command(json_output: bool) -> None:
    from convilyn import local

    renderer = make_renderer(json_output=json_output)
    caps = local.capabilities()

    for requirement in caps.packages:
        renderer.event(
            "ok" if requirement.available else "warn",
            message=_requirement_line(requirement),
        )
    for requirement in caps.tools:
        renderer.event(
            "ok" if requirement.available else "warn",
            message=_requirement_line(requirement),
        )

    available = len(caps.available_routes)
    renderer.final(
        {
            "command": "local doctor",
            "packages": [_requirement_payload(r) for r in caps.packages],
            "tools": [_requirement_payload(r) for r in caps.tools],
            "routes_available": available,
            "routes_total": len(caps.routes),
            "summary": (
                f"{available} of {len(caps.routes)} conversions available. "
                f"Run `convilyn local formats` for the per-format detail."
            ),
        }
    )


# ── Helpers (testable in isolation; SRP) ─────────────────────────────


def _requirement_line(requirement: Requirement) -> str:
    """One diagnostic line, naming the fix when there is one."""
    if requirement.available:
        return f"{requirement.name}: installed"
    state = "optional — improves image handling" if requirement.optional else "missing"
    return f"{requirement.name}: {state} — {requirement.install_hint}"


def _requirement_payload(requirement: Requirement) -> dict[str, object]:
    return {
        "name": requirement.name,
        "kind": requirement.kind,
        "available": requirement.available,
        "optional": requirement.optional,
        "extra": requirement.extra,
        "install_hint": requirement.install_hint,
    }


def _result_payload(result: ConversionResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "source": str(result.source),
        "output": str(result.output) if result.output else None,
        "source_format": result.source_format,
        "target_format": result.target_format,
        "engine": result.engine,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "warnings": list(result.warnings),
        "error": (
            {"code": result.error.code, "message": result.error.message} if result.error else None
        ),
    }


def _emit_dry_run(
    renderer: OutputRenderer, source: Path, route: Route, output_path: Path | None
) -> None:
    """Print the resolved route and write nothing."""
    destination = output_path or source.with_suffix(f".{route.target_format}")

    renderer.event(
        "wait",
        message=(
            f"[dry-run] Would convert {source.name} → {destination.name} "
            f"using the {route.engine} engine"
        ),
    )
    if not route.available:
        renderer.event("warn", message=f"[dry-run] {route.unavailable_reason}")

    renderer.final(
        {
            "command": "local convert",
            "dry_run": True,
            "source": str(source),
            "output": str(destination),
            "engine": route.engine,
            "available": route.available,
            "unavailable_kind": route.unavailable_kind,
            "unavailable_reason": route.unavailable_reason,
            "summary": "[dry-run] Nothing was written.",
        }
    )


def _fail(renderer: OutputRenderer, prefix: str, exc: Exception) -> Exception:
    """Report an error and hand it back so the raise-from chain stays readable."""
    renderer.event("error", message=f"{prefix}: {exc}")
    return exc


# ── pdf ──────────────────────────────────────────────────────────────


@click.group("pdf", help="Rearrange PDF pages offline: merge, split, rotate, protect.")
def pdf_command() -> None:
    """Page operations. These change the pages, never the format."""


_PDF_INPUT = click.argument(
    "input_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
_PDF_OUTPUT = click.argument("output_file", type=click.Path(dir_okay=False, path_type=Path))
_PAGES_OPTION = click.option(
    "--pages",
    help='Pages to act on, 1-based: "3", "1-5", "1-3,7,10-12". Default: all.',
)


def _pdf_run(renderer: OutputRenderer, name: str, call, /, **payload: object) -> None:
    """Run one page operation and report it, or exit on a refusal.

    Every sub-command below is the same three steps in a different order, and
    the ordering is what matters: a refusal must reach the user as one line
    naming what was wrong, not as a traceback through the engine.
    """
    from convilyn import local

    try:
        result = call()
    except local.MissingDependencyError as exc:
        raise SystemExit(EXIT_USAGE) from _fail(renderer, "Cannot run this here", exc)
    except local.LocalError as exc:
        raise SystemExit(EXIT_USAGE) from _fail(renderer, f"Cannot {name} this PDF", exc)
    except OSError as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _fail(renderer, f"Could not {name}", exc)

    renderer.event("ok", message=str(payload.get("summary", f"{name} complete")))
    renderer.final({"command": f"local pdf {name}", "result": result, **payload})


@click.command("merge", help="Join several PDFs into one, in the order given.")
@click.argument(
    "inputs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-o",
    "--out",
    "output_file",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the joined document.",
)
@_JSON_OPTION
def pdf_merge_command(inputs: tuple[Path, ...], output_file: Path, json_output: bool) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "merge",
        lambda: str(pdf.merge(inputs, output_file)),
        sources=[str(p) for p in inputs],
        summary=f"Joined {len(inputs)} documents into {output_file}.",
    )


@click.command("select", help="Copy the chosen pages into a new PDF.")
@_PDF_INPUT
@_PDF_OUTPUT
@_PAGES_OPTION
@_JSON_OPTION
def pdf_select_command(
    input_file: Path, output_file: Path, pages: str | None, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "select",
        lambda: str(pdf.select(input_file, output_file, pages=pages)),
        source=str(input_file),
        pages=pages,
        summary=f"Wrote {output_file}.",
    )


@click.command("rotate", help="Turn pages a quarter, half or three-quarter turn.")
@_PDF_INPUT
@_PDF_OUTPUT
@click.option(
    "--degrees",
    type=click.Choice(["90", "180", "270"]),
    default="90",
    show_default=True,
    help="Clockwise rotation.",
)
@_PAGES_OPTION
@_JSON_OPTION
def pdf_rotate_command(
    input_file: Path, output_file: Path, degrees: str, pages: str | None, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "rotate",
        lambda: str(pdf.rotate(input_file, output_file, degrees=int(degrees), pages=pages)),
        source=str(input_file),
        degrees=int(degrees),
        summary=f"Wrote {output_file}.",
    )


@click.command("split", help="Write each page as its own PDF into a directory.")
@_PDF_INPUT
@click.option(
    "--out-dir",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the pages into; created if absent.",
)
@_PAGES_OPTION
@_JSON_OPTION
def pdf_split_command(
    input_file: Path, out_dir: Path, pages: str | None, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "split",
        lambda: [str(p) for p in pdf.burst(input_file, out_dir, pages=pages)],
        source=str(input_file),
        summary=f"Wrote the pages of {input_file.name} into {out_dir}.",
    )


@click.command("compress", help="Recompress content streams losslessly.")
@_PDF_INPUT
@_PDF_OUTPUT
@_JSON_OPTION
def pdf_compress_command(input_file: Path, output_file: Path, json_output: bool) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    before = input_file.stat().st_size

    def run() -> str:
        written = pdf.compress(input_file, output_file)
        after = written.stat().st_size
        renderer.event(
            "warn" if after >= before else "ok",
            message=(
                f"{before:,} → {after:,} bytes"
                + (
                    ". A scanned document's size is in its images, which this does not touch."
                    if after >= before
                    else ""
                )
            ),
        )
        return str(written)

    _pdf_run(renderer, "compress", run, source=str(input_file), summary=f"Wrote {output_file}.")


@click.command("protect", help="Write a password-protected copy.")
@_PDF_INPUT
@_PDF_OUTPUT
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Prompted for when omitted, so it stays out of your shell history.",
)
@_JSON_OPTION
def pdf_protect_command(
    input_file: Path, output_file: Path, password: str, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "protect",
        lambda: str(pdf.encrypt(input_file, output_file, password=password)),
        source=str(input_file),
        summary=f"Wrote {output_file}, which now needs a password to open.",
    )


@click.command("unlock", help="Write a copy with the password removed.")
@_PDF_INPUT
@_PDF_OUTPUT
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    help="Prompted for when omitted, so it stays out of your shell history.",
)
@_JSON_OPTION
def pdf_unlock_command(
    input_file: Path, output_file: Path, password: str, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)
    _pdf_run(
        renderer,
        "unlock",
        lambda: str(pdf.decrypt(input_file, output_file, password=password)),
        source=str(input_file),
        summary=f"Wrote {output_file}, which opens without a password.",
    )


@click.command("info", help="Page count, and the text layer if you ask for it.")
@_PDF_INPUT
@click.option("--text", "show_text", is_flag=True, help="Also print the extracted text.")
@_PAGES_OPTION
@_JSON_OPTION
def pdf_info_command(
    input_file: Path, show_text: bool, pages: str | None, json_output: bool
) -> None:
    from convilyn.local import pdf

    renderer = make_renderer(json_output=json_output)

    # The summary is emitted from inside `run`, not passed to `_pdf_run`: a
    # keyword argument is evaluated at the call site, so composing it from
    # `page_count` there would read the document OUTSIDE the try block — and a
    # machine without pypdf would get a traceback instead of the install line.
    def run() -> dict[str, object]:
        count = pdf.page_count(input_file)
        renderer.event("ok", message=f"{input_file.name}: {count} pages")

        payload: dict[str, object] = {"pages": count}
        if show_text:
            text = pdf.extract_text(input_file, pages=pages)
            payload["text"] = text
            if not json_output:
                click.echo(text or "(no text layer — this looks like a scan)")
        return payload

    _pdf_run(renderer, "info", run, source=str(input_file), summary="Read the document.")


pdf_command.add_command(pdf_merge_command)
pdf_command.add_command(pdf_select_command)
pdf_command.add_command(pdf_split_command)
pdf_command.add_command(pdf_rotate_command)
pdf_command.add_command(pdf_compress_command)
pdf_command.add_command(pdf_protect_command)
pdf_command.add_command(pdf_unlock_command)
pdf_command.add_command(pdf_info_command)

local_command.add_command(convert_command)
local_command.add_command(batch_command)
local_command.add_command(formats_command)
local_command.add_command(doctor_command)
local_command.add_command(pdf_command)

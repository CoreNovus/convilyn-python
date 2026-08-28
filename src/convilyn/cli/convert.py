"""``convilyn convert`` — turn a local file into another format from the shell.

Composes the Python SDK exactly as a third-party caller would:
``files.upload`` → ``convert.create_and_wait`` → ``convert.download_to``.
The CLI never touches HTTPClient directly so every behaviour available on the
command line is also available programmatically.

``--dry-run`` short-circuits before any network call and prints what the live
invocation would send — including **which processor** it would reach, which is
how a user confirms they are on the free lane before spending anything.
``--json`` swaps the renderer to a single JSON object on stdout for shell
pipelines and AI agents.
"""

from __future__ import annotations

import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from convilyn import (
    APIError,
    AuthError,
    Convilyn,
    JobFailedError,
    JobTimeoutError,
)
from convilyn._internal.convert_families import build_payload, detect_format
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_USAGE,
)
from convilyn.cli._output import OutputRenderer, make_renderer
from convilyn.local.errors import UnsupportedRouteError

#: Stands in for the file id `--dry-run` has not obtained, so the preview can
#: run the SAME payload builder the live path runs. A dry run that assembled
#: its own approximation would be able to disagree with the request it claims
#: to be previewing — which is the one thing it must never do.
_PENDING_UPLOAD = "<pending upload>"


@click.command(
    help=(
        "Convert a local file into a different format — documents, images, "
        "audio and video.\n\n"
        "This is the deterministic lane: it does not consume AI credits. "
        "Extracting meaning from a file (reading a scan, describing a figure, "
        "transcribing speech) is a different, metered verb — see "
        "`convilyn goals understand`.\n\n"
        "The processor is chosen from the two formats, so there is no flag to "
        "read to know which one you get; `--dry-run` prints it.\n\n"
        "Examples:\n\n"
        "  convilyn convert report.docx --to pdf\n\n"
        "  convilyn convert photo.png --to webp\n\n"
        "  convilyn convert clip.mp4 --to mp3"
    ),
)
@click.argument(
    "input_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "--to",
    "target_format",
    required=True,
    help="Target format token (e.g. pdf, webp, mp3).",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path. Defaults to <basename>.<target_format> next to the input.",
)
@click.option(
    "--source-format",
    "source_format",
    default=None,
    help="Override source-format auto-detection from the file extension.",
)
@click.option(
    "--quality",
    default=None,
    help=(
        "Quality hint. Document and media conversions take a preset "
        "(standard / high); image conversions take 1-100. Omitted by default, "
        "so each processor applies its own."
    ),
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout (silences progress lines).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Show what would happen without making any API calls.",
)
# Same name and same wording as `convilyn local convert --overwrite`, because it
# is the same question. It arrives with the SDK change that made `download_to`
# refuse an existing destination: without a flag, a second run of the same
# command would have no way to finish.
@click.option("--overwrite", is_flag=True, help="Replace an existing output file.")
def convert_command(
    input_file: Path,
    target_format: str,
    output_path: Path | None,
    source_format: str | None,
    quality: str | None,
    json_output: bool,
    dry_run: bool,
    overwrite: bool,
) -> None:
    """Convert ``INPUT_FILE`` to ``--to`` format."""
    renderer = make_renderer(json_output=json_output)
    resolved_output = _resolve_output_path(input_file, output_path, target_format)
    resolved_source = source_format or detect_format(input_file.name)
    if resolved_source is None:
        raise SystemExit(EXIT_USAGE) from _print_error(
            ValueError(f"{input_file.name!r} has no extension"),
            "Cannot tell what this file is — pass --source-format",
        )

    # Resolved before the upload, deliberately: an unconvertible pair should
    # cost nothing, and uploading first would spend a round trip and a stored
    # object to learn what the two format tokens already say.
    payload = _payload_or_exit(
        source_format=resolved_source,
        target_format=target_format,
        quality=quality,
        page_range=None,
    )

    if dry_run:
        _emit_dry_run(
            renderer=renderer,
            input_file=input_file,
            payload=payload,
            source_format=resolved_source,
            target_format=target_format,
            quality=quality,
            output_path=resolved_output,
        )
        return

    _run_conversion(
        client_factory=_build_client,
        renderer=renderer,
        input_file=input_file,
        source_format=resolved_source,
        target_format=target_format,
        quality=quality,
        output_path=resolved_output,
        overwrite=overwrite,
    )


# ── Helpers (testable in isolation; SRP) ─────────────────────────────


def _resolve_output_path(
    input_file: Path,
    explicit: Path | None,
    target_format: str,
) -> Path:
    """Resolve the output path (explicit override > derived sibling)."""
    if explicit is not None:
        return explicit
    return input_file.with_suffix(f".{target_format}")


def _build_client() -> Convilyn:
    """Construct a synchronous Convilyn client from environment variables.

    Wrapped in a factory so tests can inject a mock client.
    """
    return Convilyn()


def _emit_dry_run(
    *,
    renderer: OutputRenderer,
    input_file: Path,
    payload: dict[str, Any],
    source_format: str,
    target_format: str,
    quality: str | None,
    output_path: Path,
) -> None:
    """Render the dry-run preview using the same renderer pipeline.

    *payload* is the real request body, built by the same function the live
    path calls, so the preview cannot claim a processor the live call would not
    reach.
    """
    params = {key: value for key, value in payload["params"].items() if value != _PENDING_UPLOAD}
    file_size = input_file.stat().st_size
    content_type = mimetypes.guess_type(input_file.name)[0] or "application/octet-stream"
    renderer.event(
        "upload",
        message=f"[dry-run] Would upload: {input_file.name} ({file_size} B, {content_type})",
    )
    rendered = ", ".join(f"{key}={value}" for key, value in params.items())
    renderer.event(
        "create",
        message=(
            "[dry-run] Would POST /api/v1/jobs: "
            f"{{processor_type={payload['processor_type']}, {rendered}}}"
        ),
    )
    renderer.event(
        "download",
        message=f"[dry-run] Would download to: {output_path}",
    )
    renderer.final(
        {
            "command": "convert",
            "dry_run": True,
            "input_file": str(input_file),
            "processor_type": payload["processor_type"],
            "source_format": source_format,
            "target_format": target_format,
            "quality": quality,
            "output_path": str(output_path),
            "summary": "[dry-run] No API calls made.",
        }
    )


def _payload_or_exit(
    *,
    source_format: str,
    target_format: str,
    quality: str | None,
    page_range: str | None,
) -> dict[str, Any]:
    """`build_payload`'s refusals, turned into a one-line usage exit.

    A pair that names no conversion is the caller's arguments being wrong, not
    a transport failure, so it exits ``EXIT_USAGE`` rather than raising a
    traceback at somebody reading a shell.

    **Two types, because they answer different questions.**
    ``UnsupportedRouteError`` means the conversion is not one this platform
    performs; ``ValueError`` means an argument does not fit the family that was
    selected — a ``page_range`` on an image, a non-numeric quality. Both are the
    caller's problem and both exit the same way here, so the distinction costs
    the shell nothing; it exists for callers of the library, where one is
    catchable as ``ConvilynError`` and the other is a plain mistake.

    The tuple is not belt-and-braces: ``UnsupportedRouteError`` is **not** a
    ``ValueError``, so listing only the latter would have turned the refusal this
    function exists to format into an unhandled traceback.
    """
    try:
        return build_payload(
            file_id=_PENDING_UPLOAD,
            source_format=source_format,
            target_format=target_format,
            quality=quality,
            page_range=page_range,
        )
    except (UnsupportedRouteError, ValueError) as exc:
        raise SystemExit(EXIT_USAGE) from _print_error(exc, "Cannot convert")


def _run_conversion(
    *,
    client_factory: Callable[[], Convilyn],
    renderer: OutputRenderer,
    input_file: Path,
    source_format: str,
    target_format: str,
    quality: str | None,
    output_path: Path,
    overwrite: bool,
) -> None:
    """Real path — upload, convert, download. Translates SDK exceptions
    into the documented exit codes.

    Kept as a free function (not a method) so tests can drive it with a
    fake ``client_factory`` and a renderer that records events.
    """
    started_at = time.monotonic()
    try:
        client = client_factory()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        renderer.event("upload", filename=input_file.name, size_bytes=input_file.stat().st_size)
        file_obj = client.files.upload(str(input_file))

        renderer.event("create", message=f"Creating conversion → {target_format}")
        job = client.convert.create_and_wait(
            file=file_obj,
            target_format=target_format,
            source_format=source_format,
            quality=quality,
        )
        renderer.event("wait", progress=job.progress)

        renderer.event("download", path=str(output_path))
        written_path = client.convert.download_to(job, to=output_path, overwrite=overwrite)
        elapsed = time.monotonic() - started_at
        output_size = written_path.stat().st_size

        renderer.final(
            {
                "command": "convert",
                "input_file": str(input_file),
                "file_id": file_obj.file_id,
                "job_id": job.job_id,
                "status": job.status,
                "output_path": str(written_path),
                "output_size_bytes": output_size,
                "elapsed_seconds": round(elapsed, 3),
            }
        )
    except JobFailedError as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _print_error(exc, "Conversion failed")
    except JobTimeoutError as exc:
        raise SystemExit(EXIT_API_ERROR) from _print_error(exc, "Polling timed out")
    except AuthError as exc:
        raise SystemExit(EXIT_USAGE) from _print_error(exc, "Authentication failed")
    except APIError as exc:
        raise SystemExit(EXIT_API_ERROR) from _print_error(exc, "API error")
    finally:
        try:
            client.close()
        except Exception:
            # Closing failure should not mask the upstream error or
            # turn a successful run into a non-zero exit — swallow it.
            pass


def _print_error(exc: Exception, prefix: str) -> Exception:
    """Print a single error line to stderr; return the exception so the
    raise-from chain stays readable in tracebacks (only shown when
    ``CONVILYN_DEBUG=1``).
    """
    if os.environ.get("CONVILYN_DEBUG"):
        click.echo(f"{prefix}: {exc!r}", err=True)
    else:
        click.echo(f"{prefix}: {exc}", err=True)
    return exc

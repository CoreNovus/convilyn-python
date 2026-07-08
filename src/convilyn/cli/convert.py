"""``convilyn convert`` — turn a local file into another format from the shell.

Composes the Python SDK exactly as a third-party caller would:
``files.upload`` → ``convert.create_and_wait`` → ``convert.download_to``.
The CLI never touches ``_internal`` or HTTPClient directly so every
behaviour available on the command line is also available
programmatically.

``--dry-run`` short-circuits before any network call and prints what
the live invocation would send. ``--json`` swaps the renderer to a
single JSON object on stdout for shell pipelines and AI agents.
"""

from __future__ import annotations

import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path

import click

from convilyn import (
    APIError,
    AuthError,
    Convilyn,
    JobFailedError,
    JobTimeoutError,
)
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_USAGE,
)
from convilyn.cli._output import OutputRenderer, make_renderer


@click.command(
    help=(
        "Convert a local file into a different format.\n\n"
        "Example: convilyn convert report.docx --to pdf"
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
    help="Target format token (e.g. pdf, docx, html).",
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
    default="standard",
    help="Conversion quality hint (standard / high). Backend-specific.",
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
def convert_command(
    input_file: Path,
    target_format: str,
    output_path: Path | None,
    source_format: str | None,
    quality: str,
    json_output: bool,
    dry_run: bool,
) -> None:
    """Convert ``INPUT_FILE`` to ``--to`` format."""
    renderer = make_renderer(json_output=json_output)
    resolved_output = _resolve_output_path(input_file, output_path, target_format)
    resolved_source = source_format or _guess_source_format(input_file.name)

    if dry_run:
        _emit_dry_run(
            renderer=renderer,
            input_file=input_file,
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


def _guess_source_format(filename: str) -> str | None:
    """Best-effort source-format hint from the file extension.

    Returns ``None`` when the extension is unknown — the SDK then
    consults its own mapping or asks the caller to be explicit.
    """
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or None


def _build_client() -> Convilyn:
    """Construct a synchronous Convilyn client from environment variables.

    Wrapped in a factory so tests can inject a mock client.
    """
    return Convilyn()


def _emit_dry_run(
    *,
    renderer: OutputRenderer,
    input_file: Path,
    source_format: str | None,
    target_format: str,
    quality: str,
    output_path: Path,
) -> None:
    """Render the dry-run preview using the same renderer pipeline."""
    file_size = input_file.stat().st_size
    content_type = mimetypes.guess_type(input_file.name)[0] or "application/octet-stream"
    renderer.event(
        "upload",
        message=f"[dry-run] Would upload: {input_file.name} ({file_size} B, {content_type})",
    )
    renderer.event(
        "create",
        message=(
            "[dry-run] Would POST /api/v1/jobs: "
            f"{{processor_type=document_conversion, "
            f"source_format={source_format}, target_format={target_format}, "
            f"quality={quality}}}"
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
            "source_format": source_format,
            "target_format": target_format,
            "quality": quality,
            "output_path": str(output_path),
            "summary": "[dry-run] No API calls made.",
        }
    )


def _run_conversion(
    *,
    client_factory: Callable[[], Convilyn],
    renderer: OutputRenderer,
    input_file: Path,
    source_format: str | None,
    target_format: str,
    quality: str,
    output_path: Path,
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
        written_path = client.convert.download_to(job, to=output_path)
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

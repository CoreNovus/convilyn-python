"""``convilyn goals understand`` — grounded, schema-constrained understanding.

Its own module rather than another block in :mod:`convilyn.cli.goals`: that
file already carries seven sub-commands and their streaming helpers, and the
project's 800-line per-file budget is a ratchet, not advice. One sub-command
per module is also the OCP seam the CLI already uses at the top level
(``cli/main.py`` registers each group with ``add_command``); this applies the
same idiom one level down.

``goals.py`` owns the group and the helpers shared by every sub-command
(``_build_client`` / ``_parse_file_ids`` / ``_print_error``) and registers this
command onto the group at the bottom of that file. Nothing is duplicated here.

Exit codes follow the pinned convention in :mod:`._exit_codes`:
``0`` success, ``1`` usage (including every ``--schema-file`` failure mode),
``2`` API / transport error or an unavailable capability, ``3`` the job did not
deliver a usable result.

``3`` covers two outcomes, and deliberately: a job that FAILED, and a job that
succeeded while producing nothing this command can return. Both were paid for
and neither is the caller's mistake, which is what separates them from ``1``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from convilyn import (
    APIError,
    AuthError,
    Convilyn,
    GoalArtifactUnusableError,
    GoalJobFailedError,
    GoalJobTimeoutError,
    UnderstandUnavailableError,
)

# Imported as a MODULE, not as bare names, so ``_build_client`` stays ONE seam
# for the whole ``goals`` group: a test that patches
# ``convilyn.cli.goals._build_client`` redirects every sub-command including
# this one. Binding the name here would snapshot the original function and
# silently escape that patch — the sub-command would dial the real network
# while its siblings used the fake.
from convilyn.cli import goals as _goals
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_JOB_FAILED,
    EXIT_USAGE,
)
from convilyn.cli._output import OutputRenderer, make_renderer


@click.command("understand")
@click.option(
    "--files",
    "files",
    required=True,
    help="Comma-separated file ids to understand (e.g. file_abc,file_def).",
)
@click.option(
    "--schema-file",
    "schema_file",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to a JSON Schema file (a JSON object) the result must conform to.",
)
@click.option(
    "--instructions",
    "instructions",
    default=None,
    help="Optional natural-language steer for the extraction.",
)
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Overall poll timeout in seconds.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout (result under `result`).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate the schema file and print the would-be request; no network.",
)
def understand_command(
    files: str,
    schema_file: Path,
    instructions: str | None,
    timeout: float,
    json_output: bool,
    dry_run: bool,
) -> None:
    """Grounded, schema-constrained understanding of file(s).

    Returns a result that conforms to the supplied JSON Schema and is
    grounded by the platform before it is returned. The schema is read and
    validated as *JSON* locally (no schema-validation dependency is added —
    conformance is the platform's guarantee), so a malformed or unreadable
    ``--schema-file`` exits ``1`` with a single error line instead of a
    traceback, and it does so **before** any network call.
    """
    renderer = make_renderer(json_output=json_output)
    file_ids = _goals._parse_file_ids(files)
    if not file_ids:
        raise click.ClickException("--files requires at least one file id")
    schema = _load_schema_file(schema_file)

    if dry_run:
        _emit_understand_dry_run(
            renderer=renderer,
            file_ids=file_ids,
            schema=schema,
            instructions=instructions,
        )
        return

    _run_understand(
        renderer=renderer,
        file_ids=file_ids,
        schema=schema,
        instructions=instructions,
        timeout=timeout,
        json_output=json_output,
    )


# ── Helpers (each testable in isolation; SRP) ────────────────────────


def _load_schema_file(path: Path) -> dict[str, Any]:
    """Read ``path`` as a JSON Schema object.

    Every failure mode — missing file, unreadable bytes, invalid JSON, or a
    JSON document that is not an object — becomes a
    :class:`click.ClickException` (exit ``1``, the ``_exit_codes`` "usage /
    file not found" code) so a script can branch on it. Raising here rather
    than letting ``understand()`` raise ``TypeError`` keeps the traceback out
    of the caller's terminal and moves the failure *before* the network call.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        detail = exc.strerror or exc
        raise click.ClickException(f"Cannot read --schema-file {path}: {detail}") from exc
    except UnicodeDecodeError as exc:
        raise click.ClickException(f"--schema-file {path} is not valid UTF-8 text") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--schema-file {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise click.ClickException(
            f"--schema-file {path} must contain a JSON object, got {type(parsed).__name__}"
        )
    return parsed


def _understand_payload(
    *,
    file_ids: list[str],
    schema: dict[str, Any],
    instructions: str | None,
) -> dict[str, Any]:
    """Build the would-be request body — the single shape both the dry-run
    preview and the real call describe, so the preview cannot drift from it.
    """
    payload: dict[str, Any] = {"fileIds": file_ids, "outputSchema": schema}
    if instructions is not None:
        payload["instructions"] = instructions
    return payload


def _emit_understand_dry_run(
    *,
    renderer: OutputRenderer,
    file_ids: list[str],
    schema: dict[str, Any],
    instructions: str | None,
) -> None:
    """Print the would-be ``POST /api/v1/jobs/goal`` payload, no network call."""
    payload = _understand_payload(file_ids=file_ids, schema=schema, instructions=instructions)
    renderer.event(
        "create",
        message=f"[dry-run] Would POST /api/v1/jobs/goal: {json.dumps(payload, sort_keys=True)}",
    )
    renderer.final(
        {
            "command": "goals.understand",
            "dry_run": True,
            "payload": payload,
            "summary": "[dry-run] No API calls made.",
        }
    )


def _run_understand(
    *,
    renderer: OutputRenderer,
    file_ids: list[str],
    schema: dict[str, Any],
    instructions: str | None,
    timeout: float,
    json_output: bool,
    client_factory: Callable[[], Convilyn] | None = None,
) -> None:
    """Drive ``client.goals.understand`` and render the parsed result.

    Separate from :func:`convilyn.cli.goals._run_sync_action` because
    ``understand()`` returns the parsed JSON artifact, not a ``GoalJob`` — the
    exception mapping is otherwise the same, plus
    :class:`UnderstandUnavailableError` (the platform has not rolled the
    capability out) which maps to ``EXIT_API_ERROR`` since it is a property of
    the connected backend, not of the caller's arguments.
    """
    factory = client_factory or _goals._build_client
    try:
        client = factory()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        renderer.event("create", message=f"Understanding {len(file_ids)} file(s)")
        result = client.goals.understand(
            file_ids,
            schema=schema,
            instructions=instructions,
            timeout=timeout,
        )
        renderer.final(_understand_to_payload(file_ids, result, json_output=json_output))
    except UnderstandUnavailableError as exc:
        raise SystemExit(EXIT_API_ERROR) from _goals._print_error(exc, "Understanding unavailable")
    except GoalJobFailedError as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _goals._print_error(exc, "Goal job failed")
    except GoalJobTimeoutError as exc:
        raise SystemExit(EXIT_API_ERROR) from _goals._print_error(exc, "Polling timed out")
    except AuthError as exc:
        raise SystemExit(EXIT_USAGE) from _goals._print_error(exc, "Authentication failed")
    except GoalArtifactUnusableError as exc:
        # The run happened and was charged; there is simply nothing to print.
        # NOT `EXIT_USAGE` — the invocation was fine.
        raise SystemExit(EXIT_JOB_FAILED) from _goals._print_error(exc, "No usable result")
    except APIError as exc:
        raise SystemExit(EXIT_API_ERROR) from _goals._print_error(exc, "API error")
    except (TypeError, ValueError) as exc:
        # understand() rejects an empty file list / non-dict schema, and raises
        # ValueError when the job stopped for slot input. All are caller
        # mistakes, none deserve a traceback. An unusable RESULT is not one of
        # them and is caught above.
        raise SystemExit(EXIT_USAGE) from _goals._print_error(exc, "Cannot understand these inputs")
    finally:
        try:
            client.close()
        except Exception as cleanup_exc:
            # Mirror goals.py — never let cleanup turn a successful run into a
            # non-zero exit or mask an upstream exception.
            if os.environ.get("CONVILYN_DEBUG"):
                click.echo(f"cleanup failed: {cleanup_exc!r}", err=True)


def _understand_to_payload(
    file_ids: list[str],
    result: Any,
    *,
    json_output: bool,
) -> dict[str, Any]:
    """Render the understood result into the shape ``final()`` expects.

    ``--json`` deliberately carries **no** ``summary`` key: the machine-readable
    document is exactly ``{command, file_ids, result}``, with no duplicated
    pretty-print riding along. Human mode instead sets ``summary`` to the
    indented result, because the result *is* the output of this command — a
    one-line "done" tick would hide the very thing the caller asked for.
    """
    payload: dict[str, Any] = {
        "command": "goals.understand",
        "file_ids": file_ids,
        "result": result,
    }
    if not json_output:
        payload["summary"] = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    return payload

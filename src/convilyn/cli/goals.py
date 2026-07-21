"""``convilyn goals`` — AI workflow operations from the shell.

Mirrors the Python surface of :class:`convilyn.resources.goals.AsyncGoals`
one-to-one so AI agents and scripts can drive agentic workflows without
writing Python. Sub-commands:

* ``start``      — create a job (workflow_id or goal_text)
* ``status``     — fetch a one-shot snapshot or poll with ``--watch``
* ``events``     — stream WebSocket events as NDJSON or glyph lines
* ``fill-slot``  — answer one slot the agent is waiting on
* ``confirm``    — submit filled slots for execution
* ``cancel``     — cancel a running / queued job
* ``retry``      — retry a failed job

Exit codes follow the pinned convention in :mod:`._exit_codes`:
``0`` success, ``1`` usage, ``2`` API / transport error,
``3`` job finished with status=failed, ``130`` SIGINT.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import IO, Any

import click

from convilyn import (
    APIError,
    AuthError,
    Convilyn,
    GoalEvent,
    GoalJob,
    GoalJobFailedError,
    GoalJobTimeoutError,
    WebSocketError,
)
from convilyn.cli._exit_codes import (
    EXIT_API_ERROR,
    EXIT_INTERRUPTED,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)
from convilyn.cli._output import OutputRenderer, make_renderer

# ── Click group + sub-commands ──────────────────────────────────────


@click.group(
    "goals",
    help=(
        "Run AI workflow (agentic) workflows from the shell.\n\n"
        "Every sub-command is scriptable: --json gives machine-readable "
        "output, --dry-run previews where applicable, and exit codes are "
        "pinned (0 ok / 2 api / 3 job-failed / 130 sigint)."
    ),
)
def goals_command() -> None:
    """Top-level ``convilyn goals`` group."""


@goals_command.command("start")
@click.option(
    "--workflow-id",
    "workflow_id",
    default=None,
    help="Built-in workflow spec id (e.g. doc_analyzer). "
    "Mutually exclusive with --user-workflow-id and --goal-text.",
)
@click.option(
    "--user-workflow-id",
    "user_workflow_id",
    default=None,
    help="Your Builder-authored workflow id (uw_...). "
    "Mutually exclusive with --workflow-id and --goal-text.",
)
@click.option(
    "--goal-text",
    "goal_text",
    default=None,
    help="Free-text goal description. Requires --files.",
)
@click.option(
    "--files",
    "files",
    default=None,
    help="Comma-separated file ids (e.g. file_abc,file_def).",
)
@click.option(
    "--slot",
    "slots",
    multiple=True,
    metavar="KEY=VALUE",
    help="Pre-fill a slot. Repeatable. VALUE is parsed as JSON if possible, "
    "falling back to the literal string.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the job as a single JSON object on stdout.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Print what would be sent without hitting the network.",
)
def start_command(
    workflow_id: str | None,
    user_workflow_id: str | None,
    goal_text: str | None,
    files: str | None,
    slots: tuple[str, ...],
    json_output: bool,
    dry_run: bool,
) -> None:
    """Create an AI workflow job."""
    renderer = make_renderer(json_output=json_output)
    file_ids = _parse_file_ids(files)
    slot_values = _parse_slot_pairs(slots)

    if dry_run:
        _emit_start_dry_run(
            renderer=renderer,
            workflow_id=workflow_id,
            user_workflow_id=user_workflow_id,
            goal_text=goal_text,
            file_ids=file_ids,
            slots=slot_values,
        )
        return

    _run_sync_action(
        action=lambda client: client.goals.start(
            workflow_id=workflow_id,
            user_workflow_id=user_workflow_id,
            goal_text=goal_text,
            files=file_ids,
            slots=slot_values or None,
        ),
        renderer=renderer,
        command_name="start",
    )


@goals_command.command("status")
@click.argument("job_spec_id")
@click.option(
    "--watch",
    "watch",
    is_flag=True,
    help="Poll until the job reaches a terminal status or HITL stop.",
)
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Watch-mode timeout in seconds.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the job as a single JSON object on stdout.",
)
def status_command(
    job_spec_id: str,
    watch: bool,
    timeout: float,
    json_output: bool,
) -> None:
    """Inspect an AI workflow job."""
    renderer = make_renderer(json_output=json_output)

    def action(client: Convilyn) -> GoalJob:
        if watch:
            return client.goals.wait(job_spec_id, timeout=timeout)
        return client.goals.retrieve(job_spec_id)

    _run_sync_action(action=action, renderer=renderer, command_name="status")


@goals_command.command("events")
@click.argument("job_spec_id")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit each event as one JSON object per line (NDJSON).",
)
@click.option(
    "--ws-url",
    "ws_url",
    default=None,
    help="Override the configured WebSocket URL (e.g. wss://ws.dev.convilyn.com).",
)
def events_command(
    job_spec_id: str,
    json_output: bool,
    ws_url: str | None,
) -> None:
    """Stream WebSocket events for an AI workflow job.

    Exits when the server emits a terminal event (``completed`` /
    ``failed`` / ``cancelled``). Ctrl-C closes the stream and exits 130.
    """
    try:
        exit_code = asyncio.run(
            _stream_events(
                job_spec_id=job_spec_id,
                json_output=json_output,
                ws_url=ws_url,
            )
        )
    except KeyboardInterrupt:
        raise SystemExit(EXIT_INTERRUPTED) from None
    raise SystemExit(exit_code)


@goals_command.command("fill-slot")
@click.argument("job_spec_id")
@click.option("--slot-id", "slot_id", required=True, help="Slot identifier to answer.")
@click.option(
    "--value",
    "value",
    required=True,
    help="Slot value. Parsed as JSON when possible, falling back to the literal string.",
)
@click.option(
    "--expected-version",
    "expected_version",
    type=int,
    default=None,
    help="Optimistic-locking item version. Server returns 409 on mismatch.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the updated job as a single JSON object on stdout.",
)
def fill_slot_command(
    job_spec_id: str,
    slot_id: str,
    value: str,
    expected_version: int | None,
    json_output: bool,
) -> None:
    """Answer one slot the agent is waiting on."""
    renderer = make_renderer(json_output=json_output)
    parsed_value = _parse_slot_value(value)

    _run_sync_action(
        action=lambda client: client.goals.fill_slot(
            job_spec_id,
            slot_id=slot_id,
            value=parsed_value,
            expected_version=expected_version,
        ),
        renderer=renderer,
        command_name="fill-slot",
    )


@goals_command.command("confirm")
@click.argument("job_spec_id")
@click.option(
    "--expected-version",
    "expected_version",
    type=int,
    default=None,
    help="Optimistic-locking item version.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON on stdout.")
def confirm_command(
    job_spec_id: str,
    expected_version: int | None,
    json_output: bool,
) -> None:
    """Confirm a job whose slots are filled."""
    renderer = make_renderer(json_output=json_output)
    _run_sync_action(
        action=lambda client: client.goals.confirm(job_spec_id, expected_version=expected_version),
        renderer=renderer,
        command_name="confirm",
    )


@goals_command.command("cancel")
@click.argument("job_spec_id")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON on stdout.")
def cancel_command(job_spec_id: str, json_output: bool) -> None:
    """Cancel a running or queued job."""
    renderer = make_renderer(json_output=json_output)
    _run_sync_action(
        action=lambda client: client.goals.cancel(job_spec_id),
        renderer=renderer,
        command_name="cancel",
    )


@goals_command.command("retry")
@click.argument("job_spec_id")
@click.option(
    "--rerun-mode",
    "rerun_mode",
    type=click.Choice(["retry_same_thread", "fresh_rerun"], case_sensitive=False),
    default="retry_same_thread",
    show_default=True,
    help="Whether to resume the existing run thread or start fresh.",
)
@click.option(
    "--reason",
    "reason",
    default=None,
    help="Optional audit string (capped at 512 chars by the backend).",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON on stdout.")
def retry_command(
    job_spec_id: str,
    rerun_mode: str,
    reason: str | None,
    json_output: bool,
) -> None:
    """Retry a failed job."""
    renderer = make_renderer(json_output=json_output)
    _run_sync_action(
        action=lambda client: client.goals.retry(
            job_spec_id,
            rerun_mode=rerun_mode,  # type: ignore[arg-type]
            reason=reason,
        ),
        renderer=renderer,
        command_name="retry",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _build_client() -> Convilyn:
    """Construct a sync :class:`Convilyn` from the environment.

    Test seam — patched via ``monkeypatch.setattr(goals_module,
    "_build_client", ...)`` so suites can inject a ``MagicMock``.
    Mirrors :func:`convilyn.cli.convert._build_client`.
    """
    return Convilyn()


def _parse_file_ids(raw: str | None) -> list[str] | None:
    """Split a comma-separated ``--files`` value into a list.

    Empty input returns ``None`` so the SDK's own XOR validation kicks in
    when neither ``--workflow-id`` nor ``--goal-text`` is supplied.
    """
    if raw is None:
        return None
    items = [token.strip() for token in raw.split(",")]
    cleaned = [token for token in items if token]
    return cleaned or None


def _parse_slot_pairs(pairs: tuple[str, ...]) -> dict[str, Any]:
    """Parse repeated ``--slot KEY=VALUE`` pairs into a dict.

    The value is JSON-decoded when possible so authors can supply
    structured values (``--slot tags='["x","y"]'``); on failure the
    literal string is kept. Mirrors ``gh api --raw-field`` precedent.
    """
    slots: dict[str, Any] = {}
    for raw in pairs:
        if "=" not in raw:
            raise click.BadParameter(
                f"--slot expects KEY=VALUE, got {raw!r}",
                param_hint="--slot",
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise click.BadParameter(
                "--slot key cannot be empty",
                param_hint="--slot",
            )
        slots[key] = _parse_slot_value(value)
    return slots


def _parse_slot_value(raw: str) -> Any:
    """Tolerant JSON-or-literal parser for slot values.

    Tries ``json.loads`` first; falls back to the raw string on
    :class:`json.JSONDecodeError` so callers can write ``--value foo``
    without quoting headaches.
    """
    stripped = raw.strip()
    if not stripped:
        raise click.BadParameter("--value cannot be empty", param_hint="--value")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return raw


def _emit_start_dry_run(
    *,
    renderer: OutputRenderer,
    workflow_id: str | None,
    user_workflow_id: str | None,
    goal_text: str | None,
    file_ids: list[str] | None,
    slots: dict[str, Any],
) -> None:
    """Print the would-be ``POST /api/v1/jobs/goal`` payload, no network call."""
    payload: dict[str, Any] = {"fileIds": file_ids or []}
    if workflow_id is not None:
        payload["workflowId"] = workflow_id
    if user_workflow_id is not None:
        payload["userWorkflowId"] = user_workflow_id
    if goal_text is not None:
        payload["goalText"] = goal_text
    if slots:
        payload["slots"] = slots
    renderer.event(
        "create",
        message=f"[dry-run] Would POST /api/v1/jobs/goal: {json.dumps(payload, sort_keys=True)}",
    )
    renderer.final(
        {
            "command": "goals.start",
            "dry_run": True,
            "payload": payload,
            "summary": "[dry-run] No API calls made.",
        }
    )


def _run_sync_action(
    *,
    action: Callable[[Convilyn], GoalJob],
    renderer: OutputRenderer,
    command_name: str,
    client_factory: Callable[[], Convilyn] | None = None,
) -> None:
    """Drive a sync SDK call and translate exceptions to documented exits.

    Mirrors :func:`convilyn.cli.convert._run_conversion` — same factory /
    exception mapping shape, just for the AI workflow verbs.
    """
    factory = client_factory or _build_client
    try:
        client = factory()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        job = action(client)
        renderer.event(command_name, status=job.status)
        renderer.final(_job_to_payload(command_name, job))
    except GoalJobFailedError as exc:
        raise SystemExit(EXIT_JOB_FAILED) from _print_error(exc, "Goal job failed")
    except GoalJobTimeoutError as exc:
        raise SystemExit(EXIT_API_ERROR) from _print_error(exc, "Polling timed out")
    except AuthError as exc:
        raise SystemExit(EXIT_USAGE) from _print_error(exc, "Authentication failed")
    except APIError as exc:
        raise SystemExit(EXIT_API_ERROR) from _print_error(exc, "API error")
    finally:
        try:
            client.close()
        except Exception as cleanup_exc:
            # Mirror convert.py — never let cleanup turn a successful run
            # into a non-zero exit or mask an upstream exception. Surface
            # the failure only under CONVILYN_DEBUG so silent leaks stay
            # debuggable without polluting normal output.
            if os.environ.get("CONVILYN_DEBUG"):
                click.echo(f"cleanup failed: {cleanup_exc!r}", err=True)


async def _stream_events(
    *,
    job_spec_id: str,
    json_output: bool,
    ws_url: str | None,
    client_factory: Callable[[], Convilyn] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Async helper driving ``goals events``.

    Returns the exit code so the sync entry point can ``raise SystemExit``
    at the boundary. Tests inject ``client_factory`` + stream sinks to
    keep the loop assertions deterministic.
    """
    factory = client_factory or _build_client
    out: IO[str] = stdout or sys.stdout
    err: IO[str] = stderr or sys.stderr

    try:
        client = factory()
    except AuthError as exc:
        click.echo(f"Authentication failed: {exc}", err=True, file=err)
        return EXIT_USAGE

    try:
        iterator = client.async_client.goals.events(job_spec_id, ws_url=ws_url)
        async for event in iterator:
            _emit_event(event, json_output=json_output, stdout=out, stderr=err)
            if event.is_terminal:
                return _terminal_event_exit_code(event)
        # Iterator closed without a terminal event — unusual but treat
        # as transport-side issue rather than success.
        click.echo(
            "Event stream closed without a terminal event",
            err=True,
            file=err,
        )
        return EXIT_API_ERROR
    except ValueError as exc:
        # resolve_ws_url raises ValueError when no WS URL is configured
        # (ctor arg, env var, and per-call --ws-url all empty).
        click.echo(f"Configuration error: {exc}", err=True, file=err)
        return EXIT_USAGE
    except WebSocketError as exc:
        click.echo(f"WebSocket error: {exc}", err=True, file=err)
        return EXIT_API_ERROR
    except APIError as exc:
        click.echo(f"API error: {exc}", err=True, file=err)
        return EXIT_API_ERROR
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Cooperative cancellation must propagate so the outer
        # `asyncio.run(...)` in `events_command` sees the original
        # signal and the SIGINT contract (exit 130) is preserved.
        raise
    except Exception as exc:
        # Catch-all for unexpected failures (e.g. ValidationError on a
        # malformed server frame, transport-level oddities). Without
        # this, an uncaught traceback leaks to stdout — worse UX than
        # the documented EXIT_API_ERROR.
        click.echo(f"Unexpected error: {exc!r}", err=True, file=err)
        return EXIT_API_ERROR
    finally:
        try:
            await client.async_client.aclose()
        except Exception as cleanup_exc:
            if os.environ.get("CONVILYN_DEBUG"):
                click.echo(f"cleanup failed: {cleanup_exc!r}", err=True, file=err)


def _emit_event(
    event: GoalEvent,
    *,
    json_output: bool,
    stdout: IO[str],
    stderr: IO[str],
) -> None:
    """Write one event to the right stream.

    JSON mode → NDJSON line on stdout for ``| jq`` pipelines. Human mode
    → glyph-prefixed status line on stderr (matches ``convert``'s
    progress-on-stderr convention so callers can still ``> result``).
    """
    if json_output:
        line = json.dumps(
            event.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
        stdout.write(line + "\n")
        stdout.flush()
        return
    glyph = _EVENT_GLYPHS.get(event.type, "•")
    detail = _summarise_event(event)
    stderr.write(f"{glyph} {event.type}{detail}\n")
    stderr.flush()


def _summarise_event(event: GoalEvent) -> str:
    """Best-effort one-line detail for human-mode rendering."""
    data = event.data or {}
    candidates = (
        data.get("name"),
        data.get("tool"),
        data.get("role"),
        data.get("message"),
        data.get("status"),
    )
    for value in candidates:
        if value:
            return f" {value}"
    if event.type in ("progress",) and "progress" in data:
        return f" {data['progress']}%"
    return ""


def _terminal_event_exit_code(event: GoalEvent) -> int:
    """Map a terminal event to the documented exit code."""
    if event.type == "failed":
        return EXIT_JOB_FAILED
    # ``completed`` (success) and ``cancelled`` (user-initiated) both
    # exit zero — the user already knew they cancelled.
    return EXIT_OK


def _job_to_payload(command_name: str, job: GoalJob) -> dict[str, Any]:
    """Render a :class:`GoalJob` into the shape ``final()`` expects."""
    return {
        "command": f"goals.{command_name}",
        "job_spec_id": job.job_spec_id,
        "status": job.status,
        "progress": job.progress,
        "pending_slots": [slot.model_dump(mode="json") for slot in job.pending_slots],
        "filled_slots": dict(job.filled_slots),
        "summary": f"✓ {command_name} job={job.job_spec_id} status={job.status}",
    }


def _print_error(exc: Exception, prefix: str) -> Exception:
    """Print one error line to stderr; mirrors ``convert._print_error``."""
    if os.environ.get("CONVILYN_DEBUG"):
        click.echo(f"{prefix}: {exc!r}", err=True)
    else:
        click.echo(f"{prefix}: {exc}", err=True)
    return exc


_EVENT_GLYPHS: dict[str, str] = {
    "tool_started": "▶",
    "tool_finished": "✓",
    "agent_step_started": "▶",
    "agent_step_finished": "✓",
    "orchestration_transition": "↔",
    "status": "•",
    "progress": "…",
    "completed": "✓",
    "failed": "✗",
    "slot_needed": "?",
    "keepalive": "·",
    "agent_text": "›",
    "agent_text_done": "›",
}

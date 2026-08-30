"""``convilyn account`` — billing tier + quota queries from the shell.

Two sub-commands mirroring the :class:`convilyn.resources.account.Account`
resource:

* ``convilyn account plan``  — what tier am I on?
* ``convilyn account quota`` — would a workflow with these tools fit my plan?

Designed for AI agents and CI scripts: ``--json`` for pipe consumption,
pinned exit codes, no interactive prompts.

Exit codes follow the project convention (`._exit_codes`):
``0`` ok, ``1`` usage, ``2`` API / transport error, ``130`` SIGINT.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from convilyn import (
    APIError,
    AuthError,
    Convilyn,
    InsufficientCreditsError,
    PlanRequiredError,
    QuotaExceededError,
)
from convilyn.cli._browser import open_url_with_fallback
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_USAGE
from convilyn.cli._output import OutputRenderer, make_renderer

# ── Click group + sub-commands ──────────────────────────────────────


@click.group(
    "account",
    help=(
        "Check your billing tier + workflow cost quota.\n\n"
        "Read-only. No side effects. Pair with --json for shell pipelines."
    ),
)
def account_command() -> None:
    """Top-level ``convilyn account`` group."""


@account_command.command("plan")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout.",
)
def plan_command(json_output: bool) -> None:
    """Print the caller's current billing tier."""
    renderer = make_renderer(json_output=json_output)

    def action(client: Convilyn) -> dict[str, Any]:
        plan = client.account.get_plan()
        return {
            "command": "account.plan",
            "tier": plan.tier,
            "summary": f"tier={plan.tier}",
        }

    _run_account_action(action=action, renderer=renderer)


@account_command.command("quota")
@click.option(
    "--tool",
    "tools",
    multiple=True,
    metavar="TOOL",
    help='Fully-qualified tool id, e.g. "pdf-mcp:extract_text". Repeatable.',
)
@click.option(
    "--max-iter",
    "max_iterations",
    type=int,
    default=None,
    help="Iteration cap from the draft spec. Defaults to backend default.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout.",
)
def quota_command(
    tools: tuple[str, ...],
    max_iterations: int | None,
    json_output: bool,
) -> None:
    """Estimate cost + check the verdict against the caller's tier."""
    renderer = make_renderer(json_output=json_output)

    def action(client: Convilyn) -> dict[str, Any]:
        estimate = client.account.get_quota(tools=list(tools), max_iterations=max_iterations)
        quota = estimate.quota_check
        return {
            "command": "account.quota",
            "tier": quota.tier,
            "state": quota.state,
            "estimated_micro_u": estimate.estimated_micro_u,
            "estimated_usd": estimate.estimated_usd,
            "threshold_micro_u": quota.threshold_micro_u,
            "upgrade_url": quota.upgrade_url,
            "summary": (
                f"tier={quota.tier} state={quota.state} "
                f"cost={estimate.estimated_micro_u}/{quota.threshold_micro_u} micro-U"
            ),
        }

    _run_account_action(action=action, renderer=renderer)


# ── Helpers ─────────────────────────────────────────────────────────


def _build_client() -> Convilyn:
    """Construct a sync Convilyn from the environment.

    Test seam — patched via ``monkeypatch.setattr(account_module,
    "_build_client", ...)``. Mirrors the pattern in
    :mod:`convilyn.cli.convert` / :mod:`convilyn.cli.goals`.
    """
    return Convilyn()


def _run_account_action(
    *,
    action: Callable[[Convilyn], dict[str, Any]],
    renderer: OutputRenderer,
    client_factory: Callable[[], Convilyn] | None = None,
) -> None:
    """Drive a sync SDK call + translate billing exceptions to pinned exit codes.

    Plan / quota errors are explicitly surfaced: a free-tier caller
    running ``convilyn account quota --tool ...`` against a Pro-only
    endpoint should see ``EXIT_USAGE`` (1) — they need to upgrade
    before the call will succeed — NOT the generic
    ``EXIT_API_ERROR`` (2) which suggests a transient backend failure.
    """
    factory = client_factory or _build_client
    try:
        client = factory()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        payload = action(client)
        renderer.event(payload["command"], summary=payload["summary"])
        renderer.final(payload)
    except PlanRequiredError as exc:
        click.echo(
            f"Plan required: {exc.message} (upgrade: {exc.upgrade_url or 'see /pricing'})",
            err=True,
        )
        raise SystemExit(EXIT_USAGE) from exc
    except QuotaExceededError as exc:
        click.echo(
            f"Quota exceeded: {exc.message} "
            f"(used {exc.estimated_micro_u}/{exc.threshold_micro_u} micro-U)",
            err=True,
        )
        raise SystemExit(EXIT_USAGE) from exc
    except InsufficientCreditsError as exc:
        click.echo(
            f"Insufficient credits: {exc.message} "
            f"(need {exc.required_credits}, have {exc.available_credits})",
            err=True,
        )
        if exc.upgrade_url:
            open_url_with_fallback(exc.upgrade_url, intro="Opening your browser to top up credits…")
        raise SystemExit(EXIT_USAGE) from exc
    except AuthError as exc:
        click.echo(f"Authentication failed: {exc}", err=True)
        raise SystemExit(EXIT_USAGE) from exc
    except APIError as exc:
        click.echo(f"API error: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc
    finally:
        try:
            client.close()
        except Exception:
            pass

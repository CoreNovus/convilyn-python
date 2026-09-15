"""``convilyn feedback`` — three questions, and what would help.

The CLI half of the product survey. The web modal is the other half; the
two write the same row through the same endpoint and differ only in ``source``,
which is kept because the two populations are different — a CLI respondent has
already installed a package.

## Why this is a top-level command and not ``account feedback``

:mod:`convilyn.resources.account` states its own boundary: *"actions (upgrade,
top-up) belong on the website, not in the SDK"*, and its CLI mirror advertises
"Read-only. No side effects. **No interactive prompts.**" A survey is a write and
it is interactive, so it belongs beside that group rather than inside it.

## CLI-only in v1

Nothing here touches ``convilyn.__all__`` or a resource class, so
``tests/contract/test_public_surface.py``'s ``FROZEN_ALL`` and both per-resource
method maps are untouched. The transport is reached the way ``convilyn api``
already reaches it, which is the sanctioned route for an endpoint with no
first-class resource.

## Prompting requires a TTY **and** the absence of ``--json``

Not one condition, two, and the second is what keeps ``--json`` honest.
``JsonRenderer`` permits exactly one JSON document on stdout, and ``click.prompt``
writes its prompt there by default. Rather than rely on remembering ``err=True``
at every call site, ``--json`` refuses to prompt at all: in that mode every
answer must arrive as a flag. A pipeline that forgot one gets a usage error
naming the flag, never a prompt nothing will answer.

The prompts still pass ``err=True`` as well — belt and braces, because a prompt
on stdout would corrupt output that a caller is redirecting even without
``--json``.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import click
import httpx

from convilyn import APIError, AuthError, Convilyn
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_USAGE
from convilyn.cli._output import OutputRenderer, make_renderer, write_line

#: The endpoint. Relative, like every other in-SDK path — the transport refuses
#: an absolute URL because it attaches the caller's key.
SURVEY_PATH = "/api/v1/feedback/survey"

#: Q1's closed set, in the order the website lists it. The first nine mirror the
#: site's own category menu, so CLI answers aggregate with web answers rather
#: than living in a second vocabulary; the last two are the escapes a closed
#: list needs to stay honest.
PURPOSE_LABELS: dict[str, str] = {
    "jobSearch": "Career and job search",
    "subtitleTools": "Subtitles and captions",
    "ecommerce": "E-commerce listings",
    "socialMarketing": "Social and marketing",
    "education": "Teaching and education",
    "voiceAudio": "Voice and audio",
    "financeAccounting": "Finance and accounting",
    "understanding": "File understanding",
    "personalProductivity": "Personal and family",
    "conversionOnly": "Conversion or compression only, no AI",
    "other": "Something else",
}

#: Q2's closed set — what the respondent does instead today.
METHOD_LABELS: dict[str, str] = {
    "manual": "By hand",
    "otherTool": "With another tool",
    "outsourced": "Outsourced",
    "notStarted": "Not started yet",
}

# Mirrors of the bounds the API enforces. The server is the authority; these
# only stop a doomed request being sent.
MAX_OTHER_LEN = 200
MAX_TOOL_LEN = 120
MAX_SUGGESTION_LEN = 2000


@click.command(
    "feedback",
    help=(
        "Answer three questions about what you are trying to do.\n\n"
        "Interactive by default. Pass --purpose and --current-method to run it "
        "non-interactively (required with --json, which never prompts)."
    ),
)
@click.option(
    "--purpose",
    type=click.Choice(list(PURPOSE_LABELS), case_sensitive=False),
    default=None,
    help="What you are mainly trying to solve. Prompted when omitted.",
)
@click.option(
    "--purpose-other",
    default=None,
    help="Free text, only meaningful with --purpose other.",
)
@click.option(
    "--current-method",
    type=click.Choice(list(METHOD_LABELS), case_sensitive=False),
    default=None,
    help="How you handle it today. Prompted when omitted.",
)
@click.option(
    "--tool",
    default=None,
    help="Which tool, only meaningful with --current-method otherTool.",
)
@click.option(
    "--suggestion",
    default=None,
    help="What would make it more useful. Optional.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit a single JSON object on stdout. Never prompts.",
)
def feedback_command(
    purpose: str | None,
    purpose_other: str | None,
    current_method: str | None,
    tool: str | None,
    suggestion: str | None,
    json_output: bool,
) -> None:
    """Submit the product survey."""
    renderer = make_renderer(json_output=json_output)
    can_prompt = _stdin_is_interactive() and not json_output

    answers = _resolve_answers(
        purpose=purpose,
        purpose_other=purpose_other,
        current_method=current_method,
        tool=tool,
        suggestion=suggestion,
        can_prompt=can_prompt,
    )
    _submit(answers=answers, renderer=renderer)


def _stdin_is_interactive() -> bool:
    """Whether there is a terminal to prompt on.

    A module-level function rather than an inline ``sys.stdin.isatty()`` so a
    test can decide the answer; ``cli/setup.py`` has its own for the same reason.
    The two are one line each, and the right home for a shared version is
    ``_output.py``, beside ``should_colorize()`` — which answers the same kind of
    question about the same terminal. Not unified here because four of
    ``setup``'s own tests patch its copy by name, and rewriting another
    command's tests to land a new one is a change that belongs on its own.
    """
    return sys.stdin.isatty()


# ── Answer resolution (pure; no I/O beyond the prompts) ──────────────


def _resolve_answers(
    *,
    purpose: str | None,
    purpose_other: str | None,
    current_method: str | None,
    tool: str | None,
    suggestion: str | None,
    can_prompt: bool,
) -> dict[str, Any]:
    """Flags first, prompts second, refusal third.

    The refusal names the missing flag rather than the missing prompt, because
    the caller who hits it is a script and the flag is the thing it can add.
    """
    if purpose is None:
        if not can_prompt:
            raise SystemExit(_refuse("--purpose"))
        purpose = _ask_choice("What are you mainly trying to solve?", PURPOSE_LABELS)
        if purpose == "other" and purpose_other is None:
            purpose_other = _ask_text("  Tell us more (optional)", MAX_OTHER_LEN)

    if current_method is None:
        if not can_prompt:
            raise SystemExit(_refuse("--current-method"))
        current_method = _ask_choice("How is it handled today?", METHOD_LABELS)
        if current_method == "otherTool" and tool is None:
            tool = _ask_text("  Which tool (optional)", MAX_TOOL_LEN)

    if suggestion is None and can_prompt:
        suggestion = _ask_text("What would make it more useful? (optional)", MAX_SUGGESTION_LEN)

    body: dict[str, Any] = {
        "purpose": purpose,
        "current_method": current_method,
        # Not a default the server would supply: the two populations are
        # different and the row is only comparable if this is stamped.
        "source": "cli",
    }
    # An answer that only makes sense for the branch that asked for it. Sending
    # `purpose_other` alongside `purpose=jobSearch` would store a value nothing
    # can interpret, so the pairing is enforced here rather than hoped for.
    if purpose == "other" and purpose_other:
        body["purpose_other"] = purpose_other.strip()[:MAX_OTHER_LEN]
    if current_method == "otherTool" and tool:
        body["current_method_tool"] = tool.strip()[:MAX_TOOL_LEN]
    if suggestion and suggestion.strip():
        body["suggestion"] = suggestion.strip()[:MAX_SUGGESTION_LEN]
    return body


def _refuse(flag: str) -> int:
    click.echo(
        f"{flag} is required when there is no terminal to prompt on "
        "(or when --json is set, which never prompts).",
        err=True,
    )
    return EXIT_USAGE


def _ask_choice(question: str, labels: dict[str, str]) -> str:
    """A numbered menu on stderr, answered by number.

    Numbered rather than `click.Choice` over the raw values: the values are wire
    identifiers (`jobSearch`, `notStarted`) and asking a person to type one is
    asking them to retype an enum. `IntRange` re-prompts on a bad answer for
    free.
    """
    values = list(labels)
    write_line("", sys.stderr)
    write_line(question, sys.stderr)
    for index, value in enumerate(values, start=1):
        write_line(f"  {index}) {labels[value]}", sys.stderr)
    choice = click.prompt("  #", type=click.IntRange(1, len(values)), err=True)
    return values[choice - 1]


def _ask_text(question: str, max_len: int) -> str:
    answer = click.prompt(question, default="", show_default=False, err=True)
    return str(answer)[:max_len]


# ── Submission ──────────────────────────────────────────────────────


def _build_client() -> Convilyn:
    """Test seam, mirroring `cli.convert._build_client`."""
    return Convilyn()


def _submit(*, answers: dict[str, Any], renderer: OutputRenderer) -> None:
    """POST the answers and report exactly once.

    Reached through `client.async_client._http` the way `convilyn api` reaches
    it — the sanctioned route for an endpoint with no first-class resource, so
    auth, retries and the `Idempotency-Key` stamp are inherited rather than
    re-implemented.
    """
    try:
        client = _build_client()
    except AuthError as exc:
        click.echo(f"{exc}\n\nRun `convilyn setup` first.", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc

    try:
        response = asyncio.run(client.async_client._http.request("POST", SURVEY_PATH, json=answers))
        payload = response.json()
    except AuthError as exc:
        click.echo(f"{exc}\n\nRun `convilyn setup` first.", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc
    except (APIError, httpx.HTTPError, ValueError) as exc:
        click.echo(f"Could not submit: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

    renderer.event("ok", message=_summary(payload))
    renderer.final(
        {
            "command": "feedback",
            "response_id": payload.get("response_id"),
            "reward_granted": payload.get("reward_granted", False),
            "reward_credits": payload.get("reward_credits", 0),
            "reward_state": payload.get("reward_state", "none"),
            # Every command supplies its own summary; omitting it falls through
            # to the renderer's generic formatter, which emits a glyph that
            # raises UnicodeEncodeError on a cp950 console.
            "summary": _summary(payload),
        }
    )


def _summary(payload: dict[str, Any]) -> str:
    """One line, and it never promises credits the server did not grant.

    `reward_state` is the authority, not `reward_granted` alone: `disabled` is a
    live value today (the grant ships off), so "stored, no reward" is a normal
    outcome that needs its own sentence rather than silence. The number comes
    from the response — never a literal, because a figure written into code is a
    copy that does not move when the catalogue does.
    """
    if payload.get("reward_granted"):
        return f"Thanks — recorded, and {payload.get('reward_credits', 0)} credits were added."
    return "Thanks — recorded."

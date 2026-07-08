"""``convilyn api`` — gh-style escape hatch for any Convilyn API endpoint.

When a backend endpoint is not yet wrapped by a first-class SDK
resource (``client.files`` / ``client.convert`` / future ``client.goals``
/ ``client.workflows``), this command lets users and AI agents call it
directly without waiting for an SDK release. Auth, retries, and
``Idempotency-Key`` stamping are all inherited from the SDK transport,
so the escape hatch carries the same reliability story as the wrapped
resources.

Examples::

    convilyn api GET /api/v1/jobs/job_xyz
    convilyn api POST /api/v1/upload/presign --data '{"fileName":"x.docx",...}'
    convilyn api GET /api/v1/jobs --query page=2 --query limit=10
    convilyn api GET /api/v1/jobs/job_xyz --json | jq .
    convilyn api GET /api/v1/jobs/job_xyz --include      # curl -i style
    convilyn api GET /api/v1/jobs/job_xyz -o response.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click
import httpx

from convilyn import APIError, AuthError
from convilyn.cli._exit_codes import EXIT_API_ERROR, EXIT_USAGE
from convilyn.cli.convert import _build_client  # reuse the same DI seam

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")


@click.command(
    help=(
        "Call any Convilyn API endpoint (gh-style escape hatch).\n\n"
        "Examples:\n"
        "  convilyn api GET /api/v1/jobs/job_xyz\n"
        '  convilyn api POST /api/v1/upload/presign --data \'{"fileName":"x.docx"}\'\n'
        "  convilyn api GET /api/v1/jobs --query page=2 --query limit=10"
    ),
)
@click.argument("method", type=click.Choice(HTTP_METHODS, case_sensitive=False))
@click.argument("path")
@click.option(
    "--data",
    "data_inline",
    default=None,
    help="JSON body inline (mutually exclusive with --input).",
)
@click.option(
    "--input",
    "input_file",
    default=None,
    type=click.Path(allow_dash=True),
    help="Read JSON body from FILE (use '-' for stdin). Mutually exclusive with --data.",
)
@click.option(
    "--header",
    "headers",
    multiple=True,
    help='Extra header in "Name: Value" form (repeatable).',
)
@click.option(
    "--query",
    "query_params",
    multiple=True,
    help="Query string parameter in NAME=VALUE form (repeatable).",
)
@click.option(
    "--include",
    "include_headers",
    is_flag=True,
    help="Print response status + headers + body (curl -i style).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Single-line JSON (jq-friendly). Default is pretty-printed.",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    default=None,
    type=click.Path(),
    help="Write response body to FILE instead of stdout.",
)
def api_command(
    method: str,
    path: str,
    data_inline: str | None,
    input_file: str | None,
    headers: tuple[str, ...],
    query_params: tuple[str, ...],
    include_headers: bool,
    json_output: bool,
    output_file: str | None,
) -> None:
    """Issue an HTTP request to a Convilyn API endpoint."""
    if data_inline is not None and input_file is not None:
        raise click.UsageError("--data and --input are mutually exclusive")

    body = _resolve_body(data_inline=data_inline, input_file=input_file)
    header_dict = _parse_headers(headers)
    params = _parse_query(query_params)

    try:
        response = _execute_request(
            method=method.upper(),
            path=path,
            headers=header_dict,
            params=params,
            body=body,
        )
    except AuthError as exc:
        click.echo(f"Authentication failed: {exc}", err=True)
        raise SystemExit(EXIT_USAGE) from exc

    _render_response(
        response=response,
        include_headers=include_headers,
        json_output=json_output,
        output_file=output_file,
    )

    if response.status_code >= 400:
        raise SystemExit(EXIT_API_ERROR)


# ── Helpers (each testable in isolation; SRP) ────────────────────────


def _resolve_body(
    *, data_inline: str | None, input_file: str | None
) -> bytes | None:
    """Resolve the request body from either --data or --input.

    Returns ``None`` when neither is supplied. Validates the input is
    parseable JSON so a malformed payload fails fast instead of
    surprising the backend with bad input.
    """
    raw: str | None = None
    if data_inline is not None:
        raw = data_inline
    elif input_file is not None:
        if input_file == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(input_file).read_text(encoding="utf-8")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"Body is not valid JSON: {exc}") from exc
    return json.dumps(parsed).encode("utf-8")


def _parse_headers(items: tuple[str, ...]) -> dict[str, str]:
    """Parse ``--header "Name: Value"`` tuples into a dict.

    HTTP convention uses ``Name: Value`` (colon + space); we split on
    the first colon and strip whitespace so callers can write
    ``--header "X-Foo:bar"`` or ``--header "X-Foo: bar"`` equivalently.
    """
    result: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise click.UsageError(
                f'Bad --header value {item!r}; expected "Name: Value"'
            )
        name, _, value = item.partition(":")
        result[name.strip()] = value.strip()
    return result


def _parse_query(items: tuple[str, ...]) -> dict[str, str] | None:
    """Parse ``--query name=value`` tuples into a dict.

    Returns ``None`` when no items so httpx skips the query string
    entirely (slightly cleaner than ``{}``).
    """
    if not items:
        return None
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise click.UsageError(
                f"Bad --query value {item!r}; expected NAME=VALUE"
            )
        name, _, value = item.partition("=")
        result[name] = value
    return result


def _execute_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, str] | None,
    body: bytes | None,
) -> httpx.Response:
    """Run the request via the SDK's HTTPClient.raw_request (no raise on 4xx/5xx)."""
    client = _build_client()
    try:
        return asyncio.run(
            client._async._http.raw_request(
                method,
                path,
                headers=headers if headers else None,
                params=params,
                content=body,
            )
        )
    except APIError as exc:
        # raw_request itself does not raise on HTTP status, but transport
        # errors (network) may bubble up — translate them.
        click.echo(f"Transport error: {exc}", err=True)
        raise SystemExit(EXIT_API_ERROR) from exc
    finally:
        try:
            client.close()
        except Exception:
            pass


def _render_response(
    *,
    response: httpx.Response,
    include_headers: bool,
    json_output: bool,
    output_file: str | None,
) -> None:
    """Format and emit the response per the user's flags."""
    body_text = _format_body(response, json_output=json_output)

    if output_file is not None:
        Path(output_file).write_text(body_text, encoding="utf-8")
        return

    if include_headers:
        click.echo(_format_status_line(response))
        for name, value in response.headers.items():
            click.echo(f"{name}: {value}")
        click.echo("")  # blank line separating headers from body
    click.echo(body_text)


def _format_status_line(response: httpx.Response) -> str:
    version = response.http_version or "HTTP/1.1"
    return f"{version} {response.status_code} {response.reason_phrase}"


def _format_body(response: httpx.Response, *, json_output: bool) -> str:
    """Return the response body as a string, pretty-printed when possible.

    The default mode pretty-prints JSON for human readability; ``--json``
    emits single-line JSON (jq-friendly); non-JSON payloads pass
    through verbatim either way.
    """
    raw = response.text
    if not raw:
        return ""
    parsed: Any
    try:
        parsed = response.json()
    except (ValueError, json.JSONDecodeError):
        return raw
    if json_output:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)

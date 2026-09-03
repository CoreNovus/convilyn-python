"""The tool bodies, as plain functions returning plain dicts.

Deliberately free of any ``mcp`` import. Two reasons, and the second is the one
that matters:

* The MCP SDK lives behind the ``convilyn[mcp]`` extra, so anything importing it
  at module scope would break the release pipeline's no-extras wheel smoke test
  (``cli/local.py`` records that rule; this module obeys it).
* **The logic can then be tested without the extra installed at all.** A test
  that has to stand up an MCP session to check what a tool returns is a test
  most contributors will not run.

:mod:`convilyn.mcp.server` is the only place the protocol appears; it imports
these and hands the SDK their signatures.

Every tool returns a JSON-serialisable dict and **raises nothing**. An MCP tool
that raises gives the model a stack trace to reason about; one that returns
``{"ok": false, "error": ..., "hint": ...}`` gives it something to act on. The
hint is the same string the CLI prints, so a user watching the transcript and a
user running the command by hand read identical words.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from convilyn import local
from convilyn.local.errors import LocalError, MissingDependencyError

#: Operations `pdf` accepts, mapped to the `convilyn.local.pdf`
#: function each one calls.
#:
#: ONE tool with an operation enum rather than seven tools. Tool descriptions are
#: re-sent to the model every turn — measured at ~1,100 characters each in this
#: org's own catalogue — so seven near-identical page-operation entries would
#: cost roughly seven times as much context to say one thing seven ways.
PDF_OPERATIONS: tuple[str, ...] = (
    "merge",
    "select",
    "split",
    "rotate",
    "compress",
    "protect",
    "unlock",
    "info",
)

#: `info`'s default bounds. It used to return the entire text layer: a 19-page
#: spec measured 36,660 chars — roughly 9,200 tokens from one call, and doubled
#: on the wire because the payload rides in both the text block and
#: `structuredContent`. The description called it "the cheapest way to learn
#: whether a PDF has a text layer", which it was not; it was the most expensive.
#:
#: 4,000 chars is about 1,000 tokens, or two pages — enough to identify a
#: document and prove it has a text layer, which is what the operation is for.
#: Both are overridable per call, so nothing is unreachable, only un-accidental.
INFO_MAX_CHARS = 4_000
INFO_MAX_PAGES = 20


#: What each operation needs, for the message a missing argument produces.
#: Written out rather than derived from `_run_pdf`'s subscripts, because the
#: point is to tell the model what to send NEXT — deriving it would report the
#: one key that happened to be read first, not the set the call requires.
_PDF_REQUIRED_HINT: dict[str, str] = {
    "info": "needs: source",
    "merge": "needs: sources (a list), out",
    "split": "needs: source, out_dir",
    "select": "needs: source, out (pages optional)",
    "rotate": "needs: source, out (degrees, pages optional)",
    "compress": "needs: source, out",
    "protect": "needs: source, out, password",
    "unlock": "needs: source, out, password",
}


def _error(message: str, *, hint: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    return payload


def convert(
    paths: list[str],
    *,
    to: str = "md",
    out_dir: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert documents on this machine. No account, no network, no tokens.

    Takes a LIST even for one file, because batch is where this beats reading
    the documents directly: converting twelve files costs the same zero tokens
    as converting one, while reading twelve into a model's context does not.

    A failed file is a result with ``ok: false``, not an exception — one
    unreadable file in twelve must not lose the other eleven.
    """
    if not paths:
        return _error("no paths given")

    targets = [Path(p) for p in paths]
    missing = [str(p) for p in targets if not p.is_file()]
    if missing:
        return _error(f"not a file: {', '.join(missing)}")

    destination = Path(out_dir) if out_dir else targets[0].parent
    results: list[dict[str, Any]] = []
    for source in targets:
        try:
            outcome = local.convert(
                source, to=to, out=destination / f"{source.stem}.{to}", overwrite=overwrite
            )
        except LocalError as exc:
            results.append(
                {
                    "ok": False,
                    "source": str(source),
                    "error": str(exc),
                    "hint": _install_hint(source, to),
                }
            )
        except (OSError, ValueError) as exc:
            results.append({"ok": False, "source": str(source), "error": str(exc)})
        else:
            # ``output`` is None exactly when ``ok`` is false (see
            # ``local.types.ConversionOutcome``). ``convert`` raises on the
            # failures we know about, so this branch is not expected to be
            # reached -- but "not expected" is not "cannot", and the alternative
            # is an AttributeError deep inside a tool call that the model reads
            # as a crash rather than as a result it can act on.
            if outcome.output is None:
                results.append(
                    {
                        "ok": False,
                        "source": str(source),
                        "error": (
                            outcome.error.message
                            if outcome.error
                            else "the converter reported no output file"
                        ),
                    }
                )
            else:
                results.append(
                    {
                        "ok": True,
                        "source": str(source),
                        "output": str(outcome.output),
                        "bytes": outcome.output.stat().st_size if outcome.output.is_file() else 0,
                    }
                )

    converted = sum(1 for r in results if r["ok"])
    return {
        "ok": converted == len(results),
        "converted": converted,
        "total": len(results),
        "tokens_used": 0,
        "results": results,
    }


def _install_hint(source: Path, to: str) -> str:
    """The install line for whatever this route is missing, or ''.

    Read off the route's own requirements rather than composed from a table, so
    it cannot drift from what the conversion actually needs.
    """
    fmt = local.detect_format(source)
    if fmt is None:
        return ""
    route = local.capabilities().can(fmt, to)
    if route is None or route.available:
        return ""
    return " ".join(
        r.install_hint for r in route.requirements if not r.available and r.install_hint
    )


def capabilities(path: str | None = None, *, source_format: str | None = None) -> dict[str, Any]:
    """What this machine can convert — answered narrowly, never dumped.

    `local.capabilities()` knows **287 routes** on a fully-equipped machine.
    Returning all of them would put a table nobody reads into the model's
    context on every call, which is the same waste the tool-description budget
    is policed for. So this answers a question instead:

    * with a path or a format — can THAT be converted, and if not, what is
      missing and how is it installed;
    * with neither — a count plus the extras that are absent, which is what
      "why did that fail?" actually needs.
    """
    caps = local.capabilities()
    fmt = source_format or (local.detect_format(Path(path)) if path else None)

    if path and fmt is None:
        return _error(
            f"unrecognised format: {Path(path).name}",
            hint="pass source_format explicitly if the extension is missing or wrong",
        )

    if fmt is not None:
        routes = [r for r in caps.routes if r.source_format == fmt]
        if not routes:
            return _error(f"nothing can read {fmt!r} on this machine")
        return {
            "ok": True,
            "source_format": fmt,
            "targets": sorted(caps.available_targets(fmt)),
            "blocked": [_blocked(r) for r in routes if _is_actionable(r)],
        }

    missing = sorted(
        {
            requirement.install_hint
            for route in caps.routes
            for requirement in route.requirements
            if not requirement.available and requirement.install_hint
        }
    )
    return {
        "ok": True,
        "routes_available": len(caps.available_routes),
        "routes_total": len(caps.routes),
        "missing_requirements": missing,
    }


def _is_actionable(route: Any) -> bool:
    """Whether a blocked route is worth telling the model about.

    Only routes something can be DONE about. `unsupported_by_build` means the
    installed library will never do this, whatever is installed next — it is the
    "stop looking" answer, and repeating it is noise.

    Measured, which is why this filter exists: asking about `pdf` returned 25
    blocked rows, every one of them Pillow declining to read a PDF as an image,
    because `pdf` is a member of the image-format matrix as well as the document
    one. Twenty-five ways of saying "no" pushed the one useful line — the target
    that IS available — off the end of what anyone would read.
    """
    return not route.available and route.unavailable_kind != "unsupported_by_build"


def _blocked(route: Any) -> dict[str, Any]:
    return {
        "target": route.target_format,
        "reason": route.unavailable_reason,
        "install": " ".join(
            r.install_hint for r in route.requirements if not r.available and r.install_hint
        ),
    }


def pdf(operation: str, **kwargs: Any) -> dict[str, Any]:
    """Page operations on a PDF, format-preserving. Local, zero tokens.

    One entry point for eight operations — see `PDF_OPERATIONS` for why that is
    one tool rather than eight.
    """
    if operation not in PDF_OPERATIONS:
        return _error(
            f"unknown operation {operation!r}",
            hint=f"one of: {', '.join(PDF_OPERATIONS)}",
        )
    try:
        return _run_pdf(operation, kwargs)
    except MissingDependencyError as exc:
        return _error(str(exc), hint='uv add "convilyn[pdf]" (or pip install "convilyn[pdf]")')
    except LocalError as exc:
        # NOT the install hint. `PdfOperationError` also subclasses `LocalError`,
        # so a single `except LocalError` told every PDF failure to install a
        # package — measured, with pypdf present: a corrupt file returned
        # "Stream has ended unexpectedly" alongside `uv add "convilyn[pdf]"`.
        # Advice that cannot be acted on is worse than none: it sends the reader
        # to fix an environment that was never the problem.
        return _error(str(exc))
    except KeyError as exc:
        # `_run_pdf` reads its arguments by subscript, and `server.py` strips
        # every `None` before calling — so a parameter the model simply omitted
        # arrives here as a KeyError rather than as anything the caller can act
        # on. It was NOT in this tuple, which made it the one input that could
        # break this module's "raises nothing" promise (see the header): the
        # model got a stack trace where every other failure gets a result.
        #
        # `exc.args[0]` is the missing key, so the message names the parameter
        # instead of saying that something was missing.
        missing = exc.args[0] if exc.args else "?"
        return _error(
            f"{operation!r} needs a {missing!r} argument",
            hint=_PDF_REQUIRED_HINT.get(operation, ""),
        )
    except (OSError, ValueError, TypeError) as exc:
        return _error(str(exc))


def _run_pdf(operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    from convilyn.local import pdf as pdf_ops

    if operation == "info":
        source = Path(kwargs["source"])
        total = pdf_ops.page_count(source)
        max_chars = int(kwargs.get("max_chars", INFO_MAX_CHARS))
        max_pages = int(kwargs.get("max_pages", INFO_MAX_PAGES))
        requested = kwargs.get("pages")

        # A PAGE bound as well as a character bound, and the page one is not
        # redundant: pypdf extracts per page, so slicing the string afterwards
        # would still have paid to read all 300 pages of a long document. It is
        # composed as the same `pages` string the whole chain already speaks —
        # `extract_text` -> `parse_pages` — which is why neither `local/pdf.py`
        # nor the engine changes here.
        pages = requested or (f"1-{max_pages}" if total > max_pages else None)
        text = pdf_ops.extract_text(source, pages=pages)
        clipped = text[:max_chars]
        truncated = len(text) > max_chars or (requested is None and pages is not None)

        result: dict[str, Any] = {
            "ok": True,
            "pages": total,
            "pages_read": pages or f"1-{total}",
            "text": clipped,
            "text_chars": len(clipped),
            "text_truncated": truncated,
        }
        if truncated:
            # Do not cut silently. The published tool-authoring guidance is to
            # steer toward a more targeted call, and there are two right answers
            # here rather than one, because they solve different problems.
            result["hint"] = (
                f"Showing {result['pages_read']} of {total} page(s), "
                f"{len(clipped)} of {len(text)} extracted chars. "
                'For a specific range: {"operation": "info", "pages": "12-14"}. '
                "For the WHOLE document at zero tokens, convert it instead: "
                'convert(paths=[...], to="md") writes a file and returns its '
                "path — then read only the part you need."
            )
        return result
    if operation == "merge":
        out = Path(kwargs["out"])
        pdf_ops.merge([Path(p) for p in kwargs["sources"]], out)
        return {"ok": True, "output": str(out), "pages": pdf_ops.page_count(out)}
    if operation == "split":
        out_dir = Path(kwargs["out_dir"])
        written = pdf_ops.burst(Path(kwargs["source"]), out_dir, pages=kwargs.get("pages"))
        return {"ok": True, "outputs": [str(p) for p in written]}

    source, out = Path(kwargs["source"]), Path(kwargs["out"])
    if operation == "select":
        pdf_ops.select(source, out, pages=kwargs.get("pages"))
    elif operation == "rotate":
        pdf_ops.rotate(
            source, out, degrees=int(kwargs.get("degrees", 90)), pages=kwargs.get("pages")
        )
    elif operation == "compress":
        pdf_ops.compress(source, out)
    elif operation == "protect":
        pdf_ops.encrypt(source, out, password=kwargs["password"])
    else:  # unlock
        pdf_ops.decrypt(source, out, password=kwargs["password"])
    return {"ok": True, "output": str(out)}


# ── Hosted tools — these SPEND MONEY, and say so ─────────────────────


def _client() -> Any:
    """A configured sync client, or raise with the command that fixes it.

    Auth resolution is the SDK's own three-step chain (explicit arg → env →
    the credentials file `convilyn setup` writes), so the MCP configuration
    carries no key. That is deliberate: an `env` block in
    `~/.codex/config.toml` or a plugin manifest puts a live credential in a
    file people paste into issues.
    """
    from convilyn import Convilyn

    return Convilyn()


#: Directory names that hold credentials on every platform this SDK runs on.
#: Checked against the RESOLVED path's components, so a symlink pointing into
#: one is refused by the same rule that refuses naming it directly.
_SECRET_DIRS = frozenset(
    {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".gcloud", ".azure", ".config/gh"}
)

#: Filename shapes that are a credential wherever they sit — including this
#: SDK's OWN `credentials.json`, which is the first thing a prompt-injected
#: model would reach for, and which lives outside every directory above
#: (`%APPDATA%/convilyn/` on Windows, `~/.config/convilyn/` elsewhere).
#:
#: Refused even INSIDE an allowed root, because a project directory routinely
#: contains a `.env`. Costs nothing in reach: none of these extensions is a
#: source format this platform reads (measured against all 52).
_SECRET_FILES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    "credentials",
    "credentials.*",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
)


def _is_secret_shaped(resolved: Path) -> bool:
    parts = {part.lower() for part in resolved.parts}
    if parts & {d for d in _SECRET_DIRS if "/" not in d}:
        return True
    lowered = "/".join(resolved.parts).lower()
    if any(f"/{d}/" in f"/{lowered}/" for d in _SECRET_DIRS if "/" in d):
        return True
    name = resolved.name.lower()
    return any(fnmatch(name, pattern) for pattern in _SECRET_FILES)


def _fence(paths: list[str], roots: tuple[Path, ...]) -> tuple[list[Path], str]:
    """Resolve each path; refuse anything outside `roots` or credential-shaped.

    Returns ``(resolved, "")`` or ``([], reason)``. **The refusal is the
    product here**, not a validation nicety: `understand` uploads whatever it is
    handed, so without this it is a general-purpose read primitive for any file
    the user's account can open — pointed at by a model that reads untrusted
    document text. `~/.ssh/id_rsa` is a file; `is_file()` was the whole check.

    Two independent rules, and both are needed:

    * **inside a root** — `Path.resolve()` first, so a symlink cannot walk out
      of the workspace and back into `~/.aws`. Checking the unresolved path
      would compare the lie rather than the destination.
    * **not credential-shaped** — applied even within a root, because a
      project directory routinely contains a `.env`, and "the model stayed in
      the workspace" is not a reason to upload it.
    """
    if not roots:  # pragma: no cover - the caller always supplies CWD at minimum
        return [], "no allowed roots"

    resolved_roots = [root.resolve() for root in roots]
    accepted: list[Path] = []
    for raw in paths:
        try:
            target = Path(raw).resolve()
        except OSError as exc:
            return [], f"cannot resolve {raw!r}: {exc}"
        if not target.is_file():
            return [], f"not a file: {raw}"
        if _is_secret_shaped(target):
            return [], f"refusing {Path(raw).name} — it looks like a credential file"
        if not any(target.is_relative_to(root) for root in resolved_roots):
            allowed = ", ".join(str(root) for root in resolved_roots)
            return [], f"{raw} is outside this session's workspace ({allowed})"
        accepted.append(target)
    return accepted, ""


def precheck_understand(
    paths: list[str], *, schema: dict[str, Any], allowed_roots: tuple[Path, ...]
) -> tuple[list[Path], dict[str, Any] | None]:
    """Everything that can refuse for FREE. Returns ``(targets, refusal|None)``.

    Split out so the server can run it **before** it asks the human anything.
    Ordering is the point: a malformed request that would fail anyway must not
    first put an approval prompt in front of a person, and must not cost a
    round-trip to price a call that cannot happen. `understand` re-runs this
    rather than trusting a caller to have done it — it is the enforcement point,
    and a check the caller may skip is not one.
    """
    if not paths:
        return [], _error("no paths given")
    targets, refusal = _fence(paths, allowed_roots)
    if refusal:
        return [], _error(
            refusal,
            hint="this tool uploads what it is given; it only reads files inside "
            "the workspace your editor opened, and never credential files",
        )
    if not isinstance(schema, dict) or not schema:
        return [], _error(
            "schema must be a non-empty JSON Schema object",
            hint='e.g. {"type": "object", "properties": {"total": {"type": "number"}}}',
        )
    return targets, None


def understand(
    paths: list[str],
    *,
    schema: dict[str, Any],
    allowed_roots: tuple[Path, ...],
    instructions: str | None = None,
) -> dict[str, Any]:
    """Extract structured data from documents, conforming to a JSON Schema.

    **This spends credits.** Unlike every other tool here it reaches the
    platform, and the result reports what it cost so the number is visible in
    the transcript rather than only on a bill later.

    Takes local paths and uploads them, rather than the file IDs the underlying
    API wants. Making the model run upload → collect ids → understand is three
    round trips to express one intent, and the ids are useless to it afterwards.

    ``allowed_roots`` is a REQUIRED keyword, deliberately. This is the only tool
    here that sends bytes off the machine, and a containment argument with a
    default is one a future caller forgets to pass — so the decision cannot be
    omitted, only made. The server fills it from the client's declared MCP roots.
    """
    targets, refusal = precheck_understand(paths, schema=schema, allowed_roots=allowed_roots)
    if refusal is not None:
        return refusal

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 - any auth failure is the same advice
        return _error(str(exc), hint="run `convilyn setup` to sign in")

    try:
        with client:
            # `targets`, NOT `paths`. The fence resolved symlinks; uploading the
            # unresolved string would send whatever the link points at now,
            # which is the check and the use disagreeing about the same file.
            file_ids = [client.files.upload(p).file_id for p in targets]
            result = client.goals.understand(file_ids, schema=schema, instructions=instructions)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim; see below
        # Relayed rather than classified. The SDK's exception taxonomy already
        # distinguishes insufficient credits from a plan requirement from a
        # failed job, and each carries advice the caller must act on
        # differently — flattening them to "understand failed" would discard
        # exactly the part that says what to do.
        return _error(f"{type(exc).__name__}: {exc}")

    return {"ok": True, "result": _plain(result), "charged": _charge_of(result)}


def quota(tools: list[str] | None = None, *, max_iterations: int | None = None) -> dict[str, Any]:
    """What a workflow would cost, and whether this account may run it.

    Read-only — it spends nothing. It exists so the model can price a run
    BEFORE calling `understand`, which is the only way an approval prompt can
    be answered with a number rather than a guess.
    """
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc), hint="run `convilyn setup` to sign in")

    try:
        with client:
            estimate = client.account.get_quota(tools=tools, max_iterations=max_iterations)
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return {"ok": True, "estimate": _plain(estimate)}


def _plain(value: Any) -> Any:
    """Pydantic model → dict, anything else unchanged."""
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


def _charge_of(result: Any) -> dict[str, Any] | None:
    """The charge a hosted result reports, or None when it reports none.

    `None` means *not reported*, never *free*. A hosted call whose cost cannot
    be read is the one case where silence would be read as zero, so the caller
    gets an absence it can see rather than a zero it would believe.
    """
    for attr in ("charged_micro_u", "charged_micro_usd", "cost_micro_u"):
        micro = getattr(result, attr, None)
        if isinstance(micro, int):
            return {"micro_u": micro, "usd": round(micro / 1_000_000, 6)}
    return None

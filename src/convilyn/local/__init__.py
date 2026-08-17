"""Offline file conversion — no API key, no network, no account.

Everything in this namespace runs on your machine. Nothing here opens a
connection, reads ``CONVILYN_API_KEY``, or consumes quota. ``convilyn.Convilyn``
remains the way to reach the platform's hosted workflows; this is the half that
needs nothing from us.

    from convilyn import local

    local.convert("report.docx", to="md")
    local.convert_many(Path("in").glob("*.pdf"), to="md", out_dir="out")

Plain text and delimited text convert on a bare install. Everything else needs
the optional dependency for the format you are reading, and you install only the
ones you use::

    uv add "convilyn[pdf]"          # PDF
    uv add "convilyn[documents]"    # every document format

Two formats families need a desktop application rather than a package: legacy
and OpenDocument office files go through LibreOffice, ebooks through Calibre.
Neither can come from PyPI, so neither is an extra.

Ask before you convert, and nothing will surprise you::

    for route in local.capabilities().routes:
        print(route.source_format, route.available, route.unavailable_reason)

When a route is unavailable, ``unavailable_kind`` says whether it is worth doing
anything about it — the same answer on every engine, so you never have to branch
on which one you are looking at::

    worth_fixing = [
        r for r in local.capabilities().routes
        if not r.available and r.unavailable_kind != "unsupported_by_build"
    ]

``convilyn local doctor`` prints the same picture from the shell.

## How this package is laid out

**A leading underscore means internal.** That is the whole rule; you never have
to consult a list to know which side of the line a module is on.

Public, and covered by the SDK's semantic-versioning promise:

* this module — the only import a user needs
* ``types`` — frozen models (``Route``, ``Requirement``, ``ConversionResult``…)
* ``errors`` — the error taxonomy, all of it under ``convilyn.ConvilynError``
* ``api`` — the functions re-exported above

Internal, and free to change in any release:

* ``_probe`` — "is this package importable, where is this executable"
* ``_tools`` — *what* the external programs are, and where they live
* ``_run`` — *how* to invoke them
* ``_routes`` — the route table, and the availability join over it
* ``_engine`` — **generated**, and regenerated whenever the upstream engine
  changes; never edit it

``_tools`` and ``_run`` are separate because their questions are asked at
different times. "Do I have LibreOffice" runs whenever somebody lists what this
machine can convert; "run LibreOffice" happens only once a conversion starts.
Splitting them lets the route builder answer the first without being able to
start a subprocess, and lets the generated bridge invoke the second without
dragging in the discovery tables.
"""

# No `from __future__ import annotations` here, and the omission is deliberate:
# it binds a `__future__._Feature` object named `annotations`, which is not a
# module, so it surfaces as a public attribute of this namespace. `convilyn`'s
# own `__init__.py` omits it for the same reason. A re-export hub has no
# annotations to postpone.
from convilyn.local import pdf
from convilyn.local.api import (
    ProgressCallback,
    aconvert,
    aconvert_many,
    capabilities,
    convert,
    convert_many,
    detect_format,
    plan,
)
from convilyn.local.errors import (
    ConversionFailedError,
    LocalError,
    MissingDependencyError,
    PdfOperationError,
    UnsupportedRouteError,
)
from convilyn.local.types import (
    Capabilities,
    ConversionResult,
    Engine,
    LocalErrorInfo,
    ProgressEvent,
    ProgressPhase,
    Requirement,
    RequirementKind,
    Route,
    UnavailableKind,
)

__all__ = [
    "Capabilities",
    "ConversionFailedError",
    "ConversionResult",
    "Engine",
    "LocalError",
    "LocalErrorInfo",
    "MissingDependencyError",
    "PdfOperationError",
    "ProgressCallback",
    "ProgressEvent",
    "ProgressPhase",
    "Requirement",
    "RequirementKind",
    "Route",
    "UnavailableKind",
    "UnsupportedRouteError",
    "aconvert",
    "aconvert_many",
    "capabilities",
    "convert",
    "convert_many",
    "detect_format",
    "pdf",
    "plan",
]

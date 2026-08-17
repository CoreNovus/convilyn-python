"""The values offline conversion hands back.

Frozen throughout, like the rest of this SDK's public models. Unlike the rest,
these carry no wire aliases: nothing here is ever serialized to or from the
Convilyn API, so a camelCase alias would describe a format that does not exist.

The shape is built around one idea: **asking what is possible must never raise.**
A missing dependency is a fact about the machine, so it appears as data — a
:class:`Route` with ``available=False`` and a sentence saying why — rather than
as an exception the caller has to provoke in order to read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: What kind of thing a route needs. ``python_package`` can be installed with an
#: extra; ``external_tool`` cannot, and that difference is the whole reason the
#: two are distinguished rather than merged into a list of names.
RequirementKind = Literal["python_package", "external_tool"]

#: Which engine performs a conversion. Reported so a caller can tell a pure
#: Python route from one that shells out.
Engine = Literal["structured", "office-suite", "ebook", "image", "media"]

#: Why a route cannot run here — and, load-bearing, whether anything fixes it.
#:
#: Exists because ``available=False`` alone did not mean the same thing on every
#: engine. A document route was unavailable only ever because something was not
#: installed; an image route can be unavailable because this build of Pillow has
#: no encoder for the target, which no install changes. A caller therefore had to
#: know which engine it was looking at before it could read the flag — so the
#: engine leaked into the meaning of a field, and the two answers a caller can
#: act on were indistinguishable without it.
#:
#: The three values are the three distinguishable causes, ordered by what they
#: cost the user:
#:
#: * ``missing_requirement`` — a declared :class:`Requirement` is unsatisfied.
#:   :pyattr:`Route.missing` names it and ``install_hint`` says how.
#: * ``missing_plugin`` — everything declared is satisfied, and a third-party
#:   component this package does not distribute would add the format.
#:   :pyattr:`Route.unavailable_reason` names it. Separate from the above because
#:   the fix exists but is not one of our extras, so the SDK can describe it and
#:   cannot install it.
#: * ``unsupported_by_build`` — everything declared is satisfied and nothing
#:   installable changes the answer. The one value that means "stop looking".
#:
#: **"Is it worth trying to fix" is therefore one comparison, on any engine:**
#: ``unavailable_kind != "unsupported_by_build"``. That is the whole point of the
#: field; a caller that switches on ``engine`` to interpret availability is
#: reading it wrong.
UnavailableKind = Literal["missing_requirement", "missing_plugin", "unsupported_by_build"]

#: What happened to one file in a batch.
ProgressPhase = Literal["start", "done", "failed"]


class Requirement(BaseModel):
    """One thing a route needs before it can run."""

    model_config = ConfigDict(frozen=True)

    kind: RequirementKind
    #: Import name for a package, tool key for an external program.
    name: str
    available: bool
    #: Copy-pasteable command, or a sentence for something pip cannot install.
    install_hint: str
    #: The extra that provides this package, when one does. ``None`` when no
    #: extra of this package supplies it — an external program, or a codec plugin
    #: we deliberately do not ship. Those are exactly the cases a caller must not
    #: paper over by offering ``pip install "convilyn[...]"``, which cannot work.
    #: ``install_hint`` always carries something the user can actually run.
    extra: str | None = None
    #: When true, the route runs without it and produces a poorer result.
    #:
    #: Pillow is the case this exists for: the extractors use it to recognise
    #: that the same picture appears on forty pages, and to drop spacers and
    #: rules. Without it they fall back to hashing raw bytes, so a re-encoded
    #: repeat is no longer recognised as a repeat. The document still converts.
    #: Reporting that as "unavailable" would be false; reporting nothing would
    #: hide a real difference in output quality.
    optional: bool = False


class Route(BaseModel):
    """How one source format reaches one target format, and whether it can.

    **Every field below means the same thing on every engine.** That is a
    property worth stating because it did not hold once: ``available=False`` used
    to mean "install something" on a document route and could mean "impossible
    here, forever" on an image route, so a caller had to switch on
    :pyattr:`engine` before it could read :pyattr:`available`. It no longer does
    — :pyattr:`unavailable_kind` carries that distinction explicitly, for all
    four engines and for any family added later.

    The invariants, which ``tests/unit/local/test_route_invariants.py`` asserts
    over every route on a machine with the extras and on one without:

    * ``available is True`` ⟺ ``unavailable_kind is None`` ⟺
      ``unavailable_reason is None``
    * ``unavailable_kind == "missing_requirement"`` ⟺ ``missing != ()``
    * a kind of ``missing_plugin`` or ``unsupported_by_build`` ⟹ ``missing == ()``
      (everything this package declares is present; the gap is elsewhere)
    """

    model_config = ConfigDict(frozen=True)

    source_format: str
    target_format: str
    engine: Engine
    available: bool
    requirements: tuple[Requirement, ...] = ()
    #: Why not, when :pyattr:`available` is false — and whether to bother trying.
    #: ``None`` exactly when the route is available. See :data:`UnavailableKind`;
    #: prefer it over parsing :pyattr:`unavailable_reason`, which is prose for a
    #: person.
    unavailable_kind: UnavailableKind | None = None
    #: A full sentence naming what is missing and how to get it, or ``None``
    #: when the route is available. This is the text every "cannot convert"
    #: message in this namespace is built from, so it is authored once.
    unavailable_reason: str | None = None

    @property
    def missing(self) -> tuple[Requirement, ...]:
        """Unsatisfied requirements that actually block the route."""
        return tuple(r for r in self.requirements if not r.available and not r.optional)

    @property
    def degraded_by(self) -> tuple[Requirement, ...]:
        """Absent optional requirements — the route runs, less well."""
        return tuple(r for r in self.requirements if not r.available and r.optional)


class LocalErrorInfo(BaseModel):
    """Why one conversion in a batch failed, in a form that survives JSON."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    requirement: Requirement | None = None


class ConversionResult(BaseModel):
    """The outcome of converting one file."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    source: Path
    source_format: str
    target_format: str
    #: Which engine ran it. Always set when :pyattr:`ok`; ``None`` only for a
    #: failure that never reached one, because the requested conversion has no
    #: route at all — an unknown extension has no engine, and naming one would be
    #: inventing a fact about the run.
    #:
    #: It was non-optional and filled with the literal ``"structured"``, so every
    #: failed conversion in a batch reported that engine whatever had actually been
    #: attempted, image conversions included.
    engine: Engine | None
    elapsed_seconds: float = Field(ge=0)
    #: Where the output landed. ``None`` when ``ok`` is false.
    output: Path | None = None
    #: Best-effort notes from the extractor, e.g. that a format carries no
    #: structure so headings were inferred. Advisory: a document with warnings
    #: still converted.
    warnings: tuple[str, ...] = ()
    error: LocalErrorInfo | None = None


class ProgressEvent(BaseModel):
    """One step of a batch, delivered to ``convert_many(on_progress=...)``."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    total: int = Field(ge=0)
    source: Path
    phase: ProgressPhase


class Capabilities(BaseModel):
    """Everything this machine can and cannot convert, and why."""

    model_config = ConfigDict(frozen=True)

    routes: tuple[Route, ...] = ()
    #: Every optional package this namespace knows about, present or not.
    packages: tuple[Requirement, ...] = ()
    #: Every external program, present or not. Never installable with an extra.
    tools: tuple[Requirement, ...] = ()

    def can(self, source_format: str, target_format: str) -> Route | None:
        """The route for a pair, or ``None`` if this engine has no such route.

        ``None`` means *unknown conversion*; a returned route with
        ``available=False`` means *known, but not on this machine*. Collapsing
        the two would lose the only distinction the caller can act on.
        """
        for route in self.routes:
            if route.source_format == source_format and route.target_format == target_format:
                return route
        return None

    def available_targets(self, source_format: str) -> tuple[str, ...]:
        """Target formats reachable from ``source_format`` right now."""
        return tuple(
            r.target_format for r in self.routes if r.source_format == source_format and r.available
        )

    @property
    def available_routes(self) -> tuple[Route, ...]:
        return tuple(r for r in self.routes if r.available)


__all__ = [
    "Capabilities",
    "ConversionResult",
    "Engine",
    "LocalErrorInfo",
    "ProgressEvent",
    "ProgressPhase",
    "Requirement",
    "RequirementKind",
    "Route",
    "UnavailableKind",
]

"""What can be converted here, and what each route needs.

The engine's registry knows which formats it can *read*. This module answers the
question a user actually has, which is whether it can read them **on this
machine right now** — and if not, what to install.

Two tables and a join. One says which packages each source format needs; the
other comes from the engine and says which formats are read directly and which
go through a bridge. Neither is a chain of ``if``s, so adding a format is a row.

Nothing here imports an extractor. Availability is decided by
:mod:`convilyn.local._probe`, which resolves a module without executing it, so
listing the routes costs nothing even when everything is installed.

Every "cannot convert" sentence in this namespace originates here, on the
``Route``. The error classes quote it rather than composing their own, so the
text a user sees is the same whether it arrived from ``capabilities()``, from a
raised exception, or from the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

from convilyn.local._engine.formats import DocumentFormat, ImageFormat
from convilyn.local._engine.markdown.registry import bridge_for, can_read, extractor_for
from convilyn.local._probe import PackageProbe, package_available
from convilyn.local._tools import TOOLS, ExternalTool, ToolProbe, find_tool, install_hint
from convilyn.local.types import Capabilities, Engine, Requirement, Route, UnavailableKind

#: The target the document engine produces. **It produces exactly one**, and the
#: document table below is single-target by construction: :data:`_PACKAGES` is
#: keyed by source format alone, and :func:`build_routes` fills this in for every
#: document row.
#:
#: Named rather than inlined because it is also the default of the public
#: ``plan()`` and ``convert_many()``, so the three would otherwise repeat a
#: string literal. It is NOT a seam for a second document target — adding a
#: column whose only value is ``"md"`` would be configuration with one possible
#: setting. When a second target genuinely exists, the table gains a real
#: dimension then, informed by what that target actually needs.
#:
#: (The image engine is a real source × target matrix already; see
#: :func:`_image_routes`.)
MARKDOWN = "md"


@dataclass(frozen=True)
class _Package:
    """An optional dependency, in the three spellings that all differ."""

    #: What ``import`` takes — the only one a probe can use.
    import_name: str
    #: What appears on PyPI, and in an install command.
    distribution: str
    #: The extra that pulls it in.
    extra: str
    #: Runs without it, less well. See ``Requirement.optional``.
    optional: bool = False


_PDFPLUMBER = _Package("pdfplumber", "pdfplumber", "pdf")
_PYPDF = _Package("pypdf", "pypdf", "pdf")
_DOCX = _Package("docx", "python-docx", "docx")
_PPTX = _Package("pptx", "python-pptx", "pptx")
_OPENPYXL = _Package("openpyxl", "openpyxl", "xlsx")
_DEFUSEDXML = _Package("defusedxml", "defusedxml", "xml")

#: Improves every route that extracts images; blocks none of them.
_PILLOW = _Package("PIL", "Pillow", "images", optional=True)

#: The same package, as a HARD requirement. An image route without Pillow is not
#: degraded, it is impossible — the optional flag belongs to the document
#: extractors, where its absence only weakens image de-duplication.
_PILLOW_REQUIRED = _Package("PIL", "Pillow", "images")

#: Source format → the packages its extractor needs.
#:
#: A format absent from this table needs nothing, which is a real answer and not
#: an oversight: plain text and delimited text are read with the standard
#: library, so they work on a bare install.
_PACKAGES: dict[DocumentFormat, tuple[_Package, ...]] = {
    DocumentFormat.PDF: (_PDFPLUMBER, _PYPDF, _PILLOW),
    DocumentFormat.DOCX: (_DOCX, _PILLOW),
    DocumentFormat.PPTX: (_PPTX, _PILLOW),
    DocumentFormat.XLSX: (_OPENPYXL,),
    DocumentFormat.XML: (_DEFUSEDXML,),
}

#: Every package this namespace knows about, deduplicated, in a stable order.
_ALL_PACKAGES: tuple[_Package, ...] = (
    _PDFPLUMBER,
    _PYPDF,
    _DOCX,
    _PPTX,
    _OPENPYXL,
    _DEFUSEDXML,
    _PILLOW,
)


def _install_command(extra: str) -> str:
    return f'uv add "convilyn[{extra}]" (or pip install "convilyn[{extra}]")'


def _package_requirement(package: _Package, *, probe: PackageProbe) -> Requirement:
    return Requirement(
        kind="python_package",
        name=package.import_name,
        available=probe(package.import_name),
        install_hint=_install_command(package.extra),
        extra=package.extra,
        optional=package.optional,
    )


def _tool_requirement(tool: ExternalTool, *, find: ToolProbe) -> Requirement:
    return Requirement(
        kind="external_tool",
        name=tool.key,
        available=find(tool) is not None,
        install_hint=install_hint(tool),
        extra=None,
    )


def _reason(source: DocumentFormat, missing: tuple[Requirement, ...]) -> str:
    """The sentence a user reads when a conversion cannot run.

    Written to answer the next question rather than only the current one: what
    is missing, how to get it, and — for an external program, which no extra can
    supply — what they could convert instead without installing anything.

    **External programs come first, and the order is load-bearing.** A bridged
    format needs the office suite *before* the sibling's extractor is reached,
    so leading with "needs python-docx" sends the reader to install an extra
    that leaves them exactly as stuck. Ask for the gating thing first.
    """
    tools = [r for r in missing if r.kind == "external_tool"]
    packages = [r for r in missing if r.kind == "python_package"]
    native = ", ".join(sorted(f.value for f in _native_sources()))

    parts: list[str] = []
    for requirement in tools:
        tool = TOOLS[requirement.name]
        parts.append(
            f"Converting {source.value} needs {tool.display_name}, which was not found "
            f"on PATH or in the standard install location for this platform. "
            f"{install_hint(tool)} Formats that need no external program: {native}."
        )
    if packages:
        extras = sorted({r.extra for r in packages if r.extra})
        names = ", ".join(sorted(r.name for r in packages))
        lead = "It also needs" if tools else f"Converting {source.value} needs"
        one = len(packages) == 1
        parts.append(
            f"{lead} {names}, which {'is' if one else 'are'} not installed. "
            f"Add {'it' if one else 'them'} with "
            f"{' and '.join(_install_command(e) for e in extras)}."
        )
    return " ".join(parts)


def _native_sources() -> tuple[DocumentFormat, ...]:
    """Formats read directly, with no bridge. Used in the "instead" hint."""
    return tuple(f for f in DocumentFormat if extractor_for(f) is not None)


def _requirements_for(
    source: DocumentFormat, *, probe: PackageProbe, find: ToolProbe
) -> tuple[tuple[Requirement, ...], Engine]:
    """``(requirements, engine)`` for reading ``source``.

    The return type names :data:`~convilyn.local.types.Engine` rather than ``str``
    so the engine values below are checked against the Literal where they are
    written. Declared as ``str``, they reached ``Route(engine=...)`` widened, and
    the assignment carried a ``# type: ignore[arg-type]`` that suppressed exactly
    the check worth having — a typo here was a runtime ``ValidationError`` for a
    caller instead of a type error for us.
    """
    bridge = bridge_for(source)
    if bridge is None:
        packages = _PACKAGES.get(source, ())
        return tuple(_package_requirement(p, probe=probe) for p in packages), "structured"

    # A bridged format needs the external tool AND everything its modern
    # sibling needs, because the sibling's extractor is what runs afterwards.
    tool = TOOLS["libreoffice" if bridge.engine == "libreoffice" else "calibre"]
    sibling_packages = _PACKAGES.get(bridge.bridge, ())
    requirements = (
        _tool_requirement(tool, find=find),
        *(_package_requirement(p, probe=probe) for p in sibling_packages),
    )
    return requirements, ("office-suite" if bridge.engine == "libreoffice" else "ebook")


def _image_routes(*, probe: PackageProbe) -> list[Route]:
    """Image-to-image routes, from what Pillow can actually do here.

    **Measured, not declared.** The platform publishes a fixed conversion
    matrix, which is the right answer for a server whose codec set is fixed by
    its image. A desktop's is not: HEIC, AVIF and JPEG XL each depend on a
    separate plugin, and a copied matrix would advertise conversions that fail
    the moment they are attempted.

    Without Pillow there is nothing to measure, so the whole set reports
    unavailable with the one requirement that would change that.
    """
    pillow = _package_requirement(_PILLOW_REQUIRED, probe=probe)
    readable, writable = _measure(available=pillow.available)

    return [
        _image_route(source, target, pillow=pillow, readable=readable, writable=writable)
        for source in ImageFormat
        for target in ImageFormat
        if source is not target
    ]


def _image_route(
    source: ImageFormat,
    target: ImageFormat,
    *,
    pillow: Requirement,
    readable: frozenset[ImageFormat],
    writable: frozenset[ImageFormat],
) -> Route:
    """One image pair's row, with its kind and reason kept in step.

    Extracted from the comprehension above only so ``unavailable_kind`` and
    ``unavailable_reason`` come from ONE call to :func:`_image_reason`. Calling it
    twice — once per field — would let a future edit make the pair disagree, and
    the disagreement would be invisible until somebody trusted one of them.
    """
    unavailable = _image_reason(source, target, pillow=pillow, readable=readable, writable=writable)
    return Route(
        source_format=source.value,
        target_format=target.value,
        engine="image",
        available=unavailable is None,
        requirements=(pillow,),
        unavailable_kind=unavailable[0] if unavailable else None,
        unavailable_reason=unavailable[1] if unavailable else None,
    )


def _measure(*, available: bool) -> tuple[frozenset[ImageFormat], frozenset[ImageFormat]]:
    """``(readable, writable)`` as measured here — both empty without Pillow.

    Empty is the honest answer rather than a degenerate one: with no Pillow
    installed there is no codec to ask, so nothing is readable and nothing is
    writable. The route *set* is unchanged either way, which is what lets a
    caller diff two machines; only the availability flags move.
    """
    if not available:
        return frozenset(), frozenset()

    from convilyn.local._engine.image.pil_formats import get_pil_format_name
    from convilyn.local._pillow_probe import can_read, can_write

    return (
        frozenset(f for f in ImageFormat if can_read(f.value)),
        frozenset(f for f in ImageFormat if can_write(get_pil_format_name(f))),
    )


def _image_reason(
    source: ImageFormat,
    target: ImageFormat,
    *,
    pillow: Requirement,
    readable: frozenset[ImageFormat],
    writable: frozenset[ImageFormat],
) -> tuple[UnavailableKind, str] | None:
    """``(kind, sentence)`` for a pair that cannot run — ``None`` when it can.

    Every known pair gets a row whether or not this machine can run it, because
    the two answers a caller can act on are different. A format nobody has heard
    of is a typo; a format whose codec is one install away is a shopping list.
    Dropping the row collapses the second into the first, and the user is told
    "this engine cannot read heic at all" — which is false, and is the shape of
    answer that sends someone looking for a different library.

    The kind is returned **beside** the sentence rather than derived from it by
    the caller: this function is where the cause is actually known, and a caller
    re-deriving it would be parsing prose to recover a fact that was thrown away
    one frame earlier.

    Reading is reported before writing when both are missing: a caller who
    cannot open the file has nothing to decide about the output.
    """
    if not pillow.available:
        return (
            "missing_requirement",
            f"Converting images needs Pillow, which is not installed. "
            f"Add it with {_install_command(_PILLOW_REQUIRED.extra)}.",
        )
    if source not in readable:
        return _plugin_hint("Reading", source)
    if target not in writable:
        return _plugin_hint("Writing", target)
    return None


def _plugin_hint(verb: str, image_format: ImageFormat) -> tuple[UnavailableKind, str]:
    """``(kind, sentence)``: what is missing, and whether installing fixes it.

    A format absent from :data:`_CODEC_PLUGINS` is one Pillow does not handle in
    that direction at all — PSD and PCD are readable and, by design, not
    writable — so the sentence must not imply an install would help. Offering a
    package name that fixes nothing is worse than admitting the limit.

    That distinction used to live only in the wording, which meant a program had
    no way to act on it: both branches produced ``available=False`` and an
    ``unavailable_reason``, and telling them apart required reading English. The
    kind is now the machine-readable half of the same judgement, and the two
    cannot drift because they are decided here together.
    """
    plugin = _CODEC_PLUGINS.get(image_format)
    if plugin is None:
        return (
            "unsupported_by_build",
            f"{verb} {image_format.value} is not supported by this build of Pillow, "
            f"and no plugin adds it. Run `convilyn local formats --available-only` "
            f"to see what this machine can do.",
        )
    package, what = plugin
    return (
        "missing_plugin",
        f"{verb} {image_format.value} needs {package}, a Pillow plugin this package "
        f"does not install: {what} Add it with `pip install {package}` and the format "
        f"becomes available with no further configuration.",
    )


#: Formats whose codec is a separate install, and what it costs to add.
#:
#: Deliberately names the package rather than an extra. Each was left out of the
#: extras for a stated reason — a native library with no reliable wheel across
#: the supported platforms, or a decoder too large to charge every user for —
#: and offering an extra that does not exist would be worse than offering none.
_CODEC_PLUGINS: dict[ImageFormat, tuple[str, str]] = {
    ImageFormat.HEIC: ("pillow-heif", "a HEIF/HEIC codec."),
    ImageFormat.SVG: ("cairosvg", "a vector rasteriser, which needs a native cairo."),
    ImageFormat.DNG: ("rawpy", "a camera RAW decoder."),
    ImageFormat.CR2: ("rawpy", "a camera RAW decoder."),
    ImageFormat.NEF: ("rawpy", "a camera RAW decoder."),
    ImageFormat.EXR: ("imageio", "a high-dynamic-range reader."),
    ImageFormat.HDR: ("imageio", "a high-dynamic-range reader."),
}


def build_routes(
    *,
    probe: PackageProbe = package_available,
    find: ToolProbe = find_tool,
) -> tuple[Route, ...]:
    """Every route this engine knows, with its availability on this machine.

    Probes are injectable because an environment only ever demonstrates one arm
    of each: CI has the extras installed, so the "missing" branch would never be
    exercised by a test that used the real thing.
    """
    routes: list[Route] = []
    for source in DocumentFormat:
        if not can_read(source):
            continue
        requirements, engine = _requirements_for(source, probe=probe, find=find)
        blocking = tuple(r for r in requirements if not r.available and not r.optional)
        routes.append(
            Route(
                source_format=source.value,
                target_format=MARKDOWN,
                engine=engine,
                available=not blocking,
                requirements=requirements,
                # A document route is unavailable for exactly one reason: something
                # it declared is not installed. There is no document equivalent of
                # a codec the build cannot do — every gap here has a package or a
                # program that closes it, which is why this is unconditional rather
                # than a branch.
                unavailable_kind="missing_requirement" if blocking else None,
                unavailable_reason=_reason(source, blocking) if blocking else None,
            )
        )

    routes += _image_routes(probe=probe)
    return tuple(sorted(routes, key=lambda r: (r.source_format, r.target_format)))


def build_capabilities(
    *,
    probe: PackageProbe = package_available,
    find: ToolProbe = find_tool,
) -> Capabilities:
    """The full picture: routes, optional packages, and external programs."""
    return Capabilities(
        routes=build_routes(probe=probe, find=find),
        packages=tuple(_package_requirement(p, probe=probe) for p in _ALL_PACKAGES),
        tools=tuple(_tool_requirement(t, find=find) for t in TOOLS.values()),
    )


__all__ = ["MARKDOWN", "build_capabilities", "build_routes"]

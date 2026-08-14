"""One function per conversion family, and the table that picks it. Internal.

A *family* is a way of converting, not a format: the document family extracts a
structured model and renders Markdown from it, the image family decodes pixels and
re-encodes them. They share no steps, which is why they are two functions rather
than one with branches — and why there is no base class. Two implementations with
no common code would make an abstraction out of a coincidence.

The table below is keyed by :data:`~convilyn.local.types.Engine`, so three of its
four rows point at the same function. That is honest rather than redundant: a route
reports the engine that ran it (``structured`` vs ``office-suite`` vs ``ebook`` are
genuinely different provenance for a caller) while the *work* those three describe
is one family — extract, then render.

Why this is a table and not an ``if`` chain in ``api.convert``: the dispatch used to
live in ``api._run``, which also rendered the Markdown and wrote the image assets.
Adding a third family — media conversion is the next one — would have turned one
line into an ``if/elif/else`` inside a function that also renders Markdown, a
responsibility media conversion has nothing to do with. Now a family is one row here
plus one function, and nothing in ``api.py`` changes to add it.

``_engine`` is the generated tree and is imported through its public names only;
this module never reaches past that boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from convilyn.local._engine.formats import DocumentFormat, ImageFormat
from convilyn.local._engine.markdown.bridge import extract_via_bridge
from convilyn.local._engine.markdown.registry import extractor_for
from convilyn.local._engine.markdown.render import ASSET_DIR, render
from convilyn.local.types import Engine, Route

#: What every family is called with, and what it returns: ``(warnings, written)``.
#:
#: A name for a signature, not an abstraction — there is no protocol to implement
#: and no shared behaviour to inherit. The output path is passed in rather than
#: returned-only because the caller resolved it (from ``to=`` or ``out=``) and a
#: family must not get to choose where a user's file lands.
Runner = Callable[[Path, Route, Path], tuple[tuple[str, ...], Path]]


def run_document(source: Path, route: Route, output: Path) -> tuple[tuple[str, ...], Path]:
    """Extract a document, render Markdown, and write it — assets included.

    Images are written beside the Markdown under ``assets/`` because that is where
    ``render`` points its links. Writing the ``.md`` alone would produce a document
    whose every image is a broken link, which is the failure the structure-aware
    engine exists to avoid — so the write and the assets belong to the same step
    and are not separable.
    """
    source_format = DocumentFormat(route.source_format)
    extractor = extractor_for(source_format)
    if extractor is not None:
        doc = extractor(source)
    else:
        # No native extractor: convert once into the modern sibling this engine
        # already reads, then run that sibling's extractor. `capabilities()` has
        # already confirmed the external program is present, so reaching here
        # without it is a race, not a routing mistake — and it raises.
        doc = extract_via_bridge(source, source_format)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(doc), encoding="utf-8", newline="\n")

    images = doc.unique_images
    if images:
        assets = output.parent / ASSET_DIR
        assets.mkdir(exist_ok=True)
        for image in images:
            (assets / image.asset_name).write_bytes(image.data)

    return doc.warnings, output


def run_image(source: Path, route: Route, output: Path) -> tuple[tuple[str, ...], Path]:
    """Convert one image: check the budget, open, normalise, save.

    The pixel budget is checked from the header **before** the image is decoded — a
    file can declare itself tens of thousands of pixels square and exhaust memory
    during decode, so a check on the decoded result would run too late to help.
    """
    from PIL import Image

    from convilyn.local._engine.image import convert_core

    convert_core.apply_pillow_limit()
    target = ImageFormat(route.target_format)

    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        convert_core.assert_within_pixel_budget(*opened.size)
        img, warnings = convert_core.normalise_mode(opened, target)
        convert_core.save(img, output, target, convert_core.save_options(target, None))

    return tuple(warnings), output


#: Engine → the family that runs it. **Every** ``Engine`` value has a row, which
#: ``tests/unit/local/test_runners.py`` asserts against the Literal — so adding an
#: engine without a runner fails at test time rather than as a ``KeyError`` on a
#: user's machine.
#:
#: A plain lookup, deliberately: no ``register()``, no import-time mutation, no
#: singleton. Registration indirection buys extensibility this package does not
#: need, since every family ships in this file.
RUNNERS: dict[Engine, Runner] = {
    "structured": run_document,
    "office-suite": run_document,
    "ebook": run_document,
    "image": run_image,
}

__all__ = ["RUNNERS", "Runner", "run_document", "run_image"]

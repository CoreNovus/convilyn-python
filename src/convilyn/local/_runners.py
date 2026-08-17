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
A third family would have turned one line into an ``if/elif/else`` inside a function
that also renders Markdown, a responsibility media conversion has nothing to do with.
That third family has now arrived, and it cost exactly what this shape promised: one
row below plus one function, and not a line of ``api.py``.

``_engine`` is the generated tree and is imported through its public names only;
this module never reaches past that boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from convilyn.local._engine.formats import DocumentFormat, ImageFormat
from convilyn.local._engine.markdown.bridge import extract_via_bridge
from convilyn.local._engine.markdown.model import MarkdownDoc
from convilyn.local._engine.markdown.registry import extractor_for
from convilyn.local._engine.markdown.render import ASSET_DIR, render
from convilyn.local.types import Engine, Route

#: What every family is called with, and what it returns: ``(warnings, written)``.
#:
#: A name for a signature, not an abstraction — there is no protocol to implement
#: and no shared behaviour to inherit. The output path is passed in rather than
#: returned-only because the caller resolved it (from ``to=`` or ``out=``) and a
#: family must not get to choose where a user's file lands.
Runner = Callable[..., tuple[tuple[str, ...], Path]]

#: Every family is called as
#: ``(source, route, output, asset_namespace=..., max_rows=...)``.
#:
#: ``asset_namespace`` is a sub-directory this conversion's companion files go
#: under, empty when it shares its output directory with nobody. Only the document
#: family has companions, so the other two accept the argument and have nothing to
#: put there. That is the honest shape for a uniform dispatch table — the parameter
#: list describes the CALL, not what each family happens to need from it — and the
#: alternative, letting one row take a different signature, puts back in the caller
#: exactly the branch this table exists to remove.
#:
#: ``max_rows`` arrived on the same terms and is the second instance of that rule
#: rather than a new one: only the document family can act on a row cap, and the
#: other two ``del`` it beside ``asset_namespace``. Nothing about the call changes
#: when a family cannot use an argument.


def _extract_csv_capped(source: Path, max_rows: int) -> MarkdownDoc:
    """Read a CSV with the caller's row cap instead of the engine's default."""
    from convilyn.local._engine.markdown.csv_ import extract

    return extract(source, max_rows=max_rows)


#: Sources that honour a caller-supplied row cap, and how one is applied.
#:
#: A row cap is only meaningful for a source whose model IS rows — a PDF has no
#: such axis — so this is the set of formats ``max_rows`` means anything for.
#: ``api`` reads its KEYS to refuse the option on any other source, and this
#: module reads its VALUES to apply it. One table, because they are one fact:
#: split into a set here and a dispatch there, the two drift, and the failure
#: that produces is a format admitted for capping that nothing knows how to cap.
#:
#: **Not routed through ``extractor_for``, and that is a constraint rather than a
#: preference.** The registry's ``Extractor`` contract is ``(Path) -> MarkdownDoc``
#: and cannot carry a cap. Widening it means regenerating ``markdown/registry.py``,
#: which is generated rather than written — and regenerating it today also adds an
#: HTML route whose extractor is not part of this tree, which ``_native_sources``
#: would then advertise and every caller of it would hit ``ModuleNotFoundError``.
#: Once that extractor ships, this table folds back into the registry and ``api``
#: keeps reading its keys.
ROW_CAPPED_EXTRACTORS: dict[DocumentFormat, Callable[[Path, int], MarkdownDoc]] = {
    DocumentFormat.CSV: _extract_csv_capped,
}


def run_document(
    source: Path, route: Route, output: Path, *, asset_namespace: str, max_rows: int | None
) -> tuple[tuple[str, ...], Path]:
    """Extract a document, render Markdown, and write it — assets included.

    Images are written beside the Markdown under ``assets/`` because that is where
    ``render`` points its links. Writing the ``.md`` alone would produce a document
    whose every image is a broken link, which is the failure the structure-aware
    engine exists to avoid — so the write and the assets belong to the same step
    and are not separable.

    ``asset_namespace`` is a sub-directory under ``assets/`` for this document's
    images, or empty to write them straight into it. It is not a preference. The
    engine names assets per document — ``img-0001.png`` — which is unambiguous
    inside one document and collides the moment two are rendered into the same
    directory: the second write wins, silently, and the loser's Markdown is left
    linking to a file that exists and shows another document's picture. A batch
    passes the source's stem; a single conversion passes nothing, because it shares
    that directory with no one.

    ``max_rows`` is the caller's row cap, or ``None`` to use the engine's default.
    It can only arrive non-``None`` for a source in :data:`ROW_CAPPED_EXTRACTORS`,
    because ``api._convert_one`` refuses it for every other one — so the lookup
    below cannot miss, and a source that reached here uncapped genuinely has no
    rows to cap.
    """
    source_format = DocumentFormat(route.source_format)
    extractor = extractor_for(source_format)
    if max_rows is not None:
        doc = ROW_CAPPED_EXTRACTORS[source_format](source, max_rows)
    elif extractor is not None:
        doc = extractor(source)
    else:
        # No native extractor: convert once into the modern sibling this engine
        # already reads, then run that sibling's extractor. `capabilities()` has
        # already confirmed the external program is present, so reaching here
        # without it is a race, not a routing mistake — and it raises.
        doc = extract_via_bridge(source, source_format)
    if asset_namespace:
        doc = _under_namespace(doc, asset_namespace)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(doc), encoding="utf-8", newline="\n")

    assets = output.parent / ASSET_DIR
    for image in doc.unique_images:
        destination = assets / image.asset_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image.data)

    return doc.warnings, output


def _under_namespace(doc: MarkdownDoc, namespace: str) -> MarkdownDoc:
    """A copy of ``doc`` whose asset names sit under ``namespace/``.

    The namespace is applied to the asset **name**, not to the write path, and that
    is what makes it safe: ``render`` builds every link out of ``asset_name`` and
    this module writes the bytes to that same string, so the namespace enters in
    exactly one place and the link cannot disagree with the file. Prefixing the
    directory at the write site instead would have moved the images and left every
    link pointing where they used to be — trading a wrong picture for a missing one.

    Rebuilt rather than mutated: the model is frozen throughout, and the document
    the extractor produced is left as it was.
    """
    blocks = tuple(
        replace(
            block,
            image=replace(block.image, asset_name=f"{namespace}/{block.image.asset_name}"),
        )
        if block.kind == "image" and block.image is not None
        else block
        for block in doc.blocks
    )
    return replace(doc, blocks=blocks)


def run_image(
    source: Path, route: Route, output: Path, *, asset_namespace: str, max_rows: int | None
) -> tuple[tuple[str, ...], Path]:
    """Convert one image: check the budget, open, normalise, save.

    The pixel budget is checked from the header **before** the image is decoded — a
    file can declare itself tens of thousands of pixels square and exhaust memory
    during decode, so a check on the decoded result would run too late to help.

    ``asset_namespace`` and ``max_rows`` are accepted and unused: this family writes
    one file, at the path it was handed, and has no companions to place anywhere —
    nor any rows to cap.
    """
    del asset_namespace, max_rows

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


def run_media(
    source: Path, route: Route, output: Path, *, asset_namespace: str, max_rows: int | None
) -> tuple[tuple[str, ...], Path]:
    """Re-encode one media file by handing a decided argument list to ffmpeg.

    The two other families do their work in this process; this one delegates it
    to a program, so the function is short by construction. What it must not do
    is *decide* anything — which codecs, which options, whether the result
    counts — because those answers are shared with the platform and live in the
    generated engine.

    A zero exit status is not evidence of output, which is why the size is read
    and passed to the adjudicator rather than compared here.

    ``asset_namespace`` and ``max_rows`` are accepted and unused, for the image
    family's reason: one input, one output file, no companions, no rows.
    """
    del asset_namespace, max_rows

    from convilyn.local._engine.media import convert_core
    from convilyn.local._engine.media.formats import MediaFormat
    from convilyn.local._run import run_ffmpeg

    target = MediaFormat(route.target_format)
    output.parent.mkdir(parents=True, exist_ok=True)

    returncode, _stdout, stderr = run_ffmpeg(
        convert_core.build_transcode_argv(str(source), str(output), target, "standard")
    )
    produced = output.stat().st_size if output.exists() else 0

    failure = convert_core.adjudicate_transcode(returncode, stderr, produced)
    if failure is not None:
        # Remove the stub ffmpeg may have created, so a failed run does not
        # leave a zero-byte file where the user expects their conversion.
        output.unlink(missing_ok=True)
        raise RuntimeError(failure)

    return (), output


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
    "media": run_media,
}

__all__ = [
    "ROW_CAPPED_EXTRACTORS",
    "RUNNERS",
    "Runner",
    "run_document",
    "run_image",
    "run_media",
]

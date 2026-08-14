"""Rearrange PDFs on this machine — merge, split, rotate, protect.

```python
from convilyn.local import pdf

pdf.merge(["a.pdf", "b.pdf"], "combined.pdf")
pdf.select("report.pdf", "summary.pdf", pages="1-3,10")
pdf.rotate("scan.pdf", "upright.pdf", degrees=270)
```

A separate namespace from :func:`convilyn.local.convert`, because these are not
conversions. Nothing here changes the format: a PDF goes in and a PDF comes
out, rearranged. Folding them into ``convert`` would have needed a target
format that does not exist and a page range that means nothing to any other
route.

Everything needs the ``pdf`` extra (``uv add "convilyn[pdf]"``). A missing one
is reported as :class:`~convilyn.local.errors.MissingDependencyError` naming
the command, not as an ``ImportError`` from three frames down.

Page numbers are 1-based, as printed: ``pages="1-3,10"`` means what it looks
like. Every function writes to the path you name and leaves the source
untouched, so an operation can never consume its own input.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from convilyn.local._probe import package_available
from convilyn.local.errors import MissingDependencyError, PdfOperationError
from convilyn.local.types import Requirement

_EXTRA = "pdf"
_IMPORT_NAME = "pypdf"


def _require_pypdf() -> None:
    """Fail with an install command rather than an ImportError from inside.

    Checked by resolving the module without importing it, so the cost of asking
    is the same whether or not the package is there.
    """
    if package_available(_IMPORT_NAME):
        return

    hint = f'uv add "convilyn[{_EXTRA}]" (or pip install "convilyn[{_EXTRA}]")'
    raise MissingDependencyError(
        route=None,
        requirement=Requirement(
            kind="python_package",
            name=_IMPORT_NAME,
            available=False,
            install_hint=hint,
            extra=_EXTRA,
        ),
        message=(
            f"Working with PDF pages needs {_IMPORT_NAME}, which is not installed. "
            f"Add it with {hint}."
        ),
    )


def _operations():  # noqa: ANN202 — the module type is not part of any signature
    _require_pypdf()
    from convilyn.local._engine.pdf import operations

    return operations


def _translate(error: Exception) -> PdfOperationError:
    """Re-raise an engine refusal in this namespace's taxonomy.

    The generated engine raises its own error class, which is not under
    :class:`~convilyn.local.errors.LocalError` — so a caller who wrote one
    ``except`` around the public surface would miss it. Translating here keeps
    the promise ``docs/STABILITY.md`` makes about the error taxonomy without
    the engine having to know it exists.
    """
    return PdfOperationError(str(error))


# ── Reading ──────────────────────────────────────────────────────────


def page_count(source: str | Path) -> int:
    """How many pages the document has."""
    operations = _operations()
    try:
        return operations.page_count(Path(source))
    except operations.PdfOperationError as exc:
        raise _translate(exc) from exc


def extract_text(source: str | Path, *, pages: str | None = None) -> str:
    """Read the text layer, with a blank line between pages.

    Returns ``""`` for a scanned document, which has no text layer. That is the
    honest answer and not a failure — recovering glyphs from an image is OCR,
    which this does not attempt and which the platform meters separately.
    """
    operations = _operations()
    try:
        return operations.extract_text(Path(source), pages=pages)
    except operations.PdfOperationError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise _translate(exc) from exc


# ── Rearranging ──────────────────────────────────────────────────────


def select(source: str | Path, output: str | Path, *, pages: str | None = None) -> Path:
    """Write the chosen pages into one new PDF.

    ``pages`` accepts single pages, inclusive ranges, and both together:
    ``"1"``, ``"1-5"``, ``"1-3,7,10-12"``. Without it, every page is copied.

    A range that runs past the end selects what exists, so ``"1-100"`` on a
    shorter document is a fine way to say "the rest". A selection matching no
    page at all is an error.
    """
    return _run("select", Path(source), Path(output), pages=pages)


def merge(sources: Iterable[str | Path], output: str | Path) -> Path:
    """Concatenate several PDFs into one, in the order given."""
    operations = _operations()
    paths: Sequence[Path] = [Path(s) for s in sources]
    try:
        return operations.merge(paths, Path(output))
    except operations.PdfOperationError as exc:
        raise _translate(exc) from exc


def rotate(
    source: str | Path,
    output: str | Path,
    *,
    degrees: int = 90,
    pages: str | None = None,
) -> Path:
    """Rotate the chosen pages clockwise, copying the rest unchanged.

    ``degrees`` must be a quarter turn. Other angles are refused rather than
    written: the underlying library accepts them and most viewers render the
    result incorrectly, which is a bug report a week later instead of an error
    now.
    """
    return _run("rotate", Path(source), Path(output), degrees=degrees, pages=pages)


def compress(source: str | Path, output: str | Path) -> Path:
    """Recompress content streams losslessly.

    Text and vector artwork shrink; a scan does not, because its size is in the
    embedded images and this touches only the streams describing the page.
    Nothing is re-encoded, so the output renders identically to the input.
    """
    return _run("compress", Path(source), Path(output))


def encrypt(source: str | Path, output: str | Path, *, password: str) -> Path:
    """Write a copy that requires ``password`` to open."""
    return _run("encrypt", Path(source), Path(output), password=password)


def decrypt(source: str | Path, output: str | Path, *, password: str) -> Path:
    """Write a copy with the password removed.

    A document that is not encrypted is copied rather than refused — the caller
    asked for an unprotected file and that is what they get. A wrong password
    is an error.
    """
    return _run("decrypt", Path(source), Path(output), password=password)


def burst(source: str | Path, out_dir: str | Path, *, pages: str | None = None) -> tuple[Path, ...]:
    """Write each chosen page as its own PDF inside ``out_dir``.

    Names are zero-padded (``page_001.pdf``) so a directory listing sorts into
    page order. The directory is created if it does not exist.

    Existing files are overwritten. That differs from :func:`convilyn.local.convert`,
    which refuses without ``overwrite=True``, and the difference is deliberate:
    the names here are generated rather than chosen, so a collision means a
    previous burst of the same document, not somebody's unrelated file.
    """
    operations = _operations()
    directory = Path(out_dir)
    try:
        produced = operations.burst(Path(source), pages=pages)
    except operations.PdfOperationError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise _translate(exc) from exc

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in produced:
        path = directory / name
        path.write_bytes(content)
        written.append(path)
    return tuple(written)


def _run(name: str, source: Path, output: Path, **kwargs: object) -> Path:
    """Call one engine operation, translating both refusal shapes it can raise.

    Written once rather than per function: each of these differs only in which
    name it calls and which keywords it forwards, and six copies of the same
    two ``except`` clauses is six places for one of them to be forgotten.
    """
    operations = _operations()
    try:
        return getattr(operations, name)(source, output, **kwargs)
    except operations.PdfOperationError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        # A malformed page range. Raised by the parser as ValueError because it
        # is bad input rather than a document problem; a caller of this
        # namespace should still only have to catch LocalError.
        raise _translate(exc) from exc


__all__ = [
    "burst",
    "compress",
    "decrypt",
    "encrypt",
    "extract_text",
    "merge",
    "page_count",
    "rotate",
    "select",
]

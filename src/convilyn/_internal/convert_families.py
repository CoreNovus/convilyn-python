"""Which free conversion family a request belongs to, and how it is spelled on the wire.

`POST /api/v1/jobs` takes a discriminated union, and the three conversions that
run on the free deterministic lane are three of its members. They do not share a
request shape: only `document_conversion` names its input `source_file_id` and
declares a `source_format`, and only it takes a `str` quality. The other two use
`file_id` / `output_format`, and the image one takes an integer quality.

So `convert` has two questions to answer before it can build a request — which
family, and what that family's params are called — and this module answers both
from tables rather than from a chain of `if`s.

## Picking the family

**The family is the one that contains BOTH the source and the target format.**

That is a lookup, and its answer is unique. Two families would both qualify only
if the source and the target were both inside the same overlap between two
vocabularies, and every overlap holds at most one format — `pdf` between
document and image, `gif` between image and media — so that requires
``source == target``, which is not a conversion and is refused before the
lookup runs. ``project_convert_formats.py`` re-derives the overlaps from the
backend on every run and refuses to emit a table that breaks the precondition.

The alternative — reading the family off the source alone — cannot work, and
`.gif` is why: a GIF is an image when the target is `png` and a video when the
target is `mp4`.

## What this module deliberately does not know

Whether a pair is actually PRODUCIBLE. `mp4 → mp3` and `gif → mp4` are both
media pairs; only one of them is advertised. That matrix is the backend's
(`services/conversion/capability_matrix.py`, published at
`GET /api/v1/{document,image,media}/support` and enforced at job admission), and
a copy of it here would be a second list that drifts — the exact defect the
projected format tables exist to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from convilyn._internal.convert_formats import (
    DOCUMENT_FORMATS,
    IMAGE_FORMAT_ALIASES,
    IMAGE_FORMATS,
    MEDIA_FORMATS,
)

# The offline namespace already owns the name for "there is no conversion from A
# to B", and this is the same question asked of the hosted lane, so it is reused
# rather than duplicated under a second name.
#
# It reaches `convilyn.local`, which `convilyn/__init__` deliberately does not
# import — measured before taking it: +6 ms and +21 modules on top of
# `import convilyn`. The engine's parsers are all imported lazily inside their
# functions, so what arrives here is the small hand-written layer, not Pillow or
# pdfplumber. Worth that to keep one name for one concept.
from convilyn.local.errors import UnsupportedRouteError

#: Spellings the backend enums do not carry, resolved before anything looks a
#: format up. Every entry needs its own reason, or it is just a hole — the same
#: discipline the backend's own hand-written MIME widenings are held to.
#:
#: * ``htm`` — ``DocumentFormat`` has only ``html``, and the document worker
#:   builds the enum bare (``DocumentFormat(source_format)``), so an uploaded
#:   ``report.htm`` would fail inside the worker rather than at the door.
#:
#: Image aliases (``jpeg``, ``tif``, ``heif``, …) are NOT listed here. They come
#: from the projected ``IMAGE_FORMAT_ALIASES``, which upstream calls the single
#: source of truth for image alias resolution; a second one would be a second
#: answer.
_SPELLING_ALIASES: dict[str, str] = {
    "htm": "html",
}


@dataclass(frozen=True)
class ConversionFamily:
    """One member of the free lane's slice of the `POST /jobs` union."""

    #: How this SDK names the family in errors.
    name: str
    #: The `processor_type` discriminator the backend routes on.
    processor_type: str
    #: Every format spelling this family speaks (projected from the backend).
    formats: frozenset[str]
    #: What the family calls the input file.
    file_id_key: str
    #: What the family calls the target format.
    target_key: str
    #: Whether the request declares its source format. Only document does; for
    #: the other two the backend derives it from the stored object's key, so
    #: sending one would be a field the union does not define.
    declares_source: bool
    #: Whether `quality` is a 1-100 integer rather than a preset name.
    quality_is_numeric: bool
    #: Whether `page_range` is part of this family's params.
    supports_page_range: bool


#: The three free (Class-L) conversions, in the order errors list them.
#:
#: Metered processors — OCR, video/audio transcription — are deliberately
#: absent. `convert` is the verb that does not spend credits, and a verb that
#: could silently route to a billed processor would defeat the split.
FAMILIES: tuple[ConversionFamily, ...] = (
    ConversionFamily(
        name="document",
        processor_type="document_conversion",
        formats=DOCUMENT_FORMATS,
        file_id_key="source_file_id",
        target_key="target_format",
        declares_source=True,
        quality_is_numeric=False,
        supports_page_range=True,
    ),
    ConversionFamily(
        name="image",
        processor_type="image_conversion",
        formats=IMAGE_FORMATS,
        file_id_key="file_id",
        target_key="output_format",
        declares_source=False,
        quality_is_numeric=True,
        supports_page_range=False,
    ),
    ConversionFamily(
        name="media",
        processor_type="media_processing",
        formats=MEDIA_FORMATS,
        file_id_key="file_id",
        target_key="output_format",
        declares_source=False,
        quality_is_numeric=False,
        supports_page_range=False,
    ),
)

_SUPPORT_ENDPOINTS = "GET /api/v1/{document,image,media}/support"


def normalise_format(raw: str) -> str:
    """A format spelling as the backend workers expect to read it.

    Lower-cased, leading dot removed, aliases resolved. The workers construct
    their enums bare — `DocumentFormat(x)`, `MediaFormat(x)` — so `'.PDF'` and
    `'htm'` are not tolerated there; normalising here is what keeps a perfectly
    ordinary filename from failing deep inside a worker.
    """
    token = raw.strip().lower().lstrip(".")
    return IMAGE_FORMAT_ALIASES.get(token) or _SPELLING_ALIASES.get(token) or token


def detect_format(filename: str) -> str | None:
    """The format a filename names, or ``None`` when it carries no extension.

    An extension this SDK does not recognise is returned as-is rather than
    swallowed: `resolve_family` then reports which side of the pair is the
    unknown one, which is a better answer than "could not infer".
    """
    suffix = Path(filename).suffix
    return normalise_format(suffix) if suffix.strip(". ") else None


def families_speaking(format_token: str) -> tuple[ConversionFamily, ...]:
    """Every family whose vocabulary contains *format_token*."""
    return tuple(family for family in FAMILIES if format_token in family.formats)


def resolve_family(source_format: str, target_format: str) -> ConversionFamily:
    """The family that speaks both formats.

    Raises:
        UnsupportedRouteError: the pair names no family, names two, or is not a
            conversion at all. A ``ConvilynError``, because "this conversion is
            not one we perform" is a domain answer and the documented promise is
            that one ``except ConvilynError`` covers the flow. Argument mistakes
            in this module stay ``ValueError`` — see the note at ``build_payload``.
    """
    if source_format == target_format:
        raise UnsupportedRouteError(
            source_format=source_format,
            target_format=target_format,
            reason=(
                f"source and target are both {source_format!r} — that is not a conversion. "
                "Pass a different target format."
            ),
        )

    matches = [
        family
        for family in FAMILIES
        if source_format in family.formats and target_format in family.formats
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise UnsupportedRouteError(
            source_format=source_format,
            target_format=target_format,
            reason=(
                f"{source_format!r} → {target_format!r} is claimed by more than one conversion "
                f"family ({', '.join(f.name for f in matches)}). The projected format tables were "
                "supposed to make that impossible; re-run scripts/oss/project_convert_formats.py."
            ),
        )
    raise UnsupportedRouteError(
        source_format=source_format,
        target_format=target_format,
        reason=_no_family_message(source_format, target_format),
    )


def _no_family_message(source_format: str, target_format: str) -> str:
    unknown = [token for token in (source_format, target_format) if not families_speaking(token)]
    if unknown:
        named = " or ".join(repr(token) for token in unknown)
        # The hint only fits when it is the SOURCE that could not be placed —
        # telling somebody who already passed source_format to pass it is noise.
        hint = (
            " If the filename's extension does not name the format, pass source_format."
            if source_format in unknown
            else ""
        )
        return (
            f"{named} does not name a format `convert` knows. It handles document, image "
            f"and media conversions — see {_SUPPORT_ENDPOINTS} for what each can produce.{hint}"
        )
    return (
        f"{source_format!r} and {target_format!r} belong to different conversion families "
        f"({_families_of(source_format)} vs {_families_of(target_format)}), so there is no "
        f"single conversion between them. See {_SUPPORT_ENDPOINTS}."
    )


def _families_of(format_token: str) -> str:
    return "/".join(family.name for family in families_speaking(format_token))


def build_payload(
    *,
    file_id: str,
    source_format: str,
    target_format: str,
    quality: str | int | None = None,
    page_range: str | None = None,
) -> dict[str, Any]:
    """The complete `POST /api/v1/jobs` body for a free-lane conversion.

    One function rather than a step the caller assembles, because the CLI's
    ``--dry-run`` has to be able to show which processor a request would reach
    *without* uploading anything. A derivation that only ran inside `create()`
    could not be printed before the upload, and the printout is the whole point:
    it is how a user sees which lane they are about to use.
    """
    source = normalise_format(source_format)
    target = normalise_format(target_format)
    family = resolve_family(source, target)
    return {
        "processor_type": family.processor_type,
        "params": build_params(
            family,
            file_id=file_id,
            source_format=source,
            target_format=target,
            quality=quality,
            page_range=page_range,
        ),
    }


def build_params(
    family: ConversionFamily,
    *,
    file_id: str,
    source_format: str,
    target_format: str,
    quality: str | int | None,
    page_range: str | None,
) -> dict[str, Any]:
    """The `params` object for *family*, with only the fields it defines.

    `quality` is omitted entirely when the caller did not supply one, so each
    family's own server-side default applies. That matters beyond tidiness: the
    document default is the string ``"standard"`` and the image one is the
    integer ``80``, so a single client-side default cannot be right for both.
    """
    params: dict[str, Any] = {
        family.file_id_key: file_id,
        family.target_key: target_format,
    }
    if family.declares_source:
        params["source_format"] = source_format
    if quality is not None:
        params["quality"] = _coerce_quality(family, quality)
    if page_range is not None:
        if not family.supports_page_range:
            raise ValueError(
                f"page_range is a document-conversion parameter; {target_format!r} makes this "
                f"an {family.name} conversion, whose params do not carry one."
            )
        params["page_range"] = page_range
    return params


def _coerce_quality(family: ConversionFamily, quality: str | int) -> str | int:
    """Match `quality` to the type this family's params declare.

    Only the type, never the range: `1-100` and the preset names are the
    backend's constraint to state, and restating them here would be a second
    copy that goes stale the day either moves.
    """
    if not family.quality_is_numeric:
        return str(quality)
    try:
        return int(quality)
    except (TypeError, ValueError):
        raise ValueError(
            f"{family.name} conversions take a numeric quality, got {quality!r}. "
            "The preset names ('standard', 'high') belong to document and media conversions."
        ) from None


__all__ = [
    "FAMILIES",
    "ConversionFamily",
    "build_params",
    "build_payload",
    "detect_format",
    "families_speaking",
    "normalise_format",
    "resolve_family",
]

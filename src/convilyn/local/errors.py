"""Errors raised by offline conversion.

All of them subclass :class:`convilyn.ConvilynError`, so a caller already
handling SDK failures keeps working without knowing this namespace exists. None
of them carries an HTTP status: nothing here reaches the network, and inventing
a status code for a local file would be describing a request that never happened.

**One exception is not offline-only.** :class:`UnsupportedRouteError` is raised by
``client.convert`` as well, because "there is no conversion from A to B" is one
question and deserves one name. So catching :class:`LocalError` now also catches
that refusal from the hosted lane. That is a widening, never a narrowing: an
``except`` that worked before still catches everything it did.

The messages are not composed here. A route already knows why it cannot run —
``Route.unavailable_reason`` — and that sentence is written once, in
:mod:`convilyn.local._routes`, so the string a user sees from
``capabilities()``, from a raised error, and from the CLI is the same string.
"""

from __future__ import annotations

from convilyn.exceptions import ConvilynError
from convilyn.local.types import Requirement, Route


class LocalError(ConvilynError):
    """Base class for every offline-conversion failure.

    And for :class:`UnsupportedRouteError`, which the hosted lane raises too —
    see the module docstring for why that one is shared rather than duplicated.
    """


class UnsupportedRouteError(LocalError):
    """``source_format`` will not convert to ``target_format`` here.

    Raised by both halves of the package, because it is one question:

    * offline — this engine has no such route, or cannot run it on this machine;
    * hosted — the two formats name no single conversion the platform performs
      (``txt`` and ``mp3`` are in different families), which is refused before
      the upload so an impossible request costs nothing.

    Distinct from :class:`MissingDependencyError` by what the caller can do
    about it: that one names an **extra of this package** and is fixed by
    installing it, so a program can offer the command. This one covers
    everything else, and ``reason`` says whether anything changes the answer —
    a third-party Pillow plugin sometimes does, a format Pillow only reads
    never does.

    The distinction is drawn on "is it one of our extras", not on "is it
    fixable", because the first is a question the SDK can answer for a caller
    and the second is not.

    ``supported_targets`` is populated by the offline engine, which knows the
    whole matrix; the hosted lane leaves it empty and points at
    ``GET /api/v1/{document,image,media}/support`` in ``reason`` instead, because
    that answer is the server's and a copy here would go stale.
    """

    def __init__(
        self,
        *,
        source_format: str,
        target_format: str,
        supported_targets: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> None:
        self.source_format = source_format
        self.target_format = target_format
        self.supported_targets = supported_targets
        self.reason = reason

        # A reason from the route wins outright. It was authored against the
        # actual cause — a codec, a plugin, a read-only format — and anything
        # composed here would be a vaguer second opinion about the same fact.
        if reason:
            super().__init__(reason)
            return

        if supported_targets:
            options = ", ".join(supported_targets)
            tail = f" From {source_format} this engine can produce: {options}."
        else:
            tail = f" This engine cannot read {source_format} at all."
        super().__init__(f"No route from {source_format} to {target_format}.{tail}")


class MissingDependencyError(LocalError):
    """Something this operation needs is not installed here.

    When it came from a route, the message **is** ``route.unavailable_reason``
    verbatim, so the sentence a user reads is the same one ``capabilities()``
    would have shown them beforehand. Callers that want to act on the cause
    rather than print it read :pyattr:`requirement`.

    ``route`` is ``None`` for an operation that has no route — the page
    operations in :mod:`convilyn.local.pdf` are conversions from nothing to
    nothing, and inventing a route to describe a missing package would put a
    fictional one in the attribute a caller is meant to trust.
    """

    def __init__(
        self,
        *,
        requirement: Requirement,
        route: Route | None = None,
        message: str | None = None,
    ) -> None:
        self.route = route
        self.requirement = requirement
        reason = route.unavailable_reason if route else None
        super().__init__(message or reason or requirement.install_hint)


class ConversionFailedError(LocalError):
    """The route ran and did not produce output.

    A malformed source, an unreadable file, an extractor raising on content it
    could not parse. Distinct from the two above, which are both answered before
    any work begins.
    """

    def __init__(self, *, source: str, route: Route, message: str) -> None:
        self.source = source
        self.route = route
        self.message = message
        super().__init__(
            f"Converting {source} ({route.source_format} → {route.target_format}) failed: {message}"
        )


class PdfOperationError(LocalError):
    """A page operation was refused, or could not be performed.

    Its own class rather than :class:`ConversionFailedError` because a page
    operation has no route: merging two documents or removing a password is not
    a conversion from one format to another, and a caller catching it wants to
    say "that PDF could not be rearranged", not "that conversion failed".

    Under :class:`LocalError` like everything else, so a caller who only wants
    one ``except`` still gets one.
    """


__all__ = [
    "ConversionFailedError",
    "LocalError",
    "MissingDependencyError",
    "PdfOperationError",
    "UnsupportedRouteError",
]

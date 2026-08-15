"""``Route``'s fields mean the same thing on every engine.

This is the falsifiable form of the property `Route`'s own docstring claims. It
exists because the property did NOT hold: `available=False` meant "install
something" on a document route and could mean "impossible on this build, forever"
on an image route, so a consumer had to switch on `engine` before it could read
`available`. That is a subtype carrying semantics its base does not — and the cost
was concrete, a `KeyError: 'PSD'` that reached a user wrapped in "conversion
failed" because the caller could not tell the two apart.

`unavailable_kind` now carries that distinction explicitly. These tests assert it
holds across **every** route, on a machine with the extras and on one without,
which is the only way both arms are ever executed: this suite runs with the extras
installed, so the "fresh install" half is unreachable without injected probes.

The vacuity guards are not ceremony. An invariant quantified over routes is
trivially true if the route set is empty, and the three-kinds guard is what stops
this file from passing while only ever having seen one of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from convilyn.local._routes import build_routes
from convilyn.local.types import Engine, Route, UnavailableKind

_KINDS = frozenset(get_args(UnavailableKind))

#: The two machines every route must be coherent on: a bare install, and one with
#: every extra and every external program. Named so a failure says which.
_MACHINES = {
    "nothing-installed": (lambda _name: False, lambda _tool: None),
    "everything-installed": (lambda _name: True, lambda _tool: Path("/usr/bin/stub")),
}


def _routes(machine: str) -> tuple[Route, ...]:
    probe, find = _MACHINES[machine]
    return build_routes(probe=probe, find=find)


@pytest.fixture(scope="module")
def every_route() -> tuple[Route, ...]:
    """Every route from both machines, in one flat tuple.

    Both, deliberately: `unsupported_by_build` only ever appears when Pillow IS
    importable (a codec is missing from a library that is present), and
    `missing_requirement` on the image side only when it is not. A single machine
    cannot exhibit the whole taxonomy.
    """
    return tuple(route for machine in _MACHINES for route in _routes(machine))


# ── 0. Vacuity guards — these run first on purpose ───────────────────


class TestTheScanIsNotVacuous:
    def test_both_machines_produce_routes(self) -> None:
        for machine in _MACHINES:
            assert _routes(machine), f"{machine} produced no routes at all"

    def test_every_kind_is_actually_observed(self, every_route: tuple[Route, ...]) -> None:
        """Otherwise the invariants below are asserted over a taxonomy of one.

        `png -> psd` supplies `unsupported_by_build` (Pillow reads PSD and, by
        design, does not write it), `heic -> png` supplies `missing_plugin`, and a
        bare install supplies `missing_requirement`. If a future Pillow starts
        writing PSD this test fails rather than the invariant quietly narrowing —
        which is the correct outcome: the guard is what tells us the coverage
        moved.
        """
        observed = {r.unavailable_kind for r in every_route if r.unavailable_kind is not None}

        assert observed == _KINDS, f"kinds never exercised: {sorted(_KINDS - observed)}"

    def test_the_kind_is_declared_for_every_engine_family(
        self, every_route: tuple[Route, ...]
    ) -> None:
        """Every engine must appear, or "uniform across engines" is untested."""
        assert {r.engine for r in every_route} == set(get_args(Engine))


# ── 1. Logic — the invariants, over every route on both machines ──────


class TestAvailabilityAndKindAgree:
    def test_available_routes_carry_no_kind_and_no_reason(
        self, every_route: tuple[Route, ...]
    ) -> None:
        offenders = [
            (r.source_format, r.target_format, r.unavailable_kind, r.unavailable_reason)
            for r in every_route
            if r.available and (r.unavailable_kind is not None or r.unavailable_reason is not None)
        ]

        assert offenders == [], f"available routes explaining why they are not: {offenders}"

    def test_unavailable_routes_carry_both_a_kind_and_a_reason(
        self, every_route: tuple[Route, ...]
    ) -> None:
        """A kind without prose is unreadable; prose without a kind is unactionable."""
        offenders = [
            (r.engine, r.source_format, r.target_format)
            for r in every_route
            if not r.available and (r.unavailable_kind is None or not r.unavailable_reason)
        ]

        assert offenders == [], f"unavailable routes missing a kind or a reason: {offenders}"


class TestKindAgreesWithMissing:
    def test_missing_requirement_always_names_what_is_missing(
        self, every_route: tuple[Route, ...]
    ) -> None:
        """The kind promises `missing` is worth reading. Hold it to that.

        Without this, a caller told "missing_requirement" could find `missing`
        empty and have nothing to install — which is precisely the ambiguity on
        the image side that this whole field replaced.
        """
        offenders = [
            (r.engine, r.source_format, r.target_format)
            for r in every_route
            if r.unavailable_kind == "missing_requirement" and r.missing == ()
        ]

        assert offenders == [], f"missing_requirement with nothing missing: {offenders}"

    @pytest.mark.parametrize("kind", ["missing_plugin", "unsupported_by_build"])
    def test_the_other_kinds_mean_everything_declared_is_satisfied(
        self, kind: str, every_route: tuple[Route, ...]
    ) -> None:
        """Both say "the gap is not in anything we declared", so `missing` is empty.

        This is the converse of the test above, and together they make `missing`
        readable without consulting `engine`: non-empty exactly when the kind is
        `missing_requirement`.
        """
        offenders = [
            (r.engine, r.source_format, r.target_format, [m.name for m in r.missing])
            for r in every_route
            if r.unavailable_kind == kind and r.missing != ()
        ]

        assert offenders == [], f"{kind} with unsatisfied declared requirements: {offenders}"


class TestFixabilityNeedsNoEngine:
    """The acceptance criterion, as one expression a consumer can copy."""

    def test_one_comparison_separates_fixable_from_hopeless(
        self, every_route: tuple[Route, ...]
    ) -> None:
        blocked = [r for r in every_route if not r.available]
        fixable = [r for r in blocked if r.unavailable_kind != "unsupported_by_build"]
        hopeless = [r for r in blocked if r.unavailable_kind == "unsupported_by_build"]

        assert fixable and hopeless, "both sides must be non-empty for this to mean anything"
        assert len(fixable) + len(hopeless) == len(blocked)

    def test_a_hopeless_route_never_offers_an_install_command(
        self, every_route: tuple[Route, ...]
    ) -> None:
        """Naming a package that cannot change the answer is worse than the limit.

        Asserted on the prose because that is what the user reads, and the kind is
        what a program reads — the two must not disagree about whether a fix exists.
        """

        def offers_an_install(route: Route) -> bool:
            reason = route.unavailable_reason or ""
            return "pip install" in reason or "uv add" in reason

        offenders = [
            (r.source_format, r.target_format, r.unavailable_reason)
            for r in every_route
            if r.unavailable_kind == "unsupported_by_build" and offers_an_install(r)
        ]

        assert offenders == [], f"unsupported_by_build offering an install: {offenders}"


# ── 2. Object state — the frozen model still refuses mutation ─────────


class TestTheFieldIsPartOfTheFrozenContract:
    def test_unavailable_kind_cannot_be_mutated(self) -> None:
        route = _routes("everything-installed")[0]

        with pytest.raises(Exception):  # noqa: B017 — pydantic's own frozen error
            route.unavailable_kind = "unsupported_by_build"  # type: ignore[misc]

    def test_an_unknown_kind_is_rejected_at_construction(self) -> None:
        """The Literal is enforced, so a typo cannot reach a consumer as data."""
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            Route(
                source_format="png",
                target_format="webp",
                engine="image",
                available=False,
                unavailable_kind="probably_fixable",  # type: ignore[arg-type]
            )

"""``convilyn.local._routes`` — logic / boundary / error / object-state.

Every test here injects its probes. That is not stylistic: this suite runs with
the extras installed, so the "missing dependency" half of every route — the half
users on a fresh install actually meet — would never execute against the real
finder. Injection is the only way both arms are ever run.
"""

from __future__ import annotations

from pathlib import Path

from convilyn.local._routes import build_capabilities, build_routes

_NOTHING_INSTALLED = (lambda _name: False, lambda _tool: None)
_EVERYTHING_INSTALLED = (lambda _name: True, lambda _tool: Path("/usr/bin/stub"))


def _caps(installed: bool):
    probe, find = _EVERYTHING_INSTALLED if installed else _NOTHING_INSTALLED
    return build_capabilities(probe=probe, find=find)


# ── 1. Logic — what is reachable with and without dependencies ───────


class TestRouteLogic:
    def test_stdlib_only_formats_need_nothing(self) -> None:
        """The claim the docs make on the front page, asserted."""
        caps = _caps(installed=False)

        for source in ("txt", "csv"):
            assert caps.can(source, "md").available is True, source

    def test_every_document_route_is_available_when_everything_is_installed(self) -> None:
        """Scoped to documents, and the scope is the point.

        A document route's requirements are all installable, so satisfying the
        probes must satisfy the route. An image route's are not: stubbing the
        package probe says Pillow is importable, which is true and does not put
        a HEIC codec inside it. Asserting universal availability here would
        demand the route table lie about the second kind.
        """
        routes = build_routes(probe=lambda _n: True, find=lambda _t: Path("/usr/bin/stub"))
        # Named engines, not "everything except image". The negative form was
        # equivalent while there were two families and silently swallowed the
        # third: media routes joined "documents" the moment they existed, and
        # the guard below went on reporting a healthy filter.
        documents = [r for r in routes if r.engine in {"structured", "office-suite", "ebook"}]

        assert documents, "no document routes — the filter outlived the engines"
        assert [r.source_format for r in documents if not r.available] == []

    def test_a_parser_backed_format_is_unavailable_without_its_package(self) -> None:
        assert _caps(installed=False).can("docx", "md").available is False

    def test_a_bridged_format_needs_its_external_tool(self) -> None:
        """Packages alone are not enough for odt — the office suite runs first."""
        caps = build_capabilities(probe=lambda _n: True, find=lambda _t: None)

        assert caps.can("odt", "md").available is False


# ── 2. Boundary — the sentence a stuck user reads ────────────────────


class TestUnavailableReason:
    def test_an_available_route_has_no_reason(self) -> None:
        assert _caps(installed=True).can("pdf", "md").unavailable_reason is None

    def test_a_missing_package_reason_names_its_extra(self) -> None:
        reason = _caps(installed=False).can("xlsx", "md").unavailable_reason

        assert "convilyn[xlsx]" in reason

    def test_a_bridged_reason_leads_with_the_external_tool(self) -> None:
        """Order is load-bearing.

        A bridged format needs the office suite BEFORE the sibling's extractor
        is reached, so leading with the package would send the reader to install
        an extra that leaves them exactly as stuck.
        """
        reason = _caps(installed=False).can("odt", "md").unavailable_reason

        assert reason.index("LibreOffice") < reason.index("convilyn[docx]")

    def test_a_tool_reason_offers_something_that_needs_no_tool(self) -> None:
        reason = _caps(installed=False).can("epub", "md").unavailable_reason

        assert "Formats that need no external program" in reason
        assert "txt" in reason

    def test_a_tool_reason_never_promises_an_extra_can_supply_it(self) -> None:
        """No extra installs Calibre; suggesting one would be a dead end."""
        route = _caps(installed=False).can("epub", "md")
        tool = next(r for r in route.requirements if r.kind == "external_tool")

        assert tool.extra is None


# ── 3. Error — optional requirements degrade, they do not block ──────


class TestOptionalRequirements:
    def test_a_missing_optional_package_leaves_the_route_available(self) -> None:
        """Pillow improves image handling; its absence is not a failure."""
        caps = build_capabilities(
            probe=lambda name: name != "PIL", find=lambda _t: Path("/usr/bin/stub")
        )

        assert caps.can("docx", "md").available is True

    def test_the_absent_optional_is_still_reported(self) -> None:
        caps = build_capabilities(
            probe=lambda name: name != "PIL", find=lambda _t: Path("/usr/bin/stub")
        )
        route = caps.can("docx", "md")

        assert [r.name for r in route.degraded_by] == ["PIL"]

    def test_an_optional_requirement_is_not_counted_as_missing(self) -> None:
        caps = build_capabilities(
            probe=lambda name: name != "PIL", find=lambda _t: Path("/usr/bin/stub")
        )

        assert caps.can("docx", "md").missing == ()


# ── 4. Object-state — the shape callers navigate ─────────────────────


class TestCapabilitiesShape:
    def test_can_returns_none_for_a_conversion_that_does_not_exist(self) -> None:
        """Unknown conversion and unavailable conversion are different answers."""
        assert _caps(installed=True).can("md", "docx") is None

    def test_available_targets_reflects_the_machine(self) -> None:
        assert _caps(installed=False).available_targets("docx") == ()
        assert _caps(installed=True).available_targets("docx") == ("md",)

    def test_packages_and_tools_are_listed_whether_present_or_not(self) -> None:
        caps = _caps(installed=False)

        assert {r.name for r in caps.packages} >= {"pdfplumber", "PIL", "openpyxl"}
        assert {r.name for r in caps.tools} == {"libreoffice", "calibre", "ffmpeg"}

    def test_routes_are_sorted_by_source_format(self) -> None:
        """Stable output — a CLI table that reorders between runs is unreadable."""
        sources = [r.source_format for r in _caps(installed=True).routes]

        assert sources == sorted(sources)

    def test_the_route_set_does_not_depend_on_what_is_installed(self) -> None:
        """Availability changes; the catalogue does not.

        A user must be able to discover a conversion in order to install for it,
        so hiding an unavailable route makes the missing package undiscoverable.

        Asserted as set equality rather than against a literal count, which is
        the invariant itself and not a proxy for it. The literal this replaced
        (``== 17``) had to be edited every time an engine was added — an edit
        indistinguishable from silently accepting a real regression, since both
        look like moving the number until the test passes.
        """
        installed = _caps(installed=True).routes
        bare = _caps(installed=False).routes

        assert installed, "no routes at all — the builder is the thing under test"
        assert {(r.source_format, r.target_format) for r in installed} == {
            (r.source_format, r.target_format) for r in bare
        }


class TestFixtureSanity:
    """Both probe arms must actually differ, or every test above is vacuous."""

    def test_the_two_fixtures_disagree(self, tmp_path: Path) -> None:
        assert _caps(installed=True).available_routes != _caps(installed=False).available_routes

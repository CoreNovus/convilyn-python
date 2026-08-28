"""The media family: routes, the runner, and the two overlaps it introduces.

Media is the first family whose availability turns on an external program alone,
and the first to share a source format with another engine (``gif``). Both are
tested here rather than in ``test_routes.py``, which is about the shape every
family has in common.

Nothing in this file runs ffmpeg. The invocation is one call in ``_run``; what is
worth holding by machine is the argument list, the requirement reporting, and the
route table — all of which are decided without a binary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.local._engine.media import convert_core
from convilyn.local._engine.media.formats import MediaFormat
from convilyn.local._routes import build_routes
from convilyn.local._runners import RUNNERS, run_media
from convilyn.local._tools import FFMPEG

_HAVE = {"probe": lambda _n: True, "find": lambda _t: Path("/usr/bin/stub")}
_BARE = {"probe": lambda _n: False, "find": lambda _t: None}


def _media(**machine) -> list:
    return [r for r in build_routes(**machine) if r.engine == "media"]


class TestTheOperandsAreNotEmpty:
    """Without these, every assertion below passes on an empty route set."""

    def test_the_engine_produces_pairs(self) -> None:
        assert convert_core.transcode_pairs()

    def test_the_route_builder_emits_media_routes(self) -> None:
        assert _media(**_HAVE)


class TestRoutesFollowTheEngine:
    def test_every_engine_pair_becomes_a_route(self) -> None:
        routes = {(r.source_format, r.target_format) for r in _media(**_HAVE)}
        pairs = {(s.value, t.value) for s, t in convert_core.transcode_pairs()}

        assert routes == pairs

    def test_no_route_targets_a_read_only_container(self) -> None:
        """GIF is readable and has no encoder; advertising ``*-to-gif`` is the
        defect the shared engine's target set exists to prevent."""
        assert MediaFormat.GIF.value not in {r.target_format for r in _media(**_HAVE)}


class TestAvailabilityIsTheProgram:
    def test_every_route_runs_when_ffmpeg_is_present(self) -> None:
        assert all(r.available for r in _media(**_HAVE))

    def test_no_route_runs_without_it(self) -> None:
        assert not any(r.available for r in _media(**_BARE))

    def test_the_blocked_kind_is_a_missing_requirement(self) -> None:
        """Not ``unsupported_by_build``: installing the program fixes every one
        of these, which is the distinction ``unavailable_kind`` carries."""
        assert {r.unavailable_kind for r in _media(**_BARE)} == {"missing_requirement"}

    def test_the_missing_requirement_is_named(self) -> None:
        assert [q.name for q in _media(**_BARE)[0].missing] == [FFMPEG.key]

    def test_the_requirement_is_not_installable_with_an_extra(self) -> None:
        """ffmpeg is a program, not a wheel, so offering ``pip install
        convilyn[...]`` would be offering something that cannot work."""
        requirement = _media(**_BARE)[0].requirements[0]

        assert requirement.extra is None

    def test_the_reason_offers_no_python_install_command(self) -> None:
        reason = _media(**_BARE)[0].unavailable_reason or ""

        assert "pip install" not in reason and "uv add" not in reason

    def test_the_reason_says_where_to_get_it(self) -> None:
        assert FFMPEG.download_url in (_media(**_BARE)[0].unavailable_reason or "")


class TestTheGifOverlap:
    """``gif`` is the one source format two engines both read."""

    def test_both_engines_claim_it(self) -> None:
        engines = {r.engine for r in build_routes(**_HAVE) if r.source_format == "gif"}

        assert {"image", "media"} <= engines

    def test_the_pair_decides_the_engine_not_the_source(self) -> None:
        routes = {
            (r.target_format, r.engine) for r in build_routes(**_HAVE) if r.source_format == "gif"
        }

        assert ("png", "image") in routes
        assert ("mp4", "media") in routes


class TestPairsAreUnique:
    def test_no_two_routes_share_a_source_and_target(self) -> None:
        """``Capabilities.can()`` is a linear first-match scan, so a duplicated
        pair would resolve to whichever route was appended first — and the sort
        key is ``(source, target)``, which does not break the tie. Nothing else
        would report the collision.
        """
        routes = build_routes(**_HAVE)
        pairs = [(r.source_format, r.target_format) for r in routes]

        assert len(set(pairs)) == len(pairs)


class TestTheRunner:
    def test_it_is_the_row_the_media_engine_points_at(self) -> None:
        assert RUNNERS["media"] is run_media

    def test_a_failed_encode_leaves_no_stub_behind(self, tmp_path, monkeypatch) -> None:
        """ffmpeg creates its output before it knows whether it can write it, so
        a failure without this leaves a zero-byte file where the user expects a
        conversion — which looks like a success to everything except opening it.
        """
        output = tmp_path / "out.mp4"

        def _fake_run(args, timeout=0):
            output.write_bytes(b"")
            return 1, "", "Unknown encoder"

        monkeypatch.setattr("convilyn.local._run.run_ffmpeg", _fake_run)
        route = _media(**_HAVE)[0]

        with pytest.raises(RuntimeError):
            run_media(tmp_path / "in.mov", route, output, asset_namespace="", max_rows=None)

        assert not output.exists()

    def test_a_zero_byte_result_is_a_failure_even_on_a_clean_exit(
        self, tmp_path, monkeypatch
    ) -> None:
        """The regression that shipped empty files as completed jobs upstream."""
        output = tmp_path / "out.mp4"

        def _fake_run(args, timeout=0):
            output.write_bytes(b"")
            return 0, "", "Error opening output file"

        monkeypatch.setattr("convilyn.local._run.run_ffmpeg", _fake_run)
        route = _media(**_HAVE)[0]

        with pytest.raises(RuntimeError):
            run_media(tmp_path / "in.mov", route, output, asset_namespace="", max_rows=None)

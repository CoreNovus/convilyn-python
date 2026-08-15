"""Image conversion, and the capability probe it is built on.

Every test here writes a real image and converts it. There is no mock of Pillow
anywhere in this file, deliberately: the property under test is that *this
machine's* codecs do what the route table claims, and a stubbed codec would
assert the claim against itself.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from convilyn import local
from convilyn.local import _pillow_probe
from convilyn.local.errors import ConversionFailedError, UnsupportedRouteError


@pytest.fixture
def rgba_png(tmp_path: Path) -> Path:
    """A small image with real transparency, so alpha handling is exercised."""
    path = tmp_path / "swatch.png"
    image = Image.new("RGBA", (32, 24), (200, 30, 30, 128))
    image.save(path)
    return path


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestImageConversion:
    def test_it_converts_png_to_jpg(self, rgba_png: Path, tmp_path: Path) -> None:
        result = local.convert(rgba_png, to="jpg")

        assert result.ok
        assert result.engine == "image"
        assert result.output is not None
        assert result.output.suffix == ".jpg"
        with Image.open(result.output) as written:
            assert written.format == "JPEG"

    def test_it_preserves_the_pixel_dimensions(self, rgba_png: Path, tmp_path: Path) -> None:
        result = local.convert(rgba_png, to="webp")

        assert result.output is not None
        with Image.open(result.output) as written:
            assert written.size == (32, 24)

    def test_alpha_is_flattened_for_a_format_that_cannot_carry_it(
        self, rgba_png: Path, tmp_path: Path
    ) -> None:
        """JPEG has no alpha channel, so the conversion must compose, not fail."""
        result = local.convert(rgba_png, to="jpg")

        assert result.ok
        assert result.output is not None
        with Image.open(result.output) as written:
            assert written.mode == "RGB"

    def test_alpha_survives_a_format_that_carries_it(self, rgba_png: Path, tmp_path: Path) -> None:
        result = local.convert(rgba_png, to="webp")

        assert result.output is not None
        with Image.open(result.output) as written:
            assert written.mode in {"RGBA", "P"}


# ── 2. Boundary — what this machine cannot do, and why ───────────────


class TestUnavailableImageRoutes:
    def test_a_missing_codec_is_a_route_not_an_omission(self) -> None:
        """The row exists so the user learns a plugin would fix it."""
        route = local.capabilities().can("heic", "png")

        assert route is not None, "heic vanished from the catalogue"
        assert route.available is False

    def test_the_reason_names_the_plugin_that_would_fix_it(self) -> None:
        route = local.capabilities().can("heic", "png")

        assert route is not None
        assert route.unavailable_reason is not None
        assert "pillow-heif" in route.unavailable_reason

    def test_a_plugin_fixable_route_says_so_in_machine_readable_form(self) -> None:
        """A program must reach the same conclusion the prose does.

        `missing_plugin` rather than `missing_requirement` because Pillow itself is
        installed and satisfied — the gap is a component this package deliberately
        does not ship, so `missing` is empty and would mislead anyone reading it
        for a shopping list.
        """
        route = local.capabilities().can("heic", "png")

        assert route is not None
        assert route.unavailable_kind == "missing_plugin"
        assert route.missing == ()

    def test_a_read_only_format_does_not_promise_an_install_would_help(self) -> None:
        """PSD is readable and, by Pillow's design, not writable.

        Naming a package here would send someone to install something that
        cannot change the answer, which is worse than stating the limit.
        """
        route = local.capabilities().can("png", "psd")

        assert route is not None
        assert route.available is False
        assert route.unavailable_reason is not None
        assert "pip install" not in route.unavailable_reason

    def test_a_read_only_format_is_the_one_kind_that_means_stop_looking(self) -> None:
        """The only value a caller should not offer a remedy for.

        Pairs with the prose assertion above: the sentence declines to name a
        package, and this is the same judgement in a form a program can branch on
        without reading English or knowing which engine produced the row.
        """
        route = local.capabilities().can("png", "psd")

        assert route is not None
        assert route.unavailable_kind == "unsupported_by_build"

    def test_converting_over_an_unavailable_route_raises_with_that_reason(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "photo.png"
        Image.new("RGB", (4, 4)).save(source)

        with pytest.raises(UnsupportedRouteError) as caught:
            local.convert(source, to="psd")

        route = local.capabilities().can("png", "psd")
        assert route is not None and route.unavailable_reason is not None
        assert str(caught.value) == route.unavailable_reason


# ── 3. Failure — the guard that bounds a hostile input ───────────────


class TestPixelBudget:
    def test_an_oversized_image_is_refused_before_it_is_decoded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A decompression bomb is refused on its declared size, not its bytes.

        The budget is lowered for the test rather than a 40-megapixel file being
        written, because the guard under test reads the header — proving it with
        a real bomb would measure the disk, not the check.
        """
        from convilyn.local._engine.image import convert_core

        monkeypatch.setattr(convert_core, "MAX_IMAGE_PIXELS", 100)
        source = tmp_path / "big.png"
        Image.new("RGB", (64, 64)).save(source)  # 4096 pixels

        with pytest.raises(ConversionFailedError):
            local.convert(source, to="jpg")

    def test_the_published_budget_is_the_desktop_one(self) -> None:
        """Not the platform's, which is sized for a server and its threat model."""
        from convilyn.local._engine.image import convert_core

        assert convert_core.MAX_IMAGE_PIXELS == 40_000_000


# ── 4. Sanity — the probe must measure something ─────────────────────


class TestProbeIsNotVacuous:
    """Both sets are intersected into every image route.

    An empty one turns the whole image catalogue unavailable while every test
    above still passes, so their non-emptiness is asserted rather than assumed.
    """

    def test_something_is_readable(self) -> None:
        assert _pillow_probe.readable_extensions()

    def test_something_is_writable(self) -> None:
        assert _pillow_probe.writable_formats()

    def test_the_probe_disagrees_with_itself_across_directions(self) -> None:
        """Read and write are genuinely different questions.

        If they ever returned the same answer the two-sided route table would be
        ceremony — and it would hide exactly the read-only formats it exists to
        report.
        """
        assert _pillow_probe.can_read("psd")
        assert not _pillow_probe.can_write("PSD")

    def test_a_save_that_cannot_happen_is_reported_false(self) -> None:
        """The probe's own method, checked against Pillow directly."""
        with pytest.raises(Exception):  # noqa: B017, PT011 — any refusal will do
            Image.new("RGB", (1, 1)).save(io.BytesIO(), format="PSD")

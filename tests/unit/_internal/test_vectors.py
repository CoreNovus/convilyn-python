"""`convilyn._internal.vectors.unit` — the one way the SDK scales a vector."""

from __future__ import annotations

import math

import pytest

from convilyn._internal.vectors import unit


class TestUnit:
    def test_a_vector_is_scaled_to_length_one(self) -> None:
        assert unit((3.0, 4.0)) == pytest.approx((0.6, 0.8))

    @pytest.mark.parametrize("scale", [1e-300, 1e300], ids=["tiny", "huge"])
    def test_any_magnitude_keeps_its_direction(self, scale: float) -> None:
        assert unit((3 * scale, 4 * scale)) == pytest.approx((0.6, 0.8))

    def test_integers_are_numbers(self) -> None:
        assert unit((0, 2)) == (0.0, 1.0)

    def test_any_iterable_is_read(self) -> None:
        assert unit(value for value in (1.0, 0.0)) == (1.0, 0.0)

    def test_the_result_has_length_one(self) -> None:
        result = unit((1.0, 2.0, 3.0))

        assert math.fsum(value * value for value in result) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        "values",
        [(), (float("nan"), 1.0), (1.0, float("inf")), ("abc",), (None,), (10**400, 1)],
        ids=["empty", "nan", "infinite", "text", "none", "too-large-for-a-float"],
    )
    def test_what_is_not_finite_numbers_is_refused(self, values: tuple[object, ...]) -> None:
        with pytest.raises(ValueError, match="non-empty sequence of finite numbers"):
            unit(values)  # type: ignore[arg-type]

    def test_a_vector_of_zeros_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no direction"):
            unit((0.0, -0.0))

    def test_the_conversion_error_is_not_kept_on_the_refusal(self) -> None:
        with pytest.raises(ValueError) as caught:
            unit((10**400,))

        assert (caught.value.__cause__, caught.value.__context__) == (None, None)

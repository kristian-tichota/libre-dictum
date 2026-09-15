import math

import pytest

from libre_dictum.mathutil import clamp, ellipse_boundary


class TestClamp:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.0, 1.0)],
    )
    def test_confines_to_the_range(self, value, expected):
        assert clamp(value, 0.0, 1.0) == pytest.approx(expected)


class TestEllipseBoundary:
    @pytest.mark.parametrize(("dx", "dy"), [(1, 0), (0, 1), (1, 1), (-3, 2), (0, -5)])
    def test_a_circle_is_its_radius_in_every_direction(self, dx, dy):
        assert ellipse_boundary(dx, dy, 2.2, 2.2) == pytest.approx(2.2)

    @pytest.mark.parametrize(
        ("dx", "dy", "expected"),
        [(1, 0, 2.0), (-1, 0, 2.0), (0, 1, 6.0), (0, -1, 6.0)],
    )
    def test_an_ellipse_meets_each_axis_at_its_own_radius(self, dx, dy, expected):
        assert ellipse_boundary(dx, dy, 2.0, 6.0) == pytest.approx(expected)

    def test_a_diagonal_of_an_ellipse_lies_between_its_radii(self):
        boundary = ellipse_boundary(1, 1, 2.0, 6.0)
        assert 2.0 < boundary < 6.0

    def test_the_boundary_satisfies_the_ellipse_equation(self):
        dx, dy, radius_h, radius_v = 3.0, 1.0, 2.2, 4.0
        boundary = ellipse_boundary(dx, dy, radius_h, radius_v)
        magnitude = math.hypot(dx, dy)
        x, y = boundary * dx / magnitude, boundary * dy / magnitude
        assert (x / radius_h) ** 2 + (y / radius_v) ** 2 == pytest.approx(1.0)

    def test_scale_of_the_direction_does_not_matter(self):
        near = ellipse_boundary(1, 2, 2.0, 6.0)
        assert near == pytest.approx(ellipse_boundary(50, 100, 2.0, 6.0))

    @pytest.mark.parametrize(("radius_h", "radius_v"), [(0.0, 6.0), (2.0, 0.0), (0.0, 0.0)])
    def test_a_zeroed_radius_means_no_dead_zone_rather_than_a_division_error(
        self, radius_h, radius_v
    ):
        assert ellipse_boundary(1, 1, radius_h, radius_v) == 0.0

    def test_no_direction_has_no_boundary(self):
        assert ellipse_boundary(0, 0, 2.0, 2.0) == 0.0

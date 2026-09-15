from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from libre_dictum.pointer import PointerFilter
from libre_dictum.settings import PointerSettings
from libre_dictum.tracking.screenmap import (
    COEFFICIENTS,
    DEFAULT_SIDE,
    GRID,
    INSET,
    MAX_SIDE,
    MINIMUM_SAMPLES,
    TERMS,
    WHOLE_DESKTOP,
    Sample,
    ScreenBounds,
    ScreenMap,
    Settling,
    basis,
    grid_of,
)
from libre_dictum.tracking.tracker import extract_yaw_pitch

WIDTH_MM, HEIGHT_MM, DISTANCE_MM = 597.7, 336.2, 600.0


def looking_at(x: float, y: float, camera: np.ndarray) -> tuple[float, float]:
    """The yaw and pitch reported for a head pointing at a dot."""
    forward = camera @ np.array([-(x - 0.5) * WIDTH_MM, -(y - 0.5) * HEIGHT_MM, DISTANCE_MM])
    forward = forward / np.linalg.norm(forward)
    right = np.cross([0.0, 1.0, 0.0], forward)
    right /= np.linalg.norm(right)
    matrix = np.eye(4)
    matrix[:3, :3] = np.column_stack([right, np.cross(forward, right), forward])
    return extract_yaw_pitch(matrix)


def rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    a, b, c = math.radians(yaw), math.radians(pitch), math.radians(roll)
    about_y = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    about_x = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    about_z = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return about_z @ about_x @ about_y


def head_for(x: float, y: float) -> tuple[float, float]:
    """The yaw and pitch a head must hold to point at a normalised screen point."""
    return looking_at(x, y, np.eye(3))


SESSION = (
    (2.8, 7.9),
    (18.8, 2.0),
    (-10.7, -0.5),
    (-11.7, 11.2),
    (21.3, 16.9),
    (5.7, 3.1),
    (-14.5, 10.7),
    (4.4, 22.4),
    (22.4, 14.6),
)


def session_samples() -> list[Sample]:
    return [
        Sample(x=x, y=y, yaw=yaw, pitch=pitch)
        for (x, y), (yaw, pitch) in zip(GRID, SESSION, strict=True)
    ]


def residual_of(mapping: ScreenMap, samples: list[Sample]) -> float:
    """What fit reports, for a mapping it did not produce."""
    errors = [math.dist(mapping.project(s.yaw, s.pitch), (s.x, s.y)) for s in samples]
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def perfect_samples() -> list[Sample]:
    return [Sample(x=x, y=y, yaw=head_for(x, y)[0], pitch=head_for(x, y)[1]) for x, y in GRID]


class TestTheBasis:
    def test_it_is_the_tangent_and_not_the_angle(self):
        u, _v = basis(30.0, 0.0)
        assert u == pytest.approx(math.tan(math.radians(30.0)))
        assert u != pytest.approx(30.0 / 45.0)

    def test_looking_straight_ahead_is_the_origin(self):
        assert basis(0.0, 0.0) == (0.0, 0.0)

    def test_pitch_is_divided_by_cos_yaw_to_undo_the_spherical_convention(self):
        straight = basis(0.0, 10.0)
        turned = basis(40.0, 10.0)
        assert turned[1] > straight[1]
        assert turned[1] == pytest.approx(
            math.tan(math.radians(10.0)) / math.cos(math.radians(40.0))
        )

    def test_the_pair_is_the_ray_divided_by_its_own_depth(self):
        forward = np.array([0.4, -0.3, 1.0])
        matrix = np.eye(4)
        right = np.cross([0.0, 1.0, 0.0], forward / np.linalg.norm(forward))
        right /= np.linalg.norm(right)
        unit = forward / np.linalg.norm(forward)
        matrix[:3, :3] = np.column_stack([right, np.cross(unit, right), unit])
        u, v = basis(*extract_yaw_pitch(matrix))
        assert (u, v) == pytest.approx((0.4, 0.3), abs=1e-9)

    def test_a_wild_angle_is_clamped_before_the_tangent_runs_away(self):
        u, v = basis(300.0, -300.0)
        assert math.isfinite(u) and math.isfinite(v)


class TestTheAxesMediapipeActuallyReports:
    """Which way is left, pinned."""

    def test_the_left_of_the_screen_is_a_positive_yaw(self):
        assert head_for(0.08, 0.5)[0] > 0.0
        assert head_for(0.92, 0.5)[0] < 0.0

    def test_the_bottom_of_the_screen_is_a_positive_pitch(self):
        assert head_for(0.5, 0.92)[1] > 0.0
        assert head_for(0.5, 0.08)[1] < 0.0

    def test_the_tape_measure_and_the_rate_law_agree_about_which_way_is_left(self):
        rate = PointerFilter(PointerSettings(enabled=True, full_speed_angle=18.0)).filter(
            15.0, 0.0, 1 / 60
        )
        assert rate is not None and rate[0] < 0.0
        assert ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM).at(15.0, 0.0)[0] < 0.5

    def test_the_tape_measure_and_the_rate_law_agree_about_which_way_is_down(self):
        rate = PointerFilter(PointerSettings(enabled=True, full_speed_angle=18.0)).filter(
            0.0, 15.0, 1 / 60
        )
        assert rate is not None and rate[1] > 0.0
        assert ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM).at(0.0, 15.0)[1] > 0.5


class TestMappingFromGeometry:
    """Three numbers off a tape measure, so the law works before any session exists."""

    def test_the_head_pointing_at_a_dot_maps_to_that_dot(self):
        mapping = ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM)
        for x, y in GRID:
            assert mapping.at(*head_for(x, y)) == pytest.approx((x, y), abs=1e-9)

    def test_a_head_pointing_straight_ahead_is_the_middle_of_the_screen(self):
        assert ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM).at(0, 0) == (0.5, 0.5)

    def test_sitting_further_back_means_turning_the_head_less(self):
        near = ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, 500.0)
        far = ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, 800.0)
        assert abs(far.at(15.0, 0.0)[0] - 0.5) > abs(near.at(15.0, 0.0)[0] - 0.5)

    def test_pitch_grows_downward_and_so_does_the_screen(self):
        mapping = ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM)
        assert mapping.at(0.0, 10.0)[1] > 0.5

    @pytest.mark.parametrize("bad", [(0, 300, 600), (500, -1, 600), (500, 300, 0)])
    def test_a_screen_with_no_size_or_no_distance_is_refused(self, bad):
        with pytest.raises(ValueError, match="positive"):
            ScreenMap.from_geometry(*bad)


class TestFittingTheDots:
    def test_perfect_dots_fit_exactly(self):
        mapping = ScreenMap.fit(perfect_samples())
        assert mapping.residual == pytest.approx(0.0, abs=1e-9)
        for x, y in GRID:
            assert mapping.at(*head_for(x, y)) == pytest.approx((x, y), abs=1e-6)

    @pytest.mark.parametrize(
        "yaw,pitch,roll",
        [
            (0.0, 0.0, 0.0),
            (7.0, 0.0, 0.0),
            (0.0, 12.0, 0.0),
            (7.0, 12.0, 4.0),
        ],
    )
    def test_a_camera_that_is_not_square_to_the_head_is_absorbed_exactly(self, yaw, pitch, roll):
        camera = rotation(yaw, pitch, roll)
        samples = [
            Sample(x=x, y=y, yaw=looking_at(x, y, camera)[0], pitch=looking_at(x, y, camera)[1])
            for x, y in GRID
        ]
        mapping = ScreenMap.fit(samples)

        assert mapping.residual_px(1920, 1080) < 0.01
        for sample in samples:
            assert mapping.at(sample.yaw, sample.pitch) == pytest.approx(
                (sample.x, sample.y), abs=1e-6
            )

    def test_noise_shows_up_as_a_residual_rather_than_being_hidden(self):
        wobbled = [
            Sample(x=s.x, y=s.y, yaw=s.yaw + shift, pitch=s.pitch - shift)
            for s, shift in zip(perfect_samples(), [0.4, -0.3, 0.5, -0.2] * 3, strict=False)
        ]
        assert ScreenMap.fit(wobbled).residual > 0.0

    def test_the_fit_is_the_best_one_available_and_not_just_the_linear_solve(self):
        samples = session_samples()
        mapping = ScreenMap.fit(samples)
        best = residual_of(mapping, samples)

        assert mapping.residual == pytest.approx(best)
        for axis in ("x", "y", "perspective"):
            for index in range(len(getattr(mapping, axis))):
                for step in (2e-3, -2e-3):
                    nudged = list(getattr(mapping, axis))
                    nudged[index] += step
                    worse = replace(mapping, **{axis: tuple(nudged)})
                    assert residual_of(worse, samples) >= best

    def test_a_fit_that_throws_a_dot_off_the_screen_says_how_far_off(self):
        samples = perfect_samples()
        samples[-1] = replace(samples[-1], yaw=head_for(1.7, 0.5)[0], pitch=head_for(1.7, 0.5)[1])
        mapping = ScreenMap.fit(samples)
        clamped = [math.dist(mapping.at(s.yaw, s.pitch), (s.x, s.y)) for s in samples]

        assert any(
            not (0.0 <= v <= 1.0) for s in samples for v in mapping.project(s.yaw, s.pitch)
        ), "the setup is supposed to put at least one dot past an edge"
        assert mapping.residual > math.sqrt(sum(e * e for e in clamped) / len(clamped))

    def test_the_residual_is_reported_in_pixels_because_that_is_what_it_means(self):
        mapping = ScreenMap(x=(0,) * TERMS, y=(0,) * TERMS, residual=0.01)
        assert mapping.residual_px(1920, 1080) == pytest.approx(
            0.01 * math.hypot(1920, 1080) / 2**0.5
        )

    def test_too_few_dots_are_refused_by_name(self):
        with pytest.raises(ValueError, match="not enough"):
            ScreenMap.fit(perfect_samples()[: MINIMUM_SAMPLES - 1])

    def test_dots_in_a_single_row_are_refused_rather_than_solved_into_nonsense(self):
        row = [Sample(x=x, y=0.5, yaw=x * 40 - 20, pitch=0.0) for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        with pytest.raises(ValueError, match="do not pin down"):
            ScreenMap.fit(row)

    def test_dots_that_settled_only_along_a_diagonal_are_refused(self):
        diagonal = [
            Sample(
                x=t, y=t, yaw=looking_at(t, t, np.eye(3))[0], pitch=looking_at(t, t, np.eye(3))[1]
            )
            for t in (0.08, 0.3, 0.7, 0.92)
        ]
        with pytest.raises(ValueError, match="do not pin down"):
            ScreenMap.fit(diagonal)

    def test_a_head_that_never_moved_is_refused_too(self):
        frozen = [Sample(x=x, y=y, yaw=3.0, pitch=1.0) for x, y in GRID]
        with pytest.raises(ValueError, match="do not pin down"):
            ScreenMap.fit(frozen)

    def test_where_the_head_was_is_carried_through_for_later(self):
        samples = [
            Sample(x=s.x, y=s.y, yaw=s.yaw, pitch=s.pitch, translation=(1.0, 2.0, 30.0))
            for s in perfect_samples()
        ]
        assert ScreenMap.fit(samples).origin == pytest.approx((1.0, 2.0, 30.0))


class TestUsingTheMapping:
    def test_an_unmeasured_mapping_parks_in_the_middle_and_says_so(self):
        blank = ScreenMap()
        assert not blank.measured
        assert blank.at(20.0, -10.0) == (0.5, 0.5)

    def test_a_head_turned_past_the_screen_lands_on_the_edge(self):
        mapping = ScreenMap.from_geometry(WIDTH_MM, HEIGHT_MM, DISTANCE_MM)
        assert mapping.at(80.0, 0.0)[0] == 0.0
        assert mapping.at(-80.0, 0.0)[0] == 1.0

    def test_the_block_it_writes_is_the_block_a_config_reads(self):
        mapping = ScreenMap.fit(perfect_samples())
        block = mapping.as_config()
        assert len(block["x"]) == TERMS and len(block["y"]) == TERMS
        assert len(block["perspective"]) == 2
        assert block["samples"] == len(GRID)

    def test_a_head_pointing_along_the_plane_of_the_screen_lands_on_an_edge(self):
        steep = ScreenMap(x=(1.0, 0.0, 0.5), y=(0.0, 1.0, 0.5), perspective=(-4.0, 0.0))
        x, _y = steep.at(30.0, 0.0)
        assert 0.0 <= x <= 1.0


class TestTheGrid:
    def test_every_dot_is_on_the_screen_and_inset_from_the_edge(self):
        assert all(0.05 <= v <= 0.95 for dot in GRID for v in dot)

    def test_it_has_enough_dots_and_they_span_both_axes(self):
        assert len(GRID) >= MINIMUM_SAMPLES
        assert len({x for x, _ in GRID}) >= 3
        assert len({y for _, y in GRID}) >= 3

    def test_the_centre_comes_first_because_it_is_the_easiest(self):
        assert GRID[0] == (0.5, 0.5)


class TestAskingForMoreDots:
    """More dots is a better mapping and a worse-looking residual."""

    @pytest.mark.parametrize("side", range(DEFAULT_SIDE, MAX_SIDE + 1))
    def test_a_grid_is_square_and_inset_and_starts_in_the_middle(self, side):
        dots = grid_of(side)
        assert len(dots) == len(set(dots)) == side * side
        assert all(INSET <= v <= 1 - INSET for dot in dots for v in dot)
        assert math.dist(dots[0], (0.5, 0.5)) == pytest.approx(
            min(math.dist(d, (0.5, 0.5)) for d in dots)
        )

    @pytest.mark.parametrize("side", range(DEFAULT_SIDE, MAX_SIDE + 1))
    def test_consecutive_dots_are_never_a_single_cell_apart(self, side):
        dots = grid_of(side)
        cell = (1.0 - 2 * INSET) / (side - 1)
        for before, after in zip(dots, dots[1:], strict=False):
            assert math.dist(before, after) > cell * 1.2

    def test_the_three_by_three_is_the_grid_a_real_session_was_recorded_against(self):
        assert grid_of(DEFAULT_SIDE) == GRID

    @pytest.mark.parametrize("bad", [DEFAULT_SIDE - 1, MAX_SIDE + 1, 0, -3])
    def test_a_grid_too_small_to_check_itself_or_too_long_to_sit_through_is_refused(self, bad):
        with pytest.raises(ValueError, match="dots a side"):
            grid_of(bad)


class TestWhatTheMappingIsWorth:
    """The residual is measured on the dots the fit chose its coefficients to suit."""

    def test_it_is_less_than_the_residual_because_the_fit_absorbed_some_of_the_noise(self):
        mapping = ScreenMap.fit(session_samples())
        assert 0.0 < mapping.expected_error < mapping.residual

    def test_more_dots_lower_it_even_as_the_residual_rises(self):
        rng = np.random.default_rng(11)

        def session(side):
            dots = grid_of(side)
            return ScreenMap.fit(
                [
                    Sample(
                        x=x,
                        y=y,
                        yaw=head_for(x, y)[0] + rng.normal(0, 1.5),
                        pitch=head_for(x, y)[1] + rng.normal(0, 1.5),
                    )
                    for x, y in dots
                ]
            )

        few, many = session(3), session(5)
        assert many.residual > few.residual
        assert many.expected_error < few.expected_error

    def test_a_fit_with_nothing_spare_admits_it_rather_than_claiming_to_be_perfect(self):
        corners = [s for s in perfect_samples() if 0.5 not in (s.x, s.y)]
        assert len(corners) == MINIMUM_SAMPLES
        mapping = ScreenMap.fit(corners)
        assert mapping.residual == pytest.approx(0.0)
        assert mapping.expected_error == math.inf

    def test_it_is_the_standard_correction_for_the_degrees_of_freedom_left_over(self):
        mapping = ScreenMap.fit(session_samples())
        assert mapping.expected_error == pytest.approx(
            mapping.residual * math.sqrt(COEFFICIENTS / (2 * mapping.samples - COEFFICIENTS))
        )

    def test_it_is_reported_in_pixels_the_same_way_the_residual_is(self):
        mapping = ScreenMap.fit(session_samples())
        assert mapping.expected_error_px(1920, 1080) == pytest.approx(
            mapping.expected_error * math.hypot(1920, 1080) / 2**0.5
        )


class TestWhereTheCalibratedScreenSitsOnTheDesktop:
    """One monitor and one desktop are the same rectangle until they are not."""

    def test_one_monitor_changes_nothing(self):
        assert WHOLE_DESKTOP.whole_desktop
        assert WHOLE_DESKTOP.into_desktop(0.0, 0.0) == (0.0, 0.0)
        assert WHOLE_DESKTOP.into_desktop(0.42, 0.71) == (0.42, 0.71)
        assert WHOLE_DESKTOP.into_desktop(1.0, 1.0) == (1.0, 1.0)

    def test_a_right_hand_monitor_uses_the_right_hand_half(self):
        right = ScreenBounds.from_pixels(1920, 0, 1920, 1080, desktop=(3840, 1080))
        assert right.into_desktop(0.0, 0.0) == pytest.approx((0.5, 0.0))
        assert right.into_desktop(0.5, 0.5) == pytest.approx((0.75, 0.5))
        assert right.into_desktop(1.0, 1.0) == pytest.approx((1.0, 1.0))

    def test_a_left_hand_monitor_uses_the_left_hand_half(self):
        left = ScreenBounds.from_pixels(0, 0, 1920, 1080, desktop=(3840, 1080))
        assert left.into_desktop(0.0, 0.5) == pytest.approx((0.0, 0.5))
        assert left.into_desktop(1.0, 0.5) == pytest.approx((0.5, 0.5))

    def test_monitors_stacked_vertically_work_the_same_way(self):
        lower = ScreenBounds.from_pixels(0, 1080, 1920, 1080, desktop=(1920, 2160))
        assert lower.into_desktop(0.5, 0.0) == pytest.approx((0.5, 0.5))
        assert lower.into_desktop(0.5, 1.0) == pytest.approx((0.5, 1.0))

    def test_monitors_of_different_sizes_are_fractions_of_the_whole(self):
        wide = ScreenBounds.from_pixels(1920, 0, 2560, 1440, desktop=(4480, 1440))
        assert wide.into_desktop(0.0, 0.0) == pytest.approx((1920 / 4480, 0.0))
        assert wide.into_desktop(1.0, 1.0) == pytest.approx((1.0, 1.0))

    @pytest.mark.parametrize(
        ("rectangle", "desktop", "why"),
        [
            ((0, 0, 1920, 1080), (0, 1080), "desktop_width"),
            ((0, 0, 1920, 1080), (1920, 0), "desktop_height"),
            ((0, 0, 0, 1080), (1920, 1080), "width"),
            ((0, 0, 1920, -5), (1920, 1080), "height"),
        ],
    )
    def test_a_rectangle_with_no_size_is_refused(self, rectangle, desktop, why):
        with pytest.raises(ValueError, match=why):
            ScreenBounds.from_pixels(*rectangle, desktop=desktop)

    @pytest.mark.parametrize(
        "rectangle",
        [
            (1920, 0, 1920, 1080),
            (-1, 0, 1920, 1080),
            (0, 600, 1920, 1080),
        ],
    )
    def test_a_monitor_that_does_not_fit_the_desktop_is_refused(self, rectangle):
        with pytest.raises(ValueError, match="does not fit inside a desktop"):
            ScreenBounds.from_pixels(*rectangle, desktop=(1920, 1080))


class TestSettlingOnADot:
    """The calibration's answer to "press a key when ready", for somebody with no keyboard."""

    def test_a_head_that_holds_still_settles(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=0.0)
        assert not settling.settled(0.0, 10.0, 5.0)
        assert not settling.settled(0.5, 10.2, 5.1)
        assert settling.settled(1.0, 10.1, 4.9)

    def test_a_head_that_moves_starts_the_clock_again(self):
        settling = Settling(tolerance=1.2, hold=0.8)
        settling.feed(0.0, 10.0, 5.0)
        settling.feed(0.7, 10.1, 5.0)
        assert settling.feed(0.9, 25.0, 5.0) == 0.0
        assert not settling.settled(1.5, 25.0, 5.0)

    def test_the_tolerance_is_wider_than_a_head_at_rest_wanders(self):
        assert Settling().tolerance > 3 * 0.218

    def test_it_reports_how_far_through_the_dwell_it_is(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=0.0)
        settling.feed(0.0, 0.0, 0.0)
        assert settling.feed(0.4, 0.1, 0.1) == pytest.approx(0.4)

    def test_resetting_forgets_where_the_head_was(self):
        settling = Settling(tolerance=1.2, hold=0.8)
        settling.feed(0.0, 10.0, 5.0)
        settling.reset()
        assert settling.feed(0.5, 10.0, 5.0) == 0.0


class TestNotSettlingBeforeTheHeadHasMoved:
    """The dot's answer must not be the *previous* dot's answer."""

    def test_a_head_that_never_moves_never_settles(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=3.0)
        for tenth in range(60):
            assert not settling.settled(tenth / 10.0, 12.0, 4.0)

    def test_moving_away_and_stopping_settles(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=3.0)
        settling.feed(0.0, 12.0, 4.0)
        for step in range(1, 12):
            settling.feed(step / 20.0, 12.0 - step, 4.0)
        assert not settling.settled(0.6, 1.0, 4.0)
        assert settling.settled(1.6, 1.0, 4.0)

    def test_the_gate_is_smaller_than_the_closest_two_dots_on_the_grid(self):
        assert 0.0 < Settling().travel < 11.7 / 2

    def test_the_first_dot_switches_it_off_because_there_is_no_previous_dot(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=0.0)
        settling.feed(0.0, 12.0, 4.0)
        assert settling.settled(1.0, 12.0, 4.0)


class TestWhatTheDotRecords:
    """The mean of the dwell, off the same smoothed pose the session steers with."""

    def test_it_records_the_average_of_the_dwell_and_not_one_frame_of_it(self):
        settling = Settling(tolerance=1.2, hold=0.8, travel=0.0)
        for step, yaw in enumerate([9.6, 10.0, 10.4, 10.0, 10.4]):
            settling.feed(step / 10.0, yaw, 5.0)
        assert settling.pose is not None
        assert settling.pose[0] < 10.4
        assert settling.pose[0] == pytest.approx(10.0, abs=0.35)

    def test_there_is_nothing_to_record_before_a_frame_has_arrived(self):
        assert Settling().pose is None

    def test_it_smooths_the_pose_the_way_the_session_does(self):
        jitter = [0.0, 1.6, -1.6, 1.6, -1.6, 1.6]
        smoothed = Settling(tolerance=1.2, hold=0.4, travel=0.0)
        raw = Settling(tolerance=1.2, hold=0.4, travel=0.0, min_cutoff_hz=1e4, beta=0.0)
        for step, yaw in enumerate(jitter):
            at = step / 10.0
            smoothed.feed(at, yaw, 0.0)
            raw.feed(at, yaw, 0.0)

        assert smoothed.feed(0.6, 0.0, 0.0) >= 0.4, "smoothing should ride out the jitter"
        assert raw.feed(0.6, 0.0, 0.0) == 0.0, "unsmoothed, the dwell keeps restarting"

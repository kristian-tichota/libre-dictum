import math

import pytest

from libre_dictum.dwell import Dwell
from libre_dictum.pointer import AbsolutePointer, PointerFilter
from libre_dictum.settings import DwellSettings, PointerSettings
from libre_dictum.tracking.screenmap import WHOLE_DESKTOP, ScreenBounds, ScreenMap

DT = 1 / 60

DEFAULTS = {
    "dead_angle_h": 2.2,
    "dead_angle_v": 2.2,
    "speed_power": 1.5,
    "full_speed_angle": 18.0,
    "max_speed_px_per_sec": 800.0,
}


def filtered(yaw=0.0, pitch=0.0, dt=DT, **overrides):
    return PointerFilter(PointerSettings(enabled=True, **overrides)).filter(yaw, pitch, dt)


def speed(yaw=0.0, pitch=0.0, **overrides):
    """Pointer speed in px/s, or zero when nothing moves."""
    movement = filtered(yaw, pitch, **{**DEFAULTS, **overrides})
    return 0.0 if movement is None else math.hypot(*movement) / DT


class TestEnabling:
    def test_a_mode_without_pointer_control_never_moves_the_pointer(self):
        assert PointerFilter(PointerSettings(enabled=False)).filter(30.0, 30.0, DT) is None

    def test_a_head_facing_forward_does_not_move_the_pointer(self):
        assert filtered(0.0, 0.0) is None

    def test_the_first_frame_moves_nothing_because_it_has_no_interval(self):
        assert filtered(20.0, 0.0, dt=0.0, **DEFAULTS) is None


class TestDeadZone:
    @pytest.mark.parametrize("yaw", [0.0, 1.0, -1.9, 2.2, -2.2])
    def test_rotation_inside_the_dead_zone_is_ignored(self, yaw):
        assert filtered(yaw=yaw, **DEFAULTS) is None

    def test_the_pointer_starts_from_a_standstill_at_the_edge(self):
        assert speed(yaw=2.2001) == pytest.approx(0.0, abs=0.5)

    def test_speeds_below_one_pixel_per_frame_exist_at_all(self):
        assert 0.0 < speed(yaw=2.5) < 5.0
        assert 0.0 < speed(yaw=3.0) < 15.0

    @pytest.mark.parametrize("sign", [1, -1])
    def test_the_dead_zone_is_symmetric(self, sign):
        assert speed(yaw=sign * 6.0) == pytest.approx(speed(yaw=6.0))

    def test_each_axis_has_its_own_dead_angle(self):
        still = filtered(yaw=0.0, pitch=5.0, **{**DEFAULTS, "dead_angle_v": 10.0})
        moving = filtered(yaw=5.0, pitch=0.0, **{**DEFAULTS, "dead_angle_v": 10.0})
        assert still is None
        assert moving is not None

    def test_unequal_dead_angles_make_an_ellipse_not_a_rectangle(self):
        settings = {**DEFAULTS, "dead_angle_h": 3.0, "dead_angle_v": 9.0}
        assert filtered(yaw=2.9, pitch=0.0, **settings) is None
        assert filtered(yaw=0.0, pitch=8.9, **settings) is None
        assert filtered(yaw=2.9, pitch=8.9, **settings) is not None


class TestResponseCurve:
    @pytest.mark.parametrize(
        ("degrees", "expected"),
        [(2.5, 2), (3.0, 9), (4.0, 31), (6.0, 94), (10.0, 277), (15.0, 583), (18.0, 800)],
    )
    def test_the_shipped_curve(self, degrees, expected):
        """The decided feel: a crawl near centre, full speed at a comfortable 18 degrees."""
        assert speed(yaw=degrees) == pytest.approx(expected, abs=1.0)

    def test_full_speed_is_reached_at_the_configured_angle(self):
        assert speed(yaw=18.0) == pytest.approx(800.0)

    @pytest.mark.parametrize("degrees", [18.0, 25.0, 40.0, 90.0])
    def test_speed_is_clamped_past_that_angle(self, degrees):
        assert speed(yaw=degrees) == pytest.approx(800.0)

    def test_a_steeper_power_slows_the_middle_without_moving_the_ends(self):
        gentle = speed(yaw=9.0, speed_power=1.0)
        steep = speed(yaw=9.0, speed_power=2.5)
        assert steep < gentle
        assert speed(yaw=18.0, speed_power=2.5) == pytest.approx(speed(yaw=18.0, speed_power=1.0))

    def test_full_speed_angle_rescales_the_whole_curve(self):
        assert speed(yaw=12.0, full_speed_angle=12.0) == pytest.approx(800.0)
        assert speed(yaw=12.0, full_speed_angle=25.0) < 800.0


class TestPerSecondGain:
    @pytest.mark.parametrize("fps", [15, 30, 60, 120])
    def test_the_same_angle_is_the_same_speed_at_any_frame_rate(self, fps):
        movement = filtered(yaw=10.0, dt=1 / fps, **DEFAULTS)
        assert math.hypot(*movement) * fps == pytest.approx(277.5, abs=1.0)

    def test_a_longer_interval_moves_further_in_one_step(self):
        one = math.hypot(*filtered(yaw=10.0, dt=DT, **DEFAULTS))
        two = math.hypot(*filtered(yaw=10.0, dt=2 * DT, **DEFAULTS))
        assert two == pytest.approx(2 * one)


class TestDirection:
    @pytest.mark.parametrize(
        ("yaw", "pitch"), [(10, 10), (10, 5), (10, 3), (12, 4), (3, 1), (-8, 6), (1, -20)]
    )
    def test_the_pointer_travels_exactly_where_the_head_moved(self, yaw, pitch):
        """A per-axis curve bent this by up to 7.6 degrees, worse the steeper the curve."""
        dx, dy = filtered(yaw, pitch, **DEFAULTS)
        head = math.degrees(math.atan2(pitch, yaw))
        pointer = math.degrees(math.atan2(dy, -dx))
        assert pointer == pytest.approx(head, abs=1e-6)

    def test_direction_survives_a_steep_curve(self):
        dx, dy = filtered(10.0, 5.0, **{**DEFAULTS, "speed_power": 3.0})
        assert math.degrees(math.atan2(dy, -dx)) == pytest.approx(
            math.degrees(math.atan2(5.0, 10.0)), abs=1e-6
        )

    def test_a_diagonal_is_no_faster_than_an_axis(self):
        """The per-axis clamp let a diagonal reach 41% over the limit, so fast turns veered."""
        assert speed(yaw=40.0, pitch=40.0) == pytest.approx(speed(yaw=40.0))


class TestOrientation:
    def test_turning_the_head_left_moves_the_pointer_left(self):
        dx, _ = filtered(yaw=10.0, **DEFAULTS)
        assert dx < 0

    def test_looking_down_moves_the_pointer_down(self):
        _, dy = filtered(pitch=-10.0, **DEFAULTS)
        assert dy < 0

    @pytest.mark.parametrize(("axis", "index"), [("invert_x", 0), ("invert_y", 1)])
    def test_inverting_an_axis_flips_it(self, axis, index):
        plain = filtered(yaw=10.0, pitch=10.0, **DEFAULTS)
        inverted = filtered(yaw=10.0, pitch=10.0, **{**DEFAULTS, axis: True})
        assert inverted[index] == pytest.approx(-plain[index])
        assert inverted[1 - index] == pytest.approx(plain[1 - index])


class TestAtRest:
    """What gates auto-recentring: neutral may only absorb drift inside the dead zone."""

    @pytest.mark.parametrize("yaw", [0.0, 1.0, 2.2])
    def test_inside_the_dead_zone_is_at_rest(self, yaw):
        assert PointerFilter(PointerSettings(enabled=True, **DEFAULTS)).at_rest(yaw, 0.0)

    @pytest.mark.parametrize("yaw", [2.3, 10.0, -30.0])
    def test_outside_it_is_not(self, yaw):
        assert not PointerFilter(PointerSettings(enabled=True, **DEFAULTS)).at_rest(yaw, 0.0)

    def test_it_ignores_whether_the_mode_moves_the_pointer(self):
        disabled = PointerFilter(PointerSettings(enabled=False, **DEFAULTS))
        assert disabled.at_rest(1.0, 0.0)
        assert not disabled.at_rest(10.0, 0.0)


class TestSettingsResolution:
    def test_a_global_knob_becomes_the_default_for_every_mode(self, settings_of):
        settings = settings_of(
            {
                "ht_dead_angle_h": 5.0,
                "ht_invert_y": True,
                "modes": {"root": {"type": "vosk", "path": "m", "ht_enabled": True}},
            }
        )
        pointer = settings.modes["root"].pointer

        assert pointer.dead_angle_h == pytest.approx(5.0)
        assert pointer.invert_y is True

    def test_a_mode_can_override_a_global_knob(self, settings_of):
        settings = settings_of(
            {
                "ht_dead_angle_h": 5.0,
                "modes": {
                    "root": {
                        "type": "vosk",
                        "path": "m",
                        "ht_enabled": True,
                        "ht_dead_angle_h": 1.0,
                    }
                },
            }
        )
        assert settings.modes["root"].pointer.dead_angle_h == pytest.approx(1.0)

    def test_pointer_settings_exist_even_for_a_mode_that_does_not_use_them(self, settings_of):
        settings = settings_of({"modes": {"root": {"type": "vosk", "path": "m"}}})
        assert settings.modes["root"].pointer.enabled is False


class TestAbsolutePointer:
    """Head pose straight onto a point, and the rectangle that point is a fraction of."""

    MAPPING = ScreenMap.from_geometry(597.7, 336.2, 600.0)

    RIGHT_HALF = ScreenBounds.from_pixels(1920, 0, 1920, 1080, desktop=(3840, 1080))

    def pointer(self, placement=WHOLE_DESKTOP, mapping=None, **overrides):
        return AbsolutePointer(
            PointerSettings(enabled=True, **overrides),
            self.MAPPING if mapping is None else mapping,
            placement,
        )

    def test_a_mode_without_pointer_control_points_nowhere(self):
        assert AbsolutePointer(PointerSettings(enabled=False), self.MAPPING).at(0.0, 0.0) is None

    def test_asking_without_a_calibration_parks_rather_than_guessing(self):
        assert self.pointer(mapping=ScreenMap()).at(10.0, 5.0) is None

    def test_one_monitor_is_the_whole_desktop(self):
        assert self.pointer().at(0.0, 0.0) == pytest.approx((0.5, 0.5))

    def test_a_second_monitor_moves_the_middle_of_the_first_one(self):
        assert self.pointer(self.RIGHT_HALF).at(0.0, 0.0) == pytest.approx((0.75, 0.5))

    def test_the_pointer_cannot_leave_the_monitor_it_was_calibrated_on(self):
        for yaw in (-80.0, -40.0, 40.0, 80.0):
            x, _y = self.pointer(self.RIGHT_HALF).at(yaw, 0.0)
            assert 0.5 - 1e-9 <= x <= 1.0 + 1e-9

    def test_inverting_an_axis_mirrors_the_monitor_and_not_the_desktop(self):
        plain = self.pointer(self.RIGHT_HALF).at(80.0, 0.0)
        mirrored = self.pointer(self.RIGHT_HALF, invert_x=True).at(80.0, 0.0)
        assert plain == pytest.approx((0.5, 0.5))
        assert mirrored == pytest.approx((1.0, 0.5))

    def test_the_pose_it_takes_is_the_one_it_is_given(self):
        pointer = self.pointer(self.RIGHT_HALF)
        assert pointer.at(12.0, -4.0) == pointer.at(12.0, -4.0)


class TestDwell:
    """Where a stopped head clicks, and when it must not."""

    def run(self, poses, dwell=None, start=0, **feed):
        dwell = dwell or Dwell(DwellSettings())
        return [
            dwell.feed((start + i) * DT, yaw, pitch, **feed) for i, (yaw, pitch) in enumerate(poses)
        ]

    def turn(self, degrees=10.0, start=0.0):
        """A turn at 40 degrees a second, every frame further than the stop radius."""
        frames = round(abs(degrees) / 40.0 * 60)
        return [(start + degrees * i / frames, 0.0) for i in range(frames)]

    def still(self, seconds, yaw=10.0, wander=0.0):
        return [(yaw + wander * (-1) ** i, 0.0) for i in range(round(seconds * 60))]

    def clicks(self, steps):
        return sum(step.click for step in steps)

    def test_a_stop_after_a_deliberate_move_clicks_once(self):
        assert self.clicks(self.run(self.turn() + self.still(0.9))) == 1

    def test_the_click_waits_for_the_whole_dwell(self):
        assert self.clicks(self.run(self.turn() + self.still(0.75))) == 0

    def test_a_stop_that_nothing_led_up_to_never_clicks(self):
        steps = self.run(self.still(3.0, yaw=5.0))
        assert self.clicks(steps) == 0
        assert all(step.ring is None for step in steps)

    def test_a_drift_smaller_than_the_arrival_never_clicks(self):
        steps = self.run(self.turn(5.0) + self.still(3.0, yaw=5.0))
        assert self.clicks(steps) == 0

    def test_holding_still_after_the_click_does_not_click_again(self):
        assert self.clicks(self.run(self.turn() + self.still(3.0))) == 1

    def test_leaving_before_the_dwell_ends_takes_the_ring_away(self):
        steps = self.run(self.turn() + self.still(0.6) + [(12.0, 0.0)])
        assert steps[-2].ring is not None
        assert steps[-1].ring is None
        assert self.clicks(steps) == 0

    def test_a_correction_just_after_the_move_still_clicks(self):
        steps = self.run(self.turn() + self.still(0.3) + self.still(0.9, yaw=11.5))
        assert self.clicks(steps) == 1
        assert [step.yaw for step in steps if step.click] == [pytest.approx(11.5)]

    def test_the_ring_fills_over_the_second_half_of_the_dwell(self):
        travel = self.turn()
        steps = self.run(travel + self.still(0.6))
        assert steps[len(travel) + 18].ring is None
        assert steps[-1].ring == pytest.approx(0.5, abs=0.05)

    def test_before_the_ring_the_pointer_follows_the_head(self):
        travel = self.turn()
        poses = travel + self.still(0.3, wander=0.3)
        steps = self.run(poses)
        assert [step.yaw for step in steps] == [yaw for yaw, _ in poses]

    def test_while_the_ring_fills_the_pointer_is_held_at_the_mean(self):
        steps = self.run(self.turn() + self.still(0.9, wander=0.3))
        held = [step for step in steps if step.ring is not None and step.ring > 0.3]
        assert held
        assert all(step.yaw == pytest.approx(10.0, abs=0.02) for step in held)

    def test_the_click_lands_where_the_pointer_was_held(self):
        steps = self.run(self.turn() + self.still(0.9, wander=0.3))
        (click,) = [step for step in steps if step.click]
        assert click.yaw == pytest.approx(10.0, abs=0.02)
        assert click.clicked

    def test_a_held_mouse_button_keeps_it_from_clicking(self):
        steps = self.run(self.turn() + self.still(0.9), held=True)
        assert self.clicks(steps) == 0
        assert all(step.ring is None for step in steps)

    def test_input_during_the_stop_keeps_it_from_clicking(self):
        dwell = Dwell(DwellSettings())
        travel = self.turn()
        self.run(travel + self.still(0.3), dwell=dwell, typed=4)
        later = self.run(self.still(0.6), dwell=dwell, start=len(travel) + 18, typed=6)
        assert self.clicks(later) == 0

    def test_input_before_the_stop_does_not(self):
        assert self.clicks(self.run(self.turn() + self.still(0.9), typed=9)) == 1

    def test_a_gap_forgets_the_move_before_it(self):
        dwell = Dwell(DwellSettings())
        travel = self.turn()
        self.run(travel, dwell=dwell)
        after = self.run(self.still(0.9), dwell=dwell, start=len(travel) + 12)
        assert self.clicks(after) == 0

    def test_an_arrival_angle_of_zero_lets_any_stop_click(self):
        steps = self.run(self.still(0.9, yaw=5.0), Dwell(DwellSettings(arrival=0.0)))
        assert self.clicks(steps) == 1

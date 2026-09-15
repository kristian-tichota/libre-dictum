import math

import pytest

from libre_dictum.smoothing import Neutral, OneEuroFilter, SubPixelCarry

DT = 1 / 60


def jitter(filter_, amplitude=0.4, frames=120):
    """Run alternating noise through the filter and return the spread it lets past."""
    out = [filter_.filter(amplitude if i % 2 else -amplitude, 0.0, DT)[0] for i in range(frames)]
    settled = out[frames // 2 :]
    return max(settled) - min(settled)


def sweep(filter_, target=20.0, seconds=0.5):
    """Run a linear turn through the filter and return where it ends up."""
    frames = int(seconds / DT)
    for i in range(frames):
        value = filter_.filter(target * i / (frames - 1), 0.0, DT)
    return value[0]


class TestOneEuroFilter:
    def test_the_first_sample_passes_through_untouched(self):
        assert OneEuroFilter().filter(3.0, -4.0, DT) == (3.0, -4.0)

    def test_a_still_head_loses_almost_all_its_jitter(self):
        assert jitter(OneEuroFilter(1.0, 0.05)) < 0.1

    def test_a_deliberate_turn_arrives_nearly_intact(self):
        assert sweep(OneEuroFilter(1.0, 0.05)) > 18.5

    def test_a_higher_beta_costs_less_lag(self):
        assert sweep(OneEuroFilter(1.0, 0.2)) > sweep(OneEuroFilter(1.0, 0.001))

    def test_a_lower_cutoff_smooths_harder(self):
        assert jitter(OneEuroFilter(0.2, 0.05)) < jitter(OneEuroFilter(5.0, 0.05))

    def test_both_axes_are_smoothed_by_the_same_amount(self):
        """Smoothing one axis harder would bend the direction the radial curve preserves."""
        filter_ = OneEuroFilter()
        filter_.filter(0.0, 0.0, DT)
        for _ in range(30):
            x, y = filter_.filter(10.0, 10.0, DT)

        assert x == pytest.approx(y)

    def test_reset_forgets_the_history(self):
        filter_ = OneEuroFilter()
        for _ in range(30):
            filter_.filter(20.0, 0.0, DT)
        filter_.reset()

        assert filter_.filter(0.0, 0.0, DT) == (0.0, 0.0)

    @pytest.mark.parametrize("dt", [0.0, -1.0])
    def test_a_nonsense_interval_takes_the_sample_as_it_is(self, dt):
        assert OneEuroFilter().filter(7.0, 7.0, dt) == (7.0, 7.0)


class TestNeutral:
    def test_a_pose_is_reported_relative_to_neutral(self):
        assert Neutral(1.0, 3.0).relative(5.0, 5.0) == (4.0, 2.0)

    def test_recentring_makes_the_current_pose_the_new_zero(self):
        neutral = Neutral(0.0, 0.0)
        neutral.recenter(4.0, -2.0)

        assert neutral.relative(4.0, -2.0) == (0.0, 0.0)

    def test_settling_absorbs_drift_exponentially(self):
        neutral, pose, seconds = Neutral(), 2.0, 5.0
        for _ in range(int(seconds / DT)):
            neutral.settle(*neutral.relative(pose, 0.0), DT, seconds)

        assert neutral.yaw == pytest.approx(2.0 * (1 - math.exp(-1)), abs=0.05)

    def test_a_longer_time_constant_settles_more_slowly(self):
        def after_a_second(seconds):
            neutral = Neutral()
            for _ in range(60):
                neutral.settle(*neutral.relative(2.0, 0.0), DT, seconds)
            return neutral.yaw

        assert after_a_second(30.0) < after_a_second(3.0)

    @pytest.mark.parametrize(("dt", "seconds"), [(0.0, 5.0), (DT, 0.0), (DT, -1.0)])
    def test_a_nonsense_time_constant_moves_nothing(self, dt, seconds):
        neutral = Neutral(1.0, 1.0)
        neutral.settle(5.0, 5.0, dt, seconds)

        assert (neutral.yaw, neutral.pitch) == (1.0, 1.0)


class TestSubPixelCarry:
    def test_whole_pixels_pass_straight_through(self):
        assert SubPixelCarry().take(3.0, -2.0) == (3, -2)

    def test_a_fraction_is_held_back(self):
        assert SubPixelCarry().take(0.7, 0.7) == (0, 0)

    def test_and_paid_once_it_amounts_to_a_pixel(self):
        carry = SubPixelCarry()
        carry.take(0.7, 0.7)

        assert carry.take(0.7, 0.7) == (1, 1)

    @pytest.mark.parametrize("sign", [1, -1])
    def test_nothing_is_lost_over_many_calls(self, sign):
        carry = SubPixelCarry()
        total = sum(carry.take(sign * 0.25, 0.0)[0] for _ in range(100))

        assert total == sign * 25

    @pytest.mark.parametrize("sign", [1, -1])
    def test_an_unrepresentable_step_still_loses_less_than_a_pixel(self, sign):
        carry = SubPixelCarry()
        total = sum(carry.take(sign * 0.3, 0.0)[0] for _ in range(100))

        assert total == pytest.approx(sign * 30, abs=1)

    def test_each_axis_carries_on_its_own(self):
        carry = SubPixelCarry()
        carry.take(0.9, 0.1)

        assert carry.take(0.2, 0.2) == (1, 0)

    def test_reset_drops_what_was_owed(self):
        carry = SubPixelCarry()
        carry.take(0.9, 0.9)
        carry.reset()

        assert carry.take(0.2, 0.2) == (0, 0)

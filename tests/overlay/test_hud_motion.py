from __future__ import annotations

from pathlib import Path

import pytest

from libre_dictum.overlay.hud import motion, render
from libre_dictum.overlay.hud.motion import Slide

WINDOW = Path(__file__).parents[2] / "src" / "libre_dictum" / "overlay" / "hud" / "window.py"

CHIP, SHEET = "top-right", "right"

WIDTH, GAP = 456.0, 12.0


def at(slide: Slide, fraction: float) -> float:
    """Where a slide begun at time 0 has got to, fraction of the way through."""
    return slide.advance(slide.duration * fraction)


class TestWhichSideASurfaceIsOn:
    @pytest.mark.parametrize("anchor", render.ANCHORS)
    def test_every_anchor_the_display_offers_lands_on_a_side(self, anchor):
        assert motion.side(anchor) in motion.SIDES

    @pytest.mark.parametrize(
        "anchor, expected",
        [
            ("top-right", "right"),
            ("bottom-right", "right"),
            ("right", "right"),
            ("top-left", "left"),
            ("bottom-left", "left"),
            ("left", "left"),
        ],
    )
    def test_the_side_is_the_tail_of_the_name(self, anchor, expected):
        assert motion.side(anchor) == expected

    def test_a_name_nobody_recognizes_falls_back_where_the_window_does(self):
        assert motion.side("middle") == motion.DEFAULT_SIDE
        assert 'EDGES["top-right"]' in WINDOW.read_text()


class TestHowFarTheChipMoves:
    def test_the_default_pair_collides_and_the_chip_yields_the_whole_width(self):
        assert motion.clearance(CHIP, SHEET, width=WIDTH, gap=GAP) == WIDTH + GAP

    def test_the_gap_is_what_keeps_them_from_touching(self):
        assert motion.clearance(CHIP, SHEET, width=WIDTH) == WIDTH
        assert motion.clearance(CHIP, SHEET, width=WIDTH, gap=GAP) > WIDTH

    @pytest.mark.parametrize("chip", ["top-left", "bottom-left", "left"])
    def test_a_chip_on_the_other_side_of_the_screen_never_moves(self, chip):
        assert motion.clearance(chip, SHEET, width=WIDTH, gap=GAP) == 0.0

    @pytest.mark.parametrize("chip", render.ANCHORS)
    @pytest.mark.parametrize("sheet", render.ANCHORS)
    def test_it_moves_exactly_when_the_two_share_an_edge(self, chip, sheet):
        shared = motion.side(chip) == motion.side(sheet)
        assert bool(motion.clearance(chip, sheet, width=WIDTH, gap=GAP)) is shared

    def test_a_sheet_of_no_width_asks_for_nothing(self):
        assert motion.clearance(CHIP, SHEET, width=0.0) == 0.0

    def test_the_vertical_is_deliberately_not_consulted(self):
        assert motion.clearance("top-right", "bottom-right", width=WIDTH) == WIDTH


class TestTheEasing:
    def test_it_starts_and_ends_where_it_is_asked_to(self):
        assert motion.ease(0.0) == 0.0
        assert motion.ease(1.0) == 1.0

    def test_it_is_symmetric_so_closing_is_opening_backwards(self):
        assert motion.ease(0.5) == pytest.approx(0.5)
        for fraction in (0.1, 0.25, 0.4):
            assert motion.ease(fraction) == pytest.approx(1.0 - motion.ease(1.0 - fraction))

    def test_it_leaves_and_arrives_at_rest(self):
        assert motion.ease(0.05) < 0.05
        assert motion.ease(0.95) > 0.95

    def test_it_never_overshoots(self):
        values = [motion.ease(step / 20) for step in range(21)]
        assert values == sorted(values)
        assert all(0.0 <= value <= 1.0 for value in values)

    @pytest.mark.parametrize("fraction", [-1.0, 2.0])
    def test_a_time_outside_the_slide_is_clamped_rather_than_extrapolated(self, fraction):
        assert motion.ease(fraction) in (0.0, 1.0)


class TestTheSlide:
    def test_it_sits_still_until_it_is_aimed_somewhere(self):
        slide = Slide()
        assert slide.value == 0.0 and not slide.moving
        assert slide.advance(1000.0) == 0.0

    def test_aiming_it_somewhere_new_is_what_asks_for_frames(self):
        slide = Slide()
        assert slide.aim(468.0, now=0.0) is True
        assert slide.moving

    def test_aiming_it_where_it_already_is_asks_for_none(self):
        slide = Slide()
        assert slide.aim(0.0, now=0.0) is False
        assert not slide.moving

    def test_it_arrives_exactly_and_then_stops(self):
        slide = Slide()
        slide.aim(468.0, now=0.0)
        assert at(slide, 1.0) == 468.0
        assert not slide.moving
        assert slide.advance(10_000.0) == 468.0

    def test_it_is_somewhere_in_between_in_between(self):
        slide = Slide()
        slide.aim(468.0, now=0.0)
        assert at(slide, 0.5) == pytest.approx(234.0)
        assert slide.moving

    def test_the_journey_only_ever_goes_forwards(self):
        slide = Slide()
        slide.aim(468.0, now=0.0)
        seen = [at(slide, step / 12) for step in range(13)]
        assert seen == sorted(seen)
        assert seen[0] < 468.0 and seen[-1] == 468.0

    def test_a_sheet_closed_mid_slide_sends_the_chip_back_from_where_it_is(self):
        slide = Slide()
        slide.aim(468.0, now=0.0)
        halfway = at(slide, 0.5)
        assert slide.aim(0.0, now=slide.duration * 0.5) is True
        assert slide.advance(slide.duration * 0.5) == halfway
        assert at(slide, 0.75) < halfway
        assert slide.advance(slide.duration * 1.5) == 0.0

    def test_re_aiming_at_the_same_target_does_not_restart_the_journey(self):
        slide = Slide()
        slide.aim(468.0, now=0.0)
        halfway = at(slide, 0.5)
        assert slide.aim(468.0, now=slide.duration * 0.5) is True
        assert at(slide, 0.5) == halfway
        assert at(slide, 1.0) == 468.0

    def test_a_desktop_with_animations_off_still_ends_up_beside_the_sheet(self):
        slide = Slide(duration=0.0)
        assert slide.aim(468.0, now=0.0) is True
        assert slide.advance(0.0) == 468.0
        assert not slide.moving

    def test_a_frame_timed_before_the_aim_is_not_a_division_by_zero(self):
        slide = Slide(duration=0.0)
        slide.aim(468.0, now=1000.0)
        assert slide.advance(984.0) == 468.0
        assert not slide.moving

    def test_a_move_too_small_to_see_costs_no_frames(self):
        slide = Slide()
        assert slide.aim(motion.EPSILON / 2, now=0.0) is False
        assert slide.value == motion.EPSILON / 2

    def test_the_duration_is_one_movement_and_not_a_journey(self):
        assert 80.0 <= motion.DURATION_MS <= 400.0


class TestTheWindowUsesIt:
    """What a file this container cannot import can still be held to."""

    def test_the_chip_is_told_how_much_room_to_leave_on_every_frame(self):
        source = WINDOW.read_text()
        assert "self.chip.make_room(self._room_for(panel))" in source

    def test_the_room_it_leaves_is_the_sheet_measured_rather_than_assumed(self):
        source = WINDOW.read_text()
        assert "width=self.sheet.span()" in source
        assert "self.window.get_width()" in source

    def test_moving_the_chip_is_a_margin_and_nothing_else(self):
        assert "set_margin" in WINDOW.read_text()

    def test_the_slide_runs_on_gtks_clock_rather_than_a_timer_of_ours(self):
        assert "add_tick_callback" in WINDOW.read_text()

from __future__ import annotations

from libre_dictum.pedals.reader import TapRuns
from libre_dictum.settings import PedalSettings


def tap(runs: TapRuns, name: str, at: float, *, held: float = 0.1, typed: int = 0):
    """One press-and-release, and whatever run it ends."""
    runs.press(name, at, 0)
    return runs.release(name, at + held, typed)


class TestWhatCountsAsATap:
    def test_a_brief_bare_press_is_a_tap(self):
        assert tap(TapRuns(), "left", 0.0) == ("left",)

    def test_something_typed_while_it_was_down_is_not(self):
        assert tap(TapRuns(), "left", 0.0, typed=1) == ()

    def test_a_long_bare_press_is_not_either(self):
        runs = TapRuns(tap_ms=500)

        assert tap(runs, "left", 0.0, held=0.6) == ()

    def test_exactly_at_the_limit_still_counts(self):
        runs = TapRuns(tap_ms=500)

        assert tap(runs, "left", 0.0, held=0.5) == ("left",)

    def test_a_release_with_no_press_behind_it_is_nothing(self):
        assert TapRuns().release("left", 1.0, 0) == ()

    def test_a_pedal_is_not_still_down_after_its_release(self):
        runs = TapRuns()
        tap(runs, "left", 0.0)

        assert runs.release("left", 0.2, 0) == ()


class TestTheRun:
    def test_taps_accumulate(self):
        runs = TapRuns()

        assert tap(runs, "left", 0.0) == ("left",)
        assert tap(runs, "left", 0.3) == ("left", "left")
        assert tap(runs, "right", 0.6) == ("left", "left", "right")

    def test_a_gap_starts_the_run_again(self):
        runs = TapRuns(sequence_ms=1000)
        tap(runs, "left", 0.0)

        assert tap(runs, "left", 2.0) == ("left",)

    def test_the_gap_is_measured_between_taps_not_from_the_first(self):
        runs = TapRuns(sequence_ms=1000)
        tap(runs, "left", 0.0)
        tap(runs, "left", 0.9)

        assert tap(runs, "left", 1.8) == ("left", "left", "left")

    def test_clearing_it_starts_afresh(self):
        runs = TapRuns()
        tap(runs, "left", 0.0)
        tap(runs, "left", 0.3)
        runs.clear()

        assert tap(runs, "left", 0.6) == ("left",)

    def test_a_pedal_genuinely_used_mid_run_does_not_end_it(self):
        runs = TapRuns()
        tap(runs, "left", 0.0)
        tap(runs, "middle", 0.2, typed=1)

        assert tap(runs, "left", 0.4) == ("left", "left")

    def test_resetting_forgets_the_run_and_what_was_down(self):
        runs = TapRuns()
        runs.press("left", 0.0)
        tap(runs, "right", 0.1)

        runs.reset()

        assert runs.release("left", 0.2, 0) == ()
        assert tap(runs, "right", 0.3) == ("right",)


class TestOverlappingPresses:
    def test_each_pedal_is_timed_on_its_own(self):
        runs = TapRuns(tap_ms=500)
        runs.press("left", 0.0, 0)
        runs.press("right", 0.4, 0)

        assert runs.release("right", 0.5, 0) == ("right",)
        assert runs.release("left", 0.7, 0) == ()


class TestReadingAKeyAsARun:
    """Which keys in a pedals block are runs, and which are one pedal."""

    settings = PedalSettings()

    def test_a_pedals_own_name_is_that_pedal(self):
        assert self.settings.taps("left") == ()

    def test_several_names_are_a_run(self):
        assert self.settings.taps("left left") == ("left", "left")
        assert self.settings.taps("left middle right") == ("left", "middle", "right")

    def test_a_single_name_is_never_a_run(self):
        assert self.settings.taps("left ") == ()
        assert self.settings.taps(" left") == ()

    def test_a_pedals_own_name_wins_even_when_its_parts_are_pedals_too(self):
        odd = PedalSettings(buttons={"big": 4, "left": 5, "big left": 6})

        assert odd.taps("big left") == ()
        assert odd.taps("left big") == ("left", "big")

    def test_a_name_that_is_not_a_pedal_is_not_a_run(self):
        assert self.settings.taps("bogus") == ()
        assert self.settings.taps("left bogus") == ()

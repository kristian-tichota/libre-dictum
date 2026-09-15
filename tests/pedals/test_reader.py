from __future__ import annotations

from libre_dictum.pedals.reader import PedalEvent, PedalReader

BUTTONS = {"left": 4, "middle": 5, "right": 6}


def report(left: int = 0, middle: int = 0, right: int = 0) -> list[int]:
    """An eight-byte report with the three pedals in it."""
    return [0, 0, 0, 0, left, middle, right, 0]


def names(events: list[PedalEvent]) -> list[str]:
    """["left down", "right up"] -- what the assertions read."""
    return [event.describe() for event in events]


class TestEdges:
    def test_a_pedal_going_down_fires_once(self):
        reader = PedalReader(BUTTONS)

        assert names(reader.update(report(left=1))) == ["left down"]

    def test_a_pedal_held_down_does_not_fire_again(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))

        assert reader.update(report(left=1)) == []
        assert reader.update(report(left=1)) == []

    def test_lifting_it_fires_the_release(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))

        assert names(reader.update(report())) == ["left up"]

    def test_any_nonzero_value_is_down(self):
        reader = PedalReader(BUTTONS)

        assert names(reader.update(report(middle=200))) == ["middle down"]

    def test_two_pedals_in_one_report_fire_in_configuration_order(self):
        reader = PedalReader({"right": 6, "left": 4})

        assert names(reader.update(report(left=1, right=1))) == ["right down", "left down"]

    def test_it_remembers_which_pedals_are_down(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1, right=1))
        reader.update(report(right=1))

        assert reader.down == {"right"}


class TestAShortReport:
    def test_it_is_ignored_whole_rather_than_read_as_a_release(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))

        assert reader.update([0, 0, 0, 0, 1]) == []
        assert reader.down == {"left"}

    def test_the_length_a_reader_needs_is_the_last_offset_plus_one(self):
        assert PedalReader(BUTTONS).report_length == 7
        assert PedalReader({"only": 0}).report_length == 1
        assert PedalReader({}).report_length == 0


class TestReset:
    def test_it_hands_back_the_releases_a_dead_device_still_owes(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1, right=1))

        assert names(reader.reset()) == ["left up (owed)", "right up (owed)"]

    def test_an_owed_release_says_it_is_owed(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))

        (owed,) = reader.reset()
        assert owed.owed

    def test_a_foot_lifting_is_not_owed(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))

        (lifted,) = reader.update(report())
        assert not lifted.owed
        assert reader.down == set()

    def test_a_board_with_nothing_down_owes_nothing(self):
        assert PedalReader(BUTTONS).reset() == []

    def test_after_it_the_next_press_fires_again(self):
        reader = PedalReader(BUTTONS)
        reader.update(report(left=1))
        reader.reset()

        assert names(reader.update(report(left=1))) == ["left down"]

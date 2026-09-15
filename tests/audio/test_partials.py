import pytest

from libre_dictum.audio.partials import PartialTracker
from libre_dictum.pattern import PartialMatcher


@pytest.fixture
def tracker():
    return PartialTracker(PartialMatcher(["open terminal", "press {any}", "dictate mode"]))


class TestFiringEarly:
    def test_a_partial_ending_in_a_known_phrase_fires(self, tracker):
        assert tracker.on_partial("open terminal") == "open terminal"

    def test_a_partial_that_matches_nothing_fires_nothing(self, tracker):
        assert tracker.on_partial("open") is None

    def test_an_empty_partial_fires_nothing(self, tracker):
        assert tracker.on_partial("") is None

    def test_the_word_the_recognizer_heard_is_reported(self, tracker):
        assert tracker.on_partial("press enter") == "press enter"


class TestFiringOnce:
    def test_a_phrase_still_present_in_the_next_partial_does_not_fire_again(self, tracker):
        tracker.on_partial("open terminal")
        assert tracker.on_partial("open terminal") is None

    def test_a_second_command_in_the_same_utterance_fires(self, tracker):
        tracker.on_partial("open terminal")
        assert tracker.on_partial("open terminal press enter") == "press enter"

    def test_the_final_result_does_not_repeat_what_already_fired(self, tracker):
        tracker.on_partial("open terminal")
        assert tracker.on_final("open terminal") is None

    def test_the_final_result_reports_words_that_never_fired(self, tracker):
        tracker.on_partial("open terminal")
        assert tracker.on_final("open terminal and something else") == "and something else"

    def test_a_final_result_starts_the_next_utterance_fresh(self, tracker):
        tracker.on_partial("open terminal")
        tracker.on_final("open terminal")
        assert tracker.on_partial("open terminal") == "open terminal"


class TestReset:
    def test_reset_starts_the_next_utterance_fresh(self, tracker):
        tracker.on_partial("open terminal")
        tracker.reset()
        assert tracker.on_partial("open terminal") == "open terminal"

import pytest

from libre_dictum.tracking.hands import Hand
from libre_dictum.tracking.takes import (
    DEFAULT_SCRIPT,
    NEUTRAL,
    Frame,
    Prompt,
    Segmenter,
    build_plan,
    decode,
    encode,
    read_frames,
    remaining,
)

from .test_hands import hand


def feed(segmenter, presence, *, step=0.05, start=0.0):
    """Run a presence pattern through a segmenter."""
    closed = []
    for index, present in enumerate(presence):
        if (event := segmenter.feed(start + index * step, present)) is not None:
            closed.append(event)
    return closed


def pattern(*runs):
    """pattern((False, 20), (True, 40)) -> a presence list."""
    return [value for value, count in runs for _ in range(count)]


class TestWhenATakeStarts:
    def test_a_hand_after_a_settled_empty_frame_starts_one(self):
        segmenter = Segmenter()
        feed(segmenter, pattern((False, 20), (True, 20)))
        assert segmenter.recording

    def test_a_hand_already_in_shot_does_not(self):
        segmenter = Segmenter()
        feed(segmenter, pattern((True, 40)))
        assert not segmenter.recording

    def test_the_frame_has_to_be_empty_for_the_settle_period_first(self):
        segmenter = Segmenter(settle=1.0)
        feed(segmenter, pattern((False, 4), (True, 20)))
        assert not segmenter.recording

    def test_a_hand_seen_for_less_than_the_enter_grace_starts_nothing(self):
        segmenter = Segmenter(enter=1.0)
        feed(segmenter, pattern((False, 20), (True, 4)))
        assert not segmenter.recording


class TestWhenATakeEnds:
    def test_the_hand_leaving_closes_it(self):
        segmenter = Segmenter()
        closed = feed(segmenter, pattern((False, 20), (True, 40), (False, 20)))
        assert len(closed) == 1
        assert closed[0].kept

    def test_a_one_frame_dropout_does_not(self):
        segmenter = Segmenter()
        closed = feed(segmenter, pattern((False, 20), (True, 20), (False, 1), (True, 20)))
        assert closed == []
        assert segmenter.recording

    def test_the_span_stops_at_the_last_hand_not_at_the_grace_timeout(self):
        segmenter = Segmenter(exit=0.4)
        closed = feed(segmenter, pattern((False, 20), (True, 20), (False, 20)), step=0.05)
        assert closed[0].segment.seconds == pytest.approx(0.95, abs=0.01)

    def test_a_second_take_can_follow_the_first(self):
        segmenter = Segmenter()
        closed = feed(
            segmenter,
            pattern((False, 20), (True, 20), (False, 20), (True, 20), (False, 20)),
        )
        assert [event.kept for event in closed] == [True, True]


class TestScrappingATake:
    def test_a_span_shorter_than_the_minimum_is_not_kept(self):
        segmenter = Segmenter(minimum=1.0)
        closed = feed(segmenter, pattern((False, 20), (True, 6), (False, 20)))
        assert len(closed) == 1
        assert not closed[0].kept

    def test_a_scrapped_span_is_reported_rather_than_swallowed(self):
        segmenter = Segmenter(minimum=1.0)
        assert feed(segmenter, pattern((False, 20), (True, 6), (False, 20))) != []

    def test_the_prompt_re_arms_after_one(self):
        segmenter = Segmenter(minimum=1.0)
        feed(segmenter, pattern((False, 20), (True, 6), (False, 20), (True, 40)))
        assert segmenter.recording


class TestThePlan:
    def test_a_round_holds_one_take_of_every_label(self):
        plan = build_plan(("a", "b", "c"), 2)
        assert [prompt.label for prompt in plan] == ["a", "b", "c", "a", "b", "c"]

    def test_takes_are_numbered_per_label(self):
        plan = build_plan(("a", "b"), 3)
        assert [p.take for p in plan if p.label == "a"] == [1, 2, 3]

    def test_the_neutral_pass_comes_last_and_runs_on_a_clock(self):
        plan = build_plan(("a",), 2, neutral_seconds=60.0)
        assert plan[-1].label == NEUTRAL
        assert plan[-1].timed
        assert not plan[0].timed

    def test_no_neutral_pass_is_asked_for_when_none_is_wanted(self):
        assert all(prompt.label != NEUTRAL for prompt in build_plan(("a",), 2))

    def test_the_shipped_script_covers_both_hands_of_every_gesture(self):
        labels = {label for label, _ in DEFAULT_SCRIPT}
        for shape in ("fist", "pinch", "thumbs_up", "open"):
            assert f"left_{shape}" in labels and f"right_{shape}" in labels

    def test_every_scripted_gesture_says_what_to_do(self):
        assert all(instruction.strip() for _, instruction in DEFAULT_SCRIPT)


class TestResuming:
    def test_a_recorded_take_is_not_asked_for_again(self):
        plan = build_plan(("a", "b"), 2)
        left = remaining(plan, [("a", 1), ("b", 1)])
        assert [(p.label, p.take) for p in left] == [("a", 2), ("b", 2)]

    def test_a_take_the_file_does_not_hold_survives(self):
        assert remaining(build_plan(("a",), 2), []) == build_plan(("a",), 2)

    def test_a_take_recorded_under_a_plan_that_no_longer_exists_is_ignored(self):
        assert len(remaining(build_plan(("a",), 1), [("gone", 4)])) == 1


class TestTheRecordingFormat:
    def test_a_frame_survives_the_round_trip(self):
        frame = Frame(at=1.25, hands=(hand(side="left"),), label="left_fist", take=3)
        restored = decode(encode(frame))
        assert (restored.at, restored.label, restored.take) == (1.25, "left_fist", 3)
        assert restored.hands[0].side == "left"
        assert len(restored.hands[0].landmarks) == 21

    def test_landmarks_survive_to_three_decimals(self):
        original = hand()
        restored = decode(encode(Frame(at=0.0, hands=(original,))))
        for before, after in zip(original.landmarks, restored.hands[0].landmarks, strict=True):
            assert after == pytest.approx(before, abs=1e-3)

    def test_an_empty_frame_is_a_frame(self):
        restored = decode(encode(Frame(at=2.0, label="left_open")))
        assert restored.hands == () and not restored.present

    def test_a_resumed_frame_says_so(self):
        assert decode(encode(Frame(at=0.0, resumed=True))).resumed

    def test_presence_needs_a_whole_hand(self):
        assert not Frame(at=0.0, hands=(Hand(landmarks=[(0.0, 0.0, 0.0)]),)).present

    def test_blank_lines_are_skipped(self):
        assert len(list(read_frames(['{"t":0}', "", "  ", '{"t":1}']))) == 2

    def test_a_malformed_line_is_refused_rather_than_guessed_at(self):
        with pytest.raises(ValueError, match="not a recorded frame"):
            decode("{oh dear")

    def test_a_line_with_no_timestamp_is_refused(self):
        with pytest.raises(ValueError, match="no timestamp"):
            decode('{"l":"left_fist"}')


class TestPrompt:
    def test_a_gated_prompt_is_not_timed(self):
        assert not Prompt(label="a", take=1).timed

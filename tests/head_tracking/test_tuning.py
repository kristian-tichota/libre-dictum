import math

import pytest

from libre_dictum.tracking.hands import Calibration
from libre_dictum.tracking.takes import NEUTRAL, Frame
from libre_dictum.tracking.tuning import (
    Cell,
    definitions_of,
    held_above,
    held_below,
    propose,
    render,
    render_blocks,
    render_matrix,
    render_replay,
    replay,
    runs,
    score,
    summarise,
    windows_for,
)

from .test_hands import FOLDED, hand, hand_with_thumb_at, moved, pinched

STEP = 0.02


def series(values, *, step=STEP, start=0.0):
    """(time, value) samples at a fixed interval, from bare values."""
    return [(start + index * step, value) for index, value in enumerate(values)]


def hold(value, seconds, *, step=STEP):
    """value repeated for that many seconds."""
    return [value] * max(1, round(seconds / step))


def take(shape, *, label, number, at, seconds=1.2, approach=0.4, gap=0.6, step=STEP):
    """One take's frames: an empty pause, the hand rising into shot, the shape, nothing."""
    frames = []
    clock = at
    for _ in range(round(gap / step)):
        frames.append(Frame(at=clock, label=label))
        clock += step
    entering = round(approach / step)
    for index in range(entering):
        below = 0.25 * (1.0 - index / entering)
        hands = tuple(moved(one, by=(0.0, below)) for one in shape(0.0))
        frames.append(Frame(at=clock, hands=hands, label=label))
        clock += step
    for index in range(round(seconds / step)):
        hands = tuple(shape(index * step))
        frames.append(Frame(at=clock, hands=hands, label=label, take=number))
        clock += step
    return frames, clock


def fist(side):
    return lambda _seconds: [hand(bend=FOLDED, side=side)]


def open_hand(side):
    return lambda _seconds: [hand(bend=0.0, side=side)]


def thumbs_up(side):
    return lambda _seconds: [hand_with_thumb_at(10.0, bend=FOLDED, side=side)]


def pinch(side):
    """A hand opening and closing on the thumb, which is how a pinch is performed."""

    def shape(seconds):
        base = hand(bend=0.0, side=side)
        return [pinched(base) if (seconds % 0.34) < 0.17 else base]

    return shape


def recording(*, labels=("left_fist", "left_open"), takes=3):
    """A whole synthetic session: several takes of each label, round-robin."""
    shapes = {
        "left_fist": fist("left"),
        "right_fist": fist("right"),
        "left_open": open_hand("left"),
        "right_open": open_hand("right"),
        "left_thumbs_up": thumbs_up("left"),
        "right_thumbs_up": thumbs_up("right"),
        "left_pinch": pinch("left"),
        "right_pinch": pinch("right"),
    }
    frames = []
    clock = 0.0
    for number in range(1, takes + 1):
        for label in labels:
            block, clock = take(shapes[label], label=label, number=number, at=clock)
            frames.extend(block)
    return frames


class TestHeld:
    def test_a_level_held_for_the_window_is_reported(self):
        assert held_above(series(hold(0.8, 1.0)), 0.2) == pytest.approx(0.8)

    def test_a_spike_shorter_than_the_window_does_not_set_the_level(self):
        samples = series(hold(0.2, 1.0) + [0.95] + hold(0.2, 1.0))
        assert held_above(samples, 0.2) == pytest.approx(0.2)

    def test_the_best_window_wins_not_the_last_one(self):
        samples = series(hold(0.9, 0.5) + hold(0.3, 0.5))
        assert held_above(samples, 0.2) == pytest.approx(0.9)

    def test_a_series_shorter_than_the_window_held_nothing(self):
        assert held_above(series(hold(0.9, 0.1)), 0.5) == 0.0

    def test_a_longer_window_can_only_ask_for_less(self):
        samples = series(hold(0.4, 0.3) + hold(0.9, 0.3) + hold(0.4, 0.3))
        assert held_above(samples, 0.2) >= held_above(samples, 0.6)

    def test_no_samples_held_nothing(self):
        assert held_above([], 0.2) == 0.0

    def test_a_window_of_zero_is_the_plain_peak(self):
        assert held_above(series([0.1, 0.95, 0.1]), 0.0) == pytest.approx(0.95)


class TestHeldBelow:
    def test_a_level_stayed_under_is_reported(self):
        assert held_below(series(hold(0.15, 1.0)), 0.2) == pytest.approx(0.15)

    def test_a_single_dip_does_not_set_it(self):
        samples = series(hold(0.7, 1.0) + [0.05] + hold(0.7, 1.0))
        assert held_below(samples, 0.2) == pytest.approx(0.7)

    def test_it_is_the_mirror_of_held_above(self):
        values = [0.1, 0.4, 0.9, 0.3, 0.8, 0.2, 0.2, 0.2, 0.2, 0.7, 0.6]
        mirrored = [1.0 - value for value in values]
        assert held_below(series(values), 0.06) == pytest.approx(
            1.0 - held_above(series(mirrored), 0.06)
        )

    def test_values_outside_the_unit_range_are_handled(self):
        assert held_below(series(hold(1.4, 1.0)), 0.2) == pytest.approx(1.4)


class TestRuns:
    def test_a_continuous_stretch_is_one_run(self):
        assert len(runs([Frame(at=index * STEP) for index in range(50)])) == 1

    def test_a_gap_splits_it(self):
        frames = [Frame(at=0.0), Frame(at=0.02), Frame(at=9.0), Frame(at=9.02)]
        assert len(runs(frames)) == 2

    def test_a_resumed_frame_splits_it_however_close_the_clock_is(self):
        frames = [Frame(at=0.0), Frame(at=0.02, resumed=True)]
        assert len(runs(frames)) == 2

    def test_nothing_is_no_runs(self):
        assert runs([]) == ()


class TestSummary:
    @pytest.fixture
    def summary(self):
        return summarise(score(recording()))

    def test_every_label_with_a_take_is_summarised(self, summary):
        assert set(summary.labels) == {"left_fist", "left_open"}

    def test_one_value_per_take(self, summary):
        assert len(summary.cell("left_fist", "leftHandFist").at(summary.window)) == 3

    def test_a_fist_take_holds_a_high_fist_score(self, summary):
        assert summary.cell("left_fist", "leftHandFist").worst_high(summary.window) > 0.8

    def test_an_open_take_does_not(self, summary):
        assert summary.cell("left_open", "leftHandFist").peak < 0.3

    def test_the_peak_covers_the_approach_as_well_as_the_take(self):
        summary = summarise(score(recording(labels=("left_fist",))))
        cell = summary.cell("left_fist", "leftHandFist")
        assert cell.peak >= cell.worst_high(summary.window)

    def test_a_longer_window_never_asks_for_more(self, summary):
        cell = summary.cell("left_fist", "leftHandFist")
        assert cell.worst_high(summary.long_window) <= cell.worst_high(summary.window) + 1e-9

    def test_a_gesture_is_measured_at_the_dwell_it_asks_for(self):
        scored = score(recording(labels=("left_pinch",)))
        summary = summarise(
            scored,
            windows=windows_for({"left_pinch": {"hold": 120, "leftHandPinchIndex": {"min": 0.8}}}),
        )
        cell = summary.cell("left_pinch", "leftHandPinchIndex")
        assert cell.worst_high(0.12) > 0.8
        assert cell.worst_high(0.4) < 0.5

    def test_only_the_dwells_asked_for_are_measured(self):
        assert summarise(score(recording()), windows=(0.1, 0.3)).windows == (0.1, 0.3)

    def test_the_hand_that_is_not_there_is_scored_zero_not_left_out(self, summary):
        assert summary.cell("left_fist", "rightHandFist").peak == pytest.approx(0.0)

    def test_the_neutral_pass_sorts_last(self):
        frames, clock = take(fist("left"), label=NEUTRAL, number=1, at=0.0)
        more, _ = take(fist("left"), label="left_fist", number=1, at=clock)
        assert summarise(score(frames + more)).labels[-1] == NEUTRAL

    def test_takes_are_counted_and_timed(self, summary):
        info = summary.diagnostics["left_fist"]
        assert info.takes == 3
        assert info.median_seconds == pytest.approx(1.2, abs=0.1)

    def test_a_one_handed_take_is_not_reported_as_two_handed(self, summary):
        assert summary.diagnostics["left_fist"].two_handed == 0

    def test_an_empty_cell_is_answered_rather_than_raising(self, summary):
        assert summary.cell("left_fist", "handNoSuchThing") == Cell({}, {}, 0.0, 0.0)


class TestRatios:
    def test_a_fist_folds_the_fingers_further_than_an_open_hand(self):
        summary = summarise(score(recording()))
        folded = summary.measurements["left_fist"]["extIndex"]
        straight = summary.measurements["left_open"]["extIndex"]
        assert max(folded) < min(straight)

    def test_the_side_the_label_names_is_the_one_measured(self):
        summary = summarise(score(recording(labels=("right_fist", "right_open"))))
        assert summary.measurements["right_fist"]["extIndex"]


class TestProposal:
    def test_the_fold_constant_comes_off_the_fist_takes(self):
        summary = summarise(score(recording()))
        proposed = propose(summary).calibration
        measured = summary.measurements["left_fist"]["extIndex"]
        middle = sorted(measured)[len(measured) // 2]
        assert proposed.folded_extension == pytest.approx(middle, abs=0.05)

    def test_a_constant_nothing_measures_keeps_the_value_it_came_in_with(self):
        summary = summarise(score(recording(labels=("left_fist",))))
        proposal = propose(summary, calibration=Calibration(pinch_closed=0.42))
        assert proposal.calibration.pinch_closed == 0.42
        assert any(entry.startswith("pinch_closed") for entry in proposal.missing)

    def test_a_proposal_that_would_not_load_backs_the_offending_pair_out(self):
        summary = summarise(score(recording(labels=("left_fist", "left_open"))))
        proposal = propose(summary)
        assert proposal.calibration.problems() == []
        assert any("would not load" in entry for entry in proposal.missing)

    def test_every_derived_constant_says_where_it_came_from(self):
        proposal = propose(summarise(score(recording())))
        assert {key for key, _ in proposal.sources}
        assert all(why.strip() for _, why in proposal.sources)

    def test_the_proposal_is_a_usable_calibration(self):
        assert propose(summarise(score(recording()))).calibration.problems() == []


class TestReplay:
    DEFINITION = {"left_fist": {"hold": 150, "leftHandFist": {"min": 0.8, "release": 0.4}}}

    def test_a_gesture_fires_during_its_own_take(self):
        result = replay(score(recording()), self.DEFINITION)
        assert [f.label for f in result.fires] == ["left_fist"] * 3

    def test_every_take_is_covered_even_when_the_fire_began_on_the_approach(self):
        result = replay(score(recording()), self.DEFINITION)
        covered = [
            result.covering("left_fist", start, end) for _, start, end in result.takes["left_fist"]
        ]
        assert all(covered)

    def test_a_threshold_nothing_reaches_fires_nothing(self):
        impossible = {"left_fist": {"rightHandFist": {"min": 0.5}}}
        assert replay(score(recording()), impossible).fires == ()

    def test_a_condition_the_wrong_shape_also_meets_fires_in_its_take(self):
        wrong = {"left_fist": {"leftHandOpen": {"min": 0.5, "release": 0.2}}}
        labels = {f.label for f in replay(score(recording()), wrong).fires}
        assert "left_open" in labels

    def test_a_release_is_timed(self):
        result = replay(score(recording()), self.DEFINITION)
        assert all(f.duration is not None and f.duration > 0.5 for f in result.fires)

    PINCH = {"left_pinch": {"hold": 120, "leftHandPinchIndex": {"min": 0.8, "release": 0.4}}}

    def test_the_stride_is_the_session_rate_not_the_recording_rate(self):
        scored = score(recording(labels=("left_pinch",)))
        every = replay(scored, self.PINCH, stride=1).fires
        coarse = replay(scored, self.PINCH, stride=30).fires
        assert len(every) >= 3
        assert len(coarse) < len(every) / 2

    def test_the_stride_it_ran_at_is_reported(self):
        assert replay(score(recording()), self.DEFINITION, stride=4).stride == 4

    def test_a_hold_longer_than_any_take_never_fires(self):
        slow = {"left_fist": {"hold": 9000, "leftHandFist": {"min": 0.8}}}
        assert replay(score(recording()), slow).fires == ()

    def test_a_resumed_recording_resets_the_recognizer(self):
        shape = fist("left")(0.0)[0]
        frames = [
            Frame(at=index * STEP, hands=(shape,), label="left_fist", take=1) for index in range(60)
        ] + [
            Frame(
                at=90.0 + index * STEP,
                hands=(shape,),
                label="left_fist",
                take=2,
                resumed=index == 0,
            )
            for index in range(60)
        ]
        assert len(replay(score(frames), self.DEFINITION).fires) == 2


class TestRendering:
    @pytest.fixture
    def summary(self):
        return summarise(score(recording(labels=("left_fist", "left_open", "left_thumbs_up"))))

    def test_the_matrix_has_a_column_per_label(self, summary):
        heading = _heading(render_matrix(summary))
        assert len(heading.split()) == len(summary.labels)

    def test_a_shortened_column_says_what_it_stands_for(self, summary):
        text = render_matrix(summary)
        assert "= left_thumbs_up" in text
        assert all(word in _heading(text) for word in ("l.fist", "l.open"))

    def test_a_feature_that_reads_the_same_everywhere_is_dropped_and_named(self, summary):
        text = render_matrix(summary)
        assert "flat across every label" in text
        assert "R.Fist" in text.split("flat across every label")[1]

    def test_a_discriminating_feature_keeps_its_row(self, summary):
        assert any(line.startswith("L.Fist ") for line in render_matrix(summary).splitlines())

    def test_a_block_shows_one_value_per_take(self, summary):
        definitions = {"left_fist": {"leftHandFist": {"min": 0.8}}}
        line = _row(render_blocks(summary, definitions), "left_fist", "L.Fist")
        numbers = [part for part in line.split() if part.startswith((".", "1.", "0."))]
        assert len(numbers) >= summary.diagnostics["left_fist"].takes

    def test_a_configured_feature_is_shown_even_when_it_discriminates_nothing(self, summary):
        definitions = {"left_fist": {"rightHandSpread": {"min": 0.5}}}
        assert _row(render_blocks(summary, definitions), "left_fist", "R.Spread")

    def test_the_neutral_pass_gets_no_block_of_its_own(self):
        frames, clock = take(fist("left"), label=NEUTRAL, number=1, at=0.0)
        more, _ = take(fist("left"), label="left_fist", number=1, at=clock)
        assert f"### {NEUTRAL}" not in render_blocks(summarise(score(frames + more)), {})

    def test_the_whole_report_carries_every_section(self, summary):
        text = render(summary, {}, proposal=propose(summary))
        for heading in ("## takes", "## matrix", "## per gesture", "## ratios", "## proposed"):
            assert heading in text

    def test_the_report_stays_readable_in_a_terminal(self, summary):
        longest = max(len(line) for line in render(summary, {}).splitlines())
        assert longest <= 120, f"widest line is {longest} characters"

    def test_the_replay_names_what_fired_where(self, summary):
        definitions = {"left_fist": {"leftHandFist": {"min": 0.8, "release": 0.4}}}
        scored = score(recording(labels=("left_fist", "left_open")))
        assert "left_fist" in render_replay(replay(scored, definitions))


class TestChoosingWhatToReplay:
    def test_a_hand_gesture_is_kept(self):
        payload = {"ht_custom_gestures": {"g": {"leftHandFist": {"min": 0.8}}}}
        assert "g" in definitions_of(payload)

    def test_a_facial_gesture_is_dropped(self):
        payload = {"ht_custom_gestures": {"g": {"eyeBlinkLeft": {"min": 0.8}}}}
        assert definitions_of(payload) == {}

    def test_a_mixed_gesture_is_dropped_rather_than_half_measured(self):
        mixed = {"leftHandFist": {"min": 0.8}, "jawOpen": {"min": 0.5}}
        assert definitions_of({"ht_custom_gestures": {"g": mixed}}) == {}

    def test_a_hold_is_not_mistaken_for_a_feature(self):
        payload = {"ht_custom_gestures": {"g": {"hold": 200, "leftHandFist": {"min": 0.8}}}}
        assert "g" in definitions_of(payload)

    def test_a_config_with_no_gestures_yields_none(self):
        assert definitions_of({}) == {}


def _heading(text):
    """The matrix's column-heading line: the one that is only column names."""
    return next(line for line in text.splitlines() if line.startswith("       ") and "." in line)


def _row(text, block, feature):
    """The line for one feature inside one gesture's block, or ""."""
    section = text.split(f"### {block}")[-1].split("### ")[0]
    return next((line for line in section.splitlines() if line.startswith(f"{feature} ")), "")


def test_the_synthetic_thumbs_up_really_is_one():
    """The fixture has to make the shape, or every test above measures nothing."""
    scored = score([Frame(at=0.0, hands=(thumbs_up("left")(1.0)[0],))])
    assert scored[0].scores["leftHandThumbPointingUp"] > 0.6
    assert scored[0].scores["leftHandFist"] > 0.7
    assert math.isclose(scored[0].scores["handPresent"], 1.0)

import math

import pytest

from libre_dictum.tracking.gestures import GestureRecognizer
from libre_dictum.tracking.hands import (
    HAND_FEATURES,
    SHAPE_FEATURES,
    Calibration,
    Hand,
    PrimaryHand,
    ratios,
    report,
    scores,
    side_feature,
    user_side,
)

BONES = (0.055, 0.035, 0.025)

KNUCKLES = ((0.44, 0.72), (0.47, 0.65), (0.50, 0.64), (0.53, 0.65), (0.56, 0.67))

WRIST_AT = (0.50, 0.80)

FOLDED = math.radians(105.0)

THUMB_OUT_DEGREES = 55.0
THUMB_OUT = math.radians(THUMB_OUT_DEGREES)


def _chain(origin, bend, bones=BONES, start=0.0):
    """A finger from origin, each bone turning bend further than the last."""
    points = [(*origin, 0.0)]
    angle = -math.pi / 2.0 + start
    for length in bones:
        angle += bend
        x, y, _ = points[-1]
        points.append((x + length * math.cos(angle), y + length * math.sin(angle), 0.0))
    return points


def hand(*, bend=0.0, thumb_bend=None, side="right", spread=1.0, bones=BONES):
    """A synthetic hand."""
    centre = sum(x for x, _ in KNUCKLES) / len(KNUCKLES)
    knuckles = [(centre + (x - centre) * spread, y) for x, y in KNUCKLES]
    landmarks = [(*WRIST_AT, 0.0)]
    bends = (thumb_bend if thumb_bend is not None else bend, bend, bend, bend, bend)
    starts = (THUMB_OUT, 0.0, 0.0, 0.0, 0.0)
    for knuckle, joint_bend, start in zip(knuckles, bends, starts, strict=True):
        landmarks.extend(_chain(knuckle, joint_bend, bones, start))
    return Hand(landmarks=landmarks, side=side)


def hand_with_thumb_at(degrees_off_vertical, *, bend=FOLDED, side="right"):
    """A folded hand whose thumb leaves the palm at a chosen angle."""
    landmarks = [(*WRIST_AT, 0.0)]
    starts = (math.radians(degrees_off_vertical), 0.0, 0.0, 0.0, 0.0)
    bends = (0.0, bend, bend, bend, bend)
    for knuckle, joint_bend, start in zip(KNUCKLES, bends, starts, strict=True):
        landmarks.extend(_chain(knuckle, joint_bend, BONES, start))
    return Hand(landmarks=landmarks, side=side)


def flipped(subject):
    """The same hand upside down, which is what a thumbs-down is."""
    return Hand(
        landmarks=[(x, 2 * WRIST_AT[1] - y, z) for x, y, z in subject.landmarks],
        side=subject.side,
    )


def moved(subject, *, by=(0.0, 0.0), scale=1.0):
    """The same hand somewhere else, and a different size -- what a camera actually sees."""
    return Hand(
        landmarks=[
            (x * scale + by[0], y * scale + by[1], z * scale) for x, y, z in subject.landmarks
        ],
        side=subject.side,
    )


def pinched(subject, *, onto=8):
    """The same hand with the thumb tip laid on one fingertip."""
    landmarks = list(subject.landmarks)
    landmarks[4] = landmarks[onto]
    return Hand(landmarks=landmarks, side=subject.side)


class TestCurl:
    def test_a_straight_finger_does_not_curl(self):
        assert scores([hand(bend=0.0)])["handIndexCurl"] == pytest.approx(0.0)

    def test_a_folded_finger_curls_fully(self):
        assert scores([hand(bend=FOLDED)])["handIndexCurl"] == pytest.approx(1.0, abs=0.05)

    def test_curl_rises_with_the_fold(self):
        curls = [
            scores([hand(bend=math.radians(degrees))])["handIndexCurl"]
            for degrees in (0, 15, 30, 45, 60)
        ]
        assert curls == sorted(curls)
        assert curls[0] < curls[-1]

    def test_every_finger_is_scored(self):
        reported = scores([hand(bend=0.4)])
        for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
            assert f"hand{finger}Curl" in reported

    def test_the_thumb_is_scored_against_its_own_shorter_travel(self):
        reported = scores([hand(bend=math.radians(45), thumb_bend=math.radians(45))])
        assert reported["handThumbCurl"] > reported["handIndexCurl"] + 0.15

    def test_a_relaxed_thumb_does_not_read_as_folded(self):
        assert scores([hand(bend=0.0)])["handThumbCurl"] == pytest.approx(0.0)


class TestFistAndOpen:
    def test_a_straight_hand_is_open(self):
        reported = scores([hand(bend=0.0)])
        assert reported["handOpen"] == pytest.approx(1.0)
        assert reported["handFist"] == pytest.approx(0.0)

    def test_a_folded_hand_is_a_fist(self):
        reported = scores([hand(bend=FOLDED)])
        assert reported["handFist"] > 0.9
        assert reported["handOpen"] < 0.1

    def test_one_extended_finger_is_neither(self):
        subject = hand(bend=FOLDED)
        landmarks = list(subject.landmarks)
        landmarks[5:9] = _chain(KNUCKLES[1], 0.0)
        reported = scores([Hand(landmarks=[(*WRIST_AT, 0.0), *landmarks[1:]], side="right")])
        assert reported["handFist"] < 0.1
        assert reported["handOpen"] < 0.1

    def test_the_thumb_does_not_decide_a_fist(self):
        outside = scores([hand(bend=FOLDED, thumb_bend=0.0)])["handFist"]
        tucked = scores([hand(bend=FOLDED, thumb_bend=FOLDED)])["handFist"]
        assert outside == pytest.approx(tucked)


class TestPinch:
    def test_touching_the_index_tip_is_a_full_pinch(self):
        assert scores([pinched(hand())])["handPinchIndex"] == pytest.approx(1.0)

    def test_an_open_hand_is_not_pinching(self):
        assert scores([hand(bend=0.0, spread=1.6)])["handPinchIndex"] == pytest.approx(0.0)

    def test_the_middle_finger_is_pinched_separately(self):
        reported = scores([pinched(hand(), onto=12)])
        assert reported["handPinchMiddle"] == pytest.approx(1.0)
        assert reported["handPinchMiddle"] > reported["handPinchIndex"]


class TestSpread:
    def test_splayed_fingers_spread_further_than_closed_ones(self):
        assert scores([hand(spread=2.2)])["handSpread"] > scores([hand(spread=0.4)])["handSpread"]


class TestPointing:
    def test_a_hand_pointing_up_the_frame_points_up(self):
        reported = scores([hand(bend=0.0)])
        assert reported["handPointingUp"] == pytest.approx(1.0)
        assert reported["handPointingDown"] == pytest.approx(0.0)

    def test_pointing_down_is_the_opposite(self):
        reported = scores([hand(bend=math.pi / 3.0)])
        assert reported["handPointingDown"] > reported["handPointingUp"]

    def test_left_and_right_are_the_users_not_the_frames(self):
        subject = hand()
        landmarks = list(subject.landmarks)
        knuckle = landmarks[5]
        landmarks[8] = (knuckle[0] - 0.1, knuckle[1], 0.0)
        reported = scores([Hand(landmarks=landmarks, side="right")])
        assert reported["handPointingRight"] == pytest.approx(1.0)
        assert reported["handPointingLeft"] == pytest.approx(0.0)

    def test_a_finger_with_no_length_points_nowhere(self):
        subject = hand()
        landmarks = list(subject.landmarks)
        landmarks[8] = landmarks[5]
        reported = scores([Hand(landmarks=landmarks, side="right")])
        assert reported["handPointingUp"] == 0.0
        assert reported["handPointingDown"] == 0.0


class TestThumbDirection:
    """Which way the thumb points."""

    def test_a_thumb_up_the_frame_points_up(self):
        reported = scores([hand()])

        assert reported["handThumbPointingUp"] > 0.5
        assert reported["handThumbPointingDown"] == 0.0

    def test_turning_the_hand_over_turns_the_thumb_over(self):
        upright = scores([hand()])
        inverted = scores([flipped(hand())])

        assert inverted["handThumbPointingDown"] == pytest.approx(upright["handThumbPointingUp"])
        assert inverted["handThumbPointingUp"] == 0.0

    def test_the_thumb_and_the_index_are_scored_apart(self):
        reported = scores([hand(bend=FOLDED, thumb_bend=0.0)])

        assert reported["handThumbPointingUp"] > 0.5
        assert reported["handPointingUp"] < reported["handThumbPointingUp"]

    def test_a_thumbs_up_is_a_fist_with_a_straight_thumb(self):
        reported = scores([hand(bend=FOLDED, thumb_bend=0.0)])

        assert reported["handFist"] > 0.7
        assert reported["handThumbCurl"] < 0.35
        assert reported["handThumbPointingUp"] > 0.5

    def test_a_closed_fist_is_not_a_thumbs_up(self):
        reported = scores([hand(bend=FOLDED)])

        assert reported["handFist"] > 0.7
        assert reported["handThumbCurl"] > 0.35

    def test_a_thumb_held_up_scores_well_above_one_merely_resting_out(self):
        held_up = scores([hand_with_thumb_at(15.0)])["handThumbPointingUp"]
        resting = scores([hand_with_thumb_at(THUMB_OUT_DEGREES)])["handThumbPointingUp"]

        assert held_up > 0.9
        assert resting < 0.6
        assert held_up - resting > 0.3

    def test_the_score_falls_away_smoothly_as_the_thumb_leaves_vertical(self):
        by_angle = [scores([hand_with_thumb_at(d)])["handThumbPointingUp"] for d in (0, 30, 60, 90)]

        assert by_angle == sorted(by_angle, reverse=True)
        assert by_angle[0] == pytest.approx(1.0)
        assert by_angle[-1] == pytest.approx(0.0)

    def test_a_thumb_of_no_length_points_nowhere(self):
        reported = scores([Hand(landmarks=[(0.5, 0.5, 0.0)] * 21, side="right")])

        assert reported["handThumbPointingUp"] == 0.0
        assert reported["handThumbPointingLeft"] == 0.0

    def test_the_direction_is_scored_per_hand_too(self):
        reported = scores([hand(side="right")])

        assert reported["rightHandThumbPointingUp"] == reported["handThumbPointingUp"]


POSITION = ("handHigh", "handNear")


def shape_only(reported):
    """Every score except the two positional ones, which are allowed to move."""
    return {
        name: value
        for name, value in reported.items()
        if not any(name.endswith(p[0].upper() + p[1:]) or name == p for p in POSITION)
    }


class TestScaleAndPosition:
    """Every score is a ratio, so hand size and place cannot matter."""

    def test_moving_the_hand_changes_no_shape_score(self):
        subject = hand(bend=0.5)
        moved_away = scores([moved(subject, by=(-0.3, 0.15))])
        assert shape_only(moved_away) == pytest.approx(shape_only(scores([subject])))

    def test_a_hand_twice_the_size_scores_the_same_shape(self):
        subject = hand(bend=0.5)
        assert shape_only(scores([moved(subject, scale=2.0)])) == pytest.approx(
            shape_only(scores([subject]))
        )

    def test_a_smaller_hand_scores_the_same_shape(self):
        subject = hand(bend=0.5)
        assert shape_only(scores([moved(subject, scale=0.4)])) == pytest.approx(
            shape_only(scores([subject]))
        )

    def test_every_score_is_covered_by_one_rule_or_the_other(self):
        reported = scores([hand(bend=0.5)])
        positional = set(reported) - set(shape_only(reported))
        assert positional == {
            "handHigh",
            "handNear",
            "rightHandHigh",
            "rightHandNear",
            "leftHandHigh",
            "leftHandNear",
        }


class TestWhereTheHandIs:
    """The two positional scores, which exist because no shape score could do their job."""

    def test_a_hand_higher_up_the_frame_scores_higher(self):
        low = hand()
        assert scores([moved(low, by=(0.0, -0.3))])["handHigh"] > scores([low])["handHigh"]

    def test_the_bottom_of_the_frame_is_zero_and_the_top_is_one(self):
        assert scores([moved(hand(), by=(0.0, 0.9))])["handHigh"] == 0.0
        assert scores([moved(hand(), by=(0.0, -0.9))])["handHigh"] == 1.0

    def test_height_is_read_off_the_wrist_not_the_fingertips(self):
        assert scores([hand(bend=0.0)])["handHigh"] == pytest.approx(
            scores([hand(bend=FOLDED)])["handHigh"]
        )

    def test_a_bigger_hand_reads_as_nearer(self):
        wide = Calibration(near_span=0.5, far_span=0.05)
        subject = hand()
        assert (
            scores([moved(subject, scale=2.0)], calibration=wide)["handNear"]
            > scores([subject], calibration=wide)["handNear"]
        )

    def test_near_is_calibrated_at_both_ends(self):
        subject = hand()
        span = ratios(subject).palm_span
        wide = Calibration(near_span=span, far_span=span / 4)
        assert scores([subject], calibration=wide)["handNear"] == pytest.approx(1.0)
        far = Calibration(near_span=span * 4, far_span=span)
        assert scores([subject], calibration=far)["handNear"] == pytest.approx(0.0)

    def test_a_calibration_that_would_pin_it_at_zero_is_refused(self):
        assert Calibration(near_span=0.1, far_span=0.1).problems()

    def test_both_are_reported_per_hand_as_well(self):
        reported = scores([hand(side="left")])
        assert reported["leftHandHigh"] == reported["handHigh"]
        assert reported["leftHandNear"] == reported["handNear"]

    def test_the_raw_height_travels_with_the_ratios(self):
        assert ratios(moved(hand(), by=(0.0, -0.2))).height > ratios(hand()).height


class TestSides:
    def test_a_right_hand_reports_right_suffixed_scores(self):
        reported = scores([hand(side="right")])
        assert reported["handIsRight"] == 1.0
        assert reported["handIsLeft"] == 0.0
        assert reported["rightHandIndexCurl"] > 0.0 or "rightHandIndexCurl" in reported

    def test_the_hand_that_is_not_there_is_answered_rather_than_left_out(self):
        reported = scores([hand(side="right", bend=FOLDED)])

        assert reported["rightHandFist"] > 0.8
        assert reported["leftHandFist"] == 0.0
        assert reported["leftHandOpen"] == 0.0

    def test_a_hand_of_unknown_handedness_claims_neither_side(self):
        reported = scores([hand(side="", bend=FOLDED)])

        assert reported["handPresent"] == 1.0
        assert reported["handFist"] > 0.8
        assert (reported["handIsLeft"], reported["handIsRight"]) == (0.0, 0.0)
        assert reported["leftHandFist"] == reported["rightHandFist"] == 0.0

    def test_handedness_is_read_case_insensitively(self):
        assert scores([hand(side="Left")])["handIsLeft"] == 1.0


class TestTwoHands:
    def test_both_hands_are_reported(self):
        reported = scores([hand(side="left", bend=0.0), hand(side="right", bend=FOLDED)])
        assert reported["bothHandsPresent"] == 1.0
        assert reported["leftHandIndexCurl"] < reported["rightHandIndexCurl"]

    def test_one_hand_does_not_claim_both(self):
        assert scores([hand()])["bothHandsPresent"] == 0.0

    def test_the_unsuffixed_scores_are_the_first_hand_not_a_maximum(self):
        reported = scores([hand(side="left", bend=0.0), hand(side="right", bend=FOLDED)])
        assert reported["handIndexCurl"] == pytest.approx(reported["leftHandIndexCurl"])
        assert reported["handIsLeft"] == 1.0


class TestNoHand:
    def test_no_hands_score_nothing(self):
        assert scores([]) == {}

    def test_a_score_is_absent_rather_than_zero(self):
        assert "handIndexCurl" not in scores([])

    def test_a_short_landmark_list_is_a_dropped_hand(self):
        assert scores([Hand(landmarks=[(0.5, 0.5, 0.0)] * 20)]) == {}

    def test_a_dropped_hand_does_not_hide_a_good_one(self):
        reported = scores([Hand(landmarks=[(0.5, 0.5, 0.0)] * 3), hand(side="right")])
        assert reported["handPresent"] == 1.0
        assert reported["bothHandsPresent"] == 0.0


class TestDegenerate:
    def test_a_collapsed_hand_scores_without_raising(self):
        reported = scores([Hand(landmarks=[(0.5, 0.5, 0.0)] * 21, side="right")])
        assert all(0.0 <= score <= 1.0 for score in reported.values())

    def test_every_score_stays_inside_the_unit_range(self):
        for degrees in range(0, 121, 10):
            for spread in (0.1, 1.0, 3.0):
                reported = scores([hand(bend=math.radians(degrees), spread=spread)])
                assert all(0.0 <= score <= 1.0 for score in reported.values()), reported


class TestFeatureNames:
    """The exported names are what a load-time check validates a configuration against."""

    def test_every_reported_name_is_declared(self):
        reported = scores([hand(side="left"), hand(side="right")])
        assert set(reported) <= set(HAND_FEATURES)

    def test_no_name_is_declared_twice(self):
        assert len(HAND_FEATURES) == len(set(HAND_FEATURES))

    def test_a_shape_score_exists_for_both_hands(self):
        for name in SHAPE_FEATURES:
            assert side_feature("left", name) in HAND_FEATURES
            assert side_feature("right", name) in HAND_FEATURES

    def test_which_hand_is_never_prefixed(self):
        for name in ("handPresent", "handIsLeft", "handIsRight"):
            assert name not in SHAPE_FEATURES
            assert side_feature("left", name) not in HAND_FEATURES

    def test_a_direction_keeps_its_side_and_the_hand_keeps_its_own(self):
        assert "rightHandPointingLeft" in HAND_FEATURES
        assert "handPointingLeftRight" not in HAND_FEATURES


class TestCalibration:
    def test_a_calibration_shifts_where_a_finger_reads_as_folded(self):
        subject = hand(bend=math.radians(40))
        loose = scores([subject], calibration=Calibration(folded_extension=0.8))
        tight = scores([subject], calibration=Calibration(folded_extension=0.1))
        assert loose["handIndexCurl"] > tight["handIndexCurl"]

    def test_equal_endpoints_score_zero_rather_than_dividing_by_zero(self):
        reported = scores([hand()], calibration=Calibration(pinch_open=0.3, pinch_closed=0.3))
        assert reported["handPinchIndex"] == 0.0


class TestWithTheGestureRecognizer:
    """The point of the module: hands reuse the face path's machine, unchanged."""

    FIST = {"fist": {"handFist": {"min": 0.8, "release": 0.4}}}

    def test_a_hand_gesture_fires_through_the_existing_recognizer(self):
        recognizer = GestureRecognizer(self.FIST)
        assert recognizer.update(scores([hand(bend=FOLDED)]))[0] == ["fist"]

    def test_a_held_fist_fires_once(self):
        recognizer = GestureRecognizer(self.FIST)
        recognizer.update(scores([hand(bend=FOLDED)]))
        assert recognizer.update(scores([hand(bend=FOLDED)]))[0] == []

    def test_opening_the_hand_re_arms_it(self):
        recognizer = GestureRecognizer(self.FIST)
        recognizer.update(scores([hand(bend=FOLDED)]))
        recognizer.update(scores([hand(bend=0.0)]))
        assert recognizer.update(scores([hand(bend=FOLDED)]))[0] == ["fist"]

    def test_a_hand_leaving_the_frame_re_arms_it(self):
        recognizer = GestureRecognizer(self.FIST)
        recognizer.update(scores([hand(bend=FOLDED)]))
        recognizer.update(scores([]))
        assert recognizer.update(scores([hand(bend=FOLDED)]))[0] == ["fist"]

    def test_a_gesture_can_require_one_particular_hand(self):
        recognizer = GestureRecognizer({"left_fist": {"leftHandFist": {"min": 0.8}}})
        assert recognizer.update(scores([hand(side="right", bend=FOLDED)]))[0] == []
        assert recognizer.update(scores([hand(side="left", bend=FOLDED)]))[0] == ["left_fist"]

    def test_a_gesture_can_combine_a_hand_with_a_face(self):
        recognizer = GestureRecognizer(
            {"shout": {"handFist": {"min": 0.8}, "jawOpen": {"min": 0.5}}}
        )
        assert recognizer.update({**scores([hand(bend=FOLDED)]), "jawOpen": 0.7})[0] == ["shout"]


class TestWhichHandIsTheUsers:
    """Whether mediapipe's handedness label needs turning round."""

    def test_the_label_already_names_the_users_own_side(self):
        assert user_side("Left") == "left"
        assert user_side("Right") == "right"

    def test_the_label_is_read_case_and_space_insensitively(self):
        assert user_side("  LEFT ") == "left"

    def test_a_label_that_names_no_side_stays_unrecognised(self):
        assert user_side("Middle") == "middle"
        assert Hand(landmarks=hand().landmarks, side=user_side("Middle")).known_side == ""

    def test_applying_it_twice_changes_nothing(self):
        for label in ("Left", "Right"):
            assert user_side(user_side(label)) == label.lower()


class TestPrimaryHand:
    """Which hand the unprefixed scores describe, and why it must not change by itself."""

    def test_one_hand_is_the_primary(self):
        chooser = PrimaryHand()

        assert chooser.order([hand(side="left")])[0].known_side == "left"

    def test_the_nearer_hand_starts_as_primary(self):
        near, far = hand(side="right"), moved(hand(side="left"), scale=0.5)

        assert PrimaryHand().order([far, near])[0].known_side == "right"

    def test_the_primary_hand_keeps_the_job_while_it_is_visible(self):
        chooser = PrimaryHand()
        near, far = hand(side="right"), moved(hand(side="left"), scale=0.5)
        chooser.order([far, near])

        closer = hand(side="left")
        assert chooser.order([closer, moved(near, scale=0.4)])[0].known_side == "right"

    def test_the_job_passes_on_when_that_hand_leaves(self):
        chooser = PrimaryHand()
        chooser.order([hand(side="right")])

        assert chooser.order([hand(side="left")])[0].known_side == "left"

    def test_resetting_lets_the_next_frame_choose_afresh(self):
        chooser = PrimaryHand()
        chooser.order([moved(hand(side="right"), scale=0.5), hand(side="left")])
        chooser.reset()

        assert (
            chooser.order([moved(hand(side="right"), scale=0.5), hand(side="left")])[0].known_side
            == "left"
        )

    def test_a_dropped_hand_is_never_primary(self):
        partial = Hand(landmarks=hand().landmarks[:8], side="left")

        assert [h.known_side for h in PrimaryHand().order([partial, hand(side="right")])] == [
            "right"
        ]

    def test_no_hands_is_no_hands(self):
        assert PrimaryHand().order([]) == []

    def test_every_usable_hand_survives_the_ordering(self):
        ordered = PrimaryHand().order([hand(side="left"), hand(side="right")])

        assert len(ordered) == 2


class TestRatios:
    """The raw measurements, which are what a Calibration is actually set from."""

    def test_a_straight_finger_measures_full_extension(self):
        measured = ratios(hand())

        assert measured.extension["Index"] == pytest.approx(1.0, abs=0.01)

    def test_a_folded_finger_measures_less(self):
        assert ratios(hand(bend=FOLDED)).extension["Index"] < 0.5

    def test_a_pinch_closes_the_measured_gap(self):
        assert ratios(pinched(hand(bend=FOLDED))).pinch_index < ratios(hand()).pinch_index

    def test_splaying_widens_the_measured_spread(self):
        assert ratios(hand(spread=2.0)).spread > ratios(hand(spread=0.4)).spread

    def test_the_span_is_reported_so_distance_from_the_camera_is_visible(self):
        assert ratios(moved(hand(), scale=2.0)).palm_span == pytest.approx(
            2.0 * ratios(hand()).palm_span
        )

    def test_a_measurement_is_indifferent_to_where_the_hand_is(self):
        here, there = ratios(hand()), ratios(moved(hand(), by=(0.2, -0.1), scale=1.7))

        assert here.extension == pytest.approx(there.extension)
        assert here.pinch_index == pytest.approx(there.pinch_index)
        assert here.palm_area == pytest.approx(there.palm_area)

    def test_a_dropped_hand_measures_nothing(self):
        assert ratios(Hand(landmarks=hand().landmarks[:8])) is None

    def test_a_collapsed_hand_measures_without_raising(self):
        assert ratios(Hand(landmarks=[(0.5, 0.5, 0.0)] * 21)) is not None


class TestReport:
    """What --hand-scores prints."""

    def test_no_hand_says_so_rather_than_printing_zeroes(self):
        assert report([]) == "no hand in frame"

    def test_a_hand_reports_its_side_and_which_names_it_answers_to(self):
        line = report([hand(side="right")]).splitlines()[1]

        assert "right hand (primary)" in line
        assert "hand*, rightHand*" in line

    def test_the_scores_and_the_ratios_are_both_shown(self):
        block = report([hand(bend=FOLDED)])

        assert "scores" in block
        assert "ratios" in block
        assert "extension" in block

    def test_a_score_no_frame_reported_reads_as_absent(self):
        assert report([]) == "no hand in frame"
        assert "bothHandsPresent 0.00" in report([hand()])

    def test_both_hands_get_a_block(self):
        block = report(PrimaryHand().order([hand(side="right"), hand(side="left")]))

        assert "right hand (primary)" in block
        assert "left hand -> leftHand*" in block
        assert "bothHandsPresent 1.00" in block

    def test_a_hand_of_unknown_handedness_is_named_as_such(self):
        assert "unknown hand" in report([hand(side="")])

    def test_no_score_prints_as_negative_zero(self):
        assert "-0.00" not in report([hand(side="right")])

    def test_a_collapsed_hand_reports_without_raising(self):
        assert report([Hand(landmarks=[(0.5, 0.5, 0.0)] * 21, side="left")])

    def test_a_frame_holding_only_a_dropped_hand_says_no_hand(self):
        assert report([Hand(landmarks=hand().landmarks[:8], side="left")]) == "no hand in frame"

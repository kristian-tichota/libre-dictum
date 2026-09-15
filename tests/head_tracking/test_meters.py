import pytest

from libre_dictum.meters import ABSENT, FINGERS, GestureMeter, HandReading, Reading
from libre_dictum.tracking.gestures import GestureRecognizer
from libre_dictum.tracking.hands import FINGER_CHAINS, hand_readings

from .test_hands import hand


def reading(score=0.5, minimum=None, maximum=None, feature="handFist"):
    return Reading(feature=feature, score=score, minimum=minimum, maximum=maximum)


class TestOneCondition:
    def test_a_minimum_that_is_met_holds(self):
        assert reading(0.9, minimum=0.8).holds

    def test_a_minimum_that_is_not_met_does_not(self):
        assert not reading(0.62, minimum=0.8).holds

    def test_a_maximum_holds_below_itself(self):
        assert reading(0.2, maximum=0.4).holds
        assert not reading(0.5, maximum=0.4).holds

    def test_a_window_needs_both_ends(self):
        assert reading(0.5, minimum=0.4, maximum=0.6).holds
        assert not reading(0.7, minimum=0.4, maximum=0.6).holds

    def test_the_requirement_reads_as_the_config_wrote_it(self):
        assert reading(minimum=0.8).requirement == ">0.80"
        assert reading(maximum=0.4).requirement == "<0.40"
        assert reading(minimum=0.4, maximum=0.6).requirement == ">0.40 <0.60"

    def test_a_condition_describes_itself_with_its_own_feature_name(self):
        assert reading(0.62, minimum=0.8, feature="rightHandFist").describe() == (
            "rightHandFist 0.62 >0.80"
        )


class TestHowCloseOneConditionIs:
    """The bar's number."""

    def test_a_met_condition_is_all_the_way_there(self):
        assert reading(0.9, minimum=0.8).closeness == 1.0
        assert reading(0.1, maximum=0.4).closeness == 1.0

    def test_a_minimum_is_the_fraction_of_the_way_there(self):
        assert reading(0.4, minimum=0.8).closeness == pytest.approx(0.5)

    def test_a_maximum_falls_from_its_threshold_to_a_full_score(self):
        assert reading(0.7, maximum=0.4).closeness == pytest.approx(0.5)
        assert reading(1.0, maximum=0.4).closeness == 0.0

    def test_a_window_is_the_worse_of_its_two_ends(self):
        assert reading(0.2, minimum=0.4, maximum=0.6).closeness == pytest.approx(0.5)

    def test_a_score_that_is_not_there_is_nowhere_near(self):
        assert Reading("handFist", None, minimum=0.8).closeness == 0.0

    def test_closeness_never_leaves_the_unit_range(self):
        assert reading(9.0, minimum=0.1).closeness == 1.0
        assert reading(0.0, maximum=1.0).closeness == 1.0


class TestOneGesture:
    def test_it_is_as_close_as_its_least_satisfied_condition(self):
        meter = GestureMeter(
            "right_fist",
            (reading(0.9, minimum=0.8, feature="a"), reading(0.1, minimum=0.8, feature="b")),
        )
        assert meter.value == pytest.approx(0.125)

    def test_the_blocking_condition_is_the_least_satisfied_unmet_one(self):
        meter = GestureMeter(
            "right_fist",
            (
                reading(0.62, minimum=0.8, feature="rightHandFist"),
                reading(0.10, minimum=0.4, feature="rightHandThumbCurl"),
            ),
        )
        blocking = meter.blocking
        assert blocking is not None
        assert blocking.feature == "rightHandThumbCurl"

    def test_a_met_condition_is_never_the_blocking_one(self):
        meter = GestureMeter(
            "fist",
            (
                reading(0.79, minimum=0.8, feature="unmet"),
                reading(0.05, minimum=0.0, feature="met"),
            ),
        )
        blocking = meter.blocking
        assert blocking is not None
        assert blocking.feature == "unmet"

    def test_nothing_blocks_a_gesture_whose_conditions_all_hold(self):
        meter = GestureMeter("fist", (reading(0.9, minimum=0.8),))
        assert meter.holds
        assert meter.blocking is None

    def test_a_gesture_with_no_conditions_holds_nothing(self):
        assert not GestureMeter("empty").holds
        assert GestureMeter("empty").value == 0.0

    def test_one_absent_feature_makes_the_whole_gesture_waiting(self):
        meter = GestureMeter(
            "fist", (reading(0.9, minimum=0.8), Reading("handOpen", None, minimum=0.5))
        )
        assert meter.waiting
        assert not meter.holds


class TestReadingsOffTheRecognizer:
    def test_every_gesture_gets_a_meter_in_configuration_order(self):
        recognizer = GestureRecognizer(
            {
                "smile": {"mouthSmileLeft": {"min": 0.8}},
                "fist": {"handFist": {"min": 0.8}},
            }
        )
        assert [meter.name for meter in recognizer.meters({})] == ["smile", "fist"]

    def test_a_meter_carries_the_gestures_own_limits(self):
        recognizer = GestureRecognizer({"fist": {"handFist": {"min": 0.8, "release": 0.4}}})
        (meter,) = recognizer.meters({"handFist": 0.62})

        assert meter.readings[0].minimum == 0.8
        assert meter.readings[0].score == 0.62
        assert meter.readings[0].maximum is None

    def test_the_dwell_travels_so_a_display_can_say_what_it_is_waiting_for(self):
        recognizer = GestureRecognizer({"fist": {"hold": 250, "handFist": {"min": 0.8}}})
        (meter,) = recognizer.meters({"handFist": 0.9})

        assert meter.hold == pytest.approx(0.25)
        assert meter.holds and not meter.active

    def test_an_active_gesture_says_so(self):
        recognizer = GestureRecognizer({"fist": {"handFist": {"min": 0.8}}})
        recognizer.update({"handFist": 0.9})

        (meter,) = recognizer.meters({"handFist": 0.9})
        assert meter.active

    def test_a_frame_with_no_hand_reports_absent_rather_than_zero(self):
        recognizer = GestureRecognizer({"fist": {"handFist": {"min": 0.8}}})
        (meter,) = recognizer.meters({"mouthSmileLeft": 0.1})

        assert meter.waiting
        blocking = meter.blocking
        assert blocking is not None
        assert blocking.describe() == f"handFist {ABSENT} >0.80"

    def test_the_hold_key_is_not_read_as_a_feature(self):
        recognizer = GestureRecognizer({"fist": {"hold": 250, "handFist": {"min": 0.8}}})
        (meter,) = recognizer.meters({"handFist": 0.9})

        assert [reading.feature for reading in meter.readings] == ["handFist"]


class TestTheRawRatios:
    def test_a_hand_reports_the_measurements_behind_its_scores(self):
        (measured,) = hand_readings([hand(bend=0.0, side="right")])

        assert measured.side == "right"
        assert measured.primary
        assert measured.span > 0.0
        assert measured.extension == pytest.approx((1.0,) * 5)

    def test_the_extensions_are_paired_with_the_fingers_they_belong_to(self):
        (measured,) = hand_readings([hand(bend=0.0)])
        assert [name for name, _ in measured.extensions()] == list(FINGERS)

    def test_the_finger_names_are_the_ones_the_scoring_uses(self):
        assert tuple(FINGER_CHAINS) == FINGERS

    def test_the_primary_hand_is_the_first_one_and_says_so(self):
        readings = hand_readings([hand(side="right"), hand(side="left")])
        assert [(m.side, m.primary) for m in readings] == [("right", True), ("left", False)]

    def test_a_dropped_hand_is_left_out_rather_than_reported_as_zeroes(self):
        from libre_dictum.tracking.hands import Hand

        assert hand_readings([Hand(landmarks=[(0.0, 0.0, 0.0)], side="right")]) == ()

    def test_a_hand_of_unknown_handedness_still_reports_its_ratios(self):
        (measured,) = hand_readings([hand(side="")])
        assert measured.side == ""
        assert measured.label == "unknown hand (primary)"

    def test_the_label_says_which_hand_the_numbers_came_off(self):
        assert HandReading(side="left", primary=True).label == "left hand (primary)"
        assert HandReading(side="left").label == "left hand"

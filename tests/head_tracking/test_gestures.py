import subprocess
import sys
import textwrap

import pytest

from libre_dictum.settings import DEFAULT_GESTURE_DEFINITIONS
from libre_dictum.tracking.gestures import GestureRecognizer

WINK = {
    "left_wink": {
        "eyeBlinkLeft": {"min": 0.6, "release": 0.3},
        "eyeBlinkRight": {"max": 0.2, "release": 0.3},
    }
}


class TestFiring:
    def test_a_gesture_fires_when_every_condition_holds(self):
        recognizer = GestureRecognizer(WINK)
        assert recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})[0] == ["left_wink"]

    def test_a_gesture_with_one_condition_unmet_does_not_fire(self):
        recognizer = GestureRecognizer(WINK)
        assert recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.5})[0] == []

    def test_a_missing_feature_counts_as_zero(self):
        recognizer = GestureRecognizer(WINK)
        assert recognizer.update({"eyeBlinkRight": 0.1})[0] == []

    def test_two_gestures_can_fire_on_one_frame(self):
        recognizer = GestureRecognizer(
            {"a": {"jawOpen": {"min": 0.4}}, "b": {"browInnerUp": {"min": 0.4}}}
        )
        assert set(recognizer.update({"jawOpen": 0.9, "browInnerUp": 0.9})[0]) == {"a", "b"}


class TestHysteresis:
    def test_a_held_expression_fires_only_once(self):
        recognizer = GestureRecognizer(WINK)
        scores = {"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}

        assert recognizer.update(scores)[0] == ["left_wink"]
        assert recognizer.update(scores)[0] == []
        assert recognizer.update(scores)[0] == []

    def test_falling_below_the_release_value_re_arms(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})
        recognizer.update({"eyeBlinkLeft": 0.2, "eyeBlinkRight": 0.1})

        assert recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})[0] == ["left_wink"]

    def test_between_activation_and_release_it_stays_armed(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})
        recognizer.update({"eyeBlinkLeft": 0.45, "eyeBlinkRight": 0.1})[0]

        assert recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})[0] == []

    def test_any_feature_crossing_its_release_is_enough(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.4})[0]

        assert recognizer.active == set()

    def test_without_a_release_value_the_threshold_is_used(self):
        recognizer = GestureRecognizer({"pucker": {"mouthPucker": {"min": 0.5}}})
        recognizer.update({"mouthPucker": 0.9})
        recognizer.update({"mouthPucker": 0.5})

        assert recognizer.active == set()

    def test_reset_forgets_everything(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})
        recognizer.reset()

        assert recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})[0] == ["left_wink"]


class TestDwellTime:
    """hold is what stops a gesture firing on the way *into* another one."""

    RAMPING = {
        "smile": {
            "mouthSmileLeft": {"min": 0.8, "release": 0.4},
            "mouthSmileRight": {"min": 0.8, "release": 0.4},
        },
        "left_smirk": {
            "hold": 250,
            "mouthSmileLeft": {"min": 0.4, "release": 0.2},
            "mouthSmileRight": {"max": 0.3, "release": 0.4},
        },
    }

    def test_without_a_hold_a_gesture_fires_the_moment_it_holds(self):
        recognizer = GestureRecognizer({"pucker": {"mouthPucker": {"min": 0.5}}})

        assert recognizer.update({"mouthPucker": 0.9}, 0.0)[0] == ["pucker"]

    def test_a_hold_delays_the_firing(self):
        recognizer = GestureRecognizer({"pucker": {"hold": 200, "mouthPucker": {"min": 0.5}}})
        scores = {"mouthPucker": 0.9}

        assert recognizer.update(scores, 0.0)[0] == []
        assert recognizer.update(scores, 0.1)[0] == []
        assert recognizer.update(scores, 0.2)[0] == ["pucker"]

    def test_it_fires_only_once_after_the_dwell(self):
        recognizer = GestureRecognizer({"pucker": {"hold": 100, "mouthPucker": {"min": 0.5}}})
        scores = {"mouthPucker": 0.9}
        recognizer.update(scores, 0.0)

        assert recognizer.update(scores, 0.2)[0] == ["pucker"]
        assert recognizer.update(scores, 0.3)[0] == []

    def test_conditions_broken_before_the_dwell_elapses_restart_the_clock(self):
        recognizer = GestureRecognizer({"pucker": {"hold": 200, "mouthPucker": {"min": 0.5}}})

        recognizer.update({"mouthPucker": 0.9}, 0.0)
        recognizer.update({"mouthPucker": 0.1}, 0.1)
        recognizer.update({"mouthPucker": 0.9}, 0.2)

        assert recognizer.update({"mouthPucker": 0.9}, 0.35)[0] == []
        assert recognizer.update({"mouthPucker": 0.9}, 0.4)[0] == ["pucker"]

    def ramp(self, recognizer, seconds=1.0, dt=1 / 60):
        """A real smile: the left corner leads the right, so it is never symmetric."""
        fired = []
        for frame in range(int(seconds / dt)):
            progress = frame / (seconds / dt - 1)
            fired += recognizer.update(
                {
                    "mouthSmileLeft": progress,
                    "mouthSmileRight": max(0.0, progress - 0.25),
                },
                frame * dt,
            )[0]
        return fired

    def test_a_gesture_passed_through_on_the_way_up_used_to_fire(self):
        without = {
            name: {k: v for k, v in features.items() if k != "hold"}
            for name, features in self.RAMPING.items()
        }
        assert "left_smirk" in self.ramp(GestureRecognizer(without))

    def test_a_dwell_closes_that_window(self):
        assert "left_smirk" not in self.ramp(GestureRecognizer(self.RAMPING))

    def test_but_the_same_gesture_still_fires_when_it_is_held(self):
        recognizer = GestureRecognizer(self.RAMPING)
        fired = []
        for frame in range(60):
            fired += recognizer.update({"mouthSmileLeft": 0.6, "mouthSmileRight": 0.1}, frame / 60)[
                0
            ]

        assert fired == ["left_smirk"]

    def test_resetting_forgets_a_dwell_in_progress(self):
        recognizer = GestureRecognizer({"pucker": {"hold": 200, "mouthPucker": {"min": 0.5}}})
        recognizer.update({"mouthPucker": 0.9}, 0.0)
        recognizer.reset()

        assert recognizer.update({"mouthPucker": 0.9}, 0.15)[0] == []


class TestShippedDefinitions:
    @pytest.mark.parametrize("name", sorted(DEFAULT_GESTURE_DEFINITIONS))
    def test_every_shipped_gesture_can_fire_and_re_arm(self, name):
        definition = {name: DEFAULT_GESTURE_DEFINITIONS[name]}
        recognizer = GestureRecognizer(definition)

        activating = {}
        releasing = {}
        for feature, limits in definition[name].items():
            activating[feature] = limits.get("min", limits.get("max", 0.0))
            releasing[feature] = limits["release"]

        assert recognizer.update(activating)[0] == [name]
        recognizer.update(releasing)
        assert recognizer.active == set()


class TestAnAbsentFeature:
    """A feature no frame reported is not a feature scoring zero."""

    CLOSED = {"closed": {"jawOpen": {"max": 0.1}}}

    def test_a_max_only_gesture_does_not_fire_when_nothing_reported_it(self):
        assert GestureRecognizer(self.CLOSED).update({})[0] == []

    def test_it_still_fires_when_the_feature_is_actually_reported(self):
        assert GestureRecognizer(self.CLOSED).update({"jawOpen": 0.05})[0] == ["closed"]

    def test_an_active_gesture_re_arms_when_its_feature_stops_being_reported(self):
        recognizer = GestureRecognizer(self.CLOSED)
        assert recognizer.update({"jawOpen": 0.05})[0] == ["closed"]

        recognizer.update({})

        assert recognizer.active == set()
        assert recognizer.update({"jawOpen": 0.05})[0] == ["closed"]

    def test_a_dwell_in_progress_is_dropped_when_the_feature_goes_away(self):
        recognizer = GestureRecognizer({"closed": {"hold": 200, "jawOpen": {"max": 0.1}}})
        recognizer.update({"jawOpen": 0.05}, now=0.0)
        recognizer.update({}, now=0.1)

        assert recognizer.update({"jawOpen": 0.05}, now=0.25)[0] == []
        assert recognizer.update({"jawOpen": 0.05}, now=0.44)[0] == []
        assert recognizer.update({"jawOpen": 0.05}, now=0.45)[0] == ["closed"]

    def test_one_sensor_reporting_does_not_fire_the_other_sensors_gesture(self):
        recognizer = GestureRecognizer(
            {"fist": {"handFist": {"min": 0.8}}, **self.CLOSED},
        )

        assert recognizer.update({"handFist": 0.9})[0] == ["fist"]


class TestBothEdges:
    """A gesture re-arms, and that moment is reportable."""

    def test_a_gesture_that_re_arms_reports_a_release(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})

        assert recognizer.update({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}) == ([], ["left_wink"])

    def test_nothing_is_released_that_was_never_active(self):
        recognizer = GestureRecognizer(WINK)

        assert recognizer.update({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}) == ([], [])
        assert recognizer.update({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}) == ([], [])

    def test_a_gesture_still_holding_reports_neither_edge(self):
        recognizer = GestureRecognizer(WINK)
        held = {"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}
        recognizer.update(held)

        assert recognizer.update(held) == ([], [])

    def test_the_release_waits_for_the_release_value_not_the_threshold(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})

        assert recognizer.update({"eyeBlinkLeft": 0.45, "eyeBlinkRight": 0.1}) == ([], [])
        assert recognizer.update({"eyeBlinkLeft": 0.2, "eyeBlinkRight": 0.1}) == ([], ["left_wink"])

    def test_the_sensor_going_quiet_releases_rather_than_going_silent(self):
        recognizer = GestureRecognizer({"fist": {"handFist": {"min": 0.6}}})
        recognizer.update({"handFist": 0.9})

        assert recognizer.update({}) == ([], ["fist"])

    def test_a_dwell_that_never_completed_releases_nothing(self):
        recognizer = GestureRecognizer({"fist": {"hold": 200, "handFist": {"min": 0.6}}})
        recognizer.update({"handFist": 0.9}, now=0.0)

        assert recognizer.update({}, now=0.1) == ([], [])

    def test_both_edges_can_land_on_one_frame_for_different_gestures(self):
        recognizer = GestureRecognizer(
            {"left": {"leftHandFist": {"min": 0.6}}, "right": {"rightHandFist": {"min": 0.6}}}
        )
        recognizer.update({"leftHandFist": 0.9, "rightHandFist": 0.0})

        assert recognizer.update({"leftHandFist": 0.0, "rightHandFist": 0.9}) == (
            ["right"],
            ["left"],
        )


class TestTheMoreSpecificGestureWins:
    """Two hands at once, which is the whole reason this exclusion exists."""

    HANDS = {
        "left_open": {"leftHandOpen": {"min": 0.9, "release": 0.6}},
        "right_open": {"rightHandOpen": {"min": 0.9, "release": 0.6}},
        "both_open": {
            "leftHandOpen": {"min": 0.9, "release": 0.6},
            "rightHandOpen": {"min": 0.9, "release": 0.6},
            "bothHandsPresent": {"min": 0.5},
        },
    }

    def test_the_wider_gesture_fires_alone(self):
        recognizer = GestureRecognizer(self.HANDS)

        assert recognizer.update(
            {"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0}
        ) == (["both_open"], [])

    def test_a_narrower_gesture_already_held_is_released_when_the_wider_one_fires(self):
        recognizer = GestureRecognizer(self.HANDS)
        recognizer.update({"leftHandOpen": 0.0, "rightHandOpen": 0.95, "bothHandsPresent": 0.0})

        assert recognizer.update(
            {"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0}
        ) == (["both_open"], ["right_open"])

    def test_dropping_the_second_hand_hands_the_gesture_back(self):
        recognizer = GestureRecognizer(self.HANDS)
        recognizer.update({"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0})

        assert recognizer.update(
            {"leftHandOpen": 0.0, "rightHandOpen": 0.95, "bothHandsPresent": 0.0}
        ) == (["right_open"], ["both_open"])

    def test_a_suppressed_gesture_does_not_flicker_while_the_wider_one_holds(self):
        recognizer = GestureRecognizer(self.HANDS)
        both = {"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0}
        recognizer.update(both)

        assert recognizer.update(both) == ([], [])
        assert recognizer.update(both) == ([], [])
        assert recognizer.active == {"both_open"}

    def test_a_suppressed_gesture_serves_its_dwell_again_afterwards(self):
        recognizer = GestureRecognizer(
            {name: {"hold": 300, **block} for name, block in self.HANDS.items()}
        )
        both = {"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0}
        right = {"leftHandOpen": 0.0, "rightHandOpen": 0.95, "bothHandsPresent": 0.0}
        recognizer.update(both, now=0.0)
        assert recognizer.update(both, now=0.4) == (["both_open"], [])

        assert recognizer.update(right, now=0.5) == ([], ["both_open"])
        assert recognizer.update(right, now=0.6) == ([], [])
        assert recognizer.update(right, now=0.9) == (["right_open"], [])

    def test_a_gesture_still_dwelling_suppresses_nothing(self):
        recognizer = GestureRecognizer(
            {
                "left_open": self.HANDS["left_open"],
                "both_open": {"hold": 300, **self.HANDS["both_open"]},
            }
        )

        assert recognizer.update(
            {"leftHandOpen": 0.95, "rightHandOpen": 0.95, "bothHandsPresent": 1.0}, now=0.0
        ) == (["left_open"], [])

    def test_equal_condition_sets_suppress_neither_way(self):
        recognizer = GestureRecognizer(
            {
                "fist": {"leftHandFist": {"min": 0.5}, "leftHandPinchIndex": {"min": 0.15}},
                "pinch": {"leftHandPinchIndex": {"min": 0.85}, "leftHandFist": {"max": 0.45}},
            }
        )

        assert recognizer.suppressors == {"fist": frozenset(), "pinch": frozenset()}

    def test_the_other_hand_is_free_while_one_hand_holds_a_gesture(self):
        recognizer = GestureRecognizer(
            {
                "right_open": {"rightHandOpen": {"min": 0.9, "release": 0.6}},
                "left_fist": {"leftHandFist": {"min": 0.5, "release": 0.25}},
            }
        )
        assert recognizer.update({"rightHandOpen": 0.95, "leftHandFist": 0.0}) == (
            ["right_open"],
            [],
        )

        assert recognizer.update({"rightHandOpen": 0.95, "leftHandFist": 0.9}) == (
            ["left_fist"],
            [],
        )
        assert recognizer.active == {"right_open", "left_fist"}

    def test_suppression_reaches_through_a_chain(self):
        recognizer = GestureRecognizer(
            {
                "narrow": {"a": {"min": 0.5}},
                "middle": {"a": {"min": 0.5}, "b": {"min": 0.5}},
                "wide": {"a": {"min": 0.5}, "b": {"min": 0.5}, "c": {"min": 0.5}},
            }
        )

        assert recognizer.update({"a": 0.9, "b": 0.9, "c": 0.9}) == (["wide"], [])

    def test_gestures_over_different_features_do_not_suppress_each_other(self):
        recognizer = GestureRecognizer(
            {"pucker": {"mouthPucker": {"min": 0.7}}, "fist": {"handFist": {"min": 0.6}}}
        )

        assert set(recognizer.update({"mouthPucker": 0.9, "handFist": 0.9})[0]) == {
            "pucker",
            "fist",
        }

    def test_the_display_order_is_still_the_configured_one(self):
        recognizer = GestureRecognizer(self.HANDS)

        meters = recognizer.meters({"leftHandOpen": 0.1, "rightHandOpen": 0.1})

        assert [meter.name for meter in meters] == ["left_open", "right_open", "both_open"]


class TestDebugOutput:
    def test_the_debug_line_shows_scores_and_requirements(self):
        recognizer = GestureRecognizer(WINK)
        line = recognizer.describe({"eyeBlinkLeft": 0.45, "eyeBlinkRight": 0.1}, ["left_wink"])

        assert "[IDLE] left_wink" in line
        assert "eyeBlinkLeft: 0.45 (>0.6)" in line
        assert "eyeBlinkRight: 0.10 (<0.2)" in line

    def test_an_active_gesture_is_marked(self):
        recognizer = GestureRecognizer(WINK)
        recognizer.update({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1})

        assert "[ACTIVE]" in recognizer.describe({}, ["left_wink"])

    def test_a_name_that_is_not_a_gesture_is_shown_as_a_bare_feature(self):
        line = GestureRecognizer(WINK).describe({"handFist": 0.94}, ["handFist"])

        assert line == "handFist: 0.94"

    def test_a_feature_this_frame_did_not_report_reads_as_absent(self):
        assert GestureRecognizer(WINK).describe({}, ["handFist"]) == "handFist: --"


class TestImportingWithoutTheExtra:
    """Gestures are pure, so the camera stack must not be needed to reach them."""

    SCRIPT = textwrap.dedent("""
        import sys

        class Blocked:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {"cv2", "mediapipe"}:
                    raise ImportError(f"no {name} here")
                return None

        sys.meta_path.insert(0, Blocked())
        from libre_dictum.tracking import GestureRecognizer as from_the_package
        from libre_dictum.tracking.gestures import GestureRecognizer as from_the_module
        """)

    def run_blocked(self, *lines: str) -> subprocess.CompletedProcess[str]:
        script = "\n".join([self.SCRIPT, *lines])
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    def test_the_recognizer_imports_without_opencv_or_mediapipe(self):
        result = self.run_blocked()

        assert result.returncode == 0, result.stderr

    def test_the_tracker_still_raises_importerror_when_asked_for(self):
        result = self.run_blocked("from libre_dictum.tracking import FaceRotationTracker")

        assert result.returncode != 0
        assert "ImportError" in result.stderr

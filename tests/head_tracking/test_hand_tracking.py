import json
import logging

import pytest

pytest.importorskip("cv2", reason="head-tracking extra not installed")
pytest.importorskip("mediapipe", reason="head-tracking extra not installed")

from libre_dictum import main as main_module  # noqa: E402
from libre_dictum.settings import (  # noqa: E402
    HandTrackingSettings,
    HeadTrackingSettings,
)
from libre_dictum.tracking import tracker as tracker_module  # noqa: E402
from libre_dictum.tracking.hands import Calibration, Hand  # noqa: E402
from libre_dictum.tracking.tracker import FaceRotationTracker, hands_from  # noqa: E402

from .test_hands import FOLDED, hand  # noqa: E402


class Landmark:
    """One of mediapipe's normalized landmarks: attributes, not a tuple."""

    def __init__(self, point) -> None:
        self.x, self.y, self.z = point


class Category:
    def __init__(self, name, score=1.0) -> None:
        self.category_name, self.score = name, score


class HandResult:
    """What HandLandmarker.detect_for_video hands back, from the shared synthetic hands."""

    def __init__(self, hands=()) -> None:
        self.hand_landmarks = [
            [Landmark(point) for point in subject.landmarks] for subject in hands
        ]
        self.handedness = [[Category(subject.side.capitalize())] for subject in hands]


class FaceResult:
    """... and its face counterpart, with only the parts the tracker reads."""

    def __init__(self, blendshapes=None, matrix=None) -> None:
        self.face_landmarks = [object()] if blendshapes is not None or matrix is not None else []
        self.face_blendshapes = (
            [[Category(name, score) for name, score in blendshapes.items()]]
            if blendshapes is not None
            else []
        )
        self.facial_transformation_matrixes = [matrix] if matrix is not None else []


class FakeLandmarker:
    """Counts the frames it was asked about, and answers with whatever it was given."""

    def __init__(self, result=None, raises=None) -> None:
        self.result = result if result is not None else HandResult()
        self.raises = raises
        self.calls: list[int] = []
        self.closed = False

    def detect_for_video(self, _image, timestamp_ms):
        self.calls.append(timestamp_ms)
        if self.raises is not None:
            raise self.raises
        return self.result

    def close(self) -> None:
        self.closed = True


FIST = hand(bend=FOLDED, side="right")
OPEN = hand(side="left")
PARTLY_FOLDED = hand(bend=1.0, side="right")
OPEN_RIGHT = hand(side="right")


def tracker(
    *,
    gestures=None,
    hands=None,
    stride=4,
    events=None,
    calibration=None,
    face_baseline=None,
    held=None,
    meters=None,
    wants_meters=False,
):
    """A tracker with no thread and no camera, collecting (name, pressed) as it goes."""
    hand_settings = (
        HandTrackingSettings(
            model_path="hand.task",
            stride=stride,
            **({"calibration": calibration} if calibration is not None else {}),
        )
        if hands
        else None
    )
    settings = HeadTrackingSettings(
        model_path="face.task",
        gesture_definitions=gestures if gestures is not None else {},
        face_baseline=dict(face_baseline) if face_baseline else {},
        hands=hand_settings,
    )
    return FaceRotationTracker(
        settings,
        rotation_callback=lambda *_: None,
        gesture_callback=(
            (lambda name, pressed: events.append((name, pressed))) if events is not None else None
        ),
        held_callback=(held.append if held is not None else None),
        meter_callback=(
            (lambda readings, hands_seen: meters.append((readings, hands_seen)))
            if meters is not None
            else None
        ),
        wants_meters=lambda: wants_meters,
    )


def pressed(events):
    """Just the names that went active, which is what most of these are about."""
    return [name for name, is_press in events if is_press]


def relaxed(events):
    """... and the names that let go."""
    return [name for name, is_press in events if not is_press]


class TestHandednessComesFromOnePlace:
    """One place turns mediapipe's handedness label into a side, whichever way it decides."""

    def test_the_label_arrives_as_the_users_own_side(self):
        assert hands_from(HandResult([OPEN]))[0].known_side == "left"
        assert hands_from(HandResult([FIST]))[0].known_side == "right"

    def test_the_landmarks_survive_as_triples(self):
        hands = hands_from(HandResult([FIST]))

        assert len(hands[0].landmarks) == 21
        assert hands[0].landmarks[0] == FIST.landmarks[0]

    def test_a_hand_with_no_handedness_at_all_still_arrives(self):
        result = HandResult([FIST])
        result.handedness = []

        assert hands_from(result)[0].known_side == ""

    def test_no_hands_is_no_hands(self):
        assert hands_from(HandResult()) == []


class TestTheStride:
    """The hand model costs five times the face model, and they share one frame budget."""

    def test_the_first_frame_is_landmarked(self):
        subject, landmarker = tracker(hands=True, stride=4), FakeLandmarker()
        subject._read_hands(landmarker, object(), 1)

        assert len(landmarker.calls) == 1

    def test_the_frames_between_are_skipped(self):
        subject, landmarker = tracker(hands=True, stride=4), FakeLandmarker()
        for frame in range(8):
            subject._read_hands(landmarker, object(), frame)
            subject._frames_seen += 1

        assert landmarker.calls == [0, 4]

    def test_a_stride_of_one_landmarks_every_frame(self):
        subject, landmarker = tracker(hands=True, stride=1), FakeLandmarker()
        for frame in range(5):
            subject._read_hands(landmarker, object(), frame)
            subject._frames_seen += 1

        assert len(landmarker.calls) == 5

    def test_the_scores_stand_between_strided_frames(self):
        subject, landmarker = tracker(hands=True, stride=4), FakeLandmarker(HandResult([FIST]))
        subject._read_hands(landmarker, object(), 0)
        held = dict(subject._hand_scores)

        subject._frames_seen += 1
        subject._read_hands(landmarker, object(), 1)

        assert subject._hand_scores == held
        assert held


class TestOneMappingOneRecognizer:
    """Both feature sets merge on this thread, which is what keeps the recognizer simple."""

    SHOUT = {"shout": {"handFist": {"min": 0.6}, "jawOpen": {"min": 0.5}}}

    def test_a_gesture_spanning_both_models_fires(self):
        events = []
        subject = tracker(gestures=self.SHOUT, hands=True, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._handle_result(FaceResult(blendshapes={"jawOpen": 0.7}))

        assert pressed(events) == ["shout"]

    def test_half_of_it_is_not_enough(self):
        events = []
        subject = tracker(gestures=self.SHOUT, hands=True, events=events)

        subject._handle_result(FaceResult(blendshapes={"jawOpen": 0.7}))

        assert pressed(events) == []

    def test_a_hand_gesture_fires_with_the_face_out_of_frame(self):
        events = []
        subject = tracker(gestures={"fist": {"handFist": {"min": 0.6}}}, hands=True, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._handle_result(FaceResult())

        assert pressed(events) == ["fist"]

    def test_a_face_gesture_still_fires_with_no_hands_configured(self):
        events = []
        subject = tracker(gestures={"gape": {"jawOpen": {"min": 0.5}}}, events=events)

        subject._handle_result(FaceResult(blendshapes={"jawOpen": 0.7}))

        assert pressed(events) == ["gape"]


class TestTheFaceBaselineReachesTheScoring:
    """The face's answer to the calibration, and the same failure if it did not arrive."""

    WINK = {"wink": {"eyeBlinkLeft": {"min": 0.35}}}
    IDLE = {"eyeBlinkLeft": 0.305, "eyeBlinkRight": 0.394}

    def test_a_score_is_evaluated_as_its_rise_from_rest(self):
        events = []
        subject = tracker(gestures=self.WINK, events=events, face_baseline=self.IDLE)
        subject._handle_result(FaceResult(blendshapes={"eyeBlinkLeft": 0.50}))
        assert pressed(events) == []

        subject._handle_result(FaceResult(blendshapes={"eyeBlinkLeft": 0.70}))
        assert pressed(events) == ["wink"]

    def test_the_same_threshold_reaches_both_eyes_despite_different_idle_levels(self):
        mirrored = {"wink": {"eyeBlinkRight": {"min": 0.35}}}
        left, right = [], []
        subject = tracker(gestures=self.WINK, events=left, face_baseline=self.IDLE)
        other = tracker(gestures=mirrored, events=right, face_baseline=self.IDLE)

        subject._handle_result(FaceResult(blendshapes={"eyeBlinkLeft": 0.60}))
        other._handle_result(FaceResult(blendshapes={"eyeBlinkRight": 0.65}))

        assert pressed(left) == ["wink"] and pressed(right) == ["wink"]

    def test_with_no_baseline_the_raw_scores_are_what_conditions_see(self):
        events = []
        subject = tracker(gestures=self.WINK, events=events)
        subject._handle_result(FaceResult(blendshapes={"eyeBlinkLeft": 0.40}))
        assert pressed(events) == ["wink"]


class TestTheCalibrationReachesTheScoring:
    """The constants are configurable so that one edit fixes every fist-shaped gesture."""

    FIST_AT_SIX = {"fist": {"handFist": {"min": 0.6}}}

    def part_folded(self, **calibration):
        """A part-folded hand, scored under a calibration of the caller's choosing."""
        subject = tracker(
            gestures=self.FIST_AT_SIX,
            hands=True,
            calibration=Calibration(**calibration) if calibration else None,
        )
        subject._read_hands(FakeLandmarker(HandResult([PARTLY_FOLDED])), object(), 0)
        return subject._hand_scores["handFist"]

    def test_the_configured_constants_are_the_ones_applied(self):
        assert self.part_folded(folded_extension=0.75) > self.part_folded()

    def test_a_calibration_can_be_the_difference_between_firing_and_not(self):
        events = []
        subject = tracker(
            gestures=self.FIST_AT_SIX,
            hands=True,
            events=events,
            calibration=Calibration(folded_extension=0.75),
        )
        subject._read_hands(FakeLandmarker(HandResult([PARTLY_FOLDED])), object(), 0)
        subject._handle_result(FaceResult())
        assert pressed(events) == ["fist"]

        tight = []
        strict = tracker(gestures=self.FIST_AT_SIX, hands=True, events=tight)
        strict._read_hands(FakeLandmarker(HandResult([PARTLY_FOLDED])), object(), 0)
        strict._handle_result(FaceResult())
        assert pressed(tight) == []


class TestWhatADisplayIsTold:
    """The two things reported rather than acted on, and they are gated differently."""

    FIST = {"fist": {"handFist": {"min": 0.6}}}

    def test_a_shape_going_active_reports_the_held_set(self):
        held = []
        subject = tracker(gestures=self.FIST, hands=True, held=held)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._handle_result(FaceResult())

        assert held == [("fist",)]

    def test_letting_go_reports_the_empty_set(self):
        held = []
        subject = tracker(gestures=self.FIST, hands=True, held=held)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())

        subject._read_hands(FakeLandmarker(HandResult([OPEN_RIGHT])), object(), 1)
        subject._handle_result(FaceResult())

        assert held == [("fist",), ()]

    def test_a_shape_still_held_is_not_reported_again(self):
        held = []
        subject = tracker(gestures=self.FIST, hands=True, held=held)
        for frame in range(4):
            subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), frame)
            subject._handle_result(FaceResult())

        assert held == [("fist",)]

    def test_two_shapes_are_reported_in_a_fixed_order(self):
        held = []
        subject = tracker(
            gestures={"fist": {"handFist": {"min": 0.6}}, "gape": {"jawOpen": {"min": 0.5}}},
            hands=True,
            held=held,
        )
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._handle_result(FaceResult(blendshapes={"jawOpen": 0.7}))

        assert held[-1] == ("fist", "gape")

    def test_nothing_is_reported_while_nothing_is_held(self):
        held = []
        subject = tracker(gestures=self.FIST, hands=True, held=held)

        subject._handle_result(FaceResult())

        assert held == []

    def test_no_readings_are_built_for_a_page_nobody_has_open(self):
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=False)

        subject._handle_result(FaceResult())

        assert readings == []

    def test_readings_are_built_when_something_is_drawing_them(self):
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=True)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._handle_result(FaceResult())

        meters, hands_seen = readings[0]
        assert [meter.name for meter in meters] == ["fist"]
        assert [hand_seen.side for hand_seen in hands_seen] == ["right"]

    def test_they_carry_this_frames_scores(self):
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=True)
        subject._read_hands(FakeLandmarker(HandResult([OPEN_RIGHT])), object(), 0)

        subject._handle_result(FaceResult())

        blocking = readings[0][0][0].blocking
        assert blocking is not None
        assert blocking.feature == "handFist"

    def test_they_are_thinned_rather_than_sent_every_frame(self):
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=True)

        for _ in range(20):
            subject._handle_result(FaceResult())

        assert len(readings) == 1

    def test_the_gap_between_two_frames_of_them_is_the_documented_one(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(tracker_module.time, "monotonic", lambda: clock[0])
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=True)
        subject._handle_result(FaceResult())

        clock[0] += tracker_module.METER_INTERVAL_SECONDS / 2.0
        subject._handle_result(FaceResult())
        assert len(readings) == 1, "a frame arrived inside the interval"

        clock[0] += tracker_module.METER_INTERVAL_SECONDS
        subject._handle_result(FaceResult())

        assert len(readings) == 2

    def test_a_hand_leaving_the_frame_takes_its_ratios_with_it(self):
        readings = []
        subject = tracker(gestures=self.FIST, hands=True, meters=readings, wants_meters=True)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._read_hands(FakeLandmarker(HandResult()), object(), 1)

        subject._handle_result(FaceResult())

        assert readings[0][1] == ()


class TestTheAppWiresAllThreeCallbacks:
    """A callback the app forgets to pass is a feature that is silently not there."""

    CONFIG = {
        "enable_head_tracking": True,
        "ht_model_path": "face.task",
        "modes": {"root mode": {"type": "vosk", "path": "m"}},
    }

    def app(self, backend, tmp_path):
        from libre_dictum.app import Application
        from libre_dictum.settings import parse_settings

        return Application(parse_settings(self.CONFIG, tmp_path), backend=backend)

    def test_every_callback_is_the_applications_own(self, backend, tmp_path):
        application = self.app(backend, tmp_path)

        subject = application._create_tracker(application.settings.head_tracking)

        assert subject.gesture_callback == application._on_gesture
        assert subject.held_callback == application._on_gestures_held
        assert subject.meter_callback == application._on_meters
        assert subject.wants_meters == application._wants_meters

    def test_with_no_display_socket_nothing_wants_the_readings(self, backend, tmp_path):
        application = self.app(backend, tmp_path)

        assert application.hud is None
        assert not application._wants_meters()

    def test_reporting_to_a_session_with_no_display_is_silence(self, backend, tmp_path):
        application = self.app(backend, tmp_path)

        application._on_gestures_held(("fist",))
        application._on_meters((), ())


class TestAHandModelThatFails:
    """Rule 2: a failed model costs one thing."""

    def test_a_model_that_will_not_load_leaves_the_face_working(self, caplog):
        events = []
        subject = tracker(gestures={"gape": {"jawOpen": {"min": 0.5}}}, hands=True, events=events)

        assert subject._open_hand_landmarker() is None
        assert subject.hand_failure is not None
        assert "Head tracking is unaffected" in caplog.text

        subject._handle_result(FaceResult(blendshapes={"jawOpen": 0.7}))
        assert pressed(events) == ["gape"]

    def test_the_camera_failure_is_left_alone(self):
        subject = tracker(hands=True)
        subject._open_hand_landmarker()

        assert subject.failure is None

    def test_a_model_that_starts_raising_gives_up_rather_than_retrying_every_frame(self):
        subject = tracker(hands=True, stride=1)
        landmarker = FakeLandmarker(raises=RuntimeError("tflite went away"))

        assert subject._read_hands(landmarker, object(), 0) is False
        assert isinstance(subject.hand_failure, RuntimeError)

    def test_the_stale_scores_are_cleared_when_it_gives_up(self):
        subject = tracker(hands=True, stride=1)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        assert subject._hand_scores

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(raises=RuntimeError("boom")), object(), 1)

        assert subject._hand_scores == {}

    def test_a_gesture_it_was_holding_releases(self):
        events = []
        subject = tracker(gestures={"fist": {"handFist": {"min": 0.6}}}, hands=True, stride=1)
        subject.gesture_callback = lambda name, is_press: events.append((name, is_press))
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())
        assert pressed(events) == ["fist"]

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(raises=RuntimeError("boom")), object(), 1)
        subject._handle_result(FaceResult())

        assert subject.gestures.active == set()


class TestAHandLeavingTheFrame:
    def test_the_scores_go_away_rather_than_to_zero(self):
        subject = tracker(hands=True, stride=1)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult()), object(), 1)

        assert subject._hand_scores == {}

    def test_a_gesture_it_was_holding_re_arms(self):
        events = []
        subject = tracker(gestures={"fist": {"handFist": {"min": 0.6}}}, hands=True, stride=1)
        subject.gesture_callback = lambda name, is_press: events.append((name, is_press))
        present = FakeLandmarker(HandResult([FIST]))

        subject._read_hands(present, object(), 0)
        subject._handle_result(FaceResult())
        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult()), object(), 1)
        subject._handle_result(FaceResult())
        subject._frames_seen += 1
        subject._read_hands(present, object(), 2)
        subject._handle_result(FaceResult())

        assert pressed(events) == ["fist", "fist"]


class TestThePrimaryHandIsStable:
    def test_the_primary_hand_survives_the_order_reversing(self):
        subject = tracker(hands=True, stride=1)
        one, two = FIST, OPEN

        subject._read_hands(FakeLandmarker(HandResult([one, two])), object(), 0)
        first = subject._hand_scores["handFist"]
        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult([two, one])), object(), 1)

        assert subject._hand_scores["handFist"] == first

    def test_giving_up_forgets_which_hand_was_primary(self):
        subject = tracker(hands=True, stride=1)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(raises=RuntimeError("boom")), object(), 1)

        assert subject._primary.order([Hand(landmarks=[(0.0, 0.0, 0.0)] * 21, side="left")])


class TestBothEdgesReachTheCallback:
    """A fist opening arrives as its own event."""

    FIST_ONLY = {"fist": {"handFist": {"min": 0.6}}}

    def test_opening_the_hand_arrives_as_a_release(self):
        events = []
        subject = tracker(gestures=self.FIST_ONLY, hands=True, stride=1, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult([OPEN_RIGHT])), object(), 1)
        subject._handle_result(FaceResult())

        assert events == [("fist", True), ("fist", False)]

    def test_the_hand_leaving_the_frame_arrives_as_a_release(self):
        events = []
        subject = tracker(gestures=self.FIST_ONLY, hands=True, stride=1, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult()), object(), 1)
        subject._handle_result(FaceResult())

        assert relaxed(events) == ["fist"]

    def test_a_held_fist_is_not_re_reported_on_the_frames_between_strides(self):
        events = []
        subject = tracker(gestures=self.FIST_ONLY, hands=True, stride=4, events=events)
        for frame in range(8):
            subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), frame)
            subject._frames_seen += 1
            subject._handle_result(FaceResult())

        assert events == [("fist", True)]

    def test_a_dead_hand_model_releases_what_it_was_holding(self):
        events = []
        subject = tracker(gestures=self.FIST_ONLY, hands=True, stride=1, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(raises=RuntimeError("boom")), object(), 1)
        subject._handle_result(FaceResult())

        assert relaxed(events) == ["fist"]


class TestTwoHandsAtOnce:
    """One hand holding a shape while the other does something else, through the camera path."""

    PER_HAND = {
        "right_fist": {"rightHandFist": {"min": 0.6}},
        "left_open": {"leftHandOpen": {"min": 0.9}},
    }

    def test_each_hand_fires_its_own_gesture(self):
        events = []
        subject = tracker(gestures=self.PER_HAND, hands=True, stride=1, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST, OPEN])), object(), 0)
        subject._handle_result(FaceResult())

        assert set(pressed(events)) == {"right_fist", "left_open"}

    def test_the_second_hand_arriving_does_not_disturb_the_first(self):
        events = []
        subject = tracker(gestures=self.PER_HAND, hands=True, stride=1, events=events)
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())
        assert events == [("right_fist", True)]

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult([FIST, OPEN])), object(), 1)
        subject._handle_result(FaceResult())

        assert events == [("right_fist", True), ("left_open", True)]

    def test_a_release_is_dispatched_before_a_fire_on_the_same_frame(self):
        events = []
        subject = tracker(
            gestures={
                "fist": {"rightHandFist": {"min": 0.6}},
                "open": {"rightHandOpen": {"min": 0.9}},
            },
            hands=True,
            stride=1,
            events=events,
        )
        subject._read_hands(FakeLandmarker(HandResult([FIST])), object(), 0)
        subject._handle_result(FaceResult())
        assert events == [("fist", True)]

        subject._frames_seen += 1
        subject._read_hands(FakeLandmarker(HandResult([OPEN_RIGHT])), object(), 1)
        subject._handle_result(FaceResult())

        assert events == [("fist", True), ("fist", False), ("open", True)]


class TestFindingTheHandModel:
    """--hand-scores has to run before the config has heard of hands."""

    @pytest.fixture
    def config_dir(self, tmp_path):
        """A configuration with head tracking on, hands unmentioned, and a face model on disk."""
        (tmp_path / "face_landmarker.task").write_bytes(b"not really a model")
        (tmp_path / "model").mkdir()
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "enable_head_tracking": True,
                    "ht_model_path": "face_landmarker.task",
                    "modes": {"root": {"type": "vosk", "path": "model"}},
                }
            )
        )
        return tmp_path

    @pytest.fixture
    def chosen(self, monkeypatch):
        """Collects the settings the probe would have been run with, opening no camera."""
        seen = []

        def watch_hands(hands, **_):
            seen.append(hands)
            return 0

        monkeypatch.setattr("libre_dictum.tracking.probe.watch_hands", watch_hands)
        return seen

    def test_the_model_beside_the_face_one_is_found_with_no_arguments(
        self, config_dir, chosen, caplog
    ):
        caplog.set_level(logging.INFO)
        (config_dir / "hand_landmarker.task").write_bytes(b"nor this")

        assert main_module._watch_hand_scores(config_dir, "") == 0
        assert chosen[0].model_path == str(config_dir / "hand_landmarker.task")
        assert "found beside the face model" in caplog.text

    def test_an_explicit_path_wins(self, config_dir, chosen):
        (config_dir / "hand_landmarker.task").write_bytes(b"nor this")
        elsewhere = config_dir / "other.task"
        elsewhere.write_bytes(b"picked by hand")

        main_module._watch_hand_scores(config_dir, str(elsewhere))

        assert chosen[0].model_path == str(elsewhere)

    def test_a_configured_path_wins_over_the_guess(self, config_dir, chosen):
        (config_dir / "hand_landmarker.task").write_bytes(b"nor this")
        (config_dir / "configured.task").write_bytes(b"the configured one")
        (config_dir / "config.json").write_text(
            json.dumps(
                {
                    "enable_head_tracking": True,
                    "ht_model_path": "face_landmarker.task",
                    "ht_hand_enabled": True,
                    "ht_hand_model_path": "configured.task",
                    "modes": {"root": {"type": "vosk", "path": "model"}},
                }
            )
        )

        main_module._watch_hand_scores(config_dir, "")

        assert chosen[0].model_path == str(config_dir / "configured.task")

    def test_the_stride_from_the_config_survives_an_explicit_path(self, config_dir, chosen):
        (config_dir / "config.json").write_text(
            json.dumps(
                {
                    "enable_head_tracking": True,
                    "ht_model_path": "face_landmarker.task",
                    "ht_hand_enabled": True,
                    "ht_hand_model_path": "face_landmarker.task",
                    "ht_hand_max_hands": 1,
                    "modes": {"root": {"type": "vosk", "path": "model"}},
                }
            )
        )
        override = config_dir / "other.task"
        override.write_bytes(b"picked by hand")

        main_module._watch_hand_scores(config_dir, str(override))

        assert (chosen[0].model_path, chosen[0].max_hands) == (str(override), 1)

    def test_nothing_anywhere_names_the_file_it_looked_for(self, config_dir, chosen, caplog):
        assert main_module._watch_hand_scores(config_dir, "") == 1
        assert chosen == []
        assert "hand_landmarker.task" in caplog.text
        assert "beside the face model" in caplog.text

    def test_a_directory_of_that_name_is_not_a_model(self, config_dir, chosen):
        (config_dir / "hand_landmarker.task").mkdir()

        assert main_module._watch_hand_scores(config_dir, "") == 1

    def test_head_tracking_off_is_reported_as_such(self, tmp_path, chosen, caplog):
        (tmp_path / "model").mkdir()
        (tmp_path / "config.json").write_text(
            json.dumps({"modes": {"root": {"type": "vosk", "path": "model"}}})
        )

        assert main_module._watch_hand_scores(tmp_path, "") == 1
        assert "enable_head_tracking" in caplog.text

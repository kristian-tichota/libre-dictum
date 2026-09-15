import pytest

pytest.importorskip("cv2", reason="head-tracking extra not installed")
pytest.importorskip("mediapipe", reason="head-tracking extra not installed")

from libre_dictum.errors import CameraError  # noqa: E402
from libre_dictum.settings import CaptureSettings, HeadTrackingSettings  # noqa: E402
from libre_dictum.tracking import tracker as tracker_module  # noqa: E402
from libre_dictum.tracking.tracker import FaceRotationTracker  # noqa: E402


def build(failing_attempts: int, retry_delays=(0.0, 0.0)):
    """A tracker whose first failing_attempts attempts lose the camera."""
    tracker = FaceRotationTracker(
        HeadTrackingSettings(model_path="landmarker.task"),
        rotation_callback=lambda *_: None,
        retry_delays=retry_delays,
    )
    attempts: list[int] = []

    def track():
        attempts.append(1)
        if len(attempts) <= failing_attempts:
            raise CameraError("could not open camera index 0")

    tracker._track = track
    return tracker, attempts


def run(tracker) -> None:
    tracker.start()
    tracker._thread.join(timeout=5)
    assert not tracker._thread.is_alive(), "the tracking thread never finished"


class TestRetrying:
    def test_a_camera_that_comes_back_is_picked_up(self, caplog):
        tracker, attempts = build(failing_attempts=1)
        run(tracker)

        assert len(attempts) == 2
        assert "gave up" not in caplog.text

    def test_retrying_stops_after_a_bounded_number_of_attempts(self, caplog):
        tracker, attempts = build(failing_attempts=99, retry_delays=(0.0, 0.0))
        run(tracker)

        assert len(attempts) == 3
        assert "gave up after 3 attempts" in caplog.text

    def test_the_delays_grow(self):
        assert list(tracker_module.RETRY_DELAYS) == sorted(tracker_module.RETRY_DELAYS)
        assert len(set(tracker_module.RETRY_DELAYS)) == len(tracker_module.RETRY_DELAYS)

    def test_each_failure_is_logged_with_the_attempt(self, caplog):
        tracker, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(tracker)

        assert "could not open camera index 0" in caplog.text
        assert "attempt 1 of 2" in caplog.text

    def test_the_last_failure_is_kept_for_the_health_report(self):
        tracker, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(tracker)

        assert isinstance(tracker.failure, CameraError)

    def test_stopping_interrupts_the_backoff(self):
        tracker, _ = build(failing_attempts=99, retry_delays=(60.0,))
        tracker.start()
        tracker.stop(timeout=5)

        assert not tracker.running

    def test_a_tracker_that_gave_up_is_not_running(self):
        tracker, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(tracker)

        assert not tracker.running


class FakeCapture:
    """A cv2.VideoCapture that accepts some properties and refuses others."""

    def __init__(self, accepts: dict[int, float]) -> None:
        self.accepts = accepts
        self.properties: dict[int, float] = {}

    def set(self, prop: int, value: float) -> bool:
        self.properties[prop] = self.accepts.get(prop, value)
        return prop in self.accepts

    def get(self, prop: int) -> float:
        return self.properties.get(prop, 0.0)


class TestCaptureSettings:
    def test_what_the_camera_accepted_is_logged(self, caplog):
        import cv2

        capture = FakeCapture({})
        caplog.set_level("INFO")
        tracker_module._apply_capture_settings(
            capture, CaptureSettings(fourcc="MJPG", width=1280, height=720, fps=30)
        )

        assert capture.get(cv2.CAP_PROP_FRAME_WIDTH) == 1280
        assert "1280x720/30fps" in caplog.text

    def test_a_rejected_format_names_both_formats(self, caplog):
        import cv2

        capture = FakeCapture({cv2.CAP_PROP_FRAME_WIDTH: 640.0, cv2.CAP_PROP_FRAME_HEIGHT: 480.0})
        tracker_module._apply_capture_settings(
            capture, CaptureSettings(fourcc=None, width=1920, height=1080, fps=60)
        )

        assert "1920x1080" in caplog.text
        assert "640x480" in caplog.text
        assert "rejected" in caplog.text

    def test_an_unset_property_is_left_to_the_driver(self):
        import cv2

        capture = FakeCapture({})
        tracker_module._apply_capture_settings(capture, CaptureSettings(None, None, None, None))

        assert cv2.CAP_PROP_FRAME_WIDTH not in capture.properties

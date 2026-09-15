import json

import numpy
import pytest

from libre_dictum.settings import HandTrackingSettings
from libre_dictum.tracking import capture
from libre_dictum.tracking.takes import FRAMES_FILE, SESSION_FILE, read_frames

from .test_hands import hand

FPS = 60.0

SCRIPT = (("left_fist", "close it"), ("right_fist", "close it"))


class FakeResult:
    """Mediapipe's hand result, as much of it as hands_from reads."""

    def __init__(self, present):
        points = [_Point(*point) for point in hand(side="left").landmarks]
        self.hand_landmarks = [points] if present else []
        self.handedness = [[_Category("left")]] if present else []


class _Point:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Category:
    def __init__(self, name):
        self.category_name = name


class FakeCamera:
    """A camera that shows a hand for on seconds, then nothing for off, forever."""

    def __init__(self, *, on=1.0, off=1.0, budget=100_000):
        self.on, self.off = on, off
        self.now = 0.0
        self.budget = budget
        self.released = False

    def read(self):
        self.now += 1.0 / FPS
        self.budget -= 1
        if self.budget < 0:
            raise KeyboardInterrupt
        return True, numpy.zeros((16, 16, 3), dtype=numpy.uint8)

    def release(self):
        self.released = True

    @property
    def present(self):
        return (self.now % (self.on + self.off)) < self.on


class FakeLandmarker:
    def __init__(self, camera):
        self.camera = camera

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def detect_for_video(self, _image, _timestamp_ms):
        return FakeResult(self.camera.present)


@pytest.fixture
def rig(monkeypatch):
    """Wire a fake camera, landmarker and clock into the capture loop."""

    def build(**kwargs):
        camera = FakeCamera(**kwargs)
        monkeypatch.setattr(capture, "open_camera", lambda *_a, **_k: camera)
        monkeypatch.setattr(capture, "hand_options", lambda _settings: None)
        monkeypatch.setattr(
            capture.HandLandmarker,
            "create_from_options",
            staticmethod(lambda _o: FakeLandmarker(camera)),
        )
        return camera

    return build


def run(directory, camera, *, rounds=2, neutral=0.0, script=SCRIPT):
    return capture.capture_gestures(
        HandTrackingSettings(model_path="unused.task"),
        directory,
        rounds=rounds,
        neutral_seconds=neutral,
        script=script,
        clock=lambda: camera.now,
    )


def recorded(directory):
    """(label, take) for every take in the file, in the order they were written."""
    with (directory / FRAMES_FILE).open() as handle:
        seen = {(f.label, f.take): None for f in read_frames(handle) if f.take}
    return list(seen)


class TestAWholeSession:
    def test_every_prompt_in_the_plan_is_recorded(self, tmp_path, rig):
        camera = rig()
        assert run(tmp_path, camera) == 0
        assert recorded(tmp_path) == [
            ("left_fist", 1),
            ("right_fist", 1),
            ("left_fist", 2),
            ("right_fist", 2),
        ]

    def test_the_camera_is_released(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera)
        assert camera.released

    def test_the_approach_is_kept_beside_the_take(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera, rounds=1)
        with (tmp_path / FRAMES_FILE).open() as handle:
            frames = list(read_frames(handle))
        labelled = [f for f in frames if f.label == "left_fist"]
        assert any(f.take == 0 and f.present for f in labelled)

    def test_the_session_notes_say_what_was_asked_for(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera)
        notes = json.loads((tmp_path / SESSION_FILE).read_text())
        assert [entry[0] for entry in notes["script"]] == ["left_fist", "right_fist"]
        assert notes["rounds"] == 2
        assert "calibration_at_capture" in notes


class TestTheNeutralPass:
    def test_it_runs_on_a_clock_rather_than_on_a_hand(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera, rounds=0, neutral=3.0, script=())
        with (tmp_path / FRAMES_FILE).open() as handle:
            frames = [f for f in read_frames(handle) if f.label == "neutral"]
        assert frames[-1].at - frames[0].at == pytest.approx(3.0, abs=0.1)
        assert all(f.take == 1 for f in frames)


class TestResuming:
    def test_a_second_run_asks_only_for_what_is_missing(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        before = recorded(tmp_path)
        run(tmp_path, rig(), rounds=2)
        after = recorded(tmp_path)
        assert before == [("left_fist", 1), ("right_fist", 1)]
        assert after == before + [("left_fist", 2), ("right_fist", 2)]

    def test_a_finished_plan_records_nothing_more(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        size = (tmp_path / FRAMES_FILE).stat().st_size
        assert run(tmp_path, rig(), rounds=1) == 0
        assert (tmp_path / FRAMES_FILE).stat().st_size == size

    def test_the_break_is_marked_so_no_statistic_spans_it(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        run(tmp_path, rig(), rounds=2)
        with (tmp_path / FRAMES_FILE).open() as handle:
            frames = list(read_frames(handle))
        assert sum(1 for f in frames if f.resumed) == 1

    def test_the_clock_carries_on_rather_than_restarting(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        run(tmp_path, rig(), rounds=2)
        with (tmp_path / FRAMES_FILE).open() as handle:
            times = [f.at for f in read_frames(handle)]
        assert times == sorted(times)


class TestASpanTooShortToBeAGesture:
    def test_it_is_not_kept_and_the_prompt_stays(self, tmp_path, rig):
        camera = rig(on=0.25, off=1.0, budget=1200)
        assert run(tmp_path, camera, rounds=1) == 0
        assert recorded(tmp_path) == []
        with (tmp_path / FRAMES_FILE).open() as handle:
            assert any(f.present for f in read_frames(handle))


class TestWhenTheCameraStops:
    def test_the_session_says_so_rather_than_spinning(self, tmp_path, rig, capsys):
        camera = rig()
        camera.read = lambda: (False, None)
        assert run(tmp_path, camera, rounds=1) == 1
        assert "stopped returning frames" in capsys.readouterr().err

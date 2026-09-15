import json

import numpy
import pytest

from libre_dictum.settings import HeadTrackingSettings
from libre_dictum.tracking import face_capture
from libre_dictum.tracking.expressions import (
    REST,
    SKIP_AFTER,
    Pass,
    read_frames,
)
from libre_dictum.tracking.gestures import FACE_FEATURES
from libre_dictum.tracking.takes import FRAMES_FILE, SESSION_FILE

FPS = 60.0

SCRIPT = (("smile", "smile"), ("cheek_puff", "puff"))

RESTING = (Pass(REST, 1.0, "sit still"),)

DRIVEN = {"mouthSmileLeft": 0.9, "mouthSmileRight": 0.9, "mouthClose": 0.9}


class _Category:
    def __init__(self, name, score):
        self.category_name, self.score = name, score


class FakeResult:
    """Mediapipe's face result, as much of it as the capture loop reads."""

    def __init__(self, camera):
        if not camera.face:
            self.face_blendshapes = []
            self.facial_transformation_matrixes = []
            return
        driven = DRIVEN if camera.expressing else {}
        self.face_blendshapes = [[_Category(name, driven.get(name, 0.0)) for name in FACE_FEATURES]]
        self.facial_transformation_matrixes = [numpy.eye(4)]


class FakeCamera:
    """A face that rests for quiet, then expresses for on and rests for off."""

    def __init__(self, *, on=1.0, off=1.0, quiet=2.5, budget=100_000, face=True):
        self.on, self.off, self.quiet = on, off, quiet
        self.face = face
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
    def expressing(self):
        if self.now < self.quiet:
            return False
        return ((self.now - self.quiet) % (self.on + self.off)) < self.on


class FakeLandmarker:
    def __init__(self, camera):
        self.camera = camera

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def detect_for_video(self, _image, _timestamp_ms):
        return FakeResult(self.camera)


@pytest.fixture
def rig(monkeypatch):
    """Wire a fake camera, landmarker and clock into the capture loop."""

    def build(**kwargs):
        camera = FakeCamera(**kwargs)
        monkeypatch.setattr(face_capture, "open_camera", lambda *_a, **_k: camera)
        monkeypatch.setattr(face_capture, "face_options", lambda _settings: None)
        monkeypatch.setattr(
            face_capture.FaceLandmarker,
            "create_from_options",
            staticmethod(lambda _o: FakeLandmarker(camera)),
        )
        return camera

    return build


def run(directory, camera, *, rounds=2, script=SCRIPT, passes=RESTING):
    return face_capture.capture_expressions(
        HeadTrackingSettings(model_path="unused.task"),
        directory,
        rounds=rounds,
        script=script,
        passes=passes,
        clock=lambda: camera.now,
    )


def recorded(directory):
    """(label, take) for every take in the file, in the order they were written."""
    with (directory / FRAMES_FILE).open() as handle:
        seen = {(f.label, f.take): None for f in read_frames(handle) if f.take}
    return list(seen)


def notes(directory):
    return json.loads((directory / SESSION_FILE).read_text())


class TestAWholeSession:
    def test_every_prompt_in_the_plan_is_recorded_with_rest_first(self, tmp_path, rig):
        camera = rig()
        assert run(tmp_path, camera) == 0
        assert recorded(tmp_path) == [
            (REST, 1),
            ("smile", 1),
            ("cheek_puff", 1),
            ("smile", 2),
            ("cheek_puff", 2),
        ]

    def test_the_camera_is_released(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera)
        assert camera.released

    def test_the_ramp_into_the_expression_is_kept_beside_the_take(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera, rounds=1)
        with (tmp_path / FRAMES_FILE).open() as handle:
            labelled = [f for f in read_frames(handle) if f.label == "smile"]
        assert any(f.take == 0 for f in labelled)

    def test_it_is_marked_as_a_face_recording(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        assert notes(tmp_path)["kind"] == "face"

    def test_the_session_notes_say_what_was_asked_for_and_what_gated_it(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        written = notes(tmp_path)
        assert [entry[0] for entry in written["script"]] == ["smile", "cheek_puff"]
        assert written["gates"]["smile"] == ["mouthSmileLeft", "mouthSmileRight"]
        assert written["features"] == list(FACE_FEATURES)


class TestTheRestPass:
    def test_it_runs_on_a_clock_because_nothing_delimits_doing_nothing(self, tmp_path, rig):
        camera = rig()
        run(tmp_path, camera, rounds=0, script=(), passes=(Pass(REST, 3.0, "still"),))
        with (tmp_path / FRAMES_FILE).open() as handle:
            frames = [f for f in read_frames(handle) if f.label == REST]
        assert frames[-1].at - frames[0].at == pytest.approx(3.0, abs=0.1)
        assert all(f.take == 1 for f in frames)

    def test_the_idle_baseline_is_measured_from_it_and_written_down(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        baseline = notes(tmp_path)["baseline"]
        assert set(baseline) == set(FACE_FEATURES)
        assert baseline["mouthSmileLeft"] == 0.0

    def test_a_gated_take_refuses_to_run_without_one(self, tmp_path, rig, capsys):
        camera = rig()
        assert run(tmp_path, camera, rounds=1, passes=()) == 1
        assert "no rest pass" in capsys.readouterr().err


class TestGivingUpOnAPerformance:
    def test_sitting_still_skips_the_whole_performance(self, tmp_path, rig):
        camera = rig(on=0.0, off=1.0, budget=int((SKIP_AFTER + 6.0) * FPS))
        assert run(tmp_path, camera, rounds=3, script=SCRIPT[:1]) == 0
        assert recorded(tmp_path) == [(REST, 1)]
        assert json.loads((tmp_path / "skipped.json").read_text()) == ["smile"]

    def test_a_resumed_session_does_not_ask_for_it_again(self, tmp_path, rig):
        camera = rig(on=0.0, off=1.0, budget=int((SKIP_AFTER + 6.0) * FPS))
        run(tmp_path, camera, rounds=3, script=SCRIPT[:1])
        assert run(tmp_path, rig(), rounds=3, script=SCRIPT[:1]) == 0
        assert recorded(tmp_path) == [(REST, 1)]

    def test_walking_away_never_skips_anything(self, tmp_path, rig):
        camera = rig(face=False, budget=int((SKIP_AFTER + 10.0) * FPS))
        run(tmp_path, camera, rounds=1, script=SCRIPT[:1])
        assert not (tmp_path / "skipped.json").exists()


class TestASpanTooShortToBeAnExpression:
    def test_it_is_not_kept_and_the_prompt_stays(self, tmp_path, rig):
        camera = rig(on=0.2, off=1.0, budget=1500)
        assert run(tmp_path, camera, rounds=1, script=SCRIPT[:1]) == 0
        assert recorded(tmp_path) == [(REST, 1)]


class TestResuming:
    def test_a_second_run_asks_only_for_what_is_missing(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        before = recorded(tmp_path)
        run(tmp_path, rig(), rounds=2)
        assert before == [(REST, 1), ("smile", 1), ("cheek_puff", 1)]
        assert recorded(tmp_path) == before + [("smile", 2), ("cheek_puff", 2)]

    def test_the_baseline_comes_back_from_the_recording_rather_than_the_notes(
        self, tmp_path, rig, capsys
    ):
        run(tmp_path, rig(), rounds=1)
        capsys.readouterr()
        run(tmp_path, rig(quiet=0.8), rounds=2)
        assert "baseline re-read" in capsys.readouterr().out

    def test_a_finished_plan_records_nothing_more(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        size = (tmp_path / FRAMES_FILE).stat().st_size
        assert run(tmp_path, rig(), rounds=1) == 0
        assert (tmp_path / FRAMES_FILE).stat().st_size == size

    def test_the_break_is_marked_so_no_statistic_spans_it(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        run(tmp_path, rig(), rounds=2)
        with (tmp_path / FRAMES_FILE).open() as handle:
            assert sum(1 for f in read_frames(handle) if f.resumed) == 1

    def test_the_clock_carries_on_rather_than_restarting(self, tmp_path, rig):
        run(tmp_path, rig(), rounds=1)
        run(tmp_path, rig(), rounds=2)
        with (tmp_path / FRAMES_FILE).open() as handle:
            times = [f.at for f in read_frames(handle)]
        assert times == sorted(times)


class TestWhenTheCameraStops:
    def test_the_session_says_so_rather_than_spinning(self, tmp_path, rig, capsys):
        camera = rig()
        camera.read = lambda: (False, None)
        assert run(tmp_path, camera, rounds=1) == 1
        assert "stopped returning frames" in capsys.readouterr().err

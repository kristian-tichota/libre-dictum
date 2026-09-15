import math
from dataclasses import replace

import numpy
import pytest

from libre_dictum.settings import HeadTrackingSettings
from libre_dictum.tracking import screen_capture
from libre_dictum.tracking.screen_capture import BETWEEN_DOTS, LEAD_IN, _run
from libre_dictum.tracking.screenmap import ScreenMap

from .test_screenmap import perfect_samples, session_samples

FPS = 60.0

GRID = ((0.5, 0.5), (0.1, 0.1), (0.9, 0.1), (0.9, 0.9))

POSES = ((0.0, 0.0), (15.0, -10.0), (-15.0, -10.0), (-15.0, 12.0))

READING = (6.0, 9.0)

REACTION = 1.2


def matrix_for(yaw, pitch):
    """A facial transformation matrix that extract_yaw_pitch reads back as this pose."""
    a, b = math.radians(yaw), math.radians(pitch)
    forward = (math.cos(b) * math.sin(a), -math.sin(b), math.cos(b) * math.cos(a))
    rows = numpy.eye(4)
    rows[:3, 2] = forward
    return rows


class FakeDisplay:
    """A HudService, as much of it as _publish uses."""

    def __init__(self, camera):
        self.camera = camera
        self.frames = []
        self.shown_at = {}

    def set_dot(self, dot):
        self.frames.append(dot)
        if dot is not None:
            self.shown_at.setdefault((dot.x, dot.y), self.camera.now)

    @property
    def dot(self):
        return self.frames[-1] if self.frames else None

    def settled_on(self, index):
        """Every dwell fraction published for one dot, in order."""
        return [f.settled for f in self.frames if f is not None and f.index == index]


class FakeCamera:
    """A camera that owns the clock, a frame at a time."""

    def __init__(self, budget=20_000):
        self.now = 0.0
        self.budget = budget
        self.reads = 0
        self.released = False

    def read(self):
        self.now += 1.0 / FPS
        self.reads += 1
        self.budget -= 1
        if self.budget < 0:
            raise AssertionError("the loop never finished")
        return True, numpy.zeros((16, 16, 3), dtype=numpy.uint8)

    def release(self):
        self.released = True


class FakeResult:
    def __init__(self, matrix):
        self.face_landmarks = [object()]
        self.facial_transformation_matrixes = [matrix]


class ScriptedHead:
    """A head that finds each dot REACTION seconds after it appears, then holds still."""

    def __init__(self, camera, display, *, reaction=REACTION, jitter=0.0, poses=POSES, finds=None):
        self.camera, self.display = camera, display
        self.reaction, self.jitter, self.poses = reaction, jitter, poses
        self.finds = finds
        self._held = READING
        self.detected = True

    def detect_for_video(self, _image, _timestamp):  # noqa: D102
        dot = self.display.dot
        wanted = self.finds is None or (dot is not None and dot.index in self.finds)
        shown_at = self.display.shown_at.get((dot.x, dot.y)) if dot is not None else None
        arrived = wanted and shown_at is not None and self.camera.now - shown_at >= self.reaction
        if arrived:
            self._held = self.poses[dot.index - 1]
        yaw, pitch = self._held
        if self.jitter:
            yaw += self.jitter if self.camera.reads % 2 else -self.jitter
        if not self.detected:
            return FakeResult(None).__class__.__new__(FakeResult)
        return FakeResult(matrix_for(yaw, pitch))


def session(*, min_cutoff=1.0, beta=0.05, **kwargs):
    """Run the loop over GRID, returning the samples, the display and the camera."""
    camera = FakeCamera()
    display = FakeDisplay(camera)
    head = ScriptedHead(camera, display, **kwargs)
    settings = HeadTrackingSettings(
        model_path="unused", filter_min_cutoff=min_cutoff, filter_beta=beta
    )
    samples = _run(head, camera, display, GRID, lambda: camera.now, settings)
    return samples, display, camera


class TestWhichPoseBelongsToWhichDot:
    def test_every_dot_records_its_own_pose(self):
        samples, _display, _camera = session()
        assert len(samples) == len(GRID)
        for sample, (x, y), (yaw, pitch) in zip(samples, GRID, POSES, strict=True):
            assert (sample.x, sample.y) == (x, y)
            assert (sample.yaw, sample.pitch) == pytest.approx((yaw, pitch), abs=0.5)

    def test_a_dot_is_not_recorded_from_the_pose_left_over_from_the_last_one(self):
        samples, _display, _camera = session(reaction=3.0)
        for sample, (yaw, pitch) in zip(samples, POSES, strict=True):
            assert (sample.yaw, sample.pitch) == pytest.approx((yaw, pitch), abs=0.5)

    def test_the_first_dot_is_not_recorded_while_the_instructions_are_still_being_read(self):
        samples, _display, camera = session(reaction=LEAD_IN - 0.5)
        assert camera.now > LEAD_IN
        assert (samples[0].yaw, samples[0].pitch) == pytest.approx(POSES[0], abs=0.5)
        assert (samples[0].yaw, samples[0].pitch) != pytest.approx(READING, abs=0.5)

    def test_a_head_that_never_finds_a_dot_skips_it_rather_than_stalling(self):
        samples, _display, camera = session(finds={1, 2, 4})
        assert [(s.x, s.y) for s in samples] == [GRID[0], GRID[1], GRID[3]]
        assert camera.now > screen_capture.GIVE_UP_SECONDS


class TestWhatThePersonInTheChairSees:
    def test_the_ring_is_left_closed_on_the_dot_it_recorded(self):
        _samples, display, _camera = session()
        for index in range(1, len(GRID) + 1):
            assert display.settled_on(index)[-1] == 1.0

    def test_the_ring_stays_empty_until_the_head_has_moved_off_the_last_dot(self):
        _samples, display, _camera = session(reaction=3.0)
        published = display.settled_on(2)
        assert published[0] == 0.0
        assert max(published) == 1.0

    def test_jitter_alone_neither_shivers_the_ring_nor_costs_a_dot(self):
        held, shown, _camera = session(jitter=0.9)
        dropped, shivering, _also = session(jitter=0.9, min_cutoff=1e4, beta=0.0)

        assert dropped == [], "unsmoothed, jitter alone is enough to lose every dot"
        assert max(shivering.settled_on(2)) == 0.0
        assert len(held) == len(GRID)
        assert max(shown.settled_on(2)) == 1.0


class TestNotSleepingThroughTheCameraQueue:
    """The camera owns the clock here, which makes "time passed without frames" unbuildable."""

    def test_the_gaps_between_dots_are_spent_reading_frames(self):
        _samples, _display, camera = session()
        gaps = LEAD_IN + BETWEEN_DOTS * len(GRID)
        assert camera.reads >= gaps * FPS * 0.9
        assert camera.now == pytest.approx(camera.reads / FPS)


class TestWhatTheReportSays:
    """The number a session is judged by, and the advice that follows from it."""

    def report(self, capsys, samples, side=3):
        assert screen_capture._report(samples, len(samples), side) == 0
        return capsys.readouterr().out

    def careful(self, sigma=0.5):
        """A session pointed at as well as a person reasonably can."""
        rng = numpy.random.default_rng(3)
        return [
            replace(s, yaw=s.yaw + rng.normal(0, sigma), pitch=s.pitch + rng.normal(0, sigma))
            for s in perfect_samples()
        ]

    def test_a_careful_session_is_not_sent_back_to_be_repeated(self, capsys):
        samples = self.careful()
        fitted = ScreenMap.fit(samples)
        assert fitted.residual_px(1920, 1080) > 25, "the rule this replaced would have fired"
        assert "That is loose" not in self.report(capsys, samples)

    def test_a_session_nobody_should_keep_is_still_sent_back(self, capsys):
        assert "That is loose" in self.report(capsys, session_samples())

    def test_it_leads_with_what_the_mapping_is_worth_and_not_the_residual(self, capsys):
        fitted = ScreenMap.fit(session_samples())
        out = self.report(capsys, session_samples())
        assert f"{fitted.residual_px(1920, 1080):.0f} px" in out
        assert f"within about {fitted.expected_error_px(1920, 1080):.0f} px" in out
        assert "noise the fit has already absorbed" in out

    def test_it_offers_a_denser_grid_until_there_is_no_denser_one(self, capsys):
        assert "--calibrate-screen 4" in self.report(capsys, session_samples(), side=3)
        assert "--calibrate-screen 7" not in self.report(capsys, session_samples(), side=6)

    def test_it_names_the_axis_the_head_moved_least_along(self, capsys):
        out = self.report(capsys, session_samples())
        assert "33° across and 15° down" in out
        assert "pointing further up and down" in out

    def test_a_head_that_never_moved_is_described_rather_than_divided_by(self, capsys):
        still = [replace(s, yaw=1.0, pitch=2.0) for s in perfect_samples()]
        with pytest.raises(ValueError, match="do not pin down"):
            ScreenMap.fit(still)
        assert screen_capture._report(still, len(still), 3) == 1

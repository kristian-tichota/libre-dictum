import logging

import pytest

from libre_dictum.app import Application
from libre_dictum.health import Health, HealthMonitor
from libre_dictum.settings import parse_settings
from tests.conftest import FakeIndicator, FakeStream

CONFIG = {
    "enable_systray": True,
    "starting_mode": "root mode",
    "modes": {
        "root mode": {"type": "vosk", "path": "m", "icon": [0, 255, 0]},
        "dictate mode": {"type": "transformer", "model_name": "whisper-turbo"},
    },
}


class TestHealthMonitor:
    def test_a_change_is_reported_once(self):
        seen: list[Health] = []
        monitor = HealthMonitor(on_change=seen.append)

        monitor.update({"camera": "unplugged"}, ["camera", "voice"])
        monitor.update({"camera": "unplugged"}, ["camera", "voice"])

        assert len(seen) == 1
        assert seen[0].failed == (("camera", "unplugged"),)
        assert seen[0].working == ("voice",)

    def test_recovery_is_a_change_too(self):
        seen: list[Health] = []
        monitor = HealthMonitor(on_change=seen.append)

        monitor.update({"camera": "unplugged"}, ["camera"])
        monitor.update({}, ["camera"])

        assert [health.ok for health in seen] == [False, True]

    def test_the_summary_names_both_sides(self):
        health = HealthMonitor().update({"camera": "unplugged"}, ["camera", "voice"])

        assert "camera (unplugged)" in health.summary()
        assert "voice" in health.summary()

    def test_a_healthy_snapshot_is_ok(self):
        assert HealthMonitor().update({}, ["voice"]).ok

    def test_the_reason_is_retrievable(self):
        health = HealthMonitor().update({"camera": "unplugged"}, [])
        assert health.reason("camera") == "unplugged"
        assert health.reason("voice") is None


@pytest.fixture
def app(backend, tmp_path):
    streams: dict[str, FakeStream] = {}

    def factory(mode, settings, on_text, on_partial=None):
        streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
        return streams[mode.name]

    application = Application(
        parse_settings(CONFIG, tmp_path), backend=backend, stream_factory=factory
    )
    application.indicator = FakeIndicator()
    application.modes._indicator = application.indicator
    application.streams = streams  # type: ignore[attr-defined] - test convenience
    yield application
    application.stop()


class TestApplicationHealth:
    def test_a_healthy_app_reports_every_subsystem_as_working(self, app):
        app.start()
        health = app.refresh_health()

        assert health.ok
        assert set(health.working) == {"mode 'root mode'", "mode 'dictate mode'", "system tray"}

    def test_a_dead_recognizer_becomes_a_fault(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("model file vanished")

        health = app.refresh_health()

        assert health.reason("mode 'root mode'") == "model file vanished"
        assert "mode 'dictate mode'" in health.working

    def test_a_fault_reaches_the_tray(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("model file vanished")
        app.refresh_health()

        assert app.indicator.fault == "mode 'root mode'"

    def test_the_fault_outlives_a_mode_switch(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("boom")
        app.refresh_health()

        app.modes.switch("dictate mode")

        assert app.indicator.fault is not None

    def test_a_recovered_subsystem_clears_the_fault(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("boom")
        app.refresh_health()

        app.streams["root mode"].failure = None
        app.refresh_health()

        assert app.indicator.fault is None

    def test_the_failure_is_logged_with_what_still_works(self, app, caplog):
        app.start()
        app.streams["root mode"].failure = RuntimeError("boom")
        app.refresh_health()

        assert "mode 'root mode'" in caplog.text
        assert "mode 'dictate mode'" in caplog.text

    def test_a_rejected_reload_is_a_fault_until_one_succeeds(self, app):
        app.start()
        app.reload()

        assert app.indicator.fault == "configuration"

    def test_the_ready_state_is_logged(self, app, caplog):
        caplog.set_level(logging.INFO)
        app.start()

        assert "2 of 2 mode(s) listening" in caplog.text
        assert "'root mode'" in caplog.text
        assert "system tray" in caplog.text

    def test_a_missing_optional_dependency_names_the_extra(self, backend, tmp_path, caplog):
        Application(parse_settings(CONFIG, tmp_path), backend=backend, stream_factory=FakeStream)

        assert "--extra system-tray" in caplog.text


class FakeTracker:
    """Stands in for the head-tracking thread."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.running = False
        self.failure: BaseException | None = None
        self.hand_failure: BaseException | None = None

    def start(self) -> None:
        self.running = True

    def stop(self, timeout: float = 2.0) -> None:
        self.running = False


class TestHeadTrackingRecovery:
    """The reload phrase is the hands-free way back from an unplugged webcam."""

    @pytest.fixture
    def app(self, backend, tmp_path):
        config = {
            **CONFIG,
            "enable_head_tracking": True,
            "ht_model_path": "landmarker.task",
        }
        settings = parse_settings(config, tmp_path)
        application = Application(settings, backend=backend, stream_factory=FakeStream)
        application.indicator = FakeIndicator()
        application._create_tracker = FakeTracker
        application.modes._load_settings = lambda: parse_settings(config, tmp_path)
        yield application
        application.stop()

    def test_head_tracking_starts_with_the_app(self, app):
        app.start()

        assert app._tracker.running

    def test_a_camera_that_gave_up_becomes_a_fault(self, app):
        app.start()
        app._tracker.running = False
        app._tracker.failure = RuntimeError("the camera stopped returning frames")

        health = app.refresh_health()

        assert health.reason("head tracking") == "the camera stopped returning frames"
        assert app.indicator.fault == "head tracking"

    def test_a_reload_restarts_a_tracker_that_gave_up(self, app):
        app.start()
        dead = app._tracker
        dead.running = False
        dead.failure = RuntimeError("camera gone")

        app.reload()

        assert app._tracker is not dead
        assert app._tracker.running
        assert app.indicator.fault is None

    def test_a_reload_leaves_a_working_tracker_alone(self, app):
        app.start()
        tracker = app._tracker

        app.reload()

        assert app._tracker is tracker

    def test_voice_control_is_untouched_by_a_dead_camera(self, app):
        app.start()
        app._tracker.failure = RuntimeError("camera gone")
        app.refresh_health()

        assert app.modes.active == "root mode"
        assert app.refresh_health().working


class TestShowingHeldInput:
    """A stuck modifier reaches the display, which is the only place it is visible."""

    def test_a_held_modifier_reaches_the_indicator(self, app):
        app.start()

        app.executor.execute("toggle(shift)")

        assert app.indicator.held.summary() == "⇧ shift held"

    def test_releasing_it_clears_the_indicator(self, app):
        app.start()

        app.executor.execute("toggle(shift)")
        app.executor.panic()

        assert app.indicator.held.empty

    def test_a_held_modifier_is_logged(self, app, caplog):
        app.start()

        with caplog.at_level(logging.INFO):
            app.executor.execute("toggle(shift)")

        assert "shift held" in caplog.text

    def test_the_watchdog_re_shows_what_is_held(self, app):
        app.start()
        app.executor.execute("toggle(shift)")
        app.indicator.held_pushes.clear()

        app.refresh_health()

        assert [held.summary() for held in app.indicator.held_pushes] == ["⇧ shift held"]

    def test_a_completed_chord_does_not_repaint(self, app):
        app.start()
        app.indicator.held_pushes.clear()

        app.executor.execute("ctrl + c")

        assert app.indicator.held_pushes == []

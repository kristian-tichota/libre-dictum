from types import SimpleNamespace

import pytest

from libre_dictum.app import Application, add_script_path
from libre_dictum.settings import parse_settings
from libre_dictum.tracking.cameras import CameraNode
from tests.conftest import FakeStream

CARD = "i-tec SOLOMON 300: i-tec SOLOMO"
FRAMES = CameraNode(index=0, card=CARD, capture=True)
METADATA = CameraNode(index=1, card=CARD, capture=False, metadata=True)

CONFIG = {
    "starting_mode": "root mode",
    "enable_pedals": True,
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "ht_enabled": True,
            "ht_dead_angle_h": 0.0,
            "ht_dead_angle_v": 0.0,
            "gestures": {
                "blink": "ctrl + c",
                "fist": {"press": "hold(left_mouse)", "release": "release(left_mouse)"},
                "letting_go": {"release": "esc"},
            },
            "commands": {"open terminal": "alt + enter"},
            "pedals": {
                "left": {"press": "hold(left_mouse)", "release": "release(left_mouse)"},
                "middle": "enter",
            },
        },
        "dictate mode": {"type": "transformer", "model_name": "whisper-turbo"},
        "scrolling": {
            "pedals": {"left": "up"},
            "gestures": {"fist": {"press": "pageup", "release": "pagedown"}},
        },
    },
}


ABSOLUTE_CONFIG = {
    "starting_mode": "mouse mode",
    "enable_head_tracking": True,
    "ht_model_path": "landmarker.task",
    "ht_pointer_law": "absolute",
    "ht_screen_calibration": {"width_mm": 597.0, "height_mm": 336.0, "distance_mm": 600.0},
    "modes": {"mouse mode": {"type": "vosk", "path": "m", "ht_enabled": True}},
}


PAIRED_CONFIG = {
    **ABSOLUTE_CONFIG,
    "modes": {
        "mouse mode": {
            "type": "vosk",
            "path": "m",
            "ht_enabled": True,
            "gestures": {
                "left_smirk": {
                    "press": "recenter() + mode(voice:precision mode)",
                    "release": "mode(voice:mouse mode)",
                }
            },
        },
        "precision mode": {
            "type": "vosk",
            "path": "m",
            "ht_enabled": True,
            "ht_pointer_law": "rate",
            "ht_max_speed_px_per_sec": 90.0,
            "ht_full_speed_angle": 10.0,
            "ht_dead_angle_h": 0.8,
            "ht_dead_angle_v": 0.8,
        },
    },
}


def _absolute_app(backend, tmp_path, monkeypatch, config=ABSOLUTE_CONFIG):
    """An application whose one mode points the head at the screen."""
    monkeypatch.setattr(Application, "_create_pedals", lambda self, settings: None)
    monkeypatch.setattr(Application, "_create_tracker", lambda self, settings: None)
    return Application(
        parse_settings(config, tmp_path),
        backend=backend,
        stream_factory=lambda mode, settings, on_text, on_partial=None: FakeStream(
            mode, settings, on_text, on_partial
        ),
    )


@pytest.fixture
def app(backend, tmp_path, monkeypatch):
    streams: dict[str, FakeStream] = {}

    def factory(mode, settings, on_text, on_partial=None):
        streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
        return streams[mode.name]

    monkeypatch.setattr(Application, "_create_pedals", lambda self, settings: None)
    application = Application(
        parse_settings(CONFIG, tmp_path), backend=backend, stream_factory=factory
    )
    application.streams = streams  # type: ignore[attr-defined] - test convenience
    return application


class TestAShutdownThatArrivesDuringStartup:
    """The longest thing this application does, and a signal in the middle of it."""

    def application(self, backend, tmp_path, *, on_build=None):
        streams: dict[str, FakeStream] = {}

        def factory(mode, settings, on_text, on_partial=None):
            streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            if on_build is not None:
                on_build(mode.name)
            return streams[mode.name]

        app = Application(parse_settings(CONFIG, tmp_path), backend=backend, stream_factory=factory)
        app.streams = streams  # type: ignore[attr-defined] - test convenience
        return app

    def test_it_stops_loading_models_rather_than_finishing_first(self, backend, tmp_path):
        built: list[str] = []
        app = self.application(backend, tmp_path, on_build=built.append)
        app._shutdown.set()

        app.start()

        assert built == [], "a shutdown asked for before the first model still loaded one"

    def test_a_signal_between_two_models_stops_at_that_one(self, backend, tmp_path):
        built: list[str] = []
        app = None

        def arrive(name):
            built.append(name)
            app.shutdown()

        app = self.application(backend, tmp_path, on_build=arrive)

        app.start()

        assert built == ["root mode"], "loading carried on past the shutdown"

    def test_a_signal_during_the_last_model_activates_nothing_either(self, backend, tmp_path):
        app = None

        def arrive(name):
            if name == "dictate mode":
                app.shutdown()

        app = self.application(backend, tmp_path, on_build=arrive)

        app.start()

        assert app.modes.active is None, "a shutdown during the last model still switched mode"

    def test_no_mode_is_activated_on_the_way_out(self, backend, tmp_path):
        app = self.application(backend, tmp_path)
        app._shutdown.set()

        app.start()

        assert app.modes.active is None

    def test_running_it_closes_the_devices_even_though_it_never_finished(self, backend, tmp_path):
        app = self.application(backend, tmp_path)
        app._shutdown.set()

        app.run()

        assert backend.closed

    def test_a_start_that_raises_still_releases_the_devices(self, backend, tmp_path, monkeypatch):
        app = self.application(backend, tmp_path)

        def boom() -> None:
            raise RuntimeError("a camera that was not there")

        monkeypatch.setattr(app, "_sync_head_tracking", boom)

        with pytest.raises(RuntimeError):
            app.run()

        assert backend.closed, "a failed start left /dev/uinput open"


class TestStartup:
    def test_starting_starts_every_mode(self, app):
        app.start()
        assert all(stream.started for stream in app.streams.values())

    def test_the_tray_is_not_created_when_it_is_disabled(self, app):
        assert app.indicator is None

    def test_head_tracking_is_not_started_when_it_is_disabled(self, app):
        app.start()
        assert app._tracker is None

    def test_recognized_text_reaches_the_command_table(self, app, backend):
        app.start()
        app.streams["root mode"].on_text("open terminal")

        assert backend.sequence == "alt↓ enter↓ enter↑ alt↑"

    def test_a_spoken_mode_name_switches_mode(self, app):
        app.start()
        app.streams["root mode"].on_text("dictate mode")

        assert app.modes.active == "dictate mode"


class TestShutdown:
    def test_run_returns_once_shutdown_is_requested(self, app, backend):
        app.shutdown()
        app.run()

        assert backend.closed

    def test_stopping_releases_every_held_key(self, app, backend):
        app.start()
        app.executor.execute("hold(ctrl)")
        backend.clear()

        app.stop()
        assert "ctrl↑" in backend.events

    def test_stopping_stops_the_recognizers(self, app):
        app.start()
        app.stop()

        assert all(stream.stopped for stream in app.streams.values())


class TestHeadTrackingCallbacks:
    def test_rotation_moves_the_pointer(self, app, backend):
        app.start()
        app._on_rotation(10.0, 4.0, 1 / 60, 10.0, 4.0)

        ((dx, dy),) = backend.movements
        assert dx < 0
        assert dy > 0

    def test_the_first_pose_has_no_interval_so_nothing_moves(self, app, backend):
        app.start()
        app._on_rotation(10.0, 4.0, 0.0, 10.0, 4.0)

        assert backend.movements == []

    def test_a_mode_without_pointer_control_does_not_move_the_pointer(self, app, backend):
        app.start()
        app.modes.switch("dictate mode")
        app._on_rotation(10.0, 4.0, 1 / 60, 10.0, 4.0)

        assert backend.movements == []

    def test_the_absolute_law_puts_the_pointer_where_the_head_points(
        self, backend, tmp_path, monkeypatch
    ):
        app = _absolute_app(backend, tmp_path, monkeypatch)
        app.start()
        app._on_rotation(0.0, 0.0, 1 / 60, 0.0, 0.0)

        assert backend.positions == [(0.5, 0.5)]
        assert backend.movements == []

    def test_the_absolute_law_reads_the_pose_and_not_the_offset_from_neutral(
        self, backend, tmp_path, monkeypatch
    ):
        app = _absolute_app(backend, tmp_path, monkeypatch)
        app.start()
        app._on_rotation(30.0, -20.0, 1 / 60, 0.0, 0.0)

        assert backend.positions == [(0.5, 0.5)]

    def test_turning_the_head_right_moves_the_pointer_right(self, backend, tmp_path, monkeypatch):
        app = _absolute_app(backend, tmp_path, monkeypatch)
        app.start()
        app._on_rotation(0.0, 0.0, 1 / 60, -15.0, 0.0)

        ((x, y),) = backend.positions
        assert x > 0.5
        assert y == pytest.approx(0.5)

    def test_tipping_the_head_down_moves_the_pointer_down(self, backend, tmp_path, monkeypatch):
        app = _absolute_app(backend, tmp_path, monkeypatch)
        app.start()
        app._on_rotation(0.0, 0.0, 1 / 60, 0.0, 15.0)

        ((x, y),) = backend.positions
        assert x == pytest.approx(0.5)
        assert y > 0.5

    def test_a_held_shape_swaps_the_law_and_the_release_still_finds_its_own_binding(
        self, backend, tmp_path, monkeypatch
    ):
        app = _absolute_app(backend, tmp_path, monkeypatch, PAIRED_CONFIG)
        app.start()
        app._on_rotation(0.0, 0.0, 1 / 60, -15.0, 0.0)
        assert backend.positions and not backend.movements

        app._on_gesture("left_smirk", True)
        assert app.modes.layer_state().voice == "precision mode"
        assert app.modes.layer_state().gesture == "mouse mode"
        backend.clear()
        app._on_rotation(-15.0, 0.0, 1 / 60, -15.0, 0.0)
        assert backend.movements and not backend.positions

        app._on_gesture("left_smirk", False)
        assert app.modes.layer_state().voice == "mouse mode"
        backend.clear()
        app._on_rotation(0.0, 0.0, 1 / 60, -15.0, 0.0)
        assert backend.positions and not backend.movements

    def test_arriving_in_the_slow_law_takes_the_head_where_it_is_as_neutral(
        self, backend, tmp_path, monkeypatch
    ):
        app = _absolute_app(backend, tmp_path, monkeypatch, PAIRED_CONFIG)
        recentred = []
        app.start()
        app._tracker = SimpleNamespace(
            recenter=lambda: recentred.append(True),
            stop=lambda: None,
            failure=None,
            hand_failure=None,
        )

        app._on_gesture("left_smirk", True)
        assert recentred == [True]

    def test_a_gesture_runs_the_mode_action(self, app, backend):
        app.start()
        app._on_gesture("blink", True)

        assert backend.sequence == "ctrl↓ c↓ c↑ ctrl↑"

    def test_a_gesture_the_mode_ignores_does_nothing(self, app, backend):
        app.start()
        app._on_gesture("pucker", True)

        assert backend.events == []

    def test_a_gesture_is_read_off_the_gesture_layer(self, app, backend):
        app.start()
        app.modes.switch("gesture:dictate mode")
        app._on_gesture("blink", True)

        assert backend.events == []

    def test_a_held_gesture_holds_a_button_and_letting_go_releases_it(self, app, backend):
        app.start()
        app._on_gesture("fist", True)
        assert backend.sequence == "left_mouse↓"

        app._on_gesture("fist", False)

        assert backend.sequence == "left_mouse↓ left_mouse↑"

    def test_a_gesture_bound_only_on_the_press_is_silent_when_it_re_arms(self, app, backend):
        app.start()
        app._on_gesture("blink", True)
        before = backend.sequence

        app._on_gesture("blink", False)

        assert backend.sequence == before

    def test_a_gesture_bound_only_on_the_release_is_silent_when_it_fires(self, app, backend):
        app.start()
        app._on_gesture("letting_go", True)
        assert backend.events == []

        app._on_gesture("letting_go", False)

        assert backend.sequence == "esc↓ esc↑"


class TestPedalCallbacks:
    def press(self, name):
        from libre_dictum.pedals.reader import PedalEvent

        return PedalEvent(name=name, pressed=True)

    def release(self, name):
        from libre_dictum.pedals.reader import PedalEvent

        return PedalEvent(name=name, pressed=False)

    def test_a_press_runs_the_binding(self, app, backend):
        app.start()
        app._on_pedal(self.press("middle"))

        assert backend.sequence == "enter↓ enter↑"

    def test_a_bare_binding_says_nothing_on_the_release(self, app, backend):
        app.start()
        app._on_pedal(self.press("middle"))
        backend.clear()
        app._on_pedal(self.release("middle"))

        assert backend.events == []

    def test_both_edges_of_a_hold_reach_the_backend(self, app, backend):
        app.start()
        app._on_pedal(self.press("left"))
        app._on_pedal(self.release("left"))

        assert backend.sequence == "left_mouse↓ left_mouse↑"

    def test_a_pedal_the_layout_ignores_does_nothing(self, app, backend):
        app.start()
        app._on_pedal(self.press("right"))

        assert backend.events == []

    def test_a_pedal_is_read_off_the_pedal_layer(self, app, backend):
        app.start()
        app.modes.switch("pedal:scrolling")
        app._on_pedal(self.press("left"))

        assert backend.sequence == "up↓ up↑"

    def test_a_pedal_can_move_the_voice_layer_on_its_own(self, app):
        app.start()
        app.modes.switch("pedal:scrolling")
        app.executor.execute("mode(voice:dictate mode)")

        assert app.modes.active == "dictate mode"
        assert app.modes.layer_state().pedal == "scrolling"


class TestAReleaseRunsWhatItsPressCaptured:
    """A hold outlives its mode, and so does its release."""

    def press(self, name):
        from libre_dictum.pedals.reader import PedalEvent

        return PedalEvent(name=name, pressed=True)

    def release(self, name):
        from libre_dictum.pedals.reader import PedalEvent

        return PedalEvent(name=name, pressed=False)

    def test_a_held_gesture_survives_its_own_layer_moving(self, app, backend):
        app.start()
        app._on_gesture("fist", True)
        assert backend.sequence == "left_mouse↓"

        app.modes.switch("dictate mode")
        app._on_gesture("fist", False)

        assert backend.sequence == "left_mouse↓ left_mouse↑"

    def test_a_held_pedal_survives_its_own_layer_moving(self, app, backend):
        app.start()
        app._on_pedal(self.press("left"))
        assert backend.sequence == "left_mouse↓"

        app.modes.switch("pedal:scrolling")
        app._on_pedal(self.release("left"))

        assert backend.sequence == "left_mouse↓ left_mouse↑"

    def test_the_release_the_press_saw_wins_over_the_one_bound_now(self, app, backend):
        app.start()
        app._on_gesture("fist", True)
        assert backend.sequence == "left_mouse↓"

        app.modes.switch("gesture:scrolling")
        app._on_gesture("fist", False)

        assert backend.sequence == "left_mouse↓ left_mouse↑"

    def test_a_press_nothing_bound_stays_silent_however_the_layer_moves(self, app, backend):
        app.start()
        app.modes.switch("gesture:dictate mode")
        app._on_gesture("letting_go", True)

        app.modes.switch("gesture:root mode")
        app._on_gesture("letting_go", False)

        assert backend.events == []

    def test_a_reload_drops_what_the_press_captured(self, app, backend, monkeypatch):
        app.start()
        app._on_gesture("letting_go", True)
        monkeypatch.setattr(app.modes, "_load_settings", lambda: app.settings)
        app.reload()

        app._on_gesture("letting_go", False)

        assert backend.events == []

    def test_a_rejected_reload_keeps_it(self, app, backend, monkeypatch):
        from libre_dictum.errors import ConfigError

        def rejected():
            raise ConfigError("a broken edit")

        app.start()
        app._on_gesture("fist", True)
        monkeypatch.setattr(app.modes, "_load_settings", rejected)
        app.reload()

        app._on_gesture("fist", False)

        assert backend.sequence == "left_mouse↓ left_mouse↑"


class TestScriptPath:
    def test_the_config_directory_becomes_importable(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "path", list(sys.path))
        add_script_path(tmp_path)

        assert str(tmp_path) in sys.path
        assert (tmp_path / "scripts").is_dir()

    def test_adding_it_twice_does_not_duplicate_it(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "path", list(sys.path))
        add_script_path(tmp_path)
        add_script_path(tmp_path)

        assert sys.path.count(str(tmp_path)) == 1


class TestCommandLine:
    def test_a_broken_config_exits_with_a_message(self, tmp_path, caplog):
        from libre_dictum.main import main

        (tmp_path / "config.json").write_text('{"modes": {"root": {"type": "vosk"}}}')

        assert main(["--config-dir", str(tmp_path)]) == 1
        assert "'root'" in caplog.text

    def test_a_missing_config_exits_with_a_message(self, tmp_path, caplog):
        from libre_dictum.main import main

        assert main(["--config-dir", str(tmp_path / "nowhere")]) == 1
        assert "is missing" in caplog.text

    def test_the_defaults_point_at_the_user_config(self):
        from libre_dictum.main import parse_args

        assert parse_args([]).config_dir.name == "libre-dictum"
        assert parse_args([]).log_level == "INFO"


class TestListingCameras:
    """--cameras answers "which of these numbers is my webcam" before a session."""

    def nodes(self, monkeypatch, *found, by_id=None):
        from libre_dictum import main as main_module
        from libre_dictum.tracking import v4l2

        monkeypatch.setattr(v4l2, "enumerate_cameras", lambda *_: found)
        monkeypatch.setattr(v4l2, "stable_path", lambda index, *_: by_id)
        return main_module

    def test_it_names_the_node_it_would_open(self, monkeypatch, capsys):
        main_module = self.nodes(monkeypatch, FRAMES, METADATA)

        assert main_module.main(["--cameras"]) == 0
        printed = capsys.readouterr().out
        assert "would open:    video0 (i-tec SOLOMON 300: i-tec SOLOMO, frames)" in printed
        assert "video1 (i-tec SOLOMON 300: i-tec SOLOMO, metadata)" in printed

    def test_it_prints_a_camera_value_that_can_be_pasted(self, monkeypatch, capsys):
        main_module = self.nodes(monkeypatch, FRAMES)
        main_module.main(["--cameras"])

        assert '"camera": "i-tec SOLOMON 300"' in capsys.readouterr().out

    def test_it_offers_the_by_id_path_when_the_kernel_made_one(self, monkeypatch, capsys):
        by_id = "/dev/v4l/by-id/usb-Image__i-tec_SOLOMON_300_MT368-001-video-index0"
        main_module = self.nodes(monkeypatch, FRAMES, by_id=by_id)
        main_module.main(["--cameras"])

        assert f'"camera": "{by_id}"' in capsys.readouterr().out

    def test_no_camera_is_a_message_and_a_failure(self, monkeypatch, capsys):
        main_module = self.nodes(monkeypatch)

        assert main_module.main(["--cameras"]) == 1
        assert "no video devices at all" in capsys.readouterr().out

    def test_it_opens_no_session(self, monkeypatch, capsys):
        def refuse(*_args, **_kwargs):
            raise AssertionError("--cameras built an application")

        monkeypatch.setattr(Application, "from_config_dir", refuse)
        self.nodes(monkeypatch, FRAMES).main(["--cameras"])

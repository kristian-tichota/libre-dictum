from __future__ import annotations

import pytest

from libre_dictum import app as app_module
from libre_dictum.app import Application
from libre_dictum.errors import ConfigError
from libre_dictum.layers import Layer
from libre_dictum.pedals.reader import PedalEvent
from libre_dictum.settings import EdgeBinding, parse_settings
from tests.conftest import FakeStream

CONFIG = {
    "starting_mode": "root mode",
    "enable_pedals": True,
    "enable_head_tracking": True,
    "ht_model_path": "landmarker.task",
    "ht_custom_gestures": {
        "blink": {"eyeBlinkLeft": {"min": 0.5}, "eyeBlinkRight": {"min": 0.5}},
        "fist": {"handFist": {"min": 0.8}},
        "both_open": {"bothHandsPresent": {"min": 0.5}},
        "left_open": {"leftHandOpen": {"min": 0.9}},
    },
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "ht_enabled": True,
            "commands": {
                "open terminal": "alt + enter",
                "go to sleep": "sleep()",
                "wake up": "wake()",
                "rest my hands": "sleep(gesture)",
                "rest my feet": "sleep(pedal)",
                "show the sheet": "hud(open)",
                "wake and type": "wake() + type(hi)",
            },
            "gestures": {
                "blink": "ctrl + c",
                "fist": {"press": "hold(left_mouse)", "release": "release(left_mouse)"},
                "both_open": "sleep(gesture)",
                "left_open": "wake(gesture)",
            },
            "pedals": {
                "left": {"press": "hold(ctrl)", "release": "release(ctrl)"},
                "middle": {"press": "hold(shift)", "release": "release(shift)"},
                "right": {"press": "hold(alt)", "release": "release(alt)"},
                "left left": "sleep()",
                "right right": "wake()",
            },
        },
        "dictate mode": {
            "type": "transformer",
            "model_name": "whisper-turbo",
            "commands": {"go to sleep": "sleep()"},
        },
    },
}


@pytest.fixture
def app(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(Application, "_create_pedals", lambda self, settings: None)
    monkeypatch.setattr(Application, "_create_tracker", lambda self, settings: None)
    application = Application(
        parse_settings(CONFIG, tmp_path),
        backend=backend,
        stream_factory=lambda mode, settings, on_text, on_partial=None: FakeStream(
            mode, settings, on_text, on_partial
        ),
    )
    application.start()
    return application


def confirm(app, phrase):
    """Say a sleep or wake phrase twice, which is what it takes."""
    app.dispatcher.handle(phrase)
    app.dispatcher.handle(phrase)


def press(name):
    return PedalEvent(name=name, pressed=True)


def release(name, *, owed=False):
    return PedalEvent(name=name, pressed=False, owed=owed)


def foot_tap(app, name, *, typing=False):
    """One bare press-and-release of a pedal -- or a used one, with something typed."""
    app._on_pedal(press(name))
    if typing:
        app.executor.execute("c")
    app._on_pedal(release(name))


class TestNothingHappensOnTheFirstAsk:
    def test_one_ask_changes_nothing(self, app):
        app.dispatcher.handle("go to sleep")

        assert not app.asleep()
        assert not app.asleep(Layer.VOICE)

    def test_the_second_ask_applies_it(self, app):
        confirm(app, "go to sleep")

        assert app.asleep()

    def test_a_gesture_can_arm_what_a_pedal_confirms(self, app, backend):
        app._on_gesture("both_open", True)
        assert not app.asleep(Layer.GESTURE)

        app._on_gesture("both_open", True)

        assert app.asleep(Layer.GESTURE)


class TestAPedalRunOfBareTaps:
    """What a modifier pedal can do without giving up its day job."""

    def test_two_bare_taps_arm_it(self, app):
        foot_tap(app, "left")
        assert not app.asleep()

        foot_tap(app, "left")

        assert not app.asleep()
        assert app._sleep.view(0.0).prompt == "sleep? again to confirm"

    def test_four_arm_it_and_confirm_it(self, app):
        for _ in range(4):
            foot_tap(app, "left")

        assert app.asleep()

    def test_the_taps_still_do_their_own_job(self, app, backend):
        foot_tap(app, "left")

        assert backend.sequence == "ctrl↓ ctrl↑"

    def test_a_pedal_actually_being_used_is_not_a_tap(self, app):
        for _ in range(4):
            foot_tap(app, "left", typing=True)

        assert not app.asleep()

    def test_nor_is_one_held_too_long(self, app, monkeypatch):
        clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        monkeypatch.setattr(app_module.time, "monotonic", lambda: next(clock))
        for _ in range(4):
            app._on_pedal(press("left"))
            app._on_pedal(release("left"))

        assert not app.asleep()

    def test_taps_too_far_apart_are_not_a_run(self, app, monkeypatch):
        moments = iter([0.0, 0.05, 2.0, 2.05, 4.0, 4.05, 6.0, 6.05])
        monkeypatch.setattr(app_module.time, "monotonic", lambda: next(moments))
        for _ in range(4):
            app._on_pedal(press("left"))
            app._on_pedal(release("left"))

        assert not app.asleep()

    def test_a_different_pedal_run_is_a_different_binding(self, app):
        for _ in range(4):
            foot_tap(app, "left")
        assert app.asleep()

        for _ in range(4):
            foot_tap(app, "right")

        assert not app.asleep()

    def test_a_run_is_forgotten_once_it_matched(self, app):
        for _ in range(3):
            foot_tap(app, "left")

        assert not app.asleep()

    def test_a_longer_run_fires_when_it_is_the_one_bound(self, app, backend, monkeypatch):
        mode = app.modes.mode_for(Layer.PEDAL)
        assert mode is not None
        monkeypatch.setitem(mode.pedals, "middle middle middle", EdgeBinding(press="hud(open)"))
        shown: list[str] = []
        monkeypatch.setattr(app.executor, "_display", shown.append)

        foot_tap(app, "middle")
        foot_tap(app, "middle")
        assert shown == []

        foot_tap(app, "middle")

        assert shown == ["open"]

    def test_a_shorter_run_bound_alongside_it_gets_there_first(self, app, backend, monkeypatch):
        mode = app.modes.mode_for(Layer.PEDAL)
        assert mode is not None
        monkeypatch.setitem(mode.pedals, "left left left", EdgeBinding(press="hud(open)"))
        shown: list[str] = []
        monkeypatch.setattr(app.executor, "_display", shown.append)

        for _ in range(3):
            foot_tap(app, "left")

        assert shown == []
        assert app._sleep.view(0.0).prompt == "sleep? again to confirm"

    def test_a_board_that_dies_under_a_foot_completes_nothing(self, app, backend):
        foot_tap(app, "left")
        app._on_pedal(press("left"))
        backend.clear()

        app._on_pedal(release("left", owed=True))

        assert backend.sequence == "ctrl↑"
        assert not app.asleep()
        assert app._sleep.view(0.0).prompt is None

    def test_a_sleeping_pedal_layer_can_still_be_woken_by_taps(self, app):
        for _ in range(4):
            foot_tap(app, "left")
        assert app.asleep()

        for _ in range(4):
            foot_tap(app, "right")

        assert not app.asleep()


class TestTheArmingClearsItself:
    """A prompt nobody can clear is a prompt nobody reads."""

    @pytest.fixture
    def timers(self, monkeypatch):
        """Every timer the app schedules, un-started, for a test to fire by hand."""
        scheduled: list[tuple[float, object]] = []

        class FakeTimer:
            def __init__(self, interval, function):
                self.interval, self.function = interval, function
                self.name = ""
                self.daemon = False
                scheduled.append((interval, function))

            def start(self):
                pass

            def cancel(self):
                self.cancelled = True

        monkeypatch.setattr(app_module.threading, "Timer", FakeTimer)
        return scheduled

    def test_arming_schedules_one_past_the_deadline(self, app, timers):
        app.dispatcher.handle("go to sleep")

        ((interval, _),) = timers
        assert interval > app.settings.sleep_confirm_seconds

    def test_and_it_republishes_with_the_prompt_gone(self, app, timers, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
        app.dispatcher.handle("go to sleep")
        assert app._sleep_shown.prompt == "sleep? again to confirm"

        ((_, expire),) = timers
        clock[0] += app.settings.sleep_confirm_seconds + 1.0
        expire()

        assert app._sleep_shown.prompt is None

    def test_a_refusal_schedules_one_too(self, app, timers):
        for _ in range(4):
            foot_tap(app, "left")
        timers.clear()

        confirm(app, "wake up")

        assert timers, "a refusal left its notice on screen with nothing to clear it"

    def test_a_fresh_arming_replaces_the_one_before_it(self, app, timers):
        app.dispatcher.handle("go to sleep")
        app.dispatcher.handle("rest my hands")

        assert len(timers) == 2
        assert getattr(app._sleep_timer, "cancelled", False) is False


class TestWhatASleepingMechanismDrops:
    def test_a_sleeping_gesture_layer_runs_no_gesture(self, app, backend):
        confirm(app, "rest my hands")
        backend.clear()

        app._on_gesture("blink", True)

        assert backend.events == []

    def test_the_other_mechanisms_carry_on(self, app, backend):
        confirm(app, "rest my hands")
        backend.clear()

        app._on_pedal(press("middle"))
        app.dispatcher.handle("open terminal")

        assert backend.sequence == "shift↓ alt↓ enter↓ enter↑ alt↑"

    def test_a_sleeping_pedal_layer_runs_no_pedal(self, app, backend):
        confirm(app, "rest my feet")
        assert app.asleep(Layer.PEDAL)
        backend.clear()

        app._on_pedal(press("middle"))

        assert backend.events == []

    def test_a_sleeping_voice_layer_runs_no_command(self, app, backend):
        confirm(app, "go to sleep")
        backend.clear()

        app.dispatcher.handle("open terminal")

        assert backend.events == []

    def test_a_sleeping_voice_layer_does_not_switch_modes(self, app):
        confirm(app, "go to sleep")

        app.dispatcher.handle("dictate mode")

        assert app.modes.active == "root mode"

    def test_nor_on_the_previous_mode_keyword(self, app):
        app.modes.switch("dictate mode")
        confirm(app, "go to sleep")

        app.dispatcher.handle("previous mode")

        assert app.modes.active == "dictate mode"

    def test_a_mixed_response_is_dropped_whole(self, app, backend):
        confirm(app, "go to sleep")
        backend.clear()

        app.dispatcher.handle("wake and type")

        assert backend.events == []
        assert app.asleep()


class TestWhatItKeeps:
    def test_the_wake_phrase(self, app):
        confirm(app, "go to sleep")

        confirm(app, "wake up")

        assert not app.asleep()

    def test_the_wake_gesture_of_a_gesture_that_slept_it(self, app):
        app._on_gesture("both_open", True)
        app._on_gesture("both_open", True)
        assert app.asleep(Layer.GESTURE)

        app._on_gesture("left_open", True)
        app._on_gesture("left_open", True)

        assert not app.asleep(Layer.GESTURE)

    def test_the_panic_phrase(self, app, backend):
        app.executor.execute("hold(shift)")
        confirm(app, "go to sleep")
        assert backend.sequence.endswith("shift↑")
        app.executor.execute("hold(shift)")

        app.dispatcher.handle("release everything")

        assert not app.executor.state.modifiers_held

    def test_panic_does_not_wake(self, app):
        confirm(app, "go to sleep")

        app.dispatcher.handle("release everything")

        assert app.asleep()

    def test_a_display_phrase(self, app):
        confirm(app, "go to sleep")

        app.dispatcher.handle("show the sheet")

        assert app.asleep()


class TestWhoeverSleptItWakesIt:
    """A sleep entered with the feet is one only the feet may undo."""

    def test_a_pedal_sleep_is_not_undone_by_voice(self, app):
        for _ in range(4):
            foot_tap(app, "left")
        assert app.asleep()

        confirm(app, "wake up")

        assert app.asleep()

    def test_nor_by_a_gesture(self, app):
        for _ in range(4):
            foot_tap(app, "left")

        app._on_gesture("left_open", True)
        app._on_gesture("left_open", True)

        assert app.asleep()

    def test_and_the_pedals_still_wake_it(self, app):
        for _ in range(4):
            foot_tap(app, "left")

        for _ in range(4):
            foot_tap(app, "right")

        assert not app.asleep()

    def test_a_spoken_sleep_is_not_undone_by_the_pedals(self, app):
        confirm(app, "go to sleep")

        for _ in range(4):
            foot_tap(app, "right")

        assert app.asleep()

    def test_the_mechanism_that_confirms_it_owns_it(self, app):
        app._on_gesture("both_open", True)
        app.dispatcher.handle("rest my hands")
        assert app.asleep(Layer.GESTURE)

        app._on_gesture("left_open", True)
        app._on_gesture("left_open", True)

        assert app.asleep(Layer.GESTURE)

    def test_a_refusal_is_not_silent(self, app, caplog):
        for _ in range(4):
            foot_tap(app, "left")

        confirm(app, "wake up")

        assert "refused" in caplog.text
        assert app._sleep.view(0.0).notice is not None

    def test_and_the_display_says_which_mechanism_to_use(self, app):
        for _ in range(4):
            foot_tap(app, "left")

        view = app._sleep.view(0.0)
        assert view.owners == frozenset({Layer.PEDAL})
        assert view.summary() == "asleep (wake with pedal)"

    def test_a_reload_answers_to_nobody(self, app, monkeypatch):
        for _ in range(4):
            foot_tap(app, "left")
        monkeypatch.setattr(app.modes, "_load_settings", lambda: app.settings)

        app.dispatcher.handle("reload config")

        assert not app.asleep()


class TestNothingIsStranded:
    def test_going_to_sleep_releases_what_was_held(self, app, backend):
        app._on_gesture("fist", True)
        assert backend.sequence.endswith("left_mouse↓")

        confirm(app, "go to sleep")

        assert backend.sequence.endswith("left_mouse↑")

    def test_and_drops_the_release_it_had_captured(self, app, backend):
        app._on_pedal(press("middle"))
        confirm(app, "go to sleep")
        confirm(app, "wake up")
        backend.clear()

        app._on_pedal(release("middle"))

        assert backend.events == []

    def test_a_press_that_was_dropped_leaves_no_release_behind(self, app, backend):
        confirm(app, "rest my hands")
        app._on_gesture("fist", True)
        confirm(app, "wake up")
        backend.clear()

        app._on_gesture("fist", False)

        assert backend.events == []

    def test_a_reload_wakes_everything(self, app, monkeypatch):
        confirm(app, "go to sleep")
        monkeypatch.setattr(app.modes, "_load_settings", lambda: app.settings)

        app.reload()

        assert not app.asleep()

    def test_the_reload_phrase_is_heard_while_asleep(self, app, monkeypatch):
        confirm(app, "go to sleep")
        monkeypatch.setattr(app.modes, "_load_settings", lambda: app.settings)

        app.dispatcher.handle("reload config")

        assert not app.asleep()

    def test_a_rejected_reload_leaves_it_asleep(self, app, monkeypatch):
        def rejected():
            raise ConfigError("a broken edit")

        confirm(app, "go to sleep")
        monkeypatch.setattr(app.modes, "_load_settings", rejected)

        app.reload()

        assert app.asleep()


class TestThePointer:
    def test_everything_asleep_parks_the_pointer(self, app, backend):
        confirm(app, "go to sleep")
        backend.clear()

        app._on_rotation(20.0, 0.0, 1 / 60, 20.0, 0.0)

        assert backend.movements == [] and backend.positions == []

    def test_one_mechanism_asleep_leaves_it_alone(self, app, backend):
        confirm(app, "rest my hands")
        backend.clear()

        app._on_rotation(20.0, 0.0, 1 / 60, 20.0, 0.0)

        assert backend.movements != []


class TestALayerNameThatIsNotAMechanism:
    def test_it_is_a_load_error(self, tmp_path):
        config = {
            "modes": {
                "root mode": {"type": "vosk", "path": "m", "commands": {"nap": "sleep(mouth)"}}
            }
        }

        with pytest.raises(ConfigError, match="mouth"):
            parse_settings(config, tmp_path)

    def test_a_pedal_run_naming_something_that_is_not_a_pedal_is_refused(self, tmp_path):
        config = {
            "enable_pedals": True,
            "modes": {
                "root mode": {"type": "vosk", "path": "m", "pedals": {"left bogus": "sleep()"}}
            },
        }

        with pytest.raises(ConfigError, match="bogus"):
            parse_settings(config, tmp_path)

    def test_a_pedal_run_of_real_pedals_is_accepted(self, tmp_path):
        config = {
            "enable_pedals": True,
            "modes": {
                "root mode": {"type": "vosk", "path": "m", "pedals": {"left left": "sleep()"}}
            },
        }

        assert "left left" in parse_settings(config, tmp_path).modes["root mode"].pedals

    def test_a_tap_window_of_zero_is_refused(self, tmp_path):
        config = {
            "enable_pedals": True,
            "pedal": {"tap_ms": 0},
            "modes": {"root mode": {"type": "vosk", "path": "m"}},
        }

        with pytest.raises(ConfigError, match="pedal.tap_ms"):
            parse_settings(config, tmp_path)

    def test_and_so_is_a_bad_confirmation_window(self, tmp_path):
        config = {
            "sleep_confirm_seconds": 0,
            "modes": {"root mode": {"type": "vosk", "path": "m"}},
        }

        with pytest.raises(ConfigError, match="sleep_confirm_seconds"):
            parse_settings(config, tmp_path)

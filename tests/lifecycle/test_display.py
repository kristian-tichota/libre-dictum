from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from libre_dictum.app import Application, ModeDisplays
from libre_dictum.settings import parse_settings
from tests.conftest import FakeIndicator, FakeStream
from tests.overlay.conftest import DEADLINE_SECONDS, Display

CONFIG = {
    "enable_systray": True,
    "starting_mode": "root mode",
    "modes": {
        "::shared": {"commands": {"step up": "up", "step down": "down"}},
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [0, 255, 0],
            "banned_strings": ["the"],
            "imports": ["::shared"],
            "commands": {
                "go to sleep": "sleep()",
                "rest my hands": "sleep(gesture)",
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer overview": "hud(group:buffer)",
                "zoom in": "ctrl + = + hud(close)",
            },
        },
        "dictate mode": {
            "type": "transformer",
            "model_name": "whisper-turbo",
            "imports": ["::shared"],
        },
    },
}


@pytest.fixture
def socket_path(tmp_path_factory) -> Path:
    """Short enough for AF_UNIX, which a test-named tmp_path is not."""
    return tmp_path_factory.mktemp("hud") / "s.sock"


@pytest.fixture
def config_dir(tmp_path) -> Path:
    """A real configuration directory, so that reload() has something to re-read."""
    (tmp_path / "m").mkdir()
    (tmp_path / "config.json").write_text(json.dumps(CONFIG))
    return tmp_path


@pytest.fixture
def app(backend, config_dir, socket_path):
    streams: dict[str, FakeStream] = {}

    def factory(mode, settings, on_text, on_partial=None):
        streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
        return streams[mode.name]

    application = Application(
        parse_settings(CONFIG, config_dir),
        backend=backend,
        stream_factory=factory,
        hud_socket=socket_path,
    )
    application.streams = streams  # type: ignore[attr-defined] - test convenience
    yield application
    application.stop()


class TestWhatReachesTheSocket:
    def test_the_configuration_is_published_before_anything_starts(self, app):
        app.start()

        catalogue = app.hud._catalogue
        assert [mode.name for mode in catalogue.modes if mode.runnable] == [
            "root mode",
            "dictate mode",
        ]
        assert catalogue.mode("root mode").colour == (0, 255, 0)

    def test_a_group_arrives_with_the_phrase_that_opens_it(self, app):
        app.start()

        group = app.hud._catalogue.mode("root mode").group("buffer")
        assert group.count == 3
        assert group.phrases == ("open buffer",)

    def test_the_active_mode_arrives(self, app):
        app.start()

        assert app.hud.state().mode == "root mode"

    def test_a_mode_switch_arrives(self, app):
        app.start()
        app.modes.switch("dictate mode")

        assert app.hud.state().mode == "dictate mode"

    def test_a_held_modifier_arrives(self, app):
        app.start()
        app.executor.execute("hold(shift)")

        assert [key.key for key in app.hud.state().held] == ["shift"]

    def test_releasing_it_arrives_too(self, app):
        app.start()
        app.executor.execute("hold(shift)")
        app.executor.panic()

        assert app.hud.state().held == ()

    def test_sleep_arrives_with_which_mechanisms(self, app, socket_path):
        app.start()
        app.dispatcher.handle("rest my hands")
        app.dispatcher.handle("rest my hands")

        display = Display(socket_path)
        try:
            state = display.state_where(lambda frame: bool(frame.asleep))
        finally:
            display.close()

        assert state.asleep == ("gesture",)
        assert state.wake_with == ("voice",)

    def test_the_arming_arrives_before_anything_has_changed(self, app):
        app.start()
        app.dispatcher.handle("go to sleep")

        state = app.hud.state()
        assert state.asleep == ()
        assert state.confirming == "sleep? again to confirm"

    def test_and_the_mode_pointers_are_left_where_they_were(self, app):
        app.start()
        app.dispatcher.handle("go to sleep")
        app.dispatcher.handle("go to sleep")

        state = app.hud.state()
        assert state.mode == "root mode"
        assert state.asleep == ("voice", "gesture", "pedal")

    def test_a_fault_arrives_with_its_reason(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("model file vanished")
        app.refresh_health()

        assert app.hud.state().fault == "mode 'root mode' (model file vanished)"

    def test_and_goes_away_again(self, app):
        app.start()
        app.streams["root mode"].failure = RuntimeError("boom")
        app.refresh_health()
        app.streams["root mode"].failure = None
        app.refresh_health()

        assert app.hud.state().fault is None

    def test_a_matched_utterance_arrives_with_the_pattern_it_matched(self, app):
        app.start()
        app.streams["root mode"].on_text("buffer save")

        state = app.hud.state()
        assert (state.heard, state.matched) == ("buffer save", "buffer save")

    def test_a_near_miss_arrives_with_the_pattern_that_came_closest(self, app):
        app.start()
        app.streams["root mode"].on_text("buffer safe")

        state = app.hud.state()
        assert (state.heard, state.nearest) == ("buffer safe", "buffer save")
        assert state.missed

    def test_the_near_miss_is_computed_even_though_nothing_is_logging_debug(self, app, caplog):
        import logging

        caplog.set_level(logging.INFO)
        app.start()
        app.streams["root mode"].on_text("buffer safe")

        assert app.hud.state().nearest == "buffer save"

    def test_a_spoken_mode_name_arrives_as_what_it_matched(self, app):
        app.start()
        app.streams["root mode"].on_text("dictate mode")

        assert app.hud.state().matched == "dictate mode"

    def test_the_panic_phrase_arrives(self, app):
        app.start()
        app.streams["root mode"].on_text("release everything")

        assert app.hud.state().matched == "release everything"

    def test_a_partial_arrives_from_the_recognizer(self, app):
        app.start()
        app.streams["root mode"].on_partial("buffer sa")

        assert app.hud.state().partial == "buffer sa"

    def test_a_partial_does_not_displace_the_last_utterance(self, app):
        app.start()
        app.streams["root mode"].on_text("buffer save")
        app.streams["root mode"].on_partial("buffer cl")

        state = app.hud.state()
        assert (state.partial, state.heard) == ("buffer cl", "buffer save")


class TestWhatDoesNotReachTheSocket:
    def test_a_banned_string_is_not_drawn_as_something_the_user_said(self, app):
        app.start()
        app.streams["root mode"].on_text("buffer save")

        app.streams["root mode"].on_text("the")

        assert app.hud.state().heard == "buffer save"

    def test_no_socket_is_created_when_none_was_asked_for(self, backend, tmp_path):
        application = Application(parse_settings(CONFIG, tmp_path), backend=backend)

        assert application.hud is None


class TestTheSocketAsASubsystem:
    def test_it_is_reported_as_working(self, app):
        app.start()

        assert "display socket" in app.refresh_health().working

    def test_a_socket_that_will_not_bind_becomes_a_fault(self, backend, tmp_path):
        (tmp_path / "afile").write_text("not a directory")
        application = Application(
            parse_settings(CONFIG, tmp_path),
            backend=backend,
            stream_factory=lambda mode, settings, on_text, on_partial=None: FakeStream(
                mode, settings, on_text, on_partial
            ),
            hud_socket=tmp_path / "afile" / "s.sock",
        )
        try:
            application.start()

            assert application.refresh_health().reason("display socket") is not None
        finally:
            application.stop()

    def test_the_reload_phrase_is_the_way_back_from_one(self, app, socket_path):
        app.start()
        app.hud.hide()
        assert not app.hud.listening

        app.reload()

        assert app.hud.listening
        assert socket_path.exists()

    def test_a_reload_republishes_the_configuration(self, app):
        app.start()
        before = app.hud._catalogue.revision

        app.reload()

        assert app.hud._catalogue.revision == before + 1

    def test_stopping_removes_the_socket(self, app, socket_path):
        app.start()
        assert socket_path.exists()

        app.stop()

        assert not socket_path.exists()


class TestSayingSomethingToTheDisplay:
    """The half that makes the feature usable: phrases that reach the surfaces."""

    def test_the_configured_phrase_opens_the_sheet(self, app):
        app.start()

        app.streams["root mode"].on_text("overlay open")

        state = app.hud.state()
        assert state.sheet
        assert state.group is None, "it opens at the index"

    def test_and_the_other_one_closes_it(self, app):
        app.start()
        app.streams["root mode"].on_text("overlay open")

        app.streams["root mode"].on_text("overlay close")

        assert not app.hud.state().sheet

    def test_saying_open_twice_leaves_it_open(self, app):
        app.start()

        app.streams["root mode"].on_text("overlay open")
        app.streams["root mode"].on_text("overlay open")

        assert app.hud.state().sheet

    def test_a_generated_phrase_opens_that_group(self, app):
        app.start()

        app.streams["root mode"].on_text("open buffer")

        state = app.hud.state()
        assert state.sheet
        assert state.group == "buffer"

    def test_the_phrase_the_sheet_drew_is_the_phrase_that_works(self, app):
        """The principle the whole feature rests on, asserted rather than assumed."""
        app.start()
        mode = app.hud._catalogue.mode("root mode")

        for group in mode.groups:
            for phrase in group.phrases:
                app.streams["root mode"].on_text(phrase)
                opened = app.hud.state().group
                assert opened is not None
                assert mode.group(opened) is group, f"{phrase!r} did not open {group.name!r}"

    def test_the_chip_phrase_toggles_it(self, app):
        app.start()
        assert app.hud.state().chip

        app.streams["root mode"].on_text("chip toggle")

        assert not app.hud.state().chip

    def test_a_display_phrase_shows_up_as_something_that_fired(self, app):
        app.start()

        app.streams["root mode"].on_text("overlay open")

        assert app.hud.state().matched == "overlay open"

    def test_an_ordinary_command_still_works(self, app, backend):
        app.start()
        app.streams["root mode"].on_text("overlay open")
        backend.clear()

        app.streams["root mode"].on_text("buffer save")

        assert backend.sequence == "ctrl↓ s↓ s↑ ctrl↑"

    def test_a_display_phrase_types_nothing(self, app, backend):
        app.start()

        app.streams["root mode"].on_text("overlay open")

        assert backend.sequence == ""

    def test_the_panic_phrase_still_outranks_everything(self, app):
        app.start()
        app.executor.execute("hold(shift)")

        app.streams["root mode"].on_text("release everything")

        assert app.hud.state().held == ()

    def test_a_group_the_new_mode_also_has_survives_a_spoken_mode_switch(self, app):
        app.start()
        app.streams["root mode"].on_text("open shared")
        assert app.hud.state().group == "::shared"

        app.streams["root mode"].on_text("dictate mode")

        assert app.hud.state().group == "::shared"

    def test_the_verb_drives_the_display_from_an_ordinary_command(self, app):
        app.start()

        app.streams["root mode"].on_text("buffer overview")

        state = app.hud.state()
        assert state.sheet
        assert state.group == "buffer"

    def test_the_verb_composes_with_keystrokes(self, app, backend):
        app.start()

        app.streams["root mode"].on_text("zoom in")

        assert backend.sequence == "ctrl↓ =↓ =↑ ctrl↑"
        assert not app.hud.state().sheet, "hud(close) ran too"

    def test_a_display_phrase_with_no_display_says_so(self, backend, config_dir, caplog):
        """A reserved phrase must never reach the command table."""
        import logging

        streams: dict[str, FakeStream] = {}

        def factory(mode, settings, on_text, on_partial=None):
            streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            return streams[mode.name]

        application = Application(
            parse_settings(CONFIG, config_dir), backend=backend, stream_factory=factory
        )
        caplog.set_level(logging.WARNING)
        try:
            application.start()
            streams["root mode"].on_text("overlay open")
        finally:
            application.stop()

        assert "needs a display" in caplog.text
        assert backend.sequence == ""

    def test_nothing_is_said_to_a_session_with_no_display(self, backend, config_dir, caplog):
        import logging

        streams: dict[str, FakeStream] = {}

        def factory(mode, settings, on_text, on_partial=None):
            streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            return streams[mode.name]

        application = Application(
            parse_settings(CONFIG, config_dir), backend=backend, stream_factory=factory
        )
        caplog.set_level(logging.WARNING)
        try:
            application.start()
            streams["root mode"].on_text("buffer overview")
        finally:
            application.stop()

        assert "hud(group:buffer)" in caplog.text or "no display" in caplog.text


class TestTwoDisplaysAtOnce:
    def test_a_mode_switch_reaches_the_tray_and_the_socket(self, app):
        app.start()
        tray = FakeIndicator()
        app.modes._indicator = ModeDisplays.of(tray, app.hud)

        app.modes.switch("dictate mode")

        assert tray.current == "dictate mode"
        assert app.hud.state().mode == "dictate mode"

    def test_a_tray_that_raises_does_not_stop_the_socket_being_told(self, app):
        class Angry(FakeIndicator):
            def set_layers(self, layers):
                raise RuntimeError("no")

        app.start()
        app.modes._indicator = ModeDisplays.of(Angry(), app.hud)

        app.modes.switch("dictate mode")

        assert app.hud.state().mode == "dictate mode"

    def test_a_tray_that_raises_does_not_stop_the_mode_switching(self, app):
        class Angry(FakeIndicator):
            def set_held(self, held):
                raise RuntimeError("no")

        app.start()
        app.indicator = Angry()

        app.executor.execute("hold(shift)")

        assert [key.key for key in app.hud.state().held] == ["shift"]

    def test_no_displays_at_all_is_no_indicator_at_all(self, backend, tmp_path):
        application = Application(parse_settings(CONFIG, tmp_path), backend=backend)

        assert application.modes._indicator is None


def test_a_real_display_process_sees_the_whole_picture(app, socket_path):
    """End to end: connect a socket to a running application and read what it draws."""
    app.start()
    app.executor.execute("hold(shift)")
    app.streams["root mode"].on_text("buffer save")

    one = Display(socket_path)
    try:
        catalogue = one.catalogue()
        state = one.state()

        assert catalogue.mode("root mode").group("buffer").phrases == ("open buffer",)
        assert state.mode == "root mode"
        assert [key.symbol for key in state.held] == ["⇧"]
        assert state.matched == "buffer save"
        assert state.revision == catalogue.revision
    finally:
        one.close()


def test_a_display_that_connects_during_a_long_model_load_is_not_left_blank(
    backend, tmp_path, socket_path
):
    """The socket opens before the models."""
    connected: list[Display] = []

    def slow_factory(mode, settings, on_text, on_partial=None):
        if not connected:
            connected.append(Display(socket_path))
        return FakeStream(mode, settings, on_text, on_partial)

    application = Application(
        parse_settings(CONFIG, tmp_path),
        backend=backend,
        stream_factory=slow_factory,
        hud_socket=socket_path,
    )
    try:
        application.start()

        assert connected, "no stream was built"
        catalogue = connected[0].catalogue()
        assert [mode.name for mode in catalogue.modes if mode.runnable] == [
            "root mode",
            "dictate mode",
        ]
    finally:
        for one in connected:
            one.close()
        application.stop()


def test_publishing_survives_a_display_that_is_removed_mid_session(app, socket_path):
    """A display can be started and stopped freely."""
    app.start()
    one = Display(socket_path)
    one.catalogue()
    one.close()

    app.executor.execute("hold(shift)")
    deadline = time.monotonic() + DEADLINE_SECONDS
    while app.hud._clients and time.monotonic() < deadline:
        time.sleep(0.01)

    second = Display(socket_path)
    try:
        second.catalogue()
        assert [key.key for key in second.state().held] == ["shift"]
    finally:
        second.close()

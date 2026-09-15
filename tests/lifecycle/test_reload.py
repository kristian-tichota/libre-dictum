import copy

import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.modes import ModeManager
from libre_dictum.settings import parse_settings
from tests.conftest import FakeIndicator, FakeStream

CONFIG = {
    "starting_mode": "root mode",
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "commands": {"open terminal": "alt + enter"},
        },
        "dictate mode": {"type": "transformer", "model_name": "whisper-turbo"},
    },
}


@pytest.fixture
def session(executor, tmp_path):
    """A running manager whose next reload returns whatever the test hands it."""

    class Session:
        def __init__(self) -> None:
            self.streams: dict[str, FakeStream] = {}
            self.built: list[str] = []
            self.next_config: dict = copy.deepcopy(CONFIG)
            self.load_error: Exception | None = None
            self.indicator = FakeIndicator()
            self.manager = ModeManager(
                parse_settings(copy.deepcopy(CONFIG), tmp_path),
                executor=executor,
                on_text=lambda text: None,
                stream_factory=self._factory,
                indicator=self.indicator,
                settings_loader=self._load,
            )
            self.manager.start()

        def _factory(self, mode, settings, on_text, on_partial=None):
            self.built.append(mode.name)
            self.streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            return self.streams[mode.name]

        def _load(self):
            if self.load_error is not None:
                raise self.load_error
            return parse_settings(copy.deepcopy(self.next_config), tmp_path)

        def reload_with(self, config: dict) -> None:
            self.next_config = config
            self.built.clear()
            self.manager.reload()

    return Session()


class TestRebuilding:
    def test_an_added_mode_gets_a_running_stream(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["new mode"] = {"type": "vosk", "path": "m"}
        session.reload_with(config)

        assert session.manager.settings.modes.keys() == {"root mode", "dictate mode", "new mode"}
        assert session.streams["new mode"].started

    def test_an_edited_command_table_rebuilds_the_recognizer(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["root mode"]["commands"]["save file"] = "ctrl + s"
        session.reload_with(config)

        assert "root mode" in session.built
        assert session.streams["root mode"].enabled

    def test_an_untouched_mode_keeps_its_recognizer(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["root mode"]["commands"]["save file"] = "ctrl + s"
        original = session.streams["dictate mode"]
        session.reload_with(config)

        assert "dictate mode" not in session.built
        assert session.streams["dictate mode"] is original
        assert not original.stopped

    def test_an_edited_alias_does_not_rebuild_anything(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["root mode"]["aliases"] = {"^x$": "y"}
        session.reload_with(config)

        assert session.built == []

    def test_a_renamed_mode_rebuilds_the_grammars(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["renamed dictation"] = config["modes"].pop("dictate mode")
        session.reload_with(config)

        assert "root mode" in session.built

    def test_a_removed_mode_is_stopped(self, session):
        config = copy.deepcopy(CONFIG)
        del config["modes"]["dictate mode"]
        removed = session.streams["dictate mode"]
        session.reload_with(config)

        assert removed.stopped
        assert "dictate mode" not in session.manager.settings.modes


class TestActiveMode:
    def test_the_active_mode_survives_a_reload(self, session):
        session.manager.switch("dictate mode")
        session.reload_with(copy.deepcopy(CONFIG))

        assert session.manager.active == "dictate mode"
        assert session.streams["dictate mode"].enabled

    def test_losing_the_active_mode_falls_back_to_the_starting_mode(self, session):
        session.manager.switch("dictate mode")
        config = copy.deepcopy(CONFIG)
        del config["modes"]["dictate mode"]
        session.reload_with(config)

        assert session.manager.active == "root mode"
        assert session.streams["root mode"].enabled

    def test_a_stale_previous_mode_is_forgotten(self, session):
        session.manager.switch("dictate mode")
        session.manager.switch("root mode")
        config = copy.deepcopy(CONFIG)
        del config["modes"]["dictate mode"]
        session.reload_with(config)

        assert session.manager.previous is None

    def test_the_tray_learns_new_modes(self, session):
        config = copy.deepcopy(CONFIG)
        config["modes"]["new mode"] = {"type": "vosk", "path": "m", "icon": [1, 2, 3]}
        session.reload_with(config)

        assert session.indicator.colours["new mode"] == (1, 2, 3)


class TestAtomicity:
    @pytest.mark.parametrize(
        "broken",
        [
            {"modes": {"root mode": {"type": "vosk"}}},
            {"starting_mode": "typo", "modes": {"root mode": {"type": "vosk", "path": "m"}}},
            {"modes": {}},
        ],
    )
    def test_an_invalid_config_is_rejected_whole(self, session, broken):
        before = session.manager.settings
        session.reload_with(broken)

        assert session.manager.settings is before
        assert session.manager.active == "root mode"
        assert session.streams["root mode"].enabled

    def test_the_error_is_reported(self, session, caplog):
        session.reload_with({"modes": {"root mode": {"type": "vosk"}}})
        assert "Keeping the current configuration" in caplog.text

    def test_an_unreadable_file_keeps_the_session_running(self, session, caplog):
        session.load_error = ConfigError("config.json is not valid JSON")
        session.manager.reload()

        assert session.manager.active == "root mode"
        assert "not valid JSON" in caplog.text


class TestOutcome:
    def test_an_applied_reload_says_so(self, session):
        assert session.manager.reload() is True

    def test_a_rejected_reload_says_so(self, session):
        session.load_error = ConfigError("mode 'broken' has no model path")
        assert session.manager.reload() is False


class TestRecovery:
    def test_a_mode_that_failed_to_start_is_retried(self, executor, tmp_path):
        broken = {"root mode"}

        def factory(mode, settings, on_text, on_partial=None):
            if mode.name in broken:
                raise RuntimeError("model file vanished")
            return FakeStream(mode, settings, on_text, on_partial)

        manager = ModeManager(
            parse_settings(copy.deepcopy(CONFIG), tmp_path),
            executor=executor,
            on_text=lambda text: None,
            stream_factory=factory,
            settings_loader=lambda: parse_settings(copy.deepcopy(CONFIG), tmp_path),
        )
        manager.start()
        assert "root mode" in manager.failed_modes()

        broken.clear()
        manager.reload()

        assert manager.failed_modes() == {}
        assert manager.active in {"root mode", "dictate mode"}

    def test_held_keys_are_released_before_a_reload_applies(self, session, backend, executor):
        executor.execute("hold(ctrl)")
        backend.clear()

        session.manager.reload()

        assert backend.sequence == "ctrl↑"
        assert executor.state.keys_held == []

    def test_a_saved_value_does_not_survive_a_reload(self, session, executor):
        executor.execute("save(5)")

        session.manager.reload()

        assert executor.state.saved_value is None

from libre_dictum.modes import ModeManager
from tests.conftest import FakeIndicator, FakeStream

CONFIG = {
    "starting_mode": "root mode",
    "previous_mode_keyword": "go back",
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [0, 255, 0],
            "exit_command": "release(shift)",
        },
        "dictate mode": {
            "type": "transformer",
            "model_name": "whisper-turbo",
            "icon": [255, 0, 0],
            "enter_command": "hold(shift)",
        },
        "third mode": {"type": "vosk", "path": "m"},
    },
}


class TestStartup:
    def test_every_mode_gets_a_running_stream(self, running_modes):
        modes = running_modes(CONFIG)
        assert all(stream.started for stream in modes.streams.values())

    def test_only_the_starting_mode_is_enabled(self, running_modes):
        modes = running_modes(CONFIG)
        assert modes.active == "root mode"
        assert [name for name, s in modes.streams.items() if s.enabled] == ["root mode"]

    def test_the_tray_learns_every_mode_colour(self, running_modes):
        indicator = FakeIndicator()
        running_modes(CONFIG, indicator)

        assert indicator.colours == {
            "root mode": (0, 255, 0),
            "dictate mode": (255, 0, 0),
            "third mode": None,
        }
        assert indicator.current == "root mode"


class TestSwitching:
    def test_switching_moves_which_stream_is_enabled(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")

        assert modes.active == "dictate mode"
        assert modes.streams["dictate mode"].enabled
        assert not modes.streams["root mode"].enabled

    def test_the_exit_and_enter_commands_both_run(self, running_modes, executor, backend):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")

        assert backend.sequence == "shift↑ shift↓"
        assert executor.state.keys_held == ["shift"]

    def test_the_previous_mode_is_remembered(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")

        assert modes.previous == "root mode"

    def test_the_keyword_goes_back_to_the_previous_mode(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")
        modes.switch("go back")

        assert modes.active == "root mode"
        assert modes.previous == "dictate mode"

    def test_going_back_twice_alternates(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")
        modes.switch("go back")
        modes.switch("go back")

        assert modes.active == "dictate mode"

    def test_the_tray_follows(self, running_modes):
        indicator = FakeIndicator()
        modes = running_modes(CONFIG, indicator)
        modes.switch("dictate mode")

        assert indicator.current == "dictate mode"

    def test_switching_to_an_unknown_mode_changes_nothing(self, running_modes, caplog):
        modes = running_modes(CONFIG)
        modes.switch("nonexistent mode")

        assert modes.active == "root mode"
        assert "unknown mode" in caplog.text

    def test_going_back_before_any_switch_changes_nothing(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("go back")

        assert modes.active == "root mode"


class TestShutdown:
    def test_stopping_stops_every_stream(self, running_modes):
        modes = running_modes(CONFIG)
        streams = dict(modes.streams)
        modes.stop()

        assert all(stream.stopped for stream in streams.values())
        assert modes.active is None

    def test_stopping_twice_is_harmless(self, running_modes):
        modes = running_modes(CONFIG)
        modes.stop()
        modes.stop()


class TestDiagnostics:
    def test_a_dead_recognizer_is_reported(self, running_modes):
        modes = running_modes(CONFIG)
        failure = RuntimeError("model file vanished")
        modes.streams["root mode"].failure = failure

        assert modes.failed_modes() == {"root mode": failure}

    def test_healthy_modes_report_nothing(self, running_modes):
        assert running_modes(CONFIG).failed_modes() == {}


class TestResolvingAName:
    """Dispatch and mode() have to agree on what counts as a mode name."""

    def test_a_mode_name_is_matched_case_insensitively(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("Dictate Mode")

        assert modes.active == "dictate mode"

    def test_a_response_can_switch_by_spoken_name(self, running_modes, executor):
        modes = running_modes(CONFIG)
        executor._mode_switcher = modes.switch

        executor.execute("mode(Dictate Mode)")

        assert modes.active == "dictate mode"


class TestFailureIsolation:
    """One mode's problem must never be the session's problem."""

    def build(self, executor, settings_of, broken: str):
        streams: dict[str, FakeStream] = {}

        def factory(mode, settings, on_text, on_partial=None):
            if mode.name == broken:
                raise RuntimeError("model file vanished")
            streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            return streams[mode.name]

        manager = ModeManager(
            settings_of(CONFIG),
            executor=executor,
            on_text=lambda text: None,
            stream_factory=factory,
        )
        manager.streams = streams  # type: ignore[attr-defined]
        manager.start()
        return manager

    def test_a_mode_that_cannot_start_leaves_the_others_running(self, executor, settings_of):
        modes = self.build(executor, settings_of, broken="dictate mode")

        assert set(modes.streams) == {"root mode", "third mode"}
        assert modes.active == "root mode"

    def test_the_failure_names_the_mode(self, executor, settings_of, caplog):
        self.build(executor, settings_of, broken="dictate mode")

        assert "'dictate mode'" in caplog.text
        assert "model file vanished" in caplog.text

    def test_a_broken_starting_mode_falls_back_to_a_working_one(self, executor, settings_of):
        modes = self.build(executor, settings_of, broken="root mode")

        assert modes.active in {"dictate mode", "third mode"}

    def test_switching_into_a_mode_that_never_started_is_refused(
        self, executor, settings_of, caplog
    ):
        modes = self.build(executor, settings_of, broken="dictate mode")

        modes.switch("dictate mode")

        assert modes.active == "root mode"
        assert "unavailable" in caplog.text

    def test_switching_into_a_mode_whose_recognizer_died_is_refused(self, running_modes, caplog):
        modes = running_modes(CONFIG)
        modes.streams["dictate mode"].failure = RuntimeError("recognizer died")

        modes.switch("dictate mode")

        assert modes.active == "root mode"
        assert "unavailable" in caplog.text

    def test_a_refused_switch_does_not_become_the_previous_mode(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")
        modes.streams["third mode"].failure = RuntimeError("recognizer died")

        modes.switch("third mode")
        modes.switch("go back")

        assert modes.active == "root mode"

    def test_a_failed_mode_is_reported_whether_it_started_or_died(self, executor, settings_of):
        modes = self.build(executor, settings_of, broken="dictate mode")
        modes.streams["third mode"].failure = RuntimeError("recognizer died")

        assert set(modes.failed_modes()) == {"dictate mode", "third mode"}


class TestRedundantSwitch:
    def test_switching_to_the_active_mode_emits_nothing(self, running_modes, backend):
        modes = running_modes(CONFIG)
        modes.switch("dictate mode")
        backend.clear()

        modes.switch("dictate mode")

        assert backend.events == []
        assert modes.active == "dictate mode"
        assert modes.previous == "root mode"

import logging

import pytest

from libre_dictum.dispatch import CommandDispatcher

CONFIG = {
    "reload_command": "reload config",
    "previous_mode_keyword": "go back",
    "starting_mode": "root mode",
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "banned_strings": ["thank you"],
            "aliases": {r"^write\((.*)\)$": r"hold(shift) + \1 + release(shift)"},
            "commands": {
                "open terminal": "alt + enter",
                "press {any}": "{1}",
                "{numeric} times press {any}": "{1}({2})",
                "make it loud": "write(a)",
            },
        },
        "dictate mode": {"type": "transformer", "model_name": "whisper-turbo"},
    },
}


@pytest.fixture
def dispatcher(running_modes, executor):
    return CommandDispatcher(running_modes(CONFIG), executor)


class TestCommandTable:
    def test_a_matching_command_is_executed(self, dispatcher, backend):
        dispatcher.handle("open terminal")
        assert backend.sequence == "alt↓ enter↓ enter↑ alt↑"

    def test_captures_reach_the_response(self, dispatcher, backend):
        dispatcher.handle("press enter")
        assert backend.sequence == "enter↓ enter↑"

    def test_number_words_are_digits_by_the_time_the_response_sees_them(self, dispatcher, backend):
        dispatcher.handle("two times press a")
        assert backend.sequence == "a↓ a↑ a↓ a↑"

    def test_an_unrecognized_utterance_does_nothing(self, dispatcher, backend):
        dispatcher.handle("something else entirely")
        assert backend.events == []

    def test_punctuation_and_case_do_not_prevent_a_match(self, dispatcher, backend):
        dispatcher.handle("Open Terminal.")
        assert backend.sequence == "alt↓ enter↓ enter↑ alt↑"

    def test_aliases_are_applied_to_the_response(self, dispatcher, backend):
        dispatcher.handle("make it loud")
        assert backend.sequence == "shift↓ a↓ a↑ shift↑"


class TestDispatchOrder:
    def test_a_bare_mode_name_switches_mode(self, dispatcher, backend):
        dispatcher.handle("dictate mode")
        assert dispatcher._modes.active == "dictate mode"
        assert backend.events == []

    def test_the_previous_mode_keyword_goes_back(self, dispatcher):
        dispatcher.handle("dictate mode")
        dispatcher.handle("go back")
        assert dispatcher._modes.active == "root mode"

    def test_a_banned_string_is_dropped(self, dispatcher, backend):
        dispatcher.handle("thank you")
        assert backend.events == []

    def test_a_banned_string_is_matched_after_normalization(self, dispatcher, backend, caplog):
        dispatcher.handle("Thank you!")
        assert backend.events == []

    def test_the_reload_command_reloads(self, dispatcher, caplog):
        dispatcher.handle("reload config")
        assert "Reload is not available" in caplog.text


class TestRobustness:
    def test_a_failing_command_does_not_propagate(self, dispatcher, monkeypatch, backend):
        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(dispatcher._executor, "execute", explode)
        dispatcher.handle("open terminal")

    def test_the_failure_is_reported(self, dispatcher, monkeypatch, caplog):
        monkeypatch.setattr(
            dispatcher._modes, "switch", lambda name: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        dispatcher.handle("dictate mode")
        assert "Failed to handle" in caplog.text


class TestPanic:
    """The panic phrase is the hands-free escape from a stuck modifier."""

    def test_it_releases_every_held_key(self, dispatcher, executor, backend):
        executor.execute("hold(ctrl)")
        backend.clear()

        dispatcher.handle("release everything")

        assert backend.sequence == "ctrl↑"
        assert executor.state.keys_held == []

    def test_it_outranks_a_command_of_the_same_name(self, running_modes, executor, backend):
        modes = running_modes({**CONFIG, "panic_command": "let go"})
        dispatcher = CommandDispatcher(modes, executor)
        executor.execute("hold(shift)")
        backend.clear()

        dispatcher.handle("Let go!")

        assert backend.sequence == "shift↑"

    def test_it_works_from_a_dictation_mode(self, dispatcher, executor, backend):
        dispatcher.handle("dictate mode")
        executor.execute("hold(ctrl)")
        backend.clear()

        dispatcher.handle("release everything")

        assert backend.sequence == "ctrl↑"

    def test_it_can_be_switched_off(self, running_modes, executor, backend):
        modes = running_modes({**CONFIG, "panic_command": None})
        executor.execute("hold(ctrl)")
        backend.clear()

        CommandDispatcher(modes, executor).handle("release everything")

        assert backend.events == []
        assert executor.state.keys_held == ["ctrl"]


class TestModeNames:
    def test_a_mode_name_is_matched_case_insensitively(self, dispatcher):
        dispatcher.handle("Dictate Mode.")
        assert dispatcher._modes.active == "dictate mode"

    def test_a_phrase_containing_a_mode_name_is_not_a_switch(self, dispatcher):
        dispatcher.handle("the dictate mode is fine")
        assert dispatcher._modes.active == "root mode"


class TestTracing:
    """At debug level, an utterance's whole journey is in the log."""

    def test_the_steps_of_a_match_are_logged(self, dispatcher, caplog):
        caplog.set_level(logging.DEBUG)
        dispatcher.handle("Two times press a.")

        assert "Heard: 2 times press a" in caplog.text
        assert "'{numeric} times press {any}'" in caplog.text
        assert "'2(a)'" in caplog.text
        assert "a↓" in caplog.text or "KeyToken" in caplog.text

    def test_a_saved_value_is_visible_in_the_expansion(self, dispatcher, executor, caplog):
        caplog.set_level(logging.DEBUG)
        executor.execute("save(5)")
        executor.execute("{1=1}(down)")

        assert "Saved '5'" in caplog.text
        assert "'5(down)'" in caplog.text

    def test_a_near_miss_names_the_closest_pattern(self, dispatcher, caplog):
        caplog.set_level(logging.DEBUG)
        dispatcher.handle("press two words")

        assert "the closest pattern is 'press {any}'" in caplog.text

    def test_an_utterance_like_nothing_at_all_still_says_so(self, dispatcher, caplog):
        caplog.set_level(logging.DEBUG)
        dispatcher.handle("xylophone")

        assert "Nothing in mode 'root mode' matches" in caplog.text

    def test_partial_results_are_quiet_at_info(self, dispatcher, caplog):
        caplog.set_level(logging.INFO)
        dispatcher.handle("something else entirely")

        assert "Nothing in mode" not in caplog.text

    def test_a_repeated_bad_command_warns_every_time(self, executor, caplog):
        caplog.set_level(logging.WARNING)

        for _ in range(3):
            executor.execute("hold(bogus)")

        assert caplog.text.count("Ignoring response") == 3

import json

from libre_dictum.audio.grammar import UNKNOWN_TOKEN, build_grammar, build_vocabulary
from libre_dictum.pattern import CommandPattern
from libre_dictum.settings import Command
from libre_dictum.streams import always_recognized


def commands(*templates):
    return [Command(pattern=CommandPattern.compile(t), response="a") for t in templates]


class TestVocabulary:
    def test_a_literal_command_is_one_phrase(self):
        assert build_vocabulary(commands("open terminal")) == ["open terminal"]

    def test_numeric_becomes_one_phrase_per_digit_word(self):
        vocabulary = build_vocabulary(commands("{numeric} step up"))

        assert len(vocabulary) == 10
        assert "seven step up" in vocabulary

    def test_any_stays_a_placeholder(self):
        assert build_vocabulary(commands("press {any}")) == ["press {any}"]

    def test_a_pattern_is_normalized_and_lowercased_for_the_lexicon(self):
        assert build_vocabulary(commands("Open Terminal!")) == ["open terminal"]

    def test_always_recognized_phrases_come_last(self):
        vocabulary = build_vocabulary(commands("open terminal"), ["dictate mode", "go back"])
        assert vocabulary == ["open terminal", "dictate mode", "go back"]


class TestAlwaysRecognized:
    """What every mode's grammar carries besides its own commands."""

    def test_mode_names_and_reserved_phrases_are_included(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "Root Mode": {"type": "vosk", "path": "m"},
                    "dictate": {"type": "transformer", "model_name": "whisper-turbo"},
                }
            }
        )

        assert always_recognized(settings) == [
            "root mode",
            "dictate",
            "release everything",
            "reload config",
            "previous mode",
            "chip toggle",
            "overlay open",
            "overlay close",
            "overlay modes",
            "overlay pedals",
            "overlay gestures",
            "overlay meters",
        ]

    def test_a_disabled_reserved_phrase_is_left_out(self, settings_of):
        settings = settings_of(
            {
                "panic_command": None,
                "previous_mode_keyword": None,
                "modes": {"root": {"type": "vosk", "path": "m"}},
            }
        )

        assert always_recognized(settings) == [
            "root",
            "reload config",
            "chip toggle",
            "overlay open",
            "overlay close",
            "overlay modes",
            "overlay pedals",
            "overlay gestures",
            "overlay meters",
        ]

    def test_the_reserved_phrases_reach_the_vocabulary(self, settings_of):
        settings = settings_of({"modes": {"root": {"type": "vosk", "path": "m"}}})
        mode = settings.modes["root"]

        vocabulary = build_vocabulary(mode.commands, always_recognized(settings))

        assert "reload config" in vocabulary
        assert "release everything" in vocabulary


class TestGrammar:
    def test_the_grammar_is_a_json_list(self):
        assert json.loads(build_grammar(["open terminal"])) == ["open terminal", UNKNOWN_TOKEN]

    def test_the_unknown_token_is_always_offered(self):
        assert UNKNOWN_TOKEN in json.loads(build_grammar([]))

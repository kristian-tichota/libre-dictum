import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.settings import resolve_imports


def commands_of(mode):
    return {command.pattern.template: command.response for command in mode.commands}


class TestTemplates:
    def test_a_template_is_not_a_runnable_mode(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::shared": {"commands": {"open terminal": "alt + enter"}},
                    "root": {"type": "vosk", "path": "m", "imports": ["::shared"]},
                }
            }
        )
        assert "::shared" not in settings.modes
        assert settings.templates == ("::shared",)

    def test_an_imported_command_table_is_merged(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::shared": {"commands": {"open terminal": "alt + enter"}},
                    "root": {
                        "type": "vosk",
                        "path": "m",
                        "imports": ["::shared"],
                        "commands": {"save file": "ctrl + s"},
                    },
                }
            }
        )
        assert commands_of(settings.modes["root"]) == {
            "save file": "ctrl + s",
            "open terminal": "alt + enter",
        }

    def test_the_importing_mode_wins(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::shared": {"commands": {"save file": "ctrl + shift + s"}},
                    "root": {
                        "type": "vosk",
                        "path": "m",
                        "imports": ["::shared"],
                        "commands": {"save file": "ctrl + s"},
                    },
                }
            }
        )
        assert commands_of(settings.modes["root"])["save file"] == "ctrl + s"

    def test_lists_are_extended(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::shared": {"banned_strings": ["thank you"]},
                    "root": {
                        "type": "vosk",
                        "path": "m",
                        "imports": ["::shared"],
                        "banned_strings": ["you know"],
                    },
                }
            }
        )
        assert settings.modes["root"].banned_strings == frozenset({"thank you", "you know"})

    def test_an_icon_is_seeded_but_never_extended(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::shared": {"icon": [1, 2, 3]},
                    "seeded": {"type": "vosk", "path": "m", "imports": ["::shared"]},
                    "own": {
                        "type": "vosk",
                        "path": "m",
                        "imports": ["::shared"],
                        "icon": [9, 9, 9],
                    },
                }
            }
        )
        assert settings.modes["seeded"].icon == (1, 2, 3)
        assert settings.modes["own"].icon == (9, 9, 9)


class TestTypedBlocks:
    CONFIG = {
        "modes": {
            "::models": {
                "vosk": {"path": "models/small-en"},
                "transformer": {"model_name": "whisper-turbo", "lang": "cs"},
            },
            "commands": {"type": "vosk", "imports": ["::models"]},
            "dictation": {"type": "transformer", "imports": ["::models"]},
        }
    }

    def test_a_mode_picks_up_only_its_own_block(self, settings_of):
        settings = settings_of(self.CONFIG)

        assert settings.modes["commands"].vosk.model_path == "models/small-en"
        assert settings.modes["commands"].transformer is None

    def test_the_matching_block_is_hoisted_to_the_mode(self, settings_of):
        transformer = settings_of(self.CONFIG).modes["dictation"].transformer

        assert transformer.model_name == "whisper-turbo"
        assert transformer.lang == "cs"


class TestImportOrder:
    def test_a_template_may_import_a_template_declared_later(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::first": {"imports": ["::second"]},
                    "::second": {"commands": {"open terminal": "alt + enter"}},
                    "root": {"type": "vosk", "path": "m", "imports": ["::first"]},
                }
            }
        )
        assert "open terminal" in commands_of(settings.modes["root"])

    def test_earlier_imports_win_over_later_ones(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::a": {"commands": {"x": "a"}},
                    "::b": {"commands": {"x": "b"}},
                    "root": {"type": "vosk", "path": "m", "imports": ["::a", "::b"]},
                }
            }
        )
        assert commands_of(settings.modes["root"])["x"] == "a"


class TestProvenance:
    """Which template declared a command survives the merge that flattens it."""

    CONFIG = {
        "modes": {
            "::navigation": {
                "commands": {"step up": "up"},
                "gestures": {"blink": "esc"},
            },
            "::everything": {"imports": ["::navigation"]},
            "root": {
                "type": "vosk",
                "path": "m",
                "imports": ["::everything"],
                "commands": {"open terminal": "alt + enter"},
            },
        }
    }

    def origin_of(self, mode, template):
        return next(c.origin for c in mode.commands if c.pattern.template == template)

    def test_a_command_is_credited_to_the_template_that_declared_it(self, settings_of):
        mode = settings_of(self.CONFIG).modes["root"]

        assert self.origin_of(mode, "step up").group == "::navigation"

    def test_a_mode_s_own_command_is_credited_to_the_mode(self, settings_of):
        mode = settings_of(self.CONFIG).modes["root"]

        assert self.origin_of(mode, "open terminal").group == "root"

    def test_a_gesture_action_is_credited_too(self, settings_of):
        mode = settings_of(self.CONFIG).modes["root"]

        assert mode.gesture_origins["blink"].group == "::navigation"

    def test_the_winner_of_a_collision_is_the_one_credited(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::a": {"commands": {"x": "a"}},
                    "::b": {"commands": {"x": "b"}},
                    "root": {"type": "vosk", "path": "m", "imports": ["::a", "::b"]},
                }
            }
        )

        assert self.origin_of(settings.modes["root"], "x").group == "::a"

    def test_a_command_hoisted_out_of_a_typed_block_is_credited(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "::models": {"vosk": {"path": "m", "commands": {"step up": "up"}}},
                    "root": {"type": "vosk", "imports": ["::models"]},
                }
            }
        )

        assert self.origin_of(settings.modes["root"], "step up").group == "::models"

    def test_a_single_file_configuration_credits_config_json(self, settings_of):
        mode = settings_of(self.CONFIG).modes["root"]

        assert self.origin_of(mode, "step up").source == "config.json"
        assert mode.source == "config.json"


class TestImportErrors:
    def test_an_unknown_import_names_both_modes(self, settings_of):
        with pytest.raises(ConfigError, match="imports '::missing'"):
            settings_of(
                {"modes": {"root": {"type": "vosk", "path": "m", "imports": ["::missing"]}}}
            )

    def test_a_cycle_is_reported_rather_than_silently_truncated(self):
        with pytest.raises(ConfigError, match="cycle"):
            resolve_imports({"::a": {"imports": ["::b"]}, "::b": {"imports": ["::a"]}})

    def test_a_self_import_is_a_cycle(self):
        with pytest.raises(ConfigError, match="cycle"):
            resolve_imports({"::a": {"imports": ["::a"]}})

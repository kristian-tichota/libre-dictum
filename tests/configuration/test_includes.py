import json
from pathlib import Path

import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.modes import ModeManager
from libre_dictum.settings import load_settings
from tests.conftest import FakeStream

from .test_loading import create_models

EXAMPLE_DIR = Path(__file__).parents[2] / "examples" / "configs" / "split"

MODEL = "models/small-en"


def write(directory: Path, files: dict[str, object]) -> Path:
    """Write a whole configuration directory, and whatever models it points at."""
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        if isinstance(data, dict):
            create_models(directory, data)
    return directory


def commands_of(mode):
    return {command.pattern.template: command.response for command in mode.commands}


def origin_of(mode, template):
    return next(c.origin for c in mode.commands if c.pattern.template == template)


class TestIncluding:
    def test_an_included_file_contributes_its_modes(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["modes/*.json"], "starting_mode": "root mode"},
                    "modes/root.json": {
                        "modes": {"root mode": {"type": "vosk", "path": MODEL}},
                    },
                },
            )
        )

        assert set(settings.modes) == {"root mode"}
        assert settings.starting_mode == "root mode"

    def test_a_template_can_be_imported_across_files(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["groups/*.json", "modes/*.json"]},
                    "groups/navigation.json": {
                        "modes": {"::navigation": {"commands": {"step up": "up"}}}
                    },
                    "modes/root.json": {
                        "modes": {
                            "root mode": {
                                "type": "vosk",
                                "path": MODEL,
                                "imports": ["::navigation"],
                            }
                        }
                    },
                },
            )
        )

        assert commands_of(settings.modes["root mode"]) == {"step up": "up"}

    def test_a_glob_is_expanded_in_sorted_order(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["modes/*.json"]},
                    "modes/z-last.json": {"modes": {"zulu": {"type": "vosk", "path": MODEL}}},
                    "modes/a-first.json": {"modes": {"alpha": {"type": "vosk", "path": MODEL}}},
                },
            )
        )

        assert list(settings.modes) == ["alpha", "zulu"]
        assert settings.starting_mode == "alpha"

    def test_entries_are_loaded_in_the_order_they_are_listed(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["second/*.json", "first/*.json"]},
                    "first/a.json": {"modes": {"alpha": {"type": "vosk", "path": MODEL}}},
                    "second/z.json": {"modes": {"zulu": {"type": "vosk", "path": MODEL}}},
                },
            )
        )

        assert list(settings.modes) == ["zulu", "alpha"]

    def test_the_root_file_is_not_loaded_twice_by_a_glob(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {
                        "include": ["*.json"],
                        "modes": {"root mode": {"type": "vosk", "path": MODEL}},
                    },
                    "extra.json": {"modes": {"other mode": {"type": "vosk", "path": MODEL}}},
                },
            )
        )

        assert set(settings.modes) == {"root mode", "other mode"}

    def test_a_file_matched_by_two_entries_is_loaded_once(self, tmp_path):
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["modes/*.json", "modes/root.json"]},
                    "modes/root.json": {"modes": {"root mode": {"type": "vosk", "path": MODEL}}},
                },
            )
        )

        assert set(settings.modes) == {"root mode"}

    def test_a_directory_matched_by_a_glob_is_skipped(self, tmp_path):
        (tmp_path / "parts" / "nested.json").parent.mkdir(parents=True)
        settings = load_settings(
            write(
                tmp_path,
                {
                    "config.json": {"include": ["parts/*"]},
                    "parts/root.json": {"modes": {"root mode": {"type": "vosk", "path": MODEL}}},
                    "parts/nested.json/keep.txt": "not json",
                },
            )
        )

        assert set(settings.modes) == {"root mode"}


class TestIncludeErrors:
    def test_a_mode_defined_twice_names_both_files(self, tmp_path):
        with pytest.raises(ConfigError, match="'root mode'.*modes/a.json.*modes/b.json"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["modes/*.json"]},
                        "modes/a.json": {"modes": {"root mode": {"type": "vosk", "path": MODEL}}},
                        "modes/b.json": {"modes": {"root mode": {"type": "vosk", "path": MODEL}}},
                    },
                )
            )

    def test_a_setting_given_twice_names_both_files(self, tmp_path):
        with pytest.raises(ConfigError, match="'starting_mode'.*config.json.*modes/root.json"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["modes/*.json"], "starting_mode": "root mode"},
                        "modes/root.json": {
                            "starting_mode": "root mode",
                            "modes": {"root mode": {"type": "vosk", "path": MODEL}},
                        },
                    },
                )
            )

    def test_an_entry_that_matches_nothing_names_the_entry(self, tmp_path):
        with pytest.raises(ConfigError, match="'mods/\\*.json' of config.json matches no file"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {
                            "include": ["mods/*.json"],
                            "modes": {"root mode": {"type": "vosk", "path": MODEL}},
                        }
                    },
                )
            )

    def test_only_the_root_file_may_include(self, tmp_path):
        with pytest.raises(ConfigError, match="modes/root.json has an 'include' of its own"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["modes/*.json"]},
                        "modes/root.json": {
                            "include": ["more/*.json"],
                            "modes": {"root mode": {"type": "vosk", "path": MODEL}},
                        },
                    },
                )
            )

    def test_an_included_file_must_hold_a_json_object(self, tmp_path):
        with pytest.raises(ConfigError, match="modes/root.json must be a JSON object, got list"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["modes/*.json"]},
                        "modes/root.json": [{"modes": {}}],
                    },
                )
            )

    def test_malformed_json_in_an_included_file_names_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match="modes/root.json is not valid JSON"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["modes/*.json"]},
                        "modes/root.json": '{"modes": }',
                    },
                )
            )

    def test_include_must_be_a_list_of_paths(self, tmp_path):
        with pytest.raises(ConfigError, match="'include' in config.json must be a list"):
            load_settings(write(tmp_path, {"config.json": {"include": "modes/*.json"}}))

    def test_an_unknown_import_is_still_an_error_once_files_are_split(self, tmp_path):
        with pytest.raises(ConfigError, match="imports '::typo'"):
            load_settings(
                write(
                    tmp_path,
                    {
                        "config.json": {"include": ["groups/*.json", "modes/*.json"]},
                        "groups/navigation.json": {
                            "modes": {"::navigation": {"commands": {"step up": "up"}}}
                        },
                        "modes/root.json": {
                            "modes": {
                                "root mode": {
                                    "type": "vosk",
                                    "path": MODEL,
                                    "imports": ["::typo"],
                                }
                            }
                        },
                    },
                )
            )


class TestProvenance:
    """Where a command came from survives having its mode flattened."""

    FILES = {
        "config.json": {"include": ["groups/*.json", "modes/*.json"]},
        "groups/navigation.json": {
            "modes": {
                "::navigation": {
                    "commands": {"step up": "up"},
                    "gestures": {"blink": "esc"},
                }
            }
        },
        "modes/root.json": {
            "modes": {
                "root mode": {
                    "type": "vosk",
                    "path": MODEL,
                    "imports": ["::navigation"],
                    "commands": {"open terminal": "alt + enter"},
                }
            }
        },
    }

    def test_an_imported_command_names_its_template_and_file(self, tmp_path):
        settings = load_settings(write(tmp_path, self.FILES))

        origin = origin_of(settings.modes["root mode"], "step up")
        assert (origin.group, origin.source) == ("::navigation", "groups/navigation.json")

    def test_a_mode_s_own_command_is_credited_to_the_mode(self, tmp_path):
        settings = load_settings(write(tmp_path, self.FILES))

        origin = origin_of(settings.modes["root mode"], "open terminal")
        assert (origin.group, origin.source) == ("root mode", "modes/root.json")

    def test_a_mode_records_the_file_it_was_declared_in(self, tmp_path):
        settings = load_settings(write(tmp_path, self.FILES))

        assert settings.modes["root mode"].source == "modes/root.json"

    def test_a_gesture_action_records_where_it_came_from(self, tmp_path):
        settings = load_settings(write(tmp_path, self.FILES))

        origin = settings.modes["root mode"].gesture_origins["blink"]
        assert (origin.group, origin.source) == ("::navigation", "groups/navigation.json")

    def test_an_error_about_an_imported_command_names_the_file_to_edit(self, tmp_path):
        broken = {
            **self.FILES,
            "groups/navigation.json": {
                "modes": {"::navigation": {"commands": {"step up": "hold(bogus)"}}}
            },
        }

        with pytest.raises(
            ConfigError,
            match="'step up'.*imported from ::navigation in groups/navigation.json.*'root mode'",
        ):
            load_settings(write(tmp_path, broken))


class TestTheShippedExample:
    def test_the_split_example_loads(self, tmp_path):
        for source in sorted(EXAMPLE_DIR.rglob("*.json")):
            target = tmp_path / source.relative_to(EXAMPLE_DIR)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text())
            create_models(tmp_path, json.loads(source.read_text()))

        settings = load_settings(tmp_path)

        assert set(settings.modes) == {"command mode", "dictate mode"}
        assert settings.starting_mode == "command mode"
        assert origin_of(settings.modes["command mode"], "step up").group == "::navigation"


class TestReloadingAFileSet:
    """The atomic-reload promise has to survive being spread over several files."""

    RUNNING = {
        "config.json": {"include": ["groups/*.json"], "starting_mode": "root mode"},
        "groups/navigation.json": {"modes": {"::navigation": {"commands": {"step up": "up"}}}},
        "groups/root.json": {
            "modes": {
                "root mode": {"type": "vosk", "path": MODEL, "imports": ["::navigation"]},
            }
        },
    }

    def manager(self, executor, config_dir):
        built: list[str] = []

        def factory(mode, settings, on_text, on_partial=None):
            built.append(mode.name)
            return FakeStream(mode, settings, on_text, on_partial)

        manager = ModeManager(
            load_settings(config_dir),
            executor=executor,
            on_text=lambda text: None,
            stream_factory=factory,
            settings_loader=lambda: load_settings(config_dir),
        )
        manager.start()
        return manager, built

    def test_a_reload_that_cannot_read_a_file_keeps_the_running_configuration(
        self, executor, tmp_path, caplog
    ):
        manager, _ = self.manager(executor, write(tmp_path, self.RUNNING))
        (tmp_path / "groups").rename(tmp_path / "renamed")

        assert manager.reload() is False
        assert "matches no file" in caplog.text
        assert commands_of(manager.settings.modes["root mode"]) == {"step up": "up"}

    def test_moving_a_command_to_another_file_does_not_rebuild_the_recognizer(
        self, executor, tmp_path
    ):
        config_dir = write(
            tmp_path,
            {
                "config.json": {
                    "include": ["groups/*.json"],
                    "modes": {
                        "root mode": {
                            "type": "vosk",
                            "path": MODEL,
                            "commands": {"step up": "up"},
                        }
                    },
                },
                "groups/keep.json": {"modes": {"::unused": {}}},
            },
        )
        manager, built = self.manager(executor, config_dir)
        assert built == ["root mode"]

        write(
            tmp_path,
            {
                "config.json": {"include": ["groups/*.json"]},
                "groups/keep.json": {
                    "modes": {
                        "root mode": {
                            "type": "vosk",
                            "path": MODEL,
                            "commands": {"step up": "up"},
                        }
                    },
                },
            },
        )

        assert manager.reload() is True
        assert built == ["root mode"], "the recognizer was rebuilt for a file move"
        assert manager.settings.modes["root mode"].source == "groups/keep.json"

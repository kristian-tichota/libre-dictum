import json
from pathlib import Path

import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.settings import ModeKind, load_settings

MINIMAL = {"modes": {"root mode": {"type": "vosk", "path": "models/small-en"}}}

EXAMPLE_CONFIG_DIR = Path(__file__).parents[2] / "examples" / "configs"


def write_config(directory, data):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(json.dumps(data))
    create_models(directory, data)
    return directory


def create_models(directory, data):
    """Create whatever models the config points at."""
    for key in ("ht_model_path", "ht_hand_model_path"):
        if data.get(key):
            path = directory / data[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    for mode in data.get("modes", {}).values():
        for path in (mode.get("path"), mode.get("vosk", {}).get("path")):
            if path:
                (directory / path).mkdir(parents=True, exist_ok=True)


class TestLoading:
    def test_a_minimal_config_loads(self, tmp_path):
        settings = load_settings(write_config(tmp_path, MINIMAL))

        assert settings.starting_mode == "root mode"
        assert settings.modes["root mode"].kind is ModeKind.VOSK

    def test_the_config_directory_is_remembered(self, tmp_path):
        settings = load_settings(write_config(tmp_path, MINIMAL))

        assert settings.config_file == tmp_path / "config.json"
        assert settings.script_dir == tmp_path / "scripts"

    def test_a_missing_file_names_the_path(self, tmp_path):
        with pytest.raises(ConfigError, match="is missing"):
            load_settings(tmp_path / "nowhere")

    def test_malformed_json_reports_where(self, tmp_path):
        tmp_path.joinpath("config.json").write_text('{"modes": }')

        with pytest.raises(ConfigError, match="not valid JSON"):
            load_settings(tmp_path)

    def test_the_shipped_examples_load(self, tmp_path):
        examples = sorted(EXAMPLE_CONFIG_DIR.glob("*.json"))
        assert examples, "no example configs found"
        for example in examples:
            load_settings(write_config(tmp_path / example.stem, json.loads(example.read_text())))


class TestDefaults:
    def test_global_defaults(self, settings_of):
        settings = settings_of(MINIMAL)

        assert settings.reload_command == "reload config"
        assert settings.panic_command == "release everything"
        assert settings.previous_mode_keyword == "previous mode"
        assert settings.enable_systray is False
        assert settings.head_tracking is None

    def test_reserved_phrases_can_be_switched_off(self, settings_of):
        settings = settings_of({**MINIMAL, "panic_command": None, "previous_mode_keyword": None})

        assert settings.panic_command is None
        assert settings.previous_mode_keyword is None
        assert set(settings.reserved_phrases) == {
            "reload config",
            "chip toggle",
            "overlay open",
            "overlay close",
            "overlay modes",
            "overlay pedals",
            "overlay gestures",
            "overlay meters",
        }

    def test_mode_defaults(self, settings_of):
        mode = settings_of(MINIMAL).modes["root mode"]

        assert mode.commands == ()
        assert mode.aliases == {}
        assert mode.banned_strings == frozenset()
        assert mode.icon is None
        assert mode.input_delay == pytest.approx(0.01)
        assert mode.enter_command is None and mode.exit_command is None
        assert mode.transformer is None

    def test_dictation_defaults(self, settings_of):
        settings = settings_of(
            {"modes": {"dictate": {"type": "transformer", "model_name": "whisper-turbo"}}}
        )
        transformer = settings.modes["dictate"].transformer

        assert transformer.silence_seconds == pytest.approx(0.3)
        assert transformer.max_chunk_seconds == pytest.approx(30.0)
        assert transformer.energy_threshold == pytest.approx(0.01)
        assert transformer.pre_roll_seconds == pytest.approx(0.25)
        assert transformer.lang == "en"
        assert transformer.device == "auto"

    def test_pointer_defaults(self, settings_of):
        pointer = settings_of(MINIMAL).modes["root mode"].pointer

        assert pointer.enabled is False
        assert pointer.dead_angle_h == pytest.approx(2.0)
        assert pointer.speed_power == pytest.approx(1.5)
        assert pointer.full_speed_angle == pytest.approx(18.0)
        assert pointer.max_speed_px_per_sec == pytest.approx(800.0)

    def test_the_reload_command_is_normalized(self, settings_of):
        settings = settings_of({**MINIMAL, "reload_command": "Reload Config!"})
        assert settings.reload_command == "reload config"

    def test_the_first_mode_starts_when_none_is_named(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "first": {"type": "vosk", "path": "m"},
                    "second": {"type": "vosk", "path": "m"},
                }
            }
        )
        assert settings.starting_mode == "first"


class TestFixtureConfig:
    """The composed example in tests/fixtures/configs/two-modes.json."""

    @pytest.fixture
    def settings(self, config_fixture, settings_of):
        return settings_of(config_fixture("two-modes.json"))

    def test_both_runnable_modes_survive_the_template(self, settings):
        assert set(settings.modes) == {"root mode", "dictate mode"}
        assert settings.templates == ("::shared",)

    def test_each_mode_keeps_its_own_recognizer_settings(self, settings):
        assert settings.modes["root mode"].vosk.model_path == "models/small-en"
        assert settings.modes["dictate mode"].transformer.silence_seconds == pytest.approx(0.5)

    def test_the_template_reaches_both_modes(self, settings):
        for mode in settings.modes.values():
            assert "thank you" in mode.banned_strings
            assert mode.aliases

    def test_commands_are_compiled_once_at_load(self, settings):
        patterns = [command.pattern for command in settings.modes["root mode"].commands]
        assert {p.template for p in patterns} >= {"press {any}", "open terminal"}
        assert all(p.regex is not None for p in patterns)


class TestModelPaths:
    """A model that is not on disk is a configuration error, not a runtime surprise."""

    def test_a_missing_model_path_names_the_mode(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps(MINIMAL))

        with pytest.raises(ConfigError, match="'root mode'.*models/small-en"):
            load_settings(tmp_path)

    def test_the_error_lists_where_it_looked(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps(MINIMAL))

        with pytest.raises(ConfigError, match="Looked in"):
            load_settings(tmp_path)

    def test_a_path_beside_config_json_is_found(self, tmp_path):
        settings = load_settings(write_config(tmp_path, MINIMAL))

        assert settings.modes["root mode"].vosk.model_path == str(tmp_path / "models/small-en")

    def test_an_absolute_path_is_kept(self, tmp_path):
        model = tmp_path / "elsewhere" / "model"
        model.mkdir(parents=True)
        config = {"modes": {"root mode": {"type": "vosk", "path": str(model)}}}

        settings = load_settings(write_config(tmp_path, config))

        assert settings.modes["root mode"].vosk.model_path == str(model)

    def test_a_missing_head_tracking_model_names_the_setting(self, tmp_path):
        config = {**MINIMAL, "enable_head_tracking": True, "ht_model_path": "gone.task"}
        write_config(tmp_path, {**config, "ht_model_path": ""})
        (tmp_path / "config.json").write_text(json.dumps(config))

        with pytest.raises(ConfigError, match="ht_model_path"):
            load_settings(tmp_path)

    def test_a_dictation_model_name_is_not_a_path(self, tmp_path):
        config = {"modes": {"dictate": {"type": "transformer", "model_name": "openai/whisper"}}}

        load_settings(write_config(tmp_path, config))

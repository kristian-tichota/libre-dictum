from __future__ import annotations

import pytest

from libre_dictum.errors import CommandSyntaxError, ConfigError
from libre_dictum.input.dsl import HUD_TARGETS, check_hud_target, hud_group
from libre_dictum.overlay import model
from libre_dictum.settings import (
    DEFAULT_CHIP_COMMAND,
    DEFAULT_METERS_COMMAND,
    DEFAULT_NAVIGATE,
    DEFAULT_SHEET_CLOSE,
    DEFAULT_SHEET_OPEN,
    GroupDisplay,
)
from libre_dictum.streams import mode_vocabulary

CONFIG = {
    "modes": {
        "::alphabet": {"commands": {"type a": "a", "type b": "b", "press e": "e", "press f": "f"}},
        "root mode": {
            "type": "vosk",
            "path": "m",
            "imports": ["::alphabet"],
            "commands": {
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "zoom in": "ctrl + =",
            },
        },
        "other mode": {"type": "vosk", "path": "m"},
    }
}


MERGED = {
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "commands": {
                "vector alpha": "f13",
                "vector bravo": "f14",
                "vector charlie": "f15",
                "dispatch alpha": "meta + f13",
                "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "ctrl + f13",
                "link bravo": "ctrl + f14",
            },
        }
    }
}


@pytest.fixture
def view(settings_of):
    def build(data=None, mode="root mode"):
        settings = settings_of(data or CONFIG)
        return settings, model.build_view(
            settings.modes[mode],
            modes=list(settings.modes),
            navigate=settings.overlay.navigate,
            overrides=settings.overlay.groups,
        )

    return build


class TestTheDefaultsDeducedFromTheRealConfiguration:
    """The defaults are only defaults because they collide with nothing in a real file."""

    def test_the_navigation_prefix_is_one_word(self):
        assert len(DEFAULT_NAVIGATE.split()) == 1

    @pytest.mark.parametrize(
        "phrase",
        [DEFAULT_SHEET_OPEN, DEFAULT_SHEET_CLOSE, DEFAULT_CHIP_COMMAND, DEFAULT_METERS_COMMAND],
    )
    def test_every_configured_phrase_is_two_words(self, phrase):
        assert len(phrase.split()) == 2

    def test_the_sheet_gets_a_pair_and_not_one_toggle(self):
        assert DEFAULT_SHEET_OPEN != DEFAULT_SHEET_CLOSE

    def test_the_readings_get_one_toggle_because_their_state_is_on_screen(self):
        assert DEFAULT_METERS_COMMAND not in (DEFAULT_SHEET_OPEN, DEFAULT_SHEET_CLOSE)

    def test_they_are_reserved_so_no_command_can_shadow_them(self, settings_of):
        reserved = settings_of(CONFIG).reserved_phrases

        assert reserved[DEFAULT_SHEET_OPEN] == "overlay.sheet.open"
        assert reserved[DEFAULT_SHEET_CLOSE] == "overlay.sheet.close"
        assert reserved[DEFAULT_CHIP_COMMAND] == "overlay.chip.command"
        assert reserved[DEFAULT_METERS_COMMAND] == "overlay.meters.command"

    def test_the_prefix_is_spelled_the_same_in_both_modules(self):
        assert DEFAULT_NAVIGATE == model.DEFAULT_NAVIGATE


class TestWhatTheGrammarGains:
    def test_one_phrase_per_group(self, view):
        settings, found = view()

        assert set(found.phrases) == {
            "open alphabet",
            "open buffer",
            "open misc",
        }

    def test_they_reach_the_recognizer_alongside_the_reserved_phrases(self, view):
        settings, _ = view()

        vocabulary = mode_vocabulary(settings.modes["root mode"], settings)

        assert "open alphabet" in vocabulary
        assert "overlay open" in vocabulary
        assert "release everything" in vocabulary

    def test_a_merged_table_contributes_one_phrase_per_name(self, view):
        settings, found = view(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "focus left": "meta + left",
                            "focus right": "meta + right",
                            "transit left": "meta + alt + left",
                            "transit right": "meta + alt + right",
                        },
                    }
                }
            }
        )

        assert set(found.phrases) == {"open focus", "open transit"}

    def test_a_null_prefix_generates_none(self, view):
        settings, found = view({**CONFIG, "overlay": {"sheet": {"navigate": None}}})

        assert found.phrases == ()
        assert not found.navigable
        assert "open buffer" not in mode_vocabulary(settings.modes["root mode"], settings)

    def test_a_different_prefix_is_used(self, view):
        settings, found = view({**CONFIG, "overlay": {"sheet": {"navigate": "show"}}})

        assert "show buffer" in found.phrases

    def test_a_dictation_mode_generates_none_at_all(self, view):
        settings, found = view(
            {
                "modes": {
                    "root mode": {
                        "type": "transformer",
                        "model_name": "t",
                        "commands": {"{rest}": "type({1})"},
                    }
                }
            }
        )

        assert found.phrases == ()

    def test_editing_the_overlay_block_rebuilds_the_recognizer(self, settings_of):
        from libre_dictum.modes import _stream_signature

        before = settings_of(CONFIG)
        after = settings_of({**CONFIG, "overlay": {"sheet": {"navigate": "show"}}})

        assert _stream_signature(before.modes["root mode"], before) != _stream_signature(
            after.modes["root mode"], after
        )

    def test_moving_a_command_between_files_still_does_not(self, settings_of, tmp_path):
        from libre_dictum.modes import _stream_signature
        from libre_dictum.settings import parse_settings

        plain = parse_settings(CONFIG, tmp_path)
        moved = parse_settings(CONFIG, tmp_path, {"root mode": "modes/root.json"})

        assert _stream_signature(plain.modes["root mode"], plain) == _stream_signature(
            moved.modes["root mode"], moved
        )


class TestPerGroupOverrides:
    def test_say_changes_the_phrase_and_nothing_else(self, view):
        settings, found = view(
            {**CONFIG, "overlay": {"groups": {"::alphabet": {"say": "spelling"}}}}
        )
        group = found.group_called("::alphabet")

        assert group.phrases == ("open spelling",)
        assert group.name == "::alphabet", "the group keeps the name the author wrote"
        assert group.count == 4

    def test_say_rescues_a_name_nobody_can_pronounce(self, view):
        settings, found = view(
            {
                "overlay": {"groups": {"{numeric}": {"say": "count"}}},
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"{numeric} up": "up", "{numeric} down": "down"},
                    }
                },
            }
        )
        group = found.group_called("{numeric}")

        assert group.phrases == ("open count",)
        assert group.navigable

    def test_hide_drops_a_group_from_the_sheet_and_the_grammar(self, view):
        settings, found = view({**CONFIG, "overlay": {"groups": {"::alphabet": {"hide": True}}}})

        assert found.group_called("::alphabet") is None
        assert "open alphabet" not in found.phrases
        assert "open alphabet" not in mode_vocabulary(settings.modes["root mode"], settings)

    def test_hiding_one_name_of_a_merged_table_only_costs_that_way_in(self, view):
        _, found = view({**MERGED, "overlay": {"groups": {"dispatch": {"hide": True}}}})
        group = found.group_called("vector")

        assert group is not None
        assert group.count == 8, "the commands are still in the table"
        assert set(group.phrases) == {"open vector", "open link"}

    def test_hiding_a_group_does_not_regroup_the_others(self, view):
        """Grouping first, hiding second, and this is what that buys."""
        _, found = view({**MERGED, "overlay": {"groups": {"dispatch": {"hide": True}}}})

        assert [group.name for group in found.groups] == ["vector/dispatch/link"]

    def test_hiding_every_name_drops_the_table_entirely(self, view):
        settings, found = view(
            {
                **MERGED,
                "overlay": {
                    "groups": {name: {"hide": True} for name in ("vector", "dispatch", "link")}
                },
            }
        )

        assert found.groups == ()
        assert not any(
            phrase.startswith("open ")
            for phrase in mode_vocabulary(settings.modes["root mode"], settings)
        )

    def test_a_group_nobody_mentions_keeps_its_defaults(self, settings_of):
        overlay = settings_of(CONFIG).overlay

        assert overlay.display("buffer") == GroupDisplay()
        assert overlay.display("buffer").say is None
        assert not overlay.display("buffer").hide


class TestTheHudVerb:
    @pytest.mark.parametrize("target", sorted(HUD_TARGETS))
    def test_every_named_target_is_accepted(self, target):
        check_hud_target(target)

    def test_a_group_target_carries_its_name(self):
        assert hud_group("group:buffer") == "buffer"
        assert hud_group("chip") is None

    def test_a_group_target_with_no_name_is_refused(self):
        with pytest.raises(CommandSyntaxError, match="names no group"):
            check_hud_target("group:")

    def test_an_unknown_target_lists_the_alternatives(self):
        with pytest.raises(CommandSyntaxError, match="Targets:"):
            check_hud_target("wobble")

    def test_a_command_can_use_it(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"buffer overview": "hud(group:buffer)"},
                    }
                }
            }
        )

        assert settings.modes["root mode"].commands[0].response == "hud(group:buffer)"

    def test_a_mistyped_target_is_a_load_error_naming_the_command(self, settings_of):
        with pytest.raises(ConfigError, match="buffer overview"):
            settings_of(
                {
                    "modes": {
                        "root mode": {
                            "type": "vosk",
                            "path": "m",
                            "commands": {"buffer overview": "hud(gruop:buffer)"},
                        }
                    }
                }
            )

    def test_it_composes_with_the_rest_of_a_response(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"buffer save": "ctrl + s + hud(close)"},
                    }
                }
            }
        )

        assert settings.modes["root mode"].commands[0].response.endswith("hud(close)")

    def test_it_can_be_reached_through_an_alias(self, settings_of):
        with pytest.raises(ConfigError, match="cannot run"):
            settings_of(
                {
                    "modes": {
                        "root mode": {
                            "type": "vosk",
                            "path": "m",
                            "aliases": {"LOOK": "hud(nonsense)"},
                            "commands": {"buffer overview": "LOOK"},
                        }
                    }
                }
            )


class TestTheOutputBlock:
    def test_it_defaults_to_no_restriction_at_all(self, settings_of):
        output = settings_of(CONFIG).overlay.output

        assert output.name is None and output.only

    def test_a_named_output_is_read_with_its_rule(self, settings_of):
        overlay = {"output": {"name": "DP-1", "only": False}}

        output = settings_of({**CONFIG, "overlay": overlay}).overlay.output

        assert output.name == "DP-1" and not output.only

    def test_a_name_that_is_not_a_name_is_refused_with_the_flag_that_lists_them(self, settings_of):
        with pytest.raises(ConfigError, match="--outputs"):
            settings_of({**CONFIG, "overlay": {"output": {"name": ""}}})

    def test_and_so_is_one_that_is_not_a_string(self, settings_of):
        with pytest.raises(ConfigError, match="overlay.output.name"):
            settings_of({**CONFIG, "overlay": {"output": {"name": 1}}})

    def test_the_block_itself_has_to_be_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="overlay.output"):
            settings_of({**CONFIG, "overlay": {"output": "DP-1"}})

    def test_sleep_hiding_the_display_is_off_until_it_is_asked_for(self, settings_of):
        assert not settings_of(CONFIG).overlay.sleep_hides

        overlay = {"sleep": {"hide": True}}
        assert settings_of({**CONFIG, "overlay": overlay}).overlay.sleep_hides


class TestWhatTheLoaderRefuses:
    def test_a_generated_phrase_that_a_command_already_uses(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "buffer save": "ctrl + s",
                            "buffer close": "ctrl + w",
                            "open buffer": "ctrl + o",
                        },
                    }
                }
            }
        )

        with pytest.raises(ConfigError, match="open buffer"):
            model.check_display(settings)

    def test_and_says_how_to_fix_it(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "buffer save": "ctrl + s",
                            "buffer close": "ctrl + w",
                            "open buffer": "ctrl + o",
                        },
                    }
                }
            }
        )

        with pytest.raises(ConfigError, match="overlay.groups"):
            model.check_display(settings)

    def test_a_say_override_is_the_way_out_of_a_collision(self, settings_of):
        settings = settings_of(
            {
                "overlay": {"groups": {"buffer": {"say": "buffers"}}},
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "buffer save": "ctrl + s",
                            "buffer close": "ctrl + w",
                            "open buffer": "ctrl + o",
                        },
                    }
                },
            }
        )

        model.check_display(settings)

    def test_a_generated_phrase_that_a_reserved_phrase_already_uses(self, settings_of):
        settings = settings_of(
            {
                "reload_command": "open buffer",
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"buffer save": "ctrl + s", "buffer close": "ctrl + w"},
                    }
                },
            }
        )

        with pytest.raises(ConfigError, match="open buffer"):
            model.check_display(settings)

    def test_a_command_named_the_same_as_a_display_phrase(self, settings_of):
        with pytest.raises(ConfigError, match="overlay.sheet.open"):
            settings_of(
                {
                    "modes": {
                        "root mode": {
                            "type": "vosk",
                            "path": "m",
                            "commands": {"overlay open": "ctrl + o"},
                        }
                    }
                }
            )

    def test_a_two_word_navigation_prefix(self, settings_of):
        with pytest.raises(ConfigError, match="not one word"):
            settings_of({**CONFIG, "overlay": {"sheet": {"navigate": "please open"}}})

    def test_an_overlay_block_that_is_not_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="'overlay' must be"):
            settings_of({**CONFIG, "overlay": "yes please"})

    def test_a_section_that_is_not_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="'overlay.sheet'"):
            settings_of({**CONFIG, "overlay": {"sheet": "right"}})

    def test_a_group_override_that_is_not_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="overlay.groups.buffer"):
            settings_of({**CONFIG, "overlay": {"groups": {"buffer": "hide"}}})


class TestWhatTheLoaderOnlyWarnsAbout:
    def test_a_hud_group_that_names_no_group(self, settings_of, caplog):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "buffer save": "ctrl + s",
                            "buffer close": "ctrl + w",
                            "buffer overview": "hud(group:bufer)",
                        },
                    }
                }
            }
        )

        model.check_display(settings)

        assert "bufer" in caplog.text
        assert "buffer" in caplog.text, "the warning lists the groups that do exist"

    def test_a_hud_group_that_does_name_one_is_silent(self, settings_of, caplog):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {
                            "buffer save": "ctrl + s",
                            "buffer close": "ctrl + w",
                            "buffer overview": "hud(group:buffer)",
                        },
                    }
                }
            }
        )

        model.check_display(settings)

        assert "hud" not in caplog.text.lower()

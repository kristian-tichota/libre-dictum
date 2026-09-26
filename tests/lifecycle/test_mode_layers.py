from __future__ import annotations

import pytest

from libre_dictum.layers import Layer, LayerState, parse_target, qualified
from tests.conftest import FakeIndicator

CONFIG = {
    "enable_pedals": True,
    "enable_head_tracking": True,
    "ht_model_path": "landmarker.task",
    "starting_mode": "command mode",
    "modes": {
        "command mode": {
            "type": "vosk",
            "path": "m",
            "commands": {"pedals scroll": "mode(pedal:scrolling)"},
            "gestures": {"blink": "mode(mouse mode)"},
            "pedals": {"left": "left_mouse"},
        },
        "mouse mode": {"type": "vosk", "path": "m", "pedals": {"left": "right_mouse"}},
        "scrolling": {"pedals": {"left": "up", "right": "down"}},
    },
}


class TestParsingATarget:
    @pytest.mark.parametrize(
        ("written", "layer", "mode"),
        [
            ("mouse mode", None, "mouse mode"),
            ("voice:mouse mode", Layer.VOICE, "mouse mode"),
            ("gesture:mouse mode", Layer.GESTURE, "mouse mode"),
            ("pedal:scrolling", Layer.PEDAL, "scrolling"),
            ("pedal: scrolling ", Layer.PEDAL, "scrolling"),
            ("PEDAL:scrolling", Layer.PEDAL, "scrolling"),
        ],
    )
    def test_a_qualifier_names_the_layer(self, written, layer, mode):
        assert parse_target(written) == (layer, mode)

    def test_a_colon_that_is_not_a_layer_stays_part_of_the_name(self):
        assert parse_target("weird:mode") == (None, "weird:mode")

    def test_it_reads_back_what_it_writes(self):
        assert parse_target(qualified(Layer.PEDAL, "scrolling")) == (Layer.PEDAL, "scrolling")


class TestWhereTheLayersStart:
    def test_all_three_start_together(self, running_modes):
        modes = running_modes(CONFIG)

        assert modes.layer_state() == LayerState(
            voice="command mode", gesture="command mode", pedal="command mode"
        )

    def test_a_layer_may_be_pinned_somewhere_else(self, running_modes):
        modes = running_modes({**CONFIG, "starting_pedal_mode": "scrolling"})

        assert modes.layer_state() == LayerState(
            voice="command mode", gesture="command mode", pedal="scrolling"
        )

    def test_an_unpinned_layer_follows_the_mode_that_actually_started(self, running_modes):
        config = {**CONFIG, "starting_mode": "mouse mode"}
        modes = running_modes(config)
        modes._streams.pop("mouse mode")
        modes._layer[Layer.VOICE] = None
        modes._activate(modes._first_available("mouse mode"))
        modes._layer[Layer.GESTURE] = modes._starting(Layer.GESTURE)

        assert modes.layer_state().gesture == "command mode"

    def test_a_layer_whose_subsystem_is_off_is_not_reported(self, running_modes):
        modes = running_modes({"modes": CONFIG["modes"], "starting_mode": "command mode"})

        assert modes.layer_state() == LayerState(voice="command mode")


class TestAnUnqualifiedSwitch:
    def test_it_moves_all_three(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("mouse mode")

        assert modes.layer_state() == LayerState(
            voice="mouse mode", gesture="mouse mode", pedal="mouse mode"
        )

    def test_a_mode_with_nothing_for_a_layer_still_takes_it(self, running_modes):
        modes = running_modes({**CONFIG, "starting_pedal_mode": "scrolling"})
        modes.switch("mouse mode")

        assert modes.layer_state().pedal == "mouse mode"

    def test_a_deaf_target_moves_nothing_at_all(self, running_modes):
        modes = running_modes(CONFIG)
        modes._streams["mouse mode"].failure = RuntimeError("its thread died")
        modes.switch("mouse mode")

        assert modes.layer_state() == LayerState(
            voice="command mode", gesture="command mode", pedal="command mode"
        )


class TestAQualifiedSwitch:
    def test_it_moves_only_the_layer_it_names(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")

        assert modes.layer_state() == LayerState(
            voice="command mode", gesture="command mode", pedal="scrolling"
        )

    def test_a_layer_may_sit_on_a_mode_with_no_recognizer(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")

        assert modes.mode_for(Layer.PEDAL).pedals["right"].press == "down"

    def test_the_voice_layer_may_be_moved_on_its_own(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("voice:mouse mode")

        assert modes.layer_state() == LayerState(
            voice="mouse mode", gesture="command mode", pedal="command mode"
        )

    def test_a_layer_rejoins_the_voice_layer_wherever_it_is(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("mouse mode")
        modes.switch("pedal:scrolling")
        modes.switch("pedal:voice")

        assert modes.layer_state().pedal == "mouse mode"

    def test_a_mode_named_voice_is_still_that_mode(self, running_modes):
        modes = running_modes(
            {**CONFIG, "modes": {**CONFIG["modes"], "voice": {"pedals": {"left": "esc"}}}}
        )
        modes.switch("pedal:voice")

        assert modes.layer_state().pedal == "voice"

    def test_a_template_is_refused_for_the_voice_layer(self, running_modes, caplog):
        modes = running_modes(CONFIG)
        modes.switch("voice:scrolling")

        assert modes.active == "command mode"
        assert "unknown mode 'scrolling'" in caplog.text

    def test_moving_one_layer_runs_no_enter_or_exit_command(self, running_modes, backend):
        config = {**CONFIG, "modes": {**CONFIG["modes"]}}
        config["modes"]["mouse mode"] = {**config["modes"]["mouse mode"], "enter_command": "f1"}
        modes = running_modes(config)
        backend.clear()
        modes.switch("gesture:mouse mode")

        assert backend.sequence == ""

    def test_an_unqualified_switch_runs_them_once(self, running_modes, backend):
        config = {**CONFIG, "modes": {**CONFIG["modes"]}}
        config["modes"]["mouse mode"] = {**config["modes"]["mouse mode"], "enter_command": "f1"}
        modes = running_modes(config)
        backend.clear()
        modes.switch("mouse mode")

        assert backend.sequence == "f1↓ f1↑"


class TestGoingBack:
    def test_each_layer_remembers_its_own_previous(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")
        modes.switch("voice:mouse mode")
        modes.switch("pedal:previous mode")

        assert modes.layer_state() == LayerState(
            voice="mouse mode", gesture="command mode", pedal="command mode"
        )

    def test_going_back_on_the_pedals_leaves_the_voice_layer_alone(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")
        modes.switch("pedal:previous mode")

        assert modes.active == "command mode"
        assert modes.previous is None

    def test_an_unqualified_go_back_takes_every_layer_to_its_own_previous(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")
        modes.switch("mouse mode")
        modes.switch("previous mode")

        assert modes.layer_state() == LayerState(
            voice="command mode", gesture="command mode", pedal="scrolling"
        )


class TestReload:
    def test_a_layer_whose_mode_was_deleted_falls_back(self, running_modes, settings_of, caplog):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")
        smaller = {
            **CONFIG,
            "modes": {
                "command mode": {"type": "vosk", "path": "m", "pedals": {"left": "left_mouse"}},
                "mouse mode": CONFIG["modes"]["mouse mode"],
            },
        }
        modes.apply(settings_of(smaller))

        assert modes.layer_state().pedal == "command mode"
        assert "pedal layer's mode 'scrolling' is gone" in caplog.text

    def test_a_layer_whose_mode_survived_stays_put(self, running_modes, settings_of):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")
        modes.apply(settings_of(CONFIG))

        assert modes.layer_state().pedal == "scrolling"


class TestWhatADisplayIsTold:
    def test_the_indicator_gets_every_layer_at_once(self, running_modes):
        tray = FakeIndicator()
        modes = running_modes(CONFIG, tray)
        modes.switch("pedal:scrolling")

        assert tray.layers == LayerState(
            voice="command mode", gesture="command mode", pedal="scrolling"
        )

    def test_the_voice_layer_is_still_what_the_active_mode_means(self, running_modes):
        modes = running_modes(CONFIG)
        modes.switch("pedal:scrolling")

        assert modes.active == "command mode"
        assert modes.active_mode is modes.mode_for(Layer.VOICE)


class TestDivergence:
    def test_nothing_is_diverged_while_the_layers_agree(self):
        assert LayerState(voice="a", gesture="a", pedal="a").diverged == ()

    def test_a_layer_elsewhere_is_named_with_its_mode(self):
        state = LayerState(voice="a", gesture="a", pedal="b")

        assert state.diverged == ((Layer.PEDAL, "b"),)

    def test_a_layer_that_is_not_running_is_never_diverged(self):
        assert LayerState(voice="a").diverged == ()

    def test_before_startup_nothing_is_diverged(self):
        assert LayerState(gesture="a", pedal="b").diverged == ()

    def test_the_summary_names_every_running_layer_in_order(self):
        state = LayerState(voice="a", gesture="b", pedal="c")

        assert state.summary() == "voice: a, gesture: b, pedal: c"

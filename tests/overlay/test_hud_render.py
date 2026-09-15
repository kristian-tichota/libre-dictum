from __future__ import annotations

import pytest

from libre_dictum.health import Health
from libre_dictum.layers import Layer, LayerState, SleepView
from libre_dictum.meters import GestureMeter, HandReading, Reading
from libre_dictum.overlay import model, protocol
from libre_dictum.overlay.hud import render
from libre_dictum.overlay.protocol import CalibrationDot, State
from libre_dictum.status import HeldInput

CONFIG = {
    "modes": {
        "::alphabet": {
            "commands": {
                "type a": "a",
                "type b": "b",
                "press e": "e",
                "press hotel": "h",
                "key i": "i",
                "key j": "j",
            }
        },
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [1, 2, 3],
            "imports": ["::alphabet"],
            "commands": {
                "vector alpha": "f13",
                "vector bravo": "f14",
                "vector charlie": "f15",
                "dispatch alpha": "meta + f13",
                "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "ctrl + f13",
                "link bravo": "ctrl + f14",
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer open": "ctrl + o",
                "go away": "mode(other mode)",
            },
        },
        "other mode": {"type": "vosk", "path": "m"},
    }
}

DICTATION = {
    "modes": {
        "root mode": {
            "type": "transformer",
            "model_name": "t",
            "commands": {"{rest}": "type({1})"},
        }
    }
}


@pytest.fixture
def catalogue(settings_of):
    def build(data=None):
        settings = settings_of(data or CONFIG)
        views = [
            model.build_view(mode, modes=list(settings.modes))
            for mode in settings.layer_modes.values()
        ]
        return protocol.catalogue_of(
            views,
            revision=1,
            navigate=model.DEFAULT_NAVIGATE,
            sheet_open=settings.overlay.sheet_open or "",
            sheet_close=settings.overlay.sheet_close or "",
            sheet_modes=settings.overlay.sheet_modes or "",
            sheet_pedals=settings.overlay.sheet_pedals or "",
            sheet_gestures=settings.overlay.sheet_gestures or "",
            pedals=list(settings.pedal.buttons) if settings.pedal else [],
            gestures=(
                list(settings.head_tracking.gesture_definitions) if settings.head_tracking else []
            ),
            colours={name: mode.icon for name, mode in settings.layer_modes.items()},
        )

    return build


def state(**kwargs):
    """A state frame, with every layer already on the mode the fixtures describe."""
    sheet = kwargs.pop("sheet", False)
    chip = kwargs.pop("chip", True)
    page = kwargs.pop("page", "index")
    group = kwargs.pop("group", None)
    mode = kwargs.pop("mode", "root mode")
    layers = LayerState(
        voice=mode,
        gesture=kwargs.pop("gesture_mode", mode),
        pedal=kwargs.pop("pedal_mode", mode),
    )
    return protocol.state_of(
        model.Snapshot(layers=layers, open_group=group, **kwargs),
        revision=1,
        chip=chip,
        sheet=sheet,
        page=page,
    )


class TestTheChip:
    def test_before_the_first_frame_it_says_it_is_connecting(self):
        chip = render.chip_of(None)

        assert chip.mode == render.WAITING
        assert not chip.hearing

    def test_it_shows_the_mode_and_its_colour(self, catalogue):
        chip = render.chip_of(state(), catalogue())

        assert chip.mode == "root mode"
        assert chip.colour == (1, 2, 3)

    def test_a_mode_the_catalogue_has_not_heard_of_still_gets_a_name(self, catalogue):
        chip = render.chip_of(state(mode="gone"), catalogue())

        assert chip.mode == "gone"
        assert chip.colour is None

    def test_held_keys_are_glyphs_in_a_fixed_order(self, catalogue):
        chip = render.chip_of(state(held=HeldInput(held=("shift",), pending=("ctrl",))))

        assert chip.glyphs == "⌃ ⇧"

    def test_the_tooltip_spells_the_glyphs_out_in_words(self):
        chip = render.chip_of(state(held=HeldInput(held=("shift",), pending=("ctrl",), saved="3")))

        assert chip.tooltip() == "ctrl pending, shift held, saved '3'"

    def test_a_fault_joins_the_tooltip_so_the_reason_is_never_only_a_colour(self):
        health = Health(failed=(("mode 'root mode'", "model file vanished"),))
        chip = render.chip_of(state(health=health))

        assert chip.fault == "mode 'root mode' (model file vanished)"
        assert "model file vanished" in chip.tooltip()

    def test_nothing_asleep_draws_no_sleep_line(self):
        assert render.chip_of(state()).sleep_line == ""

    def test_everything_asleep_needs_no_list(self):
        chip = render.chip_of(state(sleep=SleepView(asleep=frozenset(Layer))))

        assert chip.sleep_line == render.ASLEEP_ALL

    def test_one_mechanism_asleep_is_named(self):
        chip = render.chip_of(state(sleep=SleepView(asleep=frozenset({Layer.GESTURE}))))

        assert chip.sleep_line == "asleep: gesture"

    def test_an_arming_is_on_the_line_before_anything_has_changed(self):
        chip = render.chip_of(state(sleep=SleepView(prompt="sleep? again to confirm")))

        assert chip.sleep_line == "sleep? again to confirm"
        assert not chip.asleep

    def test_both_facts_share_the_line(self):
        chip = render.chip_of(
            state(
                sleep=SleepView(
                    asleep=frozenset({Layer.GESTURE}), prompt="wake gesture? again to confirm"
                )
            )
        )

        assert chip.sleep_line == "asleep: gesture · wake gesture? again to confirm"

    def test_the_mode_dot_dims_while_anything_is_asleep(self, catalogue):
        lit = render.chip_of(state(), catalogue())
        quiet = render.chip_of(state(sleep=SleepView(asleep=frozenset(Layer))), catalogue())

        assert lit.swatch == lit.colour
        assert quiet.swatch is not None and quiet.swatch != quiet.colour
        assert all(channel >= 0 for channel in quiet.swatch)

    def test_a_mode_with_no_colour_has_no_dot_to_dim(self):
        chip = render.chip_of(state(sleep=SleepView(asleep=frozenset(Layer))))

        assert chip.swatch is None

    def test_the_line_says_which_mechanism_may_wake_it(self):
        chip = render.chip_of(
            state(sleep=SleepView(asleep=frozenset(Layer), owners=frozenset({Layer.PEDAL})))
        )

        assert chip.sleep_line == "asleep · wake with pedal"

    def test_a_refused_wake_says_so(self):
        chip = render.chip_of(
            state(
                sleep=SleepView(
                    asleep=frozenset(Layer),
                    owners=frozenset({Layer.PEDAL}),
                    notice="refused: only pedal wakes this",
                )
            )
        )

        assert chip.sleep_line == "asleep · refused: only pedal wakes this"

    def test_a_sleeping_session_still_says_which_mode_it_is_in(self, catalogue):
        chip = render.chip_of(state(sleep=SleepView(asleep=frozenset(Layer))), catalogue())

        assert chip.mode == "root mode"

    def test_a_match_is_the_utterance_and_a_tick(self):
        chip = render.chip_of(state(heard=model.Heard(text="buffer save", matched="buffer save")))

        assert chip.utterance == f'"buffer save" {render.MATCHED}'
        assert chip.matched

    def test_a_pattern_is_named_only_when_it_is_not_what_was_said(self):
        chip = render.chip_of(state(heard=model.Heard(text="three down", matched="{numeric} down")))

        assert chip.utterance == f'"three down" → {{numeric}} down {render.MATCHED}'

    def test_a_near_miss_rides_on_the_utterance_line(self):
        chip = render.chip_of(state(heard=model.Heard(text="buffer safe", nearest="buffer save")))

        assert chip.utterance == '"buffer safe" → buffer save?'
        assert chip.missed

    def test_a_miss_with_nothing_near_it_says_only_what_was_said(self):
        chip = render.chip_of(state(heard=model.Heard(text="elephant")))

        assert chip.utterance == '"elephant"'
        assert chip.missed

    @pytest.mark.parametrize(
        ("partial", "mark"),
        [(None, render.HEARING[None]), ("", render.HEARING[""]), ("vec", render.HEARING["words"])],
    )
    def test_the_hearing_mark_has_three_states(self, partial, mark):
        chip = render.chip_of(state(heard=model.Heard(partial=partial)))

        assert chip.mark == mark

    def test_a_dictation_mode_says_outright_that_it_cannot_be_navigated(self, catalogue):
        chip = render.chip_of(state(), catalogue(DICTATION))

        assert chip.note == render.NO_NAVIGATION

    def test_a_navigable_mode_says_nothing_of_the_kind(self, catalogue):
        assert render.chip_of(state(), catalogue()).note is None


class TestWhenTheSheetDraws:
    def test_not_at_all_while_it_is_closed(self, catalogue):
        assert render.panel_of(state(sheet=False), catalogue()) is None

    def test_not_before_a_catalogue_has_arrived(self):
        assert render.panel_of(state(sheet=True), None) is None

    def test_the_index_when_no_group_is_open(self, catalogue):
        panel = render.panel_of(state(sheet=True), catalogue())

        assert panel.layout is render.Layout.INDEX

    def test_the_group_when_one_is(self, catalogue):
        panel = render.panel_of(state(sheet=True, group="buffer"), catalogue())

        assert panel.title == "buffer"

    def test_a_group_the_catalogue_does_not_have_falls_back_to_the_index(self, catalogue):
        panel = render.panel_of(state(sheet=True, group="renamed"), catalogue())

        assert panel.layout is render.Layout.INDEX

    def test_either_name_of_a_merged_table_opens_it(self, catalogue):
        for name in ("vector", "dispatch", "link"):
            panel = render.panel_of(state(sheet=True, group=name), catalogue())
            assert panel.title == "vector/dispatch/link"


class TestTheIndex:
    def panel(self, catalogue, data=None):
        return render.panel_of(state(sheet=True), catalogue(data))

    def test_it_lists_every_group_with_its_size(self, catalogue):
        found = {item.label: item.detail for item in self.panel(catalogue).items}

        assert found["alphabet"] == "6"
        assert found["buffer"] == "3"

    def test_a_template_loses_its_colons(self, catalogue):
        assert "alphabet" in {item.label for item in self.panel(catalogue).items}

    def test_every_group_carries_the_phrase_that_opens_it(self, catalogue):
        found = {item.label: item.phrase for item in self.panel(catalogue).items}

        assert found["alphabet"] == '"open alphabet"'
        assert found["vector/dispatch/link"] == ('"open vector" / "open dispatch" / "open link"')

    def test_the_hint_says_how_to_use_them(self, catalogue):
        assert self.panel(catalogue).hint == 'say "open" + a name'

    def test_a_group_nobody_can_pronounce_is_shown_and_dimmed(self, catalogue, settings_of):
        panel = self.panel(
            catalogue,
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"{numeric} up": "up", "{numeric} down": "down"},
                    }
                }
            },
        )
        item = next(one for one in panel.items if one.label == "{numeric}")

        assert item.phrase == ""
        assert item.dim, "a group nobody can reach must look like one, not vanish"

    def test_a_dictation_mode_says_so_instead_of_hinting(self, catalogue):
        assert self.panel(catalogue, DICTATION).hint == render.NO_NAVIGATION

    def test_the_footer_names_the_ways_out_nobody_remembers(self, catalogue):
        assert self.panel(catalogue).footer == (
            '"overlay modes" → modes',
            '"overlay close" → hide',
            '"go away" → other mode',
        )

    def test_and_not_the_mode_names_themselves(self, catalogue):
        switches = [line for line in self.panel(catalogue).footer if "overlay" not in line]
        assert all("go away" in line for line in switches)

    def test_the_index_does_not_offer_a_way_back_to_itself(self, catalogue):
        assert not any(render.TO_INDEX in line for line in self.panel(catalogue).footer)


class TestTheWayOffAGroup:
    """A page reached by voice with no visible way off it."""

    def panel(self, catalogue, group="buffer"):
        return render.panel_of(state(sheet=True, group=group), catalogue())

    def test_a_group_says_how_to_get_back_to_the_index(self, catalogue):
        assert f'"overlay open" → {render.TO_INDEX}' in self.panel(catalogue).footer

    def test_a_group_says_how_to_put_the_sheet_away(self, catalogue):
        assert f'"overlay close" → {render.TO_HIDE}' in self.panel(catalogue).footer

    def test_the_way_back_comes_before_the_ways_out_of_the_mode(self, catalogue):
        footer = self.panel(catalogue).footer
        assert footer.index(f'"overlay open" → {render.TO_INDEX}') < footer.index(
            '"go away" → other mode'
        )

    def test_it_draws_the_phrases_the_config_renamed_them_to(self, catalogue):
        renamed = dict(CONFIG)
        renamed["overlay"] = {
            "sheet": {"open": "menu show", "close": "menu hide", "modes": "menu states"}
        }
        footer = render.panel_of(state(sheet=True, group="buffer"), catalogue(renamed)).footer

        assert f'"menu show" → {render.TO_INDEX}' in footer
        assert f'"menu hide" → {render.TO_HIDE}' in footer
        assert not any("overlay" in line for line in footer)

    def test_a_phrase_switched_off_is_not_drawn_as_a_way_back(self, catalogue):
        without = dict(CONFIG)
        without["overlay"] = {"sheet": {"open": None, "close": None, "modes": None}}
        footer = render.panel_of(state(sheet=True, group="buffer"), catalogue(without)).footer

        assert not any(
            target in line
            for line in footer
            for target in (render.TO_INDEX, render.TO_HIDE, render.TO_MODES)
        )


SWITCHING = {
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [1, 2, 3],
            "commands": {"buffer save": "ctrl + s", "go away": "mode(other mode)"},
            "gestures": {"smile": "mode(other mode)"},
        },
        "other mode": {"type": "vosk", "path": "m"},
        "far mode": {"type": "vosk", "path": "m"},
    }
}


class TestTheModesPage:
    """Where the session is, and what reaches every other state from here."""

    def panel(self, catalogue, data=None, mode="root mode"):
        frame = protocol.state_of(
            model.Snapshot(layers=LayerState(voice=mode, gesture=mode, pedal=mode)),
            revision=1,
            chip=True,
            sheet=True,
            page="modes",
        )
        return render.panel_of(frame, catalogue(data or SWITCHING))

    def rows(self, panel):
        return {row.label: row for row in panel.rows}

    def test_the_state_the_session_is_in_is_marked(self, catalogue):
        labels = list(self.rows(self.panel(catalogue)))
        assert f"{render.HERE} root mode" in labels
        assert "other mode" in labels

    def test_the_row_you_are_on_draws_no_transition_to_itself(self, catalogue):
        row = self.rows(self.panel(catalogue))[f"{render.HERE} root mode"]

        assert [item.label for item in row.items] == [""] * len(row.items)

    def test_a_mode_reachable_by_name_shows_the_phrase_to_say(self, catalogue):
        panel = self.panel(catalogue)
        voice = panel.columns.index("voice")

        assert self.rows(panel)["other mode"].items[voice].label == '"other mode"'

    def test_a_gesture_is_drawn_as_itself_and_not_as_something_to_say(self, catalogue):
        panel = self.panel(catalogue)
        gesture = panel.columns.index("gesture")

        assert self.rows(panel)["other mode"].items[gesture].label == "smile"

    def test_the_column_per_input_method_is_the_axis_that_grows(self, catalogue):
        assert self.panel(catalogue).columns == ("voice", "gesture")

    def test_an_input_method_this_config_never_uses_gets_no_column(self, catalogue):
        assert self.panel(catalogue, CONFIG).columns == ("voice",)

    def test_a_state_an_input_method_cannot_reach_is_drawn_as_unreachable(self, catalogue):
        panel = self.panel(catalogue)
        gesture = panel.columns.index("gesture")
        cell = self.rows(panel)["far mode"].items[gesture]

        assert cell.label == render.MISSING
        assert cell.dim

    def test_a_target_that_is_not_a_mode_still_gets_a_row(self, catalogue):
        rows = self.rows(self.panel(catalogue, RETURNING, mode="dictate mode"))

        assert "previous mode" in rows

    def test_a_transition_nobody_can_say_is_drawn_as_what_it_means(self, catalogue):
        row = self.rows(self.panel(catalogue, RETURNING, mode="dictate mode"))["previous mode"]

        assert row.items[0].label == render.UNSPOKEN

    def test_it_says_how_to_get_back_off_it(self, catalogue):
        footer = self.panel(catalogue).footer

        assert f'"overlay open" → {render.TO_INDEX}' in footer
        assert f'"overlay close" → {render.TO_HIDE}' in footer

    def test_the_other_two_pages_offer_it(self, catalogue):
        index = render.panel_of(state(sheet=True), catalogue(SWITCHING))
        group = render.panel_of(state(sheet=True, group="buffer"), catalogue(SWITCHING))

        for panel in (index, group):
            assert f'"overlay modes" → {render.TO_MODES}' in panel.footer


RETURNING = {
    "modes": {
        "root mode": {"type": "vosk", "path": "m", "commands": {"buffer save": "ctrl + s"}},
        "dictate mode": {
            "type": "transformer",
            "model_name": "tiny",
            "commands": {"{rest}": "type({1}) + mode(previous mode)"},
        },
    }
}


class TestAGroupAsATable:
    def panel(self, catalogue, group):
        return render.panel_of(state(sheet=True, group=group), catalogue())

    def test_an_aligned_table_puts_the_tails_on_the_columns(self, catalogue):
        panel = self.panel(catalogue, "vector")

        assert panel.layout is render.Layout.TABLE
        assert panel.columns == ("alpha", "bravo", "charlie")
        assert [row.label for row in panel.rows] == ["vector", "dispatch", "link"]

    def test_its_cells_only_say_whether_the_command_is_there(self, catalogue):
        panel = self.panel(catalogue, "vector")

        assert panel.rows[0].items[0].label == render.PRESENT
        assert panel.rows[0].items[0].phrase == '"vector alpha"'

    def test_a_gap_is_drawn_and_never_left_blank(self, catalogue):
        panel = self.panel(catalogue, "link")
        link = next(row for row in panel.rows if row.label == "link")

        assert [item.label for item in link.items] == [
            render.PRESENT,
            render.PRESENT,
            render.MISSING,
        ]
        assert link.items[2].dim

    def test_an_aligned_row_needs_no_phrase_of_its_own(self, catalogue):
        assert all(row.phrase == "" for row in self.panel(catalogue, "vector").rows)

    def test_a_ragged_table_has_no_columns_and_carries_the_tails_in_its_cells(self, catalogue):
        panel = self.panel(catalogue, "::alphabet")

        assert panel.columns == ()
        assert [row.label for row in panel.rows] == ["type", "press", "key"]
        assert [item.label for item in panel.rows[0].items] == ["a", "b"]

    def test_a_ragged_row_says_what_to_put_in_front_of_a_cell(self, catalogue):
        panel = self.panel(catalogue, "::alphabet")

        assert panel.rows[0].phrase == '"type _"'

    def test_a_cell_shows_the_word_to_say_and_not_the_key_it_produces(self, catalogue):
        panel = self.panel(catalogue, "::alphabet")
        press = next(row for row in panel.rows if row.label == "press")

        assert [item.label for item in press.items] == ["e", "hotel"]

    def test_the_width_is_the_widest_row_so_the_phrases_line_up(self, catalogue):
        panel = self.panel(catalogue, "::alphabet")

        assert panel.width == max(len(row.items) for row in panel.rows)


class TestAGroupAsAList:
    def test_one_first_word_is_a_list(self, catalogue):
        panel = render.panel_of(state(sheet=True, group="buffer"), catalogue())

        assert panel.layout is render.Layout.LIST
        assert [item.label for item in panel.items] == [
            "buffer save",
            "buffer close",
            "buffer open",
        ]

    def test_it_shows_the_whole_phrase_and_what_it_does(self, catalogue):
        panel = render.panel_of(state(sheet=True, group="buffer"), catalogue())

        assert panel.items[0].detail == "ctrl + s"

    def test_the_phrase_that_opened_it_stays_on_screen(self, catalogue):
        panel = render.panel_of(state(sheet=True, group="buffer"), catalogue())

        assert panel.hint == '"open buffer"'


PEDALLED = {
    "enable_pedals": True,
    "enable_head_tracking": True,
    "ht_model_path": "landmarker.task",
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [1, 2, 3],
            "commands": {"buffer save": "ctrl + s", "feet scroll": "mode(pedal:scrolling)"},
            "gestures": {"blink": "mode(other mode)"},
            "pedals": {
                "left": {"press": "hold(left_mouse)", "release": "release(left_mouse)"},
                "middle": "enter",
            },
        },
        "other mode": {"type": "vosk", "path": "m", "pedals": {"right": "mode(root mode)"}},
        "scrolling": {"pedals": {"left": "up", "right": "down"}},
    },
}


GESTURED = {
    "enable_head_tracking": True,
    "ht_model_path": "landmarker.task",
    "ht_custom_gestures": {
        "fist": {"handFist": {"min": 0.8}},
        "pinch": {"handPinchIndex": {"min": 0.8}},
        "blink": {"eyeBlinkLeft": {"min": 0.5}},
    },
    "modes": {
        "root mode": {
            "type": "vosk",
            "path": "m",
            "commands": {"hands off": "mode(gesture:::quiet)"},
            "gestures": {
                "fist": {"press": "hold(left_mouse)", "release": "release(left_mouse)"},
                "pinch": "left_mouse",
            },
        },
        "::quiet": {"gestures": {"blink": "esc"}},
    },
}


class TestTheGesturesPage:
    """A row per gesture the configuration *defines*, mirroring the pedals page exactly."""

    def panel(self, catalogue, data=None, gesture_mode="root mode"):
        frame = protocol.state_of(
            model.Snapshot(
                layers=LayerState(voice="root mode", gesture=gesture_mode, pedal="root mode")
            ),
            revision=1,
            chip=True,
            sheet=True,
            page="gestures",
        )
        return render.panel_of(frame, catalogue(data or GESTURED))

    def rows(self, panel):
        return {row.label: row for row in panel.rows}

    def test_it_has_a_row_per_gesture_the_configuration_defines(self, catalogue):
        assert list(self.rows(self.panel(catalogue))) == ["fist", "pinch", "blink"]

    def test_both_edges_get_a_column(self, catalogue):
        assert self.panel(catalogue).columns == ("press", "release")

    def test_a_gesture_bound_on_both_edges_shows_both(self, catalogue):
        row = self.rows(self.panel(catalogue))["fist"]

        assert [item.label for item in row.items] == ["hold(left_mouse)", "release(left_mouse)"]

    def test_a_bare_response_leaves_the_release_a_drawn_gap(self, catalogue):
        row = self.rows(self.panel(catalogue))["pinch"]

        assert row.items[0].label == "left_mouse"
        assert row.items[1].label == render.MISSING
        assert row.items[1].dim

    def test_a_gesture_this_mode_does_not_bind_is_still_a_row(self, catalogue):
        row = self.rows(self.panel(catalogue))["blink"]

        assert [item.label for item in row.items] == [render.MISSING, render.MISSING]

    def test_the_title_names_the_set_the_gestures_are_on(self, catalogue):
        assert "quiet" in self.panel(catalogue, gesture_mode="::quiet").title

    def test_it_shows_the_set_the_gesture_layer_is_on_not_the_voice_one(self, catalogue):
        rows = self.rows(self.panel(catalogue, gesture_mode="::quiet"))

        assert rows["blink"].items[0].label == "esc"
        assert rows["fist"].items[0].label == render.MISSING

    def test_the_footer_says_what_moves_the_gesture_layer(self, catalogue):
        footer = self.panel(catalogue, gesture_mode="::quiet").footer

        assert '"hands off" → quiet' in footer

    def test_the_footer_offers_the_ways_off_the_page(self, catalogue):
        footer = self.panel(catalogue).footer

        assert f'"overlay open" → {render.TO_INDEX}' in footer
        assert f'"overlay close" → {render.TO_HIDE}' in footer

    def test_with_no_gestures_defined_the_page_says_the_setting(self, catalogue):
        panel = self.panel(catalogue, data=CONFIG)

        assert panel.rows == ()
        assert panel.hint == render.NO_GESTURES

    def test_no_other_page_offers_it_when_nothing_is_defined(self, catalogue):
        footer = render.panel_of(state(sheet=True), catalogue(CONFIG)).footer

        assert not any(line.split(" → ")[-1] == render.TO_GESTURES for line in footer)

    def test_it_is_offered_from_the_index_when_there_are_gestures(self, catalogue):
        footer = render.panel_of(state(sheet=True), catalogue(GESTURED)).footer

        assert f'"overlay gestures" → {render.TO_GESTURES}' in footer

    def test_the_two_edge_pages_offer_each_other(self, catalogue):
        both = {**GESTURED, **PEDALLED, "modes": {**GESTURED["modes"], **PEDALLED["modes"]}}
        both["ht_custom_gestures"] = GESTURED["ht_custom_gestures"]

        gestures = self.panel(catalogue, data=both).footer
        assert f'"overlay pedals" → {render.TO_PEDALS}' in gestures


def meter(name, *readings, active=False, hold=0.0):
    """One gesture's reading, from (feature, score, min, max) tuples."""
    return GestureMeter(
        name=name,
        readings=tuple(Reading(*reading) for reading in readings),
        active=active,
        hold=hold,
    )


class TestTheLiveColumnOnTheGesturesPage:
    """Why a gesture is not firing: one bar, and the one condition keeping it shut."""

    def panel(self, catalogue, meters=(), hands=(), data=None):
        frame = protocol.state_of(
            model.Snapshot(
                layers=LayerState(voice="root mode", gesture="root mode", pedal="root mode"),
                meters=tuple(meters),
                hands=tuple(hands),
            ),
            revision=1,
            chip=True,
            sheet=True,
            page="gestures",
        )
        return render.panel_of(frame, catalogue(data or GESTURED))

    def cell(self, panel, name):
        return {row.label: row for row in panel.rows}[name].items[-1]

    def test_with_no_readings_there_is_no_column(self, catalogue):
        panel = self.panel(catalogue)

        assert panel.columns == ("press", "release")
        assert all(len(row.items) == 2 for row in panel.rows)

    def test_readings_add_one_column_at_the_end(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))])

        assert panel.columns == ("press", "release", render.METER_COLUMN)

    def test_the_blocking_condition_is_named_with_its_value_and_threshold(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))])

        assert "handFist 0.62 >0.80" in self.cell(panel, "fist").label

    def test_only_the_least_satisfied_condition_is_named(self, catalogue):
        panel = self.panel(
            catalogue,
            [meter("fist", ("handFist", 0.62, 0.8, None), ("handThumbCurl", 0.1, 0.4, None))],
        )
        label = self.cell(panel, "fist").label

        assert "handThumbCurl" in label
        assert "handFist" not in label

    def test_the_bar_says_how_close_the_whole_gesture_is(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.4, 0.8, None))])

        assert self.cell(panel, "fist").label.startswith(render.bar(0.5))

    def test_a_held_gesture_says_so_instead_of_a_number(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.9, 0.8, None), active=True)])
        cell = self.cell(panel, "fist")

        assert cell.label == f"{render.bar(1.0)} {render.METER_HELD}"
        assert cell.tone == render.TONE_LIVE

    def test_a_gesture_waiting_out_its_dwell_is_not_drawn_as_blocked(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.9, 0.8, None), hold=0.25)])
        cell = self.cell(panel, "fist")

        assert "0.25s" in cell.label
        assert cell.tone == render.TONE_LIVE

    def test_a_feature_no_frame_reported_reads_absent_rather_than_zero(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", None, 0.8, None))])
        cell = self.cell(panel, "fist")

        assert "handFist -- >0.80" in cell.label
        assert cell.tone == render.TONE_MEASURED

    def test_a_blocked_gesture_is_drawn_in_the_blocked_tone(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))])

        assert self.cell(panel, "fist").tone == render.TONE_BLOCKED

    def test_a_meter_cell_is_a_measurement_so_it_carries_a_tone(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))])

        assert self.cell(panel, "fist").tone
        assert not self.cell(panel, "fist").dim

    def test_a_gesture_with_no_reading_keeps_its_two_cells(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))])
        rows = {row.label: row for row in panel.rows}

        assert len(rows["fist"].items) == 3
        assert len(rows["blink"].items) == 2

    def test_a_max_condition_reads_as_the_config_wrote_it(self, catalogue):
        panel = self.panel(catalogue, [meter("fist", ("handFist", 0.7, None, 0.4))])

        assert "handFist 0.70 <0.40" in self.cell(panel, "fist").label


class TestTheBar:
    def test_it_is_always_the_same_width(self):
        assert all(len(render.bar(value / 10)) == render.BAR_WIDTH for value in range(11))

    def test_empty_and_full_are_what_they_say(self):
        assert render.bar(0.0) == render.BAR_EMPTY * render.BAR_WIDTH
        assert render.bar(1.0) == render.BAR_FULL * render.BAR_WIDTH

    def test_it_fills_in_proportion(self):
        assert render.bar(0.5).count(render.BAR_FULL) == render.BAR_WIDTH // 2

    def test_a_value_outside_the_range_is_clamped_rather_than_refused(self):
        assert render.bar(-1.0) == render.bar(0.0)
        assert render.bar(9.0) == render.bar(1.0)


class TestTheRatiosUnderTheTable:
    """What a calibration is set from, where a hand can be seen."""

    def panel(self, catalogue, hands):
        return TestTheLiveColumnOnTheGesturesPage().panel(
            catalogue, [meter("fist", ("handFist", 0.62, 0.8, None))], hands
        )

    def hand(self):
        return HandReading(
            side="right",
            primary=True,
            span=0.164,
            extension=(0.58, 0.36, 0.34, 0.33, 0.32),
            pinch_index=0.21,
            pinch_middle=0.55,
            spread=0.20,
            palm_area=0.30,
        )

    def test_the_block_names_the_setting_it_exists_to_be_written_into(self, catalogue):
        notes = self.panel(catalogue, [self.hand()]).notes

        assert notes[0] == render.RATIOS_TITLE
        assert "ht_hand_calibration" in notes[0]

    def test_every_ratio_a_constant_needs_is_there(self, catalogue):
        block = "\n".join(self.panel(catalogue, [self.hand()]).notes)

        assert "right hand (primary)" in block
        assert "span 0.164" in block
        assert "Index 0.36" in block
        assert "index 0.21" in block and "middle 0.55" in block
        assert "spread 0.20" in block
        assert "palmArea 0.30" in block

    def test_no_scores_appear_among_them(self, catalogue):
        block = "\n".join(self.panel(catalogue, [self.hand()]).notes)

        assert "handFist" not in block

    def test_both_hands_get_a_block(self, catalogue):
        notes = self.panel(
            catalogue, [self.hand(), HandReading(side="left", span=0.1, extension=(0.5,) * 5)]
        ).notes

        assert any(line.startswith("right hand (primary)") for line in notes)
        assert any(line.startswith("left hand") for line in notes)

    def test_with_no_hand_in_shot_the_block_says_so(self, catalogue):
        assert self.panel(catalogue, []).notes == (render.RATIOS_TITLE, render.NO_HANDS)

    def test_without_readings_there_is_no_block_at_all(self, catalogue):
        panel = TestTheLiveColumnOnTheGesturesPage().panel(catalogue, [], [self.hand()])

        assert panel.notes == ()


class TestTheChipsHeldGestureLine:
    """A held mouse button is the most invisible thing this display shows."""

    def chip(self, catalogue, held=(), data=None, gesture_mode="root mode"):
        frame = protocol.state_of(
            model.Snapshot(
                layers=LayerState(voice="root mode", gesture=gesture_mode, pedal="root mode"),
                held_gestures=tuple(held),
            ),
            revision=1,
        )
        return render.chip_of(frame, catalogue(data or GESTURED))

    def test_with_nothing_held_there_is_no_line(self, catalogue):
        assert self.chip(catalogue).gesture_line == ""

    def test_a_held_gesture_names_itself_and_what_it_is_holding(self, catalogue):
        line = self.chip(catalogue, ["fist"]).gesture_line

        assert line == "gesture: fist → hold(left_mouse)"

    def test_the_press_edge_is_what_is_shown(self, catalogue):
        assert "release(" not in self.chip(catalogue, ["fist"]).gesture_line

    def test_two_held_shapes_are_both_named(self, catalogue):
        line = self.chip(catalogue, ["fist", "pinch"]).gesture_line

        assert line == "gesture: fist → hold(left_mouse) · gesture: pinch → left_mouse"

    def test_a_shape_with_nothing_bound_is_still_named(self, catalogue):
        assert self.chip(catalogue, ["blink"]).gesture_line == "gesture: blink"

    def test_the_response_is_read_off_the_gesture_layers_mode(self, catalogue):
        line = self.chip(catalogue, ["blink"], gesture_mode="::quiet").gesture_line

        assert line == "gesture: blink → esc"

    def test_it_survives_a_catalogue_that_has_never_heard_of_the_shape(self, catalogue):
        assert self.chip(catalogue, ["nonesuch"]).gesture_line == "gesture: nonesuch"

    def test_before_the_first_catalogue_it_still_names_what_is_held(self, catalogue):
        frame = protocol.state_of(
            model.Snapshot(layers=LayerState(voice="root mode"), held_gestures=("fist",)),
            revision=1,
        )

        assert render.chip_of(frame, None).gesture_line == "gesture: fist"


class TestTheChipsLayerLine:
    """Where the other two mechanisms are, and only when that is worth a line."""

    def test_while_the_layers_agree_there_is_no_line(self, catalogue):
        chip = render.chip_of(state(), catalogue(PEDALLED))

        assert chip.layers == ()
        assert chip.layer_line == ""

    def test_a_layer_elsewhere_is_named_with_its_mode(self, catalogue):
        chip = render.chip_of(state(pedal_mode="scrolling"), catalogue(PEDALLED))

        assert chip.layers == (("pedal", "scrolling"),)
        assert chip.layer_line == "pedal: scrolling"

    def test_both_layers_elsewhere_read_in_column_order(self, catalogue):
        frame = state(gesture_mode="other mode", pedal_mode="scrolling")
        chip = render.chip_of(frame, catalogue(PEDALLED))

        assert chip.layer_line == "gesture: other mode · pedal: scrolling"

    def test_a_layer_that_is_not_running_is_never_named(self, catalogue):
        frame = protocol.state_of(model.Snapshot(layers=LayerState(voice="root mode")), revision=1)

        assert render.chip_of(frame, catalogue(PEDALLED)).layers == ()

    def test_before_the_first_frame_there_is_no_line(self):
        assert render.chip_of(None).layer_line == ""


class TestThePedalColumnOnTheModesPage:
    def panel(self, catalogue, **layers):
        mode = layers.pop("mode", "root mode")
        frame = protocol.state_of(
            model.Snapshot(
                layers=LayerState(
                    voice=mode,
                    gesture=layers.get("gesture_mode", mode),
                    pedal=layers.get("pedal_mode", mode),
                )
            ),
            revision=1,
            chip=True,
            sheet=True,
            page="modes",
        )
        return render.panel_of(frame, catalogue(PEDALLED))

    def rows(self, panel):
        return {row.label: row for row in panel.rows}

    def test_a_pedal_that_switches_is_a_column(self, catalogue):
        panel = self.panel(catalogue, mode="other mode")

        assert "pedal" in panel.columns
        cells = dict(zip(panel.columns, self.rows(panel)["root mode"].items, strict=True))
        assert cells["pedal"].label == "right"

    def test_a_pedal_bound_on_the_release_says_so(self, catalogue):
        data = {**PEDALLED, "modes": {**PEDALLED["modes"]}}
        data["modes"]["other mode"] = {
            "type": "vosk",
            "path": "m",
            "pedals": {"right": {"release": "mode(root mode)"}},
        }
        panel = render.panel_of(
            protocol.state_of(
                model.Snapshot(
                    layers=LayerState(voice="other mode", gesture="other mode", pedal="other mode")
                ),
                revision=1,
                chip=True,
                sheet=True,
                page="modes",
            ),
            catalogue(data),
        )
        cells = dict(zip(panel.columns, self.rows(panel)["root mode"].items, strict=True))

        assert cells["pedal"].label == "right released"

    def test_the_column_is_read_off_the_pedal_layer_and_not_the_voice_one(self, catalogue):
        with_voice = self.panel(catalogue, mode="other mode")
        assert "pedal" in with_voice.columns

        moved = self.panel(catalogue, mode="other mode", pedal_mode="scrolling")
        assert "pedal" not in moved.columns

    def test_a_template_is_not_a_row(self, catalogue):
        assert "scrolling" not in self.rows(self.panel(catalogue))

    def test_a_switch_that_moves_only_a_layer_is_not_a_transition_here(self, catalogue):
        panel = self.panel(catalogue)
        voice = dict(zip(panel.columns, self.rows(panel)["other mode"].items, strict=True))

        assert voice["voice"].label == '"other mode"'


class TestThePedalsPage:
    def panel(self, catalogue, data=None, pedal_mode="root mode"):
        frame = protocol.state_of(
            model.Snapshot(
                layers=LayerState(voice="root mode", gesture="root mode", pedal=pedal_mode)
            ),
            revision=1,
            chip=True,
            sheet=True,
            page="pedals",
        )
        return render.panel_of(frame, catalogue(data or PEDALLED))

    def rows(self, panel):
        return {row.label: row for row in panel.rows}

    def test_it_has_a_row_per_pedal_the_board_has(self, catalogue):
        assert list(self.rows(self.panel(catalogue))) == ["left", "middle", "right"]

    def test_both_edges_get_a_column(self, catalogue):
        assert self.panel(catalogue).columns == ("press", "release")

    def test_a_pedal_bound_on_both_edges_shows_both(self, catalogue):
        row = self.rows(self.panel(catalogue))["left"]

        assert [item.label for item in row.items] == ["hold(left_mouse)", "release(left_mouse)"]

    def test_a_bare_response_leaves_the_release_a_drawn_gap(self, catalogue):
        row = self.rows(self.panel(catalogue))["middle"]

        assert row.items[0].label == "enter"
        assert row.items[1].label == render.MISSING
        assert row.items[1].dim

    def test_a_pedal_this_layout_does_not_use_is_still_a_row(self, catalogue):
        row = self.rows(self.panel(catalogue))["right"]

        assert [item.label for item in row.items] == [render.MISSING, render.MISSING]

    def test_the_title_names_the_layout_the_pedals_are_on(self, catalogue):
        assert "scrolling" in self.panel(catalogue, pedal_mode="scrolling").title

    def test_it_shows_the_layout_the_pedal_layer_is_on_not_the_voice_one(self, catalogue):
        row = self.rows(self.panel(catalogue, pedal_mode="scrolling"))["right"]

        assert row.items[0].label == "down"

    def test_the_footer_says_what_moves_the_pedals(self, catalogue):
        footer = self.panel(catalogue, pedal_mode="scrolling").footer

        assert '"feet scroll" → scrolling' in footer

    def test_the_footer_offers_the_ways_off_the_page(self, catalogue):
        footer = self.panel(catalogue).footer

        assert f'"overlay open" → {render.TO_INDEX}' in footer
        assert f'"overlay close" → {render.TO_HIDE}' in footer

    def test_with_no_board_the_page_says_the_setting_that_turns_it_on(self, catalogue):
        panel = self.panel(catalogue, data=CONFIG)

        assert panel.rows == ()
        assert panel.hint == render.NO_PEDALS

    def test_no_other_page_offers_it_when_there_is_no_board(self, catalogue):
        footer = render.panel_of(state(sheet=True), catalogue(CONFIG)).footer

        assert not any(line.split(" → ")[-1] == render.TO_PEDALS for line in footer)

    def test_it_is_offered_from_the_index_when_there_is_one(self, catalogue):
        footer = render.panel_of(state(sheet=True), catalogue(PEDALLED)).footer

        assert f'"overlay pedals" → {render.TO_PEDALS}' in footer


class TestTheCalibrationTarget:
    """What the fullscreen dot surface is told to draw."""

    def dot(self, **kwargs):
        fields = {"x": 0.08, "y": 0.92, "index": 3, "total": 9, "settled": 0.4}
        return State(revision=1, dot=CalibrationDot(**{**fields, **kwargs}))

    def test_no_calibration_means_nothing_to_draw(self):
        assert render.target_of(State(revision=1)) is None
        assert render.target_of(None) is None

    def test_the_dot_keeps_its_normalised_position(self):
        target = render.target_of(self.dot())
        assert (target.x, target.y) == (0.08, 0.92)

    def test_the_count_says_where_in_the_session_it_is(self):
        assert render.target_of(self.dot()).caption == "3 of 9"

    def test_the_dwell_is_carried_because_it_is_the_only_feedback(self):
        assert render.target_of(self.dot(settled=0.62)).settled == pytest.approx(0.62)

    @pytest.mark.parametrize("settled,expected", [(-0.5, 0.0), (0.0, 0.0), (2.0, 1.0)])
    def test_the_ring_cannot_be_asked_to_overfill(self, settled, expected):
        assert render.target_of(self.dot(settled=settled)).settled == expected

    def test_the_hint_names_the_nose_and_not_the_eyes(self):
        assert "nose" in render.target_of(self.dot()).hint

import pytest

from libre_dictum.overlay import model
from libre_dictum.overlay.model import MISC, ModeView, RouteKind, Sheet
from libre_dictum.pattern import CommandPattern
from libre_dictum.settings import Command, EdgeBinding, ModeKind, ModeSettings, Origin

DIRECTIONS = ("left", "right", "up", "down", "display")
CALL_SIGNS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")


def command(phrase, response="a", group=None, source="config.json"):
    return Command(
        pattern=CommandPattern.compile(phrase),
        response=response,
        origin=Origin(group=group, source=source) if group else None,
    )


def family(first_word, tails, **kwargs):
    return [command(f"{first_word} {tail}", **kwargs) for tail in tails]


def mode(name="command mode", commands=(), gestures=None, aliases=None):
    return ModeSettings(
        name=name,
        kind=ModeKind.VOSK,
        commands=tuple(commands),
        gestures=dict(gestures or {}),
        aliases=dict(aliases or {}),
    )


def groups_by_name(commands, mode_name="command mode"):
    return {group.name: group for group in model.group_commands(commands, mode_name=mode_name)}


class TestGroupingByOrigin:
    def test_an_imported_command_keeps_the_name_its_template_was_given(self):
        commands = [command("step up", group="::navigation")]
        assert "::navigation" in groups_by_name(commands)

    def test_a_template_stays_whole_rather_than_splitting_by_first_word(self):
        commands = family("step", ["up", "down"], group="::navigation") + family(
            "page", ["up", "down"], group="::navigation"
        )
        assert list(groups_by_name(commands)) == ["::navigation"]

    def test_a_group_records_the_file_to_edit(self):
        commands = [command("step up", group="::navigation", source="groups/navigation.json")]
        assert groups_by_name(commands)["::navigation"].source == "groups/navigation.json"

    def test_a_command_declared_in_the_mode_itself_counts_as_inline(self):
        commands = family("buffer", ["save", "close"], group="command mode")
        assert list(groups_by_name(commands)) == ["buffer"]

    def test_a_command_with_no_origin_at_all_counts_as_inline(self):
        assert list(groups_by_name(family("buffer", ["save", "close"]))) == ["buffer"]

    def test_an_inline_family_records_the_file_the_mode_was_declared_in(self):
        commands = family(
            "buffer", ["save", "close"], group="command mode", source="modes/cmd.json"
        )
        assert groups_by_name(commands)["buffer"].source == "modes/cmd.json"


class TestGroupingByFirstWord:
    def test_commands_sharing_a_first_word_become_a_family_named_after_it(self):
        found = groups_by_name(family("buffer", ["left", "right", "save"]))
        assert found["buffer"].count == 3

    def test_a_first_word_used_once_goes_to_misc(self):
        commands = [command("grab all"), command("input date"), *family("zoom", ["in", "out"])]
        found = groups_by_name(commands)
        assert found[MISC].count == 2
        assert found["zoom"].count == 2

    def test_misc_is_absent_when_every_first_word_is_shared(self):
        assert MISC not in groups_by_name(family("zoom", ["in", "out"]))

    def test_families_are_listed_largest_first_then_alphabetically(self):
        commands = [
            *family("zoom", ["in", "out"]),
            *family("buffer", ["left", "right", "save"]),
            *family("alpha", ["one", "two"]),
        ]
        assert list(groups_by_name(commands)) == ["buffer", "alpha", "zoom"]

    def test_templates_come_before_families_and_misc_comes_last(self):
        commands = [
            command("grab all"),
            *family("buffer", ["left", "right"]),
            command("step up", group="::navigation"),
        ]
        assert list(groups_by_name(commands)) == ["::navigation", "buffer", MISC]


class TestAlignedTables:
    def test_two_families_over_the_same_tails_become_one_table(self):
        commands = family("focus", DIRECTIONS) + family("transit", DIRECTIONS)
        table = groups_by_name(commands)["focus/transit"].table
        assert table is not None
        assert table.aligned
        assert table.row_labels == ("focus", "transit")
        assert table.column_labels == DIRECTIONS

    def test_a_family_covering_part_of_the_axis_joins_and_leaves_the_gaps(self):
        commands = (
            family("vector", CALL_SIGNS)
            + family("dispatch", CALL_SIGNS)
            + family("link", CALL_SIGNS[:3])
        )
        table = groups_by_name(commands)["vector/dispatch/link"].table
        assert table is not None
        assert [cell is None for cell in table.cells[2]] == [False, False, False, True, True, True]

    def test_a_family_that_would_add_a_column_stays_its_own_group(self):
        commands = family("focus", DIRECTIONS) + family(
            "buffer", ["left", "right", "up", "down", "open", "save", "close"]
        )
        assert set(groups_by_name(commands)) == {"focus", "buffer"}

    def test_a_merge_that_makes_no_table_is_undone(self):
        commands = family("buffer", ["left", "right", "up", "down", "open", "save", "close"])
        commands += family("browser", ["left", "right"])
        found = groups_by_name(commands)
        assert set(found) == {"buffer", "browser"}
        assert found["buffer"].table is None

    def test_the_result_does_not_depend_on_the_order_the_config_declares_them(self):
        wide = family("vector", CALL_SIGNS)
        narrow = family("link", CALL_SIGNS[:3])
        assert list(groups_by_name(wide + narrow)) == list(groups_by_name(narrow + wide))

    def test_a_narrow_family_declared_first_still_joins_the_wide_one(self):
        commands = (
            family("link", CALL_SIGNS[:3])
            + family("vector", CALL_SIGNS)
            + family("dispatch", CALL_SIGNS)
        )
        assert list(groups_by_name(commands)) == ["vector/dispatch/link"]

    def test_families_that_cannot_share_an_axis_stay_apart_whichever_came_first(self):
        wide = family("buffer", ["left", "right", "up", "down", "open", "save", "close"])
        narrow = family("focus", DIRECTIONS)
        assert set(groups_by_name(wide + narrow)) == set(groups_by_name(narrow + wide))

    def test_a_family_of_one_never_joins_a_table(self):
        commands = family("focus", DIRECTIONS) + [command("transit left")]
        assert "transit" not in groups_by_name(commands)
        assert groups_by_name(commands)[MISC].count == 1


class TestRaggedTables:
    def test_rows_that_share_no_tails_still_become_a_table(self):
        commands = (
            family("type", ["a", "b", "c", "d"], group="::alphabet")
            + family("press", ["e", "f", "g", "hotel"], group="::alphabet")
            + family("key", ["i", "j", "k", "l"], group="::alphabet")
        )
        table = groups_by_name(commands)["::alphabet"].table
        assert table is not None
        assert not table.aligned
        assert table.row_labels == ("type", "press", "key")
        assert [entry.tail for entry in table.cells[1] if entry] == ["e", "f", "g", "hotel"]

    def test_a_group_of_mostly_lonely_first_words_is_a_list(self):
        commands = [
            command(f"{word} thing", group="::misc") for word in ("one", "two", "three", "four")
        ]
        assert groups_by_name(commands)["::misc"].table is None

    def test_too_few_rows_is_a_list(self):
        commands = family("type", ["a", "b"], group="::alphabet") + family(
            "press", ["c", "d"], group="::alphabet"
        )
        assert groups_by_name(commands)["::alphabet"].table is None

    def test_one_first_word_is_always_a_list(self):
        commands = family("buffer", ["left", "right", "up", "down", "save"])
        assert groups_by_name(commands)["buffer"].table is None


class TestNavigationPhrases:
    def test_a_template_is_opened_by_its_name_without_the_colons(self):
        commands = [command("step up", group="::navigation")]
        assert groups_by_name(commands)["::navigation"].phrases == ("open navigation",)

    def test_a_two_word_template_name_survives(self):
        commands = [command("send escape", group="::control keys")]
        assert groups_by_name(commands)["::control keys"].phrases == ("open control keys",)

    def test_an_underscore_becomes_a_word_break(self):
        commands = [command("send escape", group="::control_keys")]
        assert groups_by_name(commands)["::control_keys"].phrases == ("open control keys",)

    def test_every_name_of_a_merged_table_opens_it(self):
        commands = family("focus", DIRECTIONS) + family("transit", DIRECTIONS)
        group = groups_by_name(commands)["focus/transit"]
        assert group.phrases == ("open focus", "open transit")
        assert group.answers_to("open transit")

    def test_the_prefix_is_configurable(self):
        commands = [command("step up", group="::navigation")]
        groups = model.group_commands(commands, mode_name="command mode", navigate="show")
        assert groups[0].phrases == ("show navigation",)

    def test_a_group_nobody_can_pronounce_is_shown_but_not_navigable(self):
        commands = family("{numeric}", ["one", "two"])
        group = groups_by_name(commands)["{numeric}"]
        assert group.count == 2
        assert not group.navigable

    @pytest.mark.parametrize("name", ["::alphabet", "buffer", MISC])
    def test_a_view_offers_every_phrase_to_the_recognizer(self, name):
        commands = [
            command("step up", group="::alphabet"),
            *family("buffer", ["a", "b"]),
            command("grab all"),
        ]
        view = model.build_view(mode(commands=commands), modes=["command mode"])
        assert f"open {name.removeprefix('::')}" in view.phrases

    def test_a_phrase_opens_the_group_it_names(self):
        commands = [command("step up", group="::navigation")]
        view = model.build_view(mode(commands=commands), modes=["command mode"])
        found = view.group_named("open navigation")
        assert found is not None and found.name == "::navigation"


class TestAModeThatCannotBeNavigated:
    def test_a_dictation_mode_offers_no_phrases(self):
        view = model.build_view(mode(commands=[command("{rest}", "type({1})")]), modes=["m"])
        assert not view.navigable
        assert view.phrases == ()

    def test_its_groups_agree_that_they_cannot_be_opened(self):
        view = model.build_view(mode(commands=[command("{rest}", "type({1})")]), modes=["m"])
        assert not any(group.navigable for group in view.groups)

    def test_its_commands_are_still_grouped_and_shown(self):
        view = model.build_view(mode(commands=[command("{rest}", "type({1})")]), modes=["m"])
        assert sum(group.count for group in view.groups) == 1


class TestCollisions:
    def test_a_generated_phrase_that_is_already_a_command_is_reported(self):
        commands = [command("open navigation"), command("step up", group="::navigation")]
        session = mode(commands=commands)
        view = model.build_view(session, modes=["command mode"])
        assert model.colliding_phrases(view, session, []) == ["open navigation"]

    def test_a_generated_phrase_that_is_a_reserved_phrase_is_reported(self):
        commands = [command("step up", group="::navigation")]
        session = mode(commands=commands)
        view = model.build_view(session, modes=["command mode"])
        assert model.colliding_phrases(view, session, ["open navigation"]) == ["open navigation"]

    def test_no_collision_is_the_normal_case(self):
        commands = [command("buffer open"), command("step up", group="::navigation")]
        session = mode(commands=commands)
        view = model.build_view(session, modes=["command mode"])
        assert model.colliding_phrases(view, session, ["panic"]) == []


class TestRoutes:
    def test_every_other_mode_is_reachable_by_saying_its_name(self):
        view = model.build_view(mode(), modes=["command mode", "mouse mode", "dictate mode"])
        by_name = [r.target for r in view.routes if r.kind is RouteKind.NAME]
        assert by_name == ["mouse mode", "dictate mode"]

    def test_a_gesture_that_switches_is_a_route(self):
        session = mode(gestures={"smile": EdgeBinding(press="mode(mouse mode)")})
        view = model.build_view(session, modes=["command mode", "mouse mode"])
        gestures = [r for r in view.routes if r.kind is RouteKind.GESTURE]
        assert [(r.by, r.target) for r in gestures] == [("smile", "mouse mode")]

    def test_a_gesture_bound_on_the_release_says_so(self):
        session = mode(
            gestures={"fist": EdgeBinding(release="mode(command mode)")},
        )
        view = model.build_view(session, modes=["command mode", "mouse mode"])
        gestures = [r for r in view.routes if r.kind is RouteKind.GESTURE]

        assert [(r.by, r.target) for r in gestures] == [("fist released", "command mode")]

    def test_a_command_that_switches_is_a_route(self):
        session = mode(commands=[command("go dictate", "mode(dictate mode)")])
        view = model.build_view(session, modes=["command mode", "dictate mode"])
        commands = [r for r in view.routes if r.kind is RouteKind.COMMAND]
        assert [(r.by, r.target) for r in commands] == [("go dictate", "dictate mode")]

    def test_a_switch_reachable_only_through_an_alias_is_found(self):
        session = mode(
            commands=[command("go dictate", "DICTATE")], aliases={"DICTATE": "mode(dictate mode)"}
        )
        view = model.build_view(session, modes=["command mode", "dictate mode"])
        assert any(r.kind is RouteKind.COMMAND for r in view.routes)

    def test_a_switch_to_a_placeholder_is_no_route_anywhere(self):
        session = mode(commands=[command("go {any}", "mode({1})")])
        view = model.build_view(session, modes=["command mode"])
        assert view.routes == ()

    def test_the_previous_mode_keyword_is_carried_rather_than_guessed(self):
        view = model.build_view(mode(), modes=["command mode"], previous_keyword="previous mode")
        assert view.previous_keyword == "previous mode"


class TestNearestPattern:
    def test_a_word_out_reports_the_pattern_it_nearly_was(self):
        commands = [command("buffer save"), command("zoom in")]
        assert model.nearest_pattern("buffer safe", commands) == "buffer save"

    def test_nothing_like_anything_reports_nothing(self):
        assert model.nearest_pattern("elephant", [command("buffer save")]) is None

    def test_an_empty_command_table_reports_nothing(self):
        assert model.nearest_pattern("buffer save", []) is None

    def test_the_pattern_is_reported_as_the_author_wrote_it(self):
        assert model.nearest_pattern("buffer save", [command("Buffer  Save")]) == "Buffer  Save"


class TestTheSheet:
    @pytest.fixture
    def view(self):
        commands = [command("step up", group="::navigation"), *family("buffer", ["a", "b"])]
        return model.build_view(mode(commands=commands), modes=["command mode"])

    def test_it_starts_closed(self):
        assert not Sheet().visible

    def test_opening_it_with_no_group_shows_the_index(self):
        sheet = Sheet()
        sheet.open()
        assert sheet.visible and sheet.group is None

    def test_toggling_closes_an_open_sheet(self):
        sheet = Sheet(open_at_start=True)
        sheet.toggle()
        assert not sheet.visible

    def test_closing_forgets_where_it_was(self):
        sheet = Sheet()
        sheet.open("buffer")
        sheet.close()
        assert sheet.group is None

    def test_a_mode_switch_keeps_a_group_the_new_mode_also_has(self):
        sheet = Sheet()
        sheet.open("::navigation")
        sheet.follow(
            model.build_view(
                mode(name="symbol mode", commands=[command("step up", group="::navigation")]),
                modes=["symbol mode"],
            )
        )
        assert sheet.group == "::navigation"

    def test_a_mode_switch_falls_back_to_the_index_when_the_group_is_gone(self):
        sheet = Sheet()
        sheet.open("buffer")
        sheet.follow(ModeView(mode="dictate mode"))
        assert sheet.visible and sheet.group is None

    def test_a_group_reached_by_any_of_its_names_survives_the_switch(self, view):
        commands = family("focus", DIRECTIONS) + family("transit", DIRECTIONS)
        sheet = Sheet()
        sheet.open("transit")
        sheet.follow(model.build_view(mode(commands=commands), modes=["command mode"]))
        assert sheet.group == "transit"

import json
import re
from pathlib import Path

import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.overlay import model

SPECS = Path(__file__).parents[2] / "specs" / "discoverability"
FEATURE = SPECS / "command-groups.feature"
SURFACES = SPECS / "display-surfaces.feature"
NAVIGATION = SPECS / "navigating-by-voice.feature"


def fragments(feature: Path = FEATURE) -> dict[str, dict]:
    """Scenario name -> the first configuration fragment that scenario shows."""
    text = feature.read_text()
    found = {}
    for chunk in re.split(r"^\s*(?:@[\w-]+\s+)*Scenario: ", text, flags=re.MULTILINE)[1:]:
        name, _, body = chunk.partition("\n")
        block = re.search(r'"""\n(.*?)\n\s*"""', body, re.DOTALL)
        if block:
            found[name.strip()] = json.loads(block.group(1))
    return found


FRAGMENTS = fragments()
SURFACE_FRAGMENTS = fragments(SURFACES)


def rejections(feature: Path) -> set[str]:
    """Scenarios that claim their own configuration does not load."""
    text = feature.read_text()
    found = set()
    for chunk in re.split(r"^\s*(?:@[\w-]+\s+)*Scenario: ", text, flags=re.MULTILINE)[1:]:
        name, _, body = chunk.partition("\n")
        if "loading fails" in body:
            found.add(name.strip())
    return found


NAVIGATION_FRAGMENTS = fragments(NAVIGATION)
NAVIGATION_REJECTED = rejections(NAVIGATION)


@pytest.fixture
def view(settings_of):
    def build(scenario, mode="root mode"):
        settings = settings_of(FRAGMENTS[scenario])
        return model.build_view(
            settings.modes[mode],
            modes=list(settings.modes),
            previous_keyword=settings.previous_mode_keyword,
        )

    return build


def names(view):
    return [group.name for group in view.groups]


def test_the_feature_file_shows_a_fragment_for_every_scenario():
    scenarios = re.findall(r"^\s*Scenario: (.+)$", FEATURE.read_text(), flags=re.MULTILINE)
    assert len(FRAGMENTS) == len(scenarios)


@pytest.mark.parametrize("scenario", list(FRAGMENTS))
def test_every_fragment_loads_and_the_model_survives_it(scenario, view):
    assert view(scenario).mode == "root mode"


@pytest.mark.parametrize("scenario", list(SURFACE_FRAGMENTS))
def test_every_fragment_in_the_surfaces_feature_loads_too(scenario, settings_of):
    """display-surfaces.feature shows fewer configurations, but the ones it shows load."""
    assert settings_of(SURFACE_FRAGMENTS[scenario]).modes


@pytest.mark.parametrize("scenario", list(NAVIGATION_FRAGMENTS))
def test_the_navigation_feature_loads_or_refuses_exactly_as_it_says(scenario, settings_of):
    """Half of navigating-by-voice.feature's fragments are meant to be refused."""
    fragment = NAVIGATION_FRAGMENTS[scenario]
    if scenario in NAVIGATION_REJECTED:
        with pytest.raises(ConfigError):
            model.check_display(settings_of(fragment))
        return
    settings = settings_of(fragment)
    model.check_display(settings)
    assert settings.modes


class TestTheGroupingClaims:
    def test_a_template_becomes_a_group(self, view):
        found = view("A template becomes a group")
        assert "::navigation" in names(found)
        assert found.group_called("::navigation").count == 2

    def test_a_template_is_not_broken_up_by_the_first_word_rule(self, view):
        found = view("A template is not broken up by the first-word rule")
        assert names(found) == ["::navigation"]

    def test_a_shared_first_word_becomes_a_family(self, view):
        found = view("A shared first word becomes a family named after it")
        assert found.group_called("buffer").count == 3

    def test_a_first_word_used_once_lands_in_misc(self, view):
        found = view("A first word used once is not a family of one")
        assert found.group_called("zoom").count == 2
        assert found.group_called(model.MISC).count == 1


class TestTheTableClaims:
    def test_two_families_over_the_same_tails_become_one_table(self, view):
        found = view("Two families over the same tails become one table")
        table = found.group_called("focus/transit").table
        assert table is not None
        assert table.row_labels == ("focus", "transit")
        assert table.column_labels == ("left", "right")

    def test_the_gaps_of_a_partly_covering_family_are_kept(self, view):
        found = view("A family covering part of the axis joins, and the gaps are kept")
        group = found.group_called("vector/dispatch/link")
        assert group.table is not None
        assert group.table.row_labels == ("vector", "dispatch", "link")
        charlie = group.table.column_labels.index("charlie")
        link = group.table.row_labels.index("link")
        assert group.table.cells[link][charlie] is None

    def test_families_sharing_a_word_but_not_an_axis_stay_apart(self, view):
        found = view("Families that share a word but not an axis stay apart")
        assert set(names(found)) == {"buffer", "focus"}

    def test_rows_that_share_no_columns_are_still_a_table(self, view):
        found = view("Rows that share no columns are still a table")
        table = found.group_called("::alphabet").table
        assert table is not None
        assert not table.aligned
        assert table.row_labels == ("type", "press", "key")

    def test_one_first_word_is_a_list(self, view):
        assert view("One first word is a list, not a table").group_called("buffer").table is None


class TestThePhraseClaims:
    def test_a_template_is_opened_by_its_name_without_the_colons(self, view):
        group = view("A template is opened by its name without the colons")
        assert group.group_called("::control keys").phrases == ("open control keys",)

    def test_either_name_of_a_merged_table_opens_it(self, view):
        group = view("Either name of a merged table opens it").group_called("focus/transit")
        assert group.answers_to("open focus")
        assert group.answers_to("open transit")

    def test_a_group_nobody_can_pronounce_is_shown_and_says_so(self, view):
        found = view("A group nobody can pronounce is shown, and says it cannot be opened")
        group = found.group_called("{numeric}")
        assert group.count == 2
        assert not group.navigable

    def test_a_dictation_mode_cannot_be_navigated(self, view):
        found = view("A dictation mode says outright that it cannot be navigated")
        assert not found.navigable
        assert found.phrases == ()


class TestTheRouteClaims:
    def test_a_gesture_that_switches_mode_is_shown(self, view):
        found = view("A gesture that switches mode is shown")
        gestures = [r for r in found.routes if r.kind is model.RouteKind.GESTURE]
        assert [(r.by, r.target) for r in gestures] == [("smile", "other mode")]

    def test_a_switch_through_an_alias_is_shown(self, view):
        found = view("A mode switch reachable only through an alias is still shown")
        commands = [r for r in found.routes if r.kind is model.RouteKind.COMMAND]
        assert [(r.by, r.target) for r in commands] == [("go over there", "other mode")]

    def test_a_switch_to_a_placeholder_is_no_route(self, view):
        found = view("A switch to a placeholder is no route anywhere")
        assert all(r.kind is model.RouteKind.NAME for r in found.routes)

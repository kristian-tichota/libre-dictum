from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from libre_dictum.overlay.hud import compositor
from libre_dictum.overlay.hud.compositor import Placement, Session, Trouble

ROOT = Path(__file__).parents[2]

KDE_WAYLAND = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}


def outcome(session: Session, **facts) -> compositor.Outcome:
    """outcome_of with the everything-worked answer as the baseline."""
    return compositor.outcome_of(
        session,
        **{
            "library": "libgtk4-layer-shell.so.0",
            "binding": True,
            "supported": True,
            "applied": True,
            **facts,
        },
    )


class TestReadingTheSession:
    def test_wayland_display_is_what_makes_it_a_wayland_session(self):
        assert Session.from_env({"WAYLAND_DISPLAY": "wayland-0"}).wayland

    def test_a_session_type_alone_is_enough(self):
        assert Session.from_env({"XDG_SESSION_TYPE": "wayland"}).wayland

    def test_an_empty_environment_is_not_wayland(self):
        assert not Session.from_env({}).wayland

    @pytest.mark.parametrize("current", ["KDE", "kde", "KDE:X-Generic", "X-Generic:KDE"])
    def test_kde_is_recognised_wherever_and_however_it_is_spelled(self, current):
        assert Session.from_env({"XDG_CURRENT_DESKTOP": current}).kde

    def test_another_desktop_is_not_kde(self):
        assert not Session.from_env({"XDG_CURRENT_DESKTOP": "GNOME"}).kde

    def test_it_says_where_it_is_in_one_word_for_a_log_line(self):
        assert Session.from_env(KDE_WAYLAND).where == "wayland/KDE"
        assert Session.from_env({}).where == "x11/unknown"


class TestWhichLibraryToTry:
    def test_the_versioned_soname_comes_first(self):
        assert compositor.libraries({})[0] == "libgtk4-layer-shell.so.0"

    def test_an_override_is_tried_before_the_guesses(self):
        tried = compositor.libraries({compositor.LIBRARY_ENV: "/opt/lib/libgtk4-layer-shell.so"})
        assert tried[0] == "/opt/lib/libgtk4-layer-shell.so"
        assert tried[1:] == compositor.LIBRARIES

    def test_a_blank_override_is_not_a_candidate(self):
        assert compositor.libraries({compositor.LIBRARY_ENV: "   "}) == compositor.LIBRARIES


class TestTheDiagnosis:
    """Four ways to miss, told apart -- because they have four different fixes."""

    def test_a_layer_surface_is_the_answer_and_nothing_is_wrong(self):
        got = outcome(Session.from_env(KDE_WAYLAND), version=4)
        assert got.placement is Placement.LAYER
        assert got.trouble is Trouble.NONE
        assert not got.degraded

    def test_x11_is_diagnosed_before_anything_that_can_be_installed(self):
        got = outcome(Session.from_env({}), applied=False)
        assert got.trouble is Trouble.NOT_WAYLAND

    def test_a_missing_library_is_not_blamed_on_the_compositor(self):
        got = outcome(Session.from_env(KDE_WAYLAND), library=None, supported=False, applied=False)
        assert got.trouble is Trouble.NO_LIBRARY

    def test_a_library_without_its_typelib_is_not_a_missing_protocol(self):
        got = outcome(Session.from_env(KDE_WAYLAND), binding=False, supported=False, applied=False)
        assert got.trouble is Trouble.NO_BINDING

    def test_a_library_the_compositor_will_not_talk_to_is_its_own_case(self):
        got = outcome(Session.from_env(KDE_WAYLAND), supported=False, applied=False)
        assert got.trouble is Trouble.NO_PROTOCOL

    def test_everything_present_and_the_window_still_plain_is_the_load_order(self):
        got = outcome(Session.from_env(KDE_WAYLAND), applied=False)
        assert got.trouble is Trouble.NOT_APPLIED

    def test_an_applied_layer_surface_outranks_every_other_fact(self):
        got = outcome(Session.from_env({}), library=None, supported=False, applied=True)
        assert got.placement is Placement.LAYER

    @pytest.mark.parametrize("trouble", [t for t in Trouble if t is not Trouble.NONE])
    def test_every_trouble_is_degraded(self, trouble):
        assert compositor.Outcome(Placement.PLAIN, trouble).degraded


class TestWhatItSays:
    """The message is the feature: a silent failure is the one this cannot repeat."""

    def messages(self, session: Session) -> dict[Trouble, str]:
        troubles = [one for one in Trouble if one is not Trouble.NONE]
        return {
            trouble: compositor.explain(
                session, compositor.Outcome(Placement.PLAIN, trouble, "lib.so")
            )
            for trouble in troubles
        }

    def test_every_failure_says_what_it_costs_in_words_not_jargon(self):
        for trouble, said in self.messages(Session.from_env(KDE_WAYLAND)).items():
            assert "virtual desktop" in said, trouble
            assert "cover" in said, trouble
            assert "click" in said, trouble

    def test_every_failure_ends_in_something_to_do(self):
        for trouble, said in self.messages(Session.from_env(KDE_WAYLAND)).items():
            assert any(word in said for word in ("Install", "Log in", "Run", "Use")), trouble

    def test_a_missing_library_names_the_package_for_every_distribution(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND), compositor.Outcome(Placement.PLAIN, Trouble.NO_LIBRARY)
        )
        assert "gui-libs/gtk4-layer-shell" in said, "no Gentoo package named"
        assert compositor.LIBRARY_ENV in said, "no way out for a library somewhere else"
        for name in compositor.LIBRARIES:
            assert name in said, f"does not say it tried {name}"

    def test_a_missing_typelib_names_the_use_flag_and_the_gir_package(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND),
            compositor.Outcome(Placement.PLAIN, Trouble.NO_BINDING, "libgtk4-layer-shell.so.0"),
        )
        assert "USE=introspection" in said, "does not name the flag that is off by default"
        assert "gir1.2-gtk4layershell-1.0" in said, "does not name the Debian package"
        assert "GI_TYPELIB_PATH" in said, "no way out for a typelib somewhere else"
        assert "LD_PRELOAD" not in said, "sends the user after the wrong thing entirely"

    def test_the_first_install_attempt_is_told_everything_it_needs(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND), compositor.Outcome(Placement.PLAIN, Trouble.NO_LIBRARY)
        )
        assert "USE=introspection" in said
        assert "gir1.2-gtk4layershell-1.0" in said

    def test_the_load_order_failure_names_the_preload_that_proves_it(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND),
            compositor.Outcome(Placement.PLAIN, Trouble.NOT_APPLIED, "libgtk4-layer-shell.so.0"),
        )
        assert "LD_PRELOAD=libgtk4-layer-shell.so.0" in said
        assert "libwayland-client" in said

    def test_on_kde_a_silent_protocol_is_the_load_order_and_not_the_compositor(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND),
            compositor.Outcome(Placement.PLAIN, Trouble.NO_PROTOCOL, "lib.so"),
        )
        assert "LD_PRELOAD" in said
        assert "compositor that implements it" not in said

    def test_off_kde_a_silent_protocol_may_really_be_the_compositor(self):
        said = compositor.explain(
            Session.from_env({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"}),
            compositor.Outcome(Placement.PLAIN, Trouble.NO_PROTOCOL, "lib.so"),
        )
        assert "no zwlr_layer_shell_v1" in said

    def test_on_kde_every_failure_offers_the_way_back_by_hand(self):
        for trouble, said in self.messages(Session.from_env(KDE_WAYLAND)).items():
            assert compositor.RULE_FILE in said, trouble
            assert compositor.APP_ID in said, trouble

    def test_off_kde_it_does_not_offer_a_kde_dialog(self):
        said = self.messages(Session.from_env({"WAYLAND_DISPLAY": "wayland-0"}))
        assert all("System Settings" not in one for one in said.values())

    def test_success_says_the_three_properties_it_bought(self):
        said = compositor.explain(
            Session.from_env(KDE_WAYLAND),
            compositor.Outcome(Placement.LAYER, Trouble.NONE, "lib.so", 4),
        )
        assert "v4" in said
        assert "every virtual desktop" in said
        assert "click-through" in said


class TestConfiningItToOneOutput:
    """One connector name in, and either an output to pin to or an empty screen."""

    PRESENT = ("DP-2", "DP-1", "HDMI-A-1")

    def test_the_named_output_is_the_one_it_pins_to(self):
        chosen = compositor.output_for(self.PRESENT, name="DP-1", only=True)

        assert chosen == compositor.Output("DP-1", True)

    def test_naming_none_leaves_the_choice_to_the_compositor(self):
        chosen = compositor.output_for(self.PRESENT, name="", only=True)

        assert chosen.connector is None and chosen.visible

    def test_an_absent_output_draws_nothing(self):
        chosen = compositor.output_for(("DP-2",), name="DP-1", only=True)

        assert not chosen.visible and chosen.connector is None

    def test_unless_the_restriction_is_only_a_preference(self):
        chosen = compositor.output_for(("DP-2",), name="DP-1", only=False)

        assert chosen.visible and chosen.connector is None

    def test_a_session_with_no_output_at_all_is_not_a_special_case(self):
        assert not compositor.output_for((), name="DP-1", only=True).visible

    def test_the_absence_is_explained_with_what_is_there_and_how_to_list_it(self):
        said = compositor.explain_absent(("DP-2",), name="DP-1", only=True)

        assert "'DP-1'" in said and "DP-2" in said
        assert "--outputs" in said
        assert "nothing is drawn" in said

    def test_an_ordinary_window_says_the_restriction_cannot_be_applied(self):
        said = compositor.explain_unpinnable("DP-1")

        assert "'DP-1'" in said and "layer-shell" in said

    def test_a_preference_says_the_compositor_places_them_instead(self):
        said = compositor.explain_absent(("DP-2",), name="DP-1", only=False)

        assert "nothing is drawn" not in said
        assert "compositor places" in said


class TestTheFallbackRule:
    """The rule file is only useful if it matches the window it is written for."""

    def rule(self) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        read = parser.read(ROOT / compositor.RULE_FILE)
        assert read, f"{compositor.RULE_FILE} is named in the message and does not exist"
        return parser[parser.sections()[0]]

    def test_it_matches_the_app_id_the_display_actually_sets(self):
        assert self.rule()["wmclass"] == compositor.APP_ID

    def test_it_forces_all_desktops_and_keep_above(self):
        rule = self.rule()
        assert rule["desktopsrule"] == "2" and rule["desktops"] == ""
        assert rule["aboverule"] == "2" and rule["above"] == "true"

    def test_it_carries_a_description_or_kwin_drops_it_on_import(self):
        assert self.rule()["description"].strip()

    def test_the_app_id_is_the_name_of_the_program(self):
        import tomllib

        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        assert compositor.APP_ID in scripts, "the app id is not an entry point's name"

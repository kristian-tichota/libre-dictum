from __future__ import annotations

import socket
import threading
import time

import pytest

from libre_dictum.errors import ProtocolError
from libre_dictum.health import Health
from libre_dictum.input.dsl import HUD_TARGETS
from libre_dictum.layers import ALL_LAYERS, Layer, LayerState, SleepView
from libre_dictum.meters import GestureMeter, HandReading, Reading
from libre_dictum.overlay import model, protocol
from libre_dictum.overlay.service import HudService
from libre_dictum.status import HeldInput
from tests.overlay.conftest import DEADLINE_SECONDS, BrokenSocket, WedgedSocket

PUSH_DEADLINE_SECONDS = 2.0

CONFIG = {
    "modes": {
        "::navigation": {"commands": {"step up": "up", "step down": "down"}},
        "root mode": {
            "type": "vosk",
            "path": "m",
            "icon": [1, 2, 3],
            "imports": ["::navigation"],
            "commands": {"buffer save": "ctrl + s", "buffer close": "ctrl + w"},
        },
        "other mode": {"type": "vosk", "path": "m", "imports": ["::navigation"]},
    }
}


@pytest.fixture
def views(settings_of):
    def build(data=None):
        settings = settings_of(data or CONFIG)
        return (
            [
                model.build_view(mode, modes=list(settings.modes))
                for mode in settings.modes.values()
            ],
            {name: mode.icon for name, mode in settings.modes.items()},
        )

    return build


@pytest.fixture
def service(socket_path, views):
    """A listening service, already carrying a configuration and an active mode."""
    running = HudService(socket_path)
    modes, colours = views()
    running.set_views(modes, colours=colours)
    running.set_layers(LayerState(voice="root mode"))
    running.show()
    yield running
    running.hide()


def wedge(running: HudService, replacement) -> None:
    """Replace the one connected display's socket, so its writer cannot make progress."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not running._clients and time.monotonic() < deadline:
        time.sleep(0.01)
    assert running._clients, "no display connected"
    running._clients[0]._connection = replacement


class TestWhatADisplayIsSent:
    def test_it_gets_the_catalogue_and_then_the_state(self, service, display):
        one = display()

        catalogue = one.catalogue()
        state = one.state()

        assert [mode.name for mode in catalogue.modes] == list(CONFIG["modes"])[1:]
        assert state.mode == "root mode"

    def test_the_state_names_the_catalogue_it_belongs_to(self, service, display):
        one = display()

        assert one.catalogue().revision == one.state().revision

    def test_the_mode_colours_come_with_the_catalogue(self, service, display):
        catalogue = display().catalogue()

        assert catalogue.mode("root mode").colour == (1, 2, 3)

    def test_the_prefix_the_phrases_were_built_from_comes_too(self, service, display):
        assert display().catalogue().navigate == model.DEFAULT_NAVIGATE

    def test_a_display_that_connects_later_still_gets_everything(self, service, display):
        first = display()
        first.catalogue()
        service.set_held(HeldInput(held=("shift",)))

        second = display()

        assert second.catalogue().revision >= 1
        assert [key.key for key in second.state().held] == ["shift"]

    def test_held_keys_reach_a_display(self, service, display):
        one = display()
        one.catalogue()

        service.set_held(HeldInput(held=("shift",), pending=("ctrl",)))
        state = one.state_where(lambda s: s.held)

        assert [key.key for key in state.held] == ["ctrl", "shift"]

    def test_releasing_them_reaches_it_too(self, service, display):
        one = display()
        service.set_held(HeldInput(held=("shift",)))
        one.state_where(lambda s: s.held)

        service.set_held(HeldInput())

        assert one.state_where(lambda s: not s.held).held == ()

    def test_a_fault_reaches_a_display_with_its_reason(self, service, display):
        one = display()

        service.set_health(Health(failed=(("mode 'root mode'", "model file vanished"),)))

        state = one.state_where(lambda s: s.fault is not None)
        assert state.fault == "mode 'root mode' (model file vanished)"

    def test_an_utterance_and_its_near_miss_reach_a_display(self, service, display):
        one = display()

        service.set_heard(model.Heard(text="buffer safe", nearest="buffer save"))

        state = one.state_where(lambda s: s.heard is not None)
        assert (state.heard, state.nearest) == ("buffer safe", "buffer save")
        assert state.missed

    def test_a_partial_reaches_a_display_without_clearing_the_utterance(self, service, display):
        one = display()
        service.set_heard(model.Heard(text="buffer save", matched="buffer save"))
        one.state_where(lambda s: s.heard is not None)

        service.set_partial("vector al")

        state = one.state_where(lambda s: s.partial is not None)
        assert (state.partial, state.heard) == ("vector al", "buffer save")

    def test_a_mode_switch_reaches_a_display(self, service, display):
        one = display()

        service.set_layers(LayerState(voice="other mode"))

        assert one.state_where(lambda s: s.mode == "other mode")

    def test_a_reload_sends_a_new_catalogue_before_the_next_state(self, service, display, views):
        one = display()
        first = one.catalogue().revision
        one.state()

        modes, colours = views()
        service.set_views(modes, colours=colours)

        catalogue = one.catalogue()
        assert catalogue.revision == first + 1
        assert one.state().revision == catalogue.revision


class TestTheSheetAcrossAModeSwitch:
    def test_a_group_the_new_mode_also_has_stays_open(self, service, display):
        one = display()
        service.open_sheet("::navigation")
        one.state_where(lambda s: s.group == "::navigation")

        service.set_layers(LayerState(voice="other mode"))

        state = one.state_where(lambda s: s.mode == "other mode")
        assert state.group == "::navigation"
        assert state.sheet

    def test_a_group_the_new_mode_does_not_have_falls_back_to_the_index(self, service, display):
        one = display()
        service.open_sheet("buffer")
        one.state_where(lambda s: s.group == "buffer")

        service.set_layers(LayerState(voice="other mode"))

        state = one.state_where(lambda s: s.mode == "other mode")
        assert state.group is None
        assert state.sheet, "the sheet stays open; it only loses its place"

    def test_the_sheet_starts_closed_and_the_chip_starts_open(self, service, display):
        one = display()
        one.catalogue()

        state = one.state()

        assert state.chip and not state.sheet

    def test_closing_it_forgets_where_it_was(self, service, display):
        one = display()
        service.open_sheet("buffer")
        one.state_where(lambda s: s.group == "buffer")

        service.close_sheet()

        state = one.state_where(lambda s: not s.sheet)
        assert state.group is None

    def test_the_chip_is_toggleable_on_its_own(self, service, display):
        one = display()

        service.toggle_chip()

        assert not one.state_where(lambda s: not s.chip).chip


class TestTheChipsStartupState:
    """chip.enabled has to obey two rules at once, and they pull apart."""

    def views_with(self, settings_of, **overlay):
        settings = settings_of({**CONFIG, "overlay": overlay} if overlay else CONFIG)
        return (
            [
                model.build_view(mode, modes=list(settings.modes))
                for mode in settings.modes.values()
            ],
            settings.overlay,
        )

    def test_the_first_configuration_sets_it(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of, chip={"enabled": False})

        running.set_views(views, overlay=overlay)

        assert not running.chip

    def test_changing_it_and_reloading_takes_effect(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of)
        running.set_views(views, overlay=overlay)
        assert running.chip

        views, overlay = self.views_with(settings_of, chip={"enabled": False})
        running.set_views(views, overlay=overlay)

        assert not running.chip

    def test_a_reload_that_did_not_touch_it_keeps_a_toggle(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of)
        running.set_views(views, overlay=overlay)
        running.toggle_chip()
        assert not running.chip

        running.set_views(views, overlay=overlay)

        assert not running.chip, "a reload undid a toggle the user had just made"


class TestTheOutputTheSurfacesMayUse:
    """A display cannot read config.json, so the output arrives with the configuration."""

    def running(self, socket_path, settings_of, **overlay):
        settings = settings_of({**CONFIG, "overlay": overlay} if overlay else CONFIG)
        service = HudService(socket_path)
        service.set_views(
            [
                model.build_view(mode, modes=list(settings.modes))
                for mode in settings.modes.values()
            ],
            overlay=settings.overlay,
        )
        service.show()
        return service

    def test_the_named_output_reaches_a_display(self, socket_path, settings_of, display):
        service = self.running(socket_path, settings_of, output={"name": "DP-1"})
        try:
            catalogue = display().catalogue()
        finally:
            service.hide()

        assert catalogue.output == "DP-1" and catalogue.output_only

    def test_so_does_a_restriction_that_is_only_a_preference(
        self, socket_path, settings_of, display
    ):
        service = self.running(socket_path, settings_of, output={"name": "DP-1", "only": False})
        try:
            catalogue = display().catalogue()
        finally:
            service.hide()

        assert catalogue.output == "DP-1" and not catalogue.output_only

    def test_naming_none_sends_no_output_rather_than_a_name_to_look_for(
        self, socket_path, settings_of, display
    ):
        service = self.running(socket_path, settings_of)
        try:
            catalogue = display().catalogue()
        finally:
            service.hide()

        assert catalogue.output == ""


class TestAFullSleepTakingTheDisplay:
    """overlay.sleep.hide, and the one thing it has to give back: the wake confirmation."""

    def running(self, socket_path, settings_of, **overlay):
        settings = settings_of({**CONFIG, "overlay": overlay} if overlay else CONFIG)
        service = HudService(socket_path)
        service.set_views(
            [
                model.build_view(mode, modes=list(settings.modes))
                for mode in settings.modes.values()
            ],
            overlay=settings.overlay,
        )
        service.set_layers(LayerState(voice="root mode"))
        return service

    def test_the_default_leaves_the_display_alone(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of)

        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        assert service.state().chip

    def test_everything_asleep_takes_the_chip(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})

        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        assert not service.state().chip

    def test_and_the_sheet_with_it(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})
        service.open_sheet("buffer")

        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        assert not service.state().sheet

    def test_one_mechanism_asleep_is_not_a_reason_to_hide_anything(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})

        service.set_sleep(SleepView(asleep=frozenset({Layer.GESTURE})))

        assert service.state().chip

    def test_a_confirmation_brings_the_chip_back(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})

        service.set_sleep(SleepView(asleep=ALL_LAYERS, prompt="wake? again to confirm"))

        state = service.state()
        assert state.chip and state.confirming == "wake? again to confirm"

    def test_a_refusal_brings_it_back_too(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})

        service.set_sleep(SleepView(asleep=ALL_LAYERS, notice="refused: only pedal wakes this"))

        assert service.state().chip

    def test_the_window_closing_with_nothing_confirmed_goes_dark_again(
        self, socket_path, settings_of
    ):
        service = self.running(socket_path, settings_of, sleep={"hide": True})
        service.set_sleep(SleepView(asleep=ALL_LAYERS, prompt="wake? again to confirm"))

        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        assert not service.state().chip

    def test_a_chip_the_user_had_switched_off_is_not_handed_back_by_a_prompt(
        self, socket_path, settings_of
    ):
        service = self.running(socket_path, settings_of, sleep={"hide": True})
        service.toggle_chip()

        service.set_sleep(SleepView(asleep=ALL_LAYERS, prompt="wake? again to confirm"))

        assert not service.state().chip

    def test_waking_gives_back_the_group_the_sheet_was_at(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})
        service.open_sheet("buffer")
        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        service.set_sleep(SleepView())

        state = service.state()
        assert state.sheet and state.group == "buffer"

    def test_nothing_is_measured_for_a_surface_nobody_can_see(self, socket_path, settings_of):
        service = self.running(socket_path, settings_of, sleep={"hide": True})
        service.open_gestures()
        assert service.wants_meters

        service.set_sleep(SleepView(asleep=ALL_LAYERS))

        assert not service.wants_meters


class TestTheLiveReadings:
    """Readings are gated on being looked at."""

    def meters(self, *names, score=0.5):
        return [
            GestureMeter(name=name, readings=(Reading("handFist", score, 0.8, None),))
            for name in names
        ]

    def test_a_held_gesture_reaches_a_display(self, service, display):
        one = display()

        service.set_gestures(("left_fist",))

        assert one.state_where(lambda s: s.held_gestures).held_gestures == ("left_fist",)

    def test_letting_go_reaches_it_too(self, service, display):
        one = display()
        service.set_gestures(("left_fist",))
        one.state_where(lambda s: s.held_gestures)

        service.set_gestures(())

        assert one.state_where(lambda s: not s.held_gestures).held_gestures == ()

    def test_nobody_wants_them_at_the_index(self, service):
        service.open_sheet()

        assert not service.wants_meters

    def test_nor_with_the_sheet_closed(self, service):
        assert not service.wants_meters

    def test_the_gestures_page_wants_them(self, service):
        service.open_gestures()

        assert service.wants_meters

    def test_leaving_that_page_stops_wanting_them(self, service):
        service.open_gestures()
        service.open_modes()

        assert not service.wants_meters

    def test_readings_nobody_asked_for_are_dropped(self, service):
        service.open_sheet()

        service.set_meters(self.meters("fist"))

        assert service.state().meters == ()

    def test_readings_on_that_page_are_kept(self, service):
        service.open_gestures()

        service.set_meters(self.meters("fist"), [HandReading(side="right", span=0.1)])
        state = service.state()

        assert [meter.name for meter in state.meters] == ["fist"]
        assert [hand.side for hand in state.hands] == ["right"]

    def test_they_reach_a_display(self, service, display):
        one = display()
        service.open_gestures()

        service.set_meters(self.meters("fist"))

        assert one.state_where(lambda s: s.meters).meters[0].name == "fist"

    def test_leaving_the_page_clears_them_rather_than_leaving_them_standing(self, service):
        service.open_gestures()
        service.set_meters(self.meters("fist"))

        service.close_sheet()
        service.set_meters(self.meters("fist"))

        assert service.state().meters == ()

    def test_a_dropped_frame_costs_no_publish_at_all(self, service, display):
        one = display()
        one.catalogue()
        one.state()
        service.set_meters(self.meters("fist"))

        one.socket.settimeout(0.2)
        with pytest.raises(TimeoutError):
            one.state()

    def test_switching_them_off_takes_the_stored_frame_with_them(self, service):
        service.open_gestures()
        service.set_meters(self.meters("fist"))

        service.toggle_meters()

        assert not service.meters
        assert service.state().meters == ()
        assert not service.wants_meters

    def test_switching_them_back_on_wants_them_again(self, service):
        service.open_gestures()
        service.toggle_meters()

        service.toggle_meters()

        assert service.wants_meters


class TestTheReadingsStartupState:
    """meters.enabled obeys chip.enabled's two rules, and for the same reason."""

    def views_with(self, settings_of, **overlay):
        settings = settings_of({**CONFIG, "overlay": overlay} if overlay else CONFIG)
        return (
            [
                model.build_view(mode, modes=list(settings.modes))
                for mode in settings.modes.values()
            ],
            settings.overlay,
        )

    def test_they_are_on_by_default(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of)

        running.set_views(views, overlay=overlay)

        assert running.meters

    def test_an_overlay_block_that_says_nothing_about_them_leaves_them_on(
        self, socket_path, settings_of
    ):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of, chip={"enabled": True})

        running.set_views(views, overlay=overlay)

        assert running.meters

    def test_the_first_configuration_sets_it(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of, meters={"enabled": False})

        running.set_views(views, overlay=overlay)

        assert not running.meters

    def test_changing_it_and_reloading_takes_effect(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of)
        running.set_views(views, overlay=overlay)

        views, overlay = self.views_with(settings_of, meters={"enabled": False})
        running.set_views(views, overlay=overlay)

        assert not running.meters

    def test_a_reload_that_did_not_touch_it_keeps_a_toggle(self, socket_path, settings_of):
        running = HudService(socket_path)
        views, overlay = self.views_with(settings_of)
        running.set_views(views, overlay=overlay)
        running.toggle_meters()

        running.set_views(views, overlay=overlay)

        assert not running.meters, "a reload undid a toggle the user had just made"


class TestThePhrasesADisplayAnswersTo:
    def service_for(self, socket_path, settings_of, data=None):
        settings = settings_of(data or CONFIG)
        views = [
            model.build_view(
                mode,
                modes=list(settings.modes),
                navigate=settings.overlay.navigate,
                overrides=settings.overlay.groups,
            )
            for mode in settings.modes.values()
        ]
        running = HudService(socket_path)
        running.set_views(views, overlay=settings.overlay)
        running.set_layers(LayerState(voice="root mode"))
        return running

    def test_the_configured_open_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("overlay open")
        assert running.sheet

    def test_the_configured_close_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)
        running.open_sheet("::navigation")

        assert running.handle("overlay close")
        assert not running.sheet
        assert running.group is None

    def test_the_configured_modes_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("overlay modes")
        assert (running.sheet, running.page) == (True, "modes")

    def test_the_configured_pedals_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("overlay pedals")
        assert (running.sheet, running.page) == (True, "pedals")

    def test_the_verb_opens_the_pedals_page_too(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        running.act("pedals")

        assert (running.sheet, running.page) == (True, "pedals")

    def test_the_configured_gestures_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("overlay gestures")
        assert (running.sheet, running.page) == (True, "gestures")

    def test_the_verb_opens_the_gestures_page_too(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        running.act("gestures")

        assert (running.sheet, running.page) == (True, "gestures")

    def test_the_gestures_page_forgets_the_group_too(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)
        running.open_sheet("buffer")

        running.open_gestures()

        assert (running.page, running.group) == ("gestures", None)

    def test_the_pedals_page_forgets_the_group_the_way_the_modes_page_does(
        self, socket_path, settings_of
    ):
        running = self.service_for(socket_path, settings_of)
        running.open_sheet("buffer")

        running.open_pedals()

        assert (running.page, running.group) == ("pedals", None)

    def test_the_modes_page_forgets_the_group_so_the_two_cannot_disagree(
        self, socket_path, settings_of
    ):
        running = self.service_for(socket_path, settings_of)
        running.open_sheet("buffer")

        running.open_modes()

        assert (running.page, running.group) == ("modes", None)

    def test_closing_from_the_modes_page_goes_back_to_the_index(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)
        running.open_modes()

        running.close_sheet()

        assert (running.sheet, running.page) == (False, "index")

    def test_the_verb_reaches_it_too(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        running.act("modes")

        assert (running.sheet, running.page) == (True, "modes")

    def test_the_chip_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("chip toggle")
        assert not running.chip

    def test_a_generated_group_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("open buffer")
        assert (running.sheet, running.group) == (True, "buffer")

    def test_a_group_phrase_of_a_mode_that_is_not_active_is_not_claimed(
        self, socket_path, settings_of
    ):
        running = self.service_for(socket_path, settings_of)
        running.set_layers(LayerState(voice="other mode"))

        assert not running.handle("open buffer")

    def test_anything_else_is_left_alone(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert not running.handle("buffer save")
        assert not running.handle("")

    def test_a_disabled_phrase_claims_nothing(self, socket_path, settings_of):
        running = self.service_for(
            socket_path, settings_of, {**CONFIG, "overlay": {"sheet": {"open": None}}}
        )

        assert not running.handle("overlay open")

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("open", (True, None)),
            ("sheet", (True, None)),
            ("group:buffer", (True, "buffer")),
            ("close", (False, None)),
        ],
    )
    def test_the_verb_targets(self, socket_path, settings_of, target, expected):
        running = self.service_for(socket_path, settings_of)

        running.act(target)

        assert (running.sheet, running.group) == expected

    def test_the_configured_meters_phrase(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        assert running.handle("overlay meters")
        assert not running.meters

    def test_the_verb_toggles_them_too(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        running.act("meters")

        assert not running.meters

    def test_the_verb_toggles_the_chip(self, socket_path, settings_of):
        running = self.service_for(socket_path, settings_of)

        running.act("chip")

        assert not running.chip

    @pytest.mark.parametrize("target", sorted(HUD_TARGETS))
    def test_every_target_the_loader_accepts_actually_does_something(
        self, socket_path, settings_of, caplog, target
    ):
        running = self.service_for(socket_path, settings_of)

        running.act(target)

        assert "not something a display can do" not in caplog.text

    def test_a_verb_target_no_validated_config_can_produce_says_so(
        self, socket_path, settings_of, caplog
    ):
        running = self.service_for(socket_path, settings_of)

        running.act("wobble")

        assert "wobble" in caplog.text


class TestADisplayThatStopsReading:
    def test_it_cannot_delay_the_core(self, service, display):
        """The property the whole design is for."""
        display()
        wedged = WedgedSocket()
        wedge(service, wedged)
        pushed = threading.Event()

        def push() -> None:
            for _ in range(20):
                service.set_held(HeldInput(held=("shift",)))
                service.set_held(HeldInput())
                service.set_layers(LayerState(voice="other mode"))
                service.set_partial("something")
            pushed.set()

        pusher = threading.Thread(target=push, daemon=True)
        pusher.start()
        try:
            assert pushed.wait(
                PUSH_DEADLINE_SECONDS
            ), "publishing blocked on a display that had stopped reading"
        finally:
            wedged.close()
            pusher.join(timeout=DEADLINE_SECONDS)

    def test_only_the_latest_picture_is_waiting_for_it(self, service, display):
        display()
        wedged = WedgedSocket()
        wedge(service, wedged)

        for held in ("shift", "ctrl", "alt"):
            service.set_held(HeldInput(held=(held,)))
        wedged.close()

        time.sleep(0.2)
        states = [
            frame
            for payload in wedged.sent
            for frame in [protocol.decode(payload)]
            if isinstance(frame, protocol.State)
        ]
        assert len(states) <= 2, f"a burst of three queued {len(states)} frames"
        assert [key.key for key in states[-1].held] == ["alt"]

    def test_it_is_dropped_rather_than_written_to_for_ever(self, service, display):
        display()
        wedge(service, BrokenSocket())

        service.set_held(HeldInput(held=("shift",)))

        deadline = time.monotonic() + DEADLINE_SECONDS
        while service._clients and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service._clients, "a display that cannot be written to was kept"

    def test_the_core_keeps_publishing_to_the_others(self, service, display):
        one = display()
        one.catalogue()
        wedge(service, BrokenSocket())
        second = display()
        second.catalogue()

        service.set_held(HeldInput(held=("shift",)))

        assert [key.key for key in second.state_where(lambda s: s.held).held] == ["shift"]


class TestTheSocketItself:
    def test_a_display_cannot_send_the_core_anything(self, service, display):
        """The core holds /dev/uinput; a display may not ask it to type."""
        one = display()
        one.catalogue()

        with pytest.raises(OSError):
            for _ in range(10):
                one.socket.sendall(b'{"protocol": 1, "kind": "state"}\n')
                time.sleep(0.05)

    def test_nobody_else_on_the_machine_can_read_it(self, service, socket_path):
        assert socket_path.stat().st_mode & 0o777 == 0o600

    def test_a_socket_left_behind_by_a_crash_is_replaced(self, socket_path, views):
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("not a socket at all")

        running = HudService(socket_path)
        try:
            running.show()
            assert running.listening
            assert running.failure is None
        finally:
            running.hide()

    def test_a_second_core_is_refused_rather_than_stealing_it(self, service, socket_path):
        second = HudService(socket_path)

        second.show()

        assert not second.listening
        assert isinstance(second.failure, ProtocolError)
        assert "already publishing" in str(second.failure)

    def test_hanging_up_removes_the_socket(self, service, socket_path):
        service.hide()

        assert not socket_path.exists()

    def test_a_display_sees_the_end_of_the_stream_when_the_core_stops(self, service, display):
        one = display()
        one.catalogue()

        service.hide()

        assert one.socket.recv(4096) == b"" or one.socket.recv(4096) == b""

    def test_showing_twice_is_harmless(self, service):
        service.show()

        assert service.listening

    def test_a_socket_that_will_not_bind_is_reported_rather_than_raised(self, tmp_path):
        (tmp_path / "afile").write_text("not a directory")
        running = HudService(tmp_path / "afile" / "s.sock")

        running.show()

        assert not running.listening
        assert isinstance(running.failure, OSError)

    def test_a_path_that_fails_in_some_other_way_is_reported_too(self, tmp_path):
        running = HudService(tmp_path / "s.sock")
        running.path = tmp_path / "\0bad"

        running.show()

        assert not running.listening
        assert isinstance(running.failure, ValueError)

    def test_a_path_a_unix_socket_cannot_carry_is_refused_when_it_is_built(self, tmp_path):
        with pytest.raises(ProtocolError, match="at most"):
            HudService(tmp_path / ("a" * 120))

    def test_a_path_that_only_just_fits_is_refused_too(self, tmp_path):
        room = protocol.MAX_SOCKET_PATH - len(str(tmp_path)) - len("/")
        just_fits = tmp_path / ("a" * room)
        assert len(str(just_fits)) == protocol.MAX_SOCKET_PATH

        with pytest.raises(ProtocolError, match="at most"):
            HudService(just_fits)

    def test_more_displays_than_the_cap_are_refused(self, socket_path, views):
        running = HudService(socket_path, max_clients=1)
        modes, colours = views()
        running.set_views(modes, colours=colours)
        running.show()
        try:
            first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            first.connect(str(socket_path))
            deadline = time.monotonic() + DEADLINE_SECONDS
            while not running._clients and time.monotonic() < deadline:
                time.sleep(0.01)

            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            second.settimeout(DEADLINE_SECONDS)
            second.connect(str(socket_path))

            assert second.recv(4096) == b"", "a display past the cap was served anyway"
            assert len(running._clients) == 1
            first.close()
            second.close()
        finally:
            running.hide()


def test_publishing_from_two_threads_at_once_never_writes_a_half_picture(service, display):
    """Frames are whole or absent, whichever threads asked for them."""
    one = display()
    one.catalogue()
    stop = threading.Event()

    def churn(mode: str, key: str) -> None:
        while not stop.is_set():
            service.set_layers(LayerState(voice=mode))
            service.set_held(HeldInput(held=(key,)))

    threads = [
        threading.Thread(target=churn, args=("root mode", "shift"), daemon=True),
        threading.Thread(target=churn, args=("other mode", "ctrl"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        for _ in range(30):
            state = one.state()
            assert state.mode in {"root mode", "other mode"}
            assert all(key.key in {"shift", "ctrl"} for key in state.held)
            assert state.revision == 1
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=DEADLINE_SECONDS)


class TestTheCalibrationDot:
    """The dot a screen calibration puts on the screen, on its way to the display."""

    def test_it_knows_whether_anything_is_there_to_draw_the_dots(self, service, display):
        assert not service.connected

        display()
        deadline = time.monotonic() + DEADLINE_SECONDS
        while not service.connected and time.monotonic() < deadline:
            time.sleep(0.01)

        assert service.connected

    def test_no_calibration_means_no_dot(self, service, display):
        one = display()
        one.catalogue()

        assert one.state().dot is None

    def test_a_dot_reaches_the_display_where_it_was_put(self, service, display):
        one = display()
        one.catalogue()
        one.state()

        service.set_dot(protocol.CalibrationDot(x=0.08, y=0.92, index=3, total=9, settled=0.4))

        dot = one.state_where(lambda state: state.dot is not None).dot
        assert (dot.x, dot.y) == (0.08, 0.92)
        assert (dot.index, dot.total) == (3, 9)
        assert dot.settled == pytest.approx(0.4)

    def test_the_dwell_is_published_as_it_fills_because_it_is_the_only_feedback(
        self, service, display
    ):
        one = display()
        one.catalogue()
        one.state()

        for settled in (0.0, 0.5, 1.0):
            service.set_dot(protocol.CalibrationDot(0.5, 0.5, 1, 9, settled))

        assert one.state_where(lambda s: s.dot is not None and s.dot.settled == 1.0)

    def test_clearing_it_takes_the_surface_away(self, service, display):
        one = display()
        one.catalogue()
        one.state()
        service.set_dot(protocol.CalibrationDot(0.5, 0.5, 1, 9, 1.0))
        one.state_where(lambda state: state.dot is not None)

        service.set_dot(None)

        assert one.state_where(lambda state: state.dot is None)

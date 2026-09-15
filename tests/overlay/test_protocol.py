import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from libre_dictum.errors import ProtocolError
from libre_dictum.health import Health
from libre_dictum.layers import LayerState
from libre_dictum.meters import GestureMeter, HandReading, Reading
from libre_dictum.overlay import model, protocol
from libre_dictum.status import Grip, HeldInput

CONFIG = {
    "modes": {
        "::navigation": {"commands": {"step up": "up", "step down": "down"}},
        "::alphabet": {
            "commands": {
                "type a": "a",
                "type b": "b",
                "press e": "e",
                "press f": "f",
                "key i": "i",
                "key j": "j",
            }
        },
        "root mode": {
            "type": "vosk",
            "path": "models/small-en",
            "icon": [10, 20, 30],
            "imports": ["::navigation", "::alphabet"],
            "commands": {
                "focus left": "meta + left",
                "focus right": "meta + right",
                "transit left": "meta + alt + left",
                "transit right": "meta + alt + right",
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer open": "ctrl + o",
            },
        },
        "other mode": {"type": "vosk", "path": "models/small-en"},
    }
}


@pytest.fixture
def catalogue(settings_of):
    settings = settings_of(CONFIG)
    views = [model.build_view(mode, modes=list(settings.modes)) for mode in settings.modes.values()]
    return protocol.catalogue_of(
        views,
        revision=3,
        navigate=model.DEFAULT_NAVIGATE,
        colours={name: mode.icon for name, mode in settings.modes.items()},
    )


def round_trip(frame):
    return protocol.decode(protocol.encode(frame))


class TestACatalogueSurvivesTheWire:
    def test_a_configuration_comes_back_unchanged(self, catalogue):
        assert round_trip(catalogue) == catalogue

    def test_the_mode_keeps_its_colour_and_its_groups(self, catalogue):
        mode = round_trip(catalogue).mode("root mode")

        assert mode.colour == (10, 20, 30)
        assert [group.name for group in mode.groups] == [
            "::navigation",
            "::alphabet",
            "focus/transit",
            "buffer",
        ]

    def test_a_gesture_survives_with_both_of_its_edges(self, catalogue):
        card = protocol.ModeCard(
            name="root mode",
            gestures=(
                protocol.EdgeCard(
                    name="fist", press="hold(left_mouse)", release="release(left_mouse)"
                ),
            ),
        )
        wired = round_trip(
            protocol.Catalogue(
                modes=(card,), gestures=("fist", "blink"), sheet_gestures="show hands"
            )
        )

        assert wired.gestures == ("fist", "blink")
        assert wired.sheet_gestures == "show hands"
        assert wired.mode("root mode").gesture("fist").release == "release(left_mouse)"

    def test_a_gesture_the_mode_binds_nothing_to_is_simply_absent_from_the_card(self):
        wired = round_trip(
            protocol.Catalogue(modes=(protocol.ModeCard(name="m"),), gestures=("fist",))
        )

        assert wired.gestures == ("fist",)
        assert wired.mode("m").gesture("fist") is None

    def test_a_group_carries_the_phrases_that_open_it(self, catalogue):
        group = round_trip(catalogue).mode("root mode").group("focus/transit")

        assert group.phrases == ("open focus", "open transit")
        assert group.navigable

    def test_either_name_of_a_merged_table_finds_it(self, catalogue):
        mode = round_trip(catalogue).mode("root mode")

        assert mode.group("focus") is mode.group("transit")

    def test_an_aligned_table_keeps_its_axes_and_its_gaps(self, catalogue):
        table = round_trip(catalogue).mode("root mode").group("focus/transit").table

        assert table.aligned
        assert table.row_labels == ("focus", "transit")
        assert table.column_labels == ("left", "right")

    def test_a_ragged_table_has_no_columns(self, catalogue):
        table = round_trip(catalogue).mode("root mode").group("::alphabet").table

        assert table is not None
        assert not table.aligned
        assert table.column_labels is None
        assert table.row_labels == ("type", "press", "key")

    def test_a_table_cell_still_names_its_own_command(self, catalogue):
        group = round_trip(catalogue).mode("root mode").group("focus/transit")
        rows = dict(zip(group.table.row_labels, group.table.rows, strict=True))
        columns = group.table.column_labels

        for label, row in rows.items():
            for column, index in zip(columns, row, strict=True):
                assert group.cell(index).phrase == f"{label} {column}"

    def test_a_cell_carries_the_tail_a_table_draws(self, catalogue):
        group = round_trip(catalogue).mode("root mode").group("buffer")

        assert {entry.tail for entry in group.entries} == {"save", "close", "open"}
        assert group.entries[0].label == group.entries[0].tail

    def test_which_page_the_sheet_is_on_crosses(self):
        assert round_trip(protocol.State(revision=1, sheet=True, page="modes")).page == "modes"

    def test_a_page_this_reader_has_never_heard_of_is_refused(self):
        payload = json.loads(protocol.encode(protocol.State(revision=1)))
        payload["page"] = "keyboard"

        with pytest.raises(ProtocolError, match="page"):
            protocol.decode(json.dumps(payload))

    def test_the_pages_are_the_ones_the_model_has(self):
        assert {str(page) for page in model.Page} == set(protocol.PAGES)

    def test_the_sheet_s_own_phrases_cross_with_it(self, catalogue):
        came_back = round_trip(
            protocol.Catalogue(
                revision=1, navigate="open", sheet_open="menu show", sheet_close="menu hide"
            )
        )

        assert came_back.navigate == "open"
        assert came_back.sheet_open == "menu show"
        assert came_back.sheet_close == "menu hide"

    def test_a_frame_without_them_is_refused_rather_than_guessed_at(self):
        payload = json.loads(protocol.encode(protocol.Catalogue(revision=1)))
        del payload["sheet_open"]

        with pytest.raises(ProtocolError, match="sheet_open"):
            protocol.decode(json.dumps(payload))

    def test_a_one_word_phrase_falls_back_to_itself(self):
        assert protocol.Cell(phrase="panic", response="x").label == "panic"

    def test_a_mode_that_cannot_be_navigated_says_so(self, settings_of):
        settings = settings_of(
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
        view = model.build_view(settings.modes["root mode"], modes=["root mode"])
        mode = round_trip(protocol.catalogue_of([view])).mode("root mode")

        assert not mode.navigable
        assert all(not group.navigable for group in mode.groups)

    def test_a_route_out_survives(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "root mode": {
                        "type": "vosk",
                        "path": "m",
                        "aliases": {"GO": "mode(other mode)"},
                        "commands": {"go over there": "GO"},
                    },
                    "other mode": {"type": "vosk", "path": "m"},
                }
            }
        )
        view = model.build_view(settings.modes["root mode"], modes=list(settings.modes))
        mode = round_trip(protocol.catalogue_of([view])).mode("root mode")
        commands = [route for route in mode.routes if route.kind == "command"]

        assert [(route.by, route.target) for route in commands] == [("go over there", "other mode")]

    def test_the_whole_live_shaped_catalogue_is_a_few_tens_of_kilobytes(self, catalogue):
        assert len(protocol.encode(catalogue)) < 100_000


class TestEveryFieldSurvivesTheWire:
    """Both sides of the codec name their fields by hand, so nothing checks they agree."""

    def test_the_json_carries_every_field_of_state(self):
        from dataclasses import fields

        payload = protocol._state_json(protocol.State())

        missing = {field.name for field in fields(protocol.State)} - set(payload)
        assert not missing, f"fields dropped on the wire: {sorted(missing)}"

    def test_and_every_field_comes_back_set(self):
        from dataclasses import fields, replace

        filled = replace(
            protocol.State(),
            **{
                field.name: value
                for field, value in [(f, _NON_DEFAULT.get(f.name)) for f in fields(protocol.State)]
                if value is not None
            },
        )

        assert round_trip(filled) == filled


_NON_DEFAULT: dict[str, object] = {
    "revision": 7,
    "mode": "root mode",
    "gesture_mode": "mouse mode",
    "pedal_mode": "scrolling",
    "asleep": ("voice", "gesture"),
    "wake_with": ("pedal",),
    "confirming": "sleep? again to confirm",
    "notice": "refused: only pedal wakes this",
    "saved": "3",
    "fault": "mode 'a' (gone)",
    "partial": "buffer",
    "heard": "buffer save",
    "matched": "buffer save",
    "nearest": "buffer close",
    "chip": False,
    "sheet": True,
    "page": "modes",
    "group": "focus",
    "held_gestures": ("fist",),
}


class TestAStateSurvivesTheWire:
    def snapshot(self, **kwargs):
        mode = kwargs.pop("mode", None)
        if mode is not None:
            kwargs["layers"] = LayerState(voice=mode)
        return protocol.state_of(model.Snapshot(**kwargs), revision=3)

    def test_an_empty_state_comes_back_empty(self):
        state = self.snapshot()

        assert round_trip(state) == state
        assert not state.missed
        assert not state.hearing

    def test_held_keys_travel_with_their_glyphs(self):
        state = self.snapshot(held=HeldInput(held=("shift",), pending=("ctrl",)))
        back = round_trip(state)

        assert back == state
        assert [(key.key, key.symbol, key.grip) for key in back.held] == [
            ("ctrl", "⌃", Grip.PENDING.value),
            ("shift", "⇧", Grip.HELD.value),
        ]

    def test_a_key_held_under_an_alias_travels_under_its_real_name(self):
        back = round_trip(self.snapshot(held=HeldInput(held=("win",))))

        assert [key.key for key in back.held] == ["meta"]

    def test_a_fault_carries_the_reason_and_not_only_the_name(self):
        health = Health(working=("mode 'b'",), failed=(("mode 'a'", "model file vanished"),))
        back = round_trip(self.snapshot(health=health))

        assert back.fault == "mode 'a' (model file vanished)"

    def test_a_healthy_session_has_no_fault(self):
        assert round_trip(self.snapshot(health=Health(working=("mode 'a'",)))).fault is None

    def test_a_near_miss_travels_and_is_a_miss(self):
        back = round_trip(
            self.snapshot(heard=model.Heard(text="buffer safe", nearest="buffer save"))
        )

        assert (back.heard, back.nearest, back.matched) == ("buffer safe", "buffer save", None)
        assert back.missed

    def test_a_match_is_not_a_miss(self):
        back = round_trip(
            self.snapshot(heard=model.Heard(text="buffer save", matched="buffer save"))
        )

        assert not back.missed

    @pytest.mark.parametrize(
        ("partial", "hearing"), [(None, False), ("", True), ("vector al", True)]
    )
    def test_the_three_states_of_hearing_are_all_distinguishable(self, partial, hearing):
        back = round_trip(self.snapshot(heard=model.Heard(partial=partial)))

        assert back.partial == partial
        assert back.hearing is hearing

    def test_the_open_group_and_the_two_surfaces_travel(self):
        state = protocol.state_of(
            model.Snapshot(layers=LayerState(voice="root mode"), open_group="buffer"),
            chip=False,
            sheet=True,
        )
        back = round_trip(state)

        assert (back.mode, back.group) == ("root mode", "buffer")
        assert (back.chip, back.sheet) == (False, True)

    def test_a_state_is_small_enough_to_send_on_every_change(self):
        assert len(protocol.encode(self.snapshot())) < 512

    def test_the_held_gestures_travel_as_names_alone(self):
        back = round_trip(self.snapshot(held_gestures=("left_fist", "squint")))

        assert back.held_gestures == ("left_fist", "squint")


class TestTheReadingsSurviveTheWire:
    """The live meters, the one measurement in a state frame."""

    def state(self, meters=(), hands=()):
        return protocol.state_of(
            model.Snapshot(meters=tuple(meters), hands=tuple(hands)), revision=3
        )

    def meter(self, *readings, **kwargs):
        return GestureMeter(name="right_fist", readings=tuple(readings), **kwargs)

    def test_a_gesture_comes_back_with_every_reading_intact(self):
        state = self.state(
            [
                self.meter(
                    Reading("rightHandFist", 0.62, 0.8, None),
                    Reading("rightHandThumbCurl", 0.31, None, 0.4),
                    active=True,
                    hold=0.15,
                )
            ]
        )
        back = round_trip(state)

        assert back == state
        blocking = back.meters[0].blocking
        assert blocking is not None
        assert blocking.describe() == "rightHandFist 0.62 >0.80"

    def test_a_score_no_frame_reported_stays_absent_rather_than_becoming_zero(self):
        back = round_trip(self.state([self.meter(Reading("handFist", None, 0.8, None))]))

        assert back.meters[0].readings[0].score is None
        assert back.meters[0].waiting

    def test_a_limit_a_gesture_does_not_have_stays_absent_too(self):
        back = round_trip(self.state([self.meter(Reading("handFist", 0.9, 0.8, None))]))

        assert back.meters[0].readings[0].maximum is None
        assert back.meters[0].holds

    def test_a_hand_reading_keeps_every_ratio_a_constant_is_set_from(self):
        hand = HandReading(
            side="right",
            primary=True,
            span=0.164,
            extension=(0.58, 0.36, 0.34, 0.33, 0.32),
            pinch_index=0.21,
            pinch_middle=0.55,
            spread=0.20,
            palm_area=0.30,
        )
        back = round_trip(self.state([self.meter()], [hand]))

        assert back.hands == (hand,)

    def test_a_state_with_no_readings_carries_none(self):
        assert round_trip(self.state()).meters == ()
        assert round_trip(self.state()).hands == ()

    def test_a_page_of_readings_still_fits_on_one_line(self):
        meters = [
            GestureMeter(
                name=f"gesture_{index}",
                readings=tuple(
                    Reading(f"someHandFeature{part}", 0.5, 0.8, None) for part in range(3)
                ),
                hold=0.25,
            )
            for index in range(20)
        ]
        encoded = protocol.encode(self.state(meters, [HandReading(side="right")] * 2))

        assert len(encoded) < 8 * 1024
        assert round_trip(protocol.decode(encoded)) == protocol.decode(encoded)


class TestAMalformedFrameIsRefused:
    def test_a_line_that_is_not_json(self):
        with pytest.raises(ProtocolError, match="not a JSON frame"):
            protocol.decode(b"{oh dear")

    def test_a_frame_that_is_not_an_object(self):
        with pytest.raises(ProtocolError, match="must be a JSON object"):
            protocol.decode(b"[1, 2, 3]")

    def test_a_protocol_version_this_reader_does_not_speak(self):
        payload = json.loads(protocol.encode(protocol.State()))
        payload["protocol"] = protocol.PROTOCOL_VERSION + 1

        with pytest.raises(ProtocolError, match="this reader speaks"):
            protocol.decode(json.dumps(payload))

    def test_a_kind_nobody_has_defined(self):
        with pytest.raises(ProtocolError, match="unknown frame kind"):
            protocol.decode(json.dumps({"protocol": protocol.PROTOCOL_VERSION, "kind": "poem"}))

    def test_a_missing_field(self):
        payload = json.loads(protocol.encode(protocol.State()))
        del payload["mode"]
        payload["chip"] = None

        with pytest.raises(ProtocolError, match="'chip'"):
            protocol.decode(json.dumps(payload))

    def test_a_revision_that_is_a_boolean(self):
        payload = json.loads(protocol.encode(protocol.State()))
        payload["revision"] = True

        with pytest.raises(ProtocolError, match="must be an integer"):
            protocol.decode(json.dumps(payload))

    def test_a_table_cell_pointing_past_the_end_of_its_group(self, catalogue):
        payload = json.loads(protocol.encode(catalogue))
        group = next(
            group for mode in payload["modes"] for group in mode["groups"] if group.get("table")
        )
        group["table"]["rows"][0][0] = 99

        with pytest.raises(ProtocolError, match="names no entry"):
            protocol.decode(json.dumps(payload))

    def test_a_score_that_is_a_boolean(self):
        payload = json.loads(
            protocol.encode(
                protocol.state_of(
                    model.Snapshot(
                        meters=(GestureMeter("fist", (Reading("handFist", 0.5, 0.8, None),)),)
                    )
                )
            )
        )
        payload["meters"][0]["readings"][0][1] = True

        with pytest.raises(ProtocolError, match="number or null"):
            protocol.decode(json.dumps(payload))

    def test_a_reading_missing_a_position(self):
        payload = json.loads(
            protocol.encode(
                protocol.state_of(
                    model.Snapshot(
                        meters=(GestureMeter("fist", (Reading("handFist", 0.5, 0.8, None),)),)
                    )
                )
            )
        )
        payload["meters"][0]["readings"][0] = ["handFist", 0.5]

        with pytest.raises(ProtocolError, match="number or null"):
            protocol.decode(json.dumps(payload))

    def test_a_span_that_is_not_a_number(self):
        payload = json.loads(
            protocol.encode(protocol.state_of(model.Snapshot(hands=(HandReading(),))))
        )
        payload["hands"][0]["span"] = "near"

        with pytest.raises(ProtocolError, match="must be a number"):
            protocol.decode(json.dumps(payload))

    def test_an_extension_list_that_is_not_numbers(self):
        payload = json.loads(
            protocol.encode(protocol.state_of(model.Snapshot(hands=(HandReading(),))))
        )
        payload["hands"][0]["extension"] = ["straight"]

        with pytest.raises(ProtocolError, match="list of numbers"):
            protocol.decode(json.dumps(payload))

    def test_a_colour_that_is_not_three_numbers(self, catalogue):
        payload = json.loads(protocol.encode(catalogue))
        payload["modes"][0]["colour"] = ["red"]

        with pytest.raises(ProtocolError, match="three integers"):
            protocol.decode(json.dumps(payload))


class TestFraming:
    def stream(self, *frames, extra=b""):
        return io.BytesIO(b"".join(protocol.encode(frame) for frame in frames) + extra)

    def test_frames_are_one_per_line(self):
        frames = [protocol.Catalogue(revision=1), protocol.State(revision=1)]

        assert list(protocol.read_frames(self.stream(*frames))) == frames

    def test_a_blank_line_is_not_a_frame(self):
        stream = self.stream(protocol.State(), extra=b"\n\n")

        assert len(list(protocol.read_frames(stream))) == 1

    def test_the_stream_ending_is_not_an_error(self):
        assert list(protocol.read_frames(self.stream())) == []

    def test_a_frame_larger_than_the_cap_is_refused(self):
        oversized = io.BytesIO(b"x" * (protocol.MAX_FRAME_BYTES + 1) + b"\n")

        with pytest.raises(ProtocolError, match="limit"):
            list(protocol.read_frames(oversized))

    def test_a_truncated_last_frame_is_refused_rather_than_half_read(self):
        truncated = io.BytesIO(protocol.encode(protocol.State())[:-20])

        with pytest.raises(ProtocolError, match="truncated"):
            list(protocol.read_frames(truncated))


class TestWhereTheSocketLives:
    def test_the_environment_variable_wins(self, monkeypatch):
        monkeypatch.setenv(protocol.SOCKET_ENV, "/run/somewhere/else.sock")

        assert str(protocol.default_socket_path()) == "/run/somewhere/else.sock"

    def test_otherwise_the_session_runtime_directory(self, monkeypatch):
        monkeypatch.delenv(protocol.SOCKET_ENV, raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")

        assert protocol.default_socket_path() == Path("/run/user/1000/libre-dictum/hud.sock")

    def test_and_a_per_user_fallback_when_there_is_none(self, monkeypatch):
        monkeypatch.delenv(protocol.SOCKET_ENV, raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        assert str(protocol.default_socket_path()).startswith(f"/tmp/libre-dictum-{os.getuid()}")

    def test_a_path_a_unix_socket_cannot_carry_is_refused_up_front(self):
        with pytest.raises(ProtocolError, match="at most"):
            protocol.check_socket_path(Path("/tmp/" + "a" * 200))

    def test_a_path_it_can_is_returned(self):
        assert protocol.check_socket_path(Path("/tmp/x.sock")) == Path("/tmp/x.sock")


def test_the_display_side_of_the_protocol_needs_none_of_the_core():
    """Importing the protocol must not reach the executor, and so not evdev."""
    probe = (
        "import sys, libre_dictum.overlay.protocol as p;"
        "print(sorted(m for m in ('evdev', 'vosk', 'sounddevice', 'numpy')"
        " if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]", f"the protocol dragged in {result.stdout.strip()}"

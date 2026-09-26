from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from ..errors import ProtocolError
from ..layers import Layer
from ..meters import GestureMeter, HandReading, Reading
from ..runtime import runtime_dir
from ..status import HeldInput, canonical

if TYPE_CHECKING:
    from ..health import Health
    from .model import Entry, Group, ModeView, Snapshot

PROTOCOL_VERSION = 10

PAGES = ("index", "group", "modes", "pedals", "gestures")

SOCKET_ENV = "LIBRE_DICTUM_HUD_SOCKET"

SOCKET_NAME = "hud.sock"

MAX_SOCKET_PATH = 107

MAX_FRAME_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Cell:
    """One command: what to say, what it does, and what a table shows of it."""

    phrase: str
    response: str
    tail: str = ""

    @property
    def label(self) -> str:
        """What a table cell shows: the tail, or the phrase when there is no tail."""
        return self.tail or self.phrase


@dataclass(frozen=True, slots=True)
class TableCard:
    """A group's two-dimensional shape, as indices into its group's entries."""

    row_labels: tuple[str, ...]
    rows: tuple[tuple[int | None, ...], ...]
    column_labels: tuple[str, ...] | None = None

    @property
    def aligned(self) -> bool:
        return self.column_labels is not None


@dataclass(frozen=True, slots=True)
class GroupCard:
    """A named set of commands, the phrases that open it, and its shape."""

    name: str
    entries: tuple[Cell, ...] = ()
    phrases: tuple[str, ...] = ()
    table: TableCard | None = None
    source: str = ""

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def navigable(self) -> bool:
        """Whether anything can be said to open this group."""
        return bool(self.phrases)

    def cell(self, index: int | None) -> Cell | None:
        """The command a table cell holds, or None for a gap."""
        return None if index is None else self.entries[index]


@dataclass(frozen=True, slots=True)
class RouteCard:
    """One way out of a mode."""

    target: str
    kind: str
    by: str
    layer: str = ""


@dataclass(frozen=True, slots=True)
class EdgeCard:
    """One pedal or gesture, and what each edge does."""

    name: str
    press: str = ""
    release: str = ""


@dataclass(frozen=True, slots=True)
class ModeCard:
    """Everything a display can say about one mode."""

    name: str
    colour: tuple[int, int, int] | None = None
    navigable: bool = True
    runnable: bool = True
    groups: tuple[GroupCard, ...] = ()
    routes: tuple[RouteCard, ...] = ()
    pedals: tuple[EdgeCard, ...] = ()
    gestures: tuple[EdgeCard, ...] = ()

    def pedal(self, name: str) -> EdgeCard | None:
        """What this mode binds to one pedal, or None when it binds nothing."""
        return next((pedal for pedal in self.pedals if pedal.name == name), None)

    def gesture(self, name: str) -> EdgeCard | None:
        """What this mode binds to one gesture, or None when it binds nothing."""
        return next((gesture for gesture in self.gestures if gesture.name == name), None)

    def group(self, name: str) -> GroupCard | None:
        """The group called name, whether or not it is a merged table's first name."""
        for group in self.groups:
            if group.name == name or name in group.name.split("/"):
                return group
        return None


@dataclass(frozen=True, slots=True)
class Catalogue:
    """The configuration in force, as every display draws it."""

    revision: int = 0
    navigate: str = ""
    output: str = ""
    output_only: bool = True
    sheet_open: str = ""
    sheet_close: str = ""
    sheet_modes: str = ""
    sheet_pedals: str = ""
    sheet_gestures: str = ""
    modes: tuple[ModeCard, ...] = ()
    pedals: tuple[str, ...] = ()
    gestures: tuple[str, ...] = ()

    def mode(self, name: str | None) -> ModeCard | None:
        if name is None:
            return None
        return next((mode for mode in self.modes if mode.name == name), None)


@dataclass(frozen=True, slots=True)
class CalibrationDot:
    """One dot to look at, while the screen mapping is being measured."""

    x: float
    y: float
    index: int
    total: int
    settled: float = 0.0


@dataclass(frozen=True, slots=True)
class KeyCard:
    """One key that is down, under the name and glyph a display draws it by."""

    key: str
    symbol: str
    grip: str

    def describe(self) -> str:
        """ "shift held" -- the words behind the glyph."""
        return f"{self.key} {self.grip}"


@dataclass(frozen=True, slots=True)
class State:
    """Right now: one frame, comparable as a whole."""

    revision: int = 0
    mode: str | None = None
    gesture_mode: str | None = None
    pedal_mode: str | None = None
    asleep: tuple[str, ...] = ()
    wake_with: tuple[str, ...] = ()
    confirming: str | None = None
    notice: str | None = None
    held: tuple[KeyCard, ...] = ()
    saved: str | None = None
    fault: str | None = None
    partial: str | None = None
    heard: str | None = None
    matched: str | None = None
    nearest: str | None = None
    chip: bool = True
    sheet: bool = False
    page: str = PAGES[0]
    group: str | None = None
    held_gestures: tuple[str, ...] = ()
    meters: tuple[GestureMeter, ...] = ()
    hands: tuple[HandReading, ...] = ()
    dot: CalibrationDot | None = None

    @property
    def missed(self) -> bool:
        """Whether the last settled utterance matched nothing."""
        return self.heard is not None and self.matched is None

    @property
    def hearing(self) -> bool:
        """Whether the recognizer is taking something in right now."""
        return self.partial is not None


Frame = Catalogue | State


def encode(frame: Frame) -> bytes:
    """One frame as the bytes to write, terminator included."""
    payload = _catalogue_json(frame) if isinstance(frame, Catalogue) else _state_json(frame)
    payload["protocol"] = PROTOCOL_VERSION
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _catalogue_json(catalogue: Catalogue) -> dict[str, Any]:
    return {
        "kind": "catalogue",
        "revision": catalogue.revision,
        "navigate": catalogue.navigate,
        "output": catalogue.output,
        "output_only": catalogue.output_only,
        "sheet_open": catalogue.sheet_open,
        "sheet_close": catalogue.sheet_close,
        "sheet_modes": catalogue.sheet_modes,
        "sheet_pedals": catalogue.sheet_pedals,
        "sheet_gestures": catalogue.sheet_gestures,
        "modes": [_mode_json(mode) for mode in catalogue.modes],
        "pedals": list(catalogue.pedals),
        "gestures": list(catalogue.gestures),
    }


def _mode_json(mode: ModeCard) -> dict[str, Any]:
    return {
        "name": mode.name,
        "colour": list(mode.colour) if mode.colour is not None else None,
        "navigable": mode.navigable,
        "runnable": mode.runnable,
        "groups": [_group_json(group) for group in mode.groups],
        "routes": [
            {"target": r.target, "kind": r.kind, "by": r.by, "layer": r.layer} for r in mode.routes
        ],
        "pedals": [[p.name, p.press, p.release] for p in mode.pedals],
        "gestures": [[g.name, g.press, g.release] for g in mode.gestures],
    }


def _group_json(group: GroupCard) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": group.name,
        "phrases": list(group.phrases),
        "entries": [[entry.phrase, entry.response, entry.tail] for entry in group.entries],
        "source": group.source,
    }
    if group.table is not None:
        payload["table"] = {
            "row_labels": list(group.table.row_labels),
            "column_labels": (
                None if group.table.column_labels is None else list(group.table.column_labels)
            ),
            "rows": [list(row) for row in group.table.rows],
        }
    return payload


def _state_json(state: State) -> dict[str, Any]:
    return {
        "kind": "state",
        "revision": state.revision,
        "mode": state.mode,
        "gesture_mode": state.gesture_mode,
        "pedal_mode": state.pedal_mode,
        "asleep": list(state.asleep),
        "wake_with": list(state.wake_with),
        "confirming": state.confirming,
        "notice": state.notice,
        "held": [[key.key, key.symbol, key.grip] for key in state.held],
        "saved": state.saved,
        "fault": state.fault,
        "partial": state.partial,
        "heard": state.heard,
        "matched": state.matched,
        "nearest": state.nearest,
        "chip": state.chip,
        "sheet": state.sheet,
        "page": state.page,
        "group": state.group,
        "held_gestures": list(state.held_gestures),
        "meters": [_meter_json(meter) for meter in state.meters],
        "hands": [_hand_json(hand) for hand in state.hands],
        "dot": _dot_json(state.dot),
    }


def _dot_json(dot: CalibrationDot | None) -> list[float] | None:
    """The calibration dot as five numbers, or None."""
    if dot is None:
        return None
    return [dot.x, dot.y, dot.index, dot.total, dot.settled]


def _meter_json(meter: GestureMeter) -> dict[str, Any]:
    return {
        "name": meter.name,
        "active": meter.active,
        "hold": meter.hold,
        "readings": [
            [reading.feature, reading.score, reading.minimum, reading.maximum]
            for reading in meter.readings
        ],
    }


def _hand_json(hand: HandReading) -> dict[str, Any]:
    return {
        "side": hand.side,
        "primary": hand.primary,
        "span": hand.span,
        "extension": list(hand.extension),
        "pinch_index": hand.pinch_index,
        "pinch_middle": hand.pinch_middle,
        "spread": hand.spread,
        "palm_area": hand.palm_area,
    }


def decode(line: bytes | str) -> Frame:
    """One frame out of one line."""
    try:
        payload = json.loads(line)
    except ValueError as exc:
        raise ProtocolError(f"not a JSON frame: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"a frame must be a JSON object, not {type(payload).__name__}")

    version = _integer(payload, "protocol")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"frame speaks protocol {version}, this reader speaks {PROTOCOL_VERSION}"
        )

    kind = _string(payload, "kind")
    if kind == "catalogue":
        return _catalogue_from(payload)
    if kind == "state":
        return _state_from(payload)
    raise ProtocolError(f"unknown frame kind {kind!r}")


def _catalogue_from(payload: Mapping[str, Any]) -> Catalogue:
    return Catalogue(
        revision=_integer(payload, "revision"),
        navigate=_string(payload, "navigate"),
        output=_string(payload, "output"),
        output_only=_boolean(payload, "output_only"),
        sheet_open=_string(payload, "sheet_open"),
        sheet_close=_string(payload, "sheet_close"),
        sheet_modes=_string(payload, "sheet_modes"),
        sheet_pedals=_string(payload, "sheet_pedals"),
        sheet_gestures=_string(payload, "sheet_gestures"),
        modes=tuple(_mode_from(mode) for mode in _objects(payload, "modes")),
        pedals=_strings(payload, "pedals"),
        gestures=_strings(payload, "gestures"),
    )


def _mode_from(payload: Mapping[str, Any]) -> ModeCard:
    return ModeCard(
        name=_string(payload, "name"),
        colour=_colour(payload.get("colour")),
        navigable=_boolean(payload, "navigable"),
        runnable=_boolean(payload, "runnable"),
        groups=tuple(_group_from(group) for group in _objects(payload, "groups")),
        routes=tuple(
            RouteCard(
                target=_string(route, "target"),
                kind=_string(route, "kind"),
                by=_string(route, "by"),
                layer=_string(route, "layer"),
            )
            for route in _objects(payload, "routes")
        ),
        pedals=tuple(
            EdgeCard(name=_at(pedal, 0), press=_at(pedal, 1), release=_at(pedal, 2))
            for pedal in _objects(payload, "pedals", of=list)
        ),
        gestures=tuple(
            EdgeCard(name=_at(g, 0), press=_at(g, 1), release=_at(g, 2))
            for g in _objects(payload, "gestures", of=list)
        ),
    )


def _group_from(payload: Mapping[str, Any]) -> GroupCard:
    entries = tuple(
        Cell(phrase=_at(pair, 0), response=_at(pair, 1), tail=_at(pair, 2))
        for pair in _lists(payload)
    )
    table = payload.get("table")
    if table is None:
        return GroupCard(
            name=_string(payload, "name"),
            entries=entries,
            phrases=_strings(payload, "phrases"),
            source=_string(payload, "source"),
        )
    if not isinstance(table, dict):
        raise ProtocolError("'table' must be a JSON object")
    return GroupCard(
        name=_string(payload, "name"),
        entries=entries,
        phrases=_strings(payload, "phrases"),
        source=_string(payload, "source"),
        table=_table_from(table, len(entries)),
    )


def _table_from(payload: Mapping[str, Any], entries: int) -> TableCard:
    labels = payload.get("column_labels")
    rows = []
    for row in _objects(payload, "rows", of=list):
        cells: list[int | None] = []
        for cell in row:
            if cell is None:
                cells.append(None)
                continue
            if not isinstance(cell, int) or isinstance(cell, bool) or not 0 <= cell < entries:
                raise ProtocolError(f"table cell {cell!r} names no entry of this group")
            cells.append(cell)
        rows.append(tuple(cells))
    return TableCard(
        row_labels=_strings(payload, "row_labels"),
        rows=tuple(rows),
        column_labels=None if labels is None else _strings(payload, "column_labels"),
    )


def _state_from(payload: Mapping[str, Any]) -> State:
    return State(
        revision=_integer(payload, "revision"),
        mode=_optional_string(payload, "mode"),
        gesture_mode=_optional_string(payload, "gesture_mode"),
        pedal_mode=_optional_string(payload, "pedal_mode"),
        asleep=_strings(payload, "asleep"),
        wake_with=_strings(payload, "wake_with"),
        confirming=_optional_string(payload, "confirming"),
        notice=_optional_string(payload, "notice"),
        held=tuple(
            KeyCard(key=_at(key, 0), symbol=_at(key, 1), grip=_at(key, 2))
            for key in _objects(payload, "held", of=list)
        ),
        saved=_optional_string(payload, "saved"),
        fault=_optional_string(payload, "fault"),
        partial=_optional_string(payload, "partial"),
        heard=_optional_string(payload, "heard"),
        matched=_optional_string(payload, "matched"),
        nearest=_optional_string(payload, "nearest"),
        chip=_boolean(payload, "chip"),
        sheet=_boolean(payload, "sheet"),
        page=_page(payload),
        group=_optional_string(payload, "group"),
        held_gestures=_strings(payload, "held_gestures"),
        meters=tuple(_meter_from(meter) for meter in _objects(payload, "meters")),
        hands=tuple(_hand_from(hand) for hand in _objects(payload, "hands")),
        dot=_dot_from(payload.get("dot")),
    )


def _dot_from(payload: Any) -> CalibrationDot | None:
    if payload is None:
        return None
    if (
        not isinstance(payload, list)
        or len(payload) != 5
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in payload)
    ):
        raise ProtocolError(f"'dot' is {payload!r}, not five numbers")
    return CalibrationDot(
        x=float(payload[0]),
        y=float(payload[1]),
        index=int(payload[2]),
        total=int(payload[3]),
        settled=float(payload[4]),
    )


def _meter_from(payload: Mapping[str, Any]) -> GestureMeter:
    return GestureMeter(
        name=_string(payload, "name"),
        active=_boolean(payload, "active"),
        hold=_number(payload, "hold"),
        readings=tuple(
            Reading(
                feature=_at(reading, 0),
                score=_optional_number(reading, 1),
                minimum=_optional_number(reading, 2),
                maximum=_optional_number(reading, 3),
            )
            for reading in _objects(payload, "readings", of=list)
        ),
    )


def _hand_from(payload: Mapping[str, Any]) -> HandReading:
    return HandReading(
        side=_string(payload, "side"),
        primary=_boolean(payload, "primary"),
        span=_number(payload, "span"),
        extension=tuple(_numbers(payload, "extension")),
        pinch_index=_number(payload, "pinch_index"),
        pinch_middle=_number(payload, "pinch_middle"),
        spread=_number(payload, "spread"),
        palm_area=_number(payload, "palm_area"),
    )


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"{key!r} must be a string, not {type(value).__name__}")
    return value


def _page(payload: Mapping[str, Any]) -> str:
    """One of PAGES, or a refusal."""
    value = _string(payload, "page")
    if value not in PAGES:
        raise ProtocolError(f"'page' must be one of {', '.join(PAGES)}, not {value!r}")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ProtocolError(f"{key!r} must be a string or null, not {type(value).__name__}")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{key!r} must be an integer, not {type(value).__name__}")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float:
    """A float or an int, as a float."""
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ProtocolError(f"{key!r} must be a number, not {type(value).__name__}")
    return float(value)


def _numbers(payload: Mapping[str, Any], key: str) -> tuple[float, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, int | float) and not isinstance(item, bool) for item in value
    ):
        raise ProtocolError(f"{key!r} must be a list of numbers")
    return tuple(float(item) for item in value)


def _optional_number(row: Sequence[Any], index: int) -> float | None:
    """One position of a reading."""
    if len(row) <= index:
        raise ProtocolError(f"expected a number or null at position {index} of {row!r}")
    value = row[index]
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ProtocolError(f"expected a number or null at position {index} of {row!r}")
    return float(value)


def _boolean(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ProtocolError(f"{key!r} must be a boolean, not {type(value).__name__}")
    return value


def _colour(value: Any) -> tuple[int, int, int] | None:
    """An [r, g, b] triple, or None for a mode that configured no colour."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise ProtocolError(f"'colour' must be null or three integers, not {value!r}")
    if not all(isinstance(part, int) and not isinstance(part, bool) for part in value):
        raise ProtocolError(f"'colour' must be null or three integers, not {value!r}")
    red, green, blue = value
    return (red, green, blue)


def _strings(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProtocolError(f"{key!r} must be a list of strings")
    return tuple(value)


def _objects(payload: Mapping[str, Any], key: str, of: type = dict) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, of) for item in value):
        raise ProtocolError(f"{key!r} must be a list of {of.__name__}s")
    return value


def _lists(payload: Mapping[str, Any]) -> list[Any]:
    return _objects(payload, "entries", of=list)


def _at(row: Sequence[Any], index: int) -> str:
    if len(row) <= index or not isinstance(row[index], str):
        raise ProtocolError(f"expected a string at position {index} of {row!r}")
    value: str = row[index]
    return value


def read_frames(stream: IO[bytes]) -> Iterator[Frame]:
    """Every frame on stream, until it closes."""
    while True:
        line = stream.readline(MAX_FRAME_BYTES + 1)
        if not line:
            return
        if not line.endswith(b"\n"):
            raise ProtocolError(
                f"frame of {len(line)} bytes is truncated, or longer than the "
                f"{MAX_FRAME_BYTES}-byte limit"
            )
        if line.strip():
            yield decode(line)


def default_socket_path() -> Path:
    """Where the core publishes and the HUD looks, unless told otherwise."""
    override = os.environ.get(SOCKET_ENV)
    if override:
        return Path(override)
    return runtime_dir() / SOCKET_NAME


def check_socket_path(path: Path) -> Path:
    """path if AF_UNIX can carry it, else a ProtocolError saying why."""
    encoded = len(str(path).encode())
    if encoded > MAX_SOCKET_PATH:
        raise ProtocolError(
            f"socket path is {encoded} bytes, and a unix socket takes at most "
            f"{MAX_SOCKET_PATH}: {path}"
        )
    return path


def catalogue_of(
    views: Sequence[ModeView],
    *,
    revision: int = 0,
    navigate: str = "",
    output: str = "",
    output_only: bool = True,
    sheet_open: str = "",
    sheet_close: str = "",
    sheet_modes: str = "",
    sheet_pedals: str = "",
    sheet_gestures: str = "",
    pedals: Sequence[str] = (),
    gestures: Sequence[str] = (),
    colours: Mapping[str, tuple[int, int, int] | None] | None = None,
) -> Catalogue:
    """The catalogue frame for a loaded configuration's mode views."""
    palette = colours or {}
    return Catalogue(
        revision=revision,
        navigate=navigate,
        output=output,
        output_only=output_only,
        sheet_open=sheet_open,
        sheet_close=sheet_close,
        sheet_modes=sheet_modes,
        sheet_pedals=sheet_pedals,
        sheet_gestures=sheet_gestures,
        pedals=tuple(pedals),
        gestures=tuple(gestures),
        modes=tuple(
            ModeCard(
                name=view.mode,
                colour=palette.get(view.mode),
                navigable=view.navigable,
                runnable=view.runnable,
                groups=tuple(_card_of(group) for group in view.groups),
                routes=tuple(
                    RouteCard(
                        target=route.target,
                        kind=str(route.kind),
                        by=route.by,
                        layer="" if route.layer is None else str(route.layer),
                    )
                    for route in view.routes
                ),
                pedals=tuple(
                    EdgeCard(name=p.name, press=p.press, release=p.release) for p in view.pedals
                ),
                gestures=tuple(
                    EdgeCard(name=g.name, press=g.press, release=g.release) for g in view.gestures
                ),
            )
            for view in views
        ),
    )


def _card_of(group: Group) -> GroupCard:
    return GroupCard(
        name=group.name,
        entries=tuple(
            Cell(phrase=entry.phrase, response=entry.response, tail=entry.tail)
            for entry in group.entries
        ),
        phrases=group.phrases,
        source=group.source,
        table=None if group.table is None else _card_of_table(group),
    )


def _card_of_table(group: Group) -> TableCard:
    table = group.table
    assert table is not None

    position = {entry: index for index, entry in enumerate(group.entries)}

    def at(cell: Entry | None) -> int | None:
        if cell is None:
            return None
        if cell not in position:
            raise ProtocolError(f"table cell {cell.phrase!r} is not an entry of {group.name!r}")
        return position[cell]

    return TableCard(
        row_labels=table.row_labels,
        rows=tuple(tuple(at(cell) for cell in row) for row in table.cells),
        column_labels=table.column_labels,
    )


def state_of(
    snapshot: Snapshot,
    *,
    revision: int = 0,
    chip: bool = True,
    sheet: bool = False,
    page: str = PAGES[0],
    dot: CalibrationDot | None = None,
) -> State:
    """The state frame for one snapshot."""
    return State(
        revision=revision,
        mode=snapshot.layers.voice,
        gesture_mode=snapshot.layers.gesture,
        pedal_mode=snapshot.layers.pedal,
        asleep=tuple(layer.value for layer in Layer if layer in snapshot.sleep.asleep),
        wake_with=tuple(layer.value for layer in Layer if layer in snapshot.sleep.owners),
        confirming=snapshot.sleep.prompt,
        notice=snapshot.sleep.notice,
        held=_held_of(snapshot.held),
        saved=snapshot.held.saved,
        fault=_fault_of(snapshot.health),
        partial=snapshot.heard.partial,
        heard=snapshot.heard.text,
        matched=snapshot.heard.matched,
        nearest=snapshot.heard.nearest,
        dot=dot,
        chip=chip,
        sheet=sheet,
        page=page,
        group=snapshot.open_group,
        held_gestures=snapshot.held_gestures,
        meters=snapshot.meters,
        hands=snapshot.hands,
    )


def _held_of(held: HeldInput) -> tuple[KeyCard, ...]:
    return tuple(
        KeyCard(key=canonical(key.key), symbol=key.symbol, grip=key.grip.value)
        for key in held.pressed()
    )


def _fault_of(health: Health) -> str | None:
    """What is broken, with the reason."""
    if health.ok:
        return None
    return "; ".join(f"{name} ({reason})" for name, reason in health.failed)
